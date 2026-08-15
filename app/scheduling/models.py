"""Pydantic input/output models for the shift scheduling solver."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Employee(BaseModel):
    id: str = Field(..., description="Unique employee identifier")
    name: str = Field(..., min_length=1, description="Display name")
    skills: list[str] = Field(default_factory=list)
    max_hours_per_week: Optional[float] = Field(
        default=None, ge=0, description="Override the global weekly hour cap for this employee"
    )
    max_shifts_per_week: Optional[int] = Field(
        default=None, ge=0, description="Override the global weekly shift-count cap for this employee"
    )


class ShiftType(BaseModel):
    id: str = Field(..., description="Unique shift identifier, e.g. 'early'")
    name: Optional[str] = Field(default=None, description="Display name; defaults to id")
    start: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="Start time HH:MM, e.g. '08:00'")
    end: str = Field(..., pattern=r"^\d{2}:\d{2}$", description="End time HH:MM; may cross midnight")
    is_night: bool = Field(default=False, description="Counted for night-shift fairness rotation")

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        h, m = v.split(":")
        if int(h) > 23 or int(m) > 59:
            raise ValueError(f"invalid time of day: {v}")
        return v

    @property
    def start_min(self) -> int:
        h, m = self.start.split(":")
        return int(h) * 60 + int(m)

    @property
    def end_min(self) -> int:
        h, m = self.end.split(":")
        return int(h) * 60 + int(m)

    @property
    def duration_min(self) -> int:
        """Shift length in minutes; end == start is treated as a 24h shift."""
        d = self.end_min - self.start_min
        return d if d > 0 else d + 24 * 60


class CoverageRequirement(BaseModel):
    day: int = Field(..., ge=0, description="Day index, 0-based")
    shift_id: str
    min_staff: int = Field(..., ge=0)
    max_staff: Optional[int] = Field(default=None, ge=0, description="None means no upper bound")
    required_skills: list[str] = Field(
        default_factory=list,
        description="Each listed skill must be held by at least one assignee",
    )


class TimeOff(BaseModel):
    employee_id: str
    day: int = Field(..., ge=0)
    hard: bool = Field(
        default=True,
        description="Hard: never schedule. Soft: penalised but allowed if needed.",
    )


class Preference(BaseModel):
    employee_id: str
    shift_id: str
    weight: int = Field(default=1, ge=1, description="Reward granted per preferred shift assigned")


class ConstraintsConfig(BaseModel):
    max_consecutive_days: int = Field(default=5, ge=1)
    min_rest_hours: float = Field(default=11.0, ge=0)
    max_hours_per_week: float = Field(default=40.0, gt=0)
    max_shifts_per_week: Optional[int] = Field(
        default=None, ge=1, description="Optional global cap on shifts per 7-day window"
    )
    one_shift_per_day: bool = True


class ObjectiveWeights(BaseModel):
    fairness_hours: int = Field(default=10, ge=0)
    fairness_weekend: int = Field(default=5, ge=0)
    fairness_night: int = Field(default=5, ge=0)
    preference: int = Field(default=1, ge=0)
    soft_timeoff: int = Field(default=50, ge=0)


class Assignment(BaseModel):
    employee_id: str
    day: int
    shift_id: str


class EmployeeStats(BaseModel):
    employee_id: str
    name: str
    total_hours: float
    shift_count: int
    weekend_shifts: int
    night_shifts: int
    preferred_shifts: int


class SolveResponse(BaseModel):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"]
    assignments: list[Assignment] = Field(default_factory=list)
    stats: list[EmployeeStats] = Field(default_factory=list)
    objective_value: Optional[float] = None
    solve_time_ms: float = 0.0
    message: str = ""


class SolveRequest(BaseModel):
    num_days: int = Field(..., ge=1, le=62)
    start_date: Optional[str] = Field(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Display-only: schedule start date (YYYY-MM-DD). Day 0 is this date's weekday.",
    )
    employees: list[Employee] = Field(..., min_length=1)
    shifts: list[ShiftType] = Field(..., min_length=1)
    coverage: list[CoverageRequirement] = Field(..., min_length=1)
    time_off: list[TimeOff] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    constraints: ConstraintsConfig = Field(default_factory=ConstraintsConfig)
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    max_solve_seconds: float = Field(default=10.0, gt=0, le=120.0)

    @model_validator(mode="after")
    def _check_references(self) -> "SolveRequest":
        employee_ids = {e.id for e in self.employees}
        if len(employee_ids) != len(self.employees):
            raise ValueError("duplicate employee ids")
        shift_ids = {s.id for s in self.shifts}
        if len(shift_ids) != len(self.shifts):
            raise ValueError("duplicate shift ids")

        for c in self.coverage:
            if c.day >= self.num_days:
                raise ValueError(f"coverage day {c.day} out of range (num_days={self.num_days})")
            if c.shift_id not in shift_ids:
                raise ValueError(f"coverage references unknown shift '{c.shift_id}'")
            if c.max_staff is not None and c.max_staff < c.min_staff:
                raise ValueError(f"max_staff < min_staff for day {c.day} shift '{c.shift_id}'")

        for t in self.time_off:
            if t.employee_id not in employee_ids:
                raise ValueError(f"time_off references unknown employee '{t.employee_id}'")
            if t.day >= self.num_days:
                raise ValueError(f"time_off day {t.day} out of range")

        for p in self.preferences:
            if p.employee_id not in employee_ids:
                raise ValueError(f"preference references unknown employee '{p.employee_id}'")
            if p.shift_id not in shift_ids:
                raise ValueError(f"preference references unknown shift '{p.shift_id}'")

        return self

    def is_weekend(self, day: int) -> bool:
        """Weekend by actual calendar weekday: day 0 falls on start_date's weekday
        (Monday if no start_date given), so days 5 and 6 after that anchor are Sat/Sun."""
        anchor_monday = 0
        if self.start_date:
            from datetime import date
            y, m, d = map(int, self.start_date.split("-"))
            anchor_monday = date(y, m, d).weekday()  # 0=Mon ... 6=Sun
        return (anchor_monday + day) % 7 in (5, 6)
