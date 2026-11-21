"""Text matching for Ask DrainGuard: synonyms, typo fixes, place names, drain IDs and coordinates.

Pure functions only (no network, no Streamlit) so they are easy to test:
    python tests/test_matching.py
"""
import difflib
import re

# ---------- Typos ----------
# Common misspellings, fixed before any matching. Short words go here explicitly because fuzzy matching
# on short words is risky ("drain" is one letter away from "rain").
TYPOS = {
    "rian": "rain", "rainn": "rain", "raim": "rain", "rainning": "raining", "rainign": "raining",
    "wether": "weather", "wheather": "weather", "weahter": "weather", "waether": "weather",
    "forcast": "forecast", "forecase": "forecast", "forcaste": "forecast", "forcasted": "forecast",
    "drian": "drain", "drians": "drains", "drane": "drain", "dranes": "drains", "drainz": "drains",
    "uncheked": "unchecked", "unchekced": "unchecked", "unchecekd": "unchecked", "uncheck": "unchecked",
    "chekced": "checked", "cheked": "checked", "checkd": "checked",
    "neighbourhood": "neighborhood", "nieghborhood": "neighborhood", "neighbhorhood": "neighborhood",
    "tmrw": "tomorrow", "tmr": "tomorrow", "tomorow": "tomorrow", "tonite": "tonight",
    "gonna": "going to", "wanna": "want to", "pls": "please", "plz": "please",
}
# Longer keywords that may be fuzzily corrected (only tokens of 6+ letters, only if not already a keyword).
FUZZY_VOCAB = ["weather", "forecast", "raining", "rainfall", "unchecked", "checked", "neighborhood",
               "drizzle", "drizzling", "pouring", "downpour", "sprinkling", "precipitation", "inspected",
               "inspection", "volunteer", "evidence", "command", "center", "ranking"]

# ---------- Intents ----------
WEATHER_RE = re.compile(
    r"\b(rain|rains|raining|rained|rainfall|rainy|weather|wet|precip\w*|drizzl\w*|showers?|pour|pours|pouring|"
    r"downpours?|sprinkl\w*|umbrella|raincoat|stormy|soggy)\b"
    r"|\bforecast (for|near|at|in|around)\b"
    r"|\bstorms? (near|in|at|around|tonight|tomorrow|today|coming|this)\b"
    r"|\bhow much water\b")
UNCHECKED_RE = re.compile(
    r"\b(unchecked|uninspected|unvisited)\b"
    r"|\bnot (yet )?(been )?(checked|inspected|visited)\b"
    r"|\bneeds? (checking|a check|inspect\w*|to be (checked|inspected)|attention)\b"
    r"|\bstill need\b|\bhaven'?t been (checked|inspected)\b|\bnobody (has )?checked\b"
    r"|\bto (check|inspect)\b|\b(which|what|any|open|priority|urgent) drains\b"
    r"|\bdrains? (in|near|around|at|for)\b")
DRAIN_WORD_RE = re.compile(r"\b(drains?|inlets?|catch ?basins?)\b")

# ---------- Places ----------
# Extra names (lowercase) -> a canonical place. Canonical places are either keys of the app's static
# place list or DataSF Analysis Neighborhood names (looked up from the neighborhoods map layer).
LANDMARKS = {
    "dolores park": "Mission", "mission dolores": "Mission", "valencia street": "Mission", "24th street": "Mission",
    "castro": "Castro/Upper Market", "the castro": "Castro/Upper Market", "upper market": "Castro/Upper Market",
    "noe": "Noe Valley", "potrero": "Potrero Hill", "dogpatch": "Potrero Hill",
    "fidi": "Financial District/South Beach", "financial district": "Financial District/South Beach",
    "south beach": "Financial District/South Beach", "embarcadero": "Financial District/South Beach",
    "union square": "Downtown", "civic center": "Tenderloin", "the tenderloin": "Tenderloin",
    "haight": "Haight Ashbury", "the haight": "Haight Ashbury", "haight ashbury": "Haight Ashbury",
    "haight-ashbury": "Haight Ashbury", "cole valley": "Haight Ashbury",
    "usf": "Lone Mountain/USF", "lone mountain": "Lone Mountain/USF",
    "lake merced": "Lakeshore", "sfsu": "Lakeshore", "stonestown": "Lakeshore",
    "parkside": "Sunset/Parkside", "outer sunset": "Sunset/Parkside", "the sunset": "Sunset",
    "ingleside": "Oceanview/Merced/Ingleside", "oceanview": "Oceanview/Merced/Ingleside",
    "merced heights": "Oceanview/Merced/Ingleside",
    "hunters point": "Bayview Hunters Point", "bayview-hunters point": "Bayview Hunters Point",
    "west portal": "West of Twin Peaks", "forest hill": "West of Twin Peaks", "st francis wood": "West of Twin Peaks",
    "cow hollow": "Marina", "the marina": "Marina", "pac heights": "Pacific Heights",
    "sea cliff": "Seacliff", "ggp": "Golden Gate Park", "fillmore": "Western Addition",
    "vis valley": "Visitacion Valley", "bernal": "Bernal Heights", "the richmond": "Richmond",
    "richmond district": "Richmond", "sunset district": "Sunset",
    "sf": "San Francisco", "the city": "San Francisco",
    "soma": "SoMa", "the mission": "Mission", "mission district": "Mission",
}
# Places outside San Francisco (or not in the app) that look like SF names: never substitute these.
NOT_SF = ("daly city", "marin", "oakland", "berkeley", "sausalito", "pacifica", "brisbane", "alameda",
          "south san francisco", "south city", "colma", "emeryville", "san bruno", "millbrae")
# Words before a place name that make it a different place ("North Mission", "Upper Haight" is fine via aliases).
PRECEDE_BAD = {"north", "south", "east", "west", "upper", "lower", "outer", "inner", "new", "daly", "mount",
               "mt", "little", "old"}
# A place followed by one of these words is still that place; followed by anything else ("Mission Bay"
# when only "Mission" is known, "Richmond Heights") it is treated as an unknown location.
FOLLOW_OK = {"", "today", "tomorrow", "tonight", "this", "next", "now", "right", "please", "district", "area",
             "neighborhood", "and", "or", "over", "during", "for", "in", "at", "on", "the", "weekend", "later",
             "soon", "morning", "afternoon", "evening", "overnight", "drains", "drain", "s", "going", "will",
             "is", "be", "get", "gets", "rain", "raining", "weather", "forecast", "storm", "with", "any",
             "too", "pls", "thanks", "thank", "by", "around", "near", "today's", "tonight's", "tomorrow's"}

# Which ranked-drain neighborhoods each broad place covers (for "unchecked drains in ...").
RANKED_HOODS = {
    "Sunset": ["Sunset/Parkside", "Inner Sunset"], "Richmond": ["Inner Richmond", "Outer Richmond"],
    "Bayview": ["Bayview Hunters Point"], "SoMa": ["South of Market"],
    "Downtown": ["Financial District/South Beach", "Chinatown", "Tenderloin", "Nob Hill"],
}


def _norm(text):
    return " " + re.sub(r"[^a-z0-9/ '-]", " ", text.lower().replace("’", "'")).replace("'s ", " ") + " "


def normalize(text):
    """Lowercase, fix typos. Returns (normalized text, list of (original, fixed) corrections)."""
    words = re.findall(r"[a-z0-9'./-]+|[^a-z0-9'./-]+", text.lower().replace("’", "'"))
    fixes, out = [], []
    vocab = set(FUZZY_VOCAB)
    for w in words:
        if w in TYPOS:
            if TYPOS[w] not in ("going to", "want to", "please", "tomorrow", "tonight"):
                fixes.append((w, TYPOS[w]))
            out.append(TYPOS[w])
        elif w.isalpha() and len(w) >= 6 and w not in vocab:
            m = difflib.get_close_matches(w, FUZZY_VOCAB, n=1, cutoff=0.85)
            if m:
                fixes.append((w, m[0]))
                out.append(m[0])
            else:
                out.append(w)
        else:
            out.append(w)
    return "".join(out), fixes


def wants_weather(text_norm):
    return bool(WEATHER_RE.search(text_norm))


def wants_unchecked(text_norm):
    return bool(UNCHECKED_RE.search(text_norm)) and bool(DRAIN_WORD_RE.search(text_norm))


def _names(places):
    """lowercase name -> canonical place, for the known places plus landmarks pointing at known places."""
    names = {p.lower(): p for p in places}
    for alias, canon in LANDMARKS.items():
        if canon in places:
            names.setdefault(alias, canon)
    return names


def find_place(text, places):
    """Known place in the text. Returns (canonical, how) or (None, None).
    how: 'exact', ('alias', original) or ('fuzzy', original)."""
    t = _norm(text)
    if any(f" {x} " in t for x in NOT_SF):
        return None, None
    names = _names(places)
    for n in sorted(names, key=len, reverse=True):
        key = f" {n} "
        pos = t.find(key)
        if pos < 0:
            continue
        nxt = t[pos + len(key):].split(" ", 1)[0]
        prev = t[:pos].split()[-1] if t[:pos].split() else ""
        if nxt not in FOLLOW_OK or (prev in PRECEDE_BAD and f"{prev} {n}" not in names):
            return None, None          # e.g. "Mission Bay" with no Mission Bay entry: unknown, not the Mission
        canon = names[n]
        return canon, ("exact" if n == canon.lower() else ("alias", n))
    # Typo tolerance: compare 1-3 word phrases (5+ letters) with every name.
    toks = t.split()
    cands = {" ".join(toks[i:i + k]) for k in (1, 2, 3) for i in range(len(toks) - k + 1)}
    best = None
    for c in cands:
        if len(c.replace(" ", "")) < 6 or c in FUZZY_VOCAB:
            continue
        m = difflib.get_close_matches(c, list(names), n=1, cutoff=0.86)
        if m:
            score = difflib.SequenceMatcher(None, c, m[0]).ratio()
            if best is None or score > best[0]:
                best = (score, c, m[0])
    if best:
        return names[best[2]], ("fuzzy", best[1])
    return None, None


def location_phrase(text):
    """The words after in/near/at/around/for, used to say which place was not understood."""
    m = re.search(r"\b(?:in|near|at|around|by)\s+(?:the\s+)?([A-Za-z][A-Za-z .'/-]{1,40})", text)
    if not m:
        return None
    phrase = re.split(r"\b(today|tomorrow|tonight|this|next|later|right now|please)\b", m.group(1))[0]
    phrase = phrase.strip(" .?!,")
    return phrase or None


# ---------- Drain IDs ----------
ID_PATTERNS = [
    re.compile(r"\b[nN]\s*[-–#]?\s*(\d{3,7})\b"),
    re.compile(r"\b(?:drain|inlet|asset|id)\s*(?:#|no\.?|number|id)?\s*[:#]?\s*(\d{3,7})\b", re.I),
]


def drain_id_candidates(text):
    """Drain IDs written in common ways ('N-315979', 'n315979', 'N 315979', 'drain #315979') -> 'N-315979'.
    The inventory is checked afterwards, so a candidate is only used if that exact asset exists."""
    out = []
    for pat in ID_PATTERNS:
        for d in pat.findall(text):
            cid = f"N-{d}"
            if cid not in out:
                out.append(cid)
    return out


# ---------- Coordinates ----------
COORD_RE = re.compile(r"(-?\d{1,3}\.\d+)\s*°?\s*([NSns])?\s*[, ]\s*(-?\d{1,3}\.\d+)\s*°?\s*([EWew])?")
SF_LAT, SF_LON = (37.69, 37.84), (122.33, 122.54)


def find_coords(text):
    """(lat, lon, note) from 'lat, lon' text, or None. Accepts N/W suffixes and lon, lat order.
    A positive longitude that would be inside SF if negative is flipped, with a note."""
    m = COORD_RE.search(text)
    if not m:
        return None
    a, ns, b, ew = float(m.group(1)), m.group(2), float(m.group(3)), m.group(4)
    if ns and ns.lower() == "s":
        a = -a
    if ew and ew.lower() == "w":
        b = -abs(b)
    if abs(a) > 90 and abs(b) <= 90:       # lon, lat order
        a, b = b, a
    note = None
    if b > 0 and SF_LAT[0] <= a <= SF_LAT[1] and SF_LON[0] <= b <= SF_LON[1] and not ew:
        b, note = -b, f"Read longitude {m.group(3)} as -{m.group(3)} (west)."
    return a, b, note
