package mpi

import (
	"strings"
	"unicode"
)

// JaroWinkler returns a similarity in [0,1]. Inputs are compared as given;
// callers normalise first.
func JaroWinkler(a, b string) float64 {
	if a == b {
		if a == "" {
			return 0
		}
		return 1
	}
	ra, rb := []rune(a), []rune(b)
	la, lb := len(ra), len(rb)
	if la == 0 || lb == 0 {
		return 0
	}
	window := max(la, lb)/2 - 1
	if window < 0 {
		window = 0
	}
	ma, mb := make([]bool, la), make([]bool, lb)
	matches := 0
	for i := 0; i < la; i++ {
		lo, hi := max(0, i-window), min(lb-1, i+window)
		for j := lo; j <= hi; j++ {
			if !mb[j] && ra[i] == rb[j] {
				ma[i], mb[j] = true, true
				matches++
				break
			}
		}
	}
	if matches == 0 {
		return 0
	}
	transpositions, k := 0, 0
	for i := 0; i < la; i++ {
		if !ma[i] {
			continue
		}
		for !mb[k] {
			k++
		}
		if ra[i] != rb[k] {
			transpositions++
		}
		k++
	}
	m := float64(matches)
	jaro := (m/float64(la) + m/float64(lb) + (m-float64(transpositions)/2)/m) / 3
	prefix := 0
	for prefix < min(4, min(la, lb)) && ra[prefix] == rb[prefix] {
		prefix++
	}
	return jaro + float64(prefix)*0.1*(1-jaro)
}

// Soundex is the classic four-character phonetic code, used only for blocking.
func Soundex(s string) string {
	codes := map[rune]byte{'B': '1', 'F': '1', 'P': '1', 'V': '1', 'C': '2', 'G': '2', 'J': '2', 'K': '2',
		'Q': '2', 'S': '2', 'X': '2', 'Z': '2', 'D': '3', 'T': '3', 'L': '4', 'M': '5', 'N': '5', 'R': '6'}
	s = NormName(s)
	if s == "" {
		return ""
	}
	out := []byte{s[0]}
	last := codes[rune(s[0])]
	for _, r := range s[1:] {
		c, ok := codes[r]
		if ok && c != last {
			out = append(out, c)
		}
		if r != 'H' && r != 'W' {
			last = c
		}
		if len(out) == 4 {
			break
		}
	}
	for len(out) < 4 {
		out = append(out, '0')
	}
	return string(out)
}

// NormName upper-cases and drops everything that is not a letter.
func NormName(s string) string {
	var b strings.Builder
	for _, r := range strings.ToUpper(s) {
		if unicode.IsLetter(r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func digits(s string) string {
	var b strings.Builder
	for _, r := range s {
		if unicode.IsDigit(r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func normAddr(s string) string {
	return strings.Join(strings.Fields(strings.ToUpper(s)), " ")
}

// nicknames maps a diminutive to its formal name. Production loads a curated
// table; this list covers the demo population.
var nicknames = map[string]string{
	"BOB": "ROBERT", "ROB": "ROBERT", "BILL": "WILLIAM", "WILL": "WILLIAM", "JIM": "JAMES",
	"MIKE": "MICHAEL", "RICK": "RICHARD", "DICK": "RICHARD", "TOM": "THOMAS", "JOE": "JOSEPH",
	"DAN": "DANIEL", "TONY": "ANTHONY", "ED": "EDWARD", "CHUCK": "CHARLES", "SAM": "SAMUEL",
	"LIZ": "ELIZABETH", "BETH": "ELIZABETH", "KATE": "KATHERINE", "KATIE": "KATHERINE",
	"PEGGY": "MARGARET", "MAGGIE": "MARGARET", "JEN": "JENNIFER", "PAT": "PATRICIA",
	"SUE": "SUSAN", "DEB": "DEBORAH", "BECKY": "REBECCA", "VICKY": "VICTORIA", "CHRIS": "CHRISTINA",
}

func formalName(n string) string {
	if f, ok := nicknames[n]; ok {
		return f
	}
	return n
}
