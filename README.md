# Marine Survey Weather Window Tool

Weather Window duration analysis web app — Python/Streamlit version of the MATLAB WeatherWindowTool.

---

## Repository structure

```
├── app.py              ← Streamlit UI (run this)
├── engine.py           ← Data processing + Monte Carlo engine
├── requirements.txt    ← Python dependencies
└── README.md
```

---

## Input data format

The app expects the same three CSV files as the MATLAB tool.

| File | Frequency | Required columns |
|---|---|---|
| Hydrodynamics | 20-min | DateTime, CSpd, CDir |
| Waves | 1-hour | DateTime, Hs, Tp (+ optional Tz, Hmax, WaveDir, Tm) |
| Winds | 1-hour | DateTime, WSpd10, WDir10 |

- The datetime column must contain the word **Time** or **Timestamp** (case-insensitive).
- Hindcast data must be gap-free. If gaps exist the intersection merge will reduce the record length.

---

## How the analysis works

1. **Data loading:** Hydrodynamics are aggregated from 20-min to hourly by selecting the row with the highest current speed (CSpd) in each hour. All three datasets are then merged on their common timestamps.

2. **Operability windows:** For each scenario, every hour in the full hindcast is marked feasible or not based on the Hs / Tp / Wind / Current thresholds and optional direction sector filters. Contiguous runs of feasible hours longer than the minimum window duration are recorded.

3. **Monte Carlo simulation (50,000 iterations):** A random campaign start time is drawn from the hindcast. If a season or month is selected, only start times in that season/month are eligible — but windows are searched forward continuously from each start regardless of month boundary (a March-start campaign can use April/May/June windows). Total elapsed calendar time to complete the required productive hours is recorded.

4. **Output:** The distribution of 50,000 simulated campaign durations is plotted as an exceedance probability curve. P-Low, P50, P-High, average duration, and downtime % are reported in the table.

---

## Local development (optional)

```bash
pip install streamlit pandas numpy plotly
streamlit run app.py
```

The app opens at `http://localhost:8501`.
