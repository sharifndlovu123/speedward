"""Build speedward/reports/worldmap.svg.json from Natural Earth 110m land.

Source: Natural Earth 110m land (https://www.naturalearthdata.com/), public domain.
GeoJSON: https://github.com/nvkelso/natural-earth-vector/raw/master/geojson/ne_110m_land.geojson
Run once (offline afterwards): python scripts/build_worldmap.py ne_110m_land.geojson
Output paths are projected to the 1000x500 equirectangular space used by speedward.reports.map.
"""
import json
import sys
from pathlib import Path

W, H = 1000, 500
OUT = Path(__file__).resolve().parent.parent / "speedward" / "reports" / "worldmap.svg.json"


def project(lon, lat):
    return (lon + 180) / 360 * W, (90 - lat) / 180 * H


def ring_path(ring):
    pts, last = [], None
    for lon, lat in ring:
        x, y = project(lon, lat)
        p = (round(x, 1), round(y, 1))
        if p != last:
            pts.append(p)
            last = p
    if len(pts) < 3:
        return ""
    return "M" + "L".join(f"{x:g},{y:g}" for x, y in pts) + "Z"


def main(src):
    data = json.loads(Path(src).read_text())
    paths = []
    for feat in data["features"]:
        g = feat["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            for ring in poly:
                d = ring_path(ring)
                if d:
                    paths.append(d)
    OUT.write_text(json.dumps({
        "width": W, "height": H,
        "attribution": "Natural Earth 110m land, public domain",
        "paths": paths,
    }, separators=(",", ":")))
    print(OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main(sys.argv[1])
