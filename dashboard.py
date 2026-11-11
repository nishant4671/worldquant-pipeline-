"""WorldQuant BRAIN Alpha Pipeline - Streamlit Monitoring Command Centre.

Provides a read-only dark-themed operational command centre to monitor
alpha simulations, quality funnels, brain analytics, performance landscape,
near misses, the graveyard of rejections, golden alphas, and system health.
"""

from datetime import date, datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path("alphas.db")
LOG_PATH = Path("pipeline.log")

# --- Page Configuration & Dark Theme Styling ---
st.set_page_config(
    page_title="WorldQuant BRAIN Command Centre",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* Dark theme enhancements */
    .stApp {
        background-color: #0e1117;
        color: #e0e0e0;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.8rem;
        font-weight: 700;
        color: #00d26a;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.85rem;
        color: #a0aec0;
    }
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        color: #f7fafc;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #718096;
        margin-bottom: 1.2rem;
    }
    .alpha-card {
        background-color: #1a202c;
        border: 1px solid #2d3748;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Authentication Gate ---
def check_password() -> bool:
    """Validate user password against the UI_PASSWORD environment variable."""
    expected_password = os.environ.get("UI_PASSWORD")

    # If UI_PASSWORD is not configured, block access
    if not expected_password:
        st.error("⚠️ `UI_PASSWORD` environment variable is not configured on the server.")
        st.info(
            "Please set `UI_PASSWORD=your_password` in `.env` or the environment, then restart the dashboard."
        )
        st.stop()
        return False

    # Check if already authenticated in this session
    if st.session_state.get("authenticated", False):
        return True

    # Render login form
    st.markdown('<div class="main-header">🔒 WorldQuant BRAIN Command Centre</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-header">Please authenticate to access the quantitative surveillance console.</div>',
        unsafe_allow_html=True,
    )

    col1, _ = st.columns([1, 2])
    with col1:
        with st.form("login_form"):
            password_input = st.text_input("Enter Dashboard Password", type="password")
            submitted = st.form_submit_button("Log In", use_container_width=True)

            if submitted:
                if password_input == expected_password:
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("❌ Incorrect password. Access denied.")
                    st.stop()

    st.stop()
    return False


if not check_password():
    st.stop()


# --- System Health Banner ---
def show_system_health_banner() -> None:
    """Parse last 100 lines of pipeline.log to display real-time operational status."""
    if not LOG_PATH.exists():
        st.info("ℹ️ **System Status**: `pipeline.log` not found. No recent executions logged.")
        return

    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        recent_lines = lines[-100:] if len(lines) >= 100 else lines
        recent_text = "".join(recent_lines)

        has_error = any("ERROR" in line or "429" in line for line in recent_lines)
        has_success = any(
            "pipeline finished successfully" in line.lower()
            or "pipeline finished" in line.lower()
            or "simulation completed" in line.lower()
            for line in recent_lines
        )

        if has_error:
            st.error("🚨 **System Alert**: Errors or HTTP 429 rate limits detected in the last 100 log lines! Inspect the System Logs tab.")
        elif has_success:
            st.success("✅ **System Healthy**: Pipeline finished successfully.")
        else:
            st.info("ℹ️ **System Status**: Pipeline is idle or in-progress. No errors detected.")
    except Exception as err:
        st.warning(f"Unable to inspect pipeline health logs: {err}")


# --- Data Access Functions ---
@st.cache_data(ttl=10)
def load_data(db_file: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load alphas and events tables from SQLite database in read-only mode."""
    target = Path(db_file)
    if not target.exists():
        return pd.DataFrame(), pd.DataFrame()

    try:
        conn = sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(str(target))

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        df_alphas = (
            pd.read_sql_query("SELECT * FROM alphas", conn)
            if "alphas" in tables
            else pd.DataFrame()
        )
        df_events = (
            pd.read_sql_query("SELECT * FROM events", conn)
            if "events" in tables
            else pd.DataFrame()
        )
    finally:
        conn.close()

    if not df_alphas.empty and "created_at" in df_alphas.columns:
        df_alphas["created_at_dt"] = pd.to_datetime(
            df_alphas["created_at"], errors="coerce"
        )
    else:
        df_alphas["created_at_dt"] = pd.Series(dtype="datetime64[ns]")

    if not df_events.empty and "timestamp" in df_events.columns:
        df_events["timestamp_dt"] = pd.to_datetime(
            df_events["timestamp"], errors="coerce"
        )
    else:
        df_events["timestamp_dt"] = pd.Series(dtype="datetime64[ns]")

    return df_alphas, df_events


def extract_hypothesis(raw_response_str: Any) -> str:
    """Extract hypothesis or source description from raw_response JSON."""
    if not raw_response_str or pd.isna(raw_response_str):
        return "N/A"
    try:
        if isinstance(raw_response_str, dict):
            data = raw_response_str
        else:
            data = json.loads(str(raw_response_str))
        if isinstance(data, dict):
            return data.get("hypothesis") or data.get("source") or "N/A"
    except Exception:
        pass
    return "N/A"


def diagnose_alpha(row: pd.Series) -> str:
    """Diagnose why an alpha failed quality filters despite having Sharpe >= 1.0."""
    issues = []
    fitness = row.get("fitness")
    sharpe = row.get("sharpe")
    turnover = row.get("turnover")

    if pd.notna(fitness) and fitness < 1.0:
        issues.append("Needs lower turnover (Fitness < 1.0)")
    if pd.notna(sharpe) and sharpe < 1.25:
        issues.append("Sharpe marginal (1.00 - 1.24, target >= 1.25)")
    if pd.notna(turnover):
        if turnover > 0.70:
            issues.append("Excessive turnover (> 70%)")
        elif turnover < 0.01:
            issues.append("Turnover too low (< 1%)")

    return "; ".join(issues) if issues else "Sub-threshold criteria"


# --- Load Data ---
df_alphas, df_events = load_data(str(DB_PATH))

# --- Sidebar Controls ---
st.sidebar.markdown("## ⚡ Control Panel")
st.sidebar.markdown(f"**Database:** `{DB_PATH.resolve().name}`")

if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

if st.sidebar.button("🚪 Log Out", use_container_width=True):
    st.session_state["authenticated"] = False
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 📅 Date Filter")

all_dates: list[date] = []
if not df_alphas.empty and df_alphas["created_at_dt"].notna().any():
    all_dates.extend(df_alphas["created_at_dt"].dropna().dt.date.tolist())
if not df_events.empty and df_events["timestamp_dt"].notna().any():
    all_dates.extend(df_events["timestamp_dt"].dropna().dt.date.tolist())

if all_dates:
    min_date = min(all_dates)
    max_date = max(all_dates)
else:
    min_date = date.today()
    max_date = date.today()

date_selection = st.sidebar.date_input(
    "Filter by Date Range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

if isinstance(date_selection, (list, tuple)) and len(date_selection) == 2:
    start_date, end_date = date_selection
else:
    start_date, end_date = min_date, max_date

# Filter dataframes by selected date range
if not df_alphas.empty and "created_at_dt" in df_alphas.columns:
    alpha_mask = (df_alphas["created_at_dt"].dt.date >= start_date) & (
        df_alphas["created_at_dt"].dt.date <= end_date
    )
    filtered_alphas = df_alphas[alpha_mask].copy()
else:
    filtered_alphas = df_alphas.copy()

if not df_events.empty and "timestamp_dt" in df_events.columns:
    event_mask = (df_events["timestamp_dt"].dt.date >= start_date) & (
        df_events["timestamp_dt"].dt.date <= end_date
    )
    filtered_events = df_events[event_mask].copy()
else:
    filtered_events = df_events.copy()

# --- Top Banner & Header ---
show_system_health_banner()

st.markdown('<div class="main-header">⚡ WorldQuant BRAIN Command Centre</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Executive surveillance of quantitative alpha pipeline, quality gates, and failure diagnostics</div>',
    unsafe_allow_html=True,
)

# --- KPI Row (Top) ---
total_simulated = len(filtered_alphas)
golden_alphas_df = (
    filtered_alphas[filtered_alphas["passed_filters"] == 1].copy()
    if not filtered_alphas.empty and "passed_filters" in filtered_alphas.columns
    else pd.DataFrame()
)
golden_count = len(golden_alphas_df)
graveyard_df = (
    filtered_events[filtered_events["status"] == "REJECTED"].copy()
    if not filtered_events.empty and "status" in filtered_events.columns
    else pd.DataFrame()
)
total_graveyard = len(graveyard_df)
yield_rate = (golden_count / total_simulated * 100.0) if total_simulated > 0 else 0.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Simulated", f"{total_simulated:,}")
col2.metric("Golden Alphas", f"{golden_count:,}")
col3.metric("Total Graveyard Events", f"{total_graveyard:,}")
col4.metric("Yield Rate", f"{yield_rate:.1f}%")

st.markdown("---")

# --- Tabs ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "📊 The Funnel & Analytics",
        "🏆 Golden Alphas",
        "🎯 Near Misses",
        "🪦 The Graveyard (Rejections)",
        "📜 System Logs",
    ]
)

# --- Tab 1: The Funnel & Analytics ---
with tab1:
    st.subheader("Pipeline Quality Funnel")
    st.caption(
        "Candidate progression from initial Fast Expression proposal through PM Critic and static validation to golden quality status."
    )

    validator_rejections = (
        len(
            filtered_events[
                (filtered_events["component"] == "Validator")
                & (filtered_events["status"] == "REJECTED")
            ]
        )
        if not filtered_events.empty and "component" in filtered_events.columns
        else 0
    )
    critic_rejections = (
        len(
            filtered_events[
                (filtered_events["component"] == "Critic")
                & (filtered_events["status"] == "REJECTED")
            ]
        )
        if not filtered_events.empty and "component" in filtered_events.columns
        else 0
    )

    stage_golden = golden_count
    stage_val_passed = max(total_simulated, stage_golden)
    stage_critic_approved = max(stage_val_passed + critic_rejections, stage_val_passed)
    stage_proposals = max(stage_critic_approved + validator_rejections, stage_critic_approved)

    funnel_df = pd.DataFrame(
        {
            "Stage": [
                "Total Proposals",
                "Critic Approved",
                "Validator Passed",
                "Golden Alphas",
            ],
            "Count": [
                stage_proposals,
                stage_critic_approved,
                stage_val_passed,
                stage_golden,
            ],
        }
    )

    fig_funnel = px.funnel(
        funnel_df,
        x="Count",
        y="Stage",
        color="Stage",
        color_discrete_sequence=["#6366f1", "#3b82f6", "#10b981", "#f59e0b"],
    )
    fig_funnel.update_layout(
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=40, t=30, b=30),
    )
    st.plotly_chart(fig_funnel, use_container_width=True)

    st.markdown("---")

    # Item 2: Brain Analytics Chart (Critic/Validator rejections by reason)
    st.subheader("🧠 Brain Analytics: Rejection Breakdown")
    st.caption("Distribution of failure reasons identified by static validation linting and portfolio manager critique.")

    if not graveyard_df.empty and "reason" in graveyard_df.columns:
        reason_summary = (
            graveyard_df.groupby(["component", "reason"])
            .size()
            .reset_index(name="Count")
            .sort_values(by="Count", ascending=True)
        )

        fig_reasons = px.bar(
            reason_summary,
            x="Count",
            y="reason",
            color="component",
            orientation="h",
            title="Breakdown of Rejections by Gatekeeper & Reason",
            labels={"reason": "Rejection Reason", "Count": "Count", "component": "Gatekeeper"},
            color_discrete_map={"Validator": "#f87171", "Critic": "#fb923c"},
        )
        fig_reasons.update_layout(
            template="plotly_dark",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=40, r=40, t=40, b=30),
        )
        st.plotly_chart(fig_reasons, use_container_width=True)
    else:
        st.info("No rejection records available for Brain Analytics breakdown.")

    st.markdown("---")

    # Item 3: Performance Scatter Plot (Sharpe vs. Turnover with Golden Zone)
    st.subheader("🎯 Performance Landscape: Sharpe vs. Turnover")
    st.caption("Golden Zone defined by Sharpe >= 1.25 and Turnover between 0.01 (1%) and 0.70 (70%).")

    if (
        not filtered_alphas.empty
        and "turnover" in filtered_alphas.columns
        and "sharpe" in filtered_alphas.columns
    ):
        scatter_df = filtered_alphas.dropna(subset=["turnover", "sharpe"]).copy()
        if not scatter_df.empty:
            scatter_df["Status"] = scatter_df["passed_filters"].apply(
                lambda p: "Passed (Golden)" if p == 1 else "Failed Filters"
            )

            hover_cols = [
                c
                for c in ["alpha_id", "expression", "fitness"]
                if c in scatter_df.columns
            ]

            fig_scatter = px.scatter(
                scatter_df,
                x="turnover",
                y="sharpe",
                color="Status",
                color_discrete_map={
                    "Passed (Golden)": "#00d26a",
                    "Failed Filters": "#f87171",
                },
                hover_data=hover_cols,
                title="Sharpe Ratio vs. Turnover Landscape",
                labels={"turnover": "Turnover", "sharpe": "Sharpe Ratio"},
            )

            # Quality gate threshold lines
            fig_scatter.add_vline(
                x=0.01,
                line_dash="dash",
                line_color="#38bdf8",
                annotation_text="Min Turnover (0.01)",
            )
            fig_scatter.add_vline(
                x=0.70,
                line_dash="dash",
                line_color="#38bdf8",
                annotation_text="Max Turnover (0.70)",
            )
            fig_scatter.add_hline(
                y=1.25,
                line_dash="dash",
                line_color="#facc15",
                annotation_text="Min Sharpe (1.25)",
            )

            # Golden Zone shaded box
            max_sharpe = max(2.5, scatter_df["sharpe"].max() + 0.3)
            fig_scatter.add_hrect(
                y0=1.25,
                y1=max_sharpe,
                x0=0.01,
                x1=0.70,
                fillcolor="#00d26a",
                opacity=0.08,
                line_width=0,
                annotation_text="Golden Zone",
                annotation_position="top left",
            )

            fig_scatter.update_layout(
                template="plotly_dark",
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=40, r=40, t=40, b=30),
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
        else:
            st.info("No numeric simulation metrics available for the performance scatter plot.")
    else:
        st.info("Simulated alphas data not available.")


# --- Tab 2: Golden Alphas (Cards with Copyable Code) ---
with tab2:
    st.subheader("Golden Alphas (Passed Quality Filters)")
    st.caption(
        "Alphas meeting strict criteria: Sharpe >= 1.25, Fitness > 1.0, and 1% <= Turnover <= 70%."
    )

    if golden_alphas_df.empty:
        st.info("No golden alphas recorded for the selected date range.")
    else:
        st.markdown(f"**Found {len(golden_alphas_df)} Golden Alpha(s)**")
        for idx, row in golden_alphas_df.iterrows():
            alpha_id = row.get("alpha_id", f"Alpha #{idx}")
            expr = str(row.get("expression", ""))
            sharpe = row.get("sharpe")
            fitness = row.get("fitness")
            turnover = row.get("turnover")
            raw_resp = row.get("raw_response")
            hypothesis = extract_hypothesis(raw_resp)

            with st.container():
                st.markdown(f"### 🏆 `{alpha_id}`")
                st.code(expr, language="text")

                c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
                c1.metric("Sharpe", f"{sharpe:.2f}" if pd.notna(sharpe) else "N/A")
                c2.metric("Fitness", f"{fitness:.2f}" if pd.notna(fitness) else "N/A")
                c3.metric(
                    "Turnover",
                    f"{turnover:.1%}" if pd.notna(turnover) else "N/A",
                )
                with c4:
                    st.markdown("**Economic Hypothesis:**")
                    st.write(hypothesis)

                st.markdown("---")


# --- Tab 3: Near Misses ---
with tab3:
    st.subheader("🎯 Near Misses (Sharpe >= 1.0, Failed Filters)")
    st.caption(
        "Promising candidates that scored Sharpe >= 1.0 but fell short of one or more hard gates. Prime candidates for mutation feedback."
    )

    if (
        not filtered_alphas.empty
        and "sharpe" in filtered_alphas.columns
        and "passed_filters" in filtered_alphas.columns
    ):
        near_misses_df = filtered_alphas[
            filtered_alphas["sharpe"].notna()
            & (filtered_alphas["sharpe"] >= 1.0)
            & (filtered_alphas["passed_filters"] == 0)
        ].copy()
    else:
        near_misses_df = pd.DataFrame()

    if near_misses_df.empty:
        st.info("No near-miss alphas (Sharpe >= 1.0, passed_filters = 0) found in the selected date range.")
    else:
        near_misses_df["Diagnosis"] = near_misses_df.apply(diagnose_alpha, axis=1)

        cols_map = {
            "alpha_id": "Alpha ID",
            "expression": "Expression",
            "sharpe": "Sharpe",
            "fitness": "Fitness",
            "turnover": "Turnover",
            "Diagnosis": "Diagnosis",
        }
        available_cols = [
            c
            for c in ["alpha_id", "expression", "sharpe", "fitness", "turnover", "Diagnosis"]
            if c in near_misses_df.columns
        ]
        display_nm = near_misses_df[available_cols].rename(columns=cols_map)

        st.dataframe(
            display_nm,
            use_container_width=True,
            hide_index=True,
        )


# --- Tab 4: The Graveyard (Rejections) ---
with tab4:
    st.subheader("The Graveyard (Pipeline Rejections)")
    st.caption(
        "Audit trail of rejected candidates caught by Validator static checks or PM Critic vetoes."
    )

    if graveyard_df.empty:
        st.info("No rejection events recorded in the graveyard for the selected date range.")
    else:
        if "component" in graveyard_df.columns:
            comp_options = ["All"] + sorted(
                graveyard_df["component"].dropna().unique().tolist()
            )
            selected_comp = st.selectbox("Filter by Gatekeeper", comp_options)
            if selected_comp != "All":
                graveyard_df = graveyard_df[graveyard_df["component"] == selected_comp]

        cols_map = {
            "timestamp": "Timestamp",
            "component": "Component",
            "status": "Status",
            "reason": "Reason",
            "alpha_id": "Alpha ID",
        }
        available_cols = [
            c
            for c in ["timestamp", "component", "status", "reason", "alpha_id"]
            if c in graveyard_df.columns
        ]
        display_df = graveyard_df[available_cols].rename(columns=cols_map)

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )


# --- Tab 5: System Logs ---
with tab5:
    st.subheader("System Logs (`pipeline.log`)")
    st.caption("Latest 50 execution log lines captured from the automated alpha mining pipeline.")

    if LOG_PATH.exists():
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            last_50 = lines[-50:] if len(lines) >= 50 else lines
            st.text_area(
                label="Recent Pipeline Log Stream",
                value="".join(last_50),
                height=450,
                disabled=True,
            )
        except Exception as err:
            st.error(f"Failed to read {LOG_PATH}: {err}")
    else:
        st.warning("pipeline.log does not exist yet. Run a pipeline simulation to populate logs.")
