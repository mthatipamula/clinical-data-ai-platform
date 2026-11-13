# Controls

How the platform meets common enterprise control requirements, and where the evidence comes from.
Evidence is collected by code: `make evidence` writes a bundle, the compliance view shows it live,
and CI uploads it on every run. Control IDs match the bundle.

This maps design to requirement areas. It is not a certification or a legal assessment; security,
privacy, compliance and AI governance teams own that judgment.

| Requirement area | What the platform does | Enforced in | Evidence |
|---|---|---|---|
| Change management | Every change is a reviewed commit; governed files need a named owner's review; CI runs tests, evals and the evidence bundle | `.github/CODEOWNERS`, `.github/workflows/ci.yml`, PR template | `CHG-02` |
| Change management for agents | No platform change by an agent runs without a human approval | `ops.py` | `CHG-01` |
| Separation of duties | The requester of an agent action cannot approve it; approvers trigger the deployer but cannot act as data-plane identities | `ops.py`, `infra/terraform/iam.tf` | `SOD-01` |
| Least privilege | One service account per workload with narrow roles; agents limited to an allowlist of tools | `infra/terraform/iam.tf`, `config/agents.json` | `IAM-01` |
| Access reviews | Roles and approval rights are listed on demand for periodic review | `evidence.py` | `IAM-02` |
| Access to PHI | Access requires a relationship, a permitted purpose of use and, for restricted sources, consent; allow and deny are both logged | `access.py` | `PHI-01` |
| Audit trail | Append-only log, hash-chained so edits and deletions are detectable; Google Cloud data-access logs exported alongside | `store.py`, `infra/terraform/bigquery.tf` | `AUD-01` |
| Data lineage | Every harmonized record names its raw payload, hash and pipeline version | `HarmonizeFn.java` | `LIN-01` |
| Model and prompt governance | Versioned prompts, model allowlist with named approvals, every call recorded | `llm.py`, `config/models.json`, `prompts/` | `AI-01` |
| AI output controls | Extracted facts must be quoted from the source; answers must cite supplied passages | `clinical.py` | `AI-02` |
| Clinical logic governance | Rules and measures are versioned; results record the version used | `rules/`, `Measures.java` | `RUL-01` |
| Encryption | Customer-managed keys, one per store, 90-day rotation; TLS in transit | `infra/terraform/kms.tf` | Terraform state and plan |
| Network isolation | Private VPC, no public IPs, default-deny ingress, VPC Service Controls perimeter (dry run first) | `infra/terraform/network.tf`, `vpc_sc.tf` | Terraform state and plan |
| Data retention | Raw payloads retained seven years under a bucket retention policy | `infra/terraform/storage.tf` | Terraform state and plan |

## Gaps to close before production

Listed in the README under "Not built yet". The ones that bear directly on controls:
real sign-in (Identity-Aware Proxy) in place of demo users, the audit log written to BigQuery
with a locked retention policy, steward merge and unmerge with its own audit events, and a
threat model reviewed with security.
