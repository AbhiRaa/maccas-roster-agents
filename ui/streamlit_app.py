from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

import pandas as pd
import streamlit as st

# --------------------------------------------------------------------
# Ensure project root is on sys.path so "agents" and "core" import OK
# --------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.orchestrator import OrchestratorAgent  # noqa: E402


st.set_page_config(
    page_title="McDonald's Multi-Agent Roster Demo",
    layout="wide",
)

st.title("🍟 McDonald's Multi-Agent Roster Demo")

st.markdown(
    """
This UI runs the full **multi-agent pipeline**:

**DataContext → ManagementTemplate → DemandCoverage → Generator → SkillStation → Compliance → CoverageEvaluator → CostEstimator → ExportAgent → Explainer**

Use it in the demo to show:

- One click → full 2-week roster
- Key KPIs (coverage, cost, manager coverage)
- Logs that explain what each agent did
"""
)

# --- Controls ---------------------------------------------------------------

col_left, col_right = st.columns(2)

with col_left:
    store_id = st.selectbox("Store", ["store_1"], index=0)
    start_date = st.date_input("Start date", value=date(2024, 12, 9))

with col_right:
    end_date = st.date_input("End date", value=date(2024, 12, 22))

if start_date > end_date:
    st.error("Start date must be on or before end date.")
    st.stop()

run_button = st.button("🚀 Generate roster", type="primary")


# --- Run orchestration ------------------------------------------------------

if run_button:
    with st.spinner("Running multi-agent orchestration..."):
        orch = OrchestratorAgent(
            store_id=store_id,
            start_date=start_date,
            end_date=end_date,
        )
        result = orch.run()
        ctx = orch.context  # contains employees, availability, demand, etc.

    st.success("Roster generated successfully!")

    metrics = result.metrics
    violations = result.violations
    roster = result.roster

    # --- KPI Cards ----------------------------------------------------------

    st.subheader("Key Metrics")

    m1, m2, m3, m4 = st.columns(4)
    m5, m6, m7, m8 = st.columns(4)

    with m1:
        st.metric(
            "Overall coverage",
            f"{metrics.coverage_score * 100:.1f}%",
        )
    with m2:
        st.metric(
            "Peak coverage (lunch/dinner)",
            f"{metrics.peak_coverage_score * 100:.1f}%",
        )
    with m3:
        st.metric(
            "Manager coverage (any time)",
            f"{metrics.manager_coverage_score * 100:.1f}%",
        )
    with m4:
        st.metric(
            "Weekend uplift vs weekdays",
            f"{metrics.fairness_score:.2f}×",
        )

    with m5:
        st.metric(
            "Opening manager coverage",
            f"{metrics.manager_opening_coverage * 100:.1f}%",
        )
    with m6:
        st.metric(
            "Closing manager coverage",
            f"{metrics.manager_closing_coverage * 100:.1f}%",
        )

    # manager_peak_two_coverage_score is present in your latest metrics model
    peak_two = getattr(metrics, "manager_peak_two_coverage_score", None)
    with m7:
        if peak_two is not None:
            st.metric(
                "Peak manager coverage (2+ mgrs)",
                f"{peak_two * 100:.1f}%",
            )
        else:
            st.metric("Peak manager coverage (2+ mgrs)", "N/A")

    with m8:
        st.metric(
            "Estimated labour cost (2 weeks)",
            f"AUD {metrics.labour_cost_estimate:,.0f}",
        )

    # --- Violations summary -------------------------------------------------

    st.subheader("Compliance summary")

    hard = [v for v in violations if v.severity.value == "hard"]
    soft = [v for v in violations if v.severity.value == "soft"]

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Hard violations", len(hard))
    with c2:
        st.metric("Soft violations", len(soft))

    if soft:
        with st.expander("View sample soft violations", expanded=False):
            for v in soft[:10]:
                msg = f"[{v.severity.value.upper()}] {v.code}: {v.message}"
                if v.employee_id:
                    msg += f" (employee: {v.employee_id})"
                if v.date:
                    msg += f" on {v.date.isoformat()}"
                st.write("- " + msg)

    # --- Roster table -------------------------------------------------------

    st.subheader("Generated roster (full 2 weeks)")

    # Build a nice DataFrame with employee names and stations
    emp_name_lookup = {
        emp_id: emp.name for emp_id, emp in (ctx.employees or {}).items()
    }

    rows = []
    for a in roster.assignments:
        rows.append(
            {
                "Date": a.date,
                "Employee ID": a.employee_id,
                "Employee Name": emp_name_lookup.get(a.employee_id, a.employee_id),
                "Shift Code": a.shift_code,
                "Station": getattr(a.station, "name", "") if a.station else "",
                "Store": a.store_id,
            }
        )

    df = pd.DataFrame(rows).sort_values(["Date", "Employee Name", "Shift Code"])

    st.dataframe(
        df,
        use_container_width=True,
        height=500,
    )

    st.caption(
        "This table shows the complete 2-week roster produced by the multi-agent system."
    )

    # --- Logs & explanation -------------------------------------------------

    with st.expander("Run log & explanation", expanded=False):
        st.code("\n".join(result.logs), language="text")
