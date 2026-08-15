"""Recompute workload/fairness stats from a solution.

Deliberately independent of the solver's internal state, so tests can verify
fairness claims without trusting the model that produced them.
"""

from __future__ import annotations

from .models import Assignment, EmployeeStats, SolveRequest


def compute_stats(req: SolveRequest, assignments: list[Assignment]) -> list[EmployeeStats]:
    shifts = {s.id: s for s in req.shifts}
    employees = {e.id: e for e in req.employees}
    prefs: set[tuple[str, str]] = {(p.employee_id, p.shift_id) for p in req.preferences}

    stats: dict[str, EmployeeStats] = {
        e.id: EmployeeStats(
            employee_id=e.id, name=e.name,
            total_hours=0.0, shift_count=0, weekend_shifts=0, night_shifts=0,
            preferred_shifts=0,
        )
        for e in req.employees
    }

    for a in assignments:
        s = shifts[a.shift_id]
        st = stats[a.employee_id]
        st.total_hours += s.duration_min / 60.0
        st.shift_count += 1
        if req.is_weekend(a.day):
            st.weekend_shifts += 1
        if s.is_night:
            st.night_shifts += 1
        if (a.employee_id, a.shift_id) in prefs:
            st.preferred_shifts += 1

    return [stats[e.id] for e in employees.values()]


def summarize_fairness(stats: list[EmployeeStats]) -> dict:
    """Convenience aggregates for display: min/max/spread of key metrics."""
    if not stats:
        return {}
    hours = [s.total_hours for s in stats]
    weekend = [s.weekend_shifts for s in stats]
    night = [s.night_shifts for s in stats]
    return {
        "hours_min": min(hours),
        "hours_max": max(hours),
        "hours_spread": round(max(hours) - min(hours), 2),
        "weekend_min": min(weekend),
        "weekend_max": max(weekend),
        "weekend_spread": max(weekend) - min(weekend),
        "night_min": min(night),
        "night_max": max(night),
        "night_spread": max(night) - min(night),
    }
