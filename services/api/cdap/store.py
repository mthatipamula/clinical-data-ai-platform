"""Local store: SQLite standing in for BigQuery (data products) and Spanner (operational state).

The schema mirrors the BigQuery tables in infra/terraform/bigquery.tf so queries port directly.
The audit log is append-only and hash-chained: each entry commits to the one before it, so any
edit or deletion after the fact is detectable.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(os.environ.get("CDAP_ROOT", Path(__file__).resolve().parents[3]))

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
  resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, eid TEXT NOT NULL,
  source_system TEXT NOT NULL, tenant TEXT NOT NULL, ingest_id TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL, pipeline_version TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (resource_type, resource_id));
CREATE INDEX IF NOT EXISTS resources_eid ON resources (eid, resource_type);
CREATE INDEX IF NOT EXISTS resources_tenant ON resources (tenant);
CREATE TABLE IF NOT EXISTS measure_results (
  eid TEXT NOT NULL, measure_id TEXT NOT NULL, measure_version TEXT NOT NULL,
  period_start TEXT, period_end TEXT, numerator INTEGER NOT NULL, inverse INTEGER NOT NULL,
  detail TEXT, evidence TEXT NOT NULL, PRIMARY KEY (eid, measure_id));
CREATE TABLE IF NOT EXISTS extractions (
  document_id TEXT PRIMARY KEY, eid TEXT NOT NULL, source_system TEXT NOT NULL, facts TEXT NOT NULL,
  dropped_ungrounded INTEGER NOT NULL, prompt_id TEXT, prompt_version INTEGER, prompt_sha TEXT,
  provider TEXT, model TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS care_gaps (
  eid TEXT NOT NULL, rule_id TEXT NOT NULL, rule_version INTEGER NOT NULL, title TEXT, severity TEXT,
  recommendation TEXT, patient_text TEXT, evidence TEXT NOT NULL, PRIMARY KEY (eid, rule_id));
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, task TEXT, prompt_id TEXT, prompt_version INTEGER,
  prompt_sha TEXT, provider TEXT, model TEXT, input_sha TEXT, output_sha TEXT, latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS incidents (
  id TEXT PRIMARY KEY, created_at TEXT, alert TEXT NOT NULL, status TEXT NOT NULL, triage TEXT);
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, agent_id TEXT NOT NULL, tool TEXT NOT NULL,
  args TEXT NOT NULL, why TEXT, risk TEXT NOT NULL, status TEXT NOT NULL, requested_by TEXT NOT NULL,
  decided_by TEXT, decided_at TEXT, result TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS platform_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, actor_type TEXT NOT NULL,
  action TEXT NOT NULL, resource TEXT NOT NULL, purpose TEXT, outcome TEXT NOT NULL,
  detail TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""

GENESIS = "0" * 64


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Store:
    def __init__(self, path: str | os.PathLike | None = None):
        path = str(path or os.environ.get("CDAP_DB", ROOT / "var" / "cdap.db"))
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.executescript(SCHEMA)

    # ------------------------------------------------------------ plumbing
    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchall()

    def x(self, sql: str, args: Iterable[Any] = ()) -> None:
        with self._lock:
            self._db.execute(sql, tuple(args))
            self._db.commit()

    # ------------------------------------------------------------- loading
    def load_harmonized(self, directory: str | os.PathLike) -> dict[str, int]:
        """Load harmonizer output. Re-loading is safe: rows are replaced by key."""
        d = Path(directory)
        counts = {"resources": 0, "measures": 0}
        with self._lock:
            for f in sorted(d.glob("resources-*.ndjson")):
                for line in f.read_text().splitlines():
                    w = json.loads(line)
                    self._db.execute(
                        "INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?,?,?)",
                        (w["resource_type"], w["resource_id"], w["eid"], w["source_system"], w["tenant"],
                         w["ingest_id"], w["raw_sha256"], w["pipeline_version"], json.dumps(w["resource"])))
                    counts["resources"] += 1
            self._db.execute("DELETE FROM measure_results")
            for f in sorted(d.glob("measures-*.ndjson")):
                for line in f.read_text().splitlines():
                    m = json.loads(line)
                    self._db.execute(
                        "INSERT OR REPLACE INTO measure_results VALUES (?,?,?,?,?,?,?,?,?)",
                        (m["eid"], m["measure_id"], m["measure_version"], m["period_start"], m["period_end"],
                         int(m["numerator"]), int(m["inverse"]), m["detail"], json.dumps(m["evidence"])))
                    counts["measures"] += 1
            self._db.commit()
        return counts

    def resources(self, eid: str, rtype: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM resources WHERE eid=?", [eid]
        if rtype:
            sql += " AND resource_type=?"
            args.append(rtype)
        return [dict(r) | {"body": json.loads(r["body"])} for r in self.q(sql + " ORDER BY resource_id", args)]

    # --------------------------------------------------------------- audit
    def audit(self, actor: str, actor_type: str, action: str, resource: str, outcome: str,
              purpose: str | None = None, detail: dict | None = None) -> None:
        """Append one entry. Detail must never contain PHI: identifiers and reasons only."""
        with self._lock:
            last = self._db.execute("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (last["seq"] + 1, last["hash"]) if last else (1, GENESIS)
            entry = {"seq": seq, "ts": now(), "actor": actor, "actor_type": actor_type, "action": action,
                     "resource": resource, "purpose": purpose, "outcome": outcome,
                     "detail": json.dumps(detail or {}, sort_keys=True)}
            digest = sha(prev + json.dumps(entry, sort_keys=True))
            self._db.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (*entry.values(), prev, digest))
            self._db.commit()

    def verify_audit_chain(self) -> dict:
        """Recompute every hash. Returns the first sequence number where the chain breaks, if any."""
        prev, n = GENESIS, 0
        for r in self.q("SELECT * FROM audit_log ORDER BY seq"):
            entry = {k: r[k] for k in ("seq", "ts", "actor", "actor_type", "action", "resource",
                                       "purpose", "outcome", "detail")}
            if r["prev_hash"] != prev or r["hash"] != sha(prev + json.dumps(entry, sort_keys=True)):
                return {"valid": False, "entries": n, "broken_at_seq": r["seq"]}
            prev, n = r["hash"], n + 1
        return {"valid": True, "entries": n, "head": prev}
