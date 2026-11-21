"""Small geo helpers for the app (no geopandas needed)."""
import math

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
