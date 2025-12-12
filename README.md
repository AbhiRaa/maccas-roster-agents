# McRoster Multi-Agent Scheduler

## High-level Idea

We build a multi-agent system that generates a 2-week roster for an Australian McDonald's store, then automatically checks and fixes it using specialised agents.

Pipeline:

1. **DataContextAgent** – parses McDonald's Excel sheets into structured employees, availability and constraints.
2. **DemandCoverageAgent** – builds daily station demand (kitchen, counter, McCafe, dessert, delivery) from store structure.
3. **CandidateGeneratorAgent** – uses OR-Tools CP-SAT to create an initial 2-week schedule that respects availability and meets demand.
4. **SkillStationAgent** – assigns each shift to a station based on employee skills and store demand.
5. **ComplianceAgent** – checks contract-type hours (full-time, part-time, casual) and flags under/over hours.
6. **CoverageEvaluatorAgent** – computes coverage scores vs demand (overall and "peak" days).
7. **ConflictResolutionAgent** – re-balances shifts from overworked employees to underworked ones while preserving coverage.
8. **ExportAgent** – exports the final roster to CSV for managers.
9. **ExplanationAgent** – produces a human-readable summary of what happened.

Everything is coordinated by **OrchestratorAgent**, which logs each step.

## How to Run

```bash
python -m scripts.run_demo


This will:
1. Load 40 employees + 14 days of availability.
2. Build demand for 14 days.
3. Generate a roster (~261 shifts).
4. Run compliance & coverage checks.
5. Run conflict resolution to remove hard violations.
6. Export data/processed/roster_store1_2weeks.csv.
7. Print a human-readable summary of the run.