from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List

import pandas as pd

from core.models import (
    SystemContext,
    Roster, 
    Violation,
    ViolationSeverity,
    ContractType,
)

# === Default shift templates (fallback) ===
# These match the examples in management_roster_simplified.xlsx.
DEFAULT_SHIFT_TEMPLATES: Dict[str, Dict[str, object]] = {
    # Day shift
    "S": {
        "start": time(6, 30),
        "end": time(15, 0),
        "hours": 8.5,
    },
    # First half (opening + lunch)
    "1F": {
        "start": time(6, 30),
        "end": time(15, 30),
        "hours": 9.0,
    },
    # Second half (afternoon to close / dinner)
    "2F": {
        "start": time(14, 0),
        "end": time(23, 0),
        "hours": 9.0,
    },
    # Full day
    "3F": {
        "start": time(8, 0),
        "end": time(20, 0),
        "hours": 12.0,
    },
    # Shift-change / strong midday-evening coverage
    "SC": {
        "start": time(11, 0),
        "end": time(20, 0),
        "hours": 9.0,
    },
}


def _project_root() -> Path:
    """
    Project root = parent of the 'agents' and 'core' packages.
    This mirrors the path logic used in OrchestratorAgent.
    """
    return Path(__file__).resolve().parents[1]


def _load_shift_templates_from_excel() -> Dict[str, Dict[str, object]]:
    """
    Load shift code timings and hours from:
        data/raw/management_roster_simplified.xlsx  →  "Shift Codes" sheet

    We:
    - Read the sheet with header row at index 1.
    - Expect columns like: Code | Shift Name | Time | Hours | Description
      but in the actual file they appear as "Unnamed: 0", ..., "Unnamed: 4".
    - Keep only rows with numeric 'Hours'.
    - Parse time ranges like "06:30 - 15:00" into start/end times.
    - Merge results into DEFAULT_SHIFT_TEMPLATES (so defaults are the floor).
    """
    path = _project_root() / "data" / "raw" / "management_roster_simplified.xlsx"
    if not path.exists():
        # File missing – fall back to hard-coded defaults
        return DEFAULT_SHIFT_TEMPLATES.copy()

    try:
        xls = pd.ExcelFile(path)
        df = xls.parse("Shift Codes", header=1)
    except Exception:
        # Sheet missing or parse error – be safe and fall back
        return DEFAULT_SHIFT_TEMPLATES.copy()

    # The real sheet has columns: Unnamed: 0 … Unnamed: 4
    # where "Unnamed: 0" is the "Code" column, "Unnamed: 3" is "Hours".
    if "Unnamed: 0" not in df.columns or "Unnamed: 3" not in df.columns:
        return DEFAULT_SHIFT_TEMPLATES.copy()

    df = df.copy()
    df["hours_num"] = pd.to_numeric(df["Unnamed: 3"], errors="coerce")

    # Keep only rows that actually have numeric hours (skip header/footer text)
    df = df[df["hours_num"].notna()]
    df = df[df["Unnamed: 0"].notna()]

    templates: Dict[str, Dict[str, object]] = {}

    for _, row in df.iterrows():
        code = str(row["Unnamed: 0"]).strip()
        if not code or code.lower() == "code":
            # Skip the header row
            continue

        hours_val = row["hours_num"]
        hours = float(hours_val) if hours_val is not None else None

        # Time range is in "Unnamed: 2", values like "06:30 - 15:00" or "Varies"
        time_raw = row.get("Unnamed: 2", "")
        time_range = ""
        if not pd.isna(time_raw):
            time_range = str(time_raw).strip()

        start_t = None
        end_t = None

        # Parse "HH:MM - HH:MM" if present
        if "-" in time_range and ":" in time_range:
            left, right = [p.strip() for p in time_range.split("-", 1)]
            try:
                start_t = datetime.strptime(left, "%H:%M").time()
                end_t = datetime.strptime(right, "%H:%M").time()
            except ValueError:
                start_t = None
                end_t = None

        # If hours missing but we have times, compute hours from times
        if hours is None and start_t and end_t:
            base = datetime(2000, 1, 1)
            delta = datetime.combine(base.date(), end_t) - datetime.combine(
                base.date(), start_t
            )
            hours = delta.total_seconds() / 3600.0

        # Fall back to defaults (for S/1F/2F/3F/SC) if needed
        fallback_tpl = DEFAULT_SHIFT_TEMPLATES.get(code)

        if hours is None and fallback_tpl is not None:
            hours = float(fallback_tpl["hours"])
        if start_t is None and fallback_tpl is not None:
            start_t = fallback_tpl["start"]
        if end_t is None and fallback_tpl is not None:
            end_t = fallback_tpl["end"]

        # If we still don't have something usable, skip this row
        if start_t is None or end_t is None or hours is None:
            continue

        templates[code] = {
            "start": start_t,
            "end": end_t,
            "hours": hours,
        }

    # If we couldn't build anything, keep the safe defaults
    if not templates:
        return DEFAULT_SHIFT_TEMPLATES.copy()

    # Merge: defaults as base, Excel overrides on top
    merged = DEFAULT_SHIFT_TEMPLATES.copy()
    merged.update(templates)
    return merged


# === Shift templates with real times (now sourced from Excel where possible) ===
SHIFT_TEMPLATES: Dict[str, Dict[str, object]] = _load_shift_templates_from_excel()

# For compatibility with other agents (e.g. ConflictResolutionAgent)
SHIFT_HOURS: Dict[str, float] = {
    code: float(tpl["hours"]) for code, tpl in SHIFT_TEMPLATES.items()
}
DEFAULT_SHIFT_HOURS = 8.0

# Approximate 2-week bounds for each contract type (hours over 14 days).
CONTRACT_HOURS_BOUNDS: Dict[ContractType, tuple[float, float]] = {
    ContractType.FULL_TIME: (70.0, 76.0),   # 35–38 hours/week
    ContractType.PART_TIME: (40.0, 64.0),   # 20–32 hours/week
    ContractType.CASUAL: (16.0, 48.0),      # 8–24 hours/week
}

# Approximate weekly bounds for each contract type (per 7-day week).
WEEKLY_CONTRACT_HOURS_BOUNDS: Dict[ContractType, tuple[float, float]] = {
    ContractType.FULL_TIME: (35.0, 38.0),
    ContractType.PART_TIME: (20.0, 32.0),
    ContractType.CASUAL: (8.0, 24.0),
}

# Fair Work style rules (approximate)
MIN_SHIFT_HOURS_CASUAL = 3.0
MIN_REST_HOURS_BETWEEN_DAYS = 10.0
MAX_CONSECUTIVE_WORKING_DAYS = 6


@dataclass
class ComplianceAgent:
    """
    Performs compliance checks on a roster:
    - Hours per employee vs contract-type bounds (over the 2-week period)
    - Minimum shift length (especially for casuals)
    - Rest periods between days (~10 hours between shifts)
    """

    def check_roster(
        self,
        ctx: SystemContext,
        roster: Roster,
    ) -> List[Violation]:
        violations: List[Violation] = []

        # 1) Compute total hours per employee
        hours_by_emp: Dict[str, float] = {}
        for assignment in roster.assignments:
            emp_id = assignment.employee_id
            tpl = SHIFT_TEMPLATES.get(assignment.shift_code)
            if tpl is not None:
                hours = float(tpl["hours"])
            else:
                hours = DEFAULT_SHIFT_HOURS
            hours_by_emp[emp_id] = hours_by_emp.get(emp_id, 0.0) + hours

        # 2) Compare against contract bounds
        for emp_id, emp in ctx.employees.items():
            total_hours = hours_by_emp.get(emp_id, 0.0)
            bounds = CONTRACT_HOURS_BOUNDS.get(emp.contract_type)

            if bounds is None:
                continue

            min_h, max_h = bounds

            if total_hours < min_h:
                violations.append(
                    Violation(
                        severity=ViolationSeverity.SOFT,
                        code="MIN_HOURS_NOT_MET",
                        message=(
                            f"Employee {emp.name} ({emp.id}) has "
                            f"{total_hours:.1f}h, below min {min_h:.1f}h "
                            f"for contract type {emp.contract_type.value}."
                        ),
                        employee_id=emp.id,
                    )
                )

            if total_hours > max_h:
                violations.append(
                    Violation(
                        severity=ViolationSeverity.HARD,
                        code="MAX_HOURS_EXCEEDED",
                        message=(
                            f"Employee {emp.name} ({emp.id}) has "
                            f"{total_hours:.1f}h, above max {max_h:.1f}h "
                            f"for contract type {emp.contract_type.value}."
                        ),
                        employee_id=emp.id,
                    )
                )
        
        # 2b) Weekly hours per employee (week 1 vs week 2 of the window)
        weekly_hours: Dict[tuple[str, int], float] = {}
        for assignment in roster.assignments:
            tpl = SHIFT_TEMPLATES.get(assignment.shift_code)
            if tpl is not None:
                hours = float(tpl["hours"])
            else:
                hours = DEFAULT_SHIFT_HOURS

            # Position of this day relative to roster start
            if assignment.date < roster.start_date:
                continue
            days_from_start = (assignment.date - roster.start_date).days
            # week_index: 0 for the first 7 days, 1 for the next 7, etc.
            week_index = days_from_start // 7

            key = (assignment.employee_id, week_index)
            weekly_hours[key] = weekly_hours.get(key, 0.0) + hours

        for emp_id, emp in ctx.employees.items():
            bounds_week = WEEKLY_CONTRACT_HOURS_BOUNDS.get(emp.contract_type)
            if bounds_week is None:
                continue

            min_w, max_w = bounds_week

            # Only consider weeks where the employee actually worked
            for week_index in (0, 1):
                key = (emp_id, week_index)
                if key not in weekly_hours:
                    continue

                total_week = weekly_hours[key]

                if total_week < min_w:
                    violations.append(
                        Violation(
                            severity=ViolationSeverity.SOFT,
                            code="WEEKLY_MIN_HOURS_NOT_MET",
                            message=(
                                f"Employee {emp.name} ({emp.id}) has "
                                f"{total_week:.1f}h in week {week_index + 1}, "
                                f"below weekly min {min_w:.1f}h for contract "
                                f"type {emp.contract_type.value}."
                            ),
                            employee_id=emp.id,
                        )
                    )

                if total_week > max_w:
                    over = total_week - max_w

                    # Allow up to ~2h overtime per week as "near cap"
                    # without raising a formal violation. The solver
                    # still penalises weekly overtime via the CP-SAT
                    # objective, but the report only surfaces
                    # material breaches.
                    if over <= 2.0:
                        continue

                    violations.append(
                        Violation(
                            severity=ViolationSeverity.SOFT,
                            code="WEEKLY_MAX_HOURS_EXCEEDED",
                            message=(
                                f"Employee {emp.name} ({emp.id}) has "
                                f"{total_week:.1f}h in week {week_index + 1}, "
                                f"above weekly max {max_w:.1f}h for contract "
                                f"type {emp.contract_type.value}."
                            ),
                            employee_id=emp.id,
                        )
                    )

        # 3) Minimum shift length (esp. casuals) – future proof, even though
        # our current template codes are all >= 8 hours.
        for a in roster.assignments:
            emp = ctx.employees.get(a.employee_id)
            if emp is None:
                continue

            tpl = SHIFT_TEMPLATES.get(a.shift_code)
            if tpl is None:
                continue

            hours = float(tpl["hours"])
            if emp.contract_type == ContractType.CASUAL and hours < MIN_SHIFT_HOURS_CASUAL:
                violations.append(
                    Violation(
                        severity=ViolationSeverity.HARD,
                        code="MIN_SHIFT_LENGTH_CASUAL",
                        message=(
                            f"Employee {emp.name} ({emp.id}) has a "
                            f"{hours:.1f}h shift on {a.date}, "
                            f"below {MIN_SHIFT_HOURS_CASUAL:.1f}h minimum "
                            f"for casuals."
                        ),
                        employee_id=emp.id,
                        date=a.date,
                    )
                )

        # 4) Rest between days (~10 hours between shift end and next shift start)
        for emp_id, emp in ctx.employees.items():
            emp_assignments = sorted(
                [a for a in roster.assignments if a.employee_id == emp_id],
                key=lambda x: x.date,
            )
            # We only schedule at most one shift per day, so date ordering is enough
            for prev, nxt in zip(emp_assignments, emp_assignments[1:]):
                # Only check consecutive days; bigger gaps are fine
                if (nxt.date - prev.date).days != 1:
                    continue

                rest_hours = self._rest_hours_between(prev, nxt)
                if rest_hours < MIN_REST_HOURS_BETWEEN_DAYS:
                    violations.append(
                        Violation(
                            severity=ViolationSeverity.HARD,
                            code="INSUFFICIENT_REST",
                            message=(
                                f"Employee {emp.name} ({emp.id}) has only "
                                f"{rest_hours:.1f}h rest between "
                                f"{prev.date} ({prev.shift_code}) and "
                                f"{nxt.date} ({nxt.shift_code}), "
                                f"below {MIN_REST_HOURS_BETWEEN_DAYS:.1f}h."
                            ),
                            employee_id=emp.id,
                            date=nxt.date,
                        )
                    )
        
        # 5) Maximum consecutive working days (Fair Work: max 6 days in a row)
        for emp_id, emp in ctx.employees.items():
            emp_assignments = sorted(
                [a for a in roster.assignments if a.employee_id == emp_id],
                key=lambda x: x.date,
            )
            if not emp_assignments:
                continue

            streak = 1
            # Walk through their shifts day by day
            for prev, nxt in zip(emp_assignments, emp_assignments[1:]):
                if (nxt.date - prev.date).days == 1:
                    streak += 1
                else:
                    streak = 1

                if streak > MAX_CONSECUTIVE_WORKING_DAYS:
                    violations.append(
                        Violation(
                            severity=ViolationSeverity.HARD,
                            code="MAX_CONSECUTIVE_DAYS_EXCEEDED",
                            message=(
                                f"Employee {emp.name} ({emp.id}) works "
                                f"{streak} consecutive days ending {nxt.date}, "
                                f"above Fair Work limit of "
                                f"{MAX_CONSECUTIVE_WORKING_DAYS}."
                            ),
                            employee_id=emp.id,
                            date=nxt.date,
                        )
                    )
                    # Once we flag that this run of days is too long,
                    # we can stop for this employee.
                    break

        # 6) Sanity check: any assignment for unknown employees?
        for a in roster.assignments:
            if a.employee_id not in ctx.employees:
                violations.append(
                    Violation(
                        severity=ViolationSeverity.HARD,
                        code="UNKNOWN_EMPLOYEE",
                        message=(
                            f"Roster references unknown employee_id "
                            f"{a.employee_id!r} on {a.date}."
                        ),
                        employee_id=a.employee_id,
                        date=a.date,
                    )
                )

        return violations

    @staticmethod
    def _rest_hours_between(prev_assignment, next_assignment) -> float:
        """
        Compute rest hours between the end of prev_assignment and
        the start of next_assignment, using SHIFT_TEMPLATES times.
        """
        tpl_prev = SHIFT_TEMPLATES.get(prev_assignment.shift_code)
        tpl_next = SHIFT_TEMPLATES.get(next_assignment.shift_code)
        if not tpl_prev or not tpl_next:
            # If we don't know times, be conservative and assume enough rest.
            return 24.0

        prev_end_dt = datetime.combine(prev_assignment.date, tpl_prev["end"])
        next_start_dt = datetime.combine(next_assignment.date, tpl_next["start"])
        delta = next_start_dt - prev_end_dt
        return delta.total_seconds() / 3600.0
