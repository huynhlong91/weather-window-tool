"""
engine.py  –  Marine Survey Weather Window Tool
Data processing, operability analysis and Monte Carlo engine.

Python port of WeatherWindowTool.m / runMonteCarlo.m (MATLAB v1.1).

The Monte Carlo is fully vectorised across iterations, which is what makes
1,000,000 iterations viable in a browser-hosted app (the equivalent
scalar loop would take ~30 s per scenario).
"""

import numpy as np
import pandas as pd
import streamlit as st

N_ITERATIONS = 1_000_000

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONTH_MAP = {m: i + 1 for i, m in enumerate(MONTHS)}


def _to_ns(x) -> np.ndarray:
    """
    Convert a datetime array/index to int64 nanoseconds since epoch.
    Forces dtype='datetime64[ns]' first: pandas 2.x stores datetimes as
    datetime64[us] internally, which would silently break arithmetic
    against NS_PER_HOUR if cast directly.
    """
    return np.asarray(x, dtype='datetime64[ns]').astype(np.int64)


# ══════════════════════════════════════════════════════════════════════════
# 1.  DATA LOADING & MERGING
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def load_and_merge(hydro_bytes: bytes, wave_bytes: bytes, wind_bytes: bytes) -> pd.DataFrame:
    """
    Equivalent to loadData() in WeatherWindowTool.m.

    1. Read the three CSV files, auto-detecting the datetime column.
    2. Aggregate hydrodynamics from 20-min to hourly, keeping the row with
       the highest CSpd in each hour (peak current loading, not averaged).
    3. Merge on the intersection of timestamps (no interpolation, so
       direction columns are never corrupted across the 0/360 boundary).
    """
    from io import BytesIO

    def _read(b: bytes) -> pd.DataFrame:
        df = pd.read_csv(BytesIO(b))
        time_col = next(
            (c for c in df.columns
             if 'time' in c.lower() or 'timestamp' in c.lower()), None)
        if time_col is None:
            raise ValueError(
                "No DateTime/Timestamp column found. "
                "The column name must contain 'Time' or 'Timestamp'.")
        df[time_col] = pd.to_datetime(df[time_col])
        return df.set_index(time_col).sort_index()

    df_hydro = _read(hydro_bytes)
    df_wave  = _read(wave_bytes)
    df_wind  = _read(wind_bytes)

    # Hourly aggregation: sort by CSpd descending, keep first row per hour
    df_hydro = df_hydro.copy()
    df_hydro.index = df_hydro.index.floor('h')
    sort_key = -df_hydro['CSpd'].fillna(-np.inf).values
    df_hydro = df_hydro.iloc[np.argsort(sort_key, kind='stable')]
    df_hydro_hourly = (df_hydro[~df_hydro.index.duplicated(keep='first')]
                       [['CSpd', 'CDir']].sort_index())

    wave_cols = [c for c in ['Hs', 'Hmax', 'Tz', 'Tp', 'Tm', 'WaveDir']
                 if c in df_wave.columns]
    wind_cols = [c for c in ['WSpd10', 'WDir10'] if c in df_wind.columns]

    if 'Hs' not in wave_cols:
        raise ValueError("Waves CSV must contain an 'Hs' column.")
    if 'WSpd10' not in wind_cols:
        raise ValueError("Winds CSV must contain a 'WSpd10' column.")

    merged = (df_hydro_hourly
              .join(df_wave[wave_cols], how='inner')
              .join(df_wind[wind_cols], how='inner'))
    return merged.sort_index()


# ══════════════════════════════════════════════════════════════════════════
# 2.  OPERABILITY ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def find_weather_windows(merged: pd.DataFrame, params: dict):
    """
    Identify contiguous feasible windows from the FULL unfiltered hindcast,
    and compute window-filtered monthly operability.

    Returns
    -------
    win_table    : DataFrame['StartTime','Duration']  (None if no windows)
    monthly_oper : (12,) array of percentages, NaN where no data

    Monthly operability counts only hours falling INSIDE a retained window
    (one that satisfies Min Window). Isolated feasible hours in unusably
    short gaps are excluded, so the figure is consistent with the campaign
    statistics rather than contradicting them.
    """
    hs = merged['Hs'].values
    tp = merged['Tp'].values if 'Tp' in merged.columns else np.zeros(len(merged))
    wd = merged['WSpd10'].values
    cu = merged['CSpd'].values

    feasible = ((hs <= params['hs']) & (tp <= params['tp']) &
                (wd <= params['wind']) & (cu <= params['curr']))

    # Directional sectors — wrap-through-north safe
    if params['wdir_active'] and 'WDir10' in merged.columns:
        v = merged['WDir10'].values
        lo, hi = params['wdir_min'], params['wdir_max']
        feasible &= ((v >= lo) & (v <= hi)) if lo <= hi else ((v >= lo) | (v <= hi))

    if params['vdir_active'] and 'WaveDir' in merged.columns:
        v = merged['WaveDir'].values
        lo, hi = params['vdir_min'], params['vdir_max']
        feasible &= ((v >= lo) & (v <= hi)) if lo <= hi else ((v >= lo) | (v <= hi))

    # Contiguous runs of feasible hours
    pad    = np.concatenate([[0], feasible.astype(np.int8), [0]])
    diff   = np.diff(pad)
    starts = np.where(diff == 1)[0]
    ends   = np.where(diff == -1)[0]
    durs   = (ends - starts).astype(np.float64)

    keep = durs >= params['min_win']

    # ── Window-filtered monthly operability ───────────────────────────────
    n = len(merged)
    marks = np.zeros(n + 1, dtype=np.int32)
    if np.any(keep):
        np.add.at(marks, starts[keep], 1)
        np.add.at(marks, ends[keep],  -1)
    usable = np.cumsum(marks[:-1]) > 0

    months = merged.index.month.values
    monthly_oper = np.full(12, np.nan)
    for m in range(1, 13):
        in_m = (months == m)
        if in_m.any():
            monthly_oper[m - 1] = 100.0 * usable[in_m].sum() / in_m.sum()

    if not np.any(keep):
        return None, monthly_oper

    win_table = pd.DataFrame({
        'StartTime': merged.index[starts[keep]],
        'Duration':  durs[keep],
    })
    return win_table, monthly_oper


# ══════════════════════════════════════════════════════════════════════════
# 3.  MONTE CARLO  (vectorised across iterations)
# ══════════════════════════════════════════════════════════════════════════

def run_monte_carlo(merged: pd.DataFrame,
                    win_table: pd.DataFrame,
                    settings: dict) -> np.ndarray:
    """
    Monte Carlo campaign-duration estimator — vectorised port of
    runMonteCarlo.m.

    Rather than looping over iterations one at a time, all iterations
    advance in lock-step: each pass of the outer loop resolves "find the
    next window" for every still-active iteration simultaneously via a
    vectorised np.searchsorted. The outer loop runs only as many times as
    the longest campaign needs windows (typically tens), so 1,000,000
    iterations complete in about a second instead of ~30.

    Season/Month logic is unchanged: startMonths restricts ONLY which hours
    are eligible as campaign starts. Once started, a campaign searches the
    full window table freely forward in continuous time, so a March start
    correctly uses April/May/June windows.
    """
    total_hours   = float(settings['totalHours'])
    interruptible = bool(settings['isInterruptible'])
    n_iter        = int(settings['numIterations'])
    start_months  = settings['startMonths']

    if win_table is None or len(win_table) == 0:
        return np.full(n_iter, np.inf)

    # Eligible start-time pool
    row_times  = merged.index
    limit_idx  = max(1, len(row_times) - int(total_hours * 5))
    candidates = row_times[:limit_idx]
    if start_months:
        candidates = candidates[candidates.month.isin(start_months)]
    if len(candidates) == 0:
        return np.full(n_iter, np.inf)

    NS_PER_HOUR = np.int64(3_600_000_000_000)
    YEAR_NS     = np.int64(365 * 24) * NS_PER_HOUR

    cand_ns  = _to_ns(candidates)
    win_ns   = _to_ns(win_table['StartTime'])
    win_durs = win_table['Duration'].values.astype(np.float64)

    if not np.all(np.diff(win_ns) >= 0):
        order    = np.argsort(win_ns)
        win_ns   = win_ns[order]
        win_durs = win_durs[order]

    n_wins = len(win_ns)
    rng    = np.random.default_rng()

    sim_start = cand_ns[rng.integers(len(cand_ns), size=n_iter)]

    # ── Fast path: non-interruptible ──────────────────────────────────────
    # The campaign needs ONE unbroken window of at least total_hours. Walking
    # window-by-window is unnecessary: because windows are non-overlapping and
    # sorted, skipping a too-short window always lands on the next window in
    # index order. The net result is simply "the first window at or after the
    # start that is long enough". Pre-filtering to qualifying windows turns the
    # whole simulation into a single vectorised searchsorted.
    if not interruptible:
        end  = np.empty(n_iter, dtype=np.int64)
        qual = win_durs >= total_hours

        if qual.any():
            qual_ns = win_ns[qual]
            j   = np.searchsorted(qual_ns, sim_start, side='left')
            hit = j < len(qual_ns)
            end[hit] = qual_ns[j[hit]] + np.int64(total_hours * NS_PER_HOUR)
        else:
            hit = np.zeros(n_iter, dtype=bool)

        # No long-enough window ahead. The scalar algorithm skips through every
        # remaining window (each skip advances to that window's end) until the
        # table is exhausted, then applies the 1-year penalty. Reproduce that
        # endpoint directly: the end of the final window, plus one year — or,
        # if no window at all lies ahead of the start, the start plus one year.
        if (~hit).any():
            miss     = ~hit
            last_end = win_ns[-1] + np.int64(win_durs[-1] * NS_PER_HOUR)
            has_any  = np.searchsorted(win_ns, sim_start[miss], side='left') < n_wins
            end[miss] = np.where(has_any, last_end, sim_start[miss]) + YEAR_NS

        return (end - sim_start).astype(np.float64) / float(NS_PER_HOUR)

    # ── Interruptible: iterations advance in lock-step ────────────────────
    cur    = sim_start.copy()
    work   = np.zeros(n_iter, dtype=np.float64)
    active = np.ones(n_iter, dtype=bool)

    # Safety bound: a campaign can never need more windows than exist.
    max_passes = int(n_wins) + 2

    for _ in range(max_passes):
        ai = np.flatnonzero(active)
        if ai.size == 0:
            break

        idx = np.searchsorted(win_ns, cur[ai], side='left')

        # Record exhausted → 1-year penalty, iteration finished
        spent = idx >= n_wins
        if spent.any():
            si = ai[spent]
            cur[si] += YEAR_NS
            active[si] = False

        ok = ~spent
        if not ok.any():
            continue
        oi      = ai[ok]
        widx    = idx[ok]
        dur     = win_durs[widx]
        w_start = win_ns[widx]

        if interruptible:
            used = np.minimum(dur, total_hours - work[oi])
            work[oi] += used
            cur[oi]   = w_start + (used * NS_PER_HOUR).astype(np.int64)
            active[oi[work[oi] >= total_hours]] = False
        else:
            fits = dur >= total_hours
            if fits.any():
                fi = oi[fits]
                work[fi]   = total_hours
                cur[fi]    = w_start[fits] + np.int64(total_hours * NS_PER_HOUR)
                active[fi] = False
            if (~fits).any():
                ni = oi[~fits]
                cur[ni] = w_start[~fits] + (dur[~fits] * NS_PER_HOUR).astype(np.int64)

    # Any iteration still active hit the pass limit — treat as exhausted
    if active.any():
        cur[active] += YEAR_NS

    return (cur - sim_start).astype(np.float64) / float(NS_PER_HOUR)


# ══════════════════════════════════════════════════════════════════════════
# 4.  SCENARIO RUNNER
# ══════════════════════════════════════════════════════════════════════════

def run_scenario(merged: pd.DataFrame, params: dict):
    """
    Run one scenario: window identification → monthly operability → MC.

    Returns
    -------
    results      : ndarray of campaign durations (hours), or None
    monthly_oper : (12,) array of percentages
    status       : str
    """
    win_table, monthly_oper = find_weather_windows(merged, params)
    if win_table is None:
        return None, monthly_oper, "No weather windows found for these thresholds."

    season = params['season']
    if season == 'All-Year':
        start_months = []
    elif season == 'Summer (Apr-Sep)':
        start_months = [4, 5, 6, 7, 8, 9]
    elif season == 'Winter (Oct-Mar)':
        start_months = [10, 11, 12, 1, 2, 3]
    else:
        start_months = [MONTH_MAP[season]]

    raw = run_monte_carlo(merged, win_table, {
        'totalHours':      float(params['dur']),
        'isInterruptible': params['interruptible'],
        'numIterations':   N_ITERATIONS,
        'startMonths':     start_months,
    })

    results = raw[np.isfinite(raw)]
    if len(results) == 0:
        return None, monthly_oper, "All iterations returned no finite result."

    return (results, monthly_oper,
            f"{len(win_table):,} windows found, {len(results):,} valid iterations.")


# ══════════════════════════════════════════════════════════════════════════
# 5.  STATISTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════

def calc_percentile(sorted_arr: np.ndarray, p: float) -> float:
    """Nearest-rank percentile — matches the MATLAB calcPct anonymous fn."""
    idx = max(0, int(round(p / 100.0 * len(sorted_arr))) - 1)
    return float(sorted_arr[min(idx, len(sorted_arr) - 1)])


def fmt_hrs_days(h: float) -> str:
    """Format a duration as 'HH.H (DD.D)' — hours (days)."""
    return f"{h:.1f} ({h / 24:.1f})"
