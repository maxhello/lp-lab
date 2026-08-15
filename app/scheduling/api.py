"""Scheduling tool: FastAPI routes — tool page + solve/validate endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..pages import register_home
from .models import SolveRequest, SolveResponse
from .solver import solve

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


class ValidateResponse(BaseModel):
    ok: bool
    warnings: list[str] = []


router = APIRouter()


@router.get("/tools/scheduling", include_in_schema=False)
def tool_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "scheduling.html")


@router.post("/api/scheduling/solve", response_model=SolveResponse)
def api_solve(req: SolveRequest) -> SolveResponse:
    return solve(req)


@router.post("/api/scheduling/validate")
def api_validate(req: SolveRequest) -> ValidateResponse:
    return validate_request(req)


def validate_request(req: SolveRequest) -> ValidateResponse:
    """Cheap pre-solve checks: spot obviously impossible slots before burning solver time."""
    warnings: list[str] = []

    for c in req.coverage:
        eligible = [e for e in req.employees
                    if all(skill in e.skills for skill in c.required_skills)]
        if c.min_staff > len(eligible):
            warnings.append(
                f"Day {c.day} shift '{c.shift_id}': min_staff={c.min_staff} but only "
                f"{len(eligible)} employee(s) hold all required skills "
                f"{c.required_skills or '(none)'}."
            )

    warnings.extend(_capacity_warnings(req))

    return ValidateResponse(ok=not warnings, warnings=warnings)


def _capacity_warnings(req) -> list[str]:
    """Aggregate feasibility check: total demanded hours vs total staff capacity.

    Catches the classic 'works for a week, infeasible for a month' case where
    per-week hour caps make long schedules impossible well before CP-SAT runs.
    """
    warnings = []
    shift_by_id = {s.id: s for s in req.shifts}

    demand_hours = sum(c.min_staff * shift_by_id[c.shift_id].duration_min / 60
                       for c in req.coverage)
    weeks = req.num_days / 7
    capacity_hours = sum(
        (e.max_hours_per_week if e.max_hours_per_week is not None
         else req.constraints.max_hours_per_week) * weeks
        for e in req.employees
    )

    if demand_hours > capacity_hours + 1e-9:
        shortage = demand_hours - capacity_hours
        extra_needed = int(shortage // req.constraints.max_hours_per_week) + 1
        per_day_short = shortage / req.num_days
        warnings.append(
            f"总需求 {demand_hours:.0f}h 超过员工容量 {capacity_hours:.0f}h "
            f"(缺 {shortage:.0f}h,平均每天缺 {per_day_short:.1f}h)。"
            f"建议:增加约 {extra_needed} 名员工,或降低部分班次人数,或提高每周工时上限。"
        )
    else:
        utilization = demand_hours / capacity_hours * 100 if capacity_hours else 0
        if utilization > 95:
            warnings.append(
                f"利用率已达 {utilization:.0f}%({demand_hours:.0f}h / {capacity_hours:.0f}h),"
                f"几乎没有弹性空间,连续天数/休息等约束可能让求解失败。"
            )
    return warnings


def create_app() -> FastAPI:
    app = FastAPI(title="lp-lab", version="0.1.0")
    register_home(app)
    app.include_router(router)
    return app


app = create_app()
