"""CP-SAT shift scheduling model.

Decision variables: assign[e][d][s] — employee e works shift s on day d.
All durations are integer minutes. Day 0 is Monday.
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from .models import Assignment, SolveRequest, SolveResponse
from .stats import compute_stats

# Generous upper bound for spread variables (31 days of minutes).
_UB = 44640


def _forbidden_pairs(req: SolveRequest) -> set[tuple[str, str]]:
    """Shift pairs (s1, s2) violating min rest when s1 is on day d, s2 on day d+1.

    A shift occupies [d*1440 + start, d*1440 + start + duration]. The next day's
    shift starts at (d+1)*1440 + start2, so the rest in between is
    1440 + start2 - start1 - duration1 (minutes; duration already handles midnight).
    """
    rest_min = int(round(req.constraints.min_rest_hours * 60))
    bad: set[tuple[str, str]] = set()
    for s1 in req.shifts:
        for s2 in req.shifts:
            rest = 1440 + s2.start_min - s1.start_min - s1.duration_min
            if rest < rest_min:
                bad.add((s1.id, s2.id))
    return bad


def solve(req: SolveRequest) -> SolveResponse:
    started = time.perf_counter()
    model = cp_model.CpModel()

    employees = req.employees
    shifts = {s.id: s for s in req.shifts}
    num_days = req.num_days
    constraints = req.constraints
    weights = req.weights

    # Only (day, shift) slots that appear in coverage can be assigned.
    coverage = {(c.day, c.shift_id): c for c in req.coverage}

    # ------------------------------------------------------------------ vars
    assign: dict[tuple[str, int, str], cp_model.IntVar] = {}
    for e in employees:
        for d in range(num_days):
            for sid in shifts:
                if (d, sid) in coverage:
                    assign[(e.id, d, sid)] = model.new_bool_var(f"a_{e.id}_{d}_{sid}")

    def A(eid: str, d: int, sid: str) -> cp_model.IntVar:
        return assign[(eid, d, sid)]

    works: dict[tuple[str, int], cp_model.IntVar] = {}
    for e in employees:
        for d in range(num_days):
            day_vars = [A(e.id, d, sid) for sid in shifts if (e.id, d, sid) in assign]
            if day_vars:
                w = model.new_bool_var(f"w_{e.id}_{d}")
                model.add(sum(day_vars) == w)
                works[(e.id, d)] = w

    # ------------------------------------------------------- coverage + skills
    for (d, sid), c in coverage.items():
        slot_vars = [A(e.id, d, sid) for e in employees if (e.id, d, sid) in assign]
        model.add(sum(slot_vars) >= c.min_staff)
        if c.max_staff is not None:
            model.add(sum(slot_vars) <= c.max_staff)
        for skill in c.required_skills:
            qualified = [A(e.id, d, sid) for e in employees
                         if (e.id, d, sid) in assign and skill in e.skills]
            if not qualified:
                return SolveResponse(
                    status="INFEASIBLE",
                    message=(f"No employee has required skill '{skill}' "
                             f"for shift '{sid}' on day {d}."),
                )
            model.add(sum(qualified) >= 1)

    # ------------------------------------------------------------- one per day
    if constraints.one_shift_per_day:
        for e in employees:
            for d in range(num_days):
                day_vars = [A(e.id, d, sid) for sid in shifts if (e.id, d, sid) in assign]
                if len(day_vars) > 1:
                    model.add(sum(day_vars) <= 1)

    # -------------------------------------------------------- hard/soft timeoff
    soft_penalty_vars: list[cp_model.IntVar] = []
    for t in req.time_off:
        for sid in shifts:
            if (t.employee_id, t.day, sid) in assign:
                var = A(t.employee_id, t.day, sid)
                if t.hard:
                    model.add(var == 0)
                elif weights.soft_timeoff > 0:
                    soft_penalty_vars.append(var)

    # ------------------------------------------------------- consecutive days
    max_consec = constraints.max_consecutive_days
    for e in employees:
        for d in range(num_days - max_consec):
            window = [works[(e.id, dd)] for dd in range(d, d + max_consec + 1)
                      if (e.id, dd) in works]
            if len(window) == max_consec + 1:
                model.add(sum(window) <= max_consec)

    # ------------------------------------------------------------- min rest
    for s1, s2 in _forbidden_pairs(req):
        for e in employees:
            for d in range(num_days - 1):
                if (e.id, d, s1) in assign and (e.id, d + 1, s2) in assign:
                    model.add(A(e.id, d, s1) + A(e.id, d + 1, s2) <= 1)

    # ------------------------------------------------------- weekly caps
    for e in employees:
        hours_cap = (e.max_hours_per_week if e.max_hours_per_week is not None
                     else constraints.max_hours_per_week)
        minutes_cap = int(round(hours_cap * 60))
        shifts_cap = (e.max_shifts_per_week if e.max_shifts_per_week is not None
                      else constraints.max_shifts_per_week)
        for w_start in range(0, num_days, 7):
            minutes_terms = []
            shift_vars = []
            for d in range(w_start, min(w_start + 7, num_days)):
                for sid, s in shifts.items():
                    if (e.id, d, sid) in assign:
                        minutes_terms.append(A(e.id, d, sid) * s.duration_min)
                        shift_vars.append(A(e.id, d, sid))
            if minutes_terms:
                model.add(sum(minutes_terms) <= minutes_cap)
            if shifts_cap is not None and shift_vars:
                model.add(sum(shift_vars) <= shifts_cap)

    # ------------------------------------------------------------- objective
    obj_terms: list = []

    if soft_penalty_vars and weights.soft_timeoff > 0:
        obj_terms.append(weights.soft_timeoff * sum(soft_penalty_vars))

    if weights.preference > 0:
        pref_vars = [A(p.employee_id, d, p.shift_id)
                     for p in req.preferences
                     for d in range(num_days)
                     if (p.employee_id, d, p.shift_id) in assign]
        if pref_vars:
            obj_terms.append(-weights.preference * sum(pref_vars))

    def add_spread(exprs: list, weight: int) -> None:
        """Fairness term: penalise (max − min) of the given per-employee expressions."""
        if weight <= 0 or len(exprs) < 2:
            return
        max_v = model.new_int_var(0, _UB, "spread_max")
        min_v = model.new_int_var(0, _UB, "spread_min")
        model.add_max_equality(max_v, exprs)
        model.add_min_equality(min_v, exprs)
        obj_terms.append(weight * (max_v - min_v))

    hour_exprs = []
    weekend_exprs = []
    night_exprs = []
    for e in employees:
        terms = [A(e.id, d, sid) * shifts[sid].duration_min
                 for d in range(num_days) for sid in shifts if (e.id, d, sid) in assign]
        if terms:
            hour_exprs.append(sum(terms))
        w_terms = [A(e.id, d, sid) for d in range(num_days) if req.is_weekend(d)
                   for sid in shifts if (e.id, d, sid) in assign]
        if w_terms:
            weekend_exprs.append(sum(w_terms))
        n_terms = [A(e.id, d, sid) for d in range(num_days) for sid in shifts
                   if (e.id, d, sid) in assign and shifts[sid].is_night]
        if n_terms:
            night_exprs.append(sum(n_terms))
    add_spread(hour_exprs, weights.fairness_hours)
    add_spread(weekend_exprs, weights.fairness_weekend)
    add_spread(night_exprs, weights.fairness_night)

    if obj_terms:
        model.minimize(sum(obj_terms))

    # ---------------------------------------------------------------- solve
    cp = cp_model.CpSolver()
    cp.parameters.max_time_in_seconds = req.max_solve_seconds
    cp.parameters.num_workers = 8
    status = cp.solve(model)

    elapsed_ms = (time.perf_counter() - started) * 1000

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.INFEASIBLE:
            return SolveResponse(
                status="INFEASIBLE",
                message=("No schedule satisfies all hard constraints. Try lowering "
                         "min_staff, relaxing rest/hours limits, or adding staff."),
                solve_time_ms=elapsed_ms,
            )
        msg = (f"No solution found within {req.max_solve_seconds}s. "
               f"Try increasing max_solve_seconds.")
        return SolveResponse(status="UNKNOWN", message=msg, solve_time_ms=elapsed_ms)

    assignments = [Assignment(employee_id=eid, day=d, shift_id=sid)
                   for (eid, d, sid), var in assign.items() if cp.value(var)]
    assignments.sort(key=lambda a: (a.day, a.shift_id, a.employee_id))

    return SolveResponse(
        status="OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
        assignments=assignments,
        stats=compute_stats(req, assignments),
        objective_value=cp.objective_value,
        solve_time_ms=elapsed_ms,
    )
