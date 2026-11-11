"""WorldQuant BRAIN Alpha Pipeline - Streamlit Monitoring Dashboard.

Provides a read-only dark-themed operational interface to monitor
alpha simulations, quality funnels, the graveyard of rejections,
golden alphas, and live system pipeline logs.
"""

from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

DB_PATH = Path("alphas.db")
LOG_PATH = Path("pipeline.log")

# --- Page Configuration & Dark Theme Styling ---
st.set_page_config(
    page_title="WorldQuant BRAIN Dashboard",
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
        font-size: 2rem;
        font-weight: 700;
        color: #00d26a;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.9rem;
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
        margin-bottom: 1.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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


# --- Load Data ---
df_alphas, df_events = load_data(str(DB_PATH))

# --- Sidebar Controls ---
st.sidebar.markdown("## ⚡ Control Panel")
st.sidebar.markdown(f"**Database:** `{DB_PATH.resolve().name}`")

if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
    st.cache_data.clear()
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

# --- Header ---
st.markdown('<div class="main-header">⚡ WorldQuant BRAIN Alpha Dashboard</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Real-time surveillance of quantitative generation, rejection funnels, and quality metrics</div>',
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
tab1, tab2, tab3, tab4 = st.tabs(
    [
        "📊 The Funnel",
        "🏆 Golden Alphas",
        "🪦 The Graveyard (Rejections)",
        "📜 System Logs",
    ]
)

# --- Tab 1: The Funnel ---
with tab1:
    st.subheader("Alpha Generation Pipeline Funnel")
    st.caption(
        "Progression from raw LLM proposals through Critic vetoes and static linting to simulated Golden Alphas."
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

    fig = px.funnel(
        funnel_df,
        x="Count",
        y="Stage",
        color="Stage",
        color_discrete_sequence=["#6366f1", "#3b82f6", "#10b981", "#f59e0b"],
    )
    fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=40, r=40, t=30, b=30),
    )
    st.plotly_chart(fig, use_container_width=True)

# --- Tab 2: Golden Alphas ---
with tab2:
    st.subheader("Golden Alphas (Passed Quality Filters)")
    st.caption(
        "Alphas meeting strict criteria: Sharpe >= 1.25, Fitness > 1.0, and 1% <= Turnover <= 70%."
    )

    if golden_alphas_df.empty:
        st.info("No golden alphas recorded for the selected date range.")
    else:
        if "raw_response" in golden_alphas_df.columns:
            golden_alphas_df["Hypothesis"] = golden_alphas_df["raw_response"].apply(
                extract_hypothesis
            )
        else:
            golden_alphas_df["Hypothesis"] = "N/A"

        cols_map = {
            "expression": "Expression",
            "Hypothesis": "Hypothesis",
            "sharpe": "Sharpe",
            "fitness": "Fitness",
            "turnover": "Turnover",
        }
        available_cols = [c for c in ["expression", "Hypothesis", "sharpe", "fitness", "turnover"] if c in golden_alphas_df.columns]
        display_df = golden_alphas_df[available_cols].rename(columns=cols_map)

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

# --- Tab 3: The Graveyard (Rejections) ---
with tab3:
    st.subheader("The Graveyard (Pipeline Rejections)")
    st.caption(
        "Audit trail of rejected candidates caught by Validator static checks or PM Critic vetoes."
    )

    if graveyard_df.empty:
        st.info("No rejection events recorded in the graveyard for the selected date range.")
    else:
        if "component" in graveyard_df.columns:
            comp_options = ["All"] + sorted(graveyard_df["component"].dropna().unique().tolist())
            selected_comp = st.selectbox("Filter by Component", comp_options)
            if selected_comp != "All":
                graveyard_df = graveyard_df[graveyard_df["component"] == selected_comp]

        cols_map = {
            "timestamp": "Timestamp",
            "component": "Component",
            "status": "Status",
            "reason": "Reason",
            "alpha_id": "Alpha ID",
        }
        available_cols = [c for c in ["timestamp", "component", "status", "reason", "alpha_id"] if c in graveyard_df.columns]
        display_df = graveyard_df[available_cols].rename(columns=cols_map)

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

# --- Tab 4: System Logs ---
with tab4:
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
