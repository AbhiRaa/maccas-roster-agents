from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict

import pandas as pd

from core.models import SystemContext, SkillTag


@dataclass
class DemandCoverageAgent:
    """
    Builds a simple daily demand profile for the store using
    store_structure_staff_estimate.csv.

    For now, we approximate:
    - Weekdays  -> use 'Normal' staffing row
    - Weekends  -> use 'Peak' staffing row

    And we aggregate demand as "people per station per day"
    (later we can refine to hourly buckets).
    """
    raw_data_dir: Path

    def build_daily_demand(
        self,
        ctx: SystemContext,
        store_id: str,
        start_date: date,
        end_date: date,
    ) -> None:
        path = self.raw_data_dir / "store_structure_staff_estimate.csv"
        df = pd.read_csv(path)

        # Assume first three columns are:
        #  store_id, location_type, period_type
        # and the remaining numeric columns correspond to stations in a fixed order.
        if df.shape[1] < 5:
            raise ValueError(
                "Expected at least 5 columns in store_structure_staff_estimate.csv"
            )

        store_col = df.columns[0]
        period_col = df.columns[2]
        station_cols = list(df.columns[3:])  # numeric station counts

        # Define mapping order: we assume the CSV station columns match this order.
        station_order = [
            SkillTag.KITCHEN,
            SkillTag.COUNTER,
            SkillTag.MCCAFe,
            SkillTag.DESSERT,
            SkillTag.DELIVERY,
        ]

        if len(station_cols) < len(station_order):
            # If there are fewer columns than we expect, truncate the order list.
            station_order = station_order[: len(station_cols)]
        elif len(station_cols) > len(station_order):
            # If there are more columns, we'll ignore the extra ones for now.
            station_cols = station_cols[: len(station_order)]

        df_store = df[df[store_col].astype(str).str.strip().str.lower() ==
                      store_id.strip().lower()]

        if df_store.empty:
            raise ValueError(f"No rows found for store_id {store_id!r} in {path.name}")

        # Build mapping: period_type -> {station -> required_count}
        period_to_station_demand: Dict[str, Dict[SkillTag, int]] = {}
        for _, row in df_store.iterrows():
            period_raw = str(row[period_col]).strip().lower()  # "normal" or "peak" etc.
            station_demand: Dict[SkillTag, int] = {}
            for col_name, station in zip(station_cols, station_order):
                value = row[col_name]
                try:
                    count = int(value)
                except Exception:
                    count = 0
                station_demand[station] = count
            period_to_station_demand[period_raw] = station_demand

        # Now build demand_by_date for each day in the window
        current = start_date
        while current <= end_date:
            is_weekend = current.weekday() >= 5  # 5=Sat, 6=Sun
            if is_weekend:
                # Prefer "peak" if available, else fallback to any row
                key_candidates = ["peak", "weekend"]
            else:
                key_candidates = ["normal", "weekday"]

            chosen_demand = None
            for key in key_candidates:
                if key in period_to_station_demand:
                    chosen_demand = period_to_station_demand[key]
                    break

            # Fallback: if no matching period found, just pick any one row
            if chosen_demand is None and period_to_station_demand:
                chosen_demand = next(iter(period_to_station_demand.values()))

            if chosen_demand is None:
                # No demand info at all; default everything to zero
                chosen_demand = {s: 0 for s in station_order}

            ctx.demand_by_date[current] = dict(chosen_demand)
            current += timedelta(days=1)
