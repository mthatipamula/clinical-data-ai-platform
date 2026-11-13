# Spec: <short title>

Status: draft | approved | implemented
Owner: <person accountable for the outcome>

A spec is the contract an engineer or an AI agent builds against. Write it before the code.
Keep it to one page; if it does not fit, split the work.

## Problem

Who is affected, what they cannot do today, and how we will know it is fixed.

## Behavior

What the system does after this change, as observable statements. Each one should be testable.

- Given ..., when ..., then ...

## Out of scope

What this change deliberately does not do.

## Constraints

- Data: which sources and purposes of use are involved; anything consent-restricted.
- Security: new permissions, new egress, new secrets (expected answer: none).
- Compatibility: schemas, APIs or stored data that must keep working.

## Verification

The commands that prove the behavior, and the evals or gates that must stay green
(`make test`, `make eval`, `make evidence`).

## Rollout and rollback

How it ships, what to watch, and how to undo it.
