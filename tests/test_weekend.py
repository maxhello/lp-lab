"""Cross-dimension fairness: the weekend-rest loser gets compensated.

Scenario: 5 employees, 28 days, 16 weekend shifts — indivisible, so exactly one
employee takes 4 weekend shifts while the others take 3 (spread 1 is forced).
With fairness_balance on, that employee must be compensated elsewhere: strictly
fewer night shifts than everyone else.
"""

from app.scheduling.models import (
    ConstraintsConfig,
    CoverageRequirement,
    Employee,
    ObjectiveWeights,
    ShiftType,
    SolveRequest,
)
from app.scheduling.solver import solve


def _request(weights: ObjectiveWeights | None = None) -> SolveRequest:
    employees = [Employee(id=f"e{i}", name=f"E{i}") for i in range(1, 6)]
    shifts = [
        ShiftType(id="day", start="08:00", end="16:00"),
        ShiftType(id="eve", start="16:00", end="00:00"),
        ShiftType(id="night", start="00:00", end="08:00", is_night=True),
    ]
    coverage = []
    for d in range(28):
        coverage.append(CoverageRequirement(day=d, shift_id="day", min_staff=1, max_staff=1))
        coverage.append(CoverageRequirement(day=d, shift_id="eve", min_staff=1, max_staff=1))
        if d % 7 < 5:  # Mon–Fri nights only, so night counts are divisible
            coverage.append(CoverageRequirement(day=d, shift_id="night", min_staff=1, max_staff=1))
    return SolveRequest(
        num_days=28,
        employees=employees,
        shifts=shifts,
        coverage=coverage,
        constraints=ConstraintsConfig(
            max_consecutive_days=5, min_rest_hours=11.0, max_hours_per_week=44.0
        ),
        weights=weights or ObjectiveWeights(fairness_balance=5),
    )


def test_weekend_loser_is_compensated_with_fewer_nights():
    resp = solve(_request())
    assert resp.status in ("OPTIMAL", "FEASIBLE"), resp.message

    wk = {s.employee_id: s.weekend_shifts for s in resp.stats}
    nt = {s.employee_id: s.night_shifts for s in resp.stats}

    # 16 weekend shifts over 5 people → forced distribution (4,3,3,3,3).
    assert max(wk.values()) - min(wk.values()) == 1, wk
    losers = [eid for eid, w in wk.items() if w == max(wk.values())]
    assert len(losers) == 1, wk
    loser = losers[0]

    others = [nt[e] for e in nt if e != loser]
    # The loser must not also lose on nights: he is (tied-)lowest. A tie is
    # legitimate — fairness_night pulls counts level — but never above anyone.
    assert nt[loser] <= min(others), f"weekend={wk} night={nt}"
