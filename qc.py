"""
qc.py  –  Marine Survey Weather Window Tool
Stage 3b: build the data inventory shown to the user before analysis.

Answers the questions a client actually asks of an unfamiliar dataset:
how long is the record, what variables are in it, what is missing, and is
there anything that looks wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from variable_map import CANONICAL

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Inventory:
    start: pd.Timestamp = None
    end: pd.Timestamp = None
    years: float = 0.0
    n_hours_expected: int = 0
    n_hours_present: int = 0
    completeness: float = 0.0
    variables: pd.DataFrame = None      # per-variable summary
    gaps: pd.DataFrame = None           # gaps longer than one hour
    monthly: pd.DataFrame = None        # record count per calendar month
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def headline(self) -> str:
        return (f"{self.years:.1f} years | {self.start:%Y-%m-%d} to "
                f"{self.end:%Y-%m-%d} | {self.completeness:.1f}% complete")


def _variable_table(merged: pd.DataFrame, mapping_rows: list) -> pd.DataFrame:
    """One row per canonical variable: source, coverage, range, units."""
    by_key = {r.canonical: r for r in mapping_rows}
    n = len(merged)
    rows = []

    for col in merged.columns:
        var = CANONICAL.get(col)
        s = pd.to_numeric(merged[col], errors="coerce")
        present = int(s.notna().sum())
        m = by_key.get(col)

        if m is not None:
            source = f"{m.source_col}  ({m.source_file})"
        else:
            source = "derived"

        rows.append({
            "Variable":  col,
            "Description": var.label if var else col,
            "Source":    source,
            "Units":     var.unit if var else "",
            "Coverage %": round(present / n * 100, 1) if n else 0.0,
            "Missing":   n - present,
            "Min":       round(float(s.min()), 3) if present else np.nan,
            "Mean":      round(float(s.mean()), 3) if present else np.nan,
            "Max":       round(float(s.max()), 3) if present else np.nan,
        })

    order = {k: i for i, k in enumerate(CANONICAL)}
    rows.sort(key=lambda r: order.get(r["Variable"], 99))
    return pd.DataFrame(rows)


def _gap_table(merged: pd.DataFrame, cols: list) -> pd.DataFrame:
    """
    Runs of consecutive hours where every listed column is missing.
    Only gaps longer than one hour are listed.
    """
    if not cols:
        return pd.DataFrame(columns=["From", "To", "Hours", "Days"])

    missing = merged[cols].isna().all(axis=1).to_numpy()
    if not missing.any():
        return pd.DataFrame(columns=["From", "To", "Hours", "Days"])

    pad = np.concatenate([[0], missing.astype(np.int8), [0]])
    d = np.diff(pad)
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    dur = ends - starts

    keep = dur > 1
    idx = merged.index
    return pd.DataFrame({
        "From":  [idx[s] for s in starts[keep]],
        "To":    [idx[e - 1] for e in ends[keep]],
        "Hours": dur[keep],
        "Days":  np.round(dur[keep] / 24.0, 1),
    }).sort_values("Hours", ascending=False).reset_index(drop=True)


def _monthly_table(merged: pd.DataFrame, cols: list) -> pd.DataFrame:
    """
    Usable hours per calendar month, aggregated across all years.

    Matters because a seasonal Monte Carlo drawn from a record containing
    only two Januaries is unreliable, and nothing else in the tool warns
    about that.
    """
    if not cols:
        cols = list(merged.columns)
    usable = merged[cols].notna().all(axis=1)
    month = merged.index.month
    years = merged.index.year

    rows = []
    for m in range(1, 13):
        sel = month == m
        n_years = len(np.unique(years[sel])) if sel.any() else 0
        rows.append({
            "Month":        MONTHS[m - 1],
            "Hours in record": int(sel.sum()),
            "Usable hours": int(usable[sel].sum()),
            "Coverage %":   round(usable[sel].sum() / sel.sum() * 100, 1) if sel.any() else 0.0,
            "Years sampled": n_years,
        })
    return pd.DataFrame(rows)


def build_inventory(merged: pd.DataFrame,
                    mapping_rows: list,
                    active_cols: Optional[list] = None) -> Inventory:
    """
    Summarise the merged hourly dataset.

    active_cols: columns that will actually be used as constraints. Gap and
    monthly statistics are computed against these, since a missing column
    nobody is using does not matter.
    """
    inv = Inventory()
    if merged is None or len(merged) == 0:
        inv.warnings.append("Merged dataset is empty.")
        return inv

    cols = [c for c in (active_cols or merged.columns) if c in merged.columns]

    inv.start = merged.index.min()
    inv.end = merged.index.max()
    inv.n_hours_expected = int((inv.end - inv.start).total_seconds() // 3600) + 1
    inv.years = (inv.end - inv.start).total_seconds() / (365.25 * 24 * 3600)

    complete = merged[cols].notna().all(axis=1) if cols else merged.notna().any(axis=1)
    inv.n_hours_present = int(complete.sum())
    inv.completeness = (inv.n_hours_present / inv.n_hours_expected * 100
                        if inv.n_hours_expected else 0.0)

    inv.variables = _variable_table(merged, mapping_rows)
    inv.gaps = _gap_table(merged, cols)
    inv.monthly = _monthly_table(merged, cols)

    # ── Warnings ──────────────────────────────────────────────────────────
    if inv.years < 10:
        inv.warnings.append(
            f"The record covers {inv.years:.1f} years. Fewer than 10 years may "
            f"not sample rare weather patterns adequately; treat P90 results "
            f"with caution."
        )
    if inv.completeness < 95:
        inv.warnings.append(
            f"Only {inv.completeness:.1f}% of hours have all active variables "
            f"present. Check the gap policy setting before running."
        )
    if len(inv.gaps) > 0:
        biggest = inv.gaps.iloc[0]
        inv.warnings.append(
            f"{len(inv.gaps)} gap(s) longer than one hour. The largest spans "
            f"{int(biggest['Hours']):,} hours ({biggest['Days']:.1f} days) from "
            f"{biggest['From']:%Y-%m-%d} to {biggest['To']:%Y-%m-%d}."
        )

    thin = inv.monthly[inv.monthly["Years sampled"] < 5]
    if len(thin) > 0 and inv.years >= 1:
        names = ", ".join(thin["Month"].tolist())
        inv.warnings.append(
            f"Fewer than 5 years sampled for: {names}. Seasonal analysis "
            f"starting in those months will be based on a small sample."
        )

    # Physical sanity, post-conversion
    for _, row in inv.variables.iterrows():
        var = CANONICAL.get(row["Variable"])
        if var is None or pd.isna(row["Max"]):
            continue
        if row["Max"] > var.hi * 1.05 or row["Min"] < var.lo - 1e-9:
            inv.warnings.append(
                f"{row['Variable']} ranges {row['Min']:g} to {row['Max']:g} "
                f"{var.unit}, outside the expected {var.lo:g}–{var.hi:g}."
            )
        if var.kind == "direction" and (row["Max"] > 360.5 or row["Min"] < -0.5):
            inv.warnings.append(
                f"{row['Variable']} falls outside 0–360 degrees."
            )

    zero_cov = inv.variables[inv.variables["Coverage %"] == 0]
    for _, row in zero_cov.iterrows():
        inv.warnings.append(f"{row['Variable']} has no data at all.")

    return inv


def prepare_for_analysis(merged: pd.DataFrame,
                         active_cols: list,
                         gap_policy: str = "non-operable"):
    """
    Apply the chosen gap policy and return the frame the engine will use.

    'non-operable'  — keep every hour. Hours missing an active variable stay
                      as NaN and the engine treats them as infeasible. The
                      time axis remains continuous.

    'exclude'       — drop those hours entirely. They no longer count in the
                      operability denominator, which gives a fairer figure
                      when a variable is patchy — but it breaks the
                      continuity of the index. The engine MUST then apply a
                      timestamp contiguity check when building windows,
                      otherwise hours that are months apart become adjacent
                      in the array and merge into one phantom window. That
                      check is switched on by the returned flag.

    Returns (frame, needs_contiguity_check, notes).
    """
    notes = []
    cols = [c for c in active_cols if c in merged.columns]

    if gap_policy == "exclude":
        before = len(merged)
        out = merged.dropna(subset=cols) if cols else merged.dropna(how="all")
        dropped = before - len(out)
        if dropped:
            notes.append(
                f"Gap policy 'exclude': {dropped:,} hour(s) with missing data "
                f"removed. Window building will enforce timestamp contiguity."
            )
        return out, True, notes

    notes.append(
        "Gap policy 'non-operable': hours with missing data are retained and "
        "treated as unworkable."
    )
    return merged, False, notes
