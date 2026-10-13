#!/usr/bin/env python3
"""Append new earthquakes to the source table. This is the harvester.

The page's dataset is append-only: the tail moves, the past does not. This script adds
only what happened after the last event already recorded, and never rewrites history.

Input  : data/raw/usgs_idn_m5plus_1976_2026.csv   (frozen base, from 12 Oct 2026, NEVER written to)
         data/raw/usgs_append.csv                 (everything appended since, written by this script)
         data/raw/bmkg_*.json                     (BMKG live snapshots, refreshed in place)
Output : the same two CSVs, the base untouched and the append file grown by the new events
         data/out/append_report.json              (what happened, so a cron failure is visible)

Why a separate append file instead of appending to the base
  The base harvest is the evidence for every number the story has already published. Keeping
  it byte-stable means the original can always be re-checked, and the sha256 provenance cap in
  the footer keeps meaning something. Appends live beside it and are merged at build time.

Overlap window
  USGS revises events and can publish one slightly late, so each run re-queries the last
  OVERLAP_DAYS days rather than only asking for what is genuinely new. Duplicates are removed
  by event id, which is why re-querying is safe and idempotent: running twice in a row must
  add nothing the second time.

Scope
  The bbox alone is not the filter. It pulls in Philippine, Malaysian and Timor-Leste events,
  so every fetched row goes through the same KEEP_REGIONS / HISTORIC_EVENT_REGIONS test that
  build_source.py applies. Foreign rows are counted and reported, never silently kept.

Guards against a silent empty harvest
  Measured failure this defends against: a wrong path on the USGS host answers HTTP 200 with an
  HTML documentation page. csv.DictReader parses it without complaint, every row loses its
  `place` field, every row is classified as "outside scope", and the job reports success having
  harvested nothing. A harvester that cannot tell "there were no new earthquakes" from "the
  request was not understood" is worse than no harvester, because it fails at the one job it
  has. So the response must prove it is an answer to OUR query before a single byte is written:
  a CSV content type, the expected columns, and every row inside the requested box and window.
  Any violation is a refusal, not a warning.

Exit codes
  0 = ran and found nothing new, or appended cleanly
  1 = the fetch itself failed (network, HTTP error) and nothing was written
  2 = the response or the resulting file looked wrong; nothing was written
"""

import csv
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "out")

BASE_CSV = os.path.join(RAW, "usgs_idn_m5plus_1976_2026.csv")
APPEND_CSV = os.path.join(RAW, "usgs_append.csv")

LAT_MIN, LAT_MAX = -11.5, 6.5
LON_MIN, LON_MAX = 94.5, 141.5
MINMAG = "5"
OVERLAP_DAYS = 14
TIMEOUT = 60
UA = "dashboard.idzharulhuda.com earthquake data story (append job)"

REQUIRED_COLUMNS = {"time", "latitude", "longitude", "depth", "mag", "id", "place"}

BMKG_FILES = {
    "bmkg_autogempa.json": "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json",
    "bmkg_gempaterkini.json": "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json",
    "bmkg_gempadirasakan.json": "https://data.bmkg.go.id/DataMKG/TEWS/gempadirasakan.json",
}

# Same two sets as build_source.py, deliberately restated so this file can be audited alone.
# build_data.py asserts the two copies agree.
KEEP_REGIONS = {
    "Indonesia", "Banda Sea", "Molucca Sea", "Flores Sea", "Celebes Sea", "Java Sea",
    "Bali Sea", "Savu Sea", "Seram Sea", "Halmahera", "Arafura Sea", "Indonesia Earthquake",
}
HISTORIC_EVENT_REGIONS = {"2004 Sumatra - Andaman Islands Earthquake"}

TIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def http_get(url):
    """Return (status, body, content_type). Raises on transport failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, resp.read(), (resp.headers.get("Content-Type") or "")


def read_rows(path):
    """Every row of a CSV, or an empty list if the file does not exist yet."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def newest_time(rows):
    times = [r["time"] for r in rows if r.get("time")]
    return max(times) if times else None


def header_of(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def fetch_usgs(start_iso):
    """Query USGS and refuse anything that is not demonstrably the answer to that query."""
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv"
        f"&starttime={start_iso}&minmagnitude={MINMAG}"
        f"&minlatitude={LAT_MIN}&maxlatitude={LAT_MAX}"
        f"&minlongitude={LON_MIN}&maxlongitude={LON_MAX}"
        "&orderby=time"
    )
    status, body, ctype = http_get(url)
    text = body.decode("utf-8", "replace")

    if status != 200:
        raise ValueError(f"HTTP {status} from USGS")

    # Guard 1: the shape of the answer. A wrong path answers 200 with an HTML help page.
    if "csv" not in ctype.lower():
        raise ValueError(f"expected a CSV response, got Content-Type {ctype!r} "
                         f"(first 80 bytes: {text[:80]!r})")
    if text.lstrip().startswith("<"):
        raise ValueError(f"response is markup, not data (first 80 bytes: {text[:80]!r})")
    if text.strip().lower().startswith("error"):
        raise ValueError(f"USGS returned an error body: {text[:200]!r}")

    reader = csv.DictReader(text.splitlines())
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        # Guard 2: the columns we rely on. Without this a changed schema silently drops every
        # row into the "foreign" bucket, because a missing `place` reads as an empty region.
        raise ValueError(f"CSV is missing required columns {sorted(missing)}; "
                         f"got {reader.fieldnames}")

    rows = list(reader)

    # Guard 3: the rows must obey the query we sent. USGS applies the bbox server-side, so a row
    # outside it proves the parameters were ignored, whatever the status code said.
    strays = []
    for r in rows:
        try:
            la, lo = float(r["latitude"]), float(r["longitude"])
        except (TypeError, ValueError):
            strays.append(f"unparseable coordinates in {r.get('id')}")
            continue
        if not (LAT_MIN <= la <= LAT_MAX and LON_MIN <= lo <= LON_MAX):
            strays.append(f"{r.get('id')} at {la},{lo} is outside the requested box")
        elif r["time"][:10] < start_iso:
            strays.append(f"{r.get('id')} at {r['time']} predates the requested start {start_iso}")
    if strays:
        raise ValueError(f"{len(strays)} of {len(rows)} rows contradict the query "
                         f"(first: {strays[0]}); refusing to trust this response")

    return rows


def refresh_bmkg():
    """Rewrite the BMKG snapshots. A failed endpoint keeps its previous file and is reported."""
    results = {}
    for name, url in BMKG_FILES.items():
        dest = os.path.join(RAW, name)
        try:
            status, body, ctype = http_get(url)
            parsed = json.loads(body.decode("utf-8"))
            if status != 200 or "Infogempa" not in parsed:
                raise ValueError(f"HTTP {status} or unexpected shape")
            with open(dest, "wb") as fh:
                fh.write(body)
            results[name] = {"ok": True, "bytes": len(body)}
        except Exception as exc:  # noqa: BLE001 - any failure is reported, never fatal
            results[name] = {"ok": False, "error": str(exc), "kept_previous": os.path.exists(dest)}
    return results


def fail(stage, message):
    json.dump({"ok": False, "stage": stage, "error": message},
              open(os.path.join(OUT, "append_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"{stage.upper()} FAILED: {message}", file=sys.stderr)
    return 1


def main():
    os.makedirs(OUT, exist_ok=True)
    if not os.path.exists(BASE_CSV):
        return fail("base", f"base CSV missing at {BASE_CSV}")

    base_rows = read_rows(BASE_CSV)
    append_rows = read_rows(APPEND_CSV)
    known = {r["id"] for r in base_rows} | {r["id"] for r in append_rows}

    # Guard 4: the append file must not already contain duplicates. Counting unique ids would
    # hide this, so count rows.
    if len({r["id"] for r in append_rows}) != len(append_rows):
        return fail("integrity", f"usgs_append.csv already holds duplicate ids "
                                 f"({len(append_rows)} rows, {len({r['id'] for r in append_rows})} unique)")

    anchor_iso = newest_time(append_rows) or newest_time(base_rows)
    if not anchor_iso:
        return fail("base", "could not determine the newest recorded event")
    anchor = datetime.strptime(anchor_iso, TIME_FMT).replace(tzinfo=timezone.utc)
    start = (anchor - timedelta(days=OVERLAP_DAYS)).strftime("%Y-%m-%d")

    try:
        fetched = fetch_usgs(start)
    except (ValueError, urllib.error.URLError, OSError) as exc:
        return fail("fetch", str(exc))

    foreign, duplicate, new_rows = Counter(), 0, []
    for row in fetched:
        region = (row.get("place") or "").split(",")[-1].strip()
        if region not in KEEP_REGIONS and region not in HISTORIC_EVENT_REGIONS:
            foreign[region] += 1
            continue
        if row["id"] in known:
            duplicate += 1
            continue
        new_rows.append(row)

    # Guard 5: a real query over the overlap window returns Indonesian events too. If every
    # single row was thrown out as foreign, the scope filter is probably not the thing that
    # decided, and the answer is not trustworthy.
    if fetched and not new_rows and not duplicate:
        return fail("plausibility",
                    f"all {len(fetched)} fetched rows were classified as outside scope; "
                    f"that pattern means the query was not applied as intended "
                    f"(sample regions: {list(foreign)[:5]})")

    new_rows.sort(key=lambda r: r["time"])
    header = header_of(BASE_CSV)

    malformed = [r.get("id") for r in new_rows if any(k not in r for k in header)]
    if malformed:
        return fail("integrity", f"malformed rows, refusing to write: {malformed[:5]}")

    if new_rows:
        write_header = not os.path.exists(APPEND_CSV)
        with open(APPEND_CSV, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        # Read back and count rows, not ids, so a duplicate cannot hide here.
        after = read_rows(APPEND_CSV)
        if len(after) != len(append_rows) + len(new_rows):
            return fail("integrity", f"expected {len(append_rows) + len(new_rows)} rows after "
                                     f"writing, found {len(after)}")
        if len({r["id"] for r in after}) != len(after):
            return fail("integrity", "duplicate id appeared while writing")

    bmkg = refresh_bmkg()
    latest = new_rows[-1]["time"] if new_rows else anchor_iso
    report = {
        "ok": all(v["ok"] for v in bmkg.values()),
        "ran_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queried_from": start,
        "fetched": len(fetched),
        "new_events": len(new_rows),
        "already_known": duplicate,
        "dropped_outside_scope": sum(foreign.values()),
        "dropped_breakdown": dict(sorted(foreign.items(), key=lambda kv: -kv[1])),
        "new_ids": [r["id"] for r in new_rows],
        "new_latest": latest,
        "bmkg": bmkg,
    }
    with open(os.path.join(OUT, "append_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"resume: {len(fetched)} diambil, {len(new_rows)} baru, "
          f"{duplicate} sudah ada, {sum(foreign.values())} di luar cakupan")
    for name, res in bmkg.items():
        print(f"  {name}: " + ("ok" if res["ok"] else f"GAGAL ({res['error']})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
