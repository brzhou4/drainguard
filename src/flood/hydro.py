"""Surface-water model: rain on SF terrain -> drains take what they can -> the rest flows downhill and pools.

How it works (kept simple enough to explain on a slide):

1. Terrain prep (done once per grid, cached):
   - Active cells = land cells with finite elevation above 0 m. Everything else (bay, ocean, outside SF,
     shoreline at or below sea level) and the grid edge are OUTLETS: water reaching them leaves the model
     ("to the bay").
   - `fill_depressions`: priority-flood (Barnes et al. 2014) fills every closed low spot up to its spill
     level. Cells where filled > z are DEPRESSIONS (pits that hold water: underpasses, valley floors).
   - `breach_deep_pits`: a pit whose full depth (spill level - lowest cell) exceeds max_pond_depth_m
     (default 2.0 m) is not a street-flooding basin in a bare-earth DEM: it is a reservoir or lake
     (Lake Merced, Mountain/Stow Lake, Sutro/University Mound/Summit reservoirs), a freeway cut or
     underpass with its own pump/drain, or a valley where a creek runs through a culvert under fill
     (Islais, Mission Creek). Its lowest cell becomes a culvert sink (an outlet; water reaching it counts
     as drained, `volume_culvert_m3`), then the DEM is refilled; repeated until no pit is deeper. So no
     reported pond is deeper than max_pond_depth_m. Pass None to keep every pit.
   - `flow_directions`: D8 steepest descent on the filled surface; flat areas (incl. filled pits) are
     resolved by a breadth-first search from their draining edge, so every land cell drains to an outlet.
     Cells touching an outlet drain straight into it.
   - Each connected depression is collapsed into one "pond" node with a volume-vs-level curve
     V(L) = cell_area * sum(max(0, L - z_i)) and a full volume at its spill level. A pond spills to the
     lowest-potential cell its water would leave through.
   - `flow_order`: the node graph (cells + ponds) is a tree draining to outlets; nodes are grouped in
     topological "levels" (longest path from the headwaters), so each level can be routed with numpy.

2. Time loop over hyetograph steps (dt = rain.step_min). Effective rain on each land cell =
   rain rate * runoff_coeff (SF is mostly paved; default 0.85). Within a step water is routed instantly
   upstream -> downstream (no travel time; SF catchments drain in well under a 15-min step):
   - ordinary cell: inflow = upstream outflow + local runoff; its drains take up to cap_m3s * dt;
     the rest passes to the D8 downstream cell.
   - pond: stored volume S += inflow; the drains inside it take up to (sum of their cap) * dt per step,
     also from water already standing there (so pools grow in the burst and shrink after); anything
     above the full volume spills downstream. Pond level comes from the volume-level curve.
   Steps are processed in blocks with time as a vector axis; a pond's storage follows the bounded
   recursion S_t = clip(S_{t-1} + inflow_t - drain_t, 0, V_full) (closed form when it never fills).

3. Moving water depth: the excess discharge Q (m^3/s) leaving an ordinary cell gives a Manning
   wide-channel normal depth h = (Q n / (W sqrt(S)))^(3/5), W = STREET_WIDTH_M = 12 m (a typical SF
   curb-to-curb street width: runoff runs down the street, not spread over the whole 30 m cell),
   S = max(local downhill slope, 0.005), n = 0.016 (asphalt).

4. depth_m = max over the event of pond depth (pond cells) or flowing depth (other cells); 0 = dry,
   NaN off-land. Ponds are treated as one water surface (nested sub-pits share a level) - a 30 m grid
   simplification.

Volumes: runoff = drained + ponded at the end + to_bay (exact; tested to < 1 %). Extra attributes set on
the FloodResult: peak_inflow_m3s (peak water arriving at each cell, upstream + local; for pond cells
the pond's peak inflow shared out by drain capacity), peak_overflow_m3s (the part beyond the cell's
drain capacity), overwhelmed_mask (cells with drains whose inflow exceeded capacity at some step),
pond_labels (depression id per cell, 0 = none), volume_culvert_m3, n_breached_pits,
volume_ponded_end_m3, timings.
"""
import hashlib
import heapq
import time
from collections import deque

import numpy as np
from scipy import ndimage

from src.flood import FloodResult

STREET_WIDTH_M = 12.0     # flow width for the moving-water depth (typical SF street, curb to curb)
MIN_SLOPE = 0.005         # floor on slope in Manning's formula (flat streets still have crown/gutter fall)
FLOOD_DEPTH_M = 0.15      # depth that counts as "flooded" in the timeline (~ top of a curb)
BLOCK_STEPS = 32          # time steps routed together (memory ~ nodes * BLOCK_STEPS * 8 bytes)
_OFFS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_CACHE = {}


def _active_mask(z, land=None):
    z = np.asarray(z, float)
    with np.errstate(invalid="ignore"):
        act = np.isfinite(z) & (z > 0)
    if land is not None:
        act &= np.asarray(land, bool)
    return act


def _pad(a, value):
    return np.pad(a, 1, mode="constant", constant_values=value)


def fill_depressions(z, land=None, sinks=None):
    """Priority-flood fill (Barnes et al. 2014). Returns filled elevations (NaN on non-active cells).

    Active = land & finite & z > 0; non-active cells and the grid edge are outlets. `sinks` (bool mask)
    marks extra active cells that act as outlets (culvert intakes at the bottom of breached pits)."""
    z = np.asarray(z, float)
    rows, cols = z.shape
    act = _pad(_active_mask(z, land), False)
    w = cols + 2
    seeds = act & ~ndimage.binary_erosion(act, structure=np.ones((3, 3), bool), border_value=0)
    if sinks is not None:
        seeds |= act & _pad(np.asarray(sinks, bool), False)
    zf = _pad(np.where(act[1:-1, 1:-1], z, 0.0), 0.0).ravel().tolist()
    closed = (~act).ravel().tolist()
    filled = [float("nan")] * len(zf)
    seed_idx = np.flatnonzero(seeds.ravel()).tolist()
    heap = []
    for p in seed_idx:
        closed[p] = True
        filled[p] = zf[p]
        heap.append((zf[p], p))
    heapq.heapify(heap)
    offs = [dr * w + dc for dr, dc in _OFFS]
    pit = deque()
    push, pop = heapq.heappush, heapq.heappop
    while heap or pit:
        c = pit.popleft() if pit else pop(heap)[1]
        fc = filled[c]
        for d in offs:
            n = c + d
            if closed[n]:
                continue
            closed[n] = True
            zn = zf[n]
            if zn <= fc:
                filled[n] = fc
                pit.append(n)
            else:
                filled[n] = zn
                push(heap, (zn, n))
    out = np.array(filled, float).reshape(rows + 2, cols + 2)[1:-1, 1:-1]
    out[~act[1:-1, 1:-1]] = np.nan
    return out


def _flow_directions(filled, dx, dy, sinks=None):
    """D8 on the filled surface + BFS flat resolution. Returns (rec, dist) as flat arrays:
    rec = downstream flat index, -1 = outlet, -2 = not active; dist = BFS steps across a flat."""
    filled = np.asarray(filled, float)
    rows, cols = filled.shape
    act = np.isfinite(filled)
    w = cols + 2
    fp = _pad(np.where(act, filled, np.inf), np.inf)
    seeds = act & ~ndimage.binary_erosion(act, structure=np.ones((3, 3), bool), border_value=0)
    if sinks is not None:
        seeds |= act & np.asarray(sinks, bool)
    best = np.zeros((rows, cols))
    bestk = np.full((rows, cols), -1, np.int64)
    f = np.where(act, filled, -np.inf)
    for k, (dr, dc) in enumerate(_OFFS):
        nb = fp[1 + dr:rows + 1 + dr, 1 + dc:cols + 1 + dc]
        drop = (f - nb) / np.hypot(dr * dy, dc * dx)
        better = drop > best
        best[better] = drop[better]
        bestk[better] = k
    rr, cc = np.indices((rows, cols))
    rec = np.full((rows, cols), -2, np.int64)
    has = act & (bestk >= 0)
    dr = np.array([o[0] for o in _OFFS])[bestk[has]]
    dc = np.array([o[1] for o in _OFFS])[bestk[has]]
    rec[has] = (rr[has] + dr) * cols + (cc[has] + dc)
    rec[seeds] = -1
    dist = np.zeros(rows * cols, np.int64)
    flat = act & (rec == -2)
    if flat.any():
        # BFS outward from draining cells into flat cells of exactly equal filled level (padded indices)
        flatp = _pad(flat, False).ravel()
        fpl = _pad(np.where(act, filled, np.nan), np.nan).ravel()
        src = act & ~flat & ndimage.binary_dilation(flat, structure=np.ones((3, 3), bool))
        sr, sc = np.nonzero(src)
        q = deque(((sr + 1) * w + (sc + 1)).tolist())
        unassigned = flatp.tolist()
        lev = fpl.tolist()
        dpad = [0] * len(lev)
        recp = {}
        offs = [a * w + b for a, b in _OFFS]
        while q:
            c = q.popleft()
            fc = lev[c]
            dc_ = dpad[c] + 1
            for d in offs:
                n = c + d
                if unassigned[n] and lev[n] == fc:
                    unassigned[n] = False
                    recp[n] = c
                    dpad[n] = dc_
                    q.append(n)
        if recp:
            pn = np.fromiter(recp.keys(), np.int64, len(recp))
            pc = np.fromiter(recp.values(), np.int64, len(recp))
            un = (pn // w - 1) * cols + (pn % w - 1)
            rec.ravel()[un] = (pc // w - 1) * cols + (pc % w - 1)
            dist[un] = np.asarray(dpad, np.int64)[pn]
        left = act & (rec == -2)
        rec[left] = -1   # should not happen on a properly filled surface; send to outlet to be safe
    return rec.ravel(), dist


def flow_directions(filled, z=None, land=None, dx=1.0, dy=1.0):
    """D8 receivers on the filled surface with flats resolved. Returns flat int array (rows*cols):
    downstream flat index, -1 = drains into an outlet (bay/edge), -2 = not an active land cell."""
    filled = np.array(filled, float)
    if z is not None or land is not None:
        filled[~_active_mask(z if z is not None else filled, land)] = np.nan
    return _flow_directions(filled, dx, dy)[0]


def flow_order(rec):
    """Topological order of a forest given downstream pointers (rec[i] >= 0 = node, -1 = outlet, -2 = skip).

    Returns (order, level): order = node ids upstream -> downstream; level = longest path from a
    headwater (-1 for skipped nodes). Nodes in the same level never feed each other."""
    rec = np.asarray(rec, np.int64)
    n = rec.size
    valid = rec >= -1
    down = np.where(valid, rec, -1)
    indeg = np.bincount(down[down >= 0], minlength=n)
    level = np.full(n, -1, np.int64)
    frontier = np.flatnonzero(valid & (indeg == 0))
    k = 0
    while frontier.size:
        level[frontier] = k
        t = down[frontier]
        t = t[t >= 0]
        if not t.size:
            break
        u, cnt = np.unique(t, return_counts=True)
        indeg[u] -= cnt
        frontier = u[indeg[u] == 0]
        k += 1
    if np.any(valid & (level < 0)):
        raise ValueError("flow graph has a cycle")
    idx = np.flatnonzero(valid)
    return idx[np.argsort(level[idx], kind="stable")], level


def _dep_table(z, labels, n):
    """Concatenated per-depression tables sorted by (id, z)."""
    zf = np.asarray(z, float).ravel()
    lab = labels.ravel()
    cells = np.flatnonzero(lab > 0)
    o = np.lexsort((zf[cells], lab[cells]))
    cells = cells[o]
    seg = lab[cells] - 1
    zs = zf[cells]
    counts = np.bincount(seg, minlength=n)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int64)
    cum = np.cumsum(zs)
    base = np.concatenate([[0.0], cum])[starts]          # sum of z before each segment
    cum_in = cum - base[seg]                              # inclusive prefix sum within segment
    rank = np.arange(cells.size) - starts[seg]
    vat = rank * zs - (cum_in - zs)                       # storage (m * cells) when level = z_i
    return dict(cells=cells, seg=seg, zs=zs, counts=counts, starts=starts, cum_in=cum_in, vat=vat)


def _level_from_storage(s, tab):
    """Water level per depression for storage s (m * cells, i.e. m^3 / cell area)."""
    if not s.size:
        return s
    ok = (tab["vat"] <= s[tab["seg"]] + 1e-12).astype(np.int64)
    k = np.add.reduceat(ok, tab["starts"])
    k = np.maximum(k, 1)
    return (s + tab["cum_in"][tab["starts"] + k - 1]) / k


def depression_ids(z, filled, cell_area_m2=1.0):
    """Connected pits (filled > z, 8-connected). Returns (labels, deps): labels int array (0 = none,
    1..n); deps[i] = {"id", "cells" (flat idx sorted by z), "spill_m", "levels_m", "volumes_m3",
    "volume_full_m3"} where volumes_m3[j] is the stored volume when the water level is levels_m[j]."""
    z = np.asarray(z, float)
    filled = np.asarray(filled, float)
    with np.errstate(invalid="ignore"):
        dep = np.isfinite(filled) & np.isfinite(z) & (filled > z)
    labels, n = ndimage.label(dep, structure=np.ones((3, 3), int))
    tab = _dep_table(z, labels, n)
    ff = filled.ravel()
    deps = []
    for i in range(n):
        s = slice(tab["starts"][i], tab["starts"][i] + tab["counts"][i])
        cells = tab["cells"][s]
        spill = float(ff[cells[0]])
        lv = np.append(tab["zs"][s], spill)
        vol = np.append(tab["vat"][s], np.sum(spill - tab["zs"][s])) * cell_area_m2
        deps.append({"id": i + 1, "cells": cells, "spill_m": spill, "levels_m": lv, "volumes_m3": vol,
                     "volume_full_m3": float(vol[-1])})
    return labels, deps


def breach_deep_pits(z, act, max_pond_depth_m=2.0, max_iter=50):
    """Fill, then turn every pit deeper than max_pond_depth_m into a culvert sink (its lowest cell becomes an
    outlet) and refill, until no remaining pit is deeper. Returns (filled, sinks mask, n_breached)."""
    sinks = np.zeros(z.shape, bool)
    filled = fill_depressions(z, act)
    if max_pond_depth_m is None or not np.isfinite(max_pond_depth_m):
        return filled, sinks, 0
    for _ in range(max_iter):
        with np.errstate(invalid="ignore"):
            dep = act & (filled > z)
        labels, nd = ndimage.label(dep, structure=np.ones((3, 3), int))
        if not nd:
            break
        idx = np.arange(1, nd + 1)
        deep = idx[np.asarray(ndimage.maximum(np.where(dep, filled - z, 0.0), labels, idx)) > max_pond_depth_m]
        if not deep.size:
            break
        for r, c in ndimage.minimum_position(np.where(dep, z, np.inf), labels, deep):
            sinks[r, c] = True
        filled = fill_depressions(z, act, sinks)
    return filled, sinks, int(sinks.sum())


def _prepare(z, act, dx, dy, max_pond_depth_m=2.0):
    """Cap-independent terrain topology (cached per grid)."""
    key = hashlib.blake2b(z.tobytes() + act.tobytes() + np.float64([dx, dy, -1.0 if max_pond_depth_m is None
                          else max_pond_depth_m]).tobytes(), digest_size=16).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    rows, cols = z.shape
    tm = {}
    t = time.perf_counter()
    filled, sinks, n_breached = breach_deep_pits(z, act, max_pond_depth_m)
    tm["fill_s"] = time.perf_counter() - t
    t = time.perf_counter()
    rec, dist = _flow_directions(filled, dx, dy, sinks)
    tm["d8_s"] = time.perf_counter() - t
    t = time.perf_counter()
    zf, ff = z.ravel(), filled.ravel()
    with np.errstate(invalid="ignore"):
        dep = act & (filled > z)
    labels, nd = ndimage.label(dep, structure=np.ones((3, 3), int))
    lab = labels.ravel()
    cells = np.flatnonzero(act.ravel())
    normal = cells[lab[cells] == 0]
    dcells = cells[lab[cells] > 0]
    nn = normal.size
    node_of = np.full(rows * cols, -1, np.int64)
    node_of[normal] = np.arange(nn)
    node_of[dcells] = nn + lab[dcells] - 1
    down = np.full(nn + nd, -1, np.int64)
    r = rec[normal]
    down[:nn] = np.where(r >= 0, node_of[np.maximum(r, 0)], -1)
    if nd:
        # pond exit: the outside cell with the lowest (filled, flat-distance) potential its cells drain to
        rank = np.full(rows * cols, -1, np.int64)
        rank[cells[np.lexsort((dist[cells], ff[cells]))]] = np.arange(cells.size)
        rd = rec[dcells]
        out_mask = (rd < 0) | (lab[np.maximum(rd, 0)] != lab[dcells])
        dd, tg = lab[dcells[out_mask]] - 1, rd[out_mask]
        tr = np.where(tg >= 0, rank[np.maximum(tg, 0)], -1)
        o = np.lexsort((tr, dd))
        dd, tg = dd[o], tg[o]
        first = np.r_[True, dd[1:] != dd[:-1]]
        down[nn + dd[first]] = np.where(tg[first] >= 0, node_of[np.maximum(tg[first], 0)], -1)
    order, level = flow_order(down)
    tm["graph_s"] = time.perf_counter() - t
    t = time.perf_counter()
    # per-level routing structures
    is_sink = sinks.ravel()[normal]
    lv_sorted = level[order]
    bounds = np.flatnonzero(np.r_[True, lv_sorted[1:] != lv_sorted[:-1], True])
    levels = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        nodes = np.sort(order[a:b])                       # ordinary nodes first, then ponds
        tgt = down[nodes]
        bay = tgt < 0
        culv = np.flatnonzero(bay & (nodes < nn) & is_sink[np.minimum(nodes, nn - 1)]) if nn else np.zeros(0, int)
        inn = np.flatnonzero(~bay)
        bay[culv] = False
        perm = inn[np.argsort(tgt[inn], kind="stable")]
        ts = tgt[perm]
        st = np.flatnonzero(np.r_[True, ts[1:] != ts[:-1]]) if ts.size else np.zeros(0, np.int64)
        levels.append(dict(nodes=nodes, mn=int(np.searchsorted(nodes, nn)), bay=np.flatnonzero(bay), culv=culv,
                           perm=perm, starts=st, utgt=ts[st] if ts.size else ts))
    # slope along the flow direction (ordinary cells), for Manning
    rn = rec[normal]
    slope = np.full(nn, MIN_SLOPE)
    okr = rn >= 0
    dr_ = rn[okr] // cols - normal[okr] // cols
    dc_ = rn[okr] % cols - normal[okr] % cols
    slope[okr] = np.maximum((zf[normal[okr]] - zf[rn[okr]]) / np.hypot(dr_ * dy, dc_ * dx), MIN_SLOPE)
    tab = _dep_table(z, labels, nd)
    spill = ff[tab["cells"][tab["starts"]]] if nd else np.zeros(0)
    vfull = np.bincount(tab["seg"], weights=spill[tab["seg"]] - tab["zs"], minlength=nd) if nd else np.zeros(0)
    # storage (m*cells) at which each pond cell is >= FLOOD_DEPTH_M deep
    vthr = np.full(tab["cells"].size, np.inf)
    if nd:
        lt = tab["zs"] + FLOOD_DEPTH_M
        okc = lt <= spill[tab["seg"]]
        big = float(np.nanmax(np.abs(tab["zs"]))) * 4 + 1000.0
        key_arr = tab["seg"] * big + tab["zs"]
        pos = np.searchsorted(key_arr, tab["seg"][okc] * big + lt[okc], side="left")
        kk = pos - tab["starts"][tab["seg"][okc]]
        vthr[okc] = kk * lt[okc] - tab["cum_in"][pos - 1]
    tm["levels_s"] = time.perf_counter() - t
    prep = dict(filled=filled, rec=rec, labels=labels, n_dep=nd, normal=normal, dcells=dcells, dlab=lab[dcells] - 1,
                n_normal=nn, down=down, levels=levels, slope=slope, tab=tab, vfull=vfull, vthr=vthr,
                n_levels=len(levels), timings=tm, sinks=sinks, n_breached=n_breached)
    if len(_CACHE) > 4:
        _CACHE.clear()
    _CACHE[key] = prep
    return prep


def simulate(grid, cap_m3s, rain, runoff_coeff=0.85, n_manning=0.016, progress=None, max_pond_depth_m=2.0):
    """Route a rain event over the grid. Returns a FloodResult (see module docstring for the model).

    max_pond_depth_m: pits whose full depth exceeds this are breached (treated as drained by a culvert /
    reservoir: no storage, water reaching their bottom leaves as "drained"). None = keep every pit."""
    t_start = time.perf_counter()
    report = progress if callable(progress) else (lambda f: None)
    z = np.asarray(grid.z, float)
    rows, cols = z.shape
    land = np.isfinite(z) if grid.land is None else (np.asarray(grid.land, bool) & np.isfinite(z))
    act = _active_mask(z, land)
    dx, dy, area = float(grid.cell_dx_m), float(grid.cell_dy_m), float(grid.cell_area_m2)
    cap = np.clip(np.nan_to_num(np.broadcast_to(np.asarray(cap_m3s, float), z.shape)), 0, None).ravel()
    rates = np.clip(np.nan_to_num(np.asarray(rain.intensity_mm_hr, float).ravel()), 0, None)
    nt = rates.size
    dt = float(rain.step_min) * 60.0
    rdt = rates / 1000.0 / 3600.0 * dt                    # m of rain per step
    report(0.02)
    prep = _prepare(z, act, dx, dy, max_pond_depth_m)
    report(0.3)
    nn, nd = prep["n_normal"], prep["n_dep"]
    normal, dcells, dlab, tab = prep["normal"], prep["dcells"], prep["dlab"], prep["tab"]
    nodes_total = nn + nd
    # node properties
    a_node = np.empty(nodes_total)
    a_node[:nn] = area * runoff_coeff
    a_node[nn:] = np.bincount(dlab, minlength=nd) * area * runoff_coeff
    capdt = np.empty(nodes_total)
    capdt[:nn] = cap[normal] * dt
    capdt[nn:] = np.bincount(dlab, weights=cap[dcells], minlength=nd) * dt
    has_cap = capdt > 0
    vfull = prep["vfull"] * area
    qthr_dt = np.full(nodes_total, np.inf)
    qthr_dt[:nn] = FLOOD_DEPTH_M ** (5 / 3) * STREET_WIDTH_M * np.sqrt(prep["slope"]) / n_manning * dt
    # outputs
    pk_in = np.zeros(nodes_total)
    over = np.zeros(nodes_total, bool)
    flow_cnt = np.zeros(nt)
    pond_cnt = np.zeros(nt)
    ponded_t = np.zeros(nt)
    s_state = np.zeros(nd)
    s_max = np.zeros(nd)
    drained = 0.0
    to_bay = 0.0
    culvert = 0.0
    vthr = prep["vthr"]
    fl = np.isfinite(vthr)
    vthr_cells, vthr_dep = vthr[fl] * area, tab["seg"][fl]
    for b0 in range(0, nt, BLOCK_STEPS):
        r_blk = rdt[b0:b0 + BLOCK_STEPS]
        tb = r_blk.size
        if not np.any(r_blk > 0) and not np.any(s_state > 0):
            continue
        inflow = np.outer(a_node, r_blk)
        s_hist = np.zeros((nd, tb))
        for lv in prep["levels"]:
            nodes, mn = lv["nodes"], lv["mn"]
            tot = inflow[nodes]
            tmax = tot.max(axis=1)
            pk_in[nodes] = np.maximum(pk_in[nodes], tmax)
            cd = capdt[nodes]
            over[nodes] |= tmax > cd * (1 + 1e-9) + 1e-12
            out = np.maximum(tot - cd[:, None], 0.0)
            if mn:
                o_n = out[:mn]
                drained += float(tot[:mn].sum() - o_n.sum())
                hot = tmax[:mn] - cd[:mn] >= qthr_dt[nodes[:mn]]
                if hot.any():
                    flow_cnt[b0:b0 + tb] += (o_n[hot] >= qthr_dt[nodes[:mn]][hot][:, None]).sum(axis=0)
            if mn < nodes.size:
                did = nodes[mn:] - nn
                inc = tot[mn:]
                s0 = s_state[did]
                vf = vfull[did]
                x = inc - cd[mn:, None]
                cs = np.cumsum(x, axis=1)
                s = cs - np.minimum(np.minimum.accumulate(cs, axis=1), -s0[:, None])
                spill = np.zeros_like(s)
                bad = np.flatnonzero(s.max(axis=1) > vf)
                if bad.size:                               # ponds that fill up: step through time
                    sb, ib, cb, vb = s0[bad], inc[bad], cd[mn:][bad], vf[bad]
                    sh, sp = np.empty((bad.size, tb)), np.empty((bad.size, tb))
                    for t in range(tb):
                        a = np.maximum(sb + ib[:, t] - cb, 0.0)
                        spl = np.maximum(a - vb, 0.0)
                        sb = a - spl
                        sh[:, t], sp[:, t] = sb, spl
                    s[bad], spill[bad] = sh, sp
                drained += float(s0.sum() + inc.sum() - s[:, -1].sum() - spill.sum())
                s_state[did] = s[:, -1]
                s_hist[did] = s
                out[mn:] = spill
            if lv["bay"].size:
                to_bay += float(out[lv["bay"]].sum())
            if lv["culv"].size:
                culvert += float(out[lv["culv"]].sum())
            if lv["perm"].size:
                inflow[lv["utgt"]] += np.add.reduceat(out[lv["perm"]], lv["starts"], axis=0)
        if nd:
            ponded_t[b0:b0 + tb] = s_hist.sum(axis=0)
            s_max = np.maximum(s_max, s_hist.max(axis=1))
            if vthr_cells.size:
                pond_cnt[b0:b0 + tb] = (s_hist[vthr_dep] >= vthr_cells[:, None]).sum(axis=0)
        report(0.3 + 0.68 * min(1.0, (b0 + tb) / max(nt, 1)))
    # depths
    depth = np.zeros(rows * cols)
    q_pk = np.maximum(pk_in[:nn] - capdt[:nn], 0.0) / dt
    depth[normal] = (q_pk * n_manning / (STREET_WIDTH_M * np.sqrt(prep["slope"]))) ** 0.6
    if nd:
        lmax = _level_from_storage(s_max / area, tab)
        depth[tab["cells"]] = np.maximum(lmax[tab["seg"]] - tab["zs"], 0.0)
    depth = depth.reshape(rows, cols)
    depth[~land] = np.nan
    # per-cell peak inflow / overflow
    pin = np.zeros(rows * cols)
    pin[normal] = pk_in[:nn] / dt
    if nd:
        cdep = capdt[nn:][dlab]
        ncell = np.bincount(dlab, minlength=nd)[dlab]
        share = np.where(cdep > 0, cap[dcells] * dt / np.where(cdep > 0, cdep, 1), 1.0 / ncell)
        pin[dcells] = pk_in[nn:][dlab] / dt * share
    pover = np.maximum(pin - cap, 0.0)
    over_mask = np.zeros(rows * cols, bool)
    over_mask[normal] = over[:nn] & has_cap[:nn]
    if nd:
        over_mask[dcells] = over[nn:][dlab] & (cap[dcells] > 0)
    # volumes
    rain_land = float(rdt.sum() * area * land.sum())
    runoff = rain_land * runoff_coeff
    to_bay += float(rdt.sum() * area * runoff_coeff * (land & ~act).sum())   # runoff on shoreline cells
    flooded_km2 = (flow_cnt + pond_cnt) * area / 1e6
    timeline = [{"t_hr": round((i + 1) * dt / 3600.0, 4), "flooded_km2": float(flooded_km2[i]),
                 "ponded_m3": float(ponded_t[i])} for i in range(nt)]
    res = FloodResult(depth_m=depth.astype(np.float32), grid=grid, rain=rain, volume_rain_m3=rain_land,
                      volume_runoff_m3=runoff, volume_drained_m3=drained + culvert,
                      volume_ponded_m3=float(ponded_t.max()) if nt else 0.0, volume_to_bay_m3=to_bay,
                      drains_overwhelmed=int(over_mask.sum()), timeline=timeline)
    res.peak_inflow_m3s = pin.reshape(rows, cols)
    res.peak_overflow_m3s = pover.reshape(rows, cols)
    res.overwhelmed_mask = over_mask.reshape(rows, cols)
    res.pond_labels = prep["labels"]
    res.volume_ponded_end_m3 = float(s_state.sum())
    res.volume_culvert_m3 = culvert
    res.n_breached_pits = prep["n_breached"]
    res.timings = dict(prep["timings"], total_s=time.perf_counter() - t_start, n_levels=prep["n_levels"],
                       n_ponds=nd)
    report(1.0)
    return res
