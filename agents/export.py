from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import pandas as pd

from core.models import SystemContext, Roster


@dataclass
class ExportAgent:
    """
    Exports the final roster to CSV/Excel so managers can inspect it.

    For now we export a simple row-per-shift format:
    - Date
    - Employee ID
    - Employee Name
    - Contract Type
    - Shift Code
    - Store ID
    """

    output_dir: Path

    def export_roster(self, ctx: SystemContext, roster: Roster) -> Path:
        rows: List[dict] = []

        for a in roster.assignments:
            emp = ctx.employees.get(a.employee_id)
            if emp is None:
                continue

            rows.append(
                {
                    "date": a.date.isoformat(),
                    "store_id": a.store_id,
                    "employee_id": emp.id,
                    "employee_name": emp.name,
                    "contract_type": emp.contract_type.value,
                    "shift_code": a.shift_code,
                    "station": a.station.value if a.station is not None else "",
                }
            )

        df = pd.DataFrame(rows).sort_values(["date", "employee_id"]).reset_index(
            drop=True
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.output_dir / "roster_store1_2weeks.csv"
        df.to_csv(out_path, index=False)

        return out_path
