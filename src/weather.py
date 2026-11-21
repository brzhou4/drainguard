"""Real San Francisco forecast from the National Weather Service (api.weather.gov, free, no key)."""
import requests

SF_POINT = "37.7749,-122.4194"
HEADERS = {"User-Agent": "DrainGuard hackathon app (github.com/brzhou4/drainguard)", "Accept": "application/geo+json"}
RAIN_WORDS = ("rain", "showers", "storm", "drizzle")


def get_forecast():
    """Returns a list of forecast periods (name, short, detail, pop) for the next ~7 days, or [] on failure."""
    try:
        pt = requests.get(f"https://api.weather.gov/points/{SF_POINT}", headers=HEADERS, timeout=15).json()
        fc = requests.get(pt["properties"]["forecast"], headers=HEADERS, timeout=15).json()
        out = []
        for p in fc["properties"]["periods"]:
            pop = (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
            out.append({"name": p["name"], "short": p["shortForecast"], "detail": p["detailedForecast"],
                        "pop": int(pop), "start": p["startTime"]})
        return out
    except Exception:
        return []


def storm_status(periods, hours=72, pop_threshold=50):
    """Pick the first rainy period in the next `hours` (each NWS period is about 12 h)."""
    soon = periods[: max(1, hours // 12)]
    rainy = [p for p in soon if p["pop"] >= pop_threshold or
             (p["pop"] >= 30 and any(w in p["short"].lower() for w in RAIN_WORDS))]
    if not periods:
        return {"level": "unknown", "headline": "Forecast unavailable",
                "detail": "Could not reach the National Weather Service. Check weather.gov/mtr."}
    if rainy:
        top = max(rainy, key=lambda p: p["pop"])
        level = "storm" if top["pop"] >= 70 else "watch"
        return {"level": level,
                "headline": f"Rain expected {rainy[0]['name']}: {top['pop']}% chance",
                "detail": f"{top['name']}: {top['detail']}"}
    return {"level": "clear", "headline": "No storm in the next 3 days",
            "detail": f"{periods[0]['name']}: {periods[0]['short']}. Good time to check drains."}


def current_storm():
    """Storm status for the app, cached 30 min. Setting STORM_OVERRIDE=storm|watch|clear forces a level (demos)."""
    import streamlit as st

    from src.data import setting

    @st.cache_data(ttl=1800, show_spinner=False)
    def _forecast():
        return get_forecast()

    s = storm_status(_forecast())
    force = setting("STORM_OVERRIDE", "").strip().lower()
    if force in ("storm", "watch", "clear"):
        s = {**s, "level": force, "headline": f"{s['headline']} (demo override: {force})"}
    return s
