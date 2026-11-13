# Service-level objectives

Proposed objectives. Targets are starting points to revise once there is a month of real traffic;
none has been measured in production.

## Platform

| Objective | Indicator | Target | Measured from |
|---|---|---|---|
| Ingest availability | Share of ingest requests answered 202 or a correct 4xx | 99.9% over 30 days | Gateway `ingest_*_total` counters |
| Ingest durability | Accepted payloads present in the raw zone | 100% | Daily reconcile of receipts against raw objects |
| Freshness | Time from ingest receipt to harmonized record, 95th percentile | under 15 minutes | Pub/Sub oldest unacked age + pipeline watermark |
| Conversion quality | Share of payloads dead-lettered | under 0.5% per source per day | Dead-letter output, by source and reason |
| Identity safety | False merges found by steward review or audit | 0 | Review queue outcomes; `identity-eval` in CI |
| Identity backlog | Review tasks older than 5 business days | 0 | Review queue |
| API latency | Patient record read, 95th percentile | under 800 ms | API request metrics |
| LLM extraction quality | Lowest precision or recall on the golden set | at least 0.90 | `make eval`, on every prompt or model change |
| Audit integrity | Hash chain verifies | always | `AUD-01` in the evidence bundle, daily |

Error budgets: when an objective's budget for the window is spent, feature releases to that
component pause and work shifts to reliability until it recovers.

## Measuring the effect of automation

The question is whether AI-assisted operations and development help, by how much, and at what
risk. Each row compares a baseline period without the automation to a period with it.

| Question | Indicator | Source |
|---|---|---|
| Do incidents get understood faster? | Time from alert to triage summary | Incident and audit timestamps |
| Do they get fixed faster? | Time from alert to mitigated | Incident status changes |
| How much toil is removed? | Diagnostic steps run automatically per incident | `auto_executed` |
| Is the agent proposing the right things? | Share of proposed changes approved versus rejected | `human_approved`, `rejected` |
| Are approvals a bottleneck? | Time an action waits for approval | Action `created_at` to `decided_at` |
| Are the guardrails being hit? | Steps refused by least privilege | `denied_by_least_privilege` |
| Is it safe? | Incidents caused or worsened by an agent action | Post-incident review; target 0 |
| What does it cost? | Model spend per incident | Vertex AI billing labels |

A rising rejection rate or any agent-caused incident is a signal to narrow what the agent may
propose, not to add review steps after the fact.
