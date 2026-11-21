"""SF storm-drain inlets -> design intake per inlet -> drain capacity per grid cell.

Data: DataSF "Stormwater inlets, drains and catch basins" (jtgq-b7c5), ~23.9k mapped inlets.
Cached to data/processed/inlets_capacity.csv so the app works offline.

Capacity model (simplified level of service; SFPUC does not publish its pipe network):
- Each inlet can swallow at most CAPACITY_CFS[drain_type] (grate / curb-opening hydraulics).
- The pipes behind the inlets are sized for SFPUC's design storm (5-year, 3-hour). We turn that
  into a rate (mm/h of rain x runoff coefficient) over the land each inlet serves
  (every land cell belongs to its nearest inlet). A cell's drain capacity is the smaller of
  its inlets' intake and the pipe allowance of the area those inlets serve.
"""
import os

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

from src.flood import M_PER_DEG_LAT, M_PER_DEG_LON, MM_PER_IN, SF_BBOX

DATASET_URL = "https://data.sf.gov/resource/jtgq-b7c5.json"
CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed", "inlets_capacity.csv")
CFS_TO_M3S = 0.0283168
COLUMNS = ["asset_id", "lat", "lon", "drain_type", "system_use", "capacity_cfs", "capacity_m3s"]

# ---------------------------------------------------------------------------------------------
# Design intake per inlet (cubic feet per second).
# Basis: FHWA HEC-22, Urban Drainage Design Manual (3rd ed., 2009; 4th ed. 2024), Ch. 4 inlets.
#   Grate in sag, weir flow:        Q = Cw * P * d^1.5   (Cw = 3.0, P = grate perimeter ft, d = depth ft)
#   Curb opening in sag, weir flow: Q = Cw * L * d^1.5   (Cw = 2.3, L = opening length ft)
#   On-grade inlets intercept only part of the gutter flow (typically 1-3 cfs on city streets).
# Standard SF catch basin (SFPUC Wastewater typical detail CB-1.1 / CB-1.10, cast-iron grate
# roughly 2 ft x 3 ft behind a curb opening). In sag at 4 in. (0.33 ft) ponding the grate alone
# gives 3.0 * 7 ft * 0.33^1.5 ~ 4 cfs; most SF basins sit on grade and design practice applies
# ~50 % clogging to grates, so we use 2.0 cfs as the typical working intake.
# No published SFPUC cfs rating per inlet type was found; these are reasoned HEC-22 values.
# Outlets, tree wells, bubblers (discharge points), switchboxes and wetwells take no surface water.
# ---------------------------------------------------------------------------------------------
CATCH_BASIN_CFS = 2.0
CAPACITY_CFS = {
    "Catch Basin Drain": CATCH_BASIN_CFS,              # standard SF CB, grate + curb opening
    "Storm Water Inlet Drain": CATCH_BASIN_CFS,        # same hardware class as a CB
    "36in Square CB with Curb Inlet (IN-GO)": 4.0,     # 3x3 ft grate (P=9 ft) + curb opening
    "Drop Inlet": 3.0,                                 # grated box in a sag (median / lot)
    "Caltrans G1 Grate Inlet": 2.5,                    # Caltrans G-type grates, ~2x3 ft
    "Caltrans G2 Grate Inlet": 2.5,
    "Caltrans G3 Grate Inlet": 3.0,
    "Caltrans G4 Grate Inlet": 3.0,
    "Caltrans GDO Combination Inlet": 4.0,             # double grate + curb opening
    "Caltrans GO Combination Inlet": 3.5,              # grate + curb opening
    "Caltrans A2 Drain Inlet": 2.0,
    "Curb Inlet: Drain w/ no grate": 1.5,              # 2.3 * ~3.5 ft * 0.33^1.5 ~ 1.5 cfs
    "MH Inlet: Grated Cover": 1.5,                     # 24 in. round grate, P ~ 6.3 ft, half open
    "Square Hinged MH Cover": 1.5,
    "Area Drain, used in Mission Bay": 1.0,            # small yard/plaza grate
    "Drain Box": 1.0,
    "Trench Drain": 1.0,                               # narrow slotted channel
    "Track Drain": 0.5,                                # rail-trackway drain
    "Scupper-Hole in wall for surface flow": 0.5,
    "LID / Other surface flow inlet point": 0.5,       # green-infrastructure overflow inlet
    "Wing Wall:Culvert entrance:angled sides": 10.0,   # open culvert headwall, channel flow
    "LID / Other surface flow outlet point": 0.0,      # outlets: water comes OUT here
    "Drainage Bubbler (LID-EIP-Green Infra.)": 0.0,    # discharge bubbler
    "Tree Well; drains soil of tree planter": 0.0,     # subsurface soil drain only
    "Switchbox Drain": 0.0,                            # utility vault drain, not a street inlet
    "Wetwell": 0.0,                                    # pump-station wet well
    "Psuedo Drain for Model ONLY": 0.0,                # not real; dropped in load_inlets
}
DROP_TYPES = {"Psuedo Drain for Model ONLY"}

# ---------------------------------------------------------------------------------------------
# Pipe level of service. SFPUC sizes the combined sewer for the 5-year, 3-hour design storm
# (SFPUC Stormwater Management Requirements / Hydrology Calculation Methods; SFPUC quotes
# ~1.3 in, Islais Creek planning docs ~1.14 in of runoff). NOAA Atlas 14 Vol. 6 v2, PDS at
# 37.77 N, -122.42 W gives 1.24 in for 5-yr 3-hr (fetched 2026-11-21) -> 0.41 in/h = 10.5 mm/h.
# ---------------------------------------------------------------------------------------------
PIPE_DESIGN_STORM = {"return_period_yr": 5, "duration_hr": 3.0, "depth_in": 1.24,
                     "source": "NOAA Atlas 14 Vol.6 v2 PDS, 37.77N -122.42W; SFPUC 5-yr 3-hr LOS"}
MAX_SERVICE_M = 300.0   # land farther than this from any inlet (big parks) feeds no pipe allowance


def pipe_limit_mm_hr():
    """Average rain rate (mm/h) of the design storm the sewers are sized to carry."""
    return PIPE_DESIGN_STORM["depth_in"] * MM_PER_IN / PIPE_DESIGN_STORM["duration_hr"]


def capacity_for(drain_type):
    """Design intake (cfs) for a drain_type; unknown types get the catch-basin default."""
    return float(CAPACITY_CFS.get(drain_type, CATCH_BASIN_CFS))


def _fetch(page=50000):
    rows, offset = [], 0
    while True:
        params = {"$select": "puc_maximo_asset_id,dpw_node_id,drain_type,system_use,location",
                  "$order": "dpw_node_id", "$limit": page, "$offset": offset}
        r = requests.get(DATASET_URL, params=params, timeout=120)
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < page:
            return rows
        offset += page


def _to_frame(rows):
    out = []
    for r in rows:
        loc = r.get("location") or {}
        coords = loc.get("coordinates") if isinstance(loc, dict) else None
        if not coords or len(coords) != 2:
            continue
        out.append({"asset_id": r.get("puc_maximo_asset_id") or f"node-{r.get('dpw_node_id', '')}",
                    "lat": float(coords[1]), "lon": float(coords[0]),
                    "drain_type": r.get("drain_type") or "Unknown",
                    "system_use": r.get("system_use") or ""})
    df = pd.DataFrame(out, columns=COLUMNS[:5])
    df = df[~df["drain_type"].isin(DROP_TYPES)]
    df = df[df["lat"].between(SF_BBOX[0], SF_BBOX[1]) & df["lon"].between(SF_BBOX[2], SF_BBOX[3])]
    df = df.drop_duplicates("asset_id").reset_index(drop=True)
    df["lat"], df["lon"] = df["lat"].round(6), df["lon"].round(6)
    return _add_capacity(df)


def _add_capacity(df):
    df["capacity_cfs"] = df["drain_type"].map(capacity_for).astype(float)
    df["capacity_m3s"] = (df["capacity_cfs"] * CFS_TO_M3S).round(5)
    return df[COLUMNS]


def load_inlets(cache=True):
    """Every mapped SF inlet with a design intake. Uses the CSV cache when present."""
    if cache and os.path.exists(CACHE):
        df = pd.read_csv(CACHE, dtype={"asset_id": str})
        return _add_capacity(df)   # re-derive capacity so CAPACITY_CFS edits apply
    try:
        df = _to_frame(_fetch())
    except (requests.RequestException, ValueError) as e:
        if os.path.exists(CACHE):
            return _add_capacity(pd.read_csv(CACHE, dtype={"asset_id": str}))
        raise RuntimeError(f"Could not download SF storm-drain inlets and no cache exists: {e}") from e
    if df.empty:
        raise RuntimeError("DataSF returned no storm-drain inlets inside SF_BBOX")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    df.drop(columns=["capacity_m3s"]).to_csv(CACHE, index=False)
    return df


def _land(grid):
    return grid.land if grid.land is not None else np.isfinite(grid.z)


def _inlet_cells(grid, inlets):
    """(row, col, inside) for each inlet."""
    r, c = grid.rc(inlets["lat"].to_numpy(), inlets["lon"].to_numpy())
    rows, cols = grid.shape
    inside = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
    return r, c, inside


def _sum_to_cells(grid, r, c, values):
    out = np.zeros(grid.shape, float)
    np.add.at(out, (r, c), values)
    return out


def intake_grid(grid, inlets, clog_pct=0.0):
    """Sum of inlet intake (m^3/s) per cell, reduced by clog_pct."""
    r, c, inside = _inlet_cells(grid, inlets)
    q = inlets["capacity_m3s"].to_numpy(float) * (1 - clog_pct / 100.0)
    return _sum_to_cells(grid, r[inside], c[inside], q[inside])


def pipe_grid(grid, inlets, runoff_coeff=0.85, max_service_m=MAX_SERVICE_M):
    """Pipe allowance (m^3/s) per inlet cell: design rate x C x land area served by its inlets."""
    land = _land(grid)
    r, c, inside = _inlet_cells(grid, inlets)
    ok = inside & (inlets["capacity_m3s"].to_numpy(float) > 0)
    ok[ok] &= land[r[ok], c[ok]]
    out = np.zeros(grid.shape, float)
    if not ok.any() or not land.any():
        return out
    ir, ic = r[ok], c[ok]
    dx, dy = grid.cell_dx_m, grid.cell_dy_m
    tree = cKDTree(np.c_[(ic + 0.5) * dx, (ir + 0.5) * dy])
    lr, lc = np.nonzero(land)
    dist, idx = tree.query(np.c_[(lc + 0.5) * dx, (lr + 0.5) * dy],
                           distance_upper_bound=np.inf if max_service_m is None else max_service_m)
    served = np.isfinite(dist)
    area = np.bincount(idx[served], minlength=len(ir)) * grid.cell_area_m2
    rate_m_s = pipe_limit_mm_hr() / 1000.0 / 3600.0
    np.add.at(out, (ir, ic), rate_m_s * runoff_coeff * area)
    return out


def capacity_grid(grid, inlets, clog_pct=0.0, runoff_coeff=0.85, max_service_m=MAX_SERVICE_M):
    """Most water (m^3/s) each cell's drains can remove: min(inlet intake, pipe allowance).

    Simplified level-of-service model: no public pipe data, so the pipes behind each inlet are
    assumed to carry the 5-yr 3-hr design-storm rate over the land nearest that inlet.
    Cells without an inlet and off-land cells get 0.
    """
    cap = np.minimum(intake_grid(grid, inlets, clog_pct), pipe_grid(grid, inlets, runoff_coeff, max_service_m))
    cap[~_land(grid)] = 0.0
    return cap


def overwhelmed(inlets, grid, inflow_m3s):
    """Bool per inlet: its cell's peak inflow exceeded the cell's total inlet intake.

    Inflow in a cell is shared among its inlets in proportion to capacity, so every working
    inlet in a cell is overwhelmed together. Off-grid and zero-capacity inlets -> False.
    """
    r, c, inside = _inlet_cells(grid, inlets)
    intake = intake_grid(grid, inlets)
    flag = np.zeros(len(inlets), bool)
    ri, ci = r[inside], c[inside]
    flag[inside] = np.asarray(inflow_m3s)[ri, ci] > intake[ri, ci]
    flag &= inlets["capacity_m3s"].to_numpy(float) > 0
    return pd.Series(flag, index=inlets.index, name="overwhelmed")
