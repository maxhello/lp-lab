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


def _week_label(req, w_start: int, w_end: int) -> str:
    """Human label for a week block: '第 3 周(08-31 ~ 09-06)' or day indices."""
    n = w_start // 7 + 1
    if req.start_date:
        from datetime import date, timedelta
        y, m, d = map(int, req.start_date.split("-"))
        s = date(y, m, d) + timedelta(days=w_start)
        e = date(y, m, d) + timedelta(days=w_end)
        return f"第 {n} 周({s.strftime('%m-%d')} ~ {e.strftime('%m-%d')})"
    return f"第 {n} 周(第 {w_start}~{w_end} 天)"


def _capacity_warnings(req) -> list[str]:
    """Per-week-block feasibility: demanded hours vs staff capacity.

    The solver caps hours per 7-day block starting at day 0 (the trailing
    partial block still gets a full cap), so check each block the same way.
    A global total can hide the real problem: holidays early in the schedule
    'save' hours that later full weeks cannot borrow.
    """
    warnings = []
    shift_by_id = {s.id: s for s in req.shifts}
    per_week_cap = sum(
        (e.max_hours_per_week if e.max_hours_per_week is not None
         else req.constraints.max_hours_per_week)
        for e in req.employees
    )

    for w_start in range(0, req.num_days, 7):
        w_end = min(w_start + 7, req.num_days) - 1
        demand = sum(c.min_staff * shift_by_id[c.shift_id].duration_min / 60
                     for c in req.coverage if w_start <= c.day <= w_end)
        if demand > per_week_cap + 1e-9:
            shortage = demand - per_week_cap
            extra_needed = -(-shortage // req.constraints.max_hours_per_week)  # ceil
            warnings.append(
                f"{_week_label(req, w_start, w_end)}:需求 {demand:.0f}h 超过该周容量 "
                f"{per_week_cap:.0f}h({len(req.employees)} 人 × 周上限),缺 {shortage:.0f}h。"
                f"建议:该周增加约 {int(extra_needed)} 名员工,或降低该周部分班次人数,或提高每周工时上限。"
            )

    # Soft hint: near-saturated overall (no hard per-week warning above).
    if not warnings:
        demand_hours = sum(c.min_staff * shift_by_id[c.shift_id].duration_min / 60
                           for c in req.coverage)
        blocks = len(range(0, req.num_days, 7))
        capacity_hours = per_week_cap * blocks
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
