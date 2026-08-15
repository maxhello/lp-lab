import pytest

from app.scheduling.models import (
    ConstraintsConfig,
    CoverageRequirement,
    Employee,
    Preference,
    ShiftType,
    SolveRequest,
    TimeOff,
)


@pytest.fixture
def small_request() -> SolveRequest:
    """5 employees, 3 shifts (early/late/night), 7 days, moderate coverage."""
    employees = [
        Employee(id=f"e{i}", name=f"Emp{i}", skills=["cpr"] if i % 2 == 0 else [])
        for i in range(1, 6)
    ]
    shifts = [
        ShiftType(id="early", start="08:00", end="16:00"),
        ShiftType(id="late", start="16:00", end="00:00"),
        ShiftType(id="night", start="00:00", end="08:00", is_night=True),
    ]
    coverage = []
    for d in range(7):
        coverage.append(CoverageRequirement(day=d, shift_id="early", min_staff=1, max_staff=2))
        coverage.append(CoverageRequirement(day=d, shift_id="late", min_staff=1, max_staff=2))
        coverage.append(CoverageRequirement(day=d, shift_id="night", min_staff=1, max_staff=1))
    coverage[0].required_skills = []  # keep the base scenario solvable without skills

    return SolveRequest(
        num_days=7,
        employees=employees,
        shifts=shifts,
        coverage=coverage,
        constraints=ConstraintsConfig(
            max_consecutive_days=4,
            min_rest_hours=8.0,
            max_hours_per_week=44.0,
        ),
    )


@pytest.fixture
def infeasible_request(small_request: SolveRequest) -> SolveRequest:
    """min_staff=10 with 5 employees — trivially infeasible."""
    data = small_request.model_dump()
    for c in data["coverage"]:
        if c["shift_id"] == "early":
            c["min_staff"] = 10
            c["max_staff"] = 10
    return SolveRequest(**data)


@pytest.fixture
def uniform_request(small_request: SolveRequest) -> SolveRequest:
    """Identical employees, uniform coverage — fairness spread should be tiny."""
    data = small_request.model_dump()
    for e in data["employees"]:
        e["skills"] = []
    for c in data["coverage"]:
        c["required_skills"] = []
    return SolveRequest(**data)


def _timeoff_and_pref_request(small_request: SolveRequest) -> SolveRequest:
    data = small_request.model_dump()
    data["time_off"] = [TimeOff(employee_id="e1", day=0, hard=True).model_dump()]
    data["preferences"] = [Preference(employee_id="e2", shift_id="early", weight=3).model_dump()]
    return SolveRequest(**data)
