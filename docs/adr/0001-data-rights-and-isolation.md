# ADR 0001: Data rights and tenant isolation

Status: accepted

## Context

Sources arrive with different rights. A health plan's claims may be used for payment and
operations. A substance-use program's records may be disclosed for treatment only, and only with
the patient's consent. The same person appears in several sources, and three audiences read the
combined record under different purposes.

## Options

| Option | Isolation | Data rights | Scale and cost | Reliability |
|---|---|---|---|---|
| A. Project or dataset per tenant; join at query time | Strongest: IAM boundary per tenant | Coarse: all-or-nothing per tenant | Many projects, cross-project joins, cost grows with tenants | Many moving parts to deploy and monitor |
| B. One harmonized store; every record tagged with source and tenant; policy filters at read time | Logical: depends on the policy layer being the only reader | Fine: per source, per purpose, per consent | One store, one pipeline, lowest cost | One policy layer is a single point of correctness |
| C. Copy permitted subsets into per-audience marts | Strong for the marts | Fixed when the copy is made: a revoked consent stays in the mart until rebuild | Storage multiplied by audiences | Staleness is a correctness risk |

## Decision

B, with two hard boundaries added where logical isolation is not enough:

1. The policy layer (`services/api/cdap/access.py`) is the only path from a request to patient
   data. It decides per patient and per source using the caller's relationship, their purpose of
   use, the source's `permitted_purposes`, and consent.
2. Batch jobs that serve a purpose (quality measures and care-gap rules serve operations) filter
   to permitted sources before any logic runs, so a derived result can never encode a restricted
   record.
3. Withheld sources are not listed to anyone except the patient, because the existence of such a
   record is itself protected information.
4. On Google Cloud, PHI stores sit inside a VPC Service Controls perimeter with one KMS key per
   store, and each environment is its own project.

## Consequences

- Rights live in one file, `config/sources.json`, read by all three services. A rights change is a
  reviewed one-line change, effective on the next read; nothing is rebuilt.
- A bug in the policy layer is a disclosure. It is small, has no dependencies, and its tests cover
  each role against each source class. It is a governed file.
- If a tenant later requires physical separation by contract, move that tenant to option A; the
  source registry already carries the tenant on every record.
