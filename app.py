"""
app.py  –  Marine Survey Weather Window Tool
Streamlit web application.
Matches the layout and logic of WeatherWindowTool.m.
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from datetime import datetime

from engine import (
    load_and_merge,
    run_scenario,
    calc_percentile,
    fmt_hrs_days,
    N_ITERATIONS,
    MONTHS,
)

# ══════════════════════════════════════════════════════════════════════════
# Page configuration
# ══════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Marine Survey Weather Window Tool",
    page_icon="🌊",
    layout="wide",
)

COLORS  = ["#1565C0", "#C62828", "#2E7D32"]   # strong blue / red / green
SEASONS = [
    "All-Year", "Summer (Apr-Sep)", "Winter (Oct-Mar)",
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# ── Custom CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .sc-header {
        font-weight: 700;
        font-size: 1.05em;
        padding: 4px 0 2px 0;
        border-bottom: 2px solid;
        margin-bottom: 6px;
    }
    .row-label {
        font-weight: 600;
        font-size: 0.9em;
        padding-top: 6px;
    }
    div[data-testid="stNumberInput"] label,
    div[data-testid="stSelectbox"] label,
    div[data-testid="stCheckbox"] label { font-size: 0.85em; }
    div[data-testid="stMetric"] {
        background: #ffffff !important;
        border-radius: 8px;
        padding: 10px 12px;
        border: 1px solid #dddddd;
    }
    div[data-testid="stMetric"] label {
        color: #444444 !important;
        font-size: 0.82em !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #111111 !important;
        font-size: 1.3em !important;
        font-weight: 700 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
        color: #333333 !important;
    }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# Authentication
# ══════════════════════════════════════════════════════════════════════════

def check_credentials(username: str, password: str) -> bool:
    """
    Validate against Streamlit secrets (never stored in the GitHub repo).
    Set up in Streamlit Cloud → App Settings → Secrets, or locally in
    .streamlit/secrets.toml.

    Format:
        [users]
        alice = "password_for_alice"
        bob   = "password_for_bob"
    """
    try:
        users = st.secrets["users"]
        return username in users and users[username] == password
    except KeyError:
        st.error("⚠️  Credentials not configured. Add [users] to Streamlit secrets.")
        return False


def show_login_screen():
    _, centre, _ = st.columns([1, 1.4, 1])
    with centre:
        st.markdown("<div style='height:60px'></div>", unsafe_allow_html=True)
        st.markdown(
            """
            <div style='text-align:center;margin-bottom:8px'>
                <span style='font-size:3em'>🌊</span>
            </div>
            <h2 style='text-align:center;color:#1565C0;margin-bottom:2px'>
                Marine Survey Weather Window Tool
            </h2>
            <p style='text-align:center;color:#666;font-size:0.95em;margin-bottom:28px'>
                Venterra Group &nbsp;·&nbsp; Restricted Access
            </p>
            """,
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            st.markdown("#### Sign in to continue")
            username = st.text_input("Username", placeholder="Enter your username",
                                     key="login_user")
            password = st.text_input("Password", type="password",
                                     placeholder="Enter your password",
                                     key="login_pass")
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            if st.button("Sign in", type="primary", use_container_width=True):
                if username and password:
                    if check_credentials(username, password):
                        st.session_state["authenticated"] = True
                        st.session_state["current_user"] = username
                        st.rerun()
                    else:
                        st.error("Incorrect username or password. Please try again.")
                else:
                    st.warning("Please enter both username and password.")
        st.markdown(
            "<p style='text-align:center;color:#aaa;font-size:0.8em;margin-top:18px'>"
            "Access is restricted to authorised users only.<br>"
            "Contact Venterra Group to request access.</p>",
            unsafe_allow_html=True,
        )


if not st.session_state.get("authenticated", False):
    show_login_screen()
    st.stop()

# ══════════════════════════════════════════════════════════════════════════
# SECTION 1 — Data Upload
# ══════════════════════════════════════════════════════════════════════════
title_col, signout_col = st.columns([8, 1])
with title_col:
    st.title("🌊 Marine Survey Weather Window Tool")
    st.caption("Monte Carlo campaign duration analysis — up to 3 concurrent scenarios")
with signout_col:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    user = st.session_state.get("current_user", "")
    st.caption(f"👤 {user}")
    if st.button("Sign out", use_container_width=True):
        st.session_state["authenticated"] = False
        st.session_state["current_user"] = ""
        st.rerun()

tab_analysis, tab_instructions = st.tabs(["📊  Analysis", "📖  Instructions & Methodology"])

with tab_instructions:
    readme_path = __file__.rsplit("/", 1)[0] + "/README.md"
    with open(readme_path, "r") as _f:
        st.markdown(_f.read())

with tab_analysis:

    st.subheader("① Load Hindcast Data")

    u1, u2, u3 = st.columns(3)
    with u1:
        hydro_file = st.file_uploader(
            "Hydrodynamics CSV  *(20-min: DateTime, CSpd, CDir …)*",
            type="csv", key="hydro")
    with u2:
        wave_file = st.file_uploader(
            "Waves CSV  *(hourly: DateTime, Hs, Tp, Tz, WaveDir …)*",
            type="csv", key="waves")
    with u3:
        wind_file = st.file_uploader(
            "Winds CSV  *(hourly: DateTime, WSpd10, WDir10)*",
            type="csv", key="winds")

    merged = None
    if hydro_file and wave_file and wind_file:
        with st.spinner("Processing and merging datasets…"):
            try:
                merged = load_and_merge(
                    hydro_file.read(),
                    wave_file.read(),
                    wind_file.read(),
                )
                n = len(merged)
                t0 = merged.index.min().date()
                t1 = merged.index.max().date()
                st.success(
                    f"✅  All 3 files loaded — **{n:,}** merged hourly records  "
                    f"({t0}  →  {t1})"
                )
            except Exception as exc:
                st.error(f"Error loading data: {exc}")
                merged = None
    else:
        st.info("Upload all three CSV files above to enable the analysis.")

    st.divider()


    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — Scenario Matrix
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("② Scenario Parameters")

    # ── Column header row ──────────────────────────────────────────────────────
    hdr_cols = st.columns([1.6, 1, 1, 1])
    hdr_cols[0].markdown("")
    for i in range(3):
        hdr_cols[i + 1].markdown(
            f"<div class='sc-header' style='border-color:{COLORS[i]};"
            f"color:{COLORS[i]};text-align:center'>Scenario {i+1}</div>",
            unsafe_allow_html=True,
        )

    # ── Helper: one labelled row with 3 widgets ────────────────────────────────
    def matrix_row(label, widget_fn, help_txt=None):
        """Render one row of the scenario matrix."""
        cols = st.columns([1.6, 1, 1, 1])
        cols[0].markdown(f"<div class='row-label'>{label}</div>",
                         unsafe_allow_html=True, help=help_txt)
        return [widget_fn(cols[i + 1], i) for i in range(3)]


    def _num(col, i, key, val, lo, hi, step):
        return col.number_input(" ", value=val, min_value=lo, max_value=hi,
                                step=step, key=f"{key}_{i}", label_visibility="collapsed")

    def _sel(col, i, key, opts, default=0):
        return col.selectbox(" ", options=opts, index=default,
                             key=f"{key}_{i}", label_visibility="collapsed")

    def _chk(col, i, key):
        return col.checkbox("Active", key=f"{key}_{i}")


    # ── Rows ───────────────────────────────────────────────────────────────────
    hs_vals    = matrix_row("Hs Limit (m)",
        lambda c, i: _num(c, i, "hs",     2.0,  0.0, 20.0,  0.5),
        help_txt="Significant wave height operability limit")

    tp_vals    = matrix_row("Tp Limit (s)",
        lambda c, i: _num(c, i, "tp",    12.0,  0.0, 30.0,  1.0),
        help_txt="Peak wave period operability limit")

    wind_vals  = matrix_row("Wind Limit (m/s)",
        lambda c, i: _num(c, i, "wind",  15.0,  0.0, 60.0,  1.0))
        help_txt="Wind speed operability limit at deck level or 10m ASL")

    curr_vals  = matrix_row("Current Limit (m/s)",
        lambda c, i: _num(c, i, "curr",   1.0,  0.0, 10.0,  0.1))
        help_txt="Current speed operability limit")

    dur_vals   = matrix_row("Total Work (hrs)",
        lambda c, i: _num(c, i, "dur",   72.0,  1.0, 8760.0, 1.0),
        help_txt="Total productive work hours required for the campaign")

    inter_vals  = matrix_row("Interruptible",
        lambda c, i: _sel(c, i, "inter", ["Yes", "No"]),
        help_txt="Can the campaign be split across multiple weather windows?")

    # Min Window — disabled in non-interruptible mode.
    #
    # When work cannot be split, the campaign needs ONE unbroken window of at
    # least Total Work hours, so Total Work IS the binding minimum: any smaller
    # Min Window is redundant and any larger one would wrongly discard usable
    # windows. Rather than leave the field sitting there silently ignored, it is
    # disabled and mirrors Total Work so the logic is visible.
    mw_cols = st.columns([1.6, 1, 1, 1])
    mw_cols[0].markdown("<div class='row-label'>Min Window (hrs)</div>",
                        unsafe_allow_html=True,
                        help="Minimum contiguous window duration to be counted "
                             "as operationally useful. Not applicable when the "
                             "campaign is non-interruptible.")
    minwin_vals = []
    for i in range(3):
        if inter_vals[i] == "No":
            mw_cols[i + 1].number_input(
                " ", value=float(dur_vals[i]), key=f"minwin_locked_{i}",
                disabled=True, label_visibility="collapsed",
                help="Locked to Total Work: a non-interruptible campaign needs "
                     "one unbroken window of at least this length.")
            minwin_vals.append(float(dur_vals[i]))
        else:
            minwin_vals.append(
                mw_cols[i + 1].number_input(
                    " ", value=6.0, min_value=1.0, max_value=720.0, step=1.0,
                    key=f"minwin_{i}", label_visibility="collapsed"))

    season_vals = matrix_row("Start Season / Month",
        lambda c, i: _sel(c, i, "season", SEASONS),
        help_txt="Restricts only the campaign START dates to this season/month. "
                 "Windows are searched freely forward from each start.")

    # Low % / High % — two sub-columns per scenario
    perc_cols = st.columns([1.6, 1, 1, 1])
    perc_cols[0].markdown("<div class='row-label'>Low % / High %</div>",
                          unsafe_allow_html=True,
                          help="Percentile bounds for the exceedance statistics")
    low_perc_vals, high_perc_vals = [], []
    for i in range(3):
        sub = perc_cols[i + 1].columns(2)
        low_perc_vals.append(
            sub[0].number_input("Lo", value=10, min_value=1, max_value=49,
                                key=f"low_{i}", label_visibility="collapsed"))
        high_perc_vals.append(
            sub[1].number_input("Hi", value=90, min_value=51, max_value=99,
                                key=f"high_{i}", label_visibility="collapsed"))

    # Wind direction
    wdir_active_vals = matrix_row("Limit Wind Dir?",
        lambda c, i: _chk(c, i, "wdir_active"))

    wdir_cols = st.columns([1.6, 1, 1, 1])
    wdir_cols[0].markdown("<div class='row-label'>Wind Sector (Min / Max °)</div>",
                          unsafe_allow_html=True)
    wdir_min_vals, wdir_max_vals = [], []
    for i in range(3):
        sub = wdir_cols[i + 1].columns(2)
        wdir_min_vals.append(sub[0].number_input("Min", value=0,   min_value=0, max_value=360,
                             key=f"wdir_min_{i}", label_visibility="collapsed",
                             disabled=not wdir_active_vals[i]))
        wdir_max_vals.append(sub[1].number_input("Max", value=360, min_value=0, max_value=360,
                             key=f"wdir_max_{i}", label_visibility="collapsed",
                             disabled=not wdir_active_vals[i]))

    # Wave direction
    vdir_active_vals = matrix_row("Limit Wave Dir?",
        lambda c, i: _chk(c, i, "vdir_active"))

    vdir_cols = st.columns([1.6, 1, 1, 1])
    vdir_cols[0].markdown("<div class='row-label'>Wave Sector (Min / Max °)</div>",
                          unsafe_allow_html=True)
    vdir_min_vals, vdir_max_vals = [], []
    for i in range(3):
        sub = vdir_cols[i + 1].columns(2)
        vdir_min_vals.append(sub[0].number_input("Min", value=0,   min_value=0, max_value=360,
                             key=f"vdir_min_{i}", label_visibility="collapsed",
                             disabled=not vdir_active_vals[i]))
        vdir_max_vals.append(sub[1].number_input("Max", value=360, min_value=0, max_value=360,
                             key=f"vdir_max_{i}", label_visibility="collapsed",
                             disabled=not vdir_active_vals[i]))

    st.divider()


    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — Run
    # ══════════════════════════════════════════════════════════════════════════
    run_clicked = st.button(
        f"▶  RUN COMPARISON  ({N_ITERATIONS:,} iterations per scenario)",
        type="primary",
        use_container_width=True,
        disabled=(merged is None),
    )

    # Validate Min Window against Total Work (interruptible scenarios only —
    # non-interruptible ones are locked in sync above and cannot be wrong).
    bad = [
        f"Scenario {i+1}: Min Window ({minwin_vals[i]:g} hrs) exceeds "
        f"Total Work ({dur_vals[i]:g} hrs)"
        for i in range(3)
        if inter_vals[i] == "Yes" and minwin_vals[i] > dur_vals[i]
    ]
    if bad:
        st.warning(
            "**Check scenario settings — Min Window is larger than Total Work in:**\n\n"
            + "\n".join(f"- {b}" for b in bad)
            + "\n\nWindows long enough to complete the campaign will be discarded "
              "by the Min Window filter, which may give unrealistically long durations."
        )

    if run_clicked and merged is not None:

        # Clear previous results immediately so it is obvious a new run started
        st.session_state.pop("results", None)

        all_params = [
            {
                "hs":           hs_vals[i],
                "tp":           tp_vals[i],
                "wind":         wind_vals[i],
                "curr":         curr_vals[i],
                "dur":          dur_vals[i],
                "min_win":      minwin_vals[i],
                "interruptible": inter_vals[i] == "Yes",
                "season":       season_vals[i],
                "wdir_active":  wdir_active_vals[i],
                "wdir_min":     wdir_min_vals[i],
                "wdir_max":     wdir_max_vals[i],
                "vdir_active":  vdir_active_vals[i],
                "vdir_min":     vdir_min_vals[i],
                "vdir_max":     vdir_max_vals[i],
                "low_perc":     low_perc_vals[i],
                "high_perc":    high_perc_vals[i],
            }
            for i in range(3)
        ]

        status_box = st.empty()
        prog       = st.progress(0.0)
        scenario_results = []

        for i, p in enumerate(all_params):
            status_box.info(
                f"⏳  Running Scenario {i + 1} of 3  "
                f"({N_ITERATIONS:,} Monte Carlo iterations)…"
            )
            results, monthly_oper, msg = run_scenario(merged, p)
            scenario_results.append((results, monthly_oper, p, msg))
            prog.progress((i + 1) / 3.0)

        st.session_state["results"] = scenario_results
        prog.empty()
        status_box.success(
            f"✅  Analysis complete at {datetime.now().strftime('%H:%M:%S')}.")


    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — Results
    # ══════════════════════════════════════════════════════════════════════════
    if "results" in st.session_state:
        st.subheader("③ Results")

        res_list = st.session_state["results"]
        fig = go.Figure()
        x_lo, x_hi = np.inf, 0.0

        table_data = {
            "Metric": [
                "P-Low  hrs (days)",
                "P50  hrs (days)",
                "P-High  hrs (days)",
                "Avg Dur  hrs (days)",
                "Downtime %",
            ]
        }

        metrics_cols = st.columns(3)
        oper_rows = []

        for i, (results, monthly_oper, p, msg) in enumerate(res_list):
            col_key = f"Scenario {i + 1}"
            oper_rows.append(monthly_oper)

            if results is None or len(results) == 0:
                table_data[col_key] = ["—"] * 5
                metrics_cols[i].warning(f"S{i+1}: {msg}")
                continue

            sorted_res  = np.sort(results)
            n           = len(sorted_res)
            exceedance  = (np.arange(n, 0, -1) / n) * 100.0

            fig.add_trace(go.Scatter(
                x=sorted_res,
                y=exceedance,
                name=col_key,
                line=dict(color=COLORS[i], width=3),
                mode="lines",
                hovertemplate=(
                    "<b>" + col_key + "</b><br>"
                    "Duration: %{x:.1f} hrs (%{customdata:.1f} days)<br>"
                    "Exceedance: %{y:.1f}%<extra></extra>"
                ),
                customdata=sorted_res / 24,
            ))

            p_low  = calc_percentile(sorted_res, p["low_perc"])
            p_mid  = calc_percentile(sorted_res, 50)
            p_high = calc_percentile(sorted_res, p["high_perc"])
            avg    = float(np.mean(results))
            dt     = max(0.0, (avg - float(p["dur"])) / avg * 100.0)

            table_data[col_key] = [
                fmt_hrs_days(p_low),
                fmt_hrs_days(p_mid),
                fmt_hrs_days(p_high),
                fmt_hrs_days(avg),
                f"{dt:.1f}%",
            ]

            # Clip x-axis to P0.1–P99.9
            lo_clip = calc_percentile(sorted_res, 0.1)
            hi_clip = calc_percentile(sorted_res, 99.9)
            x_lo = min(x_lo, lo_clip)
            x_hi = max(x_hi, hi_clip)

            # Key metric callouts
            with metrics_cols[i]:
                st.markdown(
                    f"<span style='color:{COLORS[i]};font-weight:700;font-size:1.05em'>"
                    f"Scenario {i+1}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div style='border-top:4px solid {COLORS[i]};margin-bottom:6px'></div>",
                    unsafe_allow_html=True,
                )
                ma, mb, mc_ = st.columns(3)
                ma.metric("P50", f"{p_mid:.0f} h", f"{p_mid/24:.1f} d")
                mb.metric("Avg", f"{avg:.0f} h",   f"{avg/24:.1f} d")
                mc_.metric("Downtime", f"{dt:.1f}%")

        # ── Chart ──────────────────────────────────────────────────────────────
        if np.isfinite(x_lo) and x_hi > x_lo:
            margin = (x_hi - x_lo) * 0.02
            fig.update_xaxes(range=[max(0, x_lo - margin), x_hi + margin])

        fig.update_layout(
            xaxis_title="Total Campaign Duration (hrs)",
            yaxis_title="Probability of Exceedance (%)",
            yaxis=dict(range=[0, 100]),
            legend=dict(
                x=0.99, xanchor="right", y=0.99, yanchor="top",
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#cccccc", borderwidth=1,
                font=dict(size=13, color="#111111"),
            ),
            height=460,
            margin=dict(l=60, r=30, t=30, b=60),
            plot_bgcolor="#F8F9FA",
            paper_bgcolor="white",
            hovermode="x unified",
            font=dict(color="#111111", size=13),
        )
        fig.update_xaxes(
            showgrid=True, gridcolor="#cccccc", gridwidth=1,
            zeroline=False,
            linecolor="#333333", linewidth=1.5,
            tickfont=dict(size=12, color="#111111"),
            title_font=dict(size=14, color="#111111"),
        )
        fig.update_yaxes(
            showgrid=True, gridcolor="#cccccc", gridwidth=1,
            linecolor="#333333", linewidth=1.5,
            tickfont=dict(size=12, color="#111111"),
            title_font=dict(size=14, color="#111111"),
        )

        chart_col, tbl_col = st.columns([3, 2])

        with chart_col:
            tab_curve, tab_matrix = st.tabs(
                ["📈  Exceedance Curve", "🗓  Monthly Operability"])

            with tab_curve:
                st.plotly_chart(fig, use_container_width=True)

            with tab_matrix:
                oper_arr = np.array(oper_rows, dtype=float)   # 3 x 12

                # Auto-scale colour limits across ALL scenarios, so the full
                # colour range is used while scenarios stay comparable on a
                # single shared scale.
                if np.all(np.isnan(oper_arr)):
                    c_min, c_max = 0.0, 100.0
                else:
                    c_min = float(np.nanmin(oper_arr))
                    c_max = float(np.nanmax(oper_arr))
                    if c_max <= c_min:
                        c_min, c_max = max(0.0, c_min - 1), min(100.0, c_max + 1)

                heat = go.Figure(go.Heatmap(
                    z=oper_arr,
                    x=MONTHS,
                    y=["Scenario 1", "Scenario 2", "Scenario 3"],
                    zmin=c_min, zmax=c_max,
                    colorscale="RdYlGn",
                    xgap=2, ygap=2,
                    colorbar=dict(
                        title=dict(text="Operability (%)", side="right"),
                        thickness=14, len=0.85,
                    ),
                    text=[[("" if np.isnan(v) else f"{v:.0f}") for v in row]
                          for row in oper_arr],
                    texttemplate="%{text}",
                    textfont=dict(size=12),
                    hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1f}%<extra></extra>",
                ))
                heat.update_layout(
                    title=dict(
                        text="Monthly Operability — % of hours within usable weather windows"
                             "<br><span style='font-size:11px;color:#666'><i>Hours meeting all "
                             "limits AND inside a window ≥ Min Window duration</i></span>",
                        font=dict(size=14, color="#111111"), x=0.0, xanchor="left",
                    ),
                    height=430,
                    margin=dict(l=90, r=30, t=75, b=40),
                    paper_bgcolor="white", plot_bgcolor="white",
                    font=dict(color="#111111", size=12),
                    xaxis=dict(side="top", tickfont=dict(size=12, color="#111111")),
                    yaxis=dict(autorange="reversed",
                               tickfont=dict(size=12, color="#111111")),
                )
                st.plotly_chart(heat, use_container_width=True)
                st.caption(
                    "Counts only hours inside windows that satisfy the Min Window "
                    "setting, so isolated feasible hours in unusably short gaps are "
                    "excluded. Aggregated across every year in the hindcast."
                )

        with tbl_col:
            st.markdown("**Campaign Duration Statistics**")
            st.caption("Values shown as  *hrs (days)*  |  Low/High % are per-scenario settings")
            df_tbl = pd.DataFrame(table_data).set_index("Metric")
            st.dataframe(df_tbl, use_container_width=True, height=215)

            # CSV download
            csv = df_tbl.to_csv().encode()
            st.download_button(
                "⬇  Download results CSV",
                data=csv,
                file_name="weather_window_results.csv",
                mime="text/csv",
            )

        st.divider()
        st.caption(
            "**Methodology:** Operability windows identified from the full hindcast record. "
            "Season/Month selection restricts only the Monte Carlo campaign start dates — "
            "windows are searched forward continuously from each start regardless of month boundary. "
            f"{N_ITERATIONS:,} iterations per scenario. "
            "P-Low / P-High are nearest-rank percentiles of the simulated duration distribution."
        )
