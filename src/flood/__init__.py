"""DrainGuard flood calculator: rain event -> drain capacity -> water flowing over SF terrain -> flood map.

Pipeline (each step is its own module; this file is the shared contract they all code against):

    rain    = weather_input.from_today() | from_date(d) | from_manual(depth_in, duration_hr, shape)
    grid    = terrain.load_dem()                       # elevation grid of SF (north-up)
    drains  = drains.load_inlets()                     # every mapped SF inlet with a design flow rate
    cap     = drains.capacity_grid(grid, drains)       # m^3/s each grid cell can swallow
    result  = hydro.simulate(grid, cap, rain)          # flood depth per cell
    freq    = frequency.commonality(rain)              # how often SF sees a storm this size
    calc.run(...) wires them together for the app tab and the CLI.

Grid convention (shared by every module):
- Arrays are 2-D numpy, shape (rows, cols), row 0 = NORTH edge, col 0 = WEST edge.
- Cell (r, c) centre: lat = lat_top - (r + 0.5) * dlat ; lon = lon_left + (c + 0.5) * dlon.
- Elevation in metres above sea level (NAVD88-ish); NaN = no data / outside the model area.
"""
from dataclasses import dataclass, field

import numpy as np

# Model area: San Francisco city (same box the rest of the app uses for filtering).
SF_BBOX = (37.705, 37.835, -122.520, -122.355)   # lat_min, lat_max, lon_min, lon_max
DEFAULT_CELL_M = 30.0                             # working grid resolution in metres
LAT0 = 37.76                                      # reference latitude for metre conversions
M_PER_DEG_LAT = 110540.0
M_PER_DEG_LON = 111320.0 * float(np.cos(np.radians(LAT0)))
MM_PER_IN = 25.4


@dataclass
class Grid:
    z: np.ndarray            # float32 elevation (m), NaN outside model area
    lat_top: float           # latitude of the north edge of row 0
    lon_left: float          # longitude of the west edge of col 0
    dlat: float              # degrees latitude per row (positive)
    dlon: float              # degrees longitude per column (positive)
    land: np.ndarray = None  # bool mask, True = land cell inside SF (False = bay/ocean/outside)

    @property
    def shape(self):
        return self.z.shape

    @property
    def cell_dx_m(self):     # east-west cell size (m)
        return self.dlon * M_PER_DEG_LON

    @property
    def cell_dy_m(self):     # north-south cell size (m)
        return self.dlat * M_PER_DEG_LAT

    @property
    def cell_area_m2(self):
        return self.cell_dx_m * self.cell_dy_m

    def bounds(self):
        """[[lat_min, lon_min], [lat_max, lon_max]] (folium ImageOverlay order)."""
        rows, cols = self.shape
        return [[self.lat_top - rows * self.dlat, self.lon_left], [self.lat_top, self.lon_left + cols * self.dlon]]

    def rc(self, lat, lon):
        """Lat/lon (scalars or arrays) -> integer (row, col) arrays; may fall outside the grid."""
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        return (np.floor((self.lat_top - lat) / self.dlat).astype(int),
                np.floor((lon - self.lon_left) / self.dlon).astype(int))

    def latlon(self, r, c):
        """(row, col) -> cell-centre (lat, lon)."""
        r, c = np.asarray(r, float), np.asarray(c, float)
        return self.lat_top - (r + 0.5) * self.dlat, self.lon_left + (c + 0.5) * self.dlon


@dataclass
class RainEvent:
    depth_in: float               # total rain over the event (inches)
    duration_hr: float            # event length (hours)
    intensity_mm_hr: np.ndarray   # hyetograph: rain rate per step (mm/h), len = n steps
    step_min: float = 15.0        # minutes per hyetograph step
    source: str = "manual"        # "today-observed" | "today-forecast" | "noaa-daily" | "manual"
    label: str = ""               # human text, e.g. "Nov 18, 2026 at SF Downtown (NOAA)"
    date: str = ""                # ISO date of the event if known
    notes: list = field(default_factory=list)  # caveats to show the user

    @property
    def peak_in_hr(self):
        return float(np.max(self.intensity_mm_hr)) / MM_PER_IN if len(self.intensity_mm_hr) else 0.0


@dataclass
class FloodResult:
    depth_m: np.ndarray           # max water depth per cell over the event (m), 0 = dry, NaN off-land
    grid: Grid
    rain: RainEvent
    volume_rain_m3: float = 0.0       # total rain falling on land
    volume_runoff_m3: float = 0.0     # rain that ran off (didn't soak in)
    volume_drained_m3: float = 0.0    # taken by storm drains
    volume_ponded_m3: float = 0.0     # left standing on the surface (max over event)
    volume_to_bay_m3: float = 0.0     # flowed off the edge / into the bay/ocean
    drains_overwhelmed: int = 0       # inlets whose cell got more inflow than design capacity
    timeline: list = field(default_factory=list)  # optional [{"t_hr", "flooded_km2", "ponded_m3"}...]


@dataclass
class Commonality:
    return_period_yr: float       # e.g. 7.5 = "about once every 7-8 years"; inf/None if beyond table
    annual_chance_pct: float      # 100 / return_period
    duration_hr: float            # duration it was evaluated at
    depth_in: float
    method: str                   # "NOAA Atlas 14" etc.
    sentence: str                 # plain-English summary for the UI
    empirical: dict = field(default_factory=dict)  # from the NOAA daily record: days_at_or_above, years, first_year, rank...
    table: object = None          # pandas DataFrame of the Atlas 14 depth table used (duration x return period)
