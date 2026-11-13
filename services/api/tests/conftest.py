import base64
import json

import pytest

from cdap.access import Policy, Principal
from cdap.llm import Gateway, OfflineProvider
from cdap.store import Store

NOTE = ("PROGRESS NOTE\nPatient reports smoking 1 pack per day for the past 10 years.\n"
        "1. Type 2 diabetes mellitus. Outside lab result reviewed: HbA1c 9.8% on 2026-05-02. Poorly controlled.\n"
        "   Increase metformin to 1000 mg twice daily.")


def wrapper(eid, source, tenant, rtype, rid, body):
    return {"eid": eid, "source_system": source, "tenant": tenant, "ingest_id": "ing_" + rid, "raw_sha256": "ab" * 32,
            "resource_type": rtype, "resource_id": rid, "pipeline_version": "test", "resource": body}


def patient(mrn, system, first, last):
    return {"resourceType": "Patient", "identifier": [{"system": system, "value": mrn}],
            "name": [{"family": last, "given": [first]}], "gender": "female", "birthDate": "1960-03-04"}


def consent():
    return {"resourceType": "Consent", "status": "active",
            "provision": {"type": "permit", "purpose": [{"code": "TREAT"}]}}


@pytest.fixture
def store(tmp_path):
    s = Store(":memory:")
    icd = "http://hl7.org/fhir/sid/icd-10-cm"
    rows = [
        # Ann: EHR + claims + a note + behavioral health WITH consent
        wrapper("eid-ann", "ehr-northside", "northside-health", "Patient", "ehr.A1", patient("A1", "urn:cdap:mrn:northside", "Ann", "Lee")),
        wrapper("eid-ann", "ehr-northside", "northside-health", "Condition", "ehr.A1-c", {"code": {"coding": [{"system": icd, "code": "E11.9", "display": "Type 2 diabetes"}]}}),
        wrapper("eid-ann", "payer-claims", "summit-health-plan", "Patient", "payer.M1", patient("M1", "urn:cdap:member:summit", "ANN", "LEE")),
        wrapper("eid-ann", "document-feed", "northside-health", "DocumentReference", "doc.A1",
                {"date": "2026-06-01T00:00:00Z", "type": {"coding": [{"display": "Progress note"}]},
                 "content": [{"attachment": {"data": base64.b64encode(NOTE.encode()).decode()}}]}),
        wrapper("eid-ann", "bh-clinic", "riverbend-behavioral", "Condition", "bh.A1-c", {"code": {"coding": [{"system": icd, "code": "F10.20", "display": "Alcohol dependence"}]}}),
        wrapper("eid-ann", "bh-clinic", "riverbend-behavioral", "Consent", "bh.A1-consent", consent()),
        # Bea: EHR + behavioral health WITHOUT consent
        wrapper("eid-bea", "ehr-northside", "northside-health", "Patient", "ehr.B1", patient("B1", "urn:cdap:mrn:northside", "Bea", "Ortiz")),
        wrapper("eid-bea", "bh-clinic", "riverbend-behavioral", "Condition", "bh.B1-c", {"code": {"coding": [{"system": icd, "code": "F11.20", "display": "Opioid dependence"}]}}),
    ]
    d = tmp_path / "h"
    d.mkdir()
    (d / "resources-0.ndjson").write_text("\n".join(json.dumps(r) for r in rows))
    (d / "measures-0.ndjson").write_text(json.dumps({
        "eid": "eid-ann", "measure_id": "DM-A1C-POOR-CONTROL", "measure_version": "t", "period_start": "2026-01-01",
        "period_end": "2026-12-31", "numerator": True, "inverse": True, "detail": "no HbA1c result in 2026", "evidence": ["ehr.A1-c"]}))
    s.load_harmonized(d)
    return s


@pytest.fixture
def policy(store):
    return Policy(store)


@pytest.fixture
def llm(store):
    return Gateway(store, OfflineProvider())


PROVIDER = Principal("dr", "Dr", "provider", tenant="northside-health")
PAYER = Principal("payer", "Payer", "payer", tenant="summit-health-plan")
ANN = Principal("ann", "Ann", "patient", identifier={"system": "urn:cdap:mrn:northside", "value": "A1"})
ONCALL = Principal("oncall", "On-call", "operator")
LEAD = Principal("lead", "Lead", "operator", can_approve=True)
