"""CP-SAT shift scheduling model.

Decision variables: assign[e][d][s] — employee e works shift s on day d.
All durations are integer minutes. Day 0 is Monday.
"""

from __future__ import annotations

import time
from typing import Optional

from ortools.sat.python import cp_model

from .models import Assignment, SolveRequest, SolveResponse
from .stats import compute_shortfalls, compute_stats

# Generous upper bound for spread variables (31 days of minutes).
_UB = 44640


def _forbidden_pairs(req: SolveRequest) -> set[tuple[int, str, str]]:
    """Triples (gap, s1, s2) violating min rest when s1 is on day d, s2 on day d+gap.

    A shift occupies [d*1440 + start, d*1440 + start + duration]. A shift on day
    d+gap starts at (d+gap)*1440 + start2, so the rest in between is
    gap*1440 + start2 - start1 - duration1 (minutes; duration already handles midnight).
    gap=1 covers normal rosters; gap=2 matters when a long evening shift (≥ ~14h)
    leaves less than min rest before an early shift two days later.
    """
    rest_min = int(round(req.constraints.min_rest_hours * 60))
    bad: set[tuple[int, str, str]] = set()
    for s1 in req.shifts:
        for s2 in req.shifts:
            gap = 1
            # Rest grows by 1440 per gap; collect every gap still in violation.
            while gap * 1440 + s2.start_min - s1.start_min - s1.duration_min < rest_min:
                bad.add((gap, s1.id, s2.id))
                gap += 1
                if gap >= req.num_days:
                    break
    return bad


def _fixed_conflict_message(req: SolveRequest) -> Optional[str]:
    """Targeted INFEASIBLE reason caused by fixed_assignments alone, if any."""
    fixed = {(a.employee_id, a.day, a.shift_id) for a in req.fixed_assignments}
    if not fixed:
        return None

    hard_off = {(t.employee_id, t.day) for t in req.time_off if t.hard}
    per_emp_day: dict[tuple[str, int], set[str]] = {}
    for eid, d, sid in fixed:
        if (eid, d) in hard_off:
            return (f"Fixed assignment for '{eid}' on day {d} conflicts with "
                    f"their hard time off.")
        per_emp_day.setdefault((eid, d), set()).add(sid)

    if req.constraints.one_shift_per_day:
        for (eid, d), sids in per_emp_day.items():
            if len(sids) > 1:
                return (f"Fixed assignments pin {len(sids)} shifts on day {d} for "
                        f"'{eid}' ({', '.join(sorted(sids))}) but one_shift_per_day "
                        f"is enabled.")

    for gap, s1, s2 in _forbidden_pairs(req):
        for eid, d, sid in fixed:
            if sid == s1 and (eid, d + gap, s2) in fixed:
                return (f"Fixed assignments for '{eid}' break min rest: "
                        f"'{s1}' on day {d} then '{s2}' on day {d + gap}.")

    return None


def solve(req: SolveRequest) -> SolveResponse:
    started = time.perf_counter()

    conflict = _fixed_conflict_message(req)
    if conflict:
        return SolveResponse(
            status="INFEASIBLE",
            message=conflict,
            solve_time_ms=(time.perf_counter() - started) * 1000,
        )

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

    # ------------------------------------------------------ fixed + hints
    # Pinned assignments the user has already approved (model_validator guarantees
    # the referenced vars exist).
    for a in req.fixed_assignments:
        model.add(assign[(a.employee_id, a.day, a.shift_id)] == 1)

    # Warm start from a previous solution (fixed ones included); stale refs are
    # advisory and simply skipped.
    hinted: set[tuple[str, int, str]] = set()
    for a in [*req.fixed_assignments, *req.hint_assignments]:
        key = (a.employee_id, a.day, a.shift_id)
        if key in hinted:
            continue
        hinted.add(key)
        var = assign.get(key)
        if var is not None:
            model.add_hint(var, 1)

    # ------------------------------------------------------- coverage + skills
    # min_staff and skill coverage are soft: a dominant per-person penalty makes
    # the solver staff every slot it can and report what it could not (shortfalls,
    # recomputed independently in stats), instead of failing the whole request
    # the moment capacity is short. max_staff stays hard.
    understaff_vars: list[cp_model.IntVar] = []
    for (d, sid), c in coverage.items():
        slot_vars = [A(e.id, d, sid) for e in employees if (e.id, d, sid) in assign]
        staff_slack = model.new_int_var(0, c.min_staff, f"us_{d}_{sid}")
        model.add(sum(slot_vars) + staff_slack >= c.min_staff)
        understaff_vars.append(staff_slack)
        if c.max_staff is not None:
            model.add(sum(slot_vars) <= c.max_staff)
        for i, skill in enumerate(c.required_skills):
            qualified = [A(e.id, d, sid) for e in employees
                         if (e.id, d, sid) in assign and skill in e.skills]
            if not qualified:
                return SolveResponse(
                    status="INFEASIBLE",
                    message=(f"No employee has required skill '{skill}' "
                             f"for shift '{sid}' on day {d}."),
                )
            skill_slack = model.new_bool_var(f"sk_us_{d}_{sid}_{i}")
            model.add(sum(qualified) + skill_slack >= 1)
            understaff_vars.append(skill_slack)

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
    for gap, s1, s2 in _forbidden_pairs(req):
        for e in employees:
            for d in range(num_days - gap):
                if (e.id, d, s1) in assign and (e.id, d + gap, s2) in assign:
                    model.add(A(e.id, d, s1) + A(e.id, d + gap, s2) <= 1)

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

    # Understaffing dominates every other term, so slots go short only when forced.
    if understaff_vars:
        obj_terms.append(weights.understaff_penalty * sum(understaff_vars))

    if soft_penalty_vars and weights.soft_timeoff > 0:
        obj_terms.append(weights.soft_timeoff * sum(soft_penalty_vars))

    if weights.preference > 0:
        pref_terms = [p.weight * A(p.employee_id, d, p.shift_id)
                      for p in req.preferences
                      for d in range(num_days)
                      if (p.employee_id, d, p.shift_id) in assign]
        if pref_terms:
            obj_terms.append(-weights.preference * sum(pref_terms))

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

    # ------------------------------------------------- cross-dimension balance
    # 周末休次数常常除不尽(如 4 周 5 人 → 2,2,2,2,1)。把各维度折算成同一把
    # "辛苦尺"(周末班 480 分、夜班 240 分、工时 60 分/时)再均衡综合分:
    # 周末被迫多休不了的人,自动在夜班/工时上得到补偿,而不是被随机安排。
    if weights.fairness_balance > 0 and len(employees) > 1:
        hardship = []
        for e in employees:
            hr = sum(A(e.id, d, sid) * shifts[sid].duration_min
                     for d in range(num_days) for sid in shifts if (e.id, d, sid) in assign)
            wk = sum(A(e.id, d, sid) for d in range(num_days) if req.is_weekend(d)
                     for sid in shifts if (e.id, d, sid) in assign)
            nt = sum(A(e.id, d, sid) for d in range(num_days) for sid in shifts
                     if (e.id, d, sid) in assign and shifts[sid].is_night)
            hardship.append(hr + 480 * wk + 240 * nt)
        add_spread(hardship, weights.fairness_balance)

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
            fixed_note = (" Fixed assignments can also cause this — review them."
                          if req.fixed_assignments else "")
            return SolveResponse(
                status="INFEASIBLE",
                message=("No schedule satisfies all hard constraints. Try lowering "
                         "min_staff, relaxing rest/hours limits, or adding staff."
                         + fixed_note),
                solve_time_ms=elapsed_ms,
            )
        msg = (f"No solution found within {req.max_solve_seconds}s. "
               f"Try increasing max_solve_seconds.")
        return SolveResponse(status="UNKNOWN", message=msg, solve_time_ms=elapsed_ms)

    assignments = [Assignment(employee_id=eid, day=d, shift_id=sid)
                   for (eid, d, sid), var in assign.items() if cp.value(var)]
    assignments.sort(key=lambda a: (a.day, a.shift_id, a.employee_id))

    shortfalls = compute_shortfalls(req, assignments)
    message = ""
    if shortfalls:
        message = (f"Best-effort schedule: {len(shortfalls)} slot(s) understaffed — "
                   f"see shortfalls for where staff or skills are missing.")

    return SolveResponse(
        status="OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
        assignments=assignments,
        stats=compute_stats(req, assignments),
        shortfalls=shortfalls,
        objective_value=cp.objective_value,
        solve_time_ms=elapsed_ms,
        message=message,
    )
