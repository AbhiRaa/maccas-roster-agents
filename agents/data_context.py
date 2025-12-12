from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List

import pandas as pd

from core.models import (
    Employee,
    EmployeeAvailability,
    ContractType,
    SkillTag,
    SystemContext,
)


def _parse_contract_type(raw: str) -> ContractType:
    s = (raw or "").strip().lower()
    if s.startswith("full"):
        return ContractType.FULL_TIME
    if s.startswith("part"):
        return ContractType.PART_TIME
    if s.startswith("casual"):
        return ContractType.CASUAL
    raise ValueError(f"Unexpected contract type value: {raw!r}")


def _parse_skill_tags(raw: str) -> List[SkillTag]:
    s = (raw or "").strip().lower()
    tags: List[SkillTag] = []

    if "kitchen" in s:
        tags.append(SkillTag.KITCHEN)
    if "counter" in s:
        tags.append(SkillTag.COUNTER)
    if "cafe" in s or "mccafe" in s:
        tags.append(SkillTag.MCCAFe)
    if "dessert" in s:
        tags.append(SkillTag.DESSERT)
    # You can add more rules later (delivery, etc.)

    # Fallback: if nothing matched, assume counter as a generic front-of-house
    if not tags:
        tags.append(SkillTag.COUNTER)

    return tags


def _parse_date_header(header: str, year: int) -> date:
    """
    Convert column header like 'Mon\\nDec 9' into a date object with given year.
    """
    text = header
    if "\n" in text:
        # e.g. "Mon\nDec 9" -> "Dec 9"
        text = text.split("\n", 1)[1]
    text = text.strip()  # "Dec 9"
    dt = datetime.strptime(f"{text} {year}", "%b %d %Y")
    return dt.date()


@dataclass
class DataContextAgent:
    """
    Loads raw challenge data (for now: employee availability) into SystemContext.
    """
    raw_data_dir: Path

    def load_context(
        self,
        store_id: str,
        start_date: date,
        end_date: date,
    ) -> SystemContext:
        employees, availability = self._load_employee_availability(start_date.year)

        # Later we can load store structure, shift templates, etc.
        ctx = SystemContext(
            employees=employees,
            availability=availability,
        )
        return ctx

    def _load_employee_availability(
        self,
        year: int,
    ) -> tuple[Dict[str, Employee], Dict[str, EmployeeAvailability]]:
        """
        Parse employee_availability_2weeks.xlsx into Employees and their availability.
        """
        path = self.raw_data_dir / "employee_availability_2weeks.xlsx"
        df = pd.ExcelFile(path).parse("Employee Availability")

        # Find the header row with "ID" in the first column
        first_col = df.iloc[:, 0]
        header_row_idx = df.index[first_col == "ID"][0]

        header_row = df.iloc[header_row_idx]
        data = df.iloc[header_row_idx + 1 :].copy()
        data.columns = header_row

        # Keep only real employees: ID not null and numeric
        data = data[data["ID"].notna()]
        data = data[data["ID"].astype(str).str.isdigit()]

        employees: Dict[str, Employee] = {}
        availability_by_emp: Dict[str, EmployeeAvailability] = {}

        # Identify date columns (everything except these core fields)
        base_cols = ["ID", "Employee Name", "Type", "Station"]
        date_columns = [c for c in data.columns if c not in base_cols]

        # Pre-parse headers to dates
        date_by_col: Dict[str, date] = {
            col: _parse_date_header(str(col), year) for col in date_columns
        }

        for _, row in data.iterrows():
            emp_id = str(row["ID"]).strip()
            name = str(row["Employee Name"]).strip()
            contract_raw = row["Type"]
            station_raw = row["Station"]

            contract_type = _parse_contract_type(contract_raw)
            skill_tags = _parse_skill_tags(station_raw)

            if emp_id not in employees:
                employees[emp_id] = Employee(
                    id=emp_id,
                    name=name,
                    contract_type=contract_type,
                    skill_tags=skill_tags,
                )

            emp_avail = EmployeeAvailability(employee_id=emp_id)

            for col in date_columns:
                cell = row[col]
                if pd.isna(cell):
                    continue
                value = str(cell).strip()
                if not value or value == "/":
                    # "/" is explicit "not available"
                    continue

                shift_code = value  # e.g. "1F", "2F", "3F"
                dt = date_by_col[col]
                allowed = emp_avail.allowed_shift_codes_by_date.setdefault(dt, [])
                if shift_code not in allowed:
                    allowed.append(shift_code)

            availability_by_emp[emp_id] = emp_avail

        return employees, availability_by_emp
