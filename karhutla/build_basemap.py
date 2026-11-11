#!/usr/bin/env python3
"""build_basemap.py - batas provinsi SiPongi (GeoJSON) -> satu path SVG ringan untuk peta karhutla.

Sumber: https://opsroom-sipongi.gakkum.kehutanan.go.id/api/boundaries?level=provinsi (disimpan di
data/karhutla/source/provinsi.geojson). Proyeksi equirectangular sederhana, sama dengan yang dipakai
halaman untuk menaruh sel titik panas, jadi titik dan daratan selalu sejajar.

Keluaran: data/karhutla/basemap.json {"w","h","scale","lon0","lat0","land": "<path d>"}
Jalankan sekali; batas wilayah tidak berubah tiap hari.
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "karhutla" / "source" / "provinsi.geojson"
OUT = ROOT / "data" / "karhutla" / "basemap.json"
URL = "https://opsroom-sipongi.gakkum.kehutanan.go.id/api/boundaries?level=provinsi"
LON0, LAT0, LON1, LAT1 = 94.0, 7.0, 141.5, -11.5
SCALE = 22.0  # px per derajat
TOL = 0.9     # toleransi Douglas-Peucker, px
MIN_AREA = 2.0  # px^2; pulau lebih kecil dibuang (tidak terbaca di skala ini)


def proj(lon: float, lat: float) -> tuple[float, float]:
    return (lon - LON0) * SCALE, (LAT0 - lat) * SCALE


def dp(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        far, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / L
            if d > far:
                far, idx = d, i
        if far > tol and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def area(pts: list[tuple[float, float]]) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def main() -> None:
    if not SRC.exists():
        SRC.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        SRC.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    gj = json.loads(SRC.read_text())
    parts, rings = [], 0
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            ring = [proj(x, y) for x, y in poly[0]]
            if area(ring) < MIN_AREA:
                continue
            # cincin tertutup: titik awal = titik akhir, jadi DP dijalankan per dua paruh supaya garis
            # acuan tidak pernah nol panjangnya
            h = len(ring) // 2
            s = dp(ring[: h + 1], TOL)[:-1] + dp(ring[h:], TOL)
            if len(s) < 4:
                continue
            parts.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in s[:-1]) + "Z")
            rings += 1
    w, h = (LON1 - LON0) * SCALE, (LAT0 - LAT1) * SCALE
    OUT.write_text(json.dumps({"w": round(w), "h": round(h), "scale": SCALE, "lon0": LON0, "lat0": LAT0,
                               "provinces": len(gj["features"]), "land": "".join(parts)}) + "\n")
    print(f"basemap: {len(gj['features'])} provinsi, {rings} cincin, {OUT.stat().st_size} byte")


if __name__ == "__main__":
    main()
