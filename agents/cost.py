from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from core.models import SystemContext, Roster, ContractType
from agents.compliance import SHIFT_TEMPLATES, DEFAULT_SHIFT_HOURS


# Approximate base hourly rates (AUD) by contract type.
# These are illustrative, not exact Fair Work rates, but good enough to
# demonstrate cost-awareness in the multi-agent system.
BASE_HOURLY_RATES: Dict[ContractType, float] = {
    ContractType.FULL_TIME: 26.0,   # e.g. crew on a full-time contract
    ContractType.PART_TIME: 28.0,   # slightly higher for part-time
    ContractType.CASUAL: 32.0,      # casual loading
}

# Simple penalty loadings for weekends and late-night components.
WEEKEND_LOADING = 1.25   # +25% on Sat/Sun
LATE_NIGHT_LOADING = 1.15  # +15% if shift ends at/after 22:00


@dataclass
class CostEstimatorAgent:
    """
    Estimates total labour cost for a roster using:

    - Contract type (full_time / part_time / casual) -> base hourly rate
    - Shift templates -> hours per shift + end time
    - Weekend loading (Sat/Sun)
    - Late-night loading (shifts ending at/after 22:00)

    The goal is not to be legally exact, but to show that the MAS is
    cost-aware and can trade off coverage vs labour spend.
    """

    def estimate_cost(self, ctx: SystemContext, roster: Roster) -> float:
        total_cost = 0.0

        for a in roster.assignments:
            emp = ctx.employees.get(a.employee_id)
            if emp is None:
                continue

            tpl = SHIFT_TEMPLATES.get(a.shift_code)
            if tpl is not None:
                hours = float(tpl["hours"])
                end_time = tpl["end"]
            else:
                hours = DEFAULT_SHIFT_HOURS
                end_time = None

            base_rate = BASE_HOURLY_RATES.get(
                emp.contract_type,
                BASE_HOURLY_RATES[ContractType.CASUAL],  # conservative default
            )

            multiplier = 1.0

            # Weekend loading (Saturday/Sunday)
            if a.date.weekday() >= 5:  # 5 = Sat, 6 = Sun
                multiplier *= WEEKEND_LOADING

            # Late-night loading if shift runs to 22:00 or later
            if end_time is not None and end_time.hour >= 22:
                multiplier *= LATE_NIGHT_LOADING

            total_cost += hours * base_rate * multiplier

        return total_cost
