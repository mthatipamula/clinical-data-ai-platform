"""AI-assisted operations: an agent that triages alerts and proposes runbook actions.

The model's job is narrow: read an alert, pick a runbook from the registry, explain why. Everything
that matters for safety is enforced here in code, outside the model:

* least privilege: the agent can call only tools on its allowlist (config/agents.json); a runbook
  step naming any other tool is refused and recorded;
* approval gates: read-only tools run at once; anything that changes the platform waits for a
  human with approval rights;
* separation of duties: the person who started the triage cannot approve its actions;
* audit: every proposal, refusal, approval and execution is written to the hash-chained log.

Tool executors here act on a simulated platform state so the flow is demonstrable offline. The
Google Cloud executors (Monitoring, Dataflow, BigQuery APIs) are not written yet.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import yaml

from .access import Forbidden, Principal
from .llm import API_DIR, Gateway
from .store import ROOT, Store, now

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# name -> (risk, changes the platform?)
TOOLS: dict[str, tuple[str, bool]] = {
    "get_pubsub_backlog": ("low", False),
    "get_dataflow_job_status": ("low", False),
    "get_recent_deploys": ("low", False),
    "get_bq_spend_by_label": ("low", False),
    "scale_dataflow_workers": ("medium", True),
    "cancel_bq_job": ("medium", True),
    "rollback_deployment": ("high", True),
    "purge_dataset": ("high", True),
}

DEFAULT_STATE = {
    "pubsub": {"raw-ingest-harmonizer": {"backlog_messages": 184000, "oldest_unacked_age_s": 2700}},
    "dataflow": {"harmonizer": {"workers": 4, "max_workers": 4, "error_rate": 0.002, "dead_letters_1h": 3}},
    "deploys": {"harmonizer": [{"version": "0.1.0", "at": "2026-11-12T16:02:00Z", "by": "release-pipeline"}]},
    "bigquery": {"spend_24h_usd": 1840.0, "baseline_24h_usd": 310.0,
                 "by_label": [{"label": "team:analytics", "job_id": "bq-job-7731", "usd": 1490.0,
                               "note": "full scan of harmonized.fhir_resources without partition filter"}]},
}


def load_agents() -> dict[str, dict]:
    return {a["id"]: a for a in json.loads((ROOT / "config" / "agents.json").read_text())["agents"]}


def load_runbooks(directory: Path | None = None) -> dict[str, dict]:
    return {rb["id"]: rb for rb in
            (yaml.safe_load(f.read_text()) for f in sorted((directory or API_DIR / "runbooks").glob("*.yaml")))}


class Ops:
    def __init__(self, store: Store, llm: Gateway):
        self.store, self.llm = store, llm
        self.agents, self.runbooks = load_agents(), load_runbooks()

    # ------------------------------------------------------ simulated state
    def state(self) -> dict:
        rows = self.store.q("SELECT value FROM platform_state WHERE key='state'")
        return json.loads(rows[0]["value"]) if rows else json.loads(json.dumps(DEFAULT_STATE))

    def _save(self, state: dict) -> None:
        self.store.x("INSERT OR REPLACE INTO platform_state VALUES ('state', ?)", (json.dumps(state),))

    def _execute(self, tool: str, args: dict) -> dict:
        s = self.state()
        out: dict
        if tool == "get_pubsub_backlog":
            out = s["pubsub"].get(args.get("subscription", ""), {})
        elif tool == "get_dataflow_job_status":
            out = s["dataflow"].get(args.get("job", ""), {})
        elif tool == "get_recent_deploys":
            out = {"deploys": s["deploys"].get(args.get("service", ""), [])}
        elif tool == "get_bq_spend_by_label":
            out = s["bigquery"]
        elif tool == "scale_dataflow_workers":
            job = s["dataflow"][args["job"]]
            job["max_workers"] = job["workers"] = int(args["max_workers"])
            for sub in s["pubsub"].values():
                sub.update(backlog_messages=1200, oldest_unacked_age_s=45)
            out = {"job": args["job"], "workers": job["workers"]}
        elif tool == "cancel_bq_job":
            job_id = s["bigquery"]["by_label"][0]["job_id"] if args.get("job_id") == "from_diagnostics" else args["job_id"]
            s["bigquery"].update(spend_24h_usd=s["bigquery"]["baseline_24h_usd"], by_label=[])
            out = {"cancelled": job_id}
        elif tool == "rollback_deployment":
            s["dataflow"][args["service"]].update(error_rate=0.0, dead_letters_1h=0)
            out = {"service": args["service"], "rolled_back": True}
        else:
            raise ValueError(f"no executor for {tool}")
        self._save(s)
        return out | {"simulated": True}

    # ------------------------------------------------------------ incidents
    def open_incident(self, alert: dict, actor: str) -> dict:
        iid = "inc-" + uuid.uuid4().hex[:8]
        self.store.x("INSERT INTO incidents VALUES (?,?,?,?,?)", (iid, now(), json.dumps(alert), "open", None))
        self.store.audit(actor, "user", "open_incident", f"Incident/{iid}", "ok", None, {"alert_type": alert.get("type")})
        return self.incident(iid)

    def incident(self, iid: str) -> dict:
        rows = self.store.q("SELECT * FROM incidents WHERE id=?", (iid,))
        if not rows:
            raise KeyError(iid)
        r = rows[0]
        return {"id": r["id"], "created_at": r["created_at"], "alert": json.loads(r["alert"]), "status": r["status"],
                "triage": json.loads(r["triage"]) if r["triage"] else None,
                "actions": [self._action(a) for a in self.store.q(
                    "SELECT * FROM actions WHERE incident_id=? ORDER BY rowid", (iid,))]}

    def incidents(self) -> list[dict]:
        return [self.incident(r["id"]) for r in self.store.q("SELECT id FROM incidents ORDER BY rowid DESC")]

    @staticmethod
    def _action(a) -> dict:
        d = dict(a)
        d["args"] = json.loads(d["args"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    # --------------------------------------------------------------- triage
    def triage(self, iid: str, requested_by: Principal, agent_id: str = "ops-triage-agent") -> dict:
        agent = self.agents[agent_id]
        inc = self.incident(iid)
        if inc["triage"]:
            return inc
        catalog = [{"id": rb["id"], "alert_type": rb["alert_type"], "title": rb["title"],
                    "hypothesis": rb["hypothesis"]} for rb in self.runbooks.values()]
        out, prompt = self.llm.run("triage_incident", {"alert": json.dumps(inc["alert"]),
                                                       "runbooks": json.dumps(catalog)})
        runbook = self.runbooks.get(out.get("runbook_id") or "")
        triage = {"summary": out.get("summary"), "hypothesis": out.get("hypothesis"),
                  "severity": out.get("severity"), "runbook_id": runbook["id"] if runbook else None,
                  "agent": agent_id, "model": self.llm.provider.model, "prompt": f"{prompt.id}@v{prompt.version}",
                  "requested_by": requested_by.id}
        self.store.x("UPDATE incidents SET triage=?, status=? WHERE id=?",
                     (json.dumps(triage), "triaged" if runbook else "needs_human", iid))
        self.store.audit(agent_id, "agent", "triage", f"Incident/{iid}", "ok", None,
                         {"runbook": triage["runbook_id"], "on_behalf_of": requested_by.id})

        for step in (runbook or {}).get("steps", []):
            tool, args = step["tool"], step.get("args", {})
            aid = "act-" + uuid.uuid4().hex[:8]
            risk = TOOLS.get(tool, ("high", True))[0]
            if tool not in agent["allowed_tools"] or tool not in TOOLS:
                status, result = "denied_not_permitted", {"reason": f"{agent_id} is not permitted to call {tool}"}
            elif RISK_ORDER[risk] <= RISK_ORDER[agent["auto_approve_max_risk"]] and not TOOLS[tool][1]:
                status, result = "executed", self._execute(tool, args)
            else:
                status, result = "pending_approval", None
            self.store.x("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (aid, iid, agent_id, tool, json.dumps(args), step.get("why"), risk, status,
                          requested_by.id, "policy" if status != "pending_approval" else None,
                          now() if status != "pending_approval" else None,
                          json.dumps(result) if result else None, now()))
            self.store.audit(agent_id, "agent", f"tool:{tool}", f"Action/{aid}",
                             {"executed": "auto_executed", "pending_approval": "awaiting_approval"}.get(status, "denied"),
                             None, {"incident": iid, "risk": risk, "args": args})
        return self.incident(iid)

    # ------------------------------------------------------------ approvals
    def decide(self, action_id: str, approver: Principal, approve: bool) -> dict:
        rows = self.store.q("SELECT * FROM actions WHERE id=?", (action_id,))
        if not rows:
            raise KeyError(action_id)
        a = rows[0]

        def refuse(reason: str):
            self.store.audit(approver.id, "user", "decide_action", f"Action/{action_id}", "deny", None, {"reason": reason})
            raise Forbidden(reason)

        if a["status"] != "pending_approval":
            refuse(f"action is {a['status']}, not awaiting approval")
        if not approver.can_approve:
            refuse("you do not hold approval rights")
        if approver.id == a["requested_by"]:
            refuse("separation of duties: the requester cannot approve their own agent's action")

        result = self._execute(a["tool"], json.loads(a["args"])) if approve else None
        status = "executed" if approve else "rejected"
        self.store.x("UPDATE actions SET status=?, decided_by=?, decided_at=?, result=? WHERE id=?",
                     (status, approver.id, now(), json.dumps(result) if result else None, action_id))
        self.store.audit(approver.id, "user", "approve_action" if approve else "reject_action",
                         f"Action/{action_id}", "ok", None,
                         {"tool": a["tool"], "agent": a["agent_id"], "requested_by": a["requested_by"]})
        pending = self.store.q("SELECT 1 FROM actions WHERE incident_id=? AND status='pending_approval'", (a["incident_id"],))
        if not pending:
            self.store.x("UPDATE incidents SET status='mitigated' WHERE id=? AND status='triaged'", (a["incident_id"],))
        return self.incident(a["incident_id"])

    def metrics(self) -> dict:
        """Inputs to the automation SLOs in docs/slo.md."""
        acts = [dict(r) for r in self.store.q("SELECT * FROM actions")]
        by = lambda s: sum(1 for a in acts if a["status"] == s)  # noqa: E731
        return {"incidents": len(self.store.q("SELECT 1 FROM incidents")), "actions_total": len(acts),
                "auto_executed": sum(1 for a in acts if a["status"] == "executed" and a["decided_by"] == "policy"),
                "human_approved": sum(1 for a in acts if a["status"] == "executed" and a["decided_by"] != "policy"),
                "rejected": by("rejected"), "awaiting_approval": by("pending_approval"),
                "denied_by_least_privilege": by("denied_not_permitted")}
