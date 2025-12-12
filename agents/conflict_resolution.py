from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Tuple, Set

from core.models import (
    SystemContext,
    Roster,
    ShiftAssignment,
    ContractType,
)
from agents.compliance import SHIFT_HOURS, DEFAULT_SHIFT_HOURS, CONTRACT_HOURS_BOUNDS


@dataclass
class ConflictResolutionAgent:
    """
    Attempts to fix over-hours by rebalancing shifts:

    - Each iteration:
        * Compute hours per employee from current assignments.
        * Find employees whose hours exceed their contract max.
        * For each overloaded employee, try to move one shift at a time to
          an under-loaded, available employee.
    - Stops when:
        * No overloaded employees remain, or
        * No progress is possible, or
        * max_iterations is reached.
    """

    max_iterations: int = 20  # safety limit

    def rebalance_hours(
        self,
        ctx: SystemContext,
        roster: Roster,
    ) -> Tuple[Roster, List[str]]:
        logs: List[str] = []
        assignments = list(roster.assignments)

        for iteration in range(self.max_iterations):
            hours_by_emp = self._compute_hours_by_employee(assignments)

            # Find overloaded employees based on *current* hours
            overloaded_ids: List[str] = []
            for emp_id, emp in ctx.employees.items():
                bounds = CONTRACT_HOURS_BOUNDS.get(emp.contract_type)
                if not bounds:
                    continue
                _, max_h = bounds
                if hours_by_emp.get(emp_id, 0.0) > max_h:
                    overloaded_ids.append(emp_id)

            if not overloaded_ids:
                logs.append(
                    f"Iteration {iteration}: no overloaded employees remaining."
                )
                break

            progress_made = False

            for overloaded_id in overloaded_ids:
                emp = ctx.employees[overloaded_id]
                _, max_h = CONTRACT_HOURS_BOUNDS[emp.contract_type]
                current_h = hours_by_emp.get(overloaded_id, 0.0)

                # Re-check in case this employee was fixed earlier in this iteration
                if current_h <= max_h:
                    continue

                # Look at this employee's shifts, latest dates first
                emp_assignments = [
                    a for a in assignments if a.employee_id == overloaded_id
                ]
                emp_assignments.sort(key=lambda a: a.date, reverse=True)

                for a in emp_assignments:
                    shift_hours = SHIFT_HOURS.get(a.shift_code, DEFAULT_SHIFT_HOURS)

                    candidate_id = self._find_replacement_employee(
                        ctx=ctx,
                        assignments=assignments,
                        hours_by_emp=hours_by_emp,
                        date_=a.date,
                        shift_code=a.shift_code,
                        shift_hours=shift_hours,
                        exclude_emp_id=overloaded_id,
                    )
                    if candidate_id is None:
                        continue

                    # Apply the reassignment
                    logs.append(
                        f"Reassigning {a.shift_code} on {a.date} "
                        f"from {overloaded_id} to {candidate_id}."
                    )

                    # Update hours in local map (keeps decisions consistent in this iteration)
                    hours_by_emp[overloaded_id] -= shift_hours
                    hours_by_emp[candidate_id] = hours_by_emp.get(
                        candidate_id, 0.0
                    ) + shift_hours

                    a.employee_id = candidate_id
                    progress_made = True

                    # Stop adjusting this overloaded employee for now;
                    # we re-evaluate the set of overloaded employees in the next iteration.
                    break

            if not progress_made:
                logs.append(
                    f"Iteration {iteration}: no feasible reassignments found; stopping."
                )
                break

        new_roster = Roster(
            store_id=roster.store_id,
            start_date=roster.start_date,
            end_date=roster.end_date,
            assignments=assignments,
        )
        return new_roster, logs

    @staticmethod
    def _compute_hours_by_employee(
        assignments: List[ShiftAssignment],
    ) -> Dict[str, float]:
        hours_by_emp: Dict[str, float] = {}
        for a in assignments:
            h = SHIFT_HOURS.get(a.shift_code, DEFAULT_SHIFT_HOURS)
            hours_by_emp[a.employee_id] = hours_by_emp.get(a.employee_id, 0.0) + h
        return hours_by_emp

    def _find_replacement_employee(
        self,
        ctx: SystemContext,
        assignments: List[ShiftAssignment],
        hours_by_emp: Dict[str, float],
        date_: date,
        shift_code: str,
        shift_hours: float,
        exclude_emp_id: str,
    ) -> str | None:
        """
        Find an employee who:
        - is not the overloaded employee
        - is available for (date_, shift_code)
        - has no other assignment on date_
        - has room under their contract max hours

        Prefer employees currently under their minimum hours.
        """
        # date -> set(emp_id already assigned that day)
        assigned_on_date: Set[str] = {
            a.employee_id for a in assignments if a.date == date_
        }

        # emp_id -> date -> set(shift_codes)
        availability_map: Dict[str, Dict[date, set]] = {}
        for emp_id, avail in ctx.availability.items():
            availability_map.setdefault(emp_id, {})
            for dt, codes in avail.allowed_shift_codes_by_date.items():
                availability_map[emp_id][dt] = set(codes)

        under_min: List[str] = []
        normal: List[str] = []

        for emp_id, emp in ctx.employees.items():
            if emp_id == exclude_emp_id:
                continue

            if shift_code not in availability_map.get(emp_id, {}).get(date_, set()):
                continue

            if emp_id in assigned_on_date:
                continue

            bounds = CONTRACT_HOURS_BOUNDS.get(emp.contract_type)
            if not bounds:
                continue

            min_h, max_h = bounds
            current_h = hours_by_emp.get(emp_id, 0.0)
            if current_h + shift_hours > max_h:
                continue

            if current_h < min_h:
                under_min.append(emp_id)
            else:
                normal.append(emp_id)

        if under_min:
            return under_min[0]
        if normal:
            return normal[0]
        return None
