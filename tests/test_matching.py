"""Offline checks for Ask DrainGuard's text matching (no network).

Run from the repo root:  python tests/test_matching.py   (also works with pytest)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import matching as mt  # noqa: E402

# Static app places plus a few DataSF Analysis Neighborhoods (coordinates don't matter here).
PLACES = {n: (37.76, -122.44) for n in [
    "Mission", "Bayview Hunters Point", "Sunset/Parkside", "Inner Sunset", "South of Market", "Haight Ashbury",
    "Excelsior", "Bernal Heights", "San Francisco", "Downtown", "Richmond", "Sunset", "Bayview", "SoMa",
    "Mission Bay", "Castro/Upper Market", "Noe Valley", "Marina", "Tenderloin", "Potrero Hill", "Outer Mission"]}
STATIC_ONLY = {n: v for n, v in PLACES.items() if n not in ("Mission Bay", "Castro/Upper Market", "Noe Valley",
                                                            "Marina", "Tenderloin", "Potrero Hill", "Outer Mission")}


def place(text, places=PLACES):
    low, _ = mt.normalize(text)
    return mt.find_place(low, places)[0]


def test_weather_intent():
    yes = ["will it rain tonight", "is it gonna pour in soma tmrw", "any drizzle near the castro?",
           "whats the wether like", "how much rian in the mission", "do i need an umbrella in noe",
           "storm tomorrow?", "forcast for bernal", "Rainfall near 37.7625, -122.4155"]
    no = ["How do I mark a drain checked?", "which drains in the Mission need checking", "N-315979",
          "What does Evidence measure?", "how do i send a storm alert"]
    for q in yes:
        assert mt.wants_weather(mt.normalize(q)[0]), q
    for q in no:
        assert not mt.wants_weather(mt.normalize(q)[0]), q


def test_unchecked_intent():
    yes = ["Which unchecked drains are in the Mission?", "what drains still need checking in soma",
           "any drains not checked yet near bernal", "drians in the tenderloin that need inspection",
           "uncheked drains mission", "which drains havent been checked in the castro"]
    no = ["How do I mark a drain checked?", "rain in the mission", "what does the forecast score mean"]
    for q in yes:
        assert mt.wants_unchecked(mt.normalize(q)[0]), q
    for q in no:
        low = mt.normalize(q)[0]
        assert not (mt.wants_unchecked(low) and not mt.wants_weather(low)), q


def test_places():
    assert place("rain in the Mission") == "Mission"
    assert place("rain in mission bay") == "Mission Bay"
    assert place("rain in Mission Bay", STATIC_ONLY) is None          # never the Mission
    assert place("rain near dolores park") == "Mission"
    assert place("drizzle in the castro") == "Castro/Upper Market"
    assert place("rain in misson") == "Mission"                       # typo
    assert place("weather in tenderlion") == "Tenderloin"             # typo
    assert place("rain in soma tomorrow") == "SoMa"
    assert place("rain in the sunset") == "Sunset"
    assert place("rain in outer mission") == "Outer Mission"
    assert place("rain in north mission") is None
    assert place("rain in Daly City") is None
    assert place("rain in marin") is None                              # not Marina
    assert place("rain in oakland") is None
    assert place("rain near potrero") == "Potrero Hill"


def test_drain_ids():
    assert mt.drain_id_candidates("N-315979") == ["N-315979"]
    assert mt.drain_id_candidates("status of n315979?") == ["N-315979"]
    assert mt.drain_id_candidates("drain #11029") == ["N-11029"]
    assert mt.drain_id_candidates("N 11029 please") == ["N-11029"]
    assert mt.drain_id_candidates("rain near 37.7625, -122.4155") == []
    assert mt.drain_id_candidates("which drains in the mission") == []


def test_coords():
    assert mt.find_coords("37.7625, -122.4155")[:2] == (37.7625, -122.4155)
    assert mt.find_coords("-122.4155, 37.7625")[:2] == (37.7625, -122.4155)
    assert mt.find_coords("37.7625 N, 122.4155 W")[:2] == (37.7625, -122.4155)
    lat, lon, note = mt.find_coords("37.7625, 122.4155")
    assert (lat, lon) == (37.7625, -122.4155) and note
    assert mt.find_coords("40.7, -74.0")[:2] == (40.7, -74.0)
    assert mt.find_coords("no numbers here") is None


def test_typos():
    low, fixes = mt.normalize("Uncheked drians in the misson")
    assert "unchecked" in low and "drains" in low
    assert ("uncheked", "unchecked") in fixes
    low, _ = mt.normalize("how do I check a drain")
    assert "drain" in low and "rain " not in low.replace("drain", "")    # 'drain' is never turned into 'rain'


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} test groups passed")
