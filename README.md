# Marine Survey Weather Window Tool
### User Manual and Scientific Background

---

## Overview

The Marine Survey Weather Window Tool is a web-based campaign planning application for offshore and marine survey operations. It estimates the total elapsed calendar time required to complete a defined scope of productive marine work under realistic metocean conditions, using a Monte Carlo simulation applied to long-term hindcast data.

The tool supports simultaneous analysis of up to three operational scenarios, enabling direct comparison of different vessel specifications, operational limits, or campaign start seasons. Results are presented as exceedance probability curves and a summary statistics table.

---

## Input Data Requirements

The tool accepts **any mix of file formats**, as one file or several:

| Format | Extensions |
|---|---|
| Delimited text | `.csv` `.txt` `.asc` `.dat` `.tsv` |
| Excel | `.xlsx` `.xls` |
| netCDF timeseries | `.nc` |

Text files may use commas, semicolons, tabs or whitespace as separators, and may carry comment lines or preamble before the header row — these are detected automatically.

### Time information

Any one of the following is recognised:

- a single date/time column (a column whose name contains *time*, *date* or *timestamp*)
- separate `Year` / `Month` / `Day` / `Hour` columns
- Excel serial date numbers

Timestamps are used **exactly as written in the file**. No timezone conversion is applied.

Records finer than hourly are aggregated to hourly; records coarser than hourly (3-hourly, 6-hourly) are interpolated up to hourly, with the interpolation confined to short gaps so genuine holes in the record are preserved.

### Variables

Variables are identified automatically from column names, and — for netCDF — from `standard_name` and `long_name` attributes. Recognised naming conventions include ERA5 short names (`swh`, `pp1d`, `mwd`, `u10`, `v10`), CF standard names, and common hindcast exports.

| Canonical variable | Description | Used for |
|---|---|---|
| `Hs` | Significant wave height | Wave height limit |
| `Tp` | Peak wave period | Wave period limit |
| `WSpd` | Wind speed | Wind limit |
| `CSpd` | Current speed | Current limit |
| `WDir` | Wind direction | Wind sector filter |
| `WaveDir` | Wave direction | Wave sector filter |
| `Tz`, `Tm`, `Hmax`, `CDir` | Additional parameters | Reported only |

**Not every variable is required.** The scenario matrix offers only the constraints your dataset actually supports — a dataset with waves and wind but no current simply has no current limit.

**Vector components are handled.** Where speed and direction are absent but `u`/`v` components are present, speed and direction are derived. Wind and wave directions use the *coming-from* convention; current direction uses *going-to*.

**Units are checked.** Declared units are read where available and converted as needed (knots, cm/s, feet, radians). Where units are not declared they are inferred from the value range and flagged for confirmation.

**The proposed mapping is always shown for you to confirm or override before analysis**, because a mis-identified column produces results that look plausible and are wrong.

## Using the Tool

### Step 1 — Load data (Data tab)

Upload your files. For each one the tool reports how the time axis was read, the record length and native resolution, and any warnings.

You are then shown the **proposed variable mapping** — which column became which variable, the units detected, any conversion applied, and how confident the match was. Check this and adjust anything that looks wrong before continuing.

Finally the **data inventory** summarises the merged dataset: record length and completeness, per-variable coverage and value ranges, any gaps longer than one hour, and how many years of data exist for each calendar month.

### Step 2 — Configure Scenarios

Up to three scenarios can be configured simultaneously in the Scenario Parameters table. Each scenario has independent settings:

| Parameter | Description |
|---|---|
| **Hs Limit (m)** | Maximum significant wave height for operations |
| **Tp Limit (s)** | Maximum peak wave period for operations |
| **Wind Limit (m/s)** | Maximum wind speed for operations |
| **Current (m/s)** | Maximum current speed for operations |
| **Total Work (hrs)** | Total productive work hours required to complete the campaign |
| **Min Window (hrs)** | Minimum contiguous weather window duration to be counted as operationally useful |
| **Interruptible** | Whether the campaign can be split across multiple weather windows (Yes) or requires a single unbroken window (No) |
| **Start Season / Month** | Restricts campaign start dates to a season or calendar month. Weather windows are searched forward continuously — a March-start campaign will naturally use April, May, and June windows |
| **Low % / High %** | Percentile bounds for the P-Low and P-High statistics (e.g. 10 / 90 for P10 and P90) |
| **Limit Wind Dir?** | Enables a wind direction operability sector |
| **Wind Sector (Min / Max °)** | Inclusive directional sector for wind. Sectors wrapping through North are supported (set Min > Max, e.g. 330° to 030°) |
| **Limit Wave Dir?** | Enables a wave direction operability sector |
| **Wave Sector (Min / Max °)** | Inclusive directional sector for waves |

Each threshold also has an on/off toggle, so a constraint present in the data can be excluded from a given scenario without editing files.

#### Missing data policy

Where an active constraint has gaps, choose how those hours are treated:

- **Treat as non-operable** *(default)* — the hours are kept and counted as unworkable. Conservative, and keeps the time axis continuous.
- **Exclude from the record** — the hours are removed and do not count in the operability denominator. Fairer when a variable is patchy. Window identification then enforces timestamp contiguity, so hours far apart in real time cannot merge into a single window.

### Step 3 — Run the Analysis

Click **RUN COMPARISON** to execute the Monte Carlo simulation. Each scenario runs 1,000,000 iterations and typically completes in a few seconds.

---

## Understanding the Results

### Exceedance Probability Curve

The chart shows the probability that total campaign duration will **exceed** a given value:

- **P10 (P-Low):** 90% of simulated campaigns were shorter than this — a favourable / best-case estimate
- **P50:** Median duration — half of all campaigns were shorter, half were longer
- **P90 (P-High):** Only 10% of campaigns were shorter — a conservative planning allowance

A steep curve indicates low variability; a flat, spread-out curve indicates high weather sensitivity.

### Monthly Operability Matrix

The second results tab shows a colour-coded matrix of operability by calendar month, for each scenario.

Each cell gives the percentage of hours in that month, across every year of the hindcast, that both meet all the operability limits **and** fall inside a weather window long enough to satisfy the Min Window setting. Isolated feasible hours occurring in gaps too short to work in are excluded, so the figure reflects genuinely usable time rather than raw threshold compliance.

The colour scale is shared across all three scenarios and auto-scales to the range present in the data, so months and scenarios can be compared directly.

> **Note:** This matrix is calculated from the hindcast record and is independent of the Start Season / Month setting, which affects only the Monte Carlo simulation. It will not exactly equal `100 − Downtime %`, because the campaign statistics also include waiting time before the first usable window and carry forward into subsequent months.

### Results Table

| Row | Description |
|---|---|
| **P-Low** | Duration at the user-defined low percentile. Shown as hours (days) |
| **P50** | Median campaign duration. Shown as hours (days) |
| **P-High** | Duration at the user-defined high percentile. Shown as hours (days) |
| **Avg Dur** | Arithmetic mean of all simulated durations. Shown as hours (days) |
| **Downtime %** | `(Avg Duration − Total Work Hours) / Avg Duration × 100%` |

---

## Scientific Methodology

### 1. Data Processing

The 20-minute hydrodynamics are aggregated to hourly resolution by retaining the record with the highest current speed within each hour. This conservative approach captures peak current loading rather than averaging it away. The three datasets are then merged on the intersection of their timestamps with no interpolation applied.

### 2. Operability Analysis

At each hour *t*, a binary feasibility flag is computed. An hour is feasible if all of the following are simultaneously satisfied:

- Hs(t) ≤ Hs limit
- Tp(t) ≤ Tp limit
- WSpd10(t) ≤ Wind limit
- CSpd(t) ≤ Current limit
- Wind direction within sector *(if enabled)*
- Wave direction within sector *(if enabled)*

Contiguous sequences of feasible hours are identified as weather windows. Windows shorter than the minimum window duration are discarded. This analysis is performed on the **full unfiltered hindcast** — the season/month setting affects only the Monte Carlo start-time pool, not the window identification.

### 3. Monte Carlo Simulation

For each of the 1,000,000 iterations:

1. A campaign start time is drawn at random from the hindcast. If a season or month is specified, only start times within that period are eligible; the simulation then searches forward through the full window table with no seasonal boundary.

2. The simulation accumulates productive work hours across available windows:
   - **Interruptible:** Work is split across consecutive windows. The campaign ends the moment required hours are complete.
   - **Non-interruptible:** The simulation searches for a single unbroken window of sufficient duration.

3. Total elapsed calendar time from start to completion is recorded.

Percentiles are derived from the resulting distribution of simulated durations using the nearest-rank method.

---

## Limitations

- Results are conditional on the accuracy of the hindcast dataset. Model bias will directly affect operability estimates.
- The tool does not account for vessel heading, DP capability, access system response, or fatigue accumulation.
- Short hindcast records (< 10 years) may not adequately sample rare weather patterns. P90 estimates from short records should be treated with caution.
- The analysis assumes the historical metocean climate is representative of future conditions.

---

## Version History

### v1.2 — August 2026

- **Universal data loading.** Accepts `.csv`, `.txt`, `.asc`, `.dat`, `.tsv`, `.xlsx`, `.xls` and `.nc`, as one file or several. Delimiters, comment lines and preamble rows are detected automatically.
- **Automatic variable identification**, with the proposed mapping always shown for confirmation. Covers ERA5 short names, CF standard names and common hindcast exports.
- **Vector component derivation.** Wind and current speed and direction are derived from `u`/`v` components where speed and direction are not supplied directly.
- **Unit detection and conversion** for knots, cm/s, feet and radians, with inferred units flagged for confirmation.
- **Flexible time handling.** Single datetime column, split year/month/day/hour columns, or Excel serial numbers. Sub-hourly data is aggregated; coarser data is interpolated up to hourly without bridging genuine gaps.
- **Data inventory** reporting record length, completeness, per-variable coverage, gaps longer than one hour, and years sampled per calendar month.
- **Adaptive scenario matrix.** Only constraints supported by the dataset are offered, each with an on/off toggle.
- **Missing data policy**, selectable between treating gaps as non-operable or excluding them from the record.

### v1.1 — August 2026

- **Monthly operability matrix** added as a second results tab. Colour-coded percentage of usable hours by calendar month for each scenario, with a shared auto-scaling colour range.
- **Min Window handling in non-interruptible mode.** When a campaign cannot be split, the Min Window field is now locked to Total Work and disabled. Previously the field could be set to a value that had no effect, which was not apparent from the interface.
- **Input validation.** A warning is now shown if Min Window is set larger than Total Work in an interruptible scenario, as this discards windows that would be long enough to complete the campaign.
- **Monte Carlo iterations increased** from 50,000 to 1,000,000 for tighter percentile estimates, with no increase in run time.
- **Run feedback.** Previous results are cleared when a new run starts, a progress indicator shows which scenario is running, and the completion message is timestamped.

### v1.0 — Initial release

- Three concurrent scenarios with independent operability thresholds, campaign parameters, and directional sector filters.
- Monte Carlo campaign duration simulation with interruptible and non-interruptible modes.
- Exceedance probability curve and campaign duration statistics table.
- Per-scenario Low/High percentile selection.
- Results shown in hours with days in brackets.

---

*Marine Survey Weather Window Tool — Venterra Group*
