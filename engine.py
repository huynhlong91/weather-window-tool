"""
engine.py  –  Marine Survey Weather Window Tool
Data processing and Monte Carlo engine.
Direct translation of WeatherWindowTool.m (loadData / executeAnalysis)
and runMonteCarlo.m.
"""

import numpy as np
import pandas as pd
import streamlit as st

# ── Month name → index map ─────────────────────────────────────────────────
MONTH_MAP = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
)}


def _to_ns(x) -> np.ndarray:
    """
    Convert any datetime array / index to int64 nanoseconds since epoch.
    Explicitly forces dtype='datetime64[ns]' before casting so the result
    is always in nanoseconds regardless of the pandas storage unit
    (pandas 2.x uses datetime64[us] internally, which would break
    arithmetic against NS_PER_HOUR if not normalised first).
    """
    return np.asarray(x, dtype='datetime64[ns]').astype(np.int64)


# ══════════════════════════════════════════════════════════════════════════
# 1.  DATA LOADING & MERGING
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def load_and_merge(hydro_bytes: bytes, wave_bytes: bytes, wind_bytes: bytes) -> pd.DataFrame:
    """
    Equivalent to loadData() in WeatherWindowTool.m.

    1. Read the three CSV files.
    2. Detect the datetime column automatically (looks for 'time' or 'timestamp').
    3. Aggregate hydrodynamics from 20-min to hourly: keep the row with the
       highest CSpd in each hour (vectorised sort — same as MATLAB).
    4. Merge on the intersection of timestamps (no interpolation needed for
       gap-free hindcast data).

    Returns
    -------
    pd.DataFrame with DatetimeIndex and columns:
        CSpd, CDir, Hs, [Hmax, Tz, Tm], Tp, WaveDir, WSpd10, WDir10
    """
    from io import BytesIO

    def _read(b: bytes) -> pd.DataFrame:
        df = pd.read_csv(BytesIO(b))
        time_col = next(
            (c for c in df.columns
             if 'time' in c.lower() or 'timestamp' in c.lower()),
            None
        )
        if time_col is None:
            raise ValueError(
                "No DateTime/Timestamp column found. "
                "The column name must contain 'Time' or 'Timestamp'."
            )
        df[time_col] = pd.to_datetime(df[time_col])
        return df.set_index(time_col).sort_index()

    df_hydro = _read(hydro_bytes)
    df_wave  = _read(wave_bytes)
    df_wind  = _read(wind_bytes)

    # ── Aggregate hydrodynamics to hourly ─────────────────────────────────
    # Vectorised: sort by (hour_group ASC, CSpd DESC), take first per group.
    df_hydro = df_hydro.copy()
    df_hydro.index = df_hydro.index.floor('h')
    cspd_filled = df_hydro['CSpd'].fillna(-np.inf)
    sort_key = -cspd_filled.values
    df_hydro = df_hydro.iloc[np.argsort(sort_key, kind='stable')]
    df_hydro_hourly = (df_hydro[~df_hydro.index.duplicated(keep='first')]
                       [['CSpd', 'CDir']]
                       .sort_index())

    # ── Select wave / wind columns ────────────────────────────────────────
    wave_cols = [c for c in ['Hs', 'Hmax', 'Tz', 'Tp', 'Tm', 'WaveDir']
                 if c in df_wave.columns]
    wind_cols = [c for c in ['WSpd10', 'WDir10']
                 if c in df_wind.columns]

    if 'Hs' not in wave_cols:
        raise ValueError("Waves CSV must contain an 'Hs' column.")
    if 'WSpd10' not in wind_cols:
        raise ValueError("Winds CSV must contain a 'WSpd10' column.")

    # ── Intersection merge (no interpolation — gap-free hindcast) ─────────
    merged = (df_hydro_hourly
              .join(df_wave[wave_cols],  how='inner')
              .join(df_wind[wind_cols],  how='inner'))
    return merged.sort_index()


# ══════════════════════════════════════════════════════════════════════════
# 2.  OPERABILITY ANALYSIS  (Step 1 of executeAnalysis)
# ══════════════════════════════════════════════════════════════════════════

def find_weather_windows(merged: pd.DataFrame, params: dict):
    """
    Identify contiguous feasible weather windows from the FULL unfiltered
    hindcast.  Season/month is NOT applied here.

    Returns
    -------
    pd.DataFrame with columns ['StartTime', 'Duration'] or None.
    """
    hs = merged['Hs'].values
    tp = merged['Tp'].values  if 'Tp'     in merged.columns else np.zeros(len(merged))
    wd = merged['WSpd10'].values
    cu = merged['CSpd'].values

    feasible = (
        (hs <= params['hs'])   &
        (tp <= params['tp'])   &
        (wd <= params['wind']) &
        (cu <= params['curr'])
    )

    # Wind direction filter (circular wrap-around safe)
    if params['wdir_active'] and 'WDir10' in merged.columns:
        wdir = merged['WDir10'].values
        lo, hi = params['wdir_min'], params['wdir_max']
        feasible &= (wdir >= lo) & (wdir <= hi) if lo <= hi \
                    else (wdir >= lo) | (wdir <= hi)

    # Wave direction filter
    if params['vdir_active'] and 'WaveDir' in merged.columns:
        vdir = merged['WaveDir'].values
        lo, hi = params['vdir_min'], params['vdir_max']
        feasible &= (vdir >= lo) & (vdir <= hi) if lo <= hi \
                    else (vdir >= lo) | (vdir <= hi)

    # Identify contiguous runs of feasible hours
    pad   = np.concatenate([[0], feasible.astype(np.int8), [0]])
    diff  = np.diff(pad)
    starts = np.where(diff ==  1)[0]
    ends   = np.where(diff == -1)[0]
    durs   = (ends - starts).astype(np.float64)

    keep = durs >= params['min_win']
    if not np.any(keep):
        return None

    times = merged.index
    return pd.DataFrame({
        'StartTime': times[starts[keep]],
        'Duration':  durs[keep],
    })


# ══════════════════════════════════════════════════════════════════════════
# 3.  MONTE CARLO  (runMonteCarlo.m)
# ══════════════════════════════════════════════════════════════════════════

def run_monte_carlo(merged: pd.DataFrame,
                    win_table: pd.DataFrame,
                    settings: dict) -> np.ndarray:
    """
    Monte Carlo campaign-duration estimator.
    Direct translation of runMonteCarlo.m.

    All datetime arithmetic is done in int64 NANOSECONDS via _to_ns(),
    which explicitly casts through datetime64[ns] to avoid the pandas 2.x
    datetime64[us] storage ambiguity.
    """
    total_hours   = float(settings['totalHours'])
    interruptible = bool(settings['isInterruptible'])
    n_iter        = int(settings['numIterations'])
    start_months  = settings['startMonths']   # list[int] or []

    if win_table is None or len(win_table) == 0:
        return np.full(n_iter, np.inf)

    # ── Build start-time pool ─────────────────────────────────────────────
    row_times = merged.index
    limit_idx = max(1, len(row_times) - int(total_hours * 5))
    candidates = row_times[:limit_idx]

    if start_months:
        candidates = candidates[candidates.month.isin(start_months)]
    if len(candidates) == 0:
        return np.full(n_iter, np.inf)

    # ── Convert to int64 nanoseconds (always, regardless of pandas version)
    NS_PER_HOUR = np.int64(3_600_000_000_000)   # 1 h = 3.6e12 ns
    YEAR_NS     = np.int64(365 * 24) * NS_PER_HOUR

    cand_ns  = _to_ns(candidates)                     # shape (n_starts,)
    win_ns   = _to_ns(win_table['StartTime'])          # shape (n_wins,)
    win_durs = win_table['Duration'].values.astype(np.float64)  # hours

    # Sort windows (guard)
    if not np.all(np.diff(win_ns) >= 0):
        order    = np.argsort(win_ns)
        win_ns   = win_ns[order]
        win_durs = win_durs[order]

    n_wins   = len(win_ns)
    n_starts = len(cand_ns)
    durations = np.zeros(n_iter, dtype=np.float64)
    rng = np.random.default_rng()

    # ── Main loop ─────────────────────────────────────────────────────────
    for i in range(n_iter):
        sim_start = cand_ns[rng.integers(n_starts)]
        cur       = sim_start
        work      = 0.0

        while work < total_hours:
            idx = np.searchsorted(win_ns, cur, side='left')

            if idx >= n_wins:                      # record exhausted
                cur += YEAR_NS
                break

            dur     = win_durs[idx]
            w_start = win_ns[idx]

            if interruptible:
                used  = min(dur, total_hours - work)
                work += used
                cur   = w_start + np.int64(used * NS_PER_HOUR)
            else:
                if dur >= total_hours:
                    work = total_hours
                    cur  = w_start + np.int64(total_hours * NS_PER_HOUR)
                else:
                    cur  = w_start + np.int64(dur * NS_PER_HOUR)

        durations[i] = float(cur - sim_start) / float(NS_PER_HOUR)

    return durations


# ══════════════════════════════════════════════════════════════════════════
# 4.  FULL SCENARIO RUNNER  (Steps 1-3 of executeAnalysis)
# ══════════════════════════════════════════════════════════════════════════

def run_scenario(merged: pd.DataFrame, params: dict):
    """
    Run one complete scenario: window identification → MC simulation.

    Returns
    -------
    results : np.ndarray (campaign durations in hours, inf removed) or None
    status  : str
    """
    win_table = find_weather_windows(merged, params)
    if win_table is None:
        return None, "No weather windows found for these thresholds."

    # Season → start months
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
        'numIterations':   100_000,
        'startMonths':     start_months,
    })

    results = raw[np.isfinite(raw)]
    if len(results) == 0:
        return None, "All MC iterations returned no finite result."

    return results, f"{len(win_table):,} windows found, {len(results):,} valid iterations."


# ══════════════════════════════════════════════════════════════════════════
# 5.  STATISTICS HELPERS
# ══════════════════════════════════════════════════════════════════════════

def calc_percentile(sorted_arr: np.ndarray, p: float) -> float:
    """Nearest-rank percentile (same formula as MATLAB calcPct)."""
    idx = max(0, int(round(p / 100.0 * len(sorted_arr))) - 1)
    return float(sorted_arr[min(idx, len(sorted_arr) - 1)])


def fmt_hrs_days(h: float) -> str:
    """Format a duration as 'HH.H (DD.D)' hours (days)."""
    return f"{h:.1f} ({h / 24:.1f})"
