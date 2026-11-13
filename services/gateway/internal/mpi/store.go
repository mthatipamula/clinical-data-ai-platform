package mpi

import (
	"encoding/json"
	"os"
	"sort"
	"sync"
	"time"
)

// Person is the enterprise identity: a stable EID and every source record linked to it.
type Person struct {
	EID     string   `json:"eid"`
	Records []Record `json:"records"`
}

// ReviewTask is a possible duplicate that a data steward must decide.
type ReviewTask struct {
	ID        string   `json:"id"`
	NewEID    string   `json:"new_eid"`
	Candidate string   `json:"candidate_eid"`
	Score     float64  `json:"score"`
	Reason    string   `json:"reason"`
	Fields    []string `json:"agreeing_fields"`
	CreatedAt string   `json:"created_at"`
}

type Stats struct {
	Persons       int `json:"persons"`
	Records       int `json:"records"`
	ReviewPending int `json:"review_pending"`
}

// Index is the in-memory MPI. One mutex serialises resolution, which keeps the
// read-compare-write cycle atomic. The production store is Spanner, where the
// same cycle runs inside a read-write transaction keyed on the blocking keys
// (schema in infra/terraform/spanner.tf); that adapter is not written yet.
type Index struct {
	mu      sync.Mutex
	persons map[string]*Person
	byID    map[string]string          // identifier -> eid
	blocks  map[string]map[string]bool // blocking key -> set of eid
	reviews map[string]ReviewTask
	dirty   bool
	path    string
}

func NewIndex(snapshotPath string) (*Index, error) {
	ix := &Index{persons: map[string]*Person{}, byID: map[string]string{},
		blocks: map[string]map[string]bool{}, reviews: map[string]ReviewTask{}, path: snapshotPath}
	if snapshotPath == "" {
		return ix, nil
	}
	b, err := os.ReadFile(snapshotPath)
	if os.IsNotExist(err) {
		return ix, nil
	}
	if err != nil {
		return nil, err
	}
	var snap snapshot
	if err := json.Unmarshal(b, &snap); err != nil {
		return nil, err
	}
	for _, p := range snap.Persons {
		cp := p
		ix.persons[p.EID] = &cp
		for _, r := range p.Records {
			ix.index(p.EID, r)
		}
	}
	for _, t := range snap.Reviews {
		ix.reviews[t.ID] = t
	}
	return ix, nil
}

type snapshot struct {
	Persons []Person     `json:"persons"`
	Reviews []ReviewTask `json:"reviews"`
}

func (ix *Index) index(eid string, r Record) {
	for _, id := range r.Identifiers {
		ix.byID[idKey(id)] = eid
	}
	for _, k := range blockingKeys(r) {
		if ix.blocks[k] == nil {
			ix.blocks[k] = map[string]bool{}
		}
		ix.blocks[k][eid] = true
	}
}

// Resolve returns the EID for a record, creating a person when nothing matches.
// It is idempotent: the same record resolves to the same EID every time.
func (ix *Index) Resolve(r Record) Result {
	ix.mu.Lock()
	defer ix.mu.Unlock()

	for _, id := range r.Identifiers {
		if eid, ok := ix.byID[idKey(id)]; ok {
			ix.attach(eid, r)
			return Result{EID: eid, Decision: Matched, Score: 99, MatchedOn: []string{"identifier:" + id.System}}
		}
	}

	candidates := map[string]bool{}
	for _, k := range blockingKeys(r) {
		for eid := range ix.blocks[k] {
			candidates[eid] = true
		}
	}
	eids := make([]string, 0, len(candidates))
	for eid := range candidates {
		eids = append(eids, eid)
	}
	sort.Strings(eids) // deterministic tie-breaking

	// Track the best candidate we may link automatically separately from the
	// best candidate overall: a linkable match wins even when a non-linkable
	// one (a twin, say) scores higher.
	var bestEID, linkEID string
	best, link := comparison{score: -1e9}, comparison{score: -1e9}
	for _, eid := range eids {
		for _, existing := range ix.persons[eid].Records {
			c := compare(r, existing)
			if c.score > best.score {
				best, bestEID = c, eid
			}
			if c.autoLinkAllowed() && c.score >= MatchThreshold && c.score > link.score {
				link, linkEID = c, eid
			}
		}
	}

	if linkEID != "" {
		ix.attach(linkEID, r)
		return Result{EID: linkEID, Decision: Matched, Score: link.score, MatchedOn: link.fields}
	}

	eid := newEID(r)
	ix.persons[eid] = &Person{EID: eid}
	ix.attach(eid, r)
	if bestEID != "" && best.score >= ReviewThreshold {
		reason := "score below automatic threshold"
		switch {
		case best.ssnClash:
			reason = "demographics agree but SSN differs (possible twin or family member)"
		case !best.firstExact && best.score >= MatchThreshold:
			reason = "first name differs with no SSN to corroborate"
		}
		t := ReviewTask{ID: reviewID(eid, bestEID), NewEID: eid, Candidate: bestEID, Score: best.score,
			Reason: reason, Fields: best.fields, CreatedAt: time.Now().UTC().Format(time.RFC3339)}
		ix.reviews[t.ID] = t
		return Result{EID: eid, Decision: NewPendingReview, Score: best.score, ReviewID: t.ID}
	}
	return Result{EID: eid, Decision: New}
}

func (ix *Index) attach(eid string, r Record) {
	p := ix.persons[eid]
	for _, existing := range p.Records {
		if existing.SourceSystem == r.SourceSystem && sameIDs(existing.Identifiers, r.Identifiers) {
			return // already linked; keep the first-seen demographics
		}
	}
	p.Records = append(p.Records, r)
	ix.index(eid, r)
	ix.dirty = true
}

func sameIDs(a, b []Identifier) bool {
	if len(a) == 0 || len(b) == 0 {
		return false
	}
	return idKey(a[0]) == idKey(b[0])
}

func (ix *Index) Person(eid string) (Person, bool) {
	ix.mu.Lock()
	defer ix.mu.Unlock()
	p, ok := ix.persons[eid]
	if !ok {
		return Person{}, false
	}
	return *p, true
}

func (ix *Index) Reviews() []ReviewTask {
	ix.mu.Lock()
	defer ix.mu.Unlock()
	out := make([]ReviewTask, 0, len(ix.reviews))
	for _, t := range ix.reviews {
		out = append(out, t)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (ix *Index) Stats() Stats {
	ix.mu.Lock()
	defer ix.mu.Unlock()
	s := Stats{Persons: len(ix.persons), ReviewPending: len(ix.reviews)}
	for _, p := range ix.persons {
		s.Records += len(p.Records)
	}
	return s
}

// Save writes a snapshot when anything changed since the last save.
func (ix *Index) Save() error {
	ix.mu.Lock()
	defer ix.mu.Unlock()
	if ix.path == "" || !ix.dirty {
		return nil
	}
	var snap snapshot
	for _, p := range ix.persons {
		snap.Persons = append(snap.Persons, *p)
	}
	sort.Slice(snap.Persons, func(i, j int) bool { return snap.Persons[i].EID < snap.Persons[j].EID })
	for _, t := range ix.reviews {
		snap.Reviews = append(snap.Reviews, t)
	}
	sort.Slice(snap.Reviews, func(i, j int) bool { return snap.Reviews[i].ID < snap.Reviews[j].ID })
	b, err := json.MarshalIndent(snap, "", " ")
	if err != nil {
		return err
	}
	tmp := ix.path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	ix.dirty = false
	return os.Rename(tmp, ix.path)
}
