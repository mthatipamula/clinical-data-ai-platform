# ADR 0002: Identity resolution

Status: accepted

## Context

The same person arrives as "Robert Kowalczyk" from the EHR, "BOB KOWALCZYK" with no SSN from the
plan, and "KOWLACZYK^ROBERT" from the hospital. Twins share a surname, birth date, address and
phone. The two possible errors are not equal: a missed link leaves a duplicate that a steward can
merge later; a false merge shows one person's diagnoses on another person's chart.

## Decision

- **Deterministic first.** A shared identifier from the same assigning authority links directly.
- **Then probabilistic.** Blocking keys (surname Soundex with birth year, birth date with first
  initial, phone, SSN-4 with surname initial) pick candidates; field comparisons add log-odds
  weights (Jaro-Winkler for names, keying-error tolerance for dates).
- **Two rules cap automatic linking.** Demographics alone link automatically only if the first
  name agrees exactly or by known nickname, or the SSN agrees; and never if the SSNs disagree.
  Anything else that scores above the review threshold becomes a new person plus a review task.
- **Enterprise IDs are stable and opaque.** They are derived from the first record seen and never
  encode demographics.

## Trade-offs

| Concern | Choice | Cost |
|---|---|---|
| Safety | Bias to review, not merge | More steward work; duplicates until reviewed |
| Scale | Blocking limits comparisons to candidates | A record that shares no blocking key is never compared |
| Consistency | Resolve is one serializable read-compare-write | Throughput bounded by contention on hot blocking keys |
| Store | Spanner in production (schema in `infra/terraform/spanner.tf`) | Higher base cost than a regional database; justified by scale and transaction guarantees |

## Status of the implementation

The matcher, the review queue and the HTTP API are built in Go and tested, including the twins
case. The store is in memory with a snapshot file; the Spanner adapter is not written. Weights and
thresholds are hand-set against the synthetic population and must be re-estimated on labelled real
pairs before production. Merge and unmerge by a steward are not built.
