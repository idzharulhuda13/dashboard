#!/usr/bin/env python3
"""
pipeline/fetch_basemap.py
Download land and tectonic plate vector data, transform coordinates (y = -lat),
clip to Indonesia bbox, simplify via Douglas-Peucker (tol 0.05), and emit
data/raw/basemap.json.
"""

import hashlib
import json
import math
import os
import sys
import tempfile
import urllib.request

LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson"
LAND_SHA = "1ac90796408bc6ad6911d69448485d3c4dbf2190370080368a09976e1c9f7416"

PLATES_URL = "https://raw.githubusercontent.com/fraxen/tectonicplates/master/GeoJSON/PB2002_boundaries.json"
PLATES_SHA = "42b3e0876a7e40f133e958ba7ab85f8851b5c693a046fbc3b138e7751863d92a"

BBOX_X_MIN = 94.5
BBOX_X_MAX = 141.5
BBOX_Y_MIN = -6.5
BBOX_Y_MAX = 11.5

TOLERANCE = 0.05
PRECISION = 2

LABELS = [
    {"text": "Sumatera", "x": 102.5, "y": 1.6, "kind": "island"},
    {"text": "Kalimantan", "x": 113.6, "y": 0.2, "kind": "island"},
    {"text": "Sulawesi", "x": 121.0, "y": 2.6, "kind": "island"},
    {"text": "Jawa", "x": 110.0, "y": 7.2, "kind": "island"},
    {"text": "Papua", "x": 137.5, "y": 4.6, "kind": "island"},
]

SOURCES = [
    {
        "name": "Natural Earth",
        "url": LAND_URL,
        "sha256": LAND_SHA,
        "license": "domain publik",
    },
    {
        "name": "Bird 2003 PB2002 (fraxen/tectonicplates)",
        "url": PLATES_URL,
        "sha256": PLATES_SHA,
        "license": "ODC-By 1.0",
    },
]


def fetch_cached(url: str, expected_sha: str) -> bytes:
    cache_dir = os.path.join(tempfile.gettempdir(), "basemap_src")
    os.makedirs(cache_dir, exist_ok=True)
    filename = os.path.basename(url)
    cache_path = os.path.join(cache_dir, filename)

    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        if sha == expected_sha:
            return data
        os.remove(cache_path)

    req = urllib.request.Request(url, headers={"User-Agent": "AntigravityBasemapPipeline/1.0"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    sha = hashlib.sha256(data).hexdigest()
    if sha != expected_sha:
        raise ValueError(f"SHA256 mismatch for {url}: expected {expected_sha}, got {sha}")

    with open(cache_path, "wb") as f:
        f.write(data)
    return data


def perp_distance(pt, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denom = math.hypot(dx, dy)
    if denom == 0.0:
        return math.hypot(pt[0] - start[0], pt[1] - start[1])
    return abs(dy * pt[0] - dx * pt[1] + end[0] * start[1] - end[1] * start[0]) / denom


def douglas_peucker(points, tol):
    if len(points) <= 2:
        return list(points)
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        dmax = 0.0
        idx = start
        s_pt = points[start]
        e_pt = points[end]
        for i in range(start + 1, end):
            d = perp_distance(points[i], s_pt, e_pt)
            if d > dmax:
                dmax = d
                idx = i
        if dmax > tol:
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))
    return [points[i] for i in range(len(points)) if keep[i]]


def clip_polygon_sutherland_hodgman(poly):
    if len(poly) > 1 and poly[0] == poly[-1]:
        poly = poly[:-1]

    def clip_edge(points, inside_fn, intersect_fn):
        res = []
        if not points:
            return res
        prev = points[-1]
        prev_inside = inside_fn(prev)
        for curr in points:
            curr_inside = inside_fn(curr)
            if curr_inside:
                if not prev_inside:
                    res.append(intersect_fn(prev, curr))
                res.append(curr)
            elif prev_inside:
                res.append(intersect_fn(prev, curr))
            prev = curr
            prev_inside = curr_inside
        return res

    poly = clip_edge(poly, lambda p: p[0] >= BBOX_X_MIN,
                     lambda p1, p2: (BBOX_X_MIN, max(BBOX_Y_MIN, min(BBOX_Y_MAX, p1[1] + (BBOX_X_MIN - p1[0]) * (p2[1] - p1[1]) / (p2[0] - p1[0])))))
    poly = clip_edge(poly, lambda p: p[0] <= BBOX_X_MAX,
                     lambda p1, p2: (BBOX_X_MAX, max(BBOX_Y_MIN, min(BBOX_Y_MAX, p1[1] + (BBOX_X_MAX - p1[0]) * (p2[1] - p1[1]) / (p2[0] - p1[0])))))
    poly = clip_edge(poly, lambda p: p[1] >= BBOX_Y_MIN,
                     lambda p1, p2: (max(BBOX_X_MIN, min(BBOX_X_MAX, p1[0] + (BBOX_Y_MIN - p1[1]) * (p2[0] - p1[0]) / (p2[1] - p1[1]))), BBOX_Y_MIN))
    poly = clip_edge(poly, lambda p: p[1] <= BBOX_Y_MAX,
                     lambda p1, p2: (max(BBOX_X_MIN, min(BBOX_X_MAX, p1[0] + (BBOX_Y_MAX - p1[1]) * (p2[0] - p1[0]) / (p2[1] - p1[1]))), BBOX_Y_MAX))
    return poly


def liang_barsky(p1, p2):
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    p = [-dx, dx, -dy, dy]
    q = [x1 - BBOX_X_MIN, BBOX_X_MAX - x1, y1 - BBOX_Y_MIN, BBOX_Y_MAX - y1]
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi < 0:
                return None
        elif pi < 0:
            u1 = max(u1, qi / pi)
        else:
            u2 = min(u2, qi / pi)
        if u1 > u2:
            return None
    cx1 = max(BBOX_X_MIN, min(BBOX_X_MAX, x1 + u1 * dx))
    cy1 = max(BBOX_Y_MIN, min(BBOX_Y_MAX, y1 + u1 * dy))
    cx2 = max(BBOX_X_MIN, min(BBOX_X_MAX, x1 + u2 * dx))
    cy2 = max(BBOX_Y_MIN, min(BBOX_Y_MAX, y1 + u2 * dy))
    if math.hypot(cx2 - cx1, cy2 - cy1) < 1e-9:
        return None
    return (cx1, cy1), (cx2, cy2)


def clip_linestring(coords):
    lines = []
    cur_line = []
    for i in range(len(coords) - 1):
        seg = liang_barsky(coords[i], coords[i + 1])
        if seg is None:
            if len(cur_line) >= 2:
                lines.append(cur_line)
            cur_line = []
        else:
            p_start, p_end = seg
            if not cur_line:
                cur_line = [p_start, p_end]
            else:
                if math.hypot(p_start[0] - cur_line[-1][0], p_start[1] - cur_line[-1][1]) < 1e-6:
                    cur_line.append(p_end)
                else:
                    lines.append(cur_line)
                    cur_line = [p_start, p_end]
    if len(cur_line) >= 2:
        lines.append(cur_line)
    return lines


def ring_bbox_misses(points):
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    return max_x < BBOX_X_MIN or min_x > BBOX_X_MAX or max_y < BBOX_Y_MIN or min_y > BBOX_Y_MAX


def point_in_polygon(x, y, ring):
    inside = False
    n = len(ring)
    for i in range(n):
        j = (i - 1) % n
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
    return inside


def format_subpath_polygon(pts):
    return "M" + "L".join(f"{x:.{PRECISION}f} {y:.{PRECISION}f}" for x, y in pts) + "Z"


def format_subpath_linestring(pts):
    return "M" + "L".join(f"{x:.{PRECISION}f} {y:.{PRECISION}f}" for x, y in pts)


def process_land(geojson_data):
    features = geojson_data.get("features", [])
    rings_to_process = []

    for f in features:
        geom = f.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "Polygon":
            for ring in coords:
                rings_to_process.append(ring)
        elif gtype == "MultiPolygon":
            for poly in coords:
                for ring in poly:
                    rings_to_process.append(ring)

    subpaths = []
    land_polygons_for_anchor_check = []

    for ring in rings_to_process:
        if not ring:
            continue
        # transform: x = lon, y = -lat
        transformed = [(pt[0], -pt[1]) for pt in ring]
        if ring_bbox_misses(transformed):
            continue

        clipped = clip_polygon_sutherland_hodgman(transformed)
        if len(set(clipped)) < 3:
            continue

        clipped_closed = clipped + [clipped[0]]
        simplified = douglas_peucker(clipped_closed, TOLERANCE)

        if len(simplified) < 3:
            pts_to_use = clipped_closed
        else:
            pts_to_use = simplified

        if len(pts_to_use) >= 3:
            # Check if ring is not entirely collapsed to a point
            subpaths.append(format_subpath_polygon(pts_to_use))
            land_polygons_for_anchor_check.append(pts_to_use)

    return "".join(subpaths), land_polygons_for_anchor_check


def process_plates(geojson_data):
    features = geojson_data.get("features", [])
    lines_to_process = []

    for f in features:
        geom = f.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "LineString":
            lines_to_process.append(coords)
        elif gtype == "MultiLineString":
            for line in coords:
                lines_to_process.append(line)

    subpaths = []
    for line in lines_to_process:
        if not line:
            continue
        transformed = [(pt[0], -pt[1]) for pt in line]
        if ring_bbox_misses(transformed):
            continue

        for cl in clip_linestring(transformed):
            simplified = douglas_peucker(cl, TOLERANCE)

            pts_to_use = simplified if len(simplified) >= 2 else cl
            if len(pts_to_use) >= 2:
                subpaths.append(format_subpath_linestring(pts_to_use))

    return "".join(subpaths)


def main():
    print("Fetching Natural Earth land data...")
    land_raw = fetch_cached(LAND_URL, LAND_SHA)
    land_geojson = json.loads(land_raw.decode("utf-8"))

    print("Fetching tectonic plates data...")
    plates_raw = fetch_cached(PLATES_URL, PLATES_SHA)
    plates_geojson = json.loads(plates_raw.decode("utf-8"))

    print("Processing land...")
    land_path, land_polys = process_land(land_geojson)

    print("Processing plates...")
    plates_path = process_plates(plates_geojson)

    # Point-in-polygon verification for island labels
    for lbl in LABELS:
        name = lbl["text"]
        x, y = lbl["x"], lbl["y"]
        inside_any = any(point_in_polygon(x, y, poly) for poly in land_polys)
        print(f"Label {name} at ({x}, {y}) inside land: {inside_any}")

    land_subpaths = land_path.count("M")
    plates_subpaths = plates_path.count("M")
    print(f"Land: {land_subpaths} subpaths, {len(land_path)} chars")
    print(f"Plates: {plates_subpaths} subpaths, {len(plates_path)} chars")

    output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "raw")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "basemap.json")

    basemap_data = {
        "land": land_path,
        "plates": plates_path,
        "labels": LABELS,
        "sources": SOURCES,
        "bbox": [BBOX_X_MIN, BBOX_Y_MIN, 47, 18],
        "tolerance": TOLERANCE,
        "precision": PRECISION,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(basemap_data, f, ensure_ascii=False, indent=2)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
