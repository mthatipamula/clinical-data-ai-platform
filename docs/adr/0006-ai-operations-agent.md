# ADR 0006: AI operations agent

Status: accepted

## Context

An agent that triages alerts and runs runbooks can cut time to mitigate. It can also take a wrong
action quickly, or be steered by text inside an alert.

## Decision

The model's role is narrow: choose a runbook from the registry and explain why. Controls sit
outside the model, in code and in IAM:

| Control | Where it is enforced |
|---|---|
| Least privilege | `config/agents.json` allowlist, checked on every step; a step naming another tool is refused and recorded |
| Approval gates | Read-only tools run at once. Any tool that changes the platform waits for a human |
| Separation of duties | The person who asked for the triage cannot approve its actions; approvers need the approval right |
| Blast radius | The agent's service account has read-only roles. Approved changes run as the deployer identity, so stolen agent credentials cannot change anything |
| Audit | Every proposal, refusal, approval and execution goes to the hash-chained log with actor type `agent` |
| Fallback | An alert with no matching runbook goes to a human with no actions proposed |

Runbooks are reviewed YAML. Adding a step is a code change with an owner.

## Trade-offs

- **Speed versus control.** Diagnostics are immediate; mitigation waits for a person. For a
  regulated platform that is the right default. Widening auto-approval for specific low-risk,
  reversible actions is a later decision made per action, with evidence from the metrics below.
- **Runbooks versus free-form reasoning.** The agent cannot improvise a fix. Novel incidents get
  no automation, by design.

## Measuring the effect

`/api/ops/incidents` reports actions run automatically, approved, rejected, awaiting approval and
refused. Targets and how they roll up to service-level objectives are in `docs/slo.md`.

## Status

Triage, gating, approvals and audit are built and tested. Tool executors act on simulated
platform state; executors that call Cloud Monitoring, Dataflow and BigQuery are not written.
