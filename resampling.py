"""
resampling.py  –  Marine Survey Weather Window Tool
Stage 3a: bring every source onto a common hourly time base, then merge.

Aggregation rules (confirmed with the user):

    Hs    -> max within the hour; that row is i_Hs
    Tp, Tz, Tm, Hmax, WaveDir -> taken from row i_Hs
    WSpd  -> max within the hour; that row is i_W
    WDir  -> taken from row i_W
    CSpd  -> max within the hour; that row is i_C
    CDir  -> taken from row i_C

Three independent argmax operations, each carrying its paired direction, so
a direction is never circularly averaged. This generalises what the MATLAB
tool does for current alone.

Records coarser than hourly are interpolated up to hourly — linear for
magnitudes and periods, nearest-neighbour for directions (linear
interpolation across the 0/360 boundary would produce garbage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from variable_map import CANONICAL

# Driver -> the columns that follow its within-hour argmax
GROUPS = {
    "Hs":   ["Tp", "Tz", "Tm", "Hmax", "WaveDir"],
    "WSpd": ["WDir"],
    "CSpd": ["CDir"],
}


@dataclass
class ResampleInfo:
    source: str
    mode: str = ""                     # 'aggregated' | 'as-is' | 'interpolated'
    native_minutes: Optional[float] = None
    n_in: int = 0
    n_out: int = 0
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _native_minutes(index: pd.DatetimeIndex) -> Optional[float]:
    """Modal time step in minutes."""
    if len(index) < 2:
        return None
    d = np.diff(index.values).astype("timedelta64[s]").astype(float) / 60.0
    d = d[d > 0]
    if d.size == 0:
        return None
    vals, counts = np.unique(np.round(d, 3), return_counts=True)
    return float(vals[np.argmax(counts)])


def _pick_at_argmax(frame: pd.DataFrame, hours: np.ndarray,
                    driver: str, carry: list) -> pd.DataFrame:
    """
    For each hour, find the row where `driver` is greatest and return that
    row's driver and carried values.

    Vectorised: a stable sort by descending driver puts each hour's maximum
    first within its group, so np.unique(..., return_index=True) picks
    exactly those rows in one pass.
    """
    d = pd.to_numeric(frame[driver], errors="coerce").to_numpy(float)
    d = np.where(np.isfinite(d), d, -np.inf)      # NaN can never win a max

    order = np.argsort(-d, kind="stable")
    h_sorted = hours[order]
    uh, first = np.unique(h_sorted, return_index=True)
    rows = order[first]

    cols = [driver] + [c for c in carry if c in frame.columns]
    return pd.DataFrame(
        {c: frame[c].to_numpy()[rows] for c in cols},
        index=pd.DatetimeIndex(uh, name="time"),
    )


def _aggregate_hourly(frame: pd.DataFrame, info: ResampleInfo) -> pd.DataFrame:
    """Sub-hourly -> hourly, using the paired argmax rules."""
    hours = frame.index.floor("h").values
    parts, handled = [], set()

    for driver, carry in GROUPS.items():
        if driver not in frame.columns:
            continue
        present = [c for c in carry if c in frame.columns]
        parts.append(_pick_at_argmax(frame, hours, driver, present))
        handled.update([driver] + present)
        if present:
            info.notes.append(
                f"{', '.join(present)} taken from the hour's peak {driver} row."
            )
        info.notes.append(f"{driver} aggregated by hourly maximum.")

    # Columns with no driver in this file
    orphans = [c for c in frame.columns if c not in handled]
    if orphans:
        g = frame.groupby(hours)
        for c in orphans:
            var = CANONICAL.get(c)
            kind = var.kind if var else "magnitude"
            if kind == "direction":
                # Never average a direction. Take the hour's first sample.
                agg = g[c].first()
                info.warnings.append(
                    f"{c} has no paired magnitude in this file; the first "
                    f"sample in each hour was used rather than an average."
                )
            elif kind == "period":
                agg = g[c].mean()
                info.notes.append(f"{c} aggregated by hourly mean (no paired Hs).")
            else:
                agg = g[c].max()
                info.notes.append(f"{c} aggregated by hourly maximum.")
            agg.index = pd.DatetimeIndex(agg.index, name="time")
            parts.append(agg.to_frame())

    out = parts[0]
    for p in parts[1:]:
        out = out.join(p, how="outer")
    return out.sort_index()


def _gap_mask(valid_times: pd.DatetimeIndex, target: pd.DatetimeIndex,
              tol: pd.Timedelta) -> np.ndarray:
    """
    True where a target hour sits between two valid samples no further
    apart than `tol`.

    Without this, interpolation happily bridges a genuine data gap: a
    3-hourly record with a week missing would come back fully populated
    with invented values, and the inventory would report 100% coverage.
    """
    if len(valid_times) == 0:
        return np.zeros(len(target), dtype=bool)
    v = valid_times.values.astype("datetime64[ns]")
    t = target.values.astype("datetime64[ns]")
    pos = np.searchsorted(v, t, side="right")

    has_prev = pos > 0
    has_next = pos < len(v)
    ok = has_prev & has_next

    span = np.full(len(t), np.timedelta64(10**9, "s"))
    idx = np.flatnonzero(ok)
    if idx.size:
        span[idx] = v[pos[idx]] - v[pos[idx] - 1]

    # An exact hit on a valid sample is always fine
    exact = np.isin(t, v)
    return (ok & (span <= np.timedelta64(tol))) | exact


def _upsample_hourly(frame: pd.DataFrame, info: ResampleInfo) -> pd.DataFrame:
    """
    Coarser than hourly -> hourly.

    Magnitudes and periods are interpolated linearly; directions take the
    nearest original sample, because linear interpolation from 350 deg to
    10 deg would pass through 180 rather than through north.

    Interpolation is confined to gaps no larger than 1.5x the native step,
    so genuine holes in the record stay holes.
    """
    target = pd.date_range(frame.index.min().ceil("h"),
                           frame.index.max().floor("h"), freq="h")
    if len(target) == 0:
        return frame

    native = info.native_minutes or 60.0
    tol = pd.Timedelta(minutes=native * 1.5)

    dir_cols = [c for c in frame.columns
                if CANONICAL.get(c) and CANONICAL[c].kind == "direction"]
    num_cols = [c for c in frame.columns if c not in dir_cols]

    out = pd.DataFrame(index=target)

    if num_cols:
        union = frame.index.union(target)
        interp = (frame[num_cols]
                  .reindex(union)
                  .interpolate(method="time", limit_area="inside")
                  .reindex(target))
        for c in num_cols:
            keep = _gap_mask(frame.index[frame[c].notna()], target, tol)
            out[c] = interp[c].where(keep)

    if dir_cols:
        near = frame[dir_cols].reindex(target, method="nearest", tolerance=tol)
        for c in dir_cols:
            keep = _gap_mask(frame.index[frame[c].notna()], target, tol)
            out[c] = near[c].where(keep)
        info.notes.append(
            f"Directions ({', '.join(dir_cols)}) taken from the nearest "
            f"original sample, not interpolated."
        )

    if num_cols:
        info.notes.append(f"{', '.join(num_cols)} interpolated linearly in time.")
    info.notes.append(
        f"Interpolation limited to gaps of {tol.total_seconds()/3600:g} hours "
        f"or less; larger gaps left missing."
    )
    return out[list(frame.columns)]


def to_hourly(name: str, frame: pd.DataFrame):
    """
    Normalise one source onto an hourly index.

    Returns (hourly_frame, ResampleInfo).
    """
    info = ResampleInfo(source=name, n_in=len(frame))
    info.native_minutes = _native_minutes(frame.index)
    native = info.native_minutes

    if native is None:
        info.mode = "as-is"
        info.warnings.append("Could not determine the native time step.")
        out = frame.copy()

    elif native < 59.5:
        info.mode = "aggregated"
        out = _aggregate_hourly(frame, info)

    elif native <= 60.5:
        info.mode = "as-is"
        floored = frame.index.floor("h")
        dup = floored.duplicated(keep="first")
        out = frame[~dup].copy()
        out.index = floored[~dup]
        if dup.any():
            info.warnings.append(
                f"{int(dup.sum()):,} row(s) fell in the same hour after "
                f"flooring and were dropped."
            )
        info.notes.append("Already hourly; timestamps floored to the hour.")

    else:
        info.mode = "interpolated"
        out = _upsample_hourly(frame, info)
        hrs = native / 60.0
        info.warnings.append(
            f"Native resolution is {hrs:g}-hourly. Values were interpolated "
            f"up to hourly, which slightly smooths the window structure and "
            f"may marginally increase apparent operability."
        )

    out = out.sort_index()
    info.n_out = len(out)
    return out, info


def merge_sources(hourly: list):
    """
    Outer-join every hourly source onto a common index.

    An outer join is deliberate: it preserves the true extent of the record
    so the inventory can report honestly on coverage. An inner join would
    quietly shorten the record and hide the gaps.

    hourly: list of (name, DataFrame) with canonical columns.
    Returns (merged, notes).
    """
    notes = []
    if not hourly:
        raise ValueError("No data to merge.")

    merged = None
    for name, f in hourly:
        f = f[~f.index.duplicated(keep="first")]
        merged = f.copy() if merged is None else merged.join(
            f[[c for c in f.columns if c not in merged.columns]], how="outer")

    merged = merged.sort_index()

    # Reindex onto a complete hourly axis so gaps become explicit NaNs
    full = pd.date_range(merged.index.min(), merged.index.max(), freq="h")
    missing = len(full) - len(merged)
    merged = merged.reindex(full)
    merged.index.name = "time"
    if missing > 0:
        notes.append(
            f"{missing:,} hour(s) absent from the record were inserted as "
            f"gaps so coverage is reported honestly."
        )
    return merged, notes
