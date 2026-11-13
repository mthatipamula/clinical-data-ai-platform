"""Document extraction, grounded question answering, and the care-gap rules engine."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from .llm import API_DIR, Gateway
from .products import _code, note_text
from .store import ROOT, Store, now

FACT_KINDS = ("conditions", "medications", "labs", "social", "screenings")


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


# ------------------------------------------------------------------ extraction

def ground(facts: dict, note: str) -> tuple[dict, int]:
    """Keep only facts whose evidence quote appears verbatim in the note.

    This is the control that stops a model from adding a diagnosis the note never made: a fact
    with no matching quote is dropped and counted, whatever the model claimed.
    """
    haystack, kept, dropped = _norm(note), {}, 0
    for kind in FACT_KINDS:
        kept[kind] = []
        for f in facts.get(kind) or []:
            quote = f.get("evidence") if isinstance(f, dict) else None
            if isinstance(quote, str) and quote.strip() and _norm(quote) in haystack:
                kept[kind].append(f)
            else:
                dropped += 1
    return kept, dropped


def extract_documents(store: Store, llm: Gateway) -> dict:
    """Run extraction over every clinical note that has not been processed with this prompt version."""
    prompt = llm.prompts["extract_clinical_facts"]
    done = {r["document_id"] for r in store.q(
        "SELECT document_id FROM extractions WHERE prompt_sha=? AND model=?", (prompt.sha, llm.provider.model))}
    stats = {"processed": 0, "skipped": 0, "facts": 0, "dropped_ungrounded": 0}
    for r in store.q("SELECT * FROM resources WHERE resource_type='DocumentReference' ORDER BY resource_id"):
        if r["resource_id"] in done:
            stats["skipped"] += 1
            continue
        note = note_text(json.loads(r["body"]))
        raw, used = llm.run("extract_clinical_facts", {"note": note})
        facts, dropped = ground(raw, note)
        store.x("INSERT OR REPLACE INTO extractions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["resource_id"], r["eid"], r["source_system"], json.dumps(facts), dropped, used.id,
                 used.version, used.sha, llm.provider.name, llm.provider.model, now()))
        stats["processed"] += 1
        stats["facts"] += sum(len(v) for v in facts.values())
        stats["dropped_ungrounded"] += dropped
    store.audit("extraction-job", "service", "extract_documents", "DocumentReference/*", "ok", "operations",
                {**stats, "prompt": f"{prompt.id}@v{prompt.version}", "model": llm.provider.model})
    return stats


def evaluate_extraction(llm: Gateway, golden_path: Path | None = None) -> dict:
    """Score extraction against the golden set. Used as a release gate for prompt and model changes."""
    golden = json.loads((golden_path or ROOT / "data" / "synthetic" / "extraction_golden.json").read_text())

    def keys(facts: dict) -> dict[str, set]:
        return {
            "conditions": {c.get("code") for c in facts.get("conditions", [])},
            "medications": {(m.get("name"), m.get("dose"), m.get("frequency")) for m in facts.get("medications", [])},
            "labs": {(lab.get("test"), float(lab.get("value", 0)), lab.get("date")) for lab in facts.get("labs", [])},
            "social": {(s.get("type"), s.get("status")) for s in facts.get("social", [])},
        }

    tp, fp, fn = Counter(), Counter(), Counter()
    dropped = 0
    for case in golden:
        raw, _ = llm.run("extract_clinical_facts", {"note": case["text"]})
        got, d = ground(raw, case["text"])
        dropped += d
        g, e = keys(got), keys(case["expected"])
        for kind in e:
            tp[kind] += len(g[kind] & e[kind])
            fp[kind] += len(g[kind] - e[kind])
            fn[kind] += len(e[kind] - g[kind])
    report = {"cases": len(golden), "dropped_ungrounded": dropped, "model": llm.provider.model,
              "prompt": f"extract_clinical_facts@v{llm.prompts['extract_clinical_facts'].version}", "by_kind": {}}
    for kind in ("conditions", "medications", "labs", "social"):
        p = tp[kind] / (tp[kind] + fp[kind]) if tp[kind] + fp[kind] else 1.0
        r = tp[kind] / (tp[kind] + fn[kind]) if tp[kind] + fn[kind] else 1.0
        report["by_kind"][kind] = {"precision": round(p, 3), "recall": round(r, 3), "expected": tp[kind] + fn[kind]}
    report["min_score"] = min(min(v["precision"], v["recall"]) for v in report["by_kind"].values())
    return report


# ---------------------------------------------------------- question answering

def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def passages_for(record: dict) -> list[dict]:
    """Flatten a patient record (already filtered by access policy) into citable passages."""
    out: list[dict] = []

    def add(text: str, resource_id: str) -> None:
        out.append({"id": f"P{len(out) + 1}", "text": text, "resource_id": resource_id})

    for c in record["conditions"]:
        add(f"Condition on the problem list: {c['display']} ({c['code']}), onset {c['onset']}", c["resource_id"])
    for o in record["observations"]:
        add(f"Result: {o['display']} was {o['value']} on {o['date']}", o["resource_id"])
    for m in record["medications"]:
        add(f"Medication ordered: {m['display']} ({m['status']}, {m['authored']})", m["resource_id"])
    for e in record["encounters"]:
        kind = "Emergency department visit" if e["class"] == "EMER" else "Hospital encounter"
        add(f"{kind} on {(e['start'] or '')[:10]} for {e['reason']}", e["resource_id"])
    for d in record["documents"]:
        for line in d.get("text", "").splitlines():
            if len(line.strip()) > 25 and not line.startswith(("PROGRESS NOTE", "Electronically signed")):
                add(f"Clinical note {d['date']}: {line.strip()}", d["resource_id"])
    for m in record["measures"]:
        add(f"Quality measure {m['name']}: {m['detail']}", m["evidence"][0] if m["evidence"] else m["measure_id"])
    return out


def rank(question: str, passages: list[dict], k: int = 6) -> list[dict]:
    """BM25. Vertex AI Vector Search replaces this at scale; the interface is the same."""
    docs = [_tokens(p["text"]) for p in passages]
    if not docs:
        return []
    avg = sum(map(len, docs)) / len(docs)
    df = Counter(t for d in docs for t in set(d))
    scored = []
    for p, d in zip(passages, docs):
        tf, score = Counter(d), 0.0
        for t in set(_tokens(question)):
            if t in tf:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if score > 0:
            scored.append((score, p))
    return [p for _, p in sorted(scored, key=lambda sp: -sp[0])[:k]]


def answer_question(llm: Gateway, record: dict, question: str, audience: str) -> dict:
    top = rank(question, passages_for(record))
    if not top:
        return {"answer": "Nothing in the record available to you answers that question.", "citations": []}
    # Renumber so the model sees P1..Pn in rank order.
    numbered = [{**p, "id": f"P{i}"} for i, p in enumerate(top, 1)]
    context = "\n".join(f"[{p['id']}] {p['text']}" for p in numbered)
    out, used = llm.run("answer_patient_question", {"question": question, "context": context, "audience": audience})
    by_id = {p["id"]: p for p in numbered}
    cited = [by_id[c] for c in out.get("citations", []) if c in by_id]
    if not cited:
        # An answer that cites nothing we supplied is not grounded; do not show it.
        return {"answer": "The record does not contain a supported answer to that question.", "citations": []}
    return {"answer": str(out.get("answer", "")), "citations": cited, "model": llm.provider.model,
            "prompt": f"{used.id}@v{used.version}"}


# ----------------------------------------------------------------- rules engine

OPS = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b, "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b, "lte": lambda a, b: a is not None and a <= b,
    "exists": lambda a, _: a is not None, "missing": lambda a, _: a is None,
}


def load_rules(path: Path | None = None) -> list[dict]:
    rules = yaml.safe_load((path or API_DIR / "rules" / "care_gaps.yaml").read_text())["rules"]
    for r in rules:
        for cond in r["when"].get("all", []) + r["when"].get("any", []):
            if cond["op"] not in OPS:
                raise ValueError(f"rule {r['id']}: unknown operator {cond['op']}")
    return rules


def lookup(facts: dict, path: str):
    node = facts
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def matches(rule: dict, facts: dict) -> bool:
    def test(c: dict) -> bool:
        return OPS[c["op"]](lookup(facts, c["fact"]), c.get("value"))
    when = rule["when"]
    return all(test(c) for c in when.get("all", [])) and (not when.get("any") or any(test(c) for c in when["any"]))


def build_facts(store: Store, eid: str, permitted: set[str], year: int) -> tuple[dict, dict]:
    """Facts for one person, from operations-permitted sources only. Returns (facts, evidence ids)."""
    facts: dict = {"measures": {}, "structured": {}, "notes": {}, "utilization": {"ed_visits_ytd": 0},
                   "demographics": {}}
    ev: dict = {}
    for r in store.q("SELECT * FROM measure_results WHERE eid=?", (eid,)):
        facts["measures"][r["measure_id"]] = {"in_denominator": True, "numerator": bool(r["numerator"])}
        ev[f"measures.{r['measure_id']}"] = json.loads(r["evidence"])
    latest = None
    for r in store.resources(eid):
        if r["source_system"] not in permitted:
            continue
        b = r["body"]
        if r["resource_type"] == "Observation" and _code(b.get("code"))[0] == "4548-4":
            when = (b.get("effectiveDateTime") or "")[:10]
            if when[:4] == str(year) and (latest is None or when > latest):
                latest = when
                facts["structured"]["latest_a1c"] = b["valueQuantity"]["value"]
                ev["structured.latest_a1c"] = [r["resource_id"]]
        elif r["resource_type"] == "Encounter" and b.get("class", {}).get("code") == "EMER":
            if (b.get("period", {}).get("start") or "")[:4] == str(year):
                facts["utilization"]["ed_visits_ytd"] += 1
                ev.setdefault("utilization.ed_visits_ytd", []).append(r["resource_id"])
        elif r["resource_type"] == "Patient" and b.get("birthDate") and "age" not in facts["demographics"]:
            facts["demographics"]["age"] = year - date.fromisoformat(b["birthDate"]).year
    for r in store.q("SELECT * FROM extractions WHERE eid=?", (eid,)):
        if r["source_system"] not in permitted:
            continue
        x = json.loads(r["facts"])
        for lab in x.get("labs", []):
            if lab.get("test") == "hba1c" and str(lab.get("date", ""))[:4] == str(year):
                facts["notes"]["latest_a1c"] = lab["value"]
                ev["notes.latest_a1c"] = [r["document_id"]]
        for s in x.get("social", []):
            if s.get("type") == "tobacco_use":
                facts["notes"]["tobacco_use"] = s["status"]
                ev["notes.tobacco_use"] = [r["document_id"]]
    return facts, ev


def run_rules(store: Store, permitted: set[str], year: int = 2026) -> dict:
    rules = load_rules()
    store.x("DELETE FROM care_gaps")
    fired: Counter = Counter()
    for row in store.q("SELECT DISTINCT eid FROM resources"):
        facts, ev = build_facts(store, row["eid"], permitted, year)
        for rule in rules:
            if not matches(rule, facts):
                continue
            used = sorted({i for c in rule["when"].get("all", []) + rule["when"].get("any", [])
                           for key, ids in ev.items() if c["fact"].startswith(key) for i in ids})
            store.x("INSERT OR REPLACE INTO care_gaps VALUES (?,?,?,?,?,?,?,?)",
                    (row["eid"], rule["id"], rule["version"], rule["title"], rule["severity"],
                     " ".join(rule["recommendation"].split()), " ".join(rule["patient_text"].split()),
                     json.dumps(used)))
            fired[rule["id"]] += 1
    store.audit("rules-job", "service", "run_rules", "CareGap/*", "ok", "operations",
                {"fired": dict(fired), "rules": {r["id"]: r["version"] for r in rules}})
    return dict(fired)
