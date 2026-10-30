"""Small command line helper.

Windows (PowerShell), from the repository folder:
    python -m noctinirs_host check-geometry config\\optical_geometry_v1.json
"""

from __future__ import annotations

import argparse
import sys

from .geometry import GeometryError, load_geometry


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="noctinirs_host")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("check-geometry", help="validate a geometry JSON file")
    g.add_argument("path")
    args = p.parse_args(argv)
    try:
        geo = load_geometry(args.path)
    except GeometryError as e:
        print("GEOMETRY INVALID:")
        for prob in e.problems:
            print(" -", prob)
        return 1
    print(f"OK  sha256={geo.sha256}")
    print(f"geometry_map_id={geo.geometry_map_id}  status={geo.status}")
    if geo.is_draft:
        print("WARNING: geometry is a DRAFT (distances not locked, wavelength identity unverified)")
    for c in geo.channels:
        print(f"  {c.name:5s} {c.source:3s} -> {c.pd} (AIN{c.ain})  {c.sd_mm:5.1f} mm  {c.kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
