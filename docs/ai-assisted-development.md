# AI-assisted development practices

How engineers and AI coding agents work in this repository. The aim is faster delivery with the
same or better quality, and proof of both.

## 1. Specifications

Work beyond a small fix starts from a one-page spec (`specs/TEMPLATE.md`): the problem,
observable behavior, what is out of scope, constraints on data and security, and the commands that
prove it works. An agent given a spec with a verification section produces a change that can be
checked; an agent given a sentence produces a change that has to be reverse-engineered.

## 2. Guardrails

| Guardrail | Mechanism |
|---|---|
| Shared context | `CLAUDE.md` gives every agent the layout, commands and the rules that are not negotiable |
| Governed files | `CODEOWNERS` routes prompts, model and agent permissions, access policy and clinical logic to a named human owner |
| Stop-and-ask list | `CLAUDE.md` names the changes an agent must hand to a person: new permissions, new egress, new dependencies in the gateway, weakening a safety test |
| No real data | Synthetic data only, generated deterministically; agents never need production access to work |
| Least privilege for agents | Coding agents work in a checkout with no cloud credentials. Deploys happen only through the pipeline |
| Tests that encode the rules | The twins test, the consent tests, the grounding test and the separation-of-duties tests fail if a rule is broken, whoever wrote the change |

## 3. Code review

- A human reads every line of an agent-written diff before merge. The PR template asks the author
  to confirm it.
- An AI reviewer may run first. Its findings are advice; the author records which were acted on
  and which were declined, and why.
- The author of a change is never its only reviewer, and an agent never approves its own change.
- Review effort follows risk: governed files and anything touching patient data get a second
  human reviewer.

## 4. Evaluation

Agents are measured the way the product is: by gates that run on every change.

| Gate | What it protects |
|---|---|
| `make test` | Behavior of each service, including the safety rules above |
| `make pipeline` identity check | Fails if two people are merged |
| `make eval` | Fails if LLM extraction quality drops below the bar |
| `make evidence` | Fails if any control lacks passing evidence |
| `make tf-validate` | Infrastructure stays valid and formatted |

## 5. Human oversight

People decide what to build, approve what ships, and own what runs. Agents draft, test, review and
explain. Any change to what an agent is allowed to do is itself a reviewed change.

## 6. Piloting and measuring

Run pilots with two or three teams for six weeks against their own previous six weeks, then
compare. Record for each merged change whether an agent contributed (the PR template captures
this).

| Dimension | Measure | Read it with |
|---|---|---|
| Speed | Lead time from first commit to merge; PRs merged per engineer per week | Change size, so small PRs do not flatter the number |
| Quality | Change failure rate; reverts; defects found after merge per 100 changes | Test coverage of changed lines |
| Review load | Review time per PR; review comments per 100 changed lines | Whether AI review ran first |
| Safety | Governed-file changes merged without owner review (target 0); guardrail test failures caught in CI | |
| Experience | Short survey: where agents helped, where they cost time | |

Adopt a practice more widely when speed improves and quality does not get worse. If quality
slips, tighten specs and gates before adding reviewers.

## 7. Docs and diagrams as code

Architecture, decisions, objectives and controls live in `docs/` as Markdown with Mermaid
diagrams. They change in the same PR as the code they describe, so the description an agent reads
is the system as it is.
