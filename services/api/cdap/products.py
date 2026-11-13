"""Data products: read models built from harmonized FHIR for the three audiences.

Every function takes the set of source systems the caller may see and filters on it first, so a
product can never contain a record the access policy withheld.
"""
from __future__ import annotations

import base64
import json
from collections import defaultdict
from datetime import date

from .store import Store

MEASURE_NAMES = {
    "DM-A1C-POOR-CONTROL": "Diabetes: HbA1c poor control (>9%) — lower is better",
    "HTN-BP-CONTROL": "Hypertension: blood pressure controlled (<140/90)",
    "BCS-MAMMOGRAPHY": "Breast cancer screening",
}


def _code(cc: dict) -> tuple[str, str]:
    c = (cc or {}).get("coding", [{}])[0]
    return c.get("code", ""), c.get("display") or (cc or {}).get("text") or c.get("code", "")


def note_text(doc_ref: dict) -> str:
    data = doc_ref.get("content", [{}])[0].get("attachment", {}).get("data", "")
    return base64.b64decode(data).decode("utf-8") if data else ""


def demographics(store: Store, eid: str, sources: set[str]) -> dict:
    rows = [r for r in store.resources(eid, "Patient") if r["source_system"] in sources]
    if not rows:
        return {"eid": eid, "name": "Unknown"}
    # Prefer the clinical system of record, then anything with a full address.
    rows.sort(key=lambda r: (r["source_system"] != "ehr-northside", r["resource_id"]))
    p = rows[0]["body"]
    name = p.get("name", [{}])[0]
    addr = p.get("address", [{}])[0]
    dob = p.get("birthDate")
    age = None
    if dob:
        b, t = date.fromisoformat(dob), date.today()
        age = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    return {"eid": eid, "name": f"{' '.join(name.get('given', []))} {name.get('family', '')}".strip().title(),
            "birth_date": dob, "age": age, "sex": p.get("gender"),
            "city": (addr.get("city") or "").title(), "state": addr.get("state")}


def patient_record(store: Store, eid: str, sources: set[str], include_note_text: bool) -> dict:
    out: dict = {"conditions": [], "observations": [], "medications": [], "encounters": [], "claims": [],
                 "documents": []}
    for r in store.resources(eid):
        if r["source_system"] not in sources:
            continue
        b, base = r["body"], {"resource_id": r["resource_id"], "source": r["source_system"]}
        t = r["resource_type"]
        if t == "Condition":
            code, display = _code(b.get("code"))
            out["conditions"].append(base | {"code": code, "display": display, "onset": b.get("onsetDateTime")})
        elif t == "Observation":
            code, display = _code(b.get("code"))
            if "valueQuantity" in b:
                value = f"{b['valueQuantity']['value']} {b['valueQuantity'].get('unit', '')}".strip()
            else:
                value = "/".join(str(int(c["valueQuantity"]["value"])) for c in b.get("component", []))
            out["observations"].append(base | {"code": code, "display": display, "value": value,
                                               "date": (b.get("effectiveDateTime") or "")[:10]})
        elif t == "MedicationRequest":
            out["medications"].append(base | {"display": _code(b.get("medicationCodeableConcept"))[1],
                                              "authored": b.get("authoredOn"), "status": b.get("status")})
        elif t == "Encounter":
            reason = _code(b["reasonCode"][0]) if b.get("reasonCode") else ("", "")
            out["encounters"].append(base | {"class": b.get("class", {}).get("code"),
                                             "start": b.get("period", {}).get("start"),
                                             "end": b.get("period", {}).get("end"), "reason": reason[1]})
        elif t == "Claim":
            items = b.get("item", [])
            out["claims"].append(base | {
                "service_date": items[0].get("servicedDate") if items else (b.get("created") or "")[:10],
                "total": float(b.get("total", {}).get("value", 0)),
                "diagnoses": [_code(d.get("diagnosisCodeableConcept"))[0] for d in b.get("diagnosis", [])],
                "procedures": [_code(i.get("productOrService"))[0] for i in items]})
        elif t == "DocumentReference":
            doc = base | {"date": (b.get("date") or "")[:10], "type": _code(b.get("type"))[1]}
            if include_note_text:
                doc["text"] = note_text(b)
            out["documents"].append(doc)
    for k, key in (("observations", "date"), ("encounters", "start"), ("claims", "service_date")):
        out[k].sort(key=lambda x: x.get(key) or "", reverse=True)

    out["note_facts"] = [
        {"document_id": r["document_id"], "facts": json.loads(r["facts"]), "model": r["model"],
         "prompt": f"{r['prompt_id']}@v{r['prompt_version']}"}
        for r in store.q("SELECT * FROM extractions WHERE eid=?", (eid,)) if r["source_system"] in sources]
    out["measures"] = [
        {"measure_id": r["measure_id"], "name": MEASURE_NAMES.get(r["measure_id"], r["measure_id"]),
         "met": bool(r["numerator"]) != bool(r["inverse"]), "detail": r["detail"],
         "evidence": json.loads(r["evidence"]), "version": r["measure_version"]}
        for r in store.q("SELECT * FROM measure_results WHERE eid=? ORDER BY measure_id", (eid,))]
    return out


def care_gaps(store: Store, eid: str) -> list[dict]:
    order = {"high": 0, "medium": 1, "low": 2}
    rows = [dict(r) | {"evidence": json.loads(r["evidence"])} for r in
            store.q("SELECT * FROM care_gaps WHERE eid=?", (eid,))]
    return sorted(rows, key=lambda g: (order.get(g["severity"], 9), g["rule_id"]))


def patient_list(store: Store, eids: list[str], sources_for) -> list[dict]:
    gaps: dict[str, list] = defaultdict(list)
    for r in store.q("SELECT eid, severity FROM care_gaps"):
        gaps[r["eid"]].append(r["severity"])
    out = []
    for eid in eids:
        d = demographics(store, eid, sources_for(eid))
        g = gaps.get(eid, [])
        out.append(d | {"open_gaps": len(g), "high_severity_gaps": g.count("high")})
    return sorted(out, key=lambda x: (-x["high_severity_gaps"], -x["open_gaps"], x["name"]))


def population_measures(store: Store, eids: list[str]) -> list[dict]:
    scope = set(eids)
    agg: dict[str, dict] = {}
    for r in store.q("SELECT * FROM measure_results"):
        if r["eid"] not in scope:
            continue
        a = agg.setdefault(r["measure_id"], {"measure_id": r["measure_id"], "inverse": bool(r["inverse"]),
                                             "name": MEASURE_NAMES.get(r["measure_id"], r["measure_id"]),
                                             "denominator": 0, "numerator": 0, "version": r["measure_version"]})
        a["denominator"] += 1
        a["numerator"] += r["numerator"]
    for a in agg.values():
        a["rate"] = round(a["numerator"] / a["denominator"], 3) if a["denominator"] else None
    return sorted(agg.values(), key=lambda a: a["measure_id"])


def population_cost(store: Store, eids: list[str], tenant: str) -> dict:
    """Billed charges from the caller's own claims feed."""
    scope = set(eids)
    by_month: dict[str, float] = defaultdict(float)
    by_member: dict[str, float] = defaultdict(float)
    ed_visits: dict[str, int] = defaultdict(int)
    for r in store.q("SELECT eid, body FROM resources WHERE resource_type='Claim' AND tenant=?", (tenant,)):
        if r["eid"] not in scope:
            continue
        b = json.loads(r["body"])
        total = float(b.get("total", {}).get("value", 0))
        items = b.get("item", [])
        month = (items[0].get("servicedDate") if items else b.get("created", ""))[:7]
        by_month[month] += total
        by_member[r["eid"]] += total
        if any(_code(i.get("productOrService"))[0] in ("99281", "99282", "99283", "99284", "99285") for i in items):
            ed_visits[r["eid"]] += 1
    top = sorted(by_member.items(), key=lambda kv: -kv[1])[:8]
    return {"total_billed": round(sum(by_member.values()), 2), "members": len(by_member),
            "by_month": [{"month": m, "billed": round(v, 2)} for m, v in sorted(by_month.items())],
            "top_members": [{"eid": e, "billed": round(v, 2), "ed_visits": ed_visits.get(e, 0)} for e, v in top]}


def lineage(store: Store, eid: str, resource_id: str, sources: set[str]) -> dict | None:
    rows = store.q("SELECT * FROM resources WHERE eid=? AND resource_id=?", (eid, resource_id))
    if not rows or rows[0]["source_system"] not in sources:
        return None
    r = rows[0]
    return {"resource_id": r["resource_id"], "resource_type": r["resource_type"],
            "steps": [
                {"stage": "source", "system": r["source_system"], "tenant": r["tenant"]},
                {"stage": "raw landing", "ingest_id": r["ingest_id"], "sha256": r["raw_sha256"]},
                {"stage": "identity resolution", "enterprise_id": r["eid"]},
                {"stage": "harmonization", "pipeline_version": r["pipeline_version"], "format": "FHIR R4"}]}
