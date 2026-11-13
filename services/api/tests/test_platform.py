import json

import pytest
from conftest import ANN, LEAD, NOTE, ONCALL, PAYER, PROVIDER

from cdap import clinical, evidence, products
from cdap.access import Forbidden
from cdap.llm import Gateway, GovernanceError
from cdap.ops import Ops


def codes(store, eid, sources):
    return {c["code"] for c in products.patient_record(store, eid, sources, True)["conditions"]}


# ---------------------------------------------------------------- access control

def test_provider_sees_restricted_program_only_with_consent(store, policy):
    assert "F10.20" in codes(store, "eid-ann", policy.authorize_patient(PROVIDER, "eid-ann", "read"))
    bea = policy.authorize_patient(PROVIDER, "eid-bea", "read")
    assert "bh-clinic" not in bea and codes(store, "eid-bea", bea) == set()


def test_payer_never_sees_restricted_program_even_with_consent(store, policy):
    sources = policy.authorize_patient(PAYER, "eid-ann", "read")
    assert "bh-clinic" not in sources
    assert codes(store, "eid-ann", sources) == {"E11.9"}


def test_payer_has_no_relationship_with_non_members(policy):
    with pytest.raises(Forbidden):
        policy.authorize_patient(PAYER, "eid-bea", "read")


def test_patient_sees_all_of_own_record_and_nobody_elses(store, policy):
    assert policy.own_eid(ANN) == "eid-ann"
    assert "F10.20" in codes(store, "eid-ann", policy.authorize_patient(ANN, "eid-ann", "read"))
    with pytest.raises(Forbidden):
        policy.authorize_patient(ANN, "eid-bea", "read")


def test_operators_cannot_read_patients_and_denials_are_audited(store, policy):
    with pytest.raises(Forbidden):
        policy.authorize_patient(ONCALL, "eid-ann", "read")
    last = store.q("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1")[0]
    assert (last["actor"], last["outcome"], last["resource"]) == ("oncall", "deny", "Patient/eid-ann")


def test_payer_gets_no_note_text(store, policy):
    sources = policy.authorize_patient(PAYER, "eid-ann", "read")
    docs = products.patient_record(store, "eid-ann", sources, include_note_text=False)["documents"]
    assert docs and all("text" not in d for d in docs)


# ------------------------------------------------------------------------- audit

def test_audit_chain_detects_tampering(store):
    for i in range(5):
        store.audit("a", "user", "read", f"Patient/{i}", "allow")
    assert store.verify_audit_chain()["valid"]
    store.x("UPDATE audit_log SET outcome='deny' WHERE seq=3")
    assert store.verify_audit_chain() == {"valid": False, "entries": 2, "broken_at_seq": 3}


def test_audit_chain_detects_deletion(store):
    for i in range(4):
        store.audit("a", "user", "read", f"Patient/{i}", "allow")
    store.x("DELETE FROM audit_log WHERE seq=2")
    assert not store.verify_audit_chain()["valid"]


# --------------------------------------------------------------------------- LLM

class Hallucinating:
    name, model = "offline", "deterministic-rules-v1"

    def complete(self, task, system, user):
        return json.dumps({"conditions": [
            {"code": "E11.9", "name": "diabetes", "evidence": "Type 2 diabetes mellitus"},
            {"code": "C50.9", "name": "breast cancer", "evidence": "history of breast cancer"}]})


def test_ungrounded_facts_are_dropped(store):
    raw, _ = Gateway(store, Hallucinating()).run("extract_clinical_facts", {"note": NOTE})
    facts, dropped = clinical.ground(raw, NOTE)
    assert [c["code"] for c in facts["conditions"]] == ["E11.9"] and dropped == 1


def test_unlisted_model_is_blocked(store):
    class Rogue(Hallucinating):
        model = "some-unreviewed-model"
    with pytest.raises(GovernanceError):
        Gateway(store, Rogue()).run("extract_clinical_facts", {"note": NOTE})


def test_listed_but_unapproved_model_is_blocked_in_production(store):
    class Pending(Hallucinating):
        name, model = "vertex-claude", "claude-opus-5-5"
    Gateway(store, Pending(), require_approval=False).run("extract_clinical_facts", {"note": NOTE})
    with pytest.raises(GovernanceError):
        Gateway(store, Pending(), require_approval=True).run("extract_clinical_facts", {"note": NOTE})


def test_model_calls_record_prompt_version_without_content(store, llm):
    llm.run("extract_clinical_facts", {"note": NOTE})
    row = dict(store.q("SELECT * FROM llm_calls")[0])
    assert row["prompt_id"] == "extract_clinical_facts" and len(row["prompt_sha"]) == 64
    assert "HbA1c" not in json.dumps(row)


def test_answers_cite_only_supplied_passages(store, llm, policy):
    record = products.patient_record(store, "eid-ann", policy.visible_sources(PROVIDER, "eid-ann"), True)
    out = clinical.answer_question(llm, record, "What was the HbA1c result?", "a clinician")
    assert out["citations"] and "9.8%" in out["answer"]
    assert clinical.answer_question(llm, record, "zzz qqq", "a clinician")["citations"] == []


# ------------------------------------------------------------------------- rules

def test_note_only_a1c_becomes_an_insight(store, llm):
    clinical.extract_documents(store, llm)
    fired = clinical.run_rules(store, {"ehr-northside", "payer-claims", "document-feed"})
    assert fired["insight-a1c-in-notes-only"] == 1 and "gap-a1c-no-result" not in fired
    gap = next(g for g in products.care_gaps(store, "eid-ann") if g["rule_id"] == "insight-a1c-in-notes-only")
    assert "doc.A1" in gap["evidence"] and gap["rule_version"] == 1


def test_rules_ignore_sources_without_operations_rights(store, llm):
    facts, _ = clinical.build_facts(store, "eid-ann", {"ehr-northside"}, 2026)
    assert facts["notes"] == {}


def test_rule_operators():
    rule = {"when": {"all": [{"fact": "a.b", "op": "gte", "value": 3}], "any": [
        {"fact": "x", "op": "missing"}, {"fact": "y", "op": "eq", "value": 1}]}}
    assert clinical.matches(rule, {"a": {"b": 3}})
    assert not clinical.matches(rule, {"a": {"b": 2}})
    assert not clinical.matches(rule, {"a": {"b": 5}, "x": 0, "y": 2})
    assert not clinical.matches({"when": {"all": [{"fact": "a.b", "op": "gt", "value": 1}]}}, {})


# --------------------------------------------------------------------- operations

def triaged(store, llm, alert_type):
    ops = Ops(store, llm)
    inc = ops.open_incident({"type": alert_type, "resource": "x", "severity": "high"}, ONCALL.id)
    return ops, ops.triage(inc["id"], ONCALL)


def test_agent_reads_run_and_changes_wait_for_approval(store, llm):
    ops, inc = triaged(store, llm, "pubsub_backlog_growing")
    status = {a["tool"]: a["status"] for a in inc["actions"]}
    assert status == {"get_pubsub_backlog": "executed", "get_dataflow_job_status": "executed",
                      "get_recent_deploys": "executed", "scale_dataflow_workers": "pending_approval"}
    assert ops.state()["dataflow"]["harmonizer"]["workers"] == 4  # nothing changed yet


def test_requester_cannot_approve_and_non_approver_cannot_approve(store, llm):
    ops, inc = triaged(store, llm, "pubsub_backlog_growing")
    action = next(a for a in inc["actions"] if a["status"] == "pending_approval")
    with pytest.raises(Forbidden, match="approval rights"):
        ops.decide(action["id"], ONCALL, True)
    lead_requested = ops.triage(ops.open_incident({"type": "dataflow_error_rate", "resource": "x"}, LEAD.id)["id"], LEAD)
    own = next(a for a in lead_requested["actions"] if a["status"] == "pending_approval")
    with pytest.raises(Forbidden, match="separation of duties"):
        ops.decide(own["id"], LEAD, True)


def test_approval_executes_and_closes_the_incident(store, llm):
    ops, inc = triaged(store, llm, "pubsub_backlog_growing")
    action = next(a for a in inc["actions"] if a["status"] == "pending_approval")
    after = ops.decide(action["id"], LEAD, True)
    assert after["status"] == "mitigated" and ops.state()["dataflow"]["harmonizer"]["workers"] == 12
    with pytest.raises(Forbidden):
        ops.decide(action["id"], LEAD, True)  # cannot be decided twice


def test_agent_is_refused_tools_outside_its_allowlist(store, llm):
    ops, inc = triaged(store, llm, "bq_spend_anomaly")
    purge = next(a for a in inc["actions"] if a["tool"] == "purge_dataset")
    assert purge["status"] == "denied_not_permitted" and ops.metrics()["denied_by_least_privilege"] == 1


def test_unknown_alert_goes_to_a_human(store, llm):
    _, inc = triaged(store, llm, "something_new")
    assert inc["status"] == "needs_human" and inc["actions"] == []


def test_evidence_bundle_passes_and_flags_a_broken_chain(store, llm):
    ops, inc = triaged(store, llm, "pubsub_backlog_growing")
    ops.decide(next(a for a in inc["actions"] if a["status"] == "pending_approval")["id"], LEAD, True)
    assert evidence.bundle(store, llm)["summary"] == {"pass": 11}
    store.x("UPDATE audit_log SET actor='someone-else' WHERE seq=1")
    failed = [c["id"] for c in evidence.bundle(store, llm)["controls"] if c["status"] == "fail"]
    assert failed == ["AUD-01"]
