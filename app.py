"""
app.py  –  Marine Survey Weather Window Tool  (v1.2)
Streamlit web application.

v1.2 replaces the fixed three-CSV loader with a universal data layer: any
mix of .csv / .txt / .asc / .dat / .xlsx / .nc, variables identified
automatically and confirmed by the user, a data inventory, and a scenario
matrix that adapts to whatever variables the dataset actually contains.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from io_readers import read_any, resolve_time_axis, describe_time
from variable_map import (
    CANONICAL, COMPONENTS, DISPLAY_ORDER, propose_mapping, apply_mapping,
    MappingRow,
)
from resampling import to_hourly, merge_sources
from qc import build_inventory, prepare_for_analysis
from engine import (
    run_scenario, calc_percentile, fmt_hrs_days, N_ITERATIONS, MONTHS,
    CONSTRAINT_COLS,
)

st.set_page_config(page_title="Marine Survey Weather Window Tool",
                   page_icon="🌊", layout="wide")

COLORS = ["#1565C0", "#C62828", "#2E7D32"]
SEASONS = ["All-Year", "Summer (Apr-Sep)", "Winter (Oct-Mar)"] + MONTHS

LIMIT_ROWS = [
    ("hs",   "Hs Limit (m)",        2.0, 0.0, 20.0, 0.5,
     "Maximum significant wave height for operations"),
    ("tp",   "Tp Limit (s)",       14.0, 0.0, 30.0, 1.0,
     "Maximum peak wave period for operations"),
    ("wind", "Wind Limit (m/s)",   15.0, 0.0, 60.0, 1.0,
     "Maximum wind speed for operations"),
    ("curr", "Current Limit (m/s)", 1.5, 0.0, 10.0, 0.1,
     "Maximum current speed for operations"),
]
SECTOR_ROWS = [("wdir", "Wind Sector", "Limit Wind Dir?"),
               ("vdir", "Wave Sector", "Limit Wave Dir?")]

st.markdown("""
<style>
  .row-label { font-weight:600; font-size:0.9em; padding-top:6px; }
  .sc-header { font-weight:700; font-size:1.05em; padding:4px 0 2px 0;
               border-bottom:2px solid; margin-bottom:6px; text-align:center; }
  div[data-testid="stMetric"] { background:#fff !important; border-radius:8px;
       padding:10px 12px; border:1px solid #ddd; }
  div[data-testid="stMetric"] label { color:#444 !important;
       font-size:0.82em !important; font-weight:600 !important; }
  div[data-testid="stMetric"] [data-testid="stMetricValue"] { color:#111 !important;
       font-size:1.3em !important; font-weight:700 !important; }
  div[data-testid="stMetric"] [data-testid="stMetricDelta"] { color:#333 !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# Authentication
# ══════════════════════════════════════════════════════════════════════════

def check_credentials(username: str, password: str) -> bool:
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
        st.markdown("""
            <div style='text-align:center;margin-bottom:8px'>
                <span style='font-size:3em'>🌊</span></div>
            <h2 style='text-align:center;color:#1565C0;margin-bottom:2px'>
                Marine Survey Weather Window Tool</h2>
            <p style='text-align:center;color:#666;font-size:0.95em;margin-bottom:28px'>
                Venterra Group &nbsp;·&nbsp; Restricted Access</p>
        """, unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("#### Sign in to continue")
            u = st.text_input("Username", key="login_user")
            p = st.text_input("Password", type="password", key="login_pass")
            if st.button("Sign in", type="primary", use_container_width=True):
                if u and p:
                    if check_credentials(u, p):
                        st.session_state["authenticated"] = True
                        st.session_state["current_user"] = u
                        st.rerun()
                    else:
                        st.error("Incorrect username or password.")
                else:
                    st.warning("Please enter both username and password.")
        st.markdown(
            "<p style='text-align:center;color:#aaa;font-size:0.8em;margin-top:18px'>"
            "Access is restricted to authorised users only.<br>"
            "Contact Venterra Group to request access.</p>",
            unsafe_allow_html=True)


if not st.session_state.get("authenticated", False):
    show_login_screen()
    st.stop()


# ══════════════════════════════════════════════════════════════════════════
# Header
# ══════════════════════════════════════════════════════════════════════════

title_col, out_col = st.columns([8, 1])
with title_col:
    st.title("🌊 Marine Survey Weather Window Tool")
    st.caption("Monte Carlo campaign duration analysis — up to 3 concurrent scenarios")
with out_col:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    st.caption(f"👤 {st.session_state.get('current_user', '')}")
    if st.button("Sign out", use_container_width=True):
        for k in ("authenticated", "current_user", "dataset", "results"):
            st.session_state.pop(k, None)
        st.rerun()

tab_data, tab_analysis, tab_docs = st.tabs(
    ["📁  Data", "📊  Analysis", "📖  Instructions & Methodology"])


# ══════════════════════════════════════════════════════════════════════════
# TAB 1 — DATA
# ══════════════════════════════════════════════════════════════════════════

with tab_data:
    st.subheader("① Upload hindcast data")
    st.caption(
        "Any mix of **.csv .txt .asc .dat .xlsx .nc** — one file or several. "
        "Variables are identified automatically and shown for you to confirm. "
        "Timestamps are used exactly as written, with no timezone conversion."
    )

    files = st.file_uploader(
        "Drop your files here",
        type=["csv", "txt", "asc", "dat", "tsv", "xlsx", "xls", "nc"],
        accept_multiple_files=True)

    if not files:
        st.info("Upload at least one file to begin.")
        st.session_state.pop("dataset", None)
    else:
        sources = []
        with st.spinner("Reading files…"):
            for f in files:
                try:
                    raw = read_any(f.name, f.getvalue())
                    frame, tinfo = resolve_time_axis(raw)
                    sources.append((f.name, frame, raw.var_attrs))
                    with st.expander(f"✅  {f.name}  —  {describe_time(tinfo)}"):
                        for n in raw.notes + tinfo.notes:
                            st.caption(f"• {n}")
                        for w in raw.warnings + tinfo.warnings:
                            st.warning(w)
                        st.dataframe(frame.head(5), use_container_width=True)
                except Exception as exc:
                    st.error(f"**{f.name}** — {exc}")

        if sources:
            st.divider()
            st.subheader("② Confirm variable mapping")
            st.caption(
                "Auto-detection is a starting point, not an authority — a "
                "mis-identified column produces results that look entirely "
                "plausible and are wrong. Please check before continuing."
            )

            prop = propose_mapping(sources)

            options, lookup = ["(not used)"], {}
            for name, frame, _ in sources:
                for c in frame.columns:
                    lbl = f"{c}  ←  {name}"
                    options.append(lbl)
                    lookup[lbl] = (name, str(c))
            proposed = {r.canonical: f"{r.source_col}  ←  {r.source_file}"
                        for r in prop.rows}

            disp = []
            for r in prop.rows:
                var = CANONICAL.get(r.canonical)
                disp.append({
                    "Variable": r.canonical,
                    "Description": var.label if var else "vector component",
                    "Source column": r.source_col,
                    "File": r.source_file,
                    "Units": r.unit_detected or "—",
                    "Conversion": r.conversion or "—",
                    "Convention": r.convention or "—",
                    "Coverage %": round(r.stats.get("coverage", 0), 1),
                    "Confidence": f"{r.score:.0f}",
                    "Matched on": r.reason,
                })
            if disp:
                st.dataframe(pd.DataFrame(disp), use_container_width=True,
                             hide_index=True)

            for r in prop.rows:
                for w in r.warnings:
                    st.warning(f"**{r.canonical}** — {w}")
            for w in prop.warnings:
                st.warning(w)
            for n in prop.notes:
                st.caption(f"• {n}")
            if prop.unmapped:
                with st.expander(f"{len(prop.unmapped)} column(s) not recognised"):
                    st.caption(", ".join(f"{c} ({f})" for f, c in prop.unmapped))

            with st.expander("✏️  Adjust mapping"):
                st.caption(
                    "Override any auto-detected choice. Vector components (u/v) "
                    "are only used when the matching speed and direction are absent."
                )
                overrides, grid = {}, st.columns(3)
                for i, key in enumerate(list(DISPLAY_ORDER) + list(COMPONENTS)):
                    var = CANONICAL.get(key)
                    label = f"{key} — {var.label}" if var else f"{key} (component)"
                    default = proposed.get(key, "(not used)")
                    idx = options.index(default) if default in options else 0
                    overrides[key] = grid[i % 3].selectbox(
                        label, options, index=idx, key=f"map_{key}")

            edited = []
            for key, sel in overrides.items():
                if sel == "(not used)":
                    continue
                fname, col = lookup[sel]
                found = next((r for r in prop.rows if r.canonical == key
                              and r.source_col == col and r.source_file == fname), None)
                edited.append(found if found is not None else MappingRow(
                    canonical=key, source_file=fname, source_col=col,
                    score=0.0, reason="set manually",
                    convention=(CANONICAL[key].convention if key in CANONICAL else None)))
            prop.rows = edited

            if not prop.resolved_variables():
                st.error("No usable variables. Adjust the mapping above.")
            else:
                st.divider()
                st.subheader("③ Data inventory")

                with st.spinner("Normalising to hourly and merging…"):
                    mapped, derive_notes = apply_mapping(sources, prop)
                    hourly, infos = [], []
                    for name, frame in mapped:
                        h, ri = to_hourly(name, frame)
                        hourly.append((name, h))
                        infos.append(ri)
                    merged, merge_notes = merge_sources(hourly)

                active_default = [c for c in merged.columns
                                  if CANONICAL.get(c) and CANONICAL[c].constraint]
                inv = build_inventory(merged, prop.rows, active_cols=active_default)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Record length", f"{inv.years:.1f} yrs")
                m2.metric("Hourly steps", f"{len(merged):,}")
                m3.metric("Completeness", f"{inv.completeness:.1f}%")
                m4.metric("Variables", f"{len(merged.columns)}")
                st.caption(f"**{inv.start:%d %b %Y}**  →  **{inv.end:%d %b %Y}**")

                for w in inv.warnings:
                    st.warning(w)

                iv1, iv2 = st.columns([3, 2])
                with iv1:
                    st.markdown("**Variables**")
                    st.dataframe(inv.variables, use_container_width=True,
                                 hide_index=True, height=280)
                with iv2:
                    st.markdown("**Record by calendar month**")
                    st.dataframe(inv.monthly, use_container_width=True,
                                 hide_index=True, height=280)

                if len(inv.gaps):
                    with st.expander(f"🕳  {len(inv.gaps)} gap(s) longer than one hour"):
                        g = inv.gaps.copy()
                        g["From"] = g["From"].dt.strftime("%Y-%m-%d %H:%M")
                        g["To"] = g["To"].dt.strftime("%Y-%m-%d %H:%M")
                        st.dataframe(g, use_container_width=True, hide_index=True)
                else:
                    st.success("No gaps longer than one hour.")

                with st.expander("⚙️  Processing log"):
                    for r in infos:
                        st.markdown(f"**{r.source}** — {r.mode}, "
                                    f"{r.n_in:,} → {r.n_out:,} rows")
                        for n in r.notes:
                            st.caption(f"• {n}")
                        for w in r.warnings:
                            st.warning(w)
                    for n in derive_notes + merge_notes:
                        st.caption(f"• {n}")

                st.session_state["dataset"] = merged
                st.session_state["inventory"] = inv
                st.success("Dataset ready — open the **Analysis** tab.")


# ══════════════════════════════════════════════════════════════════════════
# TAB 2 — ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

with tab_analysis:
    merged = st.session_state.get("dataset")

    if merged is None:
        st.info("Load a dataset on the **Data** tab first.")
    else:
        avail_limits = [r for r in LIMIT_ROWS
                        if CONSTRAINT_COLS[r[0]] in merged.columns]
        avail_sectors = [s for s in SECTOR_ROWS
                         if CONSTRAINT_COLS[s[0]] in merged.columns]
        missing = [r[1] for r in LIMIT_ROWS
                   if CONSTRAINT_COLS[r[0]] not in merged.columns]
        if missing:
            st.info("Not present in this dataset, so not offered as "
                    "constraints: " + ", ".join(missing))

        st.subheader("① Scenario parameters")

        hdr = st.columns([1.7, 1, 1, 1])
        for i in range(3):
            hdr[i + 1].markdown(
                f"<div class='sc-header' style='border-color:{COLORS[i]};"
                f"color:{COLORS[i]}'>Scenario {i+1}</div>", unsafe_allow_html=True)

        def row(label, help_txt=None):
            c = st.columns([1.7, 1, 1, 1])
            c[0].markdown(f"<div class='row-label'>{label}</div>",
                          unsafe_allow_html=True, help=help_txt)
            return c

        limit_vals, limit_on = {}, {}
        for key, label, dflt, lo, hi, step, helptxt in avail_limits:
            c = row(label, helptxt)
            limit_vals[key], limit_on[key] = [], []
            for i in range(3):
                sub = c[i + 1].columns([1, 2.2])
                on = sub[0].checkbox("", value=True, key=f"on_{key}_{i}",
                                     label_visibility="collapsed")
                v = sub[1].number_input("", value=dflt, min_value=lo, max_value=hi,
                                        step=step, key=f"lim_{key}_{i}",
                                        disabled=not on, label_visibility="collapsed")
                limit_on[key].append(on)
                limit_vals[key].append(v)

        c = row("Total Work (hrs)", "Total productive work hours required")
        dur_vals = [c[i + 1].number_input("", value=72.0, min_value=1.0,
                    max_value=8760.0, step=1.0, key=f"dur_{i}",
                    label_visibility="collapsed") for i in range(3)]

        c = row("Interruptible", "Can the campaign be split across windows?")
        inter_vals = [c[i + 1].selectbox("", ["Yes", "No"], key=f"int_{i}",
                      label_visibility="collapsed") for i in range(3)]

        c = row("Min Window (hrs)",
                "Minimum contiguous window duration. Not applicable when the "
                "campaign is non-interruptible.")
        minwin_vals = []
        for i in range(3):
            if inter_vals[i] == "No":
                c[i + 1].number_input("", value=float(dur_vals[i]), disabled=True,
                                      key=f"mwlock_{i}", label_visibility="collapsed",
                                      help="Locked to Total Work: a non-interruptible "
                                           "campaign needs one unbroken window of at "
                                           "least this length.")
                minwin_vals.append(float(dur_vals[i]))
            else:
                minwin_vals.append(c[i + 1].number_input(
                    "", value=6.0, min_value=1.0, max_value=720.0, step=1.0,
                    key=f"mw_{i}", label_visibility="collapsed"))

        c = row("Start Season / Month",
                "Restricts only the campaign START dates. Windows are searched "
                "forward continuously from each start.")
        season_vals = [c[i + 1].selectbox("", SEASONS, key=f"sea_{i}",
                       label_visibility="collapsed") for i in range(3)]

        c = row("Low % / High %", "Percentile bounds for the results table")
        low_vals, high_vals = [], []
        for i in range(3):
            sub = c[i + 1].columns(2)
            low_vals.append(sub[0].number_input("", value=10, min_value=1,
                            max_value=49, key=f"lo_{i}", label_visibility="collapsed"))
            high_vals.append(sub[1].number_input("", value=90, min_value=51,
                             max_value=99, key=f"hi_{i}", label_visibility="collapsed"))

        sector_on, sector_min, sector_max = {}, {}, {}
        for key, label, toggle_label in avail_sectors:
            c = row(toggle_label)
            sector_on[key] = [c[i + 1].checkbox("Active", key=f"secon_{key}_{i}")
                              for i in range(3)]
            c = row(f"{label} (Min / Max °)")
            sector_min[key], sector_max[key] = [], []
            for i in range(3):
                sub = c[i + 1].columns(2)
                sector_min[key].append(sub[0].number_input(
                    "", value=0, min_value=0, max_value=360, key=f"smin_{key}_{i}",
                    disabled=not sector_on[key][i], label_visibility="collapsed"))
                sector_max[key].append(sub[1].number_input(
                    "", value=360, min_value=0, max_value=360, key=f"smax_{key}_{i}",
                    disabled=not sector_on[key][i], label_visibility="collapsed"))

        st.divider()
        st.subheader("② Missing data policy")
        choice = st.radio("How should hours with missing data be treated?",
                          ["Treat as non-operable", "Exclude from the record"],
                          horizontal=True,
                          help="Only matters where an active constraint has gaps.")
        if choice.startswith("Treat"):
            policy = "non-operable"
            st.caption("Hours missing any active variable are kept and counted as "
                       "unworkable. Conservative, and keeps the time axis continuous.")
        else:
            policy = "exclude"
            st.caption("Those hours are removed entirely and excluded from the "
                       "operability denominator. Fairer when a variable is patchy. "
                       "Window building automatically enforces timestamp contiguity "
                       "so hours far apart in time cannot merge into one window.")

        st.divider()
        bad = [f"Scenario {i+1}: Min Window ({minwin_vals[i]:g} h) exceeds "
               f"Total Work ({dur_vals[i]:g} h)"
               for i in range(3)
               if inter_vals[i] == "Yes" and minwin_vals[i] > dur_vals[i]]
        if bad:
            st.warning("**Check scenario settings:**\n\n"
                       + "\n".join(f"- {b}" for b in bad)
                       + "\n\nWindows long enough to complete the campaign would "
                         "be discarded by the Min Window filter.")

        none_on = [i + 1 for i in range(3)
                   if not any(limit_on[k][i] for k in limit_vals)]
        if none_on:
            st.error(f"Scenario(s) {none_on} have no active limits — every hour "
                     f"would be workable. Enable at least one.")

        run = st.button(
            f"▶  RUN COMPARISON  ({N_ITERATIONS:,} iterations per scenario)",
            type="primary", use_container_width=True, disabled=bool(none_on))

        if run:
            st.session_state.pop("results", None)
            active_cols = sorted({CONSTRAINT_COLS[k] for k in limit_vals
                                  if any(limit_on[k])} & set(merged.columns))
            frame, need_chk, pol_notes = prepare_for_analysis(
                merged, active_cols, policy)
            for n in pol_notes:
                st.caption(f"• {n}")

            params = [{
                "limits": {k: limit_vals[k][i] for k in limit_vals if limit_on[k][i]},
                "sectors": {k: (sector_min[k][i], sector_max[k][i])
                            for k in sector_on if sector_on[k][i]},
                "min_win": minwin_vals[i],
                "dur": dur_vals[i],
                "interruptible": inter_vals[i] == "Yes",
                "season": season_vals[i],
                "low_perc": low_vals[i],
                "high_perc": high_vals[i],
            } for i in range(3)]

            box, prog, out = st.empty(), st.progress(0.0), []
            for i, p in enumerate(params):
                box.info(f"⏳  Running Scenario {i+1} of 3  "
                         f"({N_ITERATIONS:,} iterations)…")
                res, oper, msg, applied = run_scenario(
                    frame, p, contiguity_check=need_chk)
                out.append((res, oper, p, msg, applied))
                prog.progress((i + 1) / 3.0)
            st.session_state["results"] = out
            prog.empty()
            box.success(f"✅  Analysis complete at {datetime.now():%H:%M:%S}.")

        if "results" in st.session_state:
            st.divider()
            st.subheader("③ Results")

            res_list = st.session_state["results"]
            fig = go.Figure()
            x_lo, x_hi = np.inf, 0.0
            table = {"Metric": ["P-Low", "P50", "P-High", "Avg Dur", "Downtime %"]}
            oper_rows, mcols = [], st.columns(3)

            for i, (results, oper, p, msg, applied) in enumerate(res_list):
                key = f"Scenario {i+1}"
                oper_rows.append(oper)
                if results is None or len(results) == 0:
                    table[key] = ["—"] * 5
                    mcols[i].warning(f"S{i+1}: {msg}")
                    continue

                s = np.sort(results)
                n = len(s)
                fig.add_trace(go.Scatter(
                    x=s, y=(np.arange(n, 0, -1) / n) * 100.0, name=key,
                    line=dict(color=COLORS[i], width=3), mode="lines",
                    customdata=s / 24,
                    hovertemplate=f"<b>{key}</b><br>%{{x:.1f}} hrs "
                                  f"(%{{customdata:.1f}} d)<br>"
                                  f"Exceedance %{{y:.1f}}%<extra></extra>"))

                pl = calc_percentile(s, p["low_perc"])
                pm = calc_percentile(s, 50)
                ph = calc_percentile(s, p["high_perc"])
                avg = float(np.mean(results))
                dt = max(0.0, (avg - float(p["dur"])) / avg * 100.0)
                table[key] = [fmt_hrs_days(pl), fmt_hrs_days(pm),
                              fmt_hrs_days(ph), fmt_hrs_days(avg), f"{dt:.1f}%"]
                x_lo = min(x_lo, calc_percentile(s, 0.1))
                x_hi = max(x_hi, calc_percentile(s, 99.9))

                with mcols[i]:
                    st.markdown(f"<span style='color:{COLORS[i]};font-weight:700;"
                                f"font-size:1.05em'>Scenario {i+1}</span>",
                                unsafe_allow_html=True)
                    st.markdown(f"<div style='border-top:4px solid {COLORS[i]};"
                                f"margin-bottom:6px'></div>", unsafe_allow_html=True)
                    a, b, c_ = st.columns(3)
                    a.metric("P50", f"{pm:.0f} h", f"{pm/24:.1f} d")
                    b.metric("Avg", f"{avg:.0f} h", f"{avg/24:.1f} d")
                    c_.metric("Downtime", f"{dt:.1f}%")
                    st.caption("Applied: " + (", ".join(applied) or "none"))

            if np.isfinite(x_lo) and x_hi > x_lo:
                mg = (x_hi - x_lo) * 0.02
                fig.update_xaxes(range=[max(0, x_lo - mg), x_hi + mg])
            fig.update_layout(
                xaxis_title="Total Campaign Duration (hrs)",
                yaxis_title="Probability of Exceedance (%)",
                yaxis=dict(range=[0, 100]), height=460,
                margin=dict(l=60, r=30, t=30, b=60),
                plot_bgcolor="#F8F9FA", paper_bgcolor="white",
                hovermode="x unified", font=dict(color="#111", size=13),
                legend=dict(x=0.99, xanchor="right", y=0.99, yanchor="top",
                            bgcolor="rgba(255,255,255,0.85)",
                            bordercolor="#ccc", borderwidth=1))
            fig.update_xaxes(showgrid=True, gridcolor="#ccc", zeroline=False,
                             linecolor="#333", linewidth=1.5)
            fig.update_yaxes(showgrid=True, gridcolor="#ccc",
                             linecolor="#333", linewidth=1.5)

            cc, tc = st.columns([3, 2])
            with cc:
                t1, t2 = st.tabs(["📈  Exceedance Curve", "🗓  Monthly Operability"])
                with t1:
                    st.plotly_chart(fig, use_container_width=True)
                with t2:
                    arr = np.array(oper_rows, dtype=float)
                    if np.all(np.isnan(arr)):
                        lo_c, hi_c = 0.0, 100.0
                    else:
                        lo_c, hi_c = float(np.nanmin(arr)), float(np.nanmax(arr))
                        if hi_c <= lo_c:
                            lo_c, hi_c = max(0.0, lo_c - 1), min(100.0, hi_c + 1)
                    heat = go.Figure(go.Heatmap(
                        z=arr, x=MONTHS,
                        y=[f"Scenario {i+1}" for i in range(len(arr))],
                        zmin=lo_c, zmax=hi_c, colorscale="RdYlGn", xgap=2, ygap=2,
                        text=[[("" if np.isnan(v) else f"{v:.0f}") for v in r]
                              for r in arr],
                        texttemplate="%{text}", textfont=dict(size=12),
                        colorbar=dict(title=dict(text="Operability (%)", side="right"),
                                      thickness=14),
                        hovertemplate="<b>%{y}</b><br>%{x}: %{z:.1f}%<extra></extra>"))
                    heat.update_layout(
                        title=dict(text="Monthly Operability — % of hours within "
                                        "usable weather windows",
                                   font=dict(size=14, color="#111"), x=0.0),
                        height=430, margin=dict(l=90, r=30, t=60, b=40),
                        paper_bgcolor="white", plot_bgcolor="white",
                        xaxis=dict(side="top"), yaxis=dict(autorange="reversed"),
                        font=dict(color="#111", size=12))
                    st.plotly_chart(heat, use_container_width=True)
                    st.caption("Counts only hours inside windows satisfying the Min "
                               "Window setting. Aggregated across every year in the "
                               "record.")

            with tc:
                st.markdown("**Campaign Duration Statistics**")
                st.caption("Values shown as *hrs (days)*")
                df = pd.DataFrame(table).set_index("Metric")
                st.dataframe(df, use_container_width=True, height=215)
                st.download_button("⬇  Download results CSV", df.to_csv().encode(),
                                   "weather_window_results.csv", "text/csv")


# ══════════════════════════════════════════════════════════════════════════
# TAB 3 — DOCUMENTATION
# ══════════════════════════════════════════════════════════════════════════

with tab_docs:
    try:
        path = __file__.rsplit("/", 1)[0] + "/README.md"
        with open(path, "r") as f:
            st.markdown(f.read())
    except Exception:
        st.info("Documentation file not found.")
