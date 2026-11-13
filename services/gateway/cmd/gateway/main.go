// Command gateway serves raw-data admission and identity resolution.
//
// Configuration (environment):
//
//	GATEWAY_ADDR      listen address                      (default :8081)
//	SOURCES_FILE      source registry                     (default config/sources.json)
//	LANDING_DIR       local landing zone                  (default var/raw)
//	RAW_BUCKET        land to this GCS bucket instead of LANDING_DIR
//	GCP_PROJECT       with RAW_TOPIC, publish to Pub/Sub
//	RAW_TOPIC         Pub/Sub topic name
//	MPI_SNAPSHOT      identity index snapshot file        (default var/mpi/index.json)
//	MAX_INFLIGHT      concurrent ingest requests before 429 (default 256)
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"clinical-data-ai-platform/services/gateway/internal/ingest"
	"clinical-data-ai-platform/services/gateway/internal/mpi"
)

const maxBody = 10 << 20

type metrics struct {
	mu sync.Mutex
	m  map[string]*atomic.Int64
}

func (m *metrics) inc(name string) {
	m.mu.Lock()
	c, ok := m.m[name]
	if !ok {
		c = &atomic.Int64{}
		m.m[name] = c
	}
	m.mu.Unlock()
	c.Add(1)
}

func (m *metrics) write(w io.Writer) {
	m.mu.Lock()
	defer m.mu.Unlock()
	names := make([]string, 0, len(m.m))
	for n := range m.m {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		fmt.Fprintf(w, "%s %d\n", n, m.m[n].Load())
	}
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	reg, err := ingest.LoadRegistry(env("SOURCES_FILE", "config/sources.json"))
	if err != nil {
		log.Error("load source registry", "err", err)
		os.Exit(1)
	}

	svc := &ingest.Service{Registry: reg, Publisher: ingest.NoopPublisher{}}
	if bucket := os.Getenv("RAW_BUCKET"); bucket != "" {
		svc.Sink = ingest.NewGCSSink(bucket)
	} else {
		fs := &ingest.FileSink{Dir: env("LANDING_DIR", "var/raw")}
		ids, err := fs.ExistingIDs()
		if err != nil {
			log.Error("scan landing zone", "err", err)
			os.Exit(1)
		}
		svc.Remember(ids)
		svc.Sink = fs
	}
	if project, topic := os.Getenv("GCP_PROJECT"), os.Getenv("RAW_TOPIC"); project != "" && topic != "" {
		svc.Publisher = ingest.NewPubSubPublisher(project, topic)
	}

	snap := env("MPI_SNAPSHOT", "var/mpi/index.json")
	_ = os.MkdirAll(filepath.Dir(snap), 0o750)
	index, err := mpi.NewIndex(snap)
	if err != nil {
		log.Error("load identity index", "err", err)
		os.Exit(1)
	}

	inflight, _ := strconv.Atoi(env("MAX_INFLIGHT", "256"))
	slots := make(chan struct{}, inflight)
	mx := &metrics{m: map[string]*atomic.Int64{}}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok\n")) })
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, _ *http.Request) { mx.write(w) })

	// Admission. The gateway sits behind the mesh, which authenticates the
	// calling workload (mTLS); the path names which registered source it is.
	mux.HandleFunc("POST /v1/ingest/{source}", func(w http.ResponseWriter, r *http.Request) {
		select {
		case slots <- struct{}{}:
			defer func() { <-slots }()
		default:
			mx.inc("ingest_shed_total")
			w.Header().Set("Retry-After", "1")
			writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "gateway saturated, retry"})
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBody))
		if err != nil {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"error": "payload too large"})
			return
		}
		rc, err := svc.Accept(r.PathValue("source"), body, time.Now())
		switch {
		case errors.Is(err, ingest.ErrUnknownSource):
			mx.inc("ingest_rejected_total")
			writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
		case errors.Is(err, ingest.ErrInvalid):
			mx.inc("ingest_rejected_total")
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		case err != nil:
			mx.inc("ingest_failed_total")
			log.Error("ingest", "source", r.PathValue("source"), "err", err)
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "temporarily unable to land payload"})
		default:
			if rc.Duplicate {
				mx.inc("ingest_duplicate_total")
			} else {
				mx.inc("ingest_accepted_total")
			}
			// Log identifiers only. Payloads are PHI and never reach logs.
			log.Info("ingest", "source", r.PathValue("source"), "ingest_id", rc.IngestID, "duplicate", rc.Duplicate)
			writeJSON(w, http.StatusAccepted, rc)
		}
	})

	mux.HandleFunc("POST /v1/identity/resolve", func(w http.ResponseWriter, r *http.Request) {
		var rec mpi.Record
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&rec); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid record"})
			return
		}
		if rec.SourceSystem == "" || rec.LastName == "" || rec.BirthDate == "" {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "source_system, last_name and birth_date are required"})
			return
		}
		res := index.Resolve(rec)
		mx.inc("identity_resolve_total")
		mx.inc("identity_decision_" + string(res.Decision) + "_total")
		log.Info("identity", "eid", res.EID, "decision", res.Decision, "source", rec.SourceSystem)
		writeJSON(w, http.StatusOK, res)
	})
	mux.HandleFunc("GET /v1/identity/persons/{eid}", func(w http.ResponseWriter, r *http.Request) {
		p, ok := index.Person(r.PathValue("eid"))
		if !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "no such person"})
			return
		}
		writeJSON(w, http.StatusOK, p)
	})
	mux.HandleFunc("GET /v1/identity/review-queue", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, index.Reviews())
	})
	mux.HandleFunc("GET /v1/identity/stats", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, index.Stats())
	})

	srv := &http.Server{Addr: env("GATEWAY_ADDR", ":8081"), Handler: mux,
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 30 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	go func() {
		t := time.NewTicker(2 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				if err := index.Save(); err != nil {
					log.Error("save identity index", "err", err)
				}
			}
		}
	}()
	go func() {
		log.Info("gateway listening", "addr", srv.Addr, "sources", len(reg))
		if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
			log.Error("serve", "err", err)
			stop()
		}
	}()
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdown)
	if err := index.Save(); err != nil {
		log.Error("final save", "err", err)
	}
	log.Info("gateway stopped")
}
