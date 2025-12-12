# 🍔 Maccas Roster Agents

A **multi-agent rostering system** for a busy McDonald’s store, built to generate **2-week rosters** that balance:

- Fair Work–style contract and rest rules  
- Coverage of **lunch / dinner peaks**  
- **Manager** availability (opening, closing & peak windows)  
- Labour cost and weekend uplift  
- Real-world McDonald’s style shift codes (S, 1F, 2F, 3F, etc.)

Under the hood, agents collaborate around a shared `SystemContext`, with a CP-SAT optimiser (OR-Tools) doing the heavy lifting and a **Streamlit UI** for quick visualisation.

---

## 🚀 Quick Start

### 1. Clone the repo

    git clone git@github.com:AbhiRaa/maccas-roster-agents.git
    cd maccas-roster-agents

### 2. Create & activate a virtualenv

    python -m venv .venv
    source .venv/bin/activate  # macOS / Linux
    # .venv\Scripts\activate   # Windows (PowerShell)

### 3. Install dependencies

    pip install -r requirements.txt

### 4. Run the CLI demo

This runs the full multi-agent pipeline for store_1 over a 2-week window.

    python -m scripts.run_demo

You should see logs like:
- Loaded 40 crew employees with availability data.
- Added 6 management employees from Monthly Roster with 2-week availability.
- Generated initial roster with 262 shift assignments.
- Initial compliance: 0 hard, 17 soft violations.
- Coverage: 100% overall, ~77% on lunch/dinner peaks.
- Manager coverage: 100% of days had at least one manager on duty.
- Opening/closing coverage: 100% opening, ~93% closing.
- Peak manager coverage: 85.7% of lunch/dinner windows had at least two managers.

The final roster is exported to:    data/processed/roster_store1_2weeks.csv.     and a human-readable explanation is printed at the end.


## Lightweight UI (Streamlit)

We also provide a minimal UI to browse the generated roster.

#### Run the app
    
    streamlit run ui/streamlit_app.py

Then open the local URL it prints (usually http://localhost:8501).

Current UI features:

- Button to run the full orchestration from the browser
- Table view of the final roster
- Filters:
    by employee
    by date range
    by contract type / manager vs crew
- Badges for:
    coverage %
    peak coverage %
    manager coverage (overall, opening, closing, peak-two-managers)
    labour cost estimate

### Core Concepts

#### SystemContext

All agents share a central SystemContext:
- employees: all crew + management (core.models.Employee)
- availability: per-employee, per-date allowed shift codes
- demand_by_date: simple daily demand per station (e.g. kitchen, counter, etc.)
- manager_template_by_weekday: expected manager headcount per weekday learned from the monthly roster template

This acts as a blackboard for the multi-agent system.

### Multi-Agent Architecture

The high-level orchestrator is agents/orchestrator.OrchestratorAgent.

Pipeline

1. DataContextAgent
- Loads employee_availability_2weeks.xlsx
- Builds base crew list (40 employees) + availability

2. ManagementTemplateAgent
- Reads management_roster_simplified.xlsx
- Learns expected manager headcount per weekday
- Creates manager employees (e.g. mgr_john_smith) with:
    contract_type=full_time
    skill_tags=[MANAGER]
- Projects their Monthly Roster into the 2-week plan as availability

3. DemandCoverageAgent
- Builds daily demand for each date and station
- This is the main “workload” signal for the optimiser

4. CandidateGeneratorAgent
- Builds a CP-SAT model (OR-Tools)
- Variables: x[e, d, s] = 1 if employee e works shift s on date d
- Objective:
    Minimise under-coverage (huge weight)
    Then minimise weekly overtime
    Then penalise days with no manager
    Then penalise missing opening/closing managers

5. SkillStationAgent
- Assigns stations (e.g. KITCHEN, COUNTER, MCCAFE) based on:
    employee skill tags
    station demand per day

6. ComplianceAgent

- Checks hard + soft rules:
    Only one shift per person per day
    Contract bounds over 2-week window
    Weekly Fair Work style bands with overtime slack
    Min 10h rest between days

- Emits a list of Violation objects

7. CoverageEvaluatorAgent

- Computes RosterEvaluationMetrics:
    coverage_score (overall)
    peak_coverage_score (11–14 & 17–21 windows)
    fairness_score (weekend uplift ratio)
    manager_coverage_score (any manager on duty)
    manager_opening_coverage (manager on S/1F-style shift)
    manager_closing_coverage (manager on 2F-style shift)
    manager_peak_two_coverage_score (≥2 managers in lunch/dinner peaks)

8. ConflictResolutionAgent (optional)
- Only runs if hard violations remain
- Tries to rebalance shifts, reduce over-hours, and keep coverage stable
- In the current demo, the optimiser already produces 0 hard violations, so this agent is available but not invoked.

9. CostEstimatorAgent
- Estimates total labour cost for the roster using simple rate assumptions.

10. ExportAgent
- Writes the final roster to CSV for Excel / BI tools.

11. ExplanationAgent
- Produces human-readable lines like:
    “Scheduled 262 shifts for 46 employees over 2 weeks.”
    “Weekend uplift: staff levels on weekends are 1.35x the weekday average.”
    “Most common remaining soft issues: WEEKLY_MIN_HOURS_NOT_MET x9, MIN_HOURS_NOT_MET x8.”
    “Multi-agent effect: DataContext and DemandCoverage prepared the inputs, Generator built the initial roster, SkillStation matched staff to stations, Compliance verified hours and rest rules, and CoverageEvaluator confirmed overall, peak, manager, opening/closing, and weekend performance — ConflictResolver was available but not needed in this run.”

### Constraints & Real-World Rules

This system is deliberately grounded in real operational constraints:

- Shift Templates

    S: Day Shift (06:30–15:00, 8.5h)
    1F: First Half (06:30–15:30, 9h)
    2F: Second Half (14:00–23:00, 9h)
    3F: Full Day (08:00–20:00, 12h)
    Plus special codes like:
        M (meeting/training)
        / (day off)
        NA (not available / leave)

- Contract Types
    full_time, part_time, casual
    Each with:
        2-week min/max hours
        Weekly Fair Work style band, with “overtime slack” captured as a soft var

- Rest Rules
    Min 10 hours rest between consecutive days
    Illegal patterns (e.g. late close then early open) are explicitly disallowed

- Manager Coverage
    At least one manager per day (soft penalty if not viable)
    Manager covering opening where possible (S, 1F, early shifts)
    Manager covering closing where possible (2F, late shifts)
    Lunch & dinner peak windows prefer at least two managers scheduled

- Demand vs Coverage
    Demand by date/station is derived from a simple model (e.g. weekend uplift)
    Coverage is evaluated globally across the 2-week horizon

### Example Output (from the demo run)

For the default 2-week window, a typical run produces:

- 262 shifts for 46 employees (40 crew + 6 managers)

- Coverage

    100% overall daily coverage

    ~77% coverage over lunch & dinner peak windows

- Management

    100% of days have at least one manager

    100% of days have a manager at opening

    ~93% of days have a manager at close

    ~86% of lunch/dinner windows have ≥2 managers

- Cost

    Total labour cost ≈ AUD 79,800 for 2 weeks

- Compliance

    0 hard violations

    17 soft violations (mostly small under-hours vs contract minimum)

These results are printed to the console and summarised as natural language by the ExplanationAgent.


### Project Structure

.
├── agents/
│   ├── orchestrator.py           # Coordinates all agents
│   ├── data_context.py           # Load crew & availability
│   ├── management_template.py    # Monthly roster → manager template + availability
│   ├── demand_coverage.py        # Build daily demand
│   ├── generator.py              # CP-SAT roster generation
│   ├── compliance.py             # Fair Work style constraint checks
│   ├── coverage_eval.py          # Coverage metrics (overall, peak, manager, weekend)
│   ├── conflict_resolution.py    # Optional rebalancing when hard violations exist
│   ├── skill_station.py          # Assign stations based on skills & demand
│   ├── cost.py                   # Labour cost estimation
│   ├── export.py                 # CSV export
│   └── explainer.py              # Human-readable explanation
├── core/
│   └── models.py                 # Core dataclasses + enums
├── data/
│   ├── raw/
│   │   ├── employee_availability_2weeks.xlsx
│   │   └── management_roster_simplified.xlsx
│   └── processed/
│       └── roster_store1_2weeks.csv   # Generated output
├── scripts/
│   └── run_demo.py               # CLI entry point for the full pipeline
├── ui/
│   └── streamlit_app.py          # Lightweight UI
├── requirements.txt
└── README.md
