# Flood calculator: how it works

A technical note for judges and teammates. Code: `src/flood/`. App tab: `views/flood_calculator.py`. CLI: `python -m src.flood`.

## Pipeline

```text
rain source                          terrain                    drains
(today / past date / typed in)       AWS Terrain Tiles          DataSF inlets (23,875)
        |                                   |                          |
weather_input -> RainEvent          terrain.load_dem -> Grid    drains.load_inlets
(15-min rain rates, mm/h)           (30 m, 480 x 485 cells)            |
        |                                   |                drains.capacity_grid
        |                                   +------------->  min(inlet intake, pipe allowance)
        |                                   |                          |
        +---------------------> hydro.simulate(grid, cap, rain) <------+
        |                       runoff -> drains -> flow downhill -> ponds
        |                                   |
frequency.commonality(rain)          FloodResult (depth per cell, volumes)
(NOAA Atlas 14 + gauge record)              |
        +---------------------> calc.run -> Scenario -> render (map, PNG) / cli (text)
```

## 1. Rain (`weather_input.py`)

- **Today:** NWS observations so far today plus the NWS gridpoint forecast (`quantitativePrecipitation`) for the rest of the day.
  The downtown station (SFOC1) does not publish rain through `api.weather.gov`, so observed rain comes from KSFO (SF Airport, about 18 km south), then KOAK.
  Only routine hourly METARs are used; special reports would double count.
- **Past date:** daily total from NOAA GHCN-Daily, SF Downtown (USW00023272), back to 1921.
  If hourly timing is cached for that day, it is scaled to the downtown total. Otherwise the day is spread over 24 h with the NRCS (SCS) Type I curve.
  GHCN lags about a week (the cache runs through Nov 13, 2026). Newer days use the NWS hourly gauge total instead.
- **Typed in:** depth 0–30 in, duration 0.25–72 h, shape `scs1` (NRCS Type I), `uniform`, `front` or `burst`.
- Time step: 15 minutes. Open-Meteo is a fallback only (often rate-limited).

## 2. Terrain (`terrain.py`)

- AWS Terrain Tiles, terrarium format, zoom 14 (about 7.5 m pixels at SF). In the US these come from USGS 3DEP/NED.
- Decode: elevation = R*256 + G + B/256 - 32768 (m).
- Bilinear resample onto a regular lat/lon grid over `SF_BBOX = (37.705, 37.835, -122.52, -122.355)`, 30 m cells by default (60 m option).
- Land mask = cells whose centre is inside the union of DataSF Analysis Neighborhoods. 30 m grid: 480 x 485 cells, 134,560 land cells.

## 3. Drain capacity (`drains.py`)

Each cell can remove at most `min(inlet intake, pipe allowance)`.

**Inlet intake** by `drain_type` (`CAPACITY_CFS`, reasoned from HEC-22 weir formulas; SFPUC publishes no per-inlet rating):

| Inlet type | cfs |
| --- | --- |
| Catch Basin Drain, Storm Water Inlet Drain (21,716 + 1,330 inlets) | 2.0 |
| 36 in square CB with curb inlet; Caltrans GDO combination | 4.0 |
| Caltrans GO combination | 3.5 |
| Drop Inlet; Caltrans G3/G4 grate | 3.0 |
| Caltrans G1/G2 grate | 2.5 |
| Caltrans A2 | 2.0 |
| Curb inlet with no grate; manhole grates | 1.5 |
| Area drain, drain box, trench drain | 1.0 |
| Track drain, scupper, LID inflow point | 0.5 |
| Culvert wing wall | 10.0 |
| Outlets, bubblers, tree wells, switchbox drains, wetwells | 0 |

- Catch basin: a 2 x 3 ft grate in a sag at 4 in of water gives about 4 cfs (Q = 3.0 * P * d^1.5). Most SF basins sit on a slope and design practice assumes about 50 % grate clogging, so we use 2.0 cfs.
- "Psuedo Drain for Model ONLY" rows are dropped. Unknown types get 2.0 cfs. Total: 23,875 inlets, 47,222 cfs.
- The "Blocked drains" setting (`clog_pct`) cuts every inlet's intake by that percent.

**Pipe allowance:** the pipes behind the inlets are sized for SFPUC's design storm, 5-year 3-hour.
NOAA Atlas 14 gives 1.24 in in 3 h for SF, so the pipe limit is 1.24 * 25.4 / 3 = **10.5 mm/h** of rain.
Every land cell belongs to its nearest inlet (within `MAX_SERVICE_M = 300 m`; big parks farther away feed no pipe).
Allowance = 10.5 mm/h x runoff coefficient x land area the inlet serves.

## 4. Water on the ground (`hydro.py`)

- Runoff = rain rate x runoff coefficient, default **0.85** (SF is mostly paved; setting 0.50–0.95).
- Outlets: bay, ocean, land at or below 0 m, and the grid edge. Water reaching them leaves ("to the bay").
- Priority-flood fill (Barnes et al. 2014) finds closed low spots. Pits deeper than 2.0 m are treated as culverts: their lowest cell becomes an outlet, so they do not fill.
- D8 steepest-descent flow directions on the filled surface; flats drain by breadth-first search.
- Each low spot becomes one "pond" with a volume-vs-level curve. Its drains keep emptying it during and after the storm. Overflow spills downstream.
- Water is routed instantly upstream to downstream within each 15-min step (SF catchments drain faster than one step).
- Flowing depth uses Manning's wide-channel formula: h = (Q n / (W sqrt(S)))^(3/5), with n = **0.016** (asphalt), W = **12 m** (typical SF street, curb to curb), S = max(slope, 0.005).
- Depth per cell = the highest pond level or flowing depth during the event.
- Mass balance: runoff = drained + still ponded + to the bay, checked to under 1 % in tests.
- "Flooded" = 0.15 m (6 in) or more. "Deep" = 0.5 m or more. A drain is "overwhelmed" when water arriving beat its intake at some step.

## 5. How common is it? (`frequency.py`)

- NOAA Atlas 14 Vol. 6, partial-duration series, point depths for SF (37.7749, -122.4194) from the PFDS server.
- Key values (inches):

| Duration | 1-yr | 2-yr | 5-yr | 10-yr | 25-yr | 100-yr |
| --- | --- | --- | --- | --- | --- | --- |
| 1 h | 0.465 | 0.575 | 0.723 | 0.846 | 1.02 | 1.29 |
| 3 h | 0.815 | 0.994 | 1.24 | 1.44 | 1.72 | 2.19 |
| 6 h | 1.10 | 1.34 | 1.68 | 1.96 | 2.35 | 3.00 |
| 24 h | 1.83 | 2.29 | 2.93 | 3.48 | 4.26 | 5.59 |

- The storm is scanned for its heaviest 15 min, 30 min, 1, 2, 3, 6, 12, 24 and 48 h (plus the whole event). The rarest window sets the return period (log-log interpolation in the table).
- Second check: how many days since 1921 the downtown gauge has been at least this wet (GHCN-Daily).
- Output is a sentence, e.g. "happens about once every 7 years in San Francisco — a 15% chance in any year."

## Cached data (`data/processed/`)

The app and tests run offline from these files. All are committed.

| File | Contents | Size | Refresh |
| --- | --- | --- | --- |
| `sf_dem_30m.npz` | elevation grid + land mask | 0.85 MB | delete it, then `terrain.load_dem()` |
| `inlets_capacity.csv` | 23,875 inlets with type and cfs | 2.1 MB | `drains.load_inlets(cache=False)` |
| `atlas14_sf.csv` | Atlas 14 depth table (in) | 1 KB | `frequency.atlas14_table(cache=False)` |
| `ghcn_sf_daily.csv` | daily rain since 1921 (38,620 days) | 0.6 MB | refreshes itself when over 1 day old; or `weather_input.load_ghcn(cache=False)` |
| `hourly_rain_cache.json` | hourly timing for recent days + last "today" fetch | 4 KB | written by `from_today` / `from_date` |
| `sf_neighborhoods.geojson` | DataSF neighborhoods (boundary + names) | 0.9 MB | `calc.load_neighborhoods_geojson(cache=False)` |

Run the refresh calls from the repo root, e.g. `python -c "from src.flood import drains; drains.load_inlets(cache=False)"`.
If a download fails, each loader falls back to the saved file.

## Performance (30 m grid, measured in the dev container)

- Load grid + inlets from cache: 0.6 s.
- First simulation in a process (terrain prep, then cached): 1.3 s. Later runs: 0.5 s (6 h storm) to 0.9 s (24 h storm).
- Full CLI command, including Python start-up: about 2.6 s for a past date or typed storm, about 4 s for `--today` (network).

## Example results

| Storm | How common | Flooded (6 in+) | Drains took | Overwhelmed inlets |
| --- | --- | --- | --- | --- |
| Nov 18, 2026 (2.69 in in 20 h, KSFO gauge) | about 1 in 6.5 yr | 2.44 km2 (602 acres) | 72 % | 5,092 |
| 2.5 in in 6 h, burst | about 1 in 83 yr | 3.10 km2 (766 acres) | 61 % | 7,336 |

Worst areas in both: Bayview Hunters Point, Mission, Mission Bay, Sunset/Parkside.

## Limits

- This is a surface model on a 30 m grid, not SFPUC's sewer model. The pipe network is not public, so intake and pipe capacity are estimates.
- SF's sewers are mostly combined (storm + sanitary). Backups, pump stations and tides are not modeled. The bay is a free outlet.
- Buildings, curbs and underpasses are not in the elevation data. Each low spot is one flat pool, so the deepest values (several feet in a few pits) are upper bounds.
- No infiltration model: one runoff coefficient for the whole city, including parks.
- Rain is the same everywhere in the city. Recent and today's rain come from the airport gauge, 18 km south of downtown.
- Atlas 14 is a point estimate for downtown SF. Return periods above the table (1,000 yr) are reported as "rarer than 1-in-1000-year".
- Not yet checked against measured flood depths. The 311 reports from Nov 18 are the best test we have.
