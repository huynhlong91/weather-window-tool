# Marine Survey Weather Window Tool

Monte Carlo campaign duration analysis web app — Python/Streamlit version of the MATLAB WeatherWindowTool.

---

## Repository structure

```
├── app.py              ← Streamlit UI (run this)
├── engine.py           ← Data processing + Monte Carlo engine
├── requirements.txt    ← Python dependencies
└── README.md
```

---

## Deploying to Streamlit Cloud (step by step)

### Step 1 — Create a GitHub repository

1. Go to [github.com](https://github.com) → **New repository**
2. Name it e.g. `weather-window-tool`
3. Set to **Private** (recommended for client work)
4. Click **Create repository**

### Step 2 — Upload the files

Option A — via the GitHub web interface:
1. Open your new repository
2. Click **Add file → Upload files**
3. Drag and drop all four files: `app.py`, `engine.py`, `requirements.txt`, `README.md`
4. Click **Commit changes**

Option B — via Git (if you have it installed):
```bash
git clone https://github.com/YOUR-USERNAME/weather-window-tool.git
cd weather-window-tool
# copy your four files here
git add .
git commit -m "Initial deployment"
git push
```

### Step 3 — Deploy on Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Sign in with your GitHub account (you said this is already linked ✅)
3. Click **New app**
4. Fill in:
   - **Repository:** `YOUR-USERNAME/weather-window-tool`
   - **Branch:** `main`
   - **Main file path:** `app.py`
5. Click **Deploy**

Streamlit will install dependencies from `requirements.txt` automatically.
Deployment takes 1–3 minutes. You will get a public URL like:

```
https://your-username-weather-window-tool-app-xxxxxx.streamlit.app
```

Share this URL with EirGrid — no installation, no IT restrictions.

### Step 4 — Updating the app

Any time you push a change to GitHub, Streamlit Cloud redeploys automatically within ~1 minute.

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
