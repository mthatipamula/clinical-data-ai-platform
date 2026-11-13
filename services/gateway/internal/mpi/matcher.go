// Package mpi is the master patient index: it decides whether a demographic
// record from a source system belongs to a person we already know.
//
// Matching is probabilistic (Fellegi-Sunter style log-odds weights) with two
// hard rules layered on top, because the cost of a false merge in healthcare
// (one person's diagnoses shown on another's chart) is far higher than the
// cost of a missed link (a duplicate that a data steward resolves later):
//
//  1. A shared identifier from the same assigning authority links directly.
//  2. Demographics alone may link automatically only when the first name
//     agrees exactly (or by known nickname) and no SSN disagrees. Anything
//     weaker is created as a new person and queued for human review.
package mpi

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
)

type Identifier struct {
	System string `json:"system"`
	Value  string `json:"value"`
}

// Record is one source system's view of a person.
type Record struct {
	SourceSystem string       `json:"source_system"`
	Identifiers  []Identifier `json:"identifiers"`
	FirstName    string       `json:"first_name"`
	LastName     string       `json:"last_name"`
	BirthDate    string       `json:"birth_date"` // YYYY-MM-DD
	Sex          string       `json:"sex"`
	SSNLast4     string       `json:"ssn_last4,omitempty"`
	Phone        string       `json:"phone,omitempty"`
	AddressLine1 string       `json:"address_line1,omitempty"`
	PostalCode   string       `json:"postal_code,omitempty"`
}

type Decision string

const (
	Matched          Decision = "matched"
	New              Decision = "new"
	NewPendingReview Decision = "new_pending_review"
)

type Result struct {
	EID       string   `json:"eid"`
	Decision  Decision `json:"decision"`
	Score     float64  `json:"score"`
	MatchedOn []string `json:"matched_on,omitempty"`
	ReviewID  string   `json:"review_task_id,omitempty"`
}

// Thresholds are in log2-odds units. Tuned on the synthetic population; a
// production deployment estimates weights and thresholds from labelled pairs.
const (
	MatchThreshold  = 11.0
	ReviewThreshold = 6.0
)

type comparison struct {
	score      float64
	fields     []string
	firstExact bool
	ssnAgree   bool
	ssnClash   bool
}

func (c comparison) autoLinkAllowed() bool {
	return !c.ssnClash && (c.firstExact || c.ssnAgree)
}

func compare(a, b Record) comparison {
	var c comparison
	add := func(w float64, field string) {
		c.score += w
		if w > 0 {
			c.fields = append(c.fields, field)
		}
	}

	la, lb := NormName(a.LastName), NormName(b.LastName)
	switch {
	case la != "" && la == lb:
		add(4.5, "last_name")
	case JaroWinkler(la, lb) >= 0.92:
		add(3.0, "last_name~")
	default:
		add(-3.0, "")
	}

	fa, fb := formalName(NormName(a.FirstName)), formalName(NormName(b.FirstName))
	switch {
	case fa != "" && fa == fb:
		c.firstExact = true
		add(3.5, "first_name")
	case JaroWinkler(fa, fb) >= 0.90:
		add(1.0, "first_name~")
	default:
		add(-3.5, "")
	}

	switch {
	case a.BirthDate != "" && a.BirthDate == b.BirthDate:
		add(5.5, "birth_date")
	case dobNear(a.BirthDate, b.BirthDate):
		add(1.5, "birth_date~")
	default:
		add(-5.0, "")
	}

	if a.Sex != "" && b.Sex != "" {
		if a.Sex == b.Sex {
			add(0.5, "sex")
		} else {
			add(-2.5, "")
		}
	}
	if a.SSNLast4 != "" && b.SSNLast4 != "" {
		if a.SSNLast4 == b.SSNLast4 {
			c.ssnAgree = true
			add(5.0, "ssn_last4")
		} else {
			c.ssnClash = true
			add(-5.0, "")
		}
	}
	if pa, pb := digits(a.Phone), digits(b.Phone); pa != "" && pb != "" {
		if pa == pb {
			add(3.0, "phone")
		} else {
			add(-0.5, "")
		}
	}
	if a.PostalCode != "" && b.PostalCode != "" {
		if a.PostalCode == b.PostalCode {
			add(1.5, "postal_code")
		} else {
			add(-0.5, "")
		}
	}
	if aa, ab := normAddr(a.AddressLine1), normAddr(b.AddressLine1); aa != "" && ab != "" {
		if aa == ab {
			add(2.0, "address")
		} else {
			add(-0.5, "")
		}
	}
	return c
}

// dobNear is true when two dates differ in exactly one character or have day
// and month swapped: the usual keying errors.
func dobNear(a, b string) bool {
	if len(a) != 10 || len(b) != 10 {
		return false
	}
	if a[:4] == b[:4] && a[5:7] == b[8:10] && a[8:10] == b[5:7] {
		return true
	}
	diff := 0
	for i := 0; i < 10; i++ {
		if a[i] != b[i] {
			diff++
		}
	}
	return diff == 1
}

// blockingKeys limit comparisons to plausible candidates. A record is only
// scored against records that share at least one key.
func blockingKeys(r Record) []string {
	var keys []string
	last, first := NormName(r.LastName), NormName(r.FirstName)
	if len(r.BirthDate) >= 4 && last != "" {
		keys = append(keys, "sx:"+Soundex(last)+":"+r.BirthDate[:4])
	}
	if r.BirthDate != "" && first != "" {
		keys = append(keys, "dob:"+r.BirthDate+":"+first[:1])
	}
	if p := digits(r.Phone); p != "" {
		keys = append(keys, "ph:"+p)
	}
	if r.SSNLast4 != "" && last != "" {
		keys = append(keys, "ssn:"+r.SSNLast4+":"+last[:1])
	}
	return keys
}

func idKey(i Identifier) string { return i.System + "|" + i.Value }

func newEID(r Record) string {
	seed := r.SourceSystem + "|" + r.FirstName + "|" + r.LastName + "|" + r.BirthDate
	if len(r.Identifiers) > 0 {
		seed = r.SourceSystem + "|" + idKey(r.Identifiers[0])
	}
	h := sha256.Sum256([]byte(seed))
	return "eid-" + hex.EncodeToString(h[:8])
}

func reviewID(eidA, eidB string) string {
	p := []string{eidA, eidB}
	sort.Strings(p)
	h := sha256.Sum256([]byte(p[0] + "|" + p[1]))
	return fmt.Sprintf("rev-%s", hex.EncodeToString(h[:6]))
}
