# Working in this repository

This file is the shared brief for engineers and AI coding agents. It is governed: changes need
review (see `.github/CODEOWNERS`).

## What this is

A platform that turns clinical, claims and document data into insight for patients, providers
and payers. Read `README.md` for the tour and `docs/architecture.md` for the design.

| Path | Language | Role |
|---|---|---|
| `services/gateway` | Go (stdlib only) | Raw ingest admission; identity resolution (master patient index) |
| `pipelines/harmonizer` | Java 21, Apache Beam, HAPI FHIR | HL7 v2 / X12 837 / FHIR / notes to FHIR R4; quality measures |
| `services/api` | Python, FastAPI | Data products, access policy, LLM gateway, rules, ops agent, audit, evidence |
| `web` | React, TypeScript | Patient, provider, payer, operations and compliance views |
| `infra/terraform` | Terraform | Google Cloud: GKE, Pub/Sub, Dataflow, BigQuery, Spanner, KMS, VPC-SC, IAM |
| `config` | JSON | Source registry, model allowlist, agent permissions, demo principals |

## Commands

```
make pipeline   # synthetic data through every stage (needs go, java 21, uv, python3)
make test       # all suites
make eval       # LLM extraction gate
make evidence   # audit evidence bundle
make tf-validate
```

## Rules that are not negotiable

1. **Synthetic data only.** Never add real patient data, real names, or real identifiers to this
   repository, to tests, to prompts, or to logs.
2. **PHI never reaches a log, an audit detail, an error message or a Pub/Sub message.** Log
   identifiers (EID, ingest ID, resource ID) and reasons. The tests check this in places; keep it
   true everywhere.
3. **Every read of patient data goes through `Policy.authorize_patient`.** Do not query
   `resources` for a patient from a request handler without the set of sources it returns.
4. **Every model call goes through `Gateway.run`.** Do not import a model SDK anywhere else. A
   new prompt is a new versioned file in `services/api/prompts/`; a changed prompt gets a new
   `version`.
5. **Model output is data, never instructions.** Extracted facts must pass `ground()`. Answers
   must cite supplied passages. An agent's choice of runbook is checked against the registry.
6. **Agents do not approve their own work.** Do not widen `allowed_tools` or
   `auto_approve_max_risk` in `config/agents.json`, and do not add IAM roles to `sa-ops-agent`,
   without a human security reviewer.
7. **A false merge is worse than a missed link.** Changes to the matcher must keep
   `false_merges: 0` in `make pipeline` and keep `TestTwinsAreNotMerged` passing.
8. **Clinical logic changes need a version bump** (`rules/*.yaml` `version`, `Measures.VERSION`)
   and a test that shows the new behavior.

## How to make a change

1. For anything beyond a small fix, write or update a spec from `specs/TEMPLATE.md` first.
2. Make the change with its tests. Prefer extending an existing test file.
3. Run `make test`. If you touched prompts, models, extraction or rules, also run
   `make pipeline && make eval`. If you touched access, audit, ops or evidence, also run
   `make evidence` and confirm no control fails.
4. Update the doc that describes what you changed, in the same change. Diagrams are Mermaid in
   Markdown so they diff and review like code.
5. In the PR, say what you verified and how. Say what you did not verify.

## What an agent must stop and ask a human about

- Anything in the "Governed files" list of the PR template.
- Adding a dependency to `services/gateway` (it is deliberately dependency-free).
- Anything that would send data outside the project: a new external API, a new egress rule.
- Deleting or rewriting audit data, or weakening a test that guards one of the rules above.
