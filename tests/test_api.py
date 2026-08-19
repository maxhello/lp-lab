from fastapi.testclient import TestClient

from app.scheduling.api import create_app


def client() -> TestClient:
    return TestClient(create_app())


def test_solve_endpoint_ok(small_request):
    with client() as c:
        r = c.post("/api/scheduling/solve", json=small_request.model_dump())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    assert body["assignments"]
    assert len(body["stats"]) == 5


def test_solve_endpoint_422_on_bad_reference(small_request):
    data = small_request.model_dump()
    data["coverage"][0]["shift_id"] = "nonexistent"
    with client() as c:
        r = c.post("/api/scheduling/solve", json=data)
    assert r.status_code == 422


def test_solve_endpoint_422_on_duplicate_employee(small_request):
    data = small_request.model_dump()
    data["employees"].append(dict(data["employees"][0]))
    with client() as c:
        r = c.post("/api/scheduling/solve", json=data)
    assert r.status_code == 422


def test_validate_flags_impossible_slot(infeasible_request):
    with client() as c:
        r = c.post("/api/scheduling/validate", json=infeasible_request.model_dump())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any("min_staff" in w for w in body["warnings"])


def test_validate_flags_fixed_min_rest_conflict(small_request):
    """Locked shifts breaking min rest: validate should warn up front, matching
    the solver's fast-fail INFEASIBLE check."""
    data = small_request.model_dump()
    data["constraints"]["min_rest_hours"] = 10.0
    data["fixed_assignments"] = [
        {"employee_id": "e1", "day": 0, "shift_id": "late"},   # ends 00:00
        {"employee_id": "e1", "day": 1, "shift_id": "early"},  # starts 08:00 → 8h rest
    ]
    with client() as c:
        r = c.post("/api/scheduling/validate", json=data)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any("休息" in w for w in body["warnings"])


def test_index_served():
    with client() as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "html" in r.headers["content-type"]
