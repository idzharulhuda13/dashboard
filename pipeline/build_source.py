#!/usr/bin/env python3
"""Build the frozen source table for the Indonesia earthquake data story.

Input  : data/raw/usgs_idn_m5plus_1976_2026.csv   (USGS FDSN event API, M5.0+, bbox 94.5-141.5E / 11.5S-6.5N)
         data/raw/provinces_bapanas.json          (38 Indonesian provinces + centroid coordinates)
         data/raw/bmkg_gempaterkini.json          (BMKG latest earthquakes, live)
         data/raw/bmkg_gempadirasakan.json        (BMKG felt earthquakes, live)
Output : data/out/gempa_events.csv                (one row per event, derived columns attached)
         data/out/bmkg_live.json                  (normalised live block)
         data/out/build_report.json               (counts the story is allowed to quote)

Every derived column is documented in METHOD below and repeated in the page footer.
No number is typed by hand into the template: the page reads data.json, which build_data.py
derives from these files.

METHOD
  scope     = events whose region (the final comma-separated field of the USGS `place` value)
              is in KEEP_REGIONS or HISTORIC_EVENT_REGIONS. The bbox alone would pull in foreign
              events (such as Philippine and Timor-Leste events); those are dropped and the
              dropped counts are recorded in the build report.
  province  = nearest Indonesian province centroid by haversine distance (Bapanas reference file).
              This is a PROXIMITY label, not an administrative boundary.
  depth     = seismological bands: dangkal <70 km, menengah 70-300 km, dalam >=300 km.
  time_wib  = event time converted to UTC+7; the source file keeps UTC.
"""

import csv
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "out")

# USGS place values that mean "inside Indonesian territory or Indonesian waters".
KEEP_REGIONS = {
    "Indonesia",
    "Banda Sea",
    "Molucca Sea",
    "Flores Sea",
    "Celebes Sea",
    "Java Sea",
    "Bali Sea",
    "Savu Sea",
    "Seram Sea",
    "Halmahera",
    "Arafura Sea",
    "Indonesia Earthquake",
}

# Regions named after a historic event instead of a country.
HISTORIC_EVENT_REGIONS = {
    "2004 Sumatra - Andaman Islands Earthquake",
}

DEPTH_BANDS = [(0.0, 70.0, "dangkal"), (70.0, 300.0, "menengah"), (300.0, 1000.0, "dalam")]

WIB = timezone(timedelta(hours=7))


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def depth_band(depth_km):
    for lo, hi, name in DEPTH_BANDS:
        if lo <= depth_km < hi:
            return name
    return "dalam"


def load_provinces():
    with open(os.path.join(RAW, "provinces_bapanas.json"), encoding="utf-8") as fh:
        data = json.load(fh)["data"]
    out = []
    for row in data:
        lat, lon = (float(x) for x in row["latlong"].split(","))
        out.append({"name": row["nama"].strip(), "lat": lat, "lon": lon})
    return out


def nearest_province(provinces, lat, lon):
    best, best_d = None, float("inf")
    for prov in provinces:
        d = haversine(lat, lon, prov["lat"], prov["lon"])
        if d < best_d:
            best, best_d = prov, d
    return best["name"], best_d


def build_events(provinces):
    path = os.path.join(RAW, "usgs_idn_m5plus_1976_2026.csv")
    rows, last_fields, dropped = [], Counter(), Counter()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            place = (row.get("place") or "").strip()
            region = place.split(",")[-1].strip()
            last_fields[region] += 1
            in_region = region in KEEP_REGIONS
            in_historic = region in HISTORIC_EVENT_REGIONS
            if not (in_region or in_historic):
                dropped[region] += 1
                continue
            lat, lon = float(row["latitude"]), float(row["longitude"])
            depth = float(row["depth"]) if row["depth"] not in ("", None) else None
            mag = float(row["mag"]) if row["mag"] not in ("", None) else None
            if depth is None or mag is None:
                continue
            ts = datetime.strptime(row["time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            prov, dist = nearest_province(provinces, lat, lon)
            # Band the depth from the ROUNDED value that gets written to the CSV, so the label a
            # reader sees always agrees with the number beside it. Banding the raw float instead
            # left 10 events stored as 70.0 km and labelled "dangkal" (<70 km).
            depth_km = round(depth, 1)
            rows.append(
                {
                    "id": row["id"],
                    "time_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "time_wib": ts.astimezone(WIB).strftime("%Y-%m-%d %H:%M"),
                    "date_wib": ts.astimezone(WIB).strftime("%Y-%m-%d"),
                    "year": ts.astimezone(WIB).year,
                    "lat": round(lat, 3),
                    "lon": round(lon, 3),
                    "depth_km": depth_km,
                    "depth_band": depth_band(depth_km),
                    "mag": round(mag, 1),
                    "mag_band": "M%d" % int(mag),
                    "place": place,
                    "region": region,
                    "province": prov,
                    "province_km": round(dist, 1),
                }
            )
    rows.sort(key=lambda r: r["time_utc"])
    return rows, last_fields, dropped


def build_live():
    with open(os.path.join(RAW, "bmkg_gempaterkini.json"), encoding="utf-8") as fh:
        latest = json.load(fh)["Infogempa"]["gempa"]
    with open(os.path.join(RAW, "bmkg_gempadirasakan.json"), encoding="utf-8") as fh:
        felt = json.load(fh)["Infogempa"]["gempa"]

    def norm(item):
        lat, lon = (float(x) for x in item["Coordinates"].split(","))
        return {
            "waktu_wib": item["Jam"],
            "tanggal": item["Tanggal"],
            "datetime_utc": item["DateTime"],
            "lat": lat,
            "lon": lon,
            "mag": float(item["Magnitude"]),
            "depth_km": float(str(item["Kedalaman"]).replace(" km", "").strip()),
            "wilayah": item["Wilayah"].strip(),
            "potensi": item.get("Potensi", "").strip(),
            "dirasakan": item.get("Dirasakan", "").strip(),
        }

    return {
        "latest": [norm(x) for x in latest],
        "felt": [norm(x) for x in felt],
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    provinces = load_provinces()
    rows, last_fields, dropped = build_events(provinces)
    live = build_live()

    csv_path = os.path.join(OUT, "gempa_events.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with open(os.path.join(OUT, "bmkg_live.json"), "w", encoding="utf-8") as fh:
        json.dump(live, fh, ensure_ascii=False, indent=1)

    raw_events = sum(last_fields.values())
    kept_events = len(rows)
    dropped_outside_indonesia = sum(dropped.values())
    accounting_ok = (kept_events + dropped_outside_indonesia) == raw_events
    dropped_breakdown = dict(sorted(dropped.items(), key=lambda kv: -kv[1]))
    kept_breakdown = dict(
        sorted(Counter(r["region"] for r in rows).items(), key=lambda kv: -kv[1])
    )
    report = {
        "raw_events": raw_events,
        "kept_events": kept_events,
        "dropped_outside_indonesia": dropped_outside_indonesia,
        "accounting_ok": accounting_ok,
        "dropped_breakdown": dropped_breakdown,
        "kept_breakdown": kept_breakdown,
        "first_event": rows[0]["time_utc"],
        "last_event": rows[-1]["time_utc"],
        "provinces": len(provinces),
    }
    with open(os.path.join(OUT, "build_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    mag_bands = Counter(r["mag_band"] for r in rows)
    depth_bands = Counter(r["depth_band"] for r in rows)
    provinces_top = Counter(r["province"] for r in rows).most_common(8)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("accounting_ok:", accounting_ok)
    print("magnitudo:", dict(sorted(mag_bands.items())))
    print("kedalaman:", dict(depth_bands))
    print("provinsi teratas:", provinces_top)
    print("gempa BMKG live:", len(live["latest"]), "| dirasakan:", len(live["felt"]))


if __name__ == "__main__":
    main()
