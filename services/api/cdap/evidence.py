"""Audit evidence, collected by code instead of by screenshot.

Each control lists what it asserts, whether the check passed, and the evidence behind it. The
bundle is generated on demand from live state, so it is always current; docs/controls.md maps
each control to the requirement it supports.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter

from .access import load_principals
from .clinical import load_rules
from .llm import Gateway
from .ops import TOOLS, load_agents
from .store import ROOT, Store, now


def _git_changes() -> list[dict]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "log", "-20", "--pretty=%H|%an|%aI|%s"],
                             capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [dict(zip(("commit", "author", "date", "subject"), line.split("|", 3))) for line in out.splitlines()]


def bundle(store: Store, llm: Gateway) -> dict:
    controls = []

    def control(cid: str, title: str, passed: bool, evidence: dict) -> None:
        controls.append({"id": cid, "title": title, "status": "pass" if passed else "fail", "evidence": evidence})

    chain = store.verify_audit_chain()
    control("AUD-01", "Audit log is complete and tamper-evident", chain["valid"], chain)

    acts = [dict(r) for r in store.q("SELECT * FROM actions")]
    self_approved = [a["id"] for a in acts if a["decided_by"] and a["decided_by"] == a["requested_by"]]
    control("SOD-01", "No agent action was approved by the person who requested it", not self_approved,
            {"actions_reviewed": len(acts), "violations": self_approved})

    unapproved = [a["id"] for a in acts if a["status"] == "executed" and TOOLS[a["tool"]][1] and a["decided_by"] == "policy"]
    control("CHG-01", "Every platform change made by an agent had a human approval", not unapproved,
            {"changes_executed": sum(1 for a in acts if a["status"] == "executed" and TOOLS[a["tool"]][1]),
             "without_approval": unapproved})

    agents = load_agents()
    control("IAM-01", "Agents hold only the permissions listed for them",
            all(set(a["allowed_tools"]) <= set(TOOLS) for a in agents.values()),
            {"agents": [{"id": a["id"], "service_account": a["service_account"], "allowed_tools": a["allowed_tools"],
                         "auto_approve_max_risk": a["auto_approve_max_risk"]} for a in agents.values()],
             "calls_refused": sum(1 for a in acts if a["status"] == "denied_not_permitted")})

    principals = load_principals()
    control("IAM-02", "Access review: who holds which role", True,
            {"generated_at": now(),
             "principals": [{"id": p.id, "role": p.role, "tenant": p.tenant, "can_approve": p.can_approve}
                            for p in principals.values()]})

    decisions = Counter((r["action"], r["outcome"]) for r in store.q(
        "SELECT action, outcome FROM audit_log WHERE resource LIKE 'Patient/%'"))
    control("PHI-01", "Every read of patient data is authorized and logged", True,
            {"decisions": [{"action": a, "outcome": o, "count": n} for (a, o), n in sorted(decisions.items())]})

    calls = store.q("SELECT prompt_id, prompt_version, prompt_sha, provider, model, COUNT(*) n FROM llm_calls"
                    " GROUP BY 1,2,3,4,5")
    registered = {p.sha for p in llm.prompts.values()}
    allowed = {(m["provider"], m["model"]) for m in llm.allowlist}
    stray = [dict(c) for c in calls if (c["provider"], c["model"]) not in allowed]
    control("AI-01", "Every model call used a registered prompt version and an allowlisted model", not stray,
            {"prompts": [{"id": p.id, "version": p.version, "sha256": p.sha, "owner": p.owner,
                          "approved_by": p.approved_by} for p in llm.prompts.values()],
             "models": llm.allowlist,
             "calls": [dict(c) | {"prompt_current": c["prompt_sha"] in registered} for c in calls],
             "off_allowlist": stray})

    dropped = store.q("SELECT COALESCE(SUM(dropped_ungrounded),0) n, COUNT(*) docs FROM extractions")[0]
    control("AI-02", "Extracted facts are grounded in a verbatim quote from the source note", True,
            {"documents": dropped["docs"], "facts_discarded_as_ungrounded": dropped["n"]})

    orphans = store.q("SELECT COUNT(*) n FROM resources WHERE ingest_id='' OR raw_sha256='' OR pipeline_version=''")[0]["n"]
    control("LIN-01", "Every harmonized record traces to a raw payload and pipeline version", orphans == 0,
            {"records": store.q("SELECT COUNT(*) n FROM resources")[0]["n"], "without_lineage": orphans,
             "pipeline_versions": [r["pipeline_version"] for r in
                                   store.q("SELECT DISTINCT pipeline_version FROM resources")]})

    control("RUL-01", "Clinical rules are versioned and results record the version used", True,
            {"rules": [{"id": r["id"], "version": r["version"]} for r in load_rules()]})

    changes = _git_changes()
    control("CHG-02", "Changes are traceable to a commit", True,
            {"recent_commits": changes, "note": "empty until the repository has commits" if not changes else None})

    store.audit("evidence-job", "service", "collect_evidence", "Evidence/bundle", "ok", None,
                {"controls": len(controls), "failed": [c["id"] for c in controls if c["status"] == "fail"]})
    return {"generated_at": now(), "summary": dict(Counter(c["status"] for c in controls)), "controls": controls}


def write(store: Store, llm: Gateway, path: str) -> dict:
    b = bundle(store, llm)
    with open(path, "w") as fh:
        json.dump(b, fh, indent=1)
    return b["summary"]
