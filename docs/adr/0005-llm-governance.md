# ADR 0005: LLM governance

Status: accepted

## Context

Models read clinical notes and answer questions about records. A model can be wrong with
confidence, can be steered by text inside a document, and can change behavior when a prompt or
model version changes.

## Decision

1. **One call path.** `Gateway.run(prompt_id, variables)` is the only way to call a model. It
   loads a versioned prompt, checks the model against an allowlist for that task, calls the
   provider, and records prompt version, prompt hash, model, and hashes of input and output.
   Content is never logged.
2. **Prompts and the allowlist are files under review.** A prompt change is a new `version`; a
   new model is a new allowlist entry with a named approver. Outside production a listed but
   unapproved model may run so it can be evaluated; in production it is refused.
3. **Outputs are checked by code, not trusted.**
   - Extraction: a fact is kept only if its evidence quote appears verbatim in the note.
   - Q&A: an answer is shown only if it cites passages that were supplied, and the passages come
     only from sources the caller may see.
   - Triage: a chosen runbook must exist in the registry.
4. **Evaluation gates release.** `make eval` scores extraction on a golden set and fails below
   the bar. A prompt or model change that lowers the score does not ship.
5. **Providers are pluggable.** Offline (deterministic, no network) for tests and local runs;
   Gemini and Claude through Vertex AI in the cloud, so prompts and PHI stay inside the perimeter.

## Trade-offs

| Choice | Benefit | Cost |
|---|---|---|
| Verbatim grounding | No invented diagnoses | Drops true facts the model paraphrased; recall depends on prompt discipline |
| Hashes, not content, in the call log | No PHI in logs | Debugging a bad output needs a deliberate, access-controlled replay |
| Offline provider by default | Everything runs and tests without credentials | Its eval score says the plumbing works, not how a real model performs |

## Not yet done

The Vertex providers are written but have not been run against Vertex AI, and no real model has
been scored on the golden set. The golden set is synthetic and template-shaped; a clinician-labelled
set of real, de-identified notes is needed before any model is approved.
