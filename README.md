# ⚡ lp-lab

Lightning-fast employee shift scheduling engine powered by Google OR-Tools CP-SAT.
Define your staff, rules and coverage — get a fair, legal schedule in seconds.

排班求解器:定义员工、规则与人力覆盖需求,秒级求解出公平、合规的排班表。

## Features

- **Coverage requirements** — min/max headcount per shift per day, plus required skills
  (e.g. "every late shift needs at least one CPR-certified employee")
- **Labor rules** — max consecutive working days, minimum rest between shifts
  (incl. cross-midnight shifts), weekly hour caps (global or per-employee), one shift per day
- **Fairness** — balanced total hours, even rotation of weekend and night shifts
- **Preferences** — preferred shifts (soft), time-off requests (hard or soft)
- Web UI + REST API, no database, solutions in tens of milliseconds

## Quick start

```bash
pip install -e .
uvicorn app.scheduling.api:app --reload
```

Open http://127.0.0.1:8000 — click **加载示例** (load example), then **⚡ 开始求解**.

## Web UI

1. **基础配置** — schedule length, consecutive-day limit, min rest, weekly hour cap
2. **员工** — name, id, skills, optional personal hour cap
3. **班次类型** — shift id, start/end time (cross-midnight supported), night flag
4. **每日覆盖需求** — per day × shift grid, each cell `min-max` (e.g. `2-3`); empty = not scheduled
5. **休假申请** — CSV lines: `employee_id, day, hard|soft`
6. **班次偏好** — CSV lines: `employee_id, shift_id, weight`

The result is a color-coded schedule grid plus a fairness stats table
(hours / weekend / night spread per employee). Export to CSV with one click.
Input is auto-saved to localStorage.

## REST API

### `POST /api/solve`

```bash
curl -X POST http://127.0.0.1:8000/api/solve \
  -H 'Content-Type: application/json' \
  -d '{
    "num_days": 7,
    "employees": [
      {"id": "e1", "name": "Alice", "skills": ["cpr"]},
      {"id": "e2", "name": "Bob", "skills": ["cpr", "supervisor"]},
      {"id": "e3", "name": "Carol", "skills": []}
    ],
    "shifts": [
      {"id": "early", "start": "08:00", "end": "16:00"},
      {"id": "late",  "start": "16:00", "end": "00:00"},
      {"id": "night", "start": "00:00", "end": "08:00", "is_night": true}
    ],
    "coverage": [
      {"day": 0, "shift_id": "early", "min_staff": 1, "max_staff": 2,
       "required_skills": ["cpr"]}
    ],
    "time_off":      [{"employee_id": "e1", "day": 5, "hard": true}],
    "preferences":   [{"employee_id": "e2", "shift_id": "early", "weight": 3}],
    "constraints": {
      "max_consecutive_days": 5,
      "min_rest_hours": 11,
      "max_hours_per_week": 40
    }
  }'
```

Response:

```json
{
  "status": "OPTIMAL",
  "assignments": [
    {"employee_id": "e2", "day": 0, "shift_id": "early"},
    {"employee_id": "e1", "day": 0, "shift_id": "night"}
  ],
  "stats": [
    {"employee_id": "e1", "name": "Alice", "total_hours": 40.0, "shift_count": 5,
     "weekend_shifts": 1, "night_shifts": 2, "preferred_shifts": 0}
  ],
  "objective_value": 12.0,
  "solve_time_ms": 24.1,
  "message": ""
}
```

`status` is `OPTIMAL`, `FEASIBLE`, `INFEASIBLE` (check `message` for hints), or `UNKNOWN` (time limit).

### `POST /api/validate`

Same body; returns cheap pre-checks (`{"ok": false, "warnings": [...]}`) such as
"min_staff exceeds the number of skill-qualified employees" — useful to catch
obviously impossible setups before solving.

Interactive docs: http://127.0.0.1:8000/docs

## How it works

Each `(employee, day, shift)` triple becomes a boolean variable in a CP-SAT model:

| Rule | Encoding |
|---|---|
| Coverage | `min ≤ Σ assigns ≤ max` per slot |
| Required skill | `Σ qualified-employee assigns ≥ 1` |
| One shift/day | `Σ shift assigns ≤ 1` per employee-day |
| Consecutive days | sliding-window sum ≤ limit |
| Min rest | forbidden adjacent-day shift pairs (cross-midnight aware) |
| Weekly cap | `Σ assigns × duration ≤ cap` per 7-day window |
| Hard time-off | variable fixed to 0 |

The objective minimises (with configurable weights):
- **fairness**: max−min spread of total hours, weekend-shift counts, night-shift counts
- **cross-dimension balance** (`fairness_balance`, off by default, the web UI enables 2):
  weekend shifts, night shifts and hours are converted into one hardship score
  (weekend shift = 480, night shift = 240, 1 hour = 60) whose spread is minimised —
  when weekend rests cannot be split evenly (e.g. 4 weekends over 5 people),
  the person who loses weekend rest is compensated with fewer nights / lighter load
- **preference violations**: soft time-offs assigned
- and maximises preferred-shift assignments

## Assumptions

- Day `0` is **Monday**; weekend = days 5 and 6 (`day % 7 ∈ {5, 6}`)
- Rest is enforced between shifts on **adjacent days** only (exact for shifts < 48h span)
- Night-shift fairness counts shifts flagged `is_night`
- Weekly caps apply to each rolling block of 7 days starting at day 0

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests re-verify every hard constraint by independent recomputation on returned
assignments — the solver's own claims are not trusted.

## Deploy

- **Render**(免费,推荐):[render.com](https://render.com) → New → Web Service →
  连接 GitHub 仓库,自动读取 `render.yaml`;此后每次 `git push` 自动部署。
  免费档 15 分钟无访问会休眠,冷启动约 30~60 秒。
- **其他平台**(Fly.io / Railway / Koyeb / 自建服务器):根目录已提供 `Dockerfile`。

## Roadmap

- [ ] Infeasibility explanation (which constraint conflicts)
- [ ] Multi-week patterns / rotating templates
- [ ] Employee unavailability date ranges

## License

MIT
