from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Dict, Set

from core.models import (
    SystemContext,
    Roster,
    RosterEvaluationMetrics,
    SkillTag,
)

from agents.compliance import SHIFT_TEMPLATES


def _covers_window(shift_code: str, window_start: time, window_end: time) -> bool:
    """
    Returns True if this shift overlaps the given [window_start, window_end]
    time window.
    """
    tpl = SHIFT_TEMPLATES.get(shift_code)
    if not tpl:
        return False

    start = tpl["start"]
    end = tpl["end"]

    # Simple interval overlap check
    return (start <= window_end) and (end >= window_start)


@dataclass
class CoverageEvaluatorAgent:
    """
    Evaluates how well the roster meets daily and peak-window demand.

    Definitions:
    - coverage_score:
        Overall daily coverage vs demand across the 2-week window.
    - peak_coverage_score:
        Combined coverage across lunch (11:00–14:00) and dinner (17:00–21:00)
        windows. We approximate required headcount in lunch/dinner as equal
        to the daily demand for that date.
    - fairness_score:
        Used here as a "weekend uplift ratio":
            weekend_avg_staff_per_day / weekday_avg_staff_per_day
    - manager_coverage_score:
        Fraction of days with at least one manager on duty.
    - manager_opening_coverage:
        Fraction of days where a manager covers the opening window.
    - manager_closing_coverage:
        Fraction of days where a manager covers the closing window.
    """

    def evaluate(
        self,
        ctx: SystemContext,
        roster: Roster,
        start_date: date,
        end_date: date,
    ) -> RosterEvaluationMetrics:
        # Windows
        lunch_start = time(11, 0)
        lunch_end = time(14, 0)
        dinner_start = time(17, 0)
        dinner_end = time(21, 0)

        # Approximate opening/closing windows
        opening_start = time(6, 30)
        opening_end = time(8, 0)
        closing_start = time(22, 0)
        closing_end = time(23, 0)

        assigned_by_date: Dict[date, Set[str]] = {}
        lunch_assigned_by_date: Dict[date, Set[str]] = {}
        dinner_assigned_by_date: Dict[date, Set[str]] = {}

        manager_by_date: Dict[date, Set[str]] = {}
        manager_opening_by_date: Dict[date, Set[str]] = {}
        manager_closing_by_date: Dict[date, Set[str]] = {}
        manager_lunch_by_date: Dict[date, Set[str]] = {}
        manager_dinner_by_date: Dict[date, Set[str]] = {}


        for a in roster.assignments:
            dt = a.date
            emp_id = a.employee_id
            shift_code = a.shift_code

            assigned_by_date.setdefault(dt, set()).add(emp_id)

            if _covers_window(shift_code, lunch_start, lunch_end):
                lunch_assigned_by_date.setdefault(dt, set()).add(emp_id)
            if _covers_window(shift_code, dinner_start, dinner_end):
                dinner_assigned_by_date.setdefault(dt, set()).add(emp_id)

            emp = ctx.employees.get(emp_id)
            if emp is None:
                continue

            if SkillTag.MANAGER in emp.skill_tags:
                manager_by_date.setdefault(dt, set()).add(emp_id)

                if _covers_window(shift_code, opening_start, opening_end):
                    manager_opening_by_date.setdefault(dt, set()).add(emp_id)
                if _covers_window(shift_code, closing_start, closing_end):
                    manager_closing_by_date.setdefault(dt, set()).add(emp_id)

                # Managers that cover lunch / dinner windows
                if _covers_window(shift_code, lunch_start, lunch_end):
                    manager_lunch_by_date.setdefault(dt, set()).add(emp_id)
                if _covers_window(shift_code, dinner_start, dinner_end):
                    manager_dinner_by_date.setdefault(dt, set()).add(emp_id)

        total_demand = 0.0
        total_shortfall = 0.0

        total_lunch_demand = 0.0
        total_lunch_shortfall = 0.0

        total_dinner_demand = 0.0
        total_dinner_shortfall = 0.0

        weekday_staff_total = 0.0
        weekday_days = 0
        weekend_staff_total = 0.0
        weekend_days = 0

        total_days = 0

        days_with_manager = 0
        days_with_manager_opening = 0
        days_with_manager_closing = 0

        total_peak_windows = 0
        peak_windows_with_two_managers = 0

        current = start_date
        while current <= end_date:
            total_days += 1

            demand_today = float(sum(ctx.demand_by_date.get(current, {}).values()))
            assigned_today_ids = assigned_by_date.get(current, set())
            assigned_today = float(len(assigned_today_ids))
            shortfall = max(demand_today - assigned_today, 0.0)

            total_demand += demand_today
            total_shortfall += shortfall

            # For lunch & dinner, approximate required headcount as daily demand.
            lunch_assigned = float(
                len(lunch_assigned_by_date.get(current, set()))
            )
            dinner_assigned = float(
                len(dinner_assigned_by_date.get(current, set()))
            )

            lunch_demand_today = demand_today
            dinner_demand_today = demand_today

            total_lunch_demand += lunch_demand_today
            total_dinner_demand += dinner_demand_today

            total_lunch_shortfall += max(lunch_demand_today - lunch_assigned, 0.0)
            total_dinner_shortfall += max(dinner_demand_today - dinner_assigned, 0.0)

            # Weekend uplift calculation (using total staff scheduled per day)
            if current.weekday() >= 5:  # 5=Sat, 6=Sun
                weekend_staff_total += assigned_today
                weekend_days += 1
            else:
                weekday_staff_total += assigned_today
                weekday_days += 1

            # Manager coverage stats
            mgrs_today = manager_by_date.get(current, set())
            if mgrs_today:
                days_with_manager += 1

            open_mgrs_today = manager_opening_by_date.get(current, set())
            if open_mgrs_today:
                days_with_manager_opening += 1

            close_mgrs_today = manager_closing_by_date.get(current, set())
            if close_mgrs_today:
                days_with_manager_closing += 1

            # 2-managers-in-peaks metric (lunch + dinner)
            lunch_mgrs = len(manager_lunch_by_date.get(current, set()))
            dinner_mgrs = len(manager_dinner_by_date.get(current, set()))

            # Only count windows on days with some demand (store open)
            if demand_today > 0:
                # Lunch window
                total_peak_windows += 1
                if lunch_mgrs >= 2:
                    peak_windows_with_two_managers += 1

                # Dinner window
                total_peak_windows += 1
                if dinner_mgrs >= 2:
                    peak_windows_with_two_managers += 1

            current += timedelta(days=1)

        metrics = RosterEvaluationMetrics()

        # Overall daily coverage
        if total_demand > 0:
            metrics.coverage_score = 1.0 - (total_shortfall / total_demand)

        # Peak-window (lunch + dinner) coverage
        total_peak_demand = total_lunch_demand + total_dinner_demand
        total_peak_shortfall = total_lunch_shortfall + total_dinner_shortfall
        if total_peak_demand > 0:
            metrics.peak_coverage_score = 1.0 - (
                total_peak_shortfall / total_peak_demand
            )

        # Weekend uplift ratio stored in fairness_score
        weekend_uplift = 0.0
        if weekday_days > 0 and weekend_days > 0:
            weekday_avg = weekday_staff_total / float(weekday_days)
            weekend_avg = weekend_staff_total / float(weekend_days)
            if weekday_avg > 0:
                weekend_uplift = weekend_avg / weekday_avg

        metrics.fairness_score = weekend_uplift

        # Manager coverage metrics
        if total_days > 0:
            metrics.manager_coverage_score = days_with_manager / float(total_days)
            metrics.manager_opening_coverage = (
                days_with_manager_opening / float(total_days)
            )
            metrics.manager_closing_coverage = (
                days_with_manager_closing / float(total_days)
            )

        # Fraction of lunch + dinner windows that had at least two managers
        if total_peak_windows > 0:
            metrics.manager_peak_two_coverage_score = (
                peak_windows_with_two_managers / float(total_peak_windows)
            )

        # labour_cost_estimate is filled elsewhere (or left as 0.0)
        return metrics
