package ingest

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"
)

// Google Cloud adapters over the REST APIs, so the gateway stays free of
// third-party dependencies. They are unit-tested against a fake server and
// have not yet been exercised against real Google Cloud.

// tokenSource fetches the workload's access token from the metadata server
// (Workload Identity on GKE). No key files are ever read.
type tokenSource struct {
	mu      sync.Mutex
	token   string
	expires time.Time
	client  *http.Client
}

func (t *tokenSource) get() (string, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.token != "" && time.Now().Before(t.expires) {
		return t.token, nil
	}
	req, _ := http.NewRequest(http.MethodGet,
		"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token", nil)
	req.Header.Set("Metadata-Flavor", "Google")
	resp, err := t.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var body struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("metadata server: %s", resp.Status)
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return "", err
	}
	t.token = body.AccessToken
	t.expires = time.Now().Add(time.Duration(body.ExpiresIn-60) * time.Second)
	return t.token, nil
}

type gcpClient struct {
	http   *http.Client
	tokens *tokenSource // nil when talking to an emulator
}

func newGCPClient(authenticated bool) gcpClient {
	c := gcpClient{http: &http.Client{Timeout: 15 * time.Second}}
	if authenticated {
		c.tokens = &tokenSource{client: c.http}
	}
	return c
}

func (c gcpClient) post(u, contentType string, body []byte) error {
	req, err := http.NewRequest(http.MethodPost, u, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", contentType)
	if c.tokens != nil {
		tok, err := c.tokens.get()
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("%s: %s", resp.Status, msg)
	}
	return nil
}

// PubSubPublisher announces a landed envelope. The message carries a pointer
// and metadata, never the payload: PHI stays in the bucket, behind its own IAM.
type PubSubPublisher struct {
	endpoint string // e.g. https://pubsub.googleapis.com
	topic    string // projects/<p>/topics/<t>
	client   gcpClient
}

func NewPubSubPublisher(project, topic string) *PubSubPublisher {
	p := &PubSubPublisher{topic: fmt.Sprintf("projects/%s/topics/%s", project, topic)}
	if emu := os.Getenv("PUBSUB_EMULATOR_HOST"); emu != "" {
		p.endpoint, p.client = "http://"+emu, newGCPClient(false)
	} else {
		p.endpoint, p.client = "https://pubsub.googleapis.com", newGCPClient(true)
	}
	return p
}

func (p *PubSubPublisher) Publish(e Envelope) error {
	body, _ := json.Marshal(map[string]any{"messages": []map[string]any{{
		"data": base64.StdEncoding.EncodeToString([]byte(e.IngestID)),
		"attributes": map[string]string{"ingest_id": e.IngestID, "source_system": e.SourceSystem,
			"tenant": e.Tenant, "kind": e.Kind, "sha256": e.SHA256, "object": objectName(e)},
		"orderingKey": e.SourceSystem,
	}}})
	return p.client.post(p.endpoint+"/v1/"+p.topic+":publish", "application/json", body)
}

// GCSSink lands one object per envelope in the raw bucket.
type GCSSink struct {
	endpoint string
	bucket   string
	client   gcpClient
}

func NewGCSSink(bucket string) *GCSSink {
	return &GCSSink{endpoint: "https://storage.googleapis.com", bucket: bucket, client: newGCPClient(true)}
}

func objectName(e Envelope) string {
	return fmt.Sprintf("%s/%s/%s.json", e.SourceSystem, e.ReceivedAt[:10], e.IngestID)
}

func (g *GCSSink) Write(e Envelope) error {
	body, err := json.Marshal(e)
	if err != nil {
		return err
	}
	u := fmt.Sprintf("%s/upload/storage/v1/b/%s/o?uploadType=media&name=%s",
		g.endpoint, url.PathEscape(g.bucket), url.QueryEscape(objectName(e)))
	return g.client.post(u, "application/json", body)
}
