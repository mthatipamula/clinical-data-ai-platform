package ingest

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func svc(t *testing.T) (*Service, *FileSink) {
	t.Helper()
	sink := &FileSink{Dir: t.TempDir()}
	return &Service{
		Registry:  Registry{"ehr": {ID: "ehr", Tenant: "t1", Kind: "fhir"}, "adt": {ID: "adt", Tenant: "t1", Kind: "hl7v2"}},
		Sink:      sink,
		Publisher: NoopPublisher{},
	}, sink
}

func TestAcceptLandsOnceAndDeduplicates(t *testing.T) {
	s, sink := svc(t)
	body := []byte(`{"resourceType":"Bundle","entry":[]}`)
	a, err := s.Accept("ehr", body, time.Now())
	if err != nil || a.Duplicate {
		t.Fatalf("first accept: %+v %v", a, err)
	}
	b, _ := s.Accept("ehr", body, time.Now())
	if !b.Duplicate || b.IngestID != a.IngestID {
		t.Fatalf("retry was not a duplicate: %+v", b)
	}
	ids, _ := sink.ExistingIDs()
	if len(ids) != 1 {
		t.Fatalf("landed %d copies, want 1", len(ids))
	}
}

func TestRestartStillDeduplicates(t *testing.T) {
	s, sink := svc(t)
	body := []byte("MSH|^~\\&|ADT|H|C|C|20260101||ADT^A03|1|P|2.5.1\r")
	a, _ := s.Accept("adt", body, time.Now())
	restarted := &Service{Registry: s.Registry, Sink: sink, Publisher: NoopPublisher{}}
	ids, _ := sink.ExistingIDs()
	restarted.Remember(ids)
	if b, _ := restarted.Accept("adt", body, time.Now()); !b.Duplicate || b.IngestID != a.IngestID {
		t.Fatalf("lost idempotency across restart: %+v", b)
	}
}

func TestRejectsUnknownSourceAndWrongKind(t *testing.T) {
	s, _ := svc(t)
	if _, err := s.Accept("nope", []byte("{}"), time.Now()); !errors.Is(err, ErrUnknownSource) {
		t.Fatalf("unknown source: %v", err)
	}
	if _, err := s.Accept("ehr", []byte("MSH|..."), time.Now()); !errors.Is(err, ErrInvalid) {
		t.Fatalf("HL7 sent to FHIR source: %v", err)
	}
}

func TestPubSubMessageCarriesPointerNotPayload(t *testing.T) {
	var got string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		got = r.URL.Path + " " + string(b)
		w.Write([]byte(`{"messageIds":["1"]}`))
	}))
	defer srv.Close()
	p := &PubSubPublisher{endpoint: srv.URL, topic: "projects/p/topics/raw", client: newGCPClient(false)}
	env := Envelope{IngestID: "ing_1", SourceSystem: "ehr", ReceivedAt: "2026-10-31T00:00:00Z", Payload: "SECRET-PHI"}
	if err := p.Publish(env); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, "/v1/projects/p/topics/raw:publish") {
		t.Fatalf("wrong path: %s", got)
	}
	if strings.Contains(got, "SECRET-PHI") {
		t.Fatal("payload leaked into the Pub/Sub message")
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(got[strings.Index(got, " ")+1:]), &body); err != nil {
		t.Fatal(err)
	}
}
