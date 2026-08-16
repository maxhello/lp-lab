"""Solver correctness: verify solutions by independent recomputation."""

from app.scheduling.models import Assignment, ShiftType, SolveRequest
from app.scheduling.solver import solve
from tests.conftest import _timeoff_and_pref_request


def _recompute_ok(req: SolveRequest, assignments: list[Assignment]) -> list[str]:
    """Re-verify every hard constraint from raw assignments. Returns violation messages."""
    errors = []
    shifts = {s.id: s for s in req.shifts}
    by_slot: dict[tuple[int, str], list[str]] = {}
    per_employee_day: dict[tuple[str, int], list[str]] = {}

    for a in assignments:
        by_slot.setdefault((a.day, a.shift_id), []).append(a.employee_id)
        per_employee_day.setdefault((a.employee_id, a.day), []).append(a.shift_id)

    # Coverage min/max + skills
    for c in req.coverage:
        assigned = by_slot.get((c.day, c.shift_id), [])
        if len(assigned) < c.min_staff:
            errors.append(f"understaffed day {c.day} {c.shift_id}: {len(assigned)} < {c.min_staff}")
        if c.max_staff is not None and len(assigned) > c.max_staff:
            errors.append(f"overstaffed day {c.day} {c.shift_id}")
        emp_map = {e.id: e for e in req.employees}
        for skill in c.required_skills:
            if not any(skill in emp_map[eid].skills for eid in assigned):
                errors.append(f"missing skill '{skill}' day {c.day} {c.shift_id}")

    # One shift per day
    if req.constraints.one_shift_per_day:
        for (eid, d), sids in per_employee_day.items():
            if len(sids) > 1:
                errors.append(f"{eid} has {len(sids)} shifts on day {d}")

    # Hard time off
    for t in req.time_off:
        if t.hard and per_employee_day.get((t.employee_id, t.day)):
            errors.append(f"{t.employee_id} scheduled on hard time-off day {t.day}")

    # Max consecutive days
    for e in req.employees:
        work_days = sorted(d for (eid, d) in per_employee_day if eid == e.id)
        run = 0
        prev = None
        for d in work_days:
            run = run + 1 if prev == d - 1 else 1
            if run > req.constraints.max_consecutive_days:
                errors.append(f"{e.id} works {run}+ consecutive days ending day {d}")
            prev = d

    # Min rest between shifts on any two days (mirrors solver's (gap, s1, s2) rule:
    # long shifts can leave too little rest even two days apart)
    rest_min = req.constraints.min_rest_hours * 60
    for e in req.employees:
        for d1 in range(req.num_days):
            for d2 in range(d1 + 1, req.num_days):
                for s1 in per_employee_day.get((e.id, d1), []):
                    for s2 in per_employee_day.get((e.id, d2), []):
                        sh1, sh2 = shifts[s1], shifts[s2]
                        rest = ((d2 - d1) * 1440 + sh2.start_min
                                - sh1.start_min - sh1.duration_min)
                        if rest < rest_min - 1e-9:
                            errors.append(
                                f"{e.id} rest violation {s1}(d{d1})->{s2}(d{d2})")

    # Weekly hour caps
    for e in req.employees:
        cap = e.max_hours_per_week or req.constraints.max_hours_per_week
        for w in range(0, req.num_days, 7):
            mins = sum(shifts[sid].duration_min
                       for (eid, d), sids in per_employee_day.items() if eid == e.id
                       for sid in sids if w <= d < w + 7)
            if mins > cap * 60 + 1e-6:
                errors.append(f"{e.id} exceeds weekly cap in week {w}: {mins/60:.1f}h > {cap}h")

    return errors


def test_feasible_scenario_satisfies_all_hard_constraints(small_request):
    resp = solve(small_request)
    assert resp.status in ("OPTIMAL", "FEASIBLE"), resp.message
    assert resp.assignments
    assert _recompute_ok(small_request, resp.assignments) == []


def test_understaffed_returns_best_effort(infeasible_request):
    """min_staff=10 with 5 employees: soft coverage returns a usable schedule
    plus explicit shortfalls instead of a bare INFEASIBLE."""
    resp = solve(infeasible_request)
    assert resp.status in ("OPTIMAL", "FEASIBLE"), resp.message
    assert resp.assignments
    assert resp.shortfalls
    assert sum(s.missing_staff for s in resp.shortfalls) > 0
    assert "Best-effort" in resp.message
    # Coverage is the only violated rule — every other hard constraint still holds.
    errors = _recompute_ok(infeasible_request, resp.assignments)
    assert errors  # capacity really is short
    assert all(e.startswith("understaffed") for e in errors)


def test_rest_constraint_spans_two_day_gap():
    """24h shift 23:00→23:00 ends 23:00 next day; an 08:00 shift two days later
    leaves only 1h rest. Adjacent-day checking misses this; the gap rule must not."""
    from app.scheduling.models import ConstraintsConfig, CoverageRequirement, Employee
    employees = [Employee(id=f"e{i}", name=f"E{i}") for i in range(1, 4)]
    shifts = [
        ShiftType(id="long", start="23:00", end="23:00"),
        ShiftType(id="early", start="08:00", end="16:00"),
    ]
    coverage = [
        CoverageRequirement(day=0, shift_id="long", min_staff=2, max_staff=2),
        CoverageRequirement(day=2, shift_id="early", min_staff=1, max_staff=1),
    ]
    req = SolveRequest(
        num_days=3, employees=employees, shifts=shifts, coverage=coverage,
        constraints=ConstraintsConfig(min_rest_hours=11.0, max_hours_per_week=60),
    )
    resp = solve(req)
    assert resp.status in ("OPTIMAL", "FEASIBLE")
    assert resp.shortfalls == []
    long_d0 = {a.employee_id for a in resp.assignments if a.day == 0 and a.shift_id == "long"}
    early_d2 = {a.employee_id for a in resp.assignments if a.day == 2 and a.shift_id == "early"}
    assert long_d0 and early_d2
    assert not (long_d0 & early_d2)  # the two long-shift workers can't take day-2 early
    assert _recompute_ok(req, resp.assignments) == []


def test_fixed_assignments_are_pinned(small_request):
    data = small_request.model_dump()
    data["fixed_assignments"] = [
        {"employee_id": "e3", "day": 2, "shift_id": "night"},
        {"employee_id": "e1", "day": 4, "shift_id": "late"},
    ]
    req = SolveRequest(**data)
    resp = solve(req)
    assert resp.status in ("OPTIMAL", "FEASIBLE"), resp.message
    got = {(a.employee_id, a.day, a.shift_id) for a in resp.assignments}
    assert ("e3", 2, "night") in got
    assert ("e1", 4, "late") in got
    assert _recompute_ok(req, resp.assignments) == []


def test_fixed_conflicting_with_hard_timeoff_is_infeasible(small_request):
    data = small_request.model_dump()
    data["time_off"] = [{"employee_id": "e1", "day": 3, "hard": True}]
    data["fixed_assignments"] = [{"employee_id": "e1", "day": 3, "shift_id": "early"}]
    resp = solve(SolveRequest(**data))
    assert resp.status == "INFEASIBLE"
    assert "hard time off" in resp.message


def test_stale_hint_entries_are_ignored(small_request):
    data = small_request.model_dump()
    data["hint_assignments"] = [
        {"employee_id": "e1", "day": 1, "shift_id": "early"},
        {"employee_id": "ghost", "day": 99, "shift_id": "nope"},
        {"employee_id": "e2", "day": 2, "shift_id": "late"},
    ]
    req = SolveRequest(**data)
    resp = solve(req)
    assert resp.status in ("OPTIMAL", "FEASIBLE")
    assert _recompute_ok(req, resp.assignments) == []


def test_hard_timeoff_respected(small_request):
    resp = solve(_timeoff_and_pref_request(small_request))
    assert resp.status in ("OPTIMAL", "FEASIBLE")
    assert _recompute_ok(_timeoff_and_pref_request(small_request), resp.assignments) == []


def test_missing_skill_is_infeasible(small_request):
    data = small_request.model_dump()
    data["coverage"][0]["required_skills"] = ["brain_surgery"]
    req = SolveRequest(**data)
    resp = solve(req)
    assert resp.status == "INFEASIBLE"
    assert "brain_surgery" in resp.message


def test_rest_constraint_prevents_late_then_early():
    """Late ends 00:00, early next day starts 08:00 — only 8h rest; require 10h."""
    from app.scheduling.models import ConstraintsConfig, CoverageRequirement, Employee
    employees = [Employee(id=f"e{i}", name=f"E{i}") for i in range(1, 4)]
    shifts = [
        ShiftType(id="late", start="16:00", end="00:00"),
        ShiftType(id="early", start="08:00", end="16:00"),
    ]
    coverage = [CoverageRequirement(day=d, shift_id=sid, min_staff=1, max_staff=2)
                for d in range(2) for sid in ("late", "early")]
    req = SolveRequest(
        num_days=2, employees=employees, shifts=shifts, coverage=coverage,
        constraints=ConstraintsConfig(min_rest_hours=10.0, max_hours_per_week=60),
    )
    resp = solve(req)
    assert resp.status in ("OPTIMAL", "FEASIBLE")
    assert _recompute_ok(req, resp.assignments) == []
    # Nobody who works late on day 0 works early on day 1.
    late_d0 = {a.employee_id for a in resp.assignments if a.day == 0 and a.shift_id == "late"}
    early_d1 = {a.employee_id for a in resp.assignments if a.day == 1 and a.shift_id == "early"}
    assert not (late_d0 & early_d1)


def test_stats_consistent_with_assignments(small_request):
    from app.scheduling.stats import compute_stats
    resp = solve(small_request)
    assert resp.stats
    total_hours = sum(s.total_hours for s in resp.stats)
    expected = sum(
        next(s for s in small_request.shifts if s.id == a.shift_id).duration_min / 60
        for a in resp.assignments
    )
    assert abs(total_hours - expected) < 1e-6
