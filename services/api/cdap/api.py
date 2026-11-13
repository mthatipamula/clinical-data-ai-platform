"""HTTP API for the web app.

Sign-in: in local development the caller names a demo principal in the X-Demo-User header. That
mode is refused when CDAP_ENV=prod; production sits behind Identity-Aware Proxy and the verified
identity header replaces it (not implemented yet, see README "Not built yet").
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import clinical, evidence, products
from .access import Forbidden, Policy, Principal, load_principals
from .llm import Gateway, GovernanceError, ModelRefusal
from .ops import Ops
from .store import Store

if os.environ.get("CDAP_ENV") == "prod":
    raise RuntimeError("Demo sign-in must not run in production. Configure Identity-Aware Proxy first.")

app = FastAPI(title="Clinical Data and AI Platform", version="0.1.0")
store = Store()
policy = Policy(store)
llm = Gateway(store)
ops = Ops(store, llm)
principals = load_principals()


@app.exception_handler(Forbidden)
async def forbidden(_: Request, exc: Forbidden) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(GovernanceError)
async def governance(_: Request, exc: GovernanceError) -> JSONResponse:
    return JSONResponse({"detail": f"blocked by model governance: {exc}"}, status_code=409)


@app.exception_handler(ModelRefusal)
async def refusal(_: Request, exc: ModelRefusal) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


def current(x_demo_user: str | None = Header(default=None)) -> Principal:
    p = principals.get(x_demo_user or "")
    if not p:
        raise HTTPException(401, "unknown or missing user")
    return p


User = Depends(current)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/demo-users")
def demo_users() -> list[dict]:
    return [{"id": p.id, "name": p.name, "role": p.role} for p in principals.values()]


@app.get("/api/me")
def me(p: Principal = User) -> dict:
    return {"id": p.id, "name": p.name, "role": p.role, "tenant": p.tenant, "can_approve": p.can_approve,
            "purpose_of_use": p.purpose, "own_eid": policy.own_eid(p)}


# ------------------------------------------------------------------- patients

@app.get("/api/patients")
def patients(p: Principal = User) -> list[dict]:
    policy.require_role(p, ("provider", "payer"), "list_patients", "Patient/*")
    return products.patient_list(store, policy.panel(p), lambda eid: policy.visible_sources(p, eid))


@app.get("/api/patients/{eid}")
def patient(eid: str, p: Principal = User) -> dict:
    sources = policy.authorize_patient(p, eid, "read_patient")
    # Minimum necessary: payers get facts derived from notes, not the notes themselves.
    record = products.patient_record(store, eid, sources, include_note_text=p.role != "payer")
    gaps = products.care_gaps(store, eid)
    if p.role == "patient":
        gaps = [{k: g[k] for k in ("rule_id", "title", "severity", "patient_text")} for g in gaps]
    out: dict[str, Any] = {"demographics": products.demographics(store, eid, sources), **record,
                           "care_gaps": gaps, "sources": sorted(sources)}
    if p.role == "patient":
        out["restricted_sources"] = sorted(s for s, cfg in policy.sources.items()
                                           if cfg.get("requires_consent") and s in sources
                                           and any(r["source_system"] == s for r in store.resources(eid)))
    return out


@app.get("/api/patients/{eid}/lineage/{resource_id}")
def lineage(eid: str, resource_id: str, p: Principal = User) -> dict:
    sources = policy.authorize_patient(p, eid, "read_lineage")
    out = products.lineage(store, eid, resource_id, sources)
    if not out:
        raise HTTPException(404, "no such record")
    return out


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.post("/api/patients/{eid}/ask")
def ask(eid: str, body: Question, p: Principal = User) -> dict:
    sources = policy.authorize_patient(p, eid, "ask_question")
    record = products.patient_record(store, eid, sources, include_note_text=p.role != "payer")
    audience = {"patient": "the patient, in plain language", "provider": "a treating clinician",
                "payer": "a health plan quality analyst"}[p.role]
    return clinical.answer_question(llm, record, body.question, audience)


# ----------------------------------------------------------------- population

@app.get("/api/population/measures")
def population_measures(p: Principal = User) -> list[dict]:
    policy.require_role(p, ("provider", "payer"), "read_population_measures", "Population/measures")
    return products.population_measures(store, policy.panel(p))


@app.get("/api/population/cost")
def population_cost(p: Principal = User) -> dict:
    policy.require_role(p, ("payer",), "read_population_cost", "Population/cost")
    cost = products.population_cost(store, policy.panel(p), p.tenant or "")
    names = {eid: products.demographics(store, eid, policy.visible_sources(p, eid))["name"]
             for eid in (m["eid"] for m in cost["top_members"])}
    for m in cost["top_members"]:
        m["name"] = names[m["eid"]]
    return cost


# ------------------------------------------------------------------ operations

class Alert(BaseModel):
    type: str
    resource: str
    severity: str = "medium"
    description: str = ""


@app.get("/api/ops/incidents")
def incidents(p: Principal = User) -> dict:
    policy.require_role(p, ("operator",), "list_incidents", "Incident/*")
    return {"incidents": ops.incidents(), "metrics": ops.metrics(), "platform": ops.state()}


@app.post("/api/ops/incidents")
def open_incident(alert: Alert, p: Principal = User) -> dict:
    policy.require_role(p, ("operator",), "open_incident", "Incident/*")
    return ops.open_incident(alert.model_dump(), p.id)


@app.post("/api/ops/incidents/{iid}/triage")
def triage(iid: str, p: Principal = User) -> dict:
    policy.require_role(p, ("operator",), "request_triage", f"Incident/{iid}")
    try:
        return ops.triage(iid, p)
    except KeyError:
        raise HTTPException(404, "no such incident") from None


class Decision(BaseModel):
    approve: bool


@app.post("/api/ops/actions/{action_id}/decision")
def decide(action_id: str, body: Decision, p: Principal = User) -> dict:
    try:
        return ops.decide(action_id, p, body.approve)
    except KeyError:
        raise HTTPException(404, "no such action") from None


# ------------------------------------------------------------------ compliance

@app.get("/api/audit")
def audit(limit: int = 100, p: Principal = User) -> dict:
    policy.require_role(p, ("auditor", "operator"), "read_audit_log", "AuditLog/*")
    rows = store.q("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (min(max(limit, 1), 500),))
    return {"chain": store.verify_audit_chain(), "entries": [dict(r) for r in rows]}


@app.get("/api/compliance/evidence")
def compliance_evidence(p: Principal = User) -> dict:
    policy.require_role(p, ("auditor",), "collect_evidence", "Evidence/bundle")
    return evidence.bundle(store, llm)
