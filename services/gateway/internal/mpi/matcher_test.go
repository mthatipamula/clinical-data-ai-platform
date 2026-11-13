package mpi

import (
	"math"
	"testing"
)

func base() Record {
	return Record{SourceSystem: "ehr", Identifiers: []Identifier{{"mrn", "1"}}, FirstName: "Robert",
		LastName: "Kowalczyk", BirthDate: "1961-04-09", Sex: "male", SSNLast4: "1234",
		Phone: "860-555-0101", AddressLine1: "12 Maple Ave", PostalCode: "06103"}
}

func TestJaroWinkler(t *testing.T) {
	if got := JaroWinkler("MARTHA", "MARHTA"); math.Abs(got-0.9611) > 0.001 {
		t.Fatalf("MARTHA/MARHTA = %v", got)
	}
	if JaroWinkler("SMITH", "JONES") > 0.6 {
		t.Fatal("unrelated names scored high")
	}
}

func TestSoundex(t *testing.T) {
	for in, want := range map[string]string{"Robert": "R163", "Rupert": "R163", "Tymczak": "T522", "Pfister": "P236"} {
		if got := Soundex(in); got != want {
			t.Errorf("Soundex(%s) = %s, want %s", in, got, want)
		}
	}
}

func TestNicknameAndCaseLinkWithoutSSN(t *testing.T) {
	ix, _ := NewIndex("")
	a := ix.Resolve(base())
	payer := Record{SourceSystem: "payer", Identifiers: []Identifier{{"member", "9"}}, FirstName: "BOB",
		LastName: "KOWALCZYK", BirthDate: "1961-04-09", Sex: "male", AddressLine1: "12 MAPLE AVE", PostalCode: "06103"}
	b := ix.Resolve(payer)
	if b.Decision != Matched || b.EID != a.EID {
		t.Fatalf("payer record not linked: %+v", b)
	}
}

func TestTypoInSurnameLinksWhenSSNAgrees(t *testing.T) {
	ix, _ := NewIndex("")
	a := ix.Resolve(base())
	adt := base()
	adt.SourceSystem, adt.Identifiers, adt.LastName = "adt", []Identifier{{"hosp", "7"}}, "KOWLACZYK"
	if b := ix.Resolve(adt); b.EID != a.EID {
		t.Fatalf("typo record not linked: %+v", b)
	}
}

func TestTwinsAreNotMerged(t *testing.T) {
	ix, _ := NewIndex("")
	maria := base()
	maria.FirstName, maria.Sex = "Maria", "female"
	marta := maria
	marta.FirstName, marta.SSNLast4, marta.Identifiers = "Marta", "5555", []Identifier{{"mrn", "2"}}
	a, b := ix.Resolve(maria), ix.Resolve(marta)
	if a.EID == b.EID {
		t.Fatal("twins were merged")
	}
	if b.Decision != NewPendingReview {
		t.Fatalf("twin should be queued for review, got %s", b.Decision)
	}
	// A payer record for Marta with no SSN must land on Marta, not Maria.
	payer := Record{SourceSystem: "payer", Identifiers: []Identifier{{"member", "22"}}, FirstName: "MARTA",
		LastName: "KOWALCZYK", BirthDate: maria.BirthDate, Sex: "female", AddressLine1: "12 MAPLE AVE", PostalCode: "06103"}
	if c := ix.Resolve(payer); c.EID != b.EID {
		t.Fatalf("payer twin record linked to %s, want %s", c.EID, b.EID)
	}
	if n := len(ix.Reviews()); n != 1 {
		t.Fatalf("want 1 review task, got %d", n)
	}
}

func TestResolveIsIdempotent(t *testing.T) {
	ix, _ := NewIndex("")
	a, b := ix.Resolve(base()), ix.Resolve(base())
	if a.EID != b.EID || ix.Stats().Records != 1 {
		t.Fatalf("not idempotent: %+v %+v %+v", a, b, ix.Stats())
	}
}

func TestStrangersGetNewEID(t *testing.T) {
	ix, _ := NewIndex("")
	ix.Resolve(base())
	other := Record{SourceSystem: "ehr", Identifiers: []Identifier{{"mrn", "3"}}, FirstName: "Wei",
		LastName: "Nakamura", BirthDate: "1961-04-09", Sex: "male"}
	if r := ix.Resolve(other); r.Decision != New {
		t.Fatalf("stranger decision = %s", r.Decision)
	}
}
