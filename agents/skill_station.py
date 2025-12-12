from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Set

from core.models import SystemContext, Roster, ShiftAssignment, SkillTag


@dataclass
class SkillStationAgent:
    """
    Assigns a station (kitchen/counter/mccafe/dessert/delivery)
    to each shift assignment, trying to match:

    - Store demand per day per station
    - Employee skill_tags

    Simple heuristic:
    - For each day:
        * For each station, figure out how many people needed.
        * Prefer employees who have that station in their skills.
        * If not enough specialists, fill with anyone.
    """

    def assign_stations(self, ctx: SystemContext, roster: Roster) -> Roster:
        # Group assignments by date
        assignments_by_date: Dict[date, List[ShiftAssignment]] = {}
        for a in roster.assignments:
            assignments_by_date.setdefault(a.date, []).append(a)

        # Build quick employee -> skills map
        skills_by_emp: Dict[str, List[SkillTag]] = {
            emp_id: emp.skill_tags for emp_id, emp in ctx.employees.items()
        }

        # For each day, assign stations greedily
        for dt, day_assignments in assignments_by_date.items():
            demand = ctx.demand_by_date.get(dt, {})

            # Copy demand counts so we can decrement as we assign
            remaining_demand: Dict[SkillTag, int] = dict(demand)

            # First pass: assign specialists to their matching station
            unassigned: List[ShiftAssignment] = []
            for a in day_assignments:
                emp_skills = skills_by_emp.get(a.employee_id, [])
                # Find a station from demand that matches one of the employee's skills
                chosen_station = None
                for s in emp_skills:
                    if remaining_demand.get(s, 0) > 0:
                        chosen_station = s
                        break

                if chosen_station is not None:
                    a.station = chosen_station
                    remaining_demand[chosen_station] -= 1
                else:
                    # We'll try to place these in a second pass
                    unassigned.append(a)

            # Second pass: assign remaining people to any station that still needs staff
            for a in unassigned:
                chosen_station = None
                for s, count in remaining_demand.items():
                    if count > 0:
                        chosen_station = s
                        break

                if chosen_station is not None:
                    a.station = chosen_station
                    remaining_demand[chosen_station] -= 1
                else:
                    # No remaining demand; just leave station as None for now
                    a.station = a.station or None

        # Return the same roster (mutated), wrapped in a new instance for clarity
        return Roster(
            store_id=roster.store_id,
            start_date=roster.start_date,
            end_date=roster.end_date,
            assignments=roster.assignments,
        )
