import os

os.environ["CDAP_DB"] = ":memory:"

from fastapi.testclient import TestClient  # noqa: E402

from cdap import api  # noqa: E402

client = TestClient(api.app)


def get(path, user=None):
    return client.get(path, headers={"X-Demo-User": user} if user else {})


def test_requires_a_known_user():
    assert get("/api/me").status_code == 401
    assert get("/api/me", "nobody").status_code == 401
    assert get("/api/me", "dr-okafor").json()["purpose_of_use"] == "treatment"


def test_role_boundaries():
    assert get("/api/patients", "ops-oncall").status_code == 403
    assert get("/api/patients", "patient-james").status_code == 403
    assert get("/api/population/cost", "dr-okafor").status_code == 403
    assert get("/api/compliance/evidence", "ops-oncall").status_code == 403
    assert get("/api/ops/incidents", "payer-analyst").status_code == 403
    assert get("/api/patients/eid-unknown", "dr-okafor").status_code == 403
    assert get("/api/audit", "auditor").json()["chain"]["valid"]
