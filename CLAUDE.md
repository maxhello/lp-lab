# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

lp-lab — a collection of small optimization tools powered by Google OR-Tools, served by one FastAPI app. Current tool: CP-SAT employee shift scheduling (`app/scheduling/`). Code/docstrings are in English; UI text and user-facing warnings are in Chinese.

## Commands

```bash
pip install -e ".[dev]"          # install (a .venv already exists in repo)
pytest                           # run all tests
pytest tests/test_solver.py      # one file
pytest tests/test_solver.py::test_feasible_scenario_satisfies_all_hard_constraints  # one test
uvicorn app.scheduling.api:app --reload   # dev server → http://127.0.0.1:8000
```

No database, no build step. The frontend is a single vanilla-JS HTML file served via `FileResponse` from `static/`, plus self-hosted frontend libs in `static/vendor/` (three.js 0.170 for the 3D duty chart) mounted at `/vendor` in `create_app()` — resolve them in the page via the importmap; no external CDN.

## Architecture

**One tool = one subpackage under `app/`**, wired together by three seams:

1. `app/pages.py` — the shared home page. Its `TOOLS` list is the registry: a new tool appends a card dict there (slug, title, desc, href).
2. Each tool's `api.py` exposes `create_app()` which calls `register_home(app)` then mounts the tool's `APIRouter` at `/tools/<slug>` (HTML page) and `/api/<slug>/...` (JSON endpoints). The module-level `app = create_app()` is what uvicorn runs.
3. `pyproject.toml` `[tool.setuptools] packages` must list each new `app.<name>` subpackage explicitly.

Tool-internal layout (see `app/scheduling/` for the reference implementation):

- `models.py` — Pydantic request/response. Cross-field reference validation (unknown shift ids, out-of-range days, duplicates) lives in a `model_validator(mode="after")`, so bad input 422s at the API boundary. Domain helpers like `is_weekend(day)` live on the request model.
- `solver.py` — builds and solves the CP-SAT model; pure function `SolveRequest -> SolveResponse`.
- `stats.py` — **recomputes** result stats from raw assignments, deliberately independent of solver internals. Tests verify hard constraints the same way — never trust the solver's own claims.
- `static/<name>.html` — the UI; talks only to the tool's `/api/<slug>/` endpoints, auto-saves input to localStorage.

## Scheduling domain invariants (solver.py)

- Decision vars are `assign[(employee_id, day, shift_id)]` booleans; **a variable only exists if that (day, shift) slot appears in `coverage`** — code throughout guards with `if (eid, d, sid) in assign`.
- All durations are integer minutes. `ShiftType.duration_min` treats `end == start` as 24h; cross-midnight handled by `+ 24*60`.
- Min-rest is enforced via precomputed forbidden `(gap, s1, s2)` triples for any gap ≥ 1 (a long evening shift can leave too little rest before an early shift **two** days later); rest formula: `gap*1440 + start2 - start1 - duration1`.
- Coverage `min_staff` and required skills are **soft**: slack vars per missing person / missing skill, penalised by `ObjectiveWeights.understaff_penalty` (default 100k — must dominate all other terms). Short-staffed input returns a best-effort schedule with `SolveResponse.shortfalls` (recomputed independently in `stats.compute_shortfalls`). `max_staff` stays hard. Remaining INFEASIBLE paths: nobody holds a required skill, or `fixed_assignments` conflict (with hard time off, one-per-day, or min rest — checked up front in `_fixed_conflict_message`).
- `fixed_assignments` pin assign vars to 1 (model_validator rejects refs to slots without coverage); `hint_assignments` warm-start via `model.add_hint` and are advisory — stale refs are skipped. The web UI locks gantt bars into `fixed_assignments` and sends the previous solution as hints **only when the hint is still valid** (unchanged input or last solve timed out at FEASIBLE) — a stale hint after edits measured ~2× slower, a valid one 7–15× faster.
- Day 0 is Monday unless `start_date` is given; weekend = `(anchor_weekday + day) % 7 in (5, 6)` via `SolveRequest.is_weekend`.
- Objective minimizes understaffing (dominant), then fairness spreads (max−min of hours/weekend/night counts) plus soft-timeoff penalties, minus preference rewards (`Preference.weight` × per-shift, scaled by `weights.preference`) — weights in `ObjectiveWeights`. Optional `fairness_balance` (default 0, UI sends 2) adds a composite hardship spread (weekend shift=480, night=240, 1h=60 in minutes-scale points) that compensates a weekend-rest loser with fewer nights/hours; scale mismatch with the count-based weights is why it is opt-in.

## Git

- Committing locally is fine, but **never push to the remote without explicit user approval** — always ask first.

## Gotchas

- README's API section says `/api/solve`; the real endpoints are `/api/scheduling/solve` and `/api/scheduling/validate` (routes are namespaced per tool).
- `app/__init__.py` is intentionally empty; don't put tool code there.
- `api.py` `validate_request()` does cheap pre-solve checks (skill eligibility, aggregate hours-vs-capacity, fixed-assignment conflicts) meant to catch obviously infeasible input before CP-SAT runs — keep solver warnings in English, validate warnings in Chinese.
