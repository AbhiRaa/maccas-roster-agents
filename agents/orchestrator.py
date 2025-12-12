from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import List, Optional

from core.models import (
    Roster,
    RosterEvaluationMetrics,
    Violation,
    SystemContext,
    ViolationSeverity,
)

from agents.data_context import DataContextAgent
from agents.demand_coverage import DemandCoverageAgent
from agents.generator import CandidateGeneratorAgent
from agents.compliance import ComplianceAgent
from agents.coverage_eval import CoverageEvaluatorAgent
from agents.conflict_resolution import ConflictResolutionAgent
from agents.export import ExportAgent
from agents.skill_station import SkillStationAgent
from agents.explainer import ExplanationAgent
from agents.cost import CostEstimatorAgent
from agents.management_template import ManagementTemplateAgent


@dataclass
class OrchestratorResult:
    roster: Roster
    violations: List[Violation]
    metrics: RosterEvaluationMetrics
    logs: List[str] = field(default_factory=list)


class OrchestratorAgent:
    """
    Coordinates the high-level pipeline:
    1. Load crew data & context from employee_availability_2weeks.xlsx
    2. Load management template + manager employees from Monthly Roster
    3. Build demand matrix
    4. Generate initial roster (crew + managers)
    5. Run compliance/coverage checks
    6. Optional conflict resolution
    7. Cost estimation & export
    """

    def __init__(self, store_id: str, start_date: date, end_date: date):
        self.store_id = store_id
        self.start_date = start_date
        self.end_date = end_date
        self.logs: List[str] = []
        self.context: Optional[SystemContext] = None

    def log(self, msg: str) -> None:
        print(msg)  # simple logging to console for now
        self.logs.append(msg)

    def _get_raw_data_dir(self) -> Path:
        # project_root / "data" / "raw"
        project_root = Path(__file__).resolve().parents[1]
        return project_root / "data" / "raw"

    def run(self) -> OrchestratorResult:
        start_ts = perf_counter()

        self.log(
            f"Starting orchestration for {self.store_id} "
            f"from {self.start_date} to {self.end_date}"
        )

        raw_data_dir = self._get_raw_data_dir()

        # Track whether the conflict resolver actually ran in this execution
        conflict_resolver_used = False

        # 1. Load base crew data & context (40 employees from availability sheet)
        data_agent = DataContextAgent(raw_data_dir=raw_data_dir)
        ctx = data_agent.load_context(
            store_id=self.store_id,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        base_crew_count = len(ctx.employees)
        self.context = ctx
        self.log(f"Loaded {base_crew_count} crew employees with availability data.")

        # 1b. Load management roster template (expected manager headcount per weekday)
        mgmt_agent = ManagementTemplateAgent(raw_data_dir=raw_data_dir)
        ctx.manager_template_by_weekday = mgmt_agent.load_manager_template()
        self.log(
            "Loaded management roster template from management_roster_simplified.xlsx "
            f"for {len(ctx.manager_template_by_weekday)} weekdays."
        )

        # 1c. Create manager employees + availability from Monthly Roster, for our
        #     2-week planning window, and merge them into the SystemContext.
        mgr_emps, mgr_avail = mgmt_agent.load_manager_employees_for_window(
            start_date=self.start_date,
            end_date=self.end_date,
        )

        for emp_id, emp in mgr_emps.items():
            # IDs are 'mgr_<name>' so they won't collide with numeric crew IDs.
            ctx.employees[emp_id] = emp

        for emp_id, avail in mgr_avail.items():
            ctx.availability[emp_id] = avail

        self.log(
            f"Added {len(mgr_emps)} management employees from Monthly Roster "
            f"with 2-week availability."
        )
        self.log(
            f"Context now has {len(ctx.employees)} total employees "
            f"(crew + management)."
        )

        # 2. Build daily demand (crew demand per station, managers handled separately)
        demand_agent = DemandCoverageAgent(raw_data_dir=raw_data_dir)
        demand_agent.build_daily_demand(
            ctx=ctx,
            store_id=self.store_id,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        self.log(
            f"Built demand for {len(ctx.demand_by_date)} days "
            f"for {self.store_id}."
        )

        # 3. Generate initial roster (crew + managers) via CP-SAT
        gen_agent = CandidateGeneratorAgent()
        roster = gen_agent.generate_initial_roster(
            ctx=ctx,
            store_id=self.store_id,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        self.log(
            f"Generated initial roster with {len(roster.assignments)} "
            f"shift assignments."
        )

        # 3b. Assign stations based on skills and demand (managers may remain unstationed)
        station_agent = SkillStationAgent()
        roster = station_agent.assign_stations(ctx=ctx, roster=roster)
        self.log("Assigned stations to all shift assignments where possible.")

        # 4. Compliance & coverage checks (first pass)
        comp_agent = ComplianceAgent()
        violations = comp_agent.check_roster(ctx=ctx, roster=roster)

        hard_violations = [v for v in violations if v.severity == ViolationSeverity.HARD]
        soft_violations = [v for v in violations if v.severity == ViolationSeverity.SOFT]

        self.log(
            f"Initial compliance: {len(hard_violations)} hard, "
            f"{len(soft_violations)} soft violations."
        )

        cov_agent = CoverageEvaluatorAgent()
        metrics = cov_agent.evaluate(
            ctx=ctx,
            roster=roster,
            start_date=self.start_date,
            end_date=self.end_date,
        )
        self.log(
            f"Initial coverage: coverage_score={metrics.coverage_score:.3f}, "
            f"peak_coverage_score={metrics.peak_coverage_score:.3f}, "
            f"manager_coverage={metrics.manager_coverage_score:.3f}, "
            f"manager_opening={metrics.manager_opening_coverage:.3f}, "
            f"manager_closing={metrics.manager_closing_coverage:.3f}, "
            f"manager_peak_two={metrics.manager_peak_two_coverage_score:.3f}"
        )


        # 5. Conflict resolution: try to fix over-hours if there are hard violations
        if hard_violations:
            self.log("Running ConflictResolutionAgent to reduce over-hours...")
            conf_agent = ConflictResolutionAgent()
            conflict_resolver_used = True

            roster, cr_logs = conf_agent.rebalance_hours(
                ctx=ctx,
                roster=roster,
            )
            for line in cr_logs:
                self.log("[ConflictResolution] " + line)

            # Re-run compliance & coverage after adjustments
            violations = comp_agent.check_roster(ctx=ctx, roster=roster)
            hard_violations = [
                v for v in violations if v.severity == ViolationSeverity.HARD
            ]
            soft_violations = [
                v for v in violations if v.severity == ViolationSeverity.SOFT
            ]
            self.log(
                f"Post-resolution compliance: {len(hard_violations)} hard, "
                f"{len(soft_violations)} soft violations."
            )

            metrics = cov_agent.evaluate(
                ctx=ctx,
                roster=roster,
                start_date=self.start_date,
                end_date=self.end_date,
            )
            self.log(
                "Post-resolution coverage: "
                f"coverage_score={metrics.coverage_score:.3f}, "
                f"peak_coverage_score={metrics.peak_coverage_score:.3f}, "
                f"manager_coverage={metrics.manager_coverage_score:.3f} "
                f"manager_opening={metrics.manager_opening_coverage:.3f}, "
                f"manager_closing={metrics.manager_closing_coverage:.3f}, "
                f"manager_peak_two={metrics.manager_peak_two_coverage_score:.3f}"
            )

        # 5b. Estimate labour cost for the final roster
        cost_agent = CostEstimatorAgent()
        metrics.labour_cost_estimate = cost_agent.estimate_cost(ctx=ctx, roster=roster)
        self.log(
            "Estimated labour cost for this roster: "
            f"AUD {metrics.labour_cost_estimate:,.2f}"
        )

        # 6. Export final roster
        export_dir = self._get_raw_data_dir().parents[0] / "processed"
        export_agent = ExportAgent(output_dir=export_dir)
        out_path = export_agent.export_roster(ctx=ctx, roster=roster)
        self.log(f"Exported final roster to {out_path}")

        # 7. Generate human-readable explanation
        explainer = ExplanationAgent()
        summary_lines = explainer.summarize(
            ctx=ctx,
            roster=roster,
            violations=violations,
            metrics=metrics,
            conflict_resolver_used=conflict_resolver_used,
        )
        for line in summary_lines:
            self.log("[Summary] " + line)

        # 8. Runtime measurement vs challenge budget
        elapsed = perf_counter() - start_ts
        self.log(
            f"Total orchestration runtime: {elapsed:.2f}s (challenge budget: 180s)."
        )

        # 9. Final completion status
        if conflict_resolver_used:
            self.log("Orchestration completed (with conflict resolution).")
        else:
            self.log("Orchestration completed (no conflict resolution required).")

        return OrchestratorResult(
            roster=roster,
            violations=violations,
            metrics=metrics,
            logs=self.logs,
        )
