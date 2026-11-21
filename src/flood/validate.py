"""Does the flood map match reality? Score a simulated flood map against real 311 flood reports.

Metrics (per depth threshold):
- hit rate: share of incidents within D metres (50, 100) of a simulated flooded cell centre;
- cost: flooded cells as a share of SF land;
- lift vs random land: hit rate / share of random land points within D of the same map (Monte Carlo,
  uniform over land cells with in-cell jitter, fixed seed). Lift 1 = no better than chance;
- lift vs inlets: same, but the null points are every mapped storm-drain inlet. 311 reports come from
  streets, and inlets sit on streets, so this is the tougher, fairer baseline;
- by neighborhood: incidents vs simulated flooded acres, Spearman rank correlation.
"""
import datetime as dt
import json

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial import cKDTree

from src.flood import M_PER_DEG_LAT, M_PER_DEG_LON

THRESHOLDS_M = (0.05, 0.15, 0.30)
DISTANCES_M = (50, 100)
N_DRAWS = 200
SEED = 18
M2_PER_ACRE = 4046.856
EVENT_DATE = "2026-11-18"
SWEEP_RUNOFF = (0.6, 0.75, 0.85, 0.95)
SWEEP_CLOG = (0, 25, 50)


def _processed():
    from src.data import PROCESSED
    return PROCESSED


def out_path():
    return _processed() / "flood_validation.json"


def load_incidents(path=None):
    """Nov 18-19 311 flood / clogged-drain reports (lat, lon)."""
    df = pd.read_csv(path or (_processed() / "nov18_incidents.csv"))
    return df.dropna(subset=["lat", "lon"]).reset_index(drop=True)


# ---------------------------------------------------------------- geometry

def _xy(lat, lon):
    """Lat/lon -> local metres (equirectangular around LAT0; fine at city scale)."""
    return np.column_stack([np.asarray(lon, float) * M_PER_DEG_LON, np.asarray(lat, float) * M_PER_DEG_LAT])


def _inside(grid, lat, lon):
    r, c = grid.rc(lat, lon)
    rows, cols = grid.shape
    return (r >= 0) & (r < rows) & (c >= 0) & (c < cols)


def nearest_m(grid, mask, lat, lon):
    """Distance (m) from each point to the nearest centre of a True cell in mask; inf if mask is empty."""
    lat, lon = np.atleast_1d(lat), np.atleast_1d(lon)
    r, c = np.nonzero(mask)
    if not len(r):
        return np.full(lat.shape, np.inf)
    tree = cKDTree(_xy(*grid.latlon(r, c)))
    return tree.query(_xy(lat, lon), k=1)[0]


def hit_rate(grid, mask, lat, lon, dist_m):
    """Share of points within dist_m of a flooded cell."""
    if len(np.atleast_1d(lat)) == 0:
        return float("nan")
    return float(np.mean(nearest_m(grid, mask, lat, lon) <= dist_m))


def land_mask(grid):
    land = grid.land if grid.land is not None else np.isfinite(grid.z)
    return np.asarray(land, bool) & np.isfinite(grid.z)


def random_land_points(grid, n, rng):
    """n uniform random points on land (random land cell + uniform jitter inside it)."""
    r, c = np.nonzero(land_mask(grid))
    k = rng.integers(0, len(r), n)
    lat = grid.lat_top - (r[k] + rng.random(n)) * grid.dlat
    lon = grid.lon_left + (c[k] + rng.random(n)) * grid.dlon
    return lat, lon


def chance_hit(grid, mask, n, dists, draws=N_DRAWS, seed=SEED):
    """Monte Carlo null: hit rate of n random land points, per distance -> {d: (mean, p5, p95, all draws)}."""
    rng = np.random.default_rng(seed)
    lat, lon = random_land_points(grid, n * draws, rng)
    near = nearest_m(grid, mask, lat, lon).reshape(draws, n)
    out = {}
    for d in dists:
        rates = (near <= d).mean(1)
        out[d] = (float(rates.mean()), float(np.percentile(rates, 5)), float(np.percentile(rates, 95)), rates)
    return out


def score_mask(grid, mask, inc_lat, inc_lon, name, dists=DISTANCES_M, null_lat=None, null_lon=None,
               draws=N_DRAWS, seed=SEED):
    """One row per distance: hit rate, area share, lift vs random land (and vs null points, e.g. inlets)."""
    mask = np.asarray(mask, bool) & land_mask(grid)
    land_n = int(land_mask(grid).sum())
    share = float(mask.sum()) / land_n if land_n else float("nan")
    near = nearest_m(grid, mask, inc_lat, inc_lon)
    chance = chance_hit(grid, mask, len(inc_lat), dists, draws, seed)
    near_null = nearest_m(grid, mask, null_lat, null_lon) if null_lat is not None and len(null_lat) else None
    rows = []
    for d in dists:
        hr = float(np.mean(near <= d)) if len(near) else float("nan")
        mean, p5, p95, rates = chance[d]
        row = {"map": name, "dist_m": d, "incidents": int(len(near)), "hit_rate": round(hr, 4),
               "flooded_km2": round(float(mask.sum()) * grid.cell_area_m2 / 1e6, 3),
               "area_share": round(share, 4),
               "chance_hit_rate": round(mean, 4), "chance_p5": round(p5, 4), "chance_p95": round(p95, 4),
               "lift": round(hr / mean, 2) if mean > 0 else None,
               "p_value": round(float((np.sum(rates >= hr) + 1) / (len(rates) + 1)), 4)}
        if near_null is not None:
            inl = float(np.mean(near_null <= d))
            row["inlet_hit_rate"] = round(inl, 4)
            row["lift_vs_inlets"] = round(hr / inl, 2) if inl > 0 else None
        rows.append(row)
    return rows


# ---------------------------------------------------------------- neighborhoods

def neighborhood_table(grid, depth, inc_lat, inc_lon, threshold=0.15, names_fn=None):
    """Per neighborhood: incidents, simulated flooded acres, land acres. Spearman rho/p on incidents vs acres."""
    if names_fn is None:
        from src.flood.calc import neighborhood_of as names_fn
    a = grid.cell_area_m2 / M2_PER_ACRE
    land = land_mask(grid)
    wet = (np.nan_to_num(depth, nan=0.0) >= threshold) & land
    inc = pd.Series(names_fn(inc_lat, inc_lon)).value_counts()
    r, c = np.nonzero(land)
    land_names = np.asarray(names_fn(*grid.latlon(r, c), nearest=False), dtype=object)
    df = pd.DataFrame({"nhood": land_names, "wet": wet[r, c]})
    df = df[df["nhood"] != ""]
    g = df.groupby("nhood")["wet"].agg(["sum", "size"])
    tab = pd.DataFrame({"incidents": inc.reindex(g.index).fillna(0).astype(int),
                        "flooded_acres": (g["sum"] * a).round(1), "land_acres": (g["size"] * a).round(0)})
    extra = inc.index.difference(tab.index).difference([""])
    for nm in extra:   # incidents in a neighborhood with no land cells on the grid
        tab.loc[nm] = [int(inc[nm]), 0.0, 0.0]
    tab = tab.sort_values("incidents", ascending=False)
    out = {"threshold_m": threshold, "n_neighborhoods": int(len(tab))}
    if len(tab) >= 3:
        rho, p = stats.spearmanr(tab["incidents"], tab["flooded_acres"])
        out.update(rho=round(float(rho), 3), p=round(float(p), 4))
        dens = tab[tab["land_acres"] > 0]
        rho2, p2 = stats.spearmanr(dens["incidents"] / dens["land_acres"], dens["flooded_acres"] / dens["land_acres"])
        out.update(rho_per_acre=round(float(rho2), 3), p_per_acre=round(float(p2), 4))
    return tab.reset_index(names="neighborhood"), out


# ---------------------------------------------------------------- flood zone

def flood_zone_mask(grid, path=None):
    """Grid mask of the SFPUC 100-year flood zone GeoJSON, or None if the file isn't there."""
    path = path or (_processed() / "flood_zone.geojson")
    if not path.exists():
        return None
    from src.flood.calc import _in_ring
    gj = json.loads(path.read_text())
    r, c = np.nonzero(land_mask(grid))
    lat, lon = grid.latlon(r, c)
    hit = np.zeros(len(r), bool)
    for f in gj.get("features", []):
        g = f.get("geometry") or {}
        polys = g.get("coordinates", [])
        if g.get("type") == "Polygon":
            polys = [polys]
        elif g.get("type") != "MultiPolygon":
            continue
        for p in polys:
            if not p:
                continue
            ring = np.asarray(p[0], float)
            box = (lon >= ring[:, 0].min()) & (lon <= ring[:, 0].max()) & (lat >= ring[:, 1].min()) & (lat <= ring[:, 1].max())
            idx = np.flatnonzero(box & ~hit)
            if len(idx):
                inside = _in_ring(lon[idx], lat[idx], ring)
                for hole in p[1:]:
                    inside &= ~_in_ring(lon[idx], lat[idx], np.asarray(hole, float))
                hit[idx[inside]] = True
    mask = np.zeros(grid.shape, bool)
    mask[r[hit], c[hit]] = True
    return mask


# ---------------------------------------------------------------- run

def _scenario(rain=None, **kw):
    from src.flood import calc, weather_input
    rain = rain if rain is not None else weather_input.from_date(EVENT_DATE)
    return calc.run(rain, **kw)


def evaluate(scenario, incidents, inlets=None, thresholds=THRESHOLDS_M, dists=DISTANCES_M, zone=True):
    """All validation rows + neighborhood table for one Scenario (or FloodResult)."""
    res = getattr(scenario, "result", scenario)
    grid, depth = res.grid, np.nan_to_num(res.depth_m, nan=0.0)
    ok = _inside(grid, incidents["lat"].values, incidents["lon"].values)
    lat, lon = incidents["lat"].values[ok], incidents["lon"].values[ok]
    nl = nlo = None
    if inlets is not None and len(inlets):
        nl, nlo = inlets["lat"].values, inlets["lon"].values
    rows = []
    for t in thresholds:
        rows += [dict(r, threshold_m=t) for r in
                 score_mask(grid, depth >= t, lat, lon, f"simulated >= {t:g} m", dists, nl, nlo)]
    notes = [f"{int(ok.sum())} of {len(incidents)} incidents fall inside the model grid."]
    zmask = flood_zone_mask(grid) if zone else None
    if zmask is not None:
        rows += [dict(r, threshold_m=None) for r in score_mask(grid, zmask, lat, lon, "SFPUC 100-yr zone", dists, nl, nlo)]
    elif zone:
        notes.append("SFPUC 100-year flood zone file not found; comparison skipped.")
    return rows, lat, lon, notes


def sweep(incidents, inlets, runoff=SWEEP_RUNOFF, clog=SWEEP_CLOG, threshold=0.15, dist=100, rain=None):
    """Sensitivity of the 100 m / 0.15 m metric to runoff_coeff and clog_pct. Sorted by lift (best first)."""
    from src.flood import weather_input
    rain = rain if rain is not None else weather_input.from_date(EVENT_DATE)
    out = []
    for rc in runoff:
        for cp in clog:
            s = _scenario(rain, runoff_coeff=rc, clog_pct=cp)
            rows, *_ = evaluate(s, incidents, inlets, thresholds=(threshold,), dists=(dist,), zone=False)
            r = rows[0]
            out.append({"runoff_coeff": rc, "clog_pct": cp, "hit_rate": r["hit_rate"], "area_share": r["area_share"],
                        "lift": r["lift"], "lift_vs_inlets": r.get("lift_vs_inlets")})
    return sorted(out, key=lambda r: -(r["lift"] or 0))


def sentence(rows):
    """One honest plain-English verdict from the 100 m / 0.15 m row."""
    r = next((x for x in rows if x.get("threshold_m") == 0.15 and x["dist_m"] == 100), None)
    if r is None or r.get("lift") is None:
        return "Not enough data to compare the simulated map with real reports."
    hr, ch, lift = 100 * r["hit_rate"], 100 * r["chance_hit_rate"], r["lift"]
    li = r.get("lift_vs_inlets")
    base = (f"{hr:.0f}% of real Nov 18 flood reports were within 100 m of a spot the model floods "
            f"(6+ in of water), which covers {100 * r['area_share']:.1f}% of SF land; ")
    if lift < 1.15:
        verdict = f"random spots on land do about as well ({ch:.0f}%), so the map doesn't beat chance."
    elif lift < 1.5:
        verdict = f"random spots on land score {ch:.0f}%, so the map is only modestly better than chance ({lift:.1f}x)."
    else:
        verdict = f"random spots on land score {ch:.0f}%, so the map is {lift:.1f}x better than chance."
    if li is not None:
        if li < 1.15:
            verdict += " Against random street drains (where reports actually come from) it shows no real skill."
        else:
            verdict += f" Against random street drains it is {li:.1f}x better."
    return base + verdict


def run(do_sweep=False, rain=None, write=True):
    """Simulate Nov 18, 2026, score it, and write data/processed/flood_validation.json."""
    from src.flood import calc
    s = _scenario(rain)
    inc = load_incidents()
    inlets = calc.cached_inlets()
    rows, lat, lon, notes = evaluate(s, inc, inlets)
    tab, sp = neighborhood_table(s.result.grid, s.result.depth_m, lat, lon)
    notes.append("Distances are to the nearest flooded 30 m cell centre. Chance = share of random land points "
                 f"({N_DRAWS} Monte Carlo draws, seed {SEED}) within the same distance of the same map.")
    notes.append("311 reports include clogged drains and come from streets, not parks or hills, so the "
                 "inlet baseline (random storm-drain inlets) is the fairer comparison.")
    out = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "event": s.result.rain.label,
           "rain_in": round(float(s.result.rain.depth_in), 2), "params": s.params, "rows": rows,
           "spearman": sp, "neighborhoods": tab.to_dict("records"), "sentence": sentence(rows), "notes": notes}
    if do_sweep:
        out["sweep"] = sweep(inc, inlets, rain=s.result.rain)
    if write:
        out_path().write_text(json.dumps(out, indent=1, default=str))
    return out


def load():
    p = out_path()
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------- UI

def validation_panel():
    """Streamlit section: 'Does the flood map match reality?' (reads the cached JSON; button recomputes)."""
    import streamlit as st
    st.subheader("Does the flood map match reality?")
    data = load()
    if st.button("Recompute validation", key="flood_validate_btn"):
        with st.spinner("Simulating Nov 18 and comparing with 311 reports..."):
            data = run(do_sweep=False)
    if not data:
        st.info("No validation results yet. Press the button to compute them.")
        return
    st.write(data.get("sentence") or sentence(data["rows"]))
    df = pd.DataFrame(data["rows"])
    df = df[df["dist_m"] == 100]
    show = pd.DataFrame({
        "Map": df["map"],
        "Reports within 100 m": (100 * df["hit_rate"]).round(0).astype(int).astype(str) + "%",
        "Share of SF flagged": (100 * df["area_share"]).round(1).astype(str) + "%",
        "Random spot within 100 m": (100 * df["chance_hit_rate"]).round(0).astype(int).astype(str) + "%",
        "Lift vs chance": df["lift"].map(lambda v: f"{v:.2f}x" if v is not None else "n/a"),
    })
    if "lift_vs_inlets" in df:
        show["Lift vs random drains"] = df["lift_vs_inlets"].map(lambda v: f"{v:.2f}x" if pd.notna(v) else "n/a")
    st.dataframe(show, hide_index=True, width="stretch")
    sp = data.get("spearman") or {}
    if "rho" in sp:
        st.caption(f"By neighborhood: reports vs simulated flooded acres, Spearman rho = {sp['rho']:.2f} "
                   f"(p = {sp['p']:.3f}, {sp['n_neighborhoods']} neighborhoods). "
                   f"{data.get('event', '')}, {data.get('rain_in', '')} in. Computed {data.get('generated', '')}.")
    with st.expander("Details"):
        st.dataframe(pd.DataFrame(data["rows"]), hide_index=True, width="stretch")
        if data.get("neighborhoods"):
            st.dataframe(pd.DataFrame(data["neighborhoods"]), hide_index=True, width="stretch")
        for n in data.get("notes", []):
            st.caption(n)


if __name__ == "__main__":
    import sys
    res = run(do_sweep="--sweep" in sys.argv)
    for r in res["rows"]:
        print(r)
    print(res["spearman"])
    print(res["sentence"])
    for r in res.get("sweep", []):
        print(r)
