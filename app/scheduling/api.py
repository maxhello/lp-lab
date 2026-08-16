"""Scheduling tool: FastAPI routes — tool page + solve/validate endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..pages import register_home
from .models import SolveRequest, SolveResponse
from .solver import solve

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
VENDOR_DIR = STATIC_DIR / "vendor"


class ValidateResponse(BaseModel):
    ok: bool
    warnings: list[str] = []


router = APIRouter()


@router.get("/tools/scheduling", include_in_schema=False)
def tool_page() -> FileResponse:
    # no-cache: 每次带 etag 再验证(未变则 304),避免浏览器启发式缓存把旧版
    # HTML 一直留在本地 —— 前端无构建步骤,页面更新全靠重新拉取。
    return FileResponse(STATIC_DIR / "scheduling.html",
                        headers={"Cache-Control": "no-cache"})


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
    warnings.extend(_fixed_assignment_warnings(req))

    return ValidateResponse(ok=not warnings, warnings=warnings)


def _fixed_assignment_warnings(req: SolveRequest) -> list[str]:
    """锁定班次与其他输入的显式冲突:能在求解前指出,就不浪费一次求解。"""
    if not req.fixed_assignments:
        return []
    warnings: list[str] = []

    hard_off = {(t.employee_id, t.day) for t in req.time_off if t.hard}
    per_emp_day: dict[tuple[str, int], set[str]] = {}
    for a in req.fixed_assignments:
        if (a.employee_id, a.day) in hard_off:
            warnings.append(
                f"{a.employee_id} 第 {a.day} 天已申请硬休假,锁定的 {a.shift_id} 班与之冲突,必然无解。"
            )
        per_emp_day.setdefault((a.employee_id, a.day), set()).add(a.shift_id)

    if req.constraints.one_shift_per_day:
        for (eid, d), sids in per_emp_day.items():
            if len(sids) > 1:
                warnings.append(
                    f"{eid} 第 {d} 天锁定了 {len(sids)} 个班次({'、'.join(sorted(sids))}),"
                    f"与「每天最多一班」冲突,必然无解。"
                )

    shift_by_id = {s.id: s for s in req.shifts}
    per_emp_week: dict[tuple[str, int], int] = {}
    for a in req.fixed_assignments:
        key = (a.employee_id, a.day // 7)
        per_emp_week[key] = per_emp_week.get(key, 0) + shift_by_id[a.shift_id].duration_min
    emp_by_id = {e.id: e for e in req.employees}
    for (eid, w), mins in sorted(per_emp_week.items()):
        e = emp_by_id[eid]
        cap = (e.max_hours_per_week if e.max_hours_per_week is not None
               else req.constraints.max_hours_per_week) * 60
        if mins > cap + 1e-9:
            warnings.append(
                f"{_week_label(req, w * 7, min(w * 7 + 6, req.num_days - 1))}:锁定的班次已有 "
                f"{mins / 60:.1f}h,超过 {eid} 的每周工时上限 {cap / 60:g}h,必然无解。"
            )
    return warnings


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
    # Self-hosted frontend libs (three.js for the 3D chart) — no external CDN.
    app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")
    app.include_router(router)
    return app


app = create_app()
