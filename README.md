# Interactive Data Dashboards

A small collection of self-built, interactive web dashboards, kept here as a
public portfolio piece. Each dashboard is a single, self-contained HTML file
(vanilla JavaScript + [Chart.js](https://www.chartjs.org/)) and is published
via GitHub Pages — no build step, no framework.

**Live site:** [volmar86.github.io/dashboards](https://volmar86.github.io/dashboards/)

## Tech stack

- **HTML5 / CSS3** — layout and styling (Tailwind via CDN)
- **Vanilla JavaScript** — interactivity, state, simulation logic (no framework)
- **Chart.js** — data visualisation
- **Python** — data-fetching and processing scripts that build `data.json` and
  `history.json` (pull from the AGSI API, compute rolling and seasonal rates,
  keep the full daily history per country)
- **GitHub Actions** — scheduled workflows that run the Python scripts weekly
- **GitHub Pages** — hosting

---

## Dashboards

### ⛽ Gas Storage Predictor (live data) — [view live](https://volmar86.github.io/dashboards/dashboards_live/lng-predictor/)
`dashboards_live/lng-predictor/`

Tracks and projects European natural-gas storage levels — the EU average
alongside Italy and Germany specifically. It combines real historical
inventory data with adjustable linear and seasonal fill/withdrawal rates,
letting the user simulate different trajectories toward the winter target
levels.

- **Data source:** [GIE AGSI](https://agsi.gie.eu/) platform
- **Auto-updating:** a scheduled GitHub Action refreshes `data.json` every week
- **Resilience:** if the live feed is unavailable, the page falls back to a
  bundled `data_fallback.json` snapshot, so it never renders empty

### 📈 Gas Storage History (live data) — [view live](https://volmar86.github.io/dashboards/dashboards_live/lng-history/)
`dashboards_live/lng-history/`

Looks back instead of forward: the filling level of every country reported on
AGSI (plus the EU aggregate), one curve per storage season. A season runs from
1 March to 1 March, so the summer injection and the winter withdrawal that
follows it sit in the same picture. It makes no forecast.

- **One country:** a grey min-max band over the last N complete seasons
  (3, 5, 7, 10 or 15, default 5), the individual seasons, the median, and the
  current season highlighted in red
- **Several countries (up to 5, EU included):** the current season of each one,
  side by side
- **Where we stand:** for every selected country, the latest level against the
  historical median and band, its position among past years, and the change
  versus the same day of the previous season
- **Data source:** [GIE AGSI](https://agsi.gie.eu/) platform, daily series from
  2011 where available (countries that joined AGSI later use the years they have)
- **Auto-updating:** a second scheduled GitHub Action refreshes `history.json`
  every week; it resumes from the last saved day, repairs gaps, and survives
  API rate limits

### 🌊 Hormuz Critical-Level Monitor (static data) — [view live](https://volmar86.github.io/dashboards/dashboards_static/)
`dashboards_static/`

Estimates when gas storage would reach critical thresholds under stress
scenarios. Data is embedded directly in the page (no external feed).

### 🎨 Montessori Teaching Dashboards — [view live](https://volmar86.github.io/dashboards/montessori_dashboards/)
`montessori_dashboards/`

Interactive tools intended for use in my Montessori teaching activity.

---

## Repository structure

```text
dashboards/
├── .github/workflows/          # Scheduled GitHub Actions (weekly data updates)
│   ├── update_data.yml         # Refreshes the predictor's data.json
│   └── update_history.yml      # Refreshes the history page's history.json
├── dashboards_live/            # Dashboards fed by external, auto-updated data
│   ├── lng-predictor/
│   │   ├── index.html          # The dashboard
│   │   ├── data.json           # Live data (refreshed by the Action)
│   │   └── data_fallback.json  # Frozen backup snapshot
│   └── lng-history/
│       ├── index.html          # The dashboard
│       ├── fetch_history.py    # Downloads the daily series from AGSI
│       └── history.json        # Daily history per country (refreshed by the Action)
├── dashboards_static/          # Dashboards with data embedded in the HTML
├── montessori_dashboards/      # Teaching tools
└── README.md
```

## How the live update works

Two GitHub Actions run on a weekly schedule.

- **Predictor** (Mondays): a Python script embedded in `update_data.yml` pulls
  the latest figures from the GIE AGSI API for the EU, Germany and Italy,
  recomputes the rolling and seasonal rates, and commits the refreshed
  `data.json`. The time window rolls over every 1 April.
- **History** (Tuesdays): `fetch_history.py` downloads the daily filling level of
  each country, from 2011 onwards, into `history.json`. On every run it reloads
  the existing file and only fetches what is new, plus a short overlap to pick
  up AGSI corrections; any gaps in the series are re-downloaded. If the API
  throttles the requests it waits and retries, and if the run uses up its time
  budget it saves what it has and the next run continues from there.

Each dashboard reads its JSON file on load.

---

*Built and maintained by [@volmar86](https://github.com/volmar86).*
