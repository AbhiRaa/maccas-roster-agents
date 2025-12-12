from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import re
import pandas as pd

from core.models import (
    SkillTag,
    SystemContext,
    Employee,
    EmployeeAvailability,
    ContractType,
)

_WEEKDAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


@dataclass
class ManagementTemplateAgent:
    """
    Works off management_roster_simplified.xlsx / 'Monthly Roster' sheet.

    Responsibilities:
    - Learn a manager headcount template per weekday
      (load_manager_template).
    - Build actual manager employees + availability for a given
      planning window (load_manager_employees_for_window).
    - (Legacy) Tag existing employees as managers by name if desired.
    """

    raw_data_dir: Path

    # ---------- LOW-LEVEL HELPERS ----------

    def _load_monthly_roster(self) -> pd.DataFrame:
        path = self.raw_data_dir / "management_roster_simplified.xlsx"
        return pd.ExcelFile(path).parse("Monthly Roster")

    def _find_header_row_index(self, df: pd.DataFrame) -> int:
        """
        Find the row where column 1 == 'Employee Name'.
        """
        for idx in range(len(df)):
            val = str(df.iloc[idx, 1]).strip()
            if val.lower() == "employee name":
                return idx
        raise ValueError(
            "Could not find 'Employee Name' header in Monthly Roster sheet."
        )

    def _find_manager_row_indices(self, df: pd.DataFrame, header_idx: int) -> List[int]:
        """
        Rows just under the header where Position contains 'manager'
        or 'trainee', ending when we hit a blank line.
        """
        manager_rows: List[int] = []
        for idx in range(header_idx + 1, len(df)):
            name = str(df.iloc[idx, 1]).strip()
            position = str(df.iloc[idx, 2]).strip().lower()

            # End of management block
            if not name and not position:
                break

            if "manager" in position or "trainee" in position:
                manager_rows.append(idx)

        if not manager_rows:
            raise ValueError(
                "No management rows found in Monthly Roster "
                "(expected positions containing 'Manager' or 'Trainee')."
            )
        return manager_rows

    def _find_day_column_indices(
        self, header_row: pd.Series
    ) -> List[int]:
        """
        Columns whose header looks like 'Mon\\n25', 'Tue\\n26', etc.
        """
        day_cols: List[int] = []
        for col_idx, cell in enumerate(header_row):
            text = str(cell).strip()
            if "\n" not in text:
                continue
            day_name = text.split("\n", 1)[0].strip().lower()
            if day_name in _WEEKDAY_MAP:
                day_cols.append(col_idx)

        if not day_cols:
            raise ValueError(
                "Could not find any day columns (like 'Mon\\n25') "
                "in Monthly Roster."
            )
        return day_cols

    def _parse_date_range_start(self, df: pd.DataFrame) -> Tuple[date, int]:
        """
        Read 'Date Range: Nov 25 - Dec 31' + Created year to recover
        the true start date of the calendar in the Monthly Roster.
        """
        date_range_row_idx = None
        created_row_idx = None

        for idx in range(len(df)):
            left0 = str(df.iloc[idx, 0]).strip().lower()
            left3 = str(df.iloc[idx, 3]).strip().lower()
            if left3 == "date range:":
                date_range_row_idx = idx
            if left0 == "created:":
                created_row_idx = idx

        if date_range_row_idx is None:
            raise ValueError("Could not find 'Date Range:' row in Monthly Roster.")
        if created_row_idx is None:
            raise ValueError("Could not find 'Created:' row in Monthly Roster.")

        date_range_text = str(df.iloc[date_range_row_idx, 4]).strip()
        created_text = str(df.iloc[created_row_idx, 1]).strip()

        try:
            created_dt = datetime.fromisoformat(created_text)
            year = created_dt.year
        except Exception:
            # Fallback: try to parse the roster period year from 'December 2024'
            period_text = str(df.iloc[date_range_row_idx - 0, 1]).strip()
            # e.g. 'December 2024'
            parts = period_text.split()
            if parts and parts[-1].isdigit():
                year = int(parts[-1])
            else:
                raise ValueError(
                    f"Could not determine year from Monthly Roster: "
                    f"Created={created_text!r}, Period={period_text!r}"
                )

        # Example: 'Nov 25 - Dec 31'
        m = re.match(
            r"([A-Za-z]{3})\s+(\d{1,2})\s*-\s*([A-Za-z]{3})\s+(\d{1,2})",
            date_range_text,
        )
        if not m:
            raise ValueError(f"Unexpected Date Range format: {date_range_text!r}")

        start_mon_str, start_day_str, _, _ = m.groups()
        start_month = datetime.strptime(start_mon_str, "%b").month
        start_day = int(start_day_str)

        start_dt = date(year, start_month, start_day)
        return start_dt, year

    def _build_column_date_map(
        self, df: pd.DataFrame, header_idx: int, day_cols: List[int]
    ) -> Dict[int, date]:
        """
        Map each day column index to an actual date, using the
        Date Range start and stepping forward one day per column.
        """
        start_dt, _ = self._parse_date_range_start(df)
        day_cols_sorted = sorted(day_cols)

        col_to_date: Dict[int, date] = {}
        for offset, col_idx in enumerate(day_cols_sorted):
            col_to_date[col_idx] = start_dt + timedelta(days=offset)
        return col_to_date

    # ---------- PUBLIC: MANAGER HEADCOUNT TEMPLATE ----------

    def load_manager_template(self) -> Dict[int, int]:
        """
        Compute an average manager count per weekday (0=Mon..6=Sun) from
        the Monthly Roster management block.
        """
        df = self._load_monthly_roster()
        header_idx = self._find_header_row_index(df)
        header_row = df.iloc[header_idx]

        manager_row_indices = self._find_manager_row_indices(df, header_idx)
        day_cols = self._find_day_column_indices(header_row)

        # For each day column, count how many managers are scheduled.
        counts_by_weekday: Dict[int, List[int]] = {}

        for col_idx in day_cols:
            header_text = str(header_row.iloc[col_idx]).strip()
            day_name = header_text.split("\n", 1)[0].strip().lower()
            weekday = _WEEKDAY_MAP[day_name]

            col_count = 0
            for row_idx in manager_row_indices:
                cell = df.iloc[row_idx, col_idx]

                if isinstance(cell, str):
                    v = cell.strip()
                    # Treat anything non-empty and not "/" as a worked shift.
                    if v and v not in ("/", "off", "OFF"):
                        col_count += 1
                else:
                    # Non-string but not NaN => also treat as "working".
                    if pd.notna(cell):
                        col_count += 1

            counts_by_weekday.setdefault(weekday, []).append(col_count)

        manager_template_by_weekday: Dict[int, int] = {}
        for weekday, counts in counts_by_weekday.items():
            if not counts:
                continue
            avg = sum(counts) / float(len(counts))
            manager_template_by_weekday[weekday] = int(round(avg))

        return manager_template_by_weekday

    # ---------- PUBLIC: BUILD MANAGER EMPLOYEES + AVAILABILITY ----------

    def load_manager_employees_for_window(
        self,
        start_date: date,
        end_date: date,
    ) -> Tuple[Dict[str, Employee], Dict[str, EmployeeAvailability]]:
        """
        Create *real* manager employees and availability from the Monthly Roster.

        - Each manager row becomes an Employee with contract_type FULL_TIME
          and skill_tags = [SkillTag.MANAGER].
        - For each day column within [start_date, end_date], if the cell
          has a shift code (S, 1F, 2F, 3F, SC, M, etc.), we treat that as
          allowed availability for that date/code.

        Returns:
            (employees, availability) dictionaries which can be merged
            into SystemContext.
        """
        df = self._load_monthly_roster()
        header_idx = self._find_header_row_index(df)
        header_row = df.iloc[header_idx]

        manager_row_indices = self._find_manager_row_indices(df, header_idx)
        day_cols = self._find_day_column_indices(header_row)
        col_to_date = self._build_column_date_map(df, header_idx, day_cols)

        employees: Dict[str, Employee] = {}
        availability: Dict[str, EmployeeAvailability] = {}

        # Helper: normalise names for IDs
        def make_emp_id(name: str) -> str:
            base = name.strip().lower().replace(" ", "_")
            return f"mgr_{base}"

        for row_idx in manager_row_indices:
            raw_name = str(df.iloc[row_idx, 1]).strip()
            position = str(df.iloc[row_idx, 2]).strip()

            if not raw_name:
                continue

            emp_id = make_emp_id(raw_name)
            if emp_id not in employees:
                employees[emp_id] = Employee(
                    id=emp_id,
                    name=raw_name,
                    contract_type=ContractType.FULL_TIME,
                    skill_tags=[SkillTag.MANAGER],
                )

            emp_avail = availability.get(emp_id)
            if emp_avail is None:
                emp_avail = EmployeeAvailability(employee_id=emp_id)
                availability[emp_id] = emp_avail

            # Walk all day columns, restrict to planning window.
            for col_idx in sorted(day_cols):
                dt = col_to_date[col_idx]
                if dt < start_date or dt > end_date:
                    continue

                cell = df.iloc[row_idx, col_idx]
                if pd.isna(cell):
                    continue

                v = str(cell).strip()
                if not v:
                    continue

                # "/" or OFF-ish => not working, so not available.
                lowered = v.lower()
                if lowered in ("/", "off", "na", "n/a"):
                    continue

                # Everything else we treat as a valid shift code, e.g. S, 1F, 2F, 3F, SC, M
                shift_code = v
                allowed = emp_avail.allowed_shift_codes_by_date.setdefault(dt, [])
                if shift_code not in allowed:
                    allowed.append(shift_code)

        return employees, availability

    # ---------- OPTIONAL LEGACY TAGGING HELPERS (unused now) ----------

    def tag_managers_from_monthly_roster(self, ctx: SystemContext) -> int:
        """
        OLD behaviour (kept for backwards compatibility but *not* used now):

        Tries to tag existing employees in ctx.employees as managers if their
        names appear in the Monthly Roster management block.

        This only works if your availability sheet already contains the
        same management names, which is *not* the case in this challenge.
        """
        df = self._load_monthly_roster()
        header_idx = self._find_header_row_index(df)

        manager_names = set()
        for idx in self._find_manager_row_indices(df, header_idx):
            name = str(df.iloc[idx, 1]).strip()
            if name:
                manager_names.add(name.lower())

        if not manager_names:
            return 0

        tagged = 0
        for emp in ctx.employees.values():
            if emp.name.strip().lower() in manager_names:
                if SkillTag.MANAGER not in emp.skill_tags:
                    emp.skill_tags.append(SkillTag.MANAGER)
                    tagged += 1

        return tagged

    def tag_manager_employees(self, ctx: SystemContext) -> int:
        """
        Deprecated alias kept to avoid surprising callers.
        """
        return self.tag_managers_from_monthly_roster(ctx)
