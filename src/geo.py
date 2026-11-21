"""Small geo helpers for the app (no geopandas needed)."""
import math
import re

import pandas as pd


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in meters between two lat/lon points."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_drains(drains: pd.DataFrame, lat, lon, radius_m=400, limit=5):
    """Drains within radius_m of (lat, lon), nearest first, with distance and walking minutes."""
    d = drains.copy()
    d["distance_m"] = [haversine_m(lat, lon, a, b) for a, b in zip(d["lat"], d["lon"])]
    d = d[d["distance_m"] <= radius_m].sort_values("distance_m").head(limit)
    d["walk_min"] = (d["distance_m"] / 80).round().astype(int)
    return d


# Rough San Francisco box (lat_min, lat_max, lon_min, lon_max), used to reject spots outside the city.
SF_BOUNDS = (37.70, 37.835, -122.52, -122.35)


def in_sf(lat, lon):
    return SF_BOUNDS[0] <= lat <= SF_BOUNDS[1] and SF_BOUNDS[2] <= lon <= SF_BOUNDS[3]


_SUFFIXES = {"st": "ST", "street": "ST", "ave": "AVE", "av": "AVE", "avenue": "AVE", "blvd": "BLVD",
             "boulevard": "BLVD", "dr": "DR", "drive": "DR", "rd": "RD", "road": "RD", "ln": "LN", "lane": "LN",
             "pl": "PL", "place": "PL", "ct": "CT", "court": "CT", "ter": "TER", "terrace": "TER", "way": "WAY",
             "hwy": "HWY", "highway": "HWY", "aly": "ALY", "alley": "ALY", "cir": "CIR", "circle": "CIR"}
_UA = {"User-Agent": "DrainGuard/1.0 (python-requests)"}
# DataSF "Streets - Active and Retired" centerlines: one row per block, with the street names at each end.
_CENTERLINES = "https://data.sfgov.org/resource/3psu-pn9h.json"


def _street_name(s):
    """'24th st' -> ('24TH', 'ST'); '1st' -> ('01ST', None); 'Castro' -> ('CASTRO', None) (DataSF naming)."""
    words = re.sub(r"[^\w\s']", " ", s).upper().split()
    suffix = None
    if len(words) > 1 and words[-1].lower() in _SUFFIXES:
        suffix = _SUFFIXES[words.pop().lower()]
    words = [("0" + w if re.fullmatch(r"\d(ST|ND|RD|TH)", w) else w) for w in words]
    return " ".join(words), suffix


def _matches(name, base, suffix):
    """Does a DataSF cross-street name like 'MISSION ST' match the typed (base, suffix)?"""
    if suffix:
        return name == f"{base} {suffix}"
    return name == base or (name.startswith(base + " ") and len(name.split()) == len(base.split()) + 1)


def _pretty(name):
    """'24TH ST' -> '24th St', '01ST ST' -> '1st St', 'SAN JOSE AVE' -> 'San Jose Ave'."""
    out = []
    for w in name.split():
        if re.fullmatch(r"\d+(ST|ND|RD|TH)", w):
            out.append(w.lstrip("0").lower() or w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _intersection(a, b):
    """Point where two named streets meet in SF, from DataSF street centerlines (free, no key)."""
    import requests
    (ab, asuf), (bb, bsuf) = _street_name(a), _street_name(b)
    if not ab or not bb:
        return None
    for (b1, s1), (b2, s2) in (((ab, asuf), (bb, bsuf)), ((bb, bsuf), (ab, asuf))):
        where = f"upper(street)='{b1.replace(chr(39), chr(39) * 2)}' AND active=true"
        if s1:
            where += f" AND st_type='{s1}'"
        try:
            r = requests.get(_CENTERLINES, params={"$where": where, "$select": "streetname,f_st,t_st,f_node_cnn,"
                                                   "t_node_cnn,line", "$limit": 2000}, headers=_UA, timeout=10)
            rows = r.json()
        except Exception:
            return None
        if not isinstance(rows, list) or not rows:
            continue
        pts, other = {}, set()  # node id -> (lat, lon); matched cross-street names
        for row in rows:
            coords = (row.get("line") or {}).get("coordinates") or []
            if not coords:
                continue
            for end, node, pt in (("f_st", "f_node_cnn", coords[0]), ("t_st", "t_node_cnn", coords[-1])):
                hit = [n for n in (x.strip() for x in (row.get(end) or "").split("\\")) if _matches(n, b2, s2)]
                if hit:
                    pts[row.get(node)] = (pt[1], pt[0])
                    other.update(hit)
        if not pts:
            return None
        la = sum(p[0] for p in pts.values()) / len(pts)  # a big intersection can have a few nodes
        lo = sum(p[1] for p in pts.values()) / len(pts)
        if not in_sf(la, lo) or max(haversine_m(la, lo, p[0], p[1]) for p in pts.values()) > 150:
            return None  # streets cross in more than one place
        streets = {row["streetname"] for row in rows if row.get("streetname")}
        n1 = next(iter(streets)) if len(streets) == 1 else (f"{b1} {s1}" if s1 else b1)
        n2 = next(iter(other)) if len(other) == 1 else (f"{b2} {s2}" if s2 else b2)
        return la, lo, f"{_pretty(n1)} & {_pretty(n2)}"
    return None


def geocode_sf(text):
    """(lat, lon, label) for an address or cross streets in San Francisco.
    Cross streets come from DataSF centerlines; other text goes to OpenStreetMap Nominatim.
    Returns None if nothing is found. Only the coordinates are kept by the app, never the typed text."""
    import requests
    q = text.strip()
    if not q:
        return None
    parts = re.split(r"\s*(?:&|/|\band\b|\bat\b|\s@\s)\s*", q, flags=re.I)
    if len(parts) == 2 and all(p.strip() for p in parts):
        hit = _intersection(*parts)
        if hit:
            return hit
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{q}, San Francisco, CA", "format": "jsonv2", "limit": 1, "countrycodes": "us",
                    "addressdetails": 1,
                    "viewbox": f"{SF_BOUNDS[2]},{SF_BOUNDS[1]},{SF_BOUNDS[3]},{SF_BOUNDS[0]}", "bounded": 1},
            headers=_UA, timeout=8)
        hits = r.json()
    except Exception:
        return None
    if not hits:
        return None
    h = hits[0]
    la, lo = float(h["lat"]), float(h["lon"])
    if not in_sf(la, lo):
        return None
    ad = h.get("address", {})
    road = ad.get("road") or ad.get("pedestrian") or ""
    label = f"{ad.get('house_number', '')} {road}".strip() or h.get("name") or q
    return la, lo, label
