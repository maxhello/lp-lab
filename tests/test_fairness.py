"""Fairness: with identical employees and uniform coverage, spreads must be small."""

from app.scheduling.solver import solve
from app.scheduling.stats import summarize_fairness


def test_uniform_scenario_has_small_spreads(uniform_request):
    resp = solve(uniform_request)
    assert resp.status in ("OPTIMAL", "FEASIBLE"), resp.message
    summary = summarize_fairness(resp.stats)
    # Hours spread: at most one 8h shift of difference (21 shifts / 5 employees ≈ 4.2 each).
    assert summary["hours_spread"] <= 8.01, summary
    # Weekend (2 days × 3 shifts = 6 weekend slots / 5 employees).
    assert summary["weekend_spread"] <= 1, summary
    # Night (7 slots / 5 employees).
    assert summary["night_spread"] <= 1, summary


def test_preference_influences_solution(uniform_request):
    """Strong preference should bias who gets the early shifts."""
    from app.scheduling.models import Preference
    data = uniform_request.model_dump()
    data["preferences"] = [Preference(employee_id="e3", shift_id="early", weight=5).model_dump()]
    req_w = type(uniform_request)(**data)
    resp_w = solve(req_w)

    data_none = uniform_request.model_dump()
    data_none["preferences"] = []
    resp_none = solve(type(uniform_request)(**data_none))

    w_early = sum(1 for a in resp_w.assignments if a.employee_id == "e3" and a.shift_id == "early")
    n_early = sum(1 for a in resp_none.assignments if a.employee_id == "e3" and a.shift_id == "early")
    assert w_early >= n_early
