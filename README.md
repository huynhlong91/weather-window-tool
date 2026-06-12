# Marine Survey Weather Window Tool

### User Manual and Scientific Background

-----

## Overview

The Marine Survey Weather Window Tool is a web-based campaign planning application for offshore and marine survey operations. It estimates the total elapsed calendar time required to complete a defined scope of productive marine work under realistic metocean conditions, using a Monte Carlo simulation applied to long-term hindcast SC-DMAP data.

The tool supports simultaneous analysis of up to three operational scenarios, enabling direct comparison of different vessel specifications, operational limits, or campaign start seasons. Results are presented as exceedance probability curves and a summary statistics table.

-----

## Input Data Requirements

The tool requires three hindcast CSV files covering the same time period. All files must include a date/time column whose name contains the word **Time** or **Timestamp** (e.g. `DateTime`, `TimeStamp`).

### Hydrodynamics (20-minute resolution)

|Column  |Description               |Units  |
|--------|--------------------------|-------|
|DateTime|Date and time of record   |—      |
|CSpd    |Current speed             |m/s    |
|CDir    |Current direction (toward)|degrees|

### Waves (hourly resolution)

|Column  |Description                            |Units  |
|--------|---------------------------------------|-------|
|DateTime|Date and time of record                |—      |
|Hs      |Significant wave height                |m      |
|Tp      |Peak wave period                       |s      |
|Tz      |Zero-crossing wave period *(optional)* |s      |
|WaveDir |Mean wave direction (from) *(optional)*|degrees|

### Winds (hourly resolution)

|Column  |Description                              |Units  |
|--------|-----------------------------------------|-------|
|DateTime|Date and time of record                  |—      |
|WSpd10  |Wind speed at 10m                        |m/s    |
|WDir10  |Wind direction (from) at 10m *(optional)*|degrees|


> **Note:** The hindcast record should ideally cover a minimum of 10 years to produce statistically robust results. A record of 20–40 years is recommended.

-----

## Using the Tool

### Step 1 — Load Hindcast Data

Upload the three CSV files using the file uploaders at the top of the page. The tool will automatically detect the datetime column, aggregate hydrodynamics to hourly resolution (retaining the peak current speed within each hour), and merge all three datasets on their common timestamps.

### Step 2 — Configure Scenarios

Up to three scenarios can be configured simultaneously in the Scenario Parameters table. Each scenario has independent settings:

|Parameter                    |Description                                                                                                                                                                             |
|-----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|**Hs Limit (m)**             |Maximum significant wave height for operations                                                                                                                                          |
|**Tp Limit (s)**             |Maximum peak wave period for operations                                                                                                                                                 |
|**Wind Limit (m/s)**         |Maximum wind speed for operations                                                                                                                                                       |
|**Current (m/s)**            |Maximum current speed for operations                                                                                                                                                    |
|**Total Work (hrs)**         |Total productive work hours required to complete the campaign                                                                                                                           |
|**Min Window (hrs)**         |Minimum contiguous weather window duration to be counted as operationally useful                                                                                                        |
|**Interruptible**            |Whether the campaign can be split across multiple weather windows (Yes) or requires a single unbroken window (No)                                                                       |
|**Start Season / Month**     |Restricts campaign start dates to a season or calendar month. Weather windows are searched forward continuously — a March-start campaign will naturally use following month until finish|
|**Low % / High %**           |Percentile bounds for the P-Low and P-High statistics represent optimistic and worst-case scenarios (e.g. 10 / 90 for P10 and P90)                                                      |
|**Limit Wind Dir?**          |Enables a wind direction operability sector                                                                                                                                             |
|**Wind Sector (Min / Max °)**|Inclusive directional sector for wind. Sectors wrapping through North are supported (set Min > Max, e.g. 330° to 030°)                                                                  |
|**Limit Wave Dir?**          |Enables a wave direction operability sector                                                                                                                                             |
|**Wave Sector (Min / Max °)**|Inclusive directional sector for waves                                                                                                                                                  |

### Step 3 — Run the Analysis

Click **RUN COMPARISON** to execute the Monte Carlo simulation. Each scenario runs 100,000 iterations and typically completes in a few seconds.

-----

## Understanding the Results

### Exceedance Probability Curve

The chart shows the probability that total campaign duration will **exceed** a given value:

- **P10 (P-Low):** 90% of simulated campaigns were shorter than this — a favourable / best-case estimate
- **P50:** Median duration — half of all campaigns were shorter, half were longer
- **P90 (P-High):** Only 10% of campaigns were shorter — a conservative planning allowance

A steep curve indicates low variability; a flat, spread-out curve indicates high weather sensitivity.

### Results Table

|Row           |Description                                                              |
|--------------|-------------------------------------------------------------------------|
|**P-Low**     |Duration at the user-defined low percentile. Shown as hours (days)       |
|**P50**       |Median campaign duration. Shown as hours (days)                          |
|**P-High**    |Duration at the user-defined high percentile. Shown as hours (days)      |
|**Avg Dur**   |Arithmetic mean of all 100,000 simulated durations. Shown as hours (days)|
|**Downtime %**|`(Avg Duration − Total Work Hours) / Avg Duration × 100%`                |

-----

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

For each of the 100,000 iterations:

1. A campaign start time is drawn at random from the hindcast. If a season or month is specified, only start times within that period are eligible; the simulation then searches forward through the full window table with no seasonal boundary.
1. The simulation accumulates productive work hours across available windows:
- **Interruptible:** Work is split across consecutive windows. The campaign ends the moment required hours are complete.
- **Non-interruptible:** The simulation searches for a single unbroken window of sufficient duration.
1. Total elapsed calendar time from start to completion is recorded.

Percentiles are derived from the resulting distribution of 100,000 simulated durations using the nearest-rank method.

-----

## Limitations

- Results are conditional on the accuracy of the hindcast dataset. Model bias will directly affect operability estimates.
- The tool does not account for vessel heading, DP capability, access system response, or fatigue accumulation.
- Short hindcast records (< 10 years) may not adequately sample rare weather patterns. P90 estimates from short records should be treated with caution.
- The analysis assumes the historical metocean climate is representative of future conditions.

-----

*Marine Survey Weather Window Tool — Venterra Group*
