"""Command line flood calculator: python -m src.flood --today | --date YYYY-MM-DD | --depth IN --duration HR."""
import argparse
import json
import sys

from src.flood import calc

SHAPES = ["scs1", "uniform", "front", "burst"]


def build_parser():
    p = argparse.ArgumentParser(prog="python -m src.flood",
                                description="Simulate where a rainstorm floods San Francisco.")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--today", action="store_true", help="today's observed + forecast rain (default)")
    src.add_argument("--date", help="a past day, e.g. 2026-11-18 (NOAA daily record)")
    src.add_argument("--depth", type=float, help="manual storm: total rain in inches (use with --duration)")
    src.add_argument("--list-storms", action="store_true", help="list notable past SF storms and exit")
    p.add_argument("--duration", type=float, help="manual storm length in hours")
    p.add_argument("--shape", choices=SHAPES, default="scs1", help="manual storm hyetograph shape")
    p.add_argument("--clog", type=float, default=0.0, help="percent of drain capacity lost to clogging")
    p.add_argument("--runoff", type=float, default=0.85, help="runoff coefficient (0-1)")
    p.add_argument("--cell", type=float, default=30.0, help="grid cell size in metres")
    p.add_argument("--top", type=int, default=10, help="how many hotspots to print")
    p.add_argument("--geojson", help="write flooded areas to this GeoJSON file")
    p.add_argument("--png", help="write a depth map PNG to this file")
    p.add_argument("--csv", help="write hotspots to this CSV file")
    return p


def rain_from_args(args):
    """RainEvent for the chosen source."""
    w = calc._mod("weather_input")
    if args.depth is not None:
        if args.duration is None or args.duration <= 0:
            raise ValueError("--depth needs --duration (hours, > 0)")
        if args.depth < 0:
            raise ValueError("--depth must be 0 or more inches")
        return w.from_manual(args.depth, args.duration, shape=args.shape)
    if args.date:
        return w.from_date(args.date)
    return w.from_today()


def _fmt(x, nd=1):
    return "n/a" if x is None else f"{x:,.{nd}f}"


def _return_period(rp):
    if rp is None:
        return "n/a"
    if rp == float("inf"):
        return "beyond the NOAA table (rarer than 1-in-1000 yr)"
    return f"about 1 in {rp:,.1f} years" if rp >= 1 else f"{rp:.2f} years (several times a year)"


def report(sc, top=10):
    """Readable multi-line text report for a Scenario."""
    r, s = sc.result.rain, sc.summary
    lines = ["", "=== DrainGuard flood calculator ===", "",
             f"Rain event: {r.label or r.source}",
             f"  {s['rain_in']:.2f} in over {s['duration_hr']:g} h, peak {s['peak_in_hr']:.2f} in/h  [{r.source}]"]
    for n in r.notes or []:
        lines.append(f"  note: {n}")
    lines += ["", "How common is it?", f"  {s['sentence']}",
              f"  Return period: {_return_period(s['return_period_yr'])}"
              + (f"  ({_fmt(s['annual_chance_pct'])}% chance in any year)" if s["annual_chance_pct"] is not None else ""),
              "", "Flooding:",
              f"  Flooded (6 in or more):  {s['flooded_km2']:.2f} km2  ({s['flooded_acres']:,.0f} acres)",
              f"  Deep (0.5 m or more):    {s['deep_km2']:.2f} km2",
              f"  Deepest water:           {s['max_depth_in']:.1f} in",
              f"  Drains took:             {s['drained_pct']:.0f}% of runoff",
              f"  Left ponding:            {s['ponded_m3']:,} m3",
              f"  Flowed to bay/ocean:     {s['to_bay_m3']:,} m3",
              f"  Inlets overwhelmed:      {s['inlets_overwhelmed']:,} of {s['inlets_total']:,}"]
    if s["worst_neighborhoods"]:
        lines.append(f"  Worst neighborhoods:     {', '.join(s['worst_neighborhoods'])}")
    hs = sc.hotspots.head(top)
    lines += ["", f"Top {len(hs)} hotspots:" if len(hs) else "No flooded hotspots (nothing 6 in or deeper)."]
    if len(hs):
        lines.append(f"  {'#':>2}  {'neighborhood':<32} {'depth in':>8} {'acres':>7}  lat, lon")
        for i, h in enumerate(hs.itertuples(), 1):
            lines.append(f"  {i:>2}  {str(h.neighborhood)[:32]:<32} {h.max_depth_in:>8.1f} {h.area_acres:>7.2f}"
                         f"  {h.lat:.5f}, {h.lon:.5f}")
    return "\n".join(lines) + "\n"


def _progress(*a, **k):
    """Tolerant progress callback: prints a percentage if the first arg is a 0-1 fraction."""
    if a and isinstance(a[0], (int, float)):
        f = a[0] if a[0] <= 1 else a[0] / 100
        print(f"\r  simulating... {f * 100:5.1f}%", end="", file=sys.stderr, flush=True)


def list_storms():
    df = calc._mod("weather_input").notable_storms(n=10)
    print("Notable SF storms (NOAA daily record):")
    print(df.to_string(index=False) if hasattr(df, "to_string") else df)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.list_storms:
            list_storms()
            return 0
        rain = rain_from_args(args)
        print(f"Running flood model for: {rain.label or rain.source} ...", file=sys.stderr)
        sc = calc.run(rain, cell_m=args.cell, clog_pct=args.clog, runoff_coeff=args.runoff, progress=_progress)
        print("", file=sys.stderr)
        print(report(sc, top=args.top))
        if args.csv:
            sc.hotspots.to_csv(args.csv, index=False)
            print(f"Wrote hotspots to {args.csv}")
        if args.geojson:
            with open(args.geojson, "w") as f:
                json.dump(calc.geojson(sc), f)
            print(f"Wrote flooded areas to {args.geojson}")
        if args.png:
            try:
                from src.flood.render import depth_png
                with open(args.png, "wb") as f:
                    f.write(depth_png(sc.result))
                print(f"Wrote depth map to {args.png}")
            except ImportError as e:
                print(f"Can't write PNG (render module unavailable: {e})", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
