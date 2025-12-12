from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Dict, List, Tuple, Set

from ortools.sat.python import cp_model

from core.models import (
    SystemContext,
    Roster,
    ShiftAssignment,
    SkillTag,
)
from agents.compliance import (
    SHIFT_TEMPLATES,
    CONTRACT_HOURS_BOUNDS,
    MIN_REST_HOURS_BETWEEN_DAYS,
    WEEKLY_CONTRACT_HOURS_BOUNDS,
)

# We need integer coefficients for CP-SAT.
# Use units of 0.5 hours so we can represent 8.5h, 9h, etc. exactly.
HOURS_SCALE = 2  # 1 unit = 0.5h


def _rest_hours_between_codes(code_prev: str, code_next: str) -> float:
    """
    Compute rest hours between the *end* of code_prev on day D
    and the *start* of code_next on day D+1, using SHIFT_TEMPLATES.
    """
    tpl_prev = SHIFT_TEMPLATES.get(code_prev)
    tpl_next = SHIFT_TEMPLATES.get(code_next)
    if not tpl_prev or not tpl_next:
        # If we don't know one of the codes, assume it's safe.
        return 24.0

    day1 = date(2000, 1, 1)
    day2 = day1 + timedelta(days=1)

    prev_end_dt = datetime.combine(day1, tpl_prev["end"])
    next_start_dt = datetime.combine(day2, tpl_next["start"])
    delta = next_start_dt - prev_end_dt
    return delta.total_seconds() / 3600.0

def _covers_lunch_window(shift_code: str) -> bool:
    """
    True if this shift overlaps the lunch window (11:00–14:00).
    """
    tpl = SHIFT_TEMPLATES.get(shift_code)
    if not tpl:
        return False
    start = tpl["start"]
    end = tpl["end"]
    lunch_start = time(11, 0)
    lunch_end = time(14, 0)
    return (start <= lunch_end) and (end >= lunch_start)


def _covers_dinner_window(shift_code: str) -> bool:
    """
    True if this shift overlaps the dinner window (17:00–21:00).
    """
    tpl = SHIFT_TEMPLATES.get(shift_code)
    if not tpl:
        return False
    start = tpl["start"]
    end = tpl["end"]
    dinner_start = time(17, 0)
    dinner_end = time(21, 0)
    return (start <= dinner_end) and (end >= dinner_start)


# Identify which shift codes act as "opening" and "closing" for managers.
OPENING_SHIFT_CODES: Set[str] = set()
CLOSING_SHIFT_CODES: Set[str] = set()
for code, tpl in SHIFT_TEMPLATES.items():
    start = tpl["start"]
    end = tpl["end"]
    # Opening: starts at or before 07:00 (e.g. S, 1F)
    if start.hour < 7 or (start.hour == 7 and start.minute == 0):
        OPENING_SHIFT_CODES.add(code)
    # Closing: ends at or after 22:00 (e.g. 2F)
    if end.hour > 22 or (end.hour == 22 and end.minute == 0):
        CLOSING_SHIFT_CODES.add(code)


@dataclass
class CandidateGeneratorAgent:
    """
    Generates an initial roster using a CP-SAT model.

    Variables:
        x[e, d, s] in {0,1}  employee e works shift s on date d

    Hard constraints:
        - At most one shift per employee per day
        - Only assign shifts allowed by availability
        - Total hours per employee <= contract max over 2-week horizon
        - Weekly hours above Fair Work band show up as "overtime" vars
        - 10h minimum rest between consecutive days' shifts

    Demand:
        - For each date, total_assigned[d] + under_coverage[d] >= demand[d]
        - We minimise sum_d under_coverage[d].

    Manager coverage:
        - Managers are part of the pool like crew.
        - We add soft penalties for:
            * days with NO manager scheduled
            * days with NO manager on opening (when possible)
            * days with NO manager on closing (when possible)
          This nudges the solver to staff managers sensibly without making
          the problem infeasible.
    """

    def generate_initial_roster(
        self,
        ctx: SystemContext,
        store_id: str,
        start_date: date,
        end_date: date,
    ) -> Roster:
        employees = list(ctx.employees.values())
        if not employees:
            raise ValueError("No employees found in context.")

        # Collect the dates in the planning window
        dates: List[date] = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)

        # Map each date to a week index (0,1,2,...) from the planning window start
        week_index_by_date: Dict[date, int] = {
            dt: (dt - start_date).days // 7 for dt in dates
        }

        # Helper: emp_id -> date -> set(allowed shift codes)
        availability_map: Dict[str, Dict[date, Set[str]]] = {}
        for emp_id, avail in ctx.availability.items():
            inner: Dict[date, Set[str]] = {}
            for dt, codes in avail.allowed_shift_codes_by_date.items():
                inner[dt] = set(codes)
            availability_map[emp_id] = inner

        # Collect all shift codes that actually appear in availability
        all_shift_codes: List[str] = []
        for emp_id, by_date in availability_map.items():
            for dt, codes in by_date.items():
                for code in codes:
                    if code not in all_shift_codes:
                        all_shift_codes.append(code)

        if not all_shift_codes:
            raise ValueError("No shift codes discovered from availability.")

        # Indexing helpers
        emp_index: Dict[str, int] = {emp.id: idx for idx, emp in enumerate(employees)}
        date_index: Dict[date, int] = {dt: idx for idx, dt in enumerate(dates)}
        shift_index: Dict[str, int] = {code: idx for idx, code in enumerate(all_shift_codes)}

        model = cp_model.CpModel()

        # Decision variables: x[e, d, s] = 1 if employee e works shift s on date d
        x: Dict[Tuple[int, int, int], cp_model.IntVar] = {}

        # Cache allowed shift codes per (emp_id, date) for later constraints
        allowed_shift_codes: Dict[Tuple[str, date], List[str]] = {}

        for emp in employees:
            for dt in dates:
                allowed_codes = sorted(availability_map.get(emp.id, {}).get(dt, set()))
                if not allowed_codes:
                    continue

                allowed_shift_codes[(emp.id, dt)] = allowed_codes

                e_idx = emp_index[emp.id]
                d_idx = date_index[dt]
                for code in allowed_codes:
                    s_idx = shift_index[code]
                    x[(e_idx, d_idx, s_idx)] = model.NewBoolVar(
                        f"x_e{e_idx}_d{d_idx}_s{s_idx}"
                    )

        # 1) At most one shift per employee per day
        for emp in employees:
            e_idx = emp_index[emp.id]
            for dt in dates:
                d_idx = date_index[dt]
                codes = allowed_shift_codes.get((emp.id, dt), [])
                if not codes:
                    continue
                vars_for_emp_day: List[cp_model.IntVar] = []
                for code in codes:
                    s_idx = shift_index[code]
                    var = x.get((e_idx, d_idx, s_idx))
                    if var is not None:
                        vars_for_emp_day.append(var)
                if vars_for_emp_day:
                    model.Add(sum(vars_for_emp_day) <= 1)

        # 2) Contract MAX hours per employee over the 2-week horizon
        for emp in employees:
            bounds = CONTRACT_HOURS_BOUNDS.get(emp.contract_type)
            if not bounds:
                continue
            _, max_hours = bounds
            max_units = int(round(max_hours * HOURS_SCALE))

            e_idx = emp_index[emp.id]
            terms: List[cp_model.LinearExpr] = []

            for dt in dates:
                d_idx = date_index[dt]
                codes = allowed_shift_codes.get((emp.id, dt), [])
                for code in codes:
                    tpl = SHIFT_TEMPLATES.get(code)
                    if tpl is None:
                        continue
                    hours = float(tpl["hours"])
                    units = int(round(hours * HOURS_SCALE))
                    var = x.get((e_idx, d_idx, shift_index[code]))
                    if var is not None:
                        terms.append(units * var)

            if terms:
                model.Add(sum(terms) <= max_units)

        # 2b) Weekly MAX hours per employee (with overtime slack)
        weekly_overtime: Dict[Tuple[int, int], cp_model.IntVar] = {}
        for emp in employees:
            bounds_week = WEEKLY_CONTRACT_HOURS_BOUNDS.get(emp.contract_type)
            if not bounds_week:
                continue

            _, max_week_hours = bounds_week
            max_week_units = int(round(max_week_hours * HOURS_SCALE))
            e_idx = emp_index[emp.id]

            # We support any horizon length; weeks are 7-day chunks from start_date.
            week_indices: Set[int] = set(week_index_by_date[dt] for dt in dates)

            for w_idx in week_indices:
                week_terms: List[cp_model.LinearExpr] = []

                for dt in dates:
                    if week_index_by_date[dt] != w_idx:
                        continue

                    d_idx = date_index[dt]
                    codes = allowed_shift_codes.get((emp.id, dt), [])
                    for code in codes:
                        tpl = SHIFT_TEMPLATES.get(code)
                        if tpl is None:
                            continue
                        hours = float(tpl["hours"])
                        units = int(round(hours * HOURS_SCALE))
                        var = x.get((e_idx, d_idx, shift_index[code]))
                        if var is not None:
                            week_terms.append(units * var)

                if week_terms:
                    # overtime_units represent how much we exceed the weekly cap (in 0.5h units)
                    ot = model.NewIntVar(
                        0,
                        max_week_units * 2,  # generous upper bound, rarely used
                        f"ot_e{e_idx}_w{w_idx}",
                    )
                    weekly_overtime[(e_idx, w_idx)] = ot

                    # total_week_units <= max_week_units + overtime_units
                    model.Add(sum(week_terms) <= max_week_units + ot)

        # 3) 10-hour minimum rest between consecutive days
        for emp in employees:
            e_idx = emp_index[emp.id]
            for i, day in enumerate(dates[:-1]):  # all but last
                next_day = dates[i + 1]
                today_codes = allowed_shift_codes.get((emp.id, day), [])
                next_codes = allowed_shift_codes.get((emp.id, next_day), [])
                if not today_codes or not next_codes:
                    continue

                d_idx = date_index[day]
                nd_idx = date_index[next_day]

                for code_today in today_codes:
                    for code_next in next_codes:
                        rest_h = _rest_hours_between_codes(code_today, code_next)
                        if rest_h < MIN_REST_HOURS_BETWEEN_DAYS:
                            var_today = x.get(
                                (e_idx, d_idx, shift_index[code_today])
                            )
                            var_next = x.get(
                                (e_idx, nd_idx, shift_index[code_next])
                            )
                            if var_today is None or var_next is None:
                                continue
                            # Can't choose an illegal rest pattern
                            model.Add(var_today + var_next <= 1)

        # 4) Demand & under-coverage per day (crew + managers together)
        under_coverage: Dict[int, cp_model.IntVar] = {}
        for dt in dates:
            d_idx = date_index[dt]
            demand_today = int(sum(ctx.demand_by_date.get(dt, {}).values()))

            uc = model.NewIntVar(0, len(employees), f"under_cov_d{d_idx}")
            under_coverage[d_idx] = uc

            # total assigned that day
            day_vars: List[cp_model.IntVar] = []
            for emp in employees:
                e_idx = emp_index[emp.id]
                codes = allowed_shift_codes.get((emp.id, dt), [])
                for code in codes:
                    s_idx = shift_index[code]
                    var = x.get((e_idx, d_idx, s_idx))
                    if var is not None:
                        day_vars.append(var)

            if day_vars and demand_today > 0:
                model.Add(sum(day_vars) + uc >= demand_today)
            else:
                model.Add(uc == 0)

        # 5) Soft manager coverage penalties
        manager_ids = [emp.id for emp in employees if SkillTag.MANAGER in emp.skill_tags]
        manager_absence_penalties: List[cp_model.IntVar] = []
        opening_manager_absence_penalties: List[cp_model.IntVar] = []
        closing_manager_absence_penalties: List[cp_model.IntVar] = []

        if manager_ids:
            for dt in dates:
                d_idx = date_index[dt]

                manager_day_vars: List[cp_model.IntVar] = []
                opening_mgr_day_vars: List[cp_model.IntVar] = []
                closing_mgr_day_vars: List[cp_model.IntVar] = []

                for emp in employees:
                    if emp.id not in manager_ids:
                        continue

                    e_idx = emp_index[emp.id]
                    codes = allowed_shift_codes.get((emp.id, dt), [])
                    for code in codes:
                        s_idx = shift_index[code]
                        var = x.get((e_idx, d_idx, s_idx))
                        if var is None:
                            continue

                        manager_day_vars.append(var)
                        if code in OPENING_SHIFT_CODES:
                            opening_mgr_day_vars.append(var)
                        if code in CLOSING_SHIFT_CODES:
                            closing_mgr_day_vars.append(var)

                # If no manager is actually available at all on this day,
                # don't penalise the solver for something impossible.
                if not manager_day_vars:
                    continue

                # Penalty: no manager at all on this day
                no_mgr = model.NewBoolVar(f"no_mgr_d{d_idx}")
                manager_absence_penalties.append(no_mgr)
                model.Add(sum(manager_day_vars) == 0).OnlyEnforceIf(no_mgr)
                model.Add(sum(manager_day_vars) >= 1).OnlyEnforceIf(no_mgr.Not())

                # Penalty: no manager covering opening on this day,
                # but only if we *could* have one (availability-wise).
                if opening_mgr_day_vars:
                    no_mgr_open = model.NewBoolVar(f"no_mgr_open_d{d_idx}")
                    opening_manager_absence_penalties.append(no_mgr_open)
                    model.Add(sum(opening_mgr_day_vars) == 0).OnlyEnforceIf(no_mgr_open)
                    model.Add(sum(opening_mgr_day_vars) >= 1).OnlyEnforceIf(
                        no_mgr_open.Not()
                    )

                # Penalty: no manager covering closing on this day,
                # but only if we *could* have one.
                if closing_mgr_day_vars:
                    no_mgr_close = model.NewBoolVar(f"no_mgr_close_d{d_idx}")
                    closing_manager_absence_penalties.append(no_mgr_close)
                    model.Add(sum(closing_mgr_day_vars) == 0).OnlyEnforceIf(no_mgr_close)
                    model.Add(sum(closing_mgr_day_vars) >= 1).OnlyEnforceIf(
                        no_mgr_close.Not()
                    )
        
        # 6) Soft preference: try to have 2 managers in lunch & dinner peaks
        peak_two_gap_terms: List[cp_model.IntVar] = []

        if manager_ids:
            for dt in dates:
                d_idx = date_index[dt]

                lunch_mgr_vars: List[cp_model.IntVar] = []
                dinner_mgr_vars: List[cp_model.IntVar] = []

                for emp in employees:
                    if emp.id not in manager_ids:
                        continue

                    e_idx = emp_index[emp.id]
                    codes = allowed_shift_codes.get((emp.id, dt), [])
                    for code in codes:
                        s_idx = shift_index[code]
                        var = x.get((e_idx, d_idx, s_idx))
                        if var is None:
                            continue

                        if _covers_lunch_window(code):
                            lunch_mgr_vars.append(var)
                        if _covers_dinner_window(code):
                            dinner_mgr_vars.append(var)

                # gap_lunch >= max(0, 2 - number_of_lunch_managers)
                if lunch_mgr_vars:
                    gap_lunch = model.NewIntVar(0, 2, f"gap_mgr_lunch_d{d_idx}")
                    model.Add(gap_lunch >= 2 - sum(lunch_mgr_vars))
                    peak_two_gap_terms.append(gap_lunch)

                # gap_dinner >= max(0, 2 - number_of_dinner_managers)
                if dinner_mgr_vars:
                    gap_dinner = model.NewIntVar(0, 2, f"gap_mgr_dinner_d{d_idx}")
                    model.Add(gap_dinner >= 2 - sum(dinner_mgr_vars))
                    peak_two_gap_terms.append(gap_dinner)

        total_peak_two_gap = sum(peak_two_gap_terms) if peak_two_gap_terms else 0

        # Objective: minimise total under-coverage first, then weekly overtime,
        # then days without any manager on duty, and finally missing opening/closing managers.
        total_under_cov = sum(under_coverage.values())
        total_overtime = sum(weekly_overtime.values()) if weekly_overtime else 0
        total_manager_absence = (
            sum(manager_absence_penalties) if manager_absence_penalties else 0
        )
        total_opening_absence = (
            sum(opening_manager_absence_penalties)
            if opening_manager_absence_penalties
            else 0
        )
        total_closing_absence = (
            sum(closing_manager_absence_penalties)
            if closing_manager_absence_penalties
            else 0
        )

        # 1000x weight on coverage so the solver strongly prefers filling demand.
        # Then overtime, then "no manager" days, then missing opening/closing managers.
        model.Minimize(
            total_under_cov * 1000
            + total_overtime
            + total_manager_absence * 100
            + total_opening_absence * 50
            + total_closing_absence * 50
            + total_peak_two_gap * 10
        )


        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 20.0  # keep it fast for now

        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(f"CP-SAT solver failed with status {status}")

        # Build roster from solution
        assignments: List[ShiftAssignment] = []
        for (e_idx, d_idx, s_idx), var in x.items():
            if solver.Value(var) == 1:
                emp = employees[e_idx]
                dt = dates[d_idx]
                shift_code = all_shift_codes[s_idx]
                assignments.append(
                    ShiftAssignment(
                        employee_id=emp.id,
                        date=dt,
                        shift_code=shift_code,
                        station=None,  # filled later by SkillStationAgent
                        store_id=store_id,
                    )
                )

        roster = Roster(
            store_id=store_id,
            start_date=start_date,
            end_date=end_date,
            assignments=assignments,
        )
        return roster
