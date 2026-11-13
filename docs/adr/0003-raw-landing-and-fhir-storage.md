# ADR 0003: Raw landing and FHIR storage

Status: accepted

## Decision

1. **Land raw first, unchanged.** The gateway validates only that a payload looks like its
   registered kind, stores it, and publishes a pointer. It does not interpret clinical content.
2. **Harmonize to FHIR R4 in a separate, replayable step** (Apache Beam on Dataflow).
3. **Store harmonized resources as JSON rows with a provenance wrapper** (enterprise ID, source,
   tenant, ingest ID, raw hash, pipeline version) in BigQuery, clustered by enterprise ID and
   resource type.

## Why

- A conversion bug found six months later is fixed by replaying raw, not by asking sources to
  resend.
- A dumb gateway stays fast and rarely changes; the code that changes often (conversion) is not on
  the admission path.
- The wrapper turns lineage into a column lookup and lets the access policy filter by source
  without parsing FHIR.

## Alternatives considered

| Alternative | Why not now |
|---|---|
| Cloud Healthcare API FHIR store as the system of record | Strong fit for FHIR REST and SMART apps, and worth adding as a serving layer. As the only store it makes population analytics and per-source filtering harder and costs more per read. |
| Flatten to relational (OMOP) at harmonization | Good for research. It loses FHIR structure the applications use and adds a second mapping to maintain. Better as a derived data product. |
| Parse and validate fully at the gateway | Couples admission latency and availability to conversion logic. |

## Cost and scale notes

Raw objects move to Coldline after 90 days and are retained seven years. The harmonized table
requires a partition filter, so an accidental full scan fails instead of billing. Pub/Sub messages
carry no payload, so message size and cost do not grow with document size.
