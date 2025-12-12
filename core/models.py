from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional
from datetime import date, time


class ContractType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CASUAL = "casual"


class SkillTag(str, Enum):
    KITCHEN = "kitchen"
    COUNTER = "counter"
    MCCAFe = "mccafe"
    DESSERT = "dessert"
    DELIVERY = "delivery"
    MANAGER = "manager"


@dataclass
class Employee:
    id: str
    name: str
    contract_type: ContractType
    skill_tags: List[SkillTag] = field(default_factory=list)


@dataclass
class ShiftTemplate:
    code: str           # e.g. "1F", "2F", "3F", "S"
    start_time: time
    end_time: time
    hours: float
    description: str = ""  # e.g. "First half day", "Full day"


@dataclass
class EmployeeAvailability:
    employee_id: str
    # mapping: date -> list of allowed shift codes (e.g. ["1F", "2F"])
    allowed_shift_codes_by_date: Dict[date, List[str]] = field(default_factory=dict)


@dataclass
class ShiftAssignment:
    employee_id: str
    date: date
    shift_code: str
    station: Optional[SkillTag] = None  # station they work at
    store_id: str = "store_1"


@dataclass
class Roster:
    store_id: str
    start_date: date
    end_date: date
    assignments: List[ShiftAssignment] = field(default_factory=list)

    def assignments_for_employee(self, employee_id: str) -> List[ShiftAssignment]:
        return [a for a in self.assignments if a.employee_id == employee_id]


class ViolationSeverity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class Violation:
    severity: ViolationSeverity
    code: str  # e.g. "MAX_HOURS_EXCEEDED", "INSUFFICIENT_REST"
    message: str
    employee_id: Optional[str] = None
    date: Optional[date] = None
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class RosterEvaluationMetrics:
    coverage_score: float = 0.0
    peak_coverage_score: float = 0.0
    manager_coverage_score: float = 0.0
    labour_cost_estimate: float = 0.0
    fairness_score: float = 0.0
    # Management-specific coverage diagnostics
    manager_opening_coverage: float = 0.0
    manager_closing_coverage: float = 0.0
    # Fraction of lunch + dinner windows that had at least two managers
    manager_peak_two_coverage_score: float = 0.0


@dataclass
class SystemContext:
    """
    Holds core in-memory data for a single store + planning window.
    """
    employees: Dict[str, Employee] = field(default_factory=dict)
    availability: Dict[str, EmployeeAvailability] = field(default_factory=dict)
    # date -> mapping of station -> required headcount (simple daily demand for now)
    demand_by_date: Dict[date, Dict[SkillTag, int]] = field(default_factory=dict)
    # weekday (0=Mon .. 6=Sun) -> expected number of managers on duty,
    # learned from the management monthly roster template.
    manager_template_by_weekday: Dict[int, int] = field(default_factory=dict)
