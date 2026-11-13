"""Who may see what, and why.

Access is attribute-based and decided per patient and per source system:

* relationship: a patient sees their own record; a provider sees patients their organization
  treats; a payer sees its own members. Operators and auditors never see patient data.
* purpose of use: each role acts under one purpose (patient access, treatment, operations). A
  source system's records are visible only if its data rights include that purpose.
* consent: sources flagged ``requires_consent`` (substance-use programs) are visible to anyone
  other than the patient only when the patient has an active consent on file.

Withheld sources are never listed to anyone but the patient. Telling a provider "there are
records here you cannot see" would itself disclose that the patient attends such a program.

Every decision, allow or deny, is written to the audit log.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .store import ROOT, Store

PURPOSE = {"patient": "patient_access", "provider": "treatment", "payer": "operations"}


class Forbidden(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    role: str
    tenant: str | None = None
    identifier: dict | None = None
    can_approve: bool = False

    @property
    def purpose(self) -> str | None:
        return PURPOSE.get(self.role)


@dataclass
class Policy:
    store: Store
    sources: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sources:
            doc = json.loads((ROOT / "config" / "sources.json").read_text())
            self.sources = {s["id"]: s for s in doc["sources"]}

    # ------------------------------------------------------- relationships
    def own_eid(self, p: Principal) -> str | None:
        if p.role != "patient" or not p.identifier:
            return None
        for r in self.store.q("SELECT eid, body FROM resources WHERE resource_type='Patient'"):
            for ident in json.loads(r["body"]).get("identifier", []):
                if ident.get("system") == p.identifier["system"] and ident.get("value") == p.identifier["value"]:
                    return r["eid"]
        return None

    def panel(self, p: Principal) -> list[str]:
        """The patients this principal has a relationship with."""
        if p.role == "patient":
            eid = self.own_eid(p)
            return [eid] if eid else []
        if p.role in ("provider", "payer") and p.tenant:
            return [r["eid"] for r in self.store.q(
                "SELECT DISTINCT eid FROM resources WHERE tenant=? ORDER BY eid", (p.tenant,))]
        return []

    # ------------------------------------------------------------ decisions
    def visible_sources(self, p: Principal, eid: str) -> set[str]:
        purpose = p.purpose
        if purpose is None:
            return set()
        out = set()
        for sid, src in self.sources.items():
            if purpose not in src["permitted_purposes"]:
                continue
            if src.get("requires_consent") and p.role != "patient" and not self._has_consent(eid, sid):
                continue
            out.add(sid)
        return out

    def _has_consent(self, eid: str, source: str) -> bool:
        for r in self.store.q(
                "SELECT body FROM resources WHERE eid=? AND source_system=? AND resource_type='Consent'", (eid, source)):
            c = json.loads(r["body"])
            purposes = {x.get("code") for x in c.get("provision", {}).get("purpose", [])}
            if c.get("status") == "active" and c.get("provision", {}).get("type") == "permit" and "TREAT" in purposes:
                return True
        return False

    def authorize_patient(self, p: Principal, eid: str, action: str) -> set[str]:
        """Return the sources the principal may read for this patient, or raise Forbidden."""
        allowed = p.purpose is not None and eid in self.panel(p)
        sources = self.visible_sources(p, eid) if allowed else set()
        self.store.audit(p.id, "user", action, f"Patient/{eid}", "allow" if allowed else "deny", p.purpose,
                         {"role": p.role, "sources": sorted(sources)})
        if not allowed:
            raise Forbidden("no relationship with this patient for your role")
        return sources

    def require_role(self, p: Principal, roles: tuple[str, ...], action: str, resource: str) -> None:
        ok = p.role in roles
        self.store.audit(p.id, "user", action, resource, "allow" if ok else "deny", None, {"role": p.role})
        if not ok:
            raise Forbidden(f"requires role: {', '.join(roles)}")


def load_principals() -> dict[str, Principal]:
    doc = json.loads((ROOT / "config" / "principals.json").read_text())
    return {d["id"]: Principal(**{k: v for k, v in d.items()}) for d in doc["principals"]}
