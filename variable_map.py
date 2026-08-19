"""
variable_map.py  –  Marine Survey Weather Window Tool
Stage 2: identify metocean variables, derive from components, check units.

Takes the raw per-file tables produced by io_readers and proposes a mapping
onto canonical variable names. The proposal is always shown to the user for
confirmation before use — auto-detection is a starting point, not an
authority, because a mis-identified Hs column produces results that look
entirely plausible and are wrong.

Direction conventions follow normal metocean practice:
    waves and wind  -> 'from' (the direction they arrive from)
    current         -> 'to'   (the direction it flows toward)
Each is overridable per variable.

Duplicate variables across files: the first file in upload order wins; the
loser is recorded so it can be reported in the inventory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════
# 1.  Canonical variables
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CanonicalVar:
    key: str
    label: str
    kind: str                      # 'magnitude' | 'direction' | 'period'
    unit: str                      # canonical unit
    pair: Optional[str] = None     # direction's paired magnitude
    convention: Optional[str] = None   # 'from' | 'to' (directions only)
    lo: float = -np.inf            # plausible range, for QC warnings
    hi: float = np.inf
    constraint: Optional[str] = None   # which scenario constraint it feeds


CANONICAL = {v.key: v for v in [
    CanonicalVar("Hs",      "Significant wave height", "magnitude", "m",
                 lo=0, hi=20, constraint="hs"),
    CanonicalVar("Hmax",    "Maximum wave height",     "magnitude", "m",
                 lo=0, hi=35),
    CanonicalVar("Tp",      "Peak wave period",        "period",    "s",
                 lo=0, hi=30, constraint="tp"),
    CanonicalVar("Tz",      "Zero-crossing period",    "period",    "s",
                 lo=0, hi=25),
    CanonicalVar("Tm",      "Mean wave period",        "period",    "s",
                 lo=0, hi=25),
    CanonicalVar("WaveDir", "Wave direction",          "direction", "deg",
                 pair="Hs", convention="from", lo=0, hi=360, constraint="vdir"),
    CanonicalVar("WSpd",    "Wind speed",              "magnitude", "m/s",
                 lo=0, hi=60, constraint="wind"),
    CanonicalVar("WDir",    "Wind direction",          "direction", "deg",
                 pair="WSpd", convention="from", lo=0, hi=360, constraint="wdir"),
    CanonicalVar("CSpd",    "Current speed",           "magnitude", "m/s",
                 lo=0, hi=10, constraint="curr"),
    CanonicalVar("CDir",    "Current direction",       "direction", "deg",
                 pair="CSpd", convention="to", lo=0, hi=360),
]}

# Vector components — never used directly, only to derive speed/direction
COMPONENTS = {
    "WindU": ("WSpd", "WDir", "from"),
    "WindV": ("WSpd", "WDir", "from"),
    "CurrU": ("CSpd", "CDir", "to"),
    "CurrV": ("CSpd", "CDir", "to"),
}

# Order in which variables are presented to the user
DISPLAY_ORDER = ["Hs", "Tp", "Tz", "Tm", "Hmax", "WaveDir",
                 "WSpd", "WDir", "CSpd", "CDir"]


# ══════════════════════════════════════════════════════════════════════════
# 2.  Synonyms
# ══════════════════════════════════════════════════════════════════════════
#
# Matched against the normalised column name, then against netCDF
# standard_name and long_name. Patterns are ordered most specific first:
# 'sigwaveheight' must beat a bare 'waveheight', and anything containing
# 'wave' must not be captured by the generic direction patterns.

EXACT = {
    "Hs":      ["hs", "hm0", "hsig", "swh", "sigwaveheight", "signwaveheight",
                "significantwaveheight", "waveheight", "hs_m", "vhm0", "hsignificant"],
    "Hmax":    ["hmax", "maxwaveheight", "maximumwaveheight", "hmaximum"],
    "Tp":      ["tp", "pp1d", "tpeak", "peakwaveperiod", "peakperiod",
                "vtpk", "tp_s", "tpk"],
    "Tz":      ["tz", "t02", "tzero", "zerocrossingperiod", "zeroupcrossingperiod",
                "vtm02", "tm02"],
    "Tm":      ["tm", "t01", "tm01", "mwp", "meanwaveperiod", "meanperiod",
                "vtm10", "tmean"],
    "WaveDir": ["wavedir", "mwd", "wavedirection", "meanwavedirection",
                "dirp", "vmdr", "wdirwave", "dp", "thetap", "wavedirfrom"],
    "WSpd":    ["wspd", "wspd10", "windspeed", "ws", "ws10", "w10",
                "windspd", "wind", "wsp", "uwind10m", "windvelocity"],
    "WDir":    ["wdir", "wdir10", "winddirection", "winddir", "wd", "wd10",
                "dirwind", "winddirfrom"],
    "CSpd":    ["cspd", "currentspeed", "curspd", "cs", "currspeed",
                "currentvelocity", "curvel", "speedofcurrent"],
    "CDir":    ["cdir", "currentdirection", "curdir", "cd", "currdir",
                "currentdir", "dirofcurrent"],
    "WindU":   ["u10", "uwnd", "uwind", "windu", "u10m", "u_wind", "uas"],
    "WindV":   ["v10", "vwnd", "vwind", "windv", "v10m", "v_wind", "vas"],
    "CurrU":   ["u", "ucur", "ucurrent", "curru", "uo", "u_current", "ux",
                "eastwardcurrent"],
    "CurrV":   ["v", "vcur", "vcurrent", "currv", "vo", "v_current", "vy",
                "northwardcurrent"],
}

# CF standard_name -> canonical. Highest confidence when present.
CF_NAMES = {
    "sea_surface_wave_significant_height": "Hs",
    "significant_height_of_wind_and_swell_waves": "Hs",
    "sea_surface_wind_wave_significant_height": "Hs",
    "sea_surface_wave_maximum_height": "Hmax",
    "sea_surface_wave_period_at_variance_spectral_density_maximum": "Tp",
    "sea_surface_wave_zero_upcrossing_period": "Tz",
    "sea_surface_wave_mean_period": "Tm",
    "sea_surface_wave_mean_period_from_variance_spectral_density_first_frequency_moment": "Tm",
    "sea_surface_wave_from_direction": "WaveDir",
    "sea_surface_wave_to_direction": "WaveDir",
    "wind_speed": "WSpd",
    "wind_from_direction": "WDir",
    "wind_to_direction": "WDir",
    "eastward_wind": "WindU",
    "northward_wind": "WindV",
    "sea_water_speed": "CSpd",
    "direction_of_sea_water_velocity": "CDir",
    "sea_water_velocity_to_direction": "CDir",
    "eastward_sea_water_velocity": "CurrU",
    "northward_sea_water_velocity": "CurrV",
}

# Long-name / free-text patterns. (regex, canonical, score)
PATTERNS = [
    (r"significant.*(wave)?.*height",            "Hs",      88),
    (r"\bswh\b",                                 "Hs",      88),
    (r"max(imum)?.*wave.*height",                "Hmax",    88),
    (r"peak.*(wave)?.*period",                   "Tp",      88),
    (r"zero.?(up)?cross.*period",                "Tz",      88),
    (r"mean.*wave.*period",                      "Tm",      86),
    (r"mean.*wave.*direction",                   "WaveDir", 88),
    (r"wave.*direction",                         "WaveDir", 84),
    (r"direction.*wave",                         "WaveDir", 84),
    (r"10.*met(re|er).*u.*wind",                 "WindU",   88),
    (r"10.*met(re|er).*v.*wind",                 "WindV",   88),
    (r"eastward.*wind",                          "WindU",   88),
    (r"northward.*wind",                         "WindV",   88),
    (r"wind.*speed",                             "WSpd",    86),
    (r"speed.*wind",                             "WSpd",    84),
    (r"wind.*direction",                         "WDir",    86),
    (r"direction.*wind",                         "WDir",    84),
    (r"eastward.*(sea.?water|current)",          "CurrU",   88),
    (r"northward.*(sea.?water|current)",         "CurrV",   88),
    (r"current.*speed",                          "CSpd",    86),
    (r"speed.*current",                          "CSpd",    84),
    (r"current.*direction",                      "CDir",    86),
    (r"direction.*current",                      "CDir",    84),
]


def _norm(s: str) -> str:
    """Lowercase and strip everything that isn't a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _norm_text(s: str) -> str:
    """Lowercase, collapse separators to spaces — for long_name matching."""
    return re.sub(r"[_\-/]+", " ", str(s).lower()).strip()


# ══════════════════════════════════════════════════════════════════════════
# 3.  Units
# ══════════════════════════════════════════════════════════════════════════

UNIT_ALIASES = {
    "m/s":    ["m/s", "ms-1", "ms**-1", "m s-1", "m s**-1", "mpers",
               "metrespersecond", "meterspersecond", "m.s-1"],
    "knots":  ["knot", "knots", "kt", "kts", "kn"],
    "cm/s":   ["cm/s", "cms-1", "cm s-1", "cm s**-1", "cms"],
    "ft/s":   ["ft/s", "fts-1", "feetpersecond"],
    "m":      ["m", "metre", "metres", "meter", "meters"],
    "cm":     ["cm", "centimetre", "centimetres", "centimeter"],
    "ft":     ["ft", "foot", "feet"],
    "s":      ["s", "sec", "secs", "second", "seconds"],
    "deg":    ["deg", "degree", "degrees", "degreetrue", "degreestrue",
               "degreenorth", "degt", "degreesnorth", "degn"],
    "rad":    ["rad", "radian", "radians"],
}

# (from, to) -> (factor, label)
CONVERSIONS = {
    ("knots", "m/s"): (0.5144444, "knots -> m/s"),
    ("cm/s",  "m/s"): (0.01,      "cm/s -> m/s"),
    ("ft/s",  "m/s"): (0.3048,    "ft/s -> m/s"),
    ("cm",    "m"):   (0.01,      "cm -> m"),
    ("ft",    "m"):   (0.3048,    "ft -> m"),
    ("rad",   "deg"): (180.0 / np.pi, "radians -> degrees"),
}


def normalise_unit(raw: Optional[str]) -> Optional[str]:
    """Map a declared unit string onto a canonical unit token."""
    if not raw:
        return None
    key = _norm(raw)
    for canon, aliases in UNIT_ALIASES.items():
        if key in [_norm(a) for a in aliases]:
            return canon
    return None


def infer_unit(values: np.ndarray, var: CanonicalVar) -> Optional[str]:
    """
    Guess the unit from the value range when none is declared.
    Deliberately conservative — only flags the clear-cut cases.
    """
    v = values[np.isfinite(values)]
    if v.size < 10:
        return None
    vmax = float(np.nanpercentile(v, 99.5))
    vmin = float(np.nanmin(v))

    if var.kind == "direction":
        if vmax <= 6.4 and vmin >= -3.2:
            return "rad"
        if vmax <= 361:
            return "deg"
        return None

    if var.key in ("WSpd",):
        if vmax > 60:
            return "knots"        # 60 m/s is a violent hurricane; knots likelier
        return "m/s"
    if var.key in ("CSpd",):
        if vmax > 20:
            return "cm/s"         # 20 m/s current is not physical
        return "m/s"
    if var.key in ("Hs", "Hmax"):
        if vmax > 40:
            return "cm"
        return "m"
    if var.kind == "period":
        return "s"
    return None


# ══════════════════════════════════════════════════════════════════════════
# 4.  Mapping proposal
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class MappingRow:
    canonical: str
    source_file: str
    source_col: str
    score: float
    reason: str
    unit_raw: Optional[str] = None
    unit_detected: Optional[str] = None
    unit_inferred: bool = False
    conversion: Optional[str] = None
    convention: Optional[str] = None
    derived_from: Optional[tuple] = None
    stats: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


@dataclass
class MappingProposal:
    rows: list = field(default_factory=list)          # accepted MappingRow
    rejected: list = field(default_factory=list)      # duplicates that lost
    unmapped: list = field(default_factory=list)      # (file, col) not identified
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def by_key(self) -> dict:
        return {r.canonical: r for r in self.rows}

    def available_constraints(self) -> list:
        """
        Constraints the scenario matrix can offer.

        Must account for derivation: a file carrying only u10/v10 yields
        WSpd and WDir after apply_mapping, so the wind constraints are
        available even though no WSpd row exists in the proposal.
        """
        keys = {r.canonical for r in self.rows}
        if {"WindU", "WindV"} <= keys:
            keys |= {"WSpd", "WDir"}
        if {"CurrU", "CurrV"} <= keys:
            keys |= {"CSpd", "CDir"}
        return sorted({CANONICAL[k].constraint for k in keys
                       if k in CANONICAL and CANONICAL[k].constraint})

    def resolved_variables(self) -> set:
        """Canonical variables that will exist after apply_mapping."""
        keys = {r.canonical for r in self.rows if r.canonical in CANONICAL}
        if {"WindU", "WindV"} <= {r.canonical for r in self.rows}:
            keys |= {"WSpd", "WDir"}
        if {"CurrU", "CurrV"} <= {r.canonical for r in self.rows}:
            keys |= {"CSpd", "CDir"}
        return keys


def _score_column(col: str, attrs: dict):
    """
    Score one column against every canonical variable.
    Returns (best_key, score, reason) or (None, 0, '').
    """
    best = (None, 0.0, "")

    # 1. CF standard_name — highest confidence
    sname = _norm_text(attrs.get("standard_name", ""))
    if sname:
        key = CF_NAMES.get(sname.replace(" ", "_"))
        if key:
            return key, 100.0, f"CF standard_name '{sname}'"

    # 2. Exact column-name match
    n = _norm(col)
    for key, aliases in EXACT.items():
        if n in aliases:
            # Longer alias = more specific = slightly higher confidence
            score = 95.0 + min(len(n), 20) * 0.1
            if score > best[1]:
                best = (key, score, f"column name '{col}'")
    if best[0]:
        return best

    # 3. Free-text patterns over long_name, then the column name
    for text, src in ((_norm_text(attrs.get("long_name", "")), "long_name"),
                      (_norm_text(col), "column name")):
        if not text:
            continue
        for pat, key, score in PATTERNS:
            if re.search(pat, text):
                if score > best[1]:
                    best = (key, float(score), f"{src} matched /{pat}/")
        if best[0]:
            return best

    # 4. Loose containment on the column name, last resort
    for key, aliases in EXACT.items():
        for a in aliases:
            if len(a) >= 4 and a in n:
                score = 60.0 + len(a) * 0.5
                if score > best[1]:
                    best = (key, score, f"'{a}' found in column name")
    return best


def _stats(series: pd.Series) -> dict:
    v = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    finite = v[np.isfinite(v)]
    if finite.size == 0:
        return {"n": 0, "coverage": 0.0}
    return {
        "n": int(finite.size),
        "coverage": float(finite.size / max(len(v), 1) * 100.0),
        "min": float(np.nanmin(finite)),
        "mean": float(np.nanmean(finite)),
        "max": float(np.nanmax(finite)),
    }


def propose_mapping(sources: list) -> MappingProposal:
    """
    Build a mapping proposal across all uploaded files.

    sources: list of (file_name, frame, var_attrs) in UPLOAD ORDER.
             frame is time-indexed with numeric columns (from io_readers).

    Duplicate variables: the first file in upload order wins; later ones are
    recorded in .rejected for the inventory.
    """
    prop = MappingProposal()
    claimed = {}                    # canonical -> MappingRow

    for fname, frame, attrs in sources:
        for col in frame.columns:
            a = attrs.get(col, {}) if attrs else {}
            key, score, reason = _score_column(col, a)

            if not key or score < 55:
                prop.unmapped.append((fname, str(col)))
                continue

            var = CANONICAL.get(key)
            row = MappingRow(
                canonical=key,
                source_file=fname,
                source_col=str(col),
                score=score,
                reason=reason,
                unit_raw=a.get("units"),
                convention=var.convention if var else None,
                stats=_stats(frame[col]),
            )

            # Units: declared first, inferred second
            if key in COMPONENTS:
                declared = normalise_unit(row.unit_raw)
                row.unit_detected = declared or "m/s"
                row.unit_inferred = declared is None
                target = "m/s"
            else:
                declared = normalise_unit(row.unit_raw)
                if declared:
                    row.unit_detected = declared
                else:
                    vals = pd.to_numeric(frame[col], errors="coerce").to_numpy(float)
                    row.unit_detected = infer_unit(vals, var)
                    row.unit_inferred = row.unit_detected is not None
                target = var.unit

            if row.unit_detected and row.unit_detected != target:
                conv = CONVERSIONS.get((row.unit_detected, target))
                if conv:
                    row.conversion = conv[1]
                    if row.unit_inferred:
                        row.warnings.append(
                            f"Units not declared; {row.unit_detected} inferred "
                            f"from the value range. Confirm before use."
                        )
                else:
                    row.warnings.append(
                        f"Units '{row.unit_detected}' cannot be converted to "
                        f"{target}. Values will be used unchanged."
                    )

            # Plausibility check, applied to the POST-conversion range so a
            # bad inference cannot hide behind a unit change (Hs of 5000 read
            # as cm is still 50 m, and still wrong).
            if var and row.stats.get("n") and key not in COMPONENTS:
                factor = 1.0
                if row.conversion:
                    factor = CONVERSIONS[(row.unit_detected, var.unit)][0]
                lo_c, hi_c = row.stats["min"] * factor, row.stats["max"] * factor
                if hi_c > var.hi * 1.05 or lo_c < var.lo - 1e-9:
                    unit_note = (f" (after {row.conversion})" if row.conversion else "")
                    row.warnings.append(
                        f"Values span {lo_c:.3g} to {hi_c:.3g} {var.unit}"
                        f"{unit_note}, outside the expected {var.lo:g}–{var.hi:g}. "
                        f"Check units and column choice."
                    )

            # First file wins on duplicates
            prev = claimed.get(key)
            if prev is None:
                claimed[key] = row
            else:
                prop.rejected.append(row)
                prop.notes.append(
                    f"{key}: using '{prev.source_col}' from {prev.source_file}; "
                    f"ignoring '{row.source_col}' from {fname} "
                    f"(first file in upload order wins)."
                )

    prop.rows = list(claimed.values())

    # Components are only useful as complete pairs
    for u, v, mag in (("WindU", "WindV", "wind"), ("CurrU", "CurrV", "current")):
        have = {r.canonical for r in prop.rows}
        if (u in have) != (v in have):
            lone = u if u in have else v
            prop.warnings.append(
                f"Only one {mag} vector component ({lone}) was found; both are "
                f"needed to derive speed and direction. It will be ignored."
            )
            prop.rows = [r for r in prop.rows if r.canonical != lone]

    return prop


# ══════════════════════════════════════════════════════════════════════════
# 5.  Derivation from vector components
# ══════════════════════════════════════════════════════════════════════════

def speed_from_uv(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vector magnitude."""
    return np.hypot(u, v)


def direction_from_uv(u: np.ndarray, v: np.ndarray, convention: str) -> np.ndarray:
    """
    Compass direction from eastward (u) and northward (v) components.

    'to'   — direction the flow is heading toward (current convention)
    'from' — direction the flow arrives from (wind and wave convention)

    atan2(u, v) gives the compass bearing toward which the vector points:
    u>0, v=0 (eastward) -> 90 deg; u=0, v>0 (northward) -> 0 deg.
    The 'from' convention is that bearing rotated by 180 degrees.
    """
    toward = np.degrees(np.arctan2(u, v)) % 360.0
    if convention == "from":
        return (toward + 180.0) % 360.0
    return toward


# ══════════════════════════════════════════════════════════════════════════
# 6.  Apply the confirmed mapping
# ══════════════════════════════════════════════════════════════════════════

def apply_mapping(sources: list, prop: MappingProposal):
    """
    Rename, convert units and derive vector quantities.

    Returns (per_source, notes) where per_source is a list of
    (file_name, DataFrame) carrying canonical column names only. Each frame
    keeps its own time index and native resolution — resampling and merging
    happen in stage 3.
    """
    frames = {name: f for name, f, _ in sources}
    notes = []
    out = []

    by_file = {}
    for r in prop.rows:
        by_file.setdefault(r.source_file, []).append(r)

    for fname, _, _ in sources:
        rows = by_file.get(fname, [])
        if not rows:
            continue
        src = frames[fname]
        data = {}

        # Direct variables
        for r in rows:
            if r.canonical in COMPONENTS:
                continue
            vals = pd.to_numeric(src[r.source_col], errors="coerce").to_numpy(float)
            if r.conversion:
                factor = CONVERSIONS[(r.unit_detected, CANONICAL[r.canonical].unit)][0]
                vals = vals * factor
                notes.append(f"{r.canonical}: applied {r.conversion}.")
            data[r.canonical] = vals

        # Derived from components
        comp = {r.canonical: r for r in rows if r.canonical in COMPONENTS}
        for uk, vk, mag_key, dir_key, conv, label in (
            ("WindU", "WindV", "WSpd", "WDir", "from", "wind"),
            ("CurrU", "CurrV", "CSpd", "CDir", "to",   "current"),
        ):
            if uk not in comp or vk not in comp:
                continue
            ru, rv = comp[uk], comp[vk]
            u = pd.to_numeric(src[ru.source_col], errors="coerce").to_numpy(float)
            v = pd.to_numeric(src[rv.source_col], errors="coerce").to_numpy(float)
            for r, arr in ((ru, u), (rv, v)):
                if r.conversion:
                    arr *= CONVERSIONS[(r.unit_detected, "m/s")][0]

            if mag_key not in data:
                data[mag_key] = speed_from_uv(u, v)
                notes.append(
                    f"{mag_key}: derived from {ru.source_col} and {rv.source_col}.")
            if dir_key not in data:
                data[dir_key] = direction_from_uv(u, v, conv)
                notes.append(
                    f"{dir_key}: derived from {ru.source_col} and "
                    f"{rv.source_col} ({conv}-convention, standard for {label}).")

        if data:
            out.append((fname, pd.DataFrame(data, index=src.index)))

    return out, notes


def convert_convention(direction_deg: np.ndarray,
                       have: str, want: str) -> np.ndarray:
    """Flip a direction array between 'from' and 'to' conventions."""
    if have == want:
        return direction_deg
    return (direction_deg + 180.0) % 360.0
