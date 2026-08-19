"""
io_readers.py  –  Marine Survey Weather Window Tool
Stage 1: universal file ingestion and time-axis resolution.

Reads .csv / .txt / .asc / .dat / .xlsx / .xls / .nc into a common
RawTable, then resolves whatever time representation the file uses into a
sorted DatetimeIndex.

Timestamps are used exactly as they appear in the file — no timezone
conversion is applied anywhere.

This module does NOT identify metocean variables; that is stage 2
(variable_map.py). Here every non-time column is passed through untouched.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# Lines beginning with these are treated as comments in text formats
COMMENT_CHARS = ("#", "%", "!")

# Column names that indicate a datetime column
_TIME_TOKENS = ("datetime", "date_time", "timestamp", "time", "date")

# Split date-part column names → canonical part
_PART_ALIASES = {
    "year":   "year",   "yyyy": "year",  "yr": "year",
    "month":  "month",  "mm":   "month", "mon": "month", "mo": "month",
    "day":    "day",    "dd":   "day",
    "hour":   "hour",   "hh":   "hour",  "hr": "hour",
    "minute": "minute", "min":  "minute", "mi": "minute",
    "second": "second", "sec":  "second", "ss": "second",
}

# Explicit formats tried before falling back to pandas inference.
# Unambiguous (ISO-like) orderings are tried first.
_TRY_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d%H", "%Y%m%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
]

# Compact all-digit formats need an exact length match. pandas parses these
# leniently: '2001030400' will "succeed" against %Y%m%d%H%M, silently reading
# the trailing 00 as minutes and inventing hour 0. Checking the digit count
# first forces the correct format to be selected.
_COMPACT_LEN = {
    "%Y%m%d%H%M%S": 14,
    "%Y%m%d%H%M":   12,
    "%Y%m%d%H":     10,
    "%Y%m%d":        8,
}


# ══════════════════════════════════════════════════════════════════════════
# Containers
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class RawTable:
    """A file read into memory, before any variable identification."""
    name: str                                    # original filename
    fmt: str                                     # 'csv' | 'excel' | 'netcdf'
    frame: pd.DataFrame
    var_attrs: dict = field(default_factory=dict)   # col -> {units, long_name, ...}
    notes: list = field(default_factory=list)       # informational
    warnings: list = field(default_factory=list)    # needs user attention
    sheets: Optional[list] = None                   # excel: all sheet names
    sheet_used: Optional[str] = None


@dataclass
class TimeInfo:
    """How the time axis was derived, and what shape it is."""
    method: str                  # 'datetime column' | 'split columns' | ...
    source: str                  # which column(s)
    fmt: Optional[str] = None    # strptime format used, if explicit
    ambiguous: bool = False      # day/month order could not be proven
    n_rows_in: int = 0
    n_rows_out: int = 0
    n_unparsed: int = 0
    n_duplicates: int = 0
    was_unsorted: bool = False
    native_minutes: Optional[float] = None
    start: Optional[pd.Timestamp] = None
    end: Optional[pd.Timestamp] = None
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
# 1.  Text formats  (.csv .txt .asc .dat)
# ══════════════════════════════════════════════════════════════════════════

def _strip_comments(text: str):
    """Remove comment lines, returning (clean_text, removed_lines)."""
    kept, removed = [], []
    for line in text.splitlines():
        if line.lstrip().startswith(COMMENT_CHARS):
            removed.append(line.strip())
        else:
            kept.append(line)
    return "\n".join(kept), removed


def _sniff_delimiter(sample: str) -> str:
    """
    Determine the field separator. csv.Sniffer first; if it fails or the
    result looks wrong, fall back to whichever candidate splits the sample
    lines into the most consistent field count.
    """
    try:
        d = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        if d in ",;\t|":
            return d
    except csv.Error:
        pass

    lines = [ln for ln in sample.splitlines() if ln.strip()][:20]
    best, best_score = r"\s+", -1.0
    for cand in [",", ";", "\t", "|", r"\s+"]:
        counts = []
        for ln in lines:
            n = len(re.split(cand, ln.strip())) if cand == r"\s+" else len(ln.split(cand))
            counts.append(n)
        if not counts or max(counts) < 2:
            continue
        # Reward many fields, penalise inconsistency between rows
        score = np.mean(counts) - 3.0 * np.std(counts)
        if score > best_score:
            best, best_score = cand, score
    return best


def _find_header_row(lines: list, sep: str) -> int:
    """
    Locate the header row: the first row that splits into >= 2 fields, has
    mostly non-numeric entries, and matches the field count of the row below.
    Handles files with several lines of preamble before the column names.
    """
    def split(ln):
        return re.split(sep, ln.strip()) if sep == r"\s+" else ln.split(sep)

    def numeric_fraction(fields):
        if not fields:
            return 1.0
        n = 0
        for f in fields:
            try:
                float(f.strip().strip('"\''))
                n += 1
            except ValueError:
                pass
        return n / len(fields)

    for i in range(min(len(lines) - 1, 30)):
        f_here = [x for x in split(lines[i]) if x.strip() != ""]
        f_next = [x for x in split(lines[i + 1]) if x.strip() != ""]
        if len(f_here) < 2 or len(f_next) < 2:
            continue
        if numeric_fraction(f_here) <= 0.5 and len(f_here) == len(f_next):
            return i
    return 0


def read_text(name: str, data: bytes) -> RawTable:
    """Read a delimited text file, tolerating comments and preamble."""
    notes, warnings = [], []

    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(enc)
            if enc != "utf-8-sig" and enc != "utf-8":
                notes.append(f"Decoded using {enc}.")
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"{name}: could not decode as text.")

    text, removed = _strip_comments(text)
    if removed:
        notes.append(f"Skipped {len(removed)} comment line(s).")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError(f"{name}: fewer than two usable lines.")

    sample = "\n".join(lines[:40])
    sep = _sniff_delimiter(sample)
    hdr = _find_header_row(lines, sep)
    if hdr > 0:
        notes.append(f"Header found on line {hdr + 1}; {hdr} preamble line(s) skipped.")

    sep_label = "whitespace" if sep == r"\s+" else repr(sep)
    notes.append(f"Delimiter: {sep_label}.")

    frame = pd.read_csv(
        io.StringIO("\n".join(lines[hdr:])),
        sep=sep,
        engine="python",
        skipinitialspace=True,
    )
    frame.columns = [str(c).strip() for c in frame.columns]

    # Drop columns pandas invented from trailing delimiters
    junk = [c for c in frame.columns
            if c.startswith("Unnamed:") and frame[c].isna().all()]
    if junk:
        frame = frame.drop(columns=junk)

    return RawTable(name=name, fmt="csv", frame=frame,
                    notes=notes, warnings=warnings)


# ══════════════════════════════════════════════════════════════════════════
# 2.  Excel  (.xlsx .xls)
# ══════════════════════════════════════════════════════════════════════════

def read_excel(name: str, data: bytes, sheet: Optional[str] = None) -> RawTable:
    """
    Read one sheet of a workbook. If sheet is None the first is used; all
    sheet names are returned so the caller can offer a choice.
    """
    notes, warnings = [], []
    book = pd.ExcelFile(io.BytesIO(data))
    sheets = list(book.sheet_names)

    if sheet is None:
        sheet = sheets[0]
        if len(sheets) > 1:
            notes.append(
                f"Workbook has {len(sheets)} sheets; using '{sheet}'. "
                f"Others: {', '.join(s for s in sheets if s != sheet)}."
            )
    elif sheet not in sheets:
        raise ValueError(f"{name}: sheet '{sheet}' not found.")

    frame = book.parse(sheet)
    frame.columns = [str(c).strip() for c in frame.columns]

    junk = [c for c in frame.columns
            if c.startswith("Unnamed:") and frame[c].isna().all()]
    if junk:
        frame = frame.drop(columns=junk)

    return RawTable(name=name, fmt="excel", frame=frame, notes=notes,
                    warnings=warnings, sheets=sheets, sheet_used=sheet)


# ══════════════════════════════════════════════════════════════════════════
# 3.  netCDF  (.nc)
# ══════════════════════════════════════════════════════════════════════════

def read_netcdf(name: str, data: bytes) -> RawTable:
    """
    Read a timeseries netCDF file.

    Singleton spatial dimensions are squeezed away silently — a (time,1,1)
    shape is common in point extractions and carries no information. A real
    spatial dimension of length > 1 is an error: the user is asked to
    extract a single point first rather than have the tool pick a node.

    Variable attributes (units, long_name, standard_name) are preserved for
    stage 2, where they give much better identification than names alone.
    """
    import xarray as xr

    notes, warnings = [], []
    ds = xr.open_dataset(io.BytesIO(data), engine="h5netcdf")

    try:
        # Locate the time dimension
        tdim = next((d for d in ds.dims
                     if str(d).lower() in ("time", "t", "datetime", "date")), None)
        if tdim is None:
            for cand in ds.coords:
                if np.issubdtype(np.asarray(ds[cand].values).dtype, np.datetime64):
                    tdim = ds[cand].dims[0] if ds[cand].dims else None
                    break
        if tdim is None:
            raise ValueError(
                f"{name}: no time dimension found. Dimensions present: "
                f"{', '.join(str(d) for d in ds.dims)}."
            )

        # Squeeze singleton non-time dimensions
        squeezed = [str(d) for d in ds.dims if d != tdim and ds.sizes[d] == 1]
        if squeezed:
            ds = ds.squeeze(drop=True)
            notes.append(f"Squeezed singleton dimension(s): {', '.join(squeezed)}.")

        # Reject genuine spatial dimensions
        spatial = {str(d): int(ds.sizes[d]) for d in ds.dims if d != tdim}
        if spatial:
            desc = ", ".join(f"{d} ({n})" for d, n in spatial.items())
            raise ValueError(
                f"{name}: this file is gridded, not a timeseries — it still has "
                f"dimension(s) {desc} after squeezing. Please extract a single "
                f"point before uploading."
            )

        # Build the frame
        cols, attrs = {}, {}
        for v in ds.data_vars:
            da = ds[v]
            if da.dims != (tdim,):
                notes.append(f"Skipped '{v}' (not a 1-D timeseries).")
                continue
            cols[str(v)] = np.asarray(da.values).ravel()
            attrs[str(v)] = {
                k: str(da.attrs[k])
                for k in ("units", "long_name", "standard_name")
                if k in da.attrs
            }

        if not cols:
            raise ValueError(f"{name}: no 1-D timeseries variables found.")

        tvals = np.asarray(ds[tdim].values).ravel()
        frame = pd.DataFrame(cols)
        frame.insert(0, "__nc_time__", tvals)

        if not np.issubdtype(tvals.dtype, np.datetime64):
            warnings.append(
                f"Time coordinate did not decode to datetimes (dtype "
                f"{tvals.dtype}). Check the calendar and units attributes."
            )

        cal = str(ds[tdim].attrs.get("calendar", "")).lower()
        if cal and cal not in ("standard", "gregorian", "proleptic_gregorian"):
            warnings.append(
                f"Non-standard calendar '{cal}' — dates may not align with "
                f"real-world dates."
            )

        notes.append(f"Read {len(cols)} variable(s) over {len(frame):,} time steps.")
        return RawTable(name=name, fmt="netcdf", frame=frame,
                        var_attrs=attrs, notes=notes, warnings=warnings)
    finally:
        ds.close()


# ══════════════════════════════════════════════════════════════════════════
# 4.  Dispatch
# ══════════════════════════════════════════════════════════════════════════

def read_any(name: str, data: bytes, sheet: Optional[str] = None) -> RawTable:
    """Read any supported file, dispatching on extension."""
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("nc", "nc4", "cdf", "netcdf"):
        return read_netcdf(name, data)
    if ext in ("xlsx", "xls", "xlsm"):
        return read_excel(name, data, sheet=sheet)
    if ext in ("csv", "txt", "asc", "dat", "tsv", "prn", ""):
        return read_text(name, data)
    raise ValueError(
        f"{name}: unsupported file type '.{ext}'. "
        f"Supported: .csv .txt .asc .dat .tsv .xlsx .xls .nc"
    )


# ══════════════════════════════════════════════════════════════════════════
# 5.  Time-axis resolution
# ══════════════════════════════════════════════════════════════════════════

def _parse_datetimes(series: pd.Series):
    """
    Parse a column to datetimes.

    Returns (parsed, format_label, ambiguous). Explicit formats are tried
    first so the format can be reported back to the user; 'ambiguous' is
    True when the values parse under both day-first and month-first
    orderings with different results, which the caller should surface
    because a silent day/month swap is very hard to spot downstream.
    """
    s = series.astype(str).str.strip()
    s = s.replace({"": None, "nan": None, "NaN": None, "None": None})
    non_null = s.dropna()
    if non_null.empty:
        return pd.to_datetime(s, errors="coerce"), None, False

    # Modal string length, used to disambiguate compact numeric formats
    modal_len = int(non_null.str.len().mode().iloc[0]) if len(non_null) else 0

    for fmt in _TRY_FORMATS:
        if fmt in _COMPACT_LEN and modal_len != _COMPACT_LEN[fmt]:
            continue
        try:
            parsed = pd.to_datetime(non_null, format=fmt, errors="raise")
        except (ValueError, TypeError):
            continue

        ambiguous = False
        if any(tok in fmt for tok in ("%d/%m", "%m/%d", "%d-%m", "%d.%m")):
            # Could the same strings parse the other way round?
            swap = {"%d/%m": "%m/%d", "%m/%d": "%d/%m",
                    "%d-%m": "%m-%d", "%d.%m": "%m.%d"}
            alt = fmt
            for a, b in swap.items():
                if a in alt:
                    alt = alt.replace(a, b)
                    break
            if alt != fmt:
                try:
                    other = pd.to_datetime(non_null, format=alt, errors="raise")
                    ambiguous = not other.equals(parsed)
                except (ValueError, TypeError):
                    ambiguous = False

        full = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
        full.loc[non_null.index] = parsed
        return full, fmt, ambiguous

    parsed = pd.to_datetime(s, errors="coerce")
    return parsed, "inferred by pandas", False


def _excel_serial_to_datetime(values: pd.Series) -> pd.Series:
    """Convert Excel serial day numbers (1900 system) to datetimes."""
    return pd.to_datetime(values.astype(float), unit="D", origin="1899-12-30")


def _find_time_column(frame: pd.DataFrame) -> Optional[str]:
    """Find the most likely datetime column by name, then by parseability."""
    if "__nc_time__" in frame.columns:
        return "__nc_time__"

    scored = []
    for c in frame.columns:
        low = str(c).lower().replace(" ", "").replace("_", "")
        if low in _PART_ALIASES:          # 'year','month' etc. are parts
            continue
        for rank, tok in enumerate(_TIME_TOKENS):
            if tok.replace("_", "") in low:
                scored.append((rank, c))
                break
    if scored:
        scored.sort()
        return scored[0][1]

    # No name match: try parsing each object column
    for c in frame.columns:
        if frame[c].dtype == object or np.issubdtype(frame[c].dtype, np.datetime64):
            parsed, _, _ = _parse_datetimes(frame[c].head(200))
            if parsed.notna().mean() > 0.9:
                return c
    return None


def _find_part_columns(frame: pd.DataFrame) -> dict:
    """Find separate year / month / day / hour columns."""
    found = {}
    for c in frame.columns:
        low = str(c).lower().replace(" ", "").replace("_", "")
        if low in _PART_ALIASES:
            part = _PART_ALIASES[low]
            if part not in found and pd.api.types.is_numeric_dtype(frame[c]):
                found[part] = c
    return found if {"year", "month", "day"} <= set(found) else {}


def resolve_time_axis(raw: RawTable):
    """
    Turn a RawTable into a DataFrame indexed by a sorted DatetimeIndex.

    Handles three representations:
      1. a single datetime column (or netCDF time coordinate)
      2. separate year / month / day / hour columns
      3. Excel serial day numbers

    Duplicate timestamps are dropped (first kept) and reported. Rows whose
    time will not parse are dropped and reported. Timestamps are used
    exactly as written — no timezone handling.
    """
    frame = raw.frame.copy()
    info = TimeInfo(method="", source="", n_rows_in=len(frame))

    tcol = _find_time_column(frame)
    parts = {} if tcol else _find_part_columns(frame)

    if tcol is not None:
        col = frame[tcol]
        if np.issubdtype(np.asarray(col.values).dtype, np.datetime64):
            stamps = pd.to_datetime(col)
            info.method = ("netCDF time coordinate" if tcol == "__nc_time__"
                           else "datetime column")
            info.source = "time" if tcol == "__nc_time__" else str(tcol)
            info.fmt = "decoded by reader"
        elif pd.api.types.is_numeric_dtype(col) and col.dropna().between(1, 100000).all():
            stamps = _excel_serial_to_datetime(col)
            info.method = "Excel serial numbers"
            info.source = str(tcol)
            info.fmt = "days since 1899-12-30"
            info.notes.append("Numeric time column read as Excel serial day numbers.")
        else:
            stamps, fmt, ambiguous = _parse_datetimes(col)
            info.method = "datetime column"
            info.source = str(tcol)
            info.fmt = fmt
            info.ambiguous = ambiguous
            if ambiguous:
                info.warnings.append(
                    f"Date order in '{tcol}' is ambiguous — read as {fmt}. "
                    f"Check the first and last timestamps below are correct."
                )
        frame = frame.drop(columns=[tcol])

    elif parts:
        cols = {p: frame[c] for p, c in parts.items()}
        stamps = pd.to_datetime(dict(
            year=cols["year"].astype("Int64"),
            month=cols["month"].astype("Int64"),
            day=cols["day"].astype("Int64"),
            hour=cols.get("hour", pd.Series(0, index=frame.index)).astype("Int64"),
            minute=cols.get("minute", pd.Series(0, index=frame.index)).astype("Int64"),
            second=cols.get("second", pd.Series(0, index=frame.index)).astype("Int64"),
        ), errors="coerce")
        info.method = "split date columns"
        info.source = ", ".join(parts[p] for p in
                                ("year", "month", "day", "hour", "minute", "second")
                                if p in parts)
        frame = frame.drop(columns=list(parts.values()))

    else:
        raise ValueError(
            f"{raw.name}: no time information found. Expected a column whose "
            f"name contains 'time', 'date' or 'timestamp', or separate "
            f"year/month/day columns. Columns present: "
            f"{', '.join(str(c) for c in frame.columns)}."
        )

    # Drop unparsed rows
    bad = stamps.isna()
    info.n_unparsed = int(bad.sum())
    if info.n_unparsed:
        frame, stamps = frame[~bad], stamps[~bad]
        info.warnings.append(
            f"{info.n_unparsed:,} row(s) had unreadable timestamps and were dropped."
        )
    if len(frame) == 0:
        raise ValueError(f"{raw.name}: no rows remained after timestamp parsing.")

    frame.index = pd.DatetimeIndex(stamps.values, name="time")

    # Sort
    if not frame.index.is_monotonic_increasing:
        info.was_unsorted = True
        frame = frame.sort_index()
        info.notes.append("Rows were not in chronological order and were sorted.")

    # Duplicates
    dup = frame.index.duplicated(keep="first")
    info.n_duplicates = int(dup.sum())
    if info.n_duplicates:
        frame = frame[~dup]
        info.warnings.append(
            f"{info.n_duplicates:,} duplicate timestamp(s) found; first kept."
        )

    # Keep only numeric columns for downstream use
    non_numeric = [c for c in frame.columns
                   if not pd.api.types.is_numeric_dtype(frame[c])]
    for c in non_numeric:
        coerced = pd.to_numeric(frame[c], errors="coerce")
        if coerced.notna().mean() > 0.5:
            frame[c] = coerced
        else:
            frame = frame.drop(columns=[c])
            info.notes.append(f"Dropped non-numeric column '{c}'.")

    # Native resolution from the modal time step
    if len(frame) > 1:
        diffs = np.diff(frame.index.values).astype("timedelta64[s]").astype(float) / 60.0
        diffs = diffs[diffs > 0]
        if diffs.size:
            vals, counts = np.unique(np.round(diffs, 3), return_counts=True)
            info.native_minutes = float(vals[np.argmax(counts)])

    info.n_rows_out = len(frame)
    info.start = frame.index.min()
    info.end = frame.index.max()
    return frame, info


def describe_time(info: TimeInfo) -> str:
    """One-line human summary of a resolved time axis."""
    if info.native_minutes is None:
        res = "unknown"
    elif info.native_minutes < 60:
        res = f"{info.native_minutes:g} min"
    elif info.native_minutes % 60 == 0:
        res = f"{info.native_minutes / 60:g} hr"
    else:
        res = f"{info.native_minutes:g} min"
    return (f"{info.n_rows_out:,} rows | {info.start:%Y-%m-%d %H:%M} to "
            f"{info.end:%Y-%m-%d %H:%M} | {res} resolution | via {info.method}")
