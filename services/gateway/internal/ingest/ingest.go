// Package ingest admits raw payloads from registered source systems, lands
// them unchanged, and announces them. It does no clinical interpretation:
// keeping the gateway dumb keeps it fast, and keeps the raw record available
// for replay when harmonization logic changes.
package ingest

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Source struct {
	ID                string   `json:"id"`
	Tenant            string   `json:"tenant"`
	Kind              string   `json:"kind"` // fhir | x12_837 | hl7v2 | document
	PermittedPurposes []string `json:"permitted_purposes"`
	RequiresConsent   bool     `json:"requires_consent"`
	Sensitivity       string   `json:"sensitivity"`
}

type Registry map[string]Source

func LoadRegistry(path string) (Registry, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var doc struct {
		Sources []Source `json:"sources"`
	}
	if err := json.Unmarshal(b, &doc); err != nil {
		return nil, err
	}
	reg := Registry{}
	for _, s := range doc.Sources {
		reg[s.ID] = s
	}
	if len(reg) == 0 {
		return nil, errors.New("source registry is empty")
	}
	return reg, nil
}

// Envelope wraps a raw payload with what downstream stages need to trace it.
type Envelope struct {
	IngestID     string `json:"ingest_id"`
	SourceSystem string `json:"source_system"`
	Tenant       string `json:"tenant"`
	Kind         string `json:"kind"`
	ReceivedAt   string `json:"received_at"`
	SHA256       string `json:"sha256"`
	Payload      string `json:"payload"`
}

// Sink durably stores an envelope. Publisher announces it.
type Sink interface{ Write(Envelope) error }
type Publisher interface{ Publish(Envelope) error }

type NoopPublisher struct{}

func (NoopPublisher) Publish(Envelope) error { return nil }

var ErrInvalid = errors.New("payload does not look like the registered kind")

// Validate is a cheap structural sniff, enough to reject a payload sent to the
// wrong endpoint. Full validation happens in the harmonizer, where a failure
// goes to a dead-letter output instead of being lost.
func Validate(kind string, payload []byte) error {
	p := bytes.TrimSpace(payload)
	ok := false
	switch kind {
	case "fhir":
		var probe struct {
			ResourceType string `json:"resourceType"`
		}
		ok = json.Unmarshal(p, &probe) == nil && probe.ResourceType == "Bundle"
	case "x12_837":
		ok = bytes.HasPrefix(p, []byte("ISA*")) && bytes.Contains(p, []byte("ST*837*"))
	case "hl7v2":
		ok = bytes.HasPrefix(p, []byte("MSH|"))
	case "document":
		var probe struct {
			DocumentID string `json:"document_id"`
			Text       string `json:"text"`
		}
		ok = json.Unmarshal(p, &probe) == nil && probe.DocumentID != "" && probe.Text != ""
	}
	if !ok {
		return fmt.Errorf("%w: %s", ErrInvalid, kind)
	}
	return nil
}

// Service is the admission path: validate, deduplicate, land, publish.
type Service struct {
	Registry  Registry
	Sink      Sink
	Publisher Publisher

	mu   sync.Mutex
	seen map[string]bool
}

type Receipt struct {
	IngestID  string `json:"ingest_id"`
	SHA256    string `json:"sha256"`
	Duplicate bool   `json:"duplicate"`
}

var ErrUnknownSource = errors.New("unknown source system")

// Accept is idempotent: the ingest ID is derived from source and content, so a
// sender that retries after a timeout gets the same receipt and no second copy.
func (s *Service) Accept(sourceID string, payload []byte, now time.Time) (Receipt, error) {
	src, ok := s.Registry[sourceID]
	if !ok {
		return Receipt{}, ErrUnknownSource
	}
	if err := Validate(src.Kind, payload); err != nil {
		return Receipt{}, err
	}
	sum := sha256.Sum256(payload)
	idSum := sha256.Sum256(append([]byte(sourceID+"\x00"), payload...))
	rc := Receipt{IngestID: "ing_" + hex.EncodeToString(idSum[:10]), SHA256: hex.EncodeToString(sum[:])}

	s.mu.Lock()
	if s.seen == nil {
		s.seen = map[string]bool{}
	}
	if s.seen[rc.IngestID] {
		s.mu.Unlock()
		rc.Duplicate = true
		return rc, nil
	}
	s.seen[rc.IngestID] = true
	s.mu.Unlock()

	env := Envelope{IngestID: rc.IngestID, SourceSystem: src.ID, Tenant: src.Tenant, Kind: src.Kind,
		ReceivedAt: now.UTC().Format(time.RFC3339), SHA256: rc.SHA256, Payload: string(payload)}
	if err := s.Sink.Write(env); err != nil {
		s.forget(rc.IngestID)
		return Receipt{}, fmt.Errorf("landing failed: %w", err)
	}
	if err := s.Publisher.Publish(env); err != nil {
		// Landed but not announced. The sender retries; the retry is a
		// duplicate for the sink and a fresh attempt for the publisher.
		s.forget(rc.IngestID)
		return Receipt{}, fmt.Errorf("publish failed: %w", err)
	}
	return rc, nil
}

func (s *Service) forget(id string) {
	s.mu.Lock()
	delete(s.seen, id)
	s.mu.Unlock()
}

// Remember marks ingest IDs as already landed (used to rebuild state on start).
func (s *Service) Remember(ids []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.seen == nil {
		s.seen = map[string]bool{}
	}
	for _, id := range ids {
		s.seen[id] = true
	}
}

// FileSink lands envelopes as NDJSON under <dir>/<source>/<date>.ndjson.
type FileSink struct {
	Dir string
	mu  sync.Mutex
}

func (f *FileSink) Write(e Envelope) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	dir := filepath.Join(f.Dir, e.SourceSystem)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return err
	}
	line, err := json.Marshal(e)
	if err != nil {
		return err
	}
	fh, err := os.OpenFile(filepath.Join(dir, e.ReceivedAt[:10]+".ndjson"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	defer fh.Close()
	_, err = fh.Write(append(line, '\n'))
	return err
}

// ExistingIDs scans landed files so a restarted gateway still deduplicates.
func (f *FileSink) ExistingIDs() ([]string, error) {
	var ids []string
	err := filepath.WalkDir(f.Dir, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".ndjson") {
			return nil
		}
		fh, err := os.Open(path)
		if err != nil {
			return err
		}
		defer fh.Close()
		sc := bufio.NewScanner(fh)
		sc.Buffer(make([]byte, 1<<20), 32<<20)
		for sc.Scan() {
			var probe struct {
				IngestID string `json:"ingest_id"`
			}
			if json.Unmarshal(sc.Bytes(), &probe) == nil && probe.IngestID != "" {
				ids = append(ids, probe.IngestID)
			}
		}
		return sc.Err()
	})
	if os.IsNotExist(err) {
		return nil, nil
	}
	return ids, err
}
