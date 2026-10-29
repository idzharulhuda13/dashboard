#!/usr/bin/env python3
"""Build gempa/data.json and generate gempa/index.html for Indonesia earthquake data story.

Deterministic generator: standard library only, frozen inputs, no wall clock,
no network, no randomness.
"""

import base64
from collections import Counter
import csv
import importlib.util
from datetime import date, datetime, timedelta
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_int(n: int) -> str:
    """Format integer with '.' thousands separator for values >= 1000."""
    return f"{n:,}".replace(",", ".")


def fmt_float(v: float, dp: int = 1) -> str:
    """Format float with ',' decimal separator, ASCII hyphen, 1 decimal place."""
    s = f"{float(v):.{dp}f}"
    if s in ("-0.0", "-0"):
        s = "0.0" if dp > 0 else "0"
    return s.replace(".", ",")


def largest_remainder_percentages(counts: list[int], total: int = None) -> list[float]:
    """Convert counts into percentages summing to 100.0 using largest-remainder method."""
    if total is None:
        total = sum(counts)
    if total == 0:
        return [0.0] * len(counts)
    scaled = [c * 1000.0 / total for c in counts]
    floors = [int(s) for s in scaled]
    remainders = [s - f for s, f in zip(scaled, floors)]
    unassigned = 1000 - sum(floors)
    order = sorted(range(len(counts)), key=lambda i: remainders[i], reverse=True)
    tenths = list(floors)
    for i in range(unassigned):
        tenths[order[i]] += 1
    return [round(t / 10.0, 1) for t in tenths]


def compute_sha256(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    csv_path = os.path.join(ROOT, "data", "out", "gempa_events.csv")
    live_path = os.path.join(ROOT, "data", "out", "bmkg_live.json")
    report_path = os.path.join(ROOT, "data", "out", "build_report.json")
    basemap_path = os.path.join(ROOT, "data", "raw", "basemap.json")
    template_path = os.path.join(ROOT, "gempa.template.html")
    fonts_dir = os.path.join(ROOT, "fonts")

    for p in [csv_path, live_path, report_path, basemap_path]:
        if not os.path.exists(p):
            raise RuntimeError(f"Missing input file: {p}")

    if not os.path.exists(template_path):
        raise RuntimeError(f"Missing template file: {template_path}")

    # Compute deterministic input hash of the 3 input files (12 hex characters)
    input_hasher = hashlib.sha256()
    for p in [csv_path, live_path, report_path]:
        with open(p, "rb") as f:
            input_hasher.update(f.read())
    input_sha = input_hasher.hexdigest()[:12]

    # 1. Read build_report.json
    with open(report_path, "r", encoding="utf-8") as f:
        build_report = json.load(f)

    # 2. Read bmkg_live.json
    with open(live_path, "r", encoding="utf-8") as f:
        bmkg_live = json.load(f)

    # 3. Read gempa_events.csv
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # 4. Read basemap.json
    with open(basemap_path, "r", encoding="utf-8") as f:
        basemap = json.load(f)

    # INVARIANTS
    #
    # This block used to freeze six exact counts (10516 rows, the magnitude bands, the depth
    # bands, the decade buckets, the dropped count, the top-5 magnitudes). That was right while
    # the dataset was a one-off snapshot, and it is wrong now: the dataset is append-only, so
    # appending a real earthquake is SUPPOSED to change those numbers, and a frozen count would
    # fail on the first legitimate append while telling us nothing about corruption.
    #
    # What replaces it: properties that must hold whatever the row count is. Corruption changes
    # relationship between the parts; growth changes only their size. verify.py is the second
    # layer, recomputing every published figure from the CSV independently.
    mag_counter = Counter(r["mag_band"] for r in rows)
    expected_bands = ["M5", "M6", "M7", "M8", "M9"]
    actual_mag_counts = {k: mag_counter.get(k, 0) for k in expected_bands}

    depth_counter = Counter(r["depth_band"] for r in rows)
    actual_depth_counts = {k: depth_counter.get(k, 0) for k in ["dangkal", "menengah", "dalam"]}

    decade_buckets = [1970, 1980, 1990, 2000, 2010, 2020]
    actual_decade_counts = [
        sum(1 for r in rows if dec <= int(r["year"]) < dec + 10) for dec in decade_buckets
    ]

    problems = []
    if not rows:
        problems.append("no rows at all")
    else:
        times = [r["time_utc"] for r in rows]
        if times != sorted(times):
            problems.append("rows are not sorted by time_utc")
        ids = [r["id"] for r in rows]
        if len(set(ids)) != len(ids):
            problems.append(f"{len(ids) - len(set(ids))} duplicate event ids")
        unknown_mag = set(mag_counter) - set(expected_bands)
        if unknown_mag:
            problems.append(f"unknown magnitude band labels: {sorted(unknown_mag)}")
        unknown_depth = set(depth_counter) - set(actual_depth_counts)
        if unknown_depth:
            problems.append(f"unknown depth band labels: {sorted(unknown_depth)}")

    total_now = len(rows)
    for label, counts in (("magnitude", actual_mag_counts), ("depth", actual_depth_counts)):
        if sum(counts.values()) != total_now:
            problems.append(f"{label} bands sum to {sum(counts.values())}, not {total_now}")
    if sum(actual_decade_counts) != total_now:
        problems.append(f"decades sum to {sum(actual_decade_counts)}, not {total_now}")

    if not build_report.get("accounting_ok"):
        problems.append("build report says kept + dropped != raw, so rows were lost or invented")

    # A partition that only ever grows should not lose the tail: the newest event must be the
    # one the report claims, so a truncated append shows up here rather than in the chart.
    if rows and build_report.get("last_event", "")[:19] != rows[-1]["time_utc"][:19]:
        problems.append("build report last_event disagrees with the newest row")

    if problems:
        raise RuntimeError("Source data failed its invariants: " + "; ".join(problems))

    # CALCULATIONS
    total_events = len(rows)

    # How many events the scope filter threw out. No longer frozen at 2173: every append can
    # add to it, so it is read from the report and published as it stands.
    dropped_val = build_report.get("dropped_outside_indonesia")

    # Distinct calendar years touched
    years_set = set(int(r["year"]) for r in rows)
    distinct_years = len(years_set)  # 51 (1976..2026)
    rate_year = round(total_events / distinct_years)  # 208

    first_iso = build_report["first_event"].replace("Z", "+00:00")
    last_iso = build_report["last_event"].replace("Z", "+00:00")
    t_first = datetime.fromisoformat(first_iso)
    t_last = datetime.fromisoformat(last_iso)

    # Months touched by the catalogue, counted from the data rather than typed. This used to be
    # the literal (2026 - 1976) * 12 + (10 - 1) + 1 = 610, which was correct for exactly one
    # month: the first append in a new month would have kept dividing by a stale denominator and
    # quietly published a slightly wrong rate per month.
    total_months = (t_last.year - t_first.year) * 12 + (t_last.month - t_first.month) + 1
    rate_month = round(total_events / total_months)

    # Rate per week
    span_days = (t_last - t_first).total_seconds() / 86400.0
    span_weeks = span_days / 7.0
    rate_week_val = total_events / span_weeks  # 4.0055...
    rate_week_raw = round(rate_week_val, 2)  # 4.01

    # Depth statistics
    depths = sorted(float(r["depth_km"]) for r in rows)
    depth_min = depths[0]
    depth_max = depths[-1]
    n_depths = len(depths)
    if n_depths % 2 == 1:
        depth_median = depths[n_depths // 2]
    else:
        depth_median = (depths[n_depths // 2 - 1] + depths[n_depths // 2]) / 2.0

    # Percentages computed using largest-remainder method to sum to exactly 100.0
    depth_counts_list = [
        actual_depth_counts["dangkal"],
        actual_depth_counts["menengah"],
        actual_depth_counts["dalam"],
    ]
    pct_dangkal, pct_menengah, pct_dalam = largest_remainder_percentages(
        depth_counts_list, total_events
    )

    # Deep focus (depth >= 300) events by province (top 5)
    deep_rows = [r for r in rows if float(r["depth_km"]) >= 300.0]
    deep_prov_counts = Counter(r["province"] for r in deep_rows).most_common(5)
    deep_prov = [{"name": name, "n": count, "n_d": fmt_int(count)} for name, count in deep_prov_counts]

    # Province event counts (top 12)
    prov_counts = Counter(r["province"] for r in rows).most_common(12)
    province_list = [{"name": name, "n": count, "n_d": fmt_int(count)} for name, count in prov_counts]
    top_prov_name = province_list[0]["name"]
    top_prov_n = province_list[0]["n"]

    # Decades array (6 entries)
    decades_list = []
    for dec in decade_buckets:
        n_dec = sum(1 for r in rows if dec <= int(r["year"]) < dec + 10)
        m7_dec = sum(1 for r in rows if dec <= int(r["year"]) < dec + 10 and float(r["mag"]) >= 7.0)
        decades_list.append({
            "label": f"{dec}an",
            "m7": m7_dec,
            "m7_d": fmt_int(m7_dec),
            "n": n_dec,
            "n_d": fmt_int(n_dec)
        })

    # Magnitude bands array (5 entries, M5..M9)
    mag_bands = ["M5", "M6", "M7", "M8", "M9"]
    mag_counts_list = [actual_mag_counts[b] for b in mag_bands]
    mag_pcts = largest_remainder_percentages(mag_counts_list, total_events)
    mag_bands_list = []
    for band, n_b, pct_b in zip(mag_bands, mag_counts_list, mag_pcts):
        mag_bands_list.append({
            "band": band,
            "n": n_b,
            "n_d": fmt_int(n_b),
            "pct": pct_b,
            "pct_d": fmt_float(pct_b, 1)
        })

    # Depth bands array (3 entries)
    depth_bands_list = [
        {
            "band": "dangkal",
            "n": actual_depth_counts["dangkal"],
            "n_d": fmt_int(actual_depth_counts["dangkal"]),
            "pct": pct_dangkal,
            "pct_d": fmt_float(pct_dangkal, 1),
            "range": "kurang dari 70 km"
        },
        {
            "band": "menengah",
            "n": actual_depth_counts["menengah"],
            "n_d": fmt_int(actual_depth_counts["menengah"]),
            "pct": pct_menengah,
            "pct_d": fmt_float(pct_menengah, 1),
            "range": "70 sampai kurang dari 300 km"
        },
        {
            "band": "dalam",
            "n": actual_depth_counts["dalam"],
            "n_d": fmt_int(actual_depth_counts["dalam"]),
            "pct": pct_dalam,
            "pct_d": fmt_float(pct_dalam, 1),
            "range": "300 km atau lebih"
        }
    ]

    # Top 5 largest events
    # Ties are broken by the later event, so the ordering is stable and the table does not
    # reshuffle between builds when two events share a magnitude.
    top5_rows = sorted(rows, key=lambda r: (float(r["mag"]), r["time_utc"]), reverse=True)[:5]
    top5_list = []
    for r in top5_rows:
        m = float(r["mag"])
        d = float(r["depth_km"])
        top5_list.append({
            "date_wib": r["date_wib"],
            "depth_d": fmt_float(d, 1),
            "depth_km": d,
            "id": r["id"],
            "mag": m,
            "mag_d": fmt_float(m, 1),
            "place": r["place"]
        })

    # Epicentre field SVG paths and event buffers per magnitude band
    # ONE loop over CSV rows that, per row, appends the dot to band_paths[mag_band] AND
    # appends four event values to per-band buffers: mag tenths round(mag*10),
    # int round(depth_km), day = days from 1970-01-01 of date_wib, province index into sorted distinct provinces.
    epoch = date(1970, 1, 1)
    sorted_provinces = sorted(set(r["province"] for r in rows))
    prov_to_idx = {p: i for i, p in enumerate(sorted_provinces)}

    band_order = ["M5", "M6", "M7", "M8", "M9"]
    band_paths = {b: [] for b in band_order}
    band_mag = {b: [] for b in band_order}
    band_depth = {b: [] for b in band_order}
    band_day = {b: [] for b in band_order}
    band_prov = {b: [] for b in band_order}

    for r in rows:
        band = r["mag_band"]
        lon = float(r["lon"])
        neg_lat = -float(r["lat"])
        if abs(neg_lat) < 0.05:
            neg_lat = 0.0
        if abs(lon) < 0.05:
            lon = 0.0
        band_paths[band].append(f"M{lon:.1f} {neg_lat:.1f}h1")

        band_mag[band].append(str(round(float(r["mag"]) * 10)))
        band_depth[band].append(str(round(float(r["depth_km"]))))
        band_day[band].append(str((date.fromisoformat(r["date_wib"]) - epoch).days))
        band_prov[band].append(str(prov_to_idx[r["province"]]))

    events_data = {
        "day": ",".join(v for b in band_order for v in band_day[b]),
        "depth": ",".join(v for b in band_order for v in band_depth[b]),
        "mag": ",".join(v for b in band_order for v in band_mag[b]),
        "n": total_events,
        "prov": ",".join(v for b in band_order for v in band_prov[b]),
    }

    field_basemap = {
        "land": basemap["land"],
        "plates": basemap["plates"],
        "labels": basemap["labels"],
        "scale": {
            "span_deg": 3,
            "km": round(3 * 111.195),
            "label": "334 km",
        },
    }

    field_data = {
        "basemap": field_basemap,
        "counts": actual_mag_counts,
        "order": "M5,M6,M7,M8,M9:csv-asc",
        "paths": {b: "".join(band_paths[b]) for b in band_order},
        "provinces": sorted_provinces,
        "viewbox": [94.5, -6.5, 47, 18],
    }

    # Time span WIB
    first_row_wib = rows[0]["time_wib"]
    last_row_wib = rows[-1]["time_wib"]
    span_first_wib_str = f"{first_row_wib} WIB"
    span_last_wib_str = f"{last_row_wib} WIB"

    # M7+ total count
    mag7plus_total = actual_mag_counts["M7"] + actual_mag_counts["M8"] + actual_mag_counts["M9"]

    # 23 fig keys
    fig_data = {
        "built_from": {"d": input_sha, "r": input_sha},
        "deep_total": {"d": fmt_int(actual_depth_counts["dalam"]), "r": actual_depth_counts["dalam"]},
        "depth_max": {"d": fmt_float(depth_max, 1), "r": depth_max},
        "depth_median": {"d": fmt_float(depth_median, 1), "r": depth_median},
        "depth_min": {"d": fmt_float(depth_min, 1), "r": depth_min},
        "dropped_total": {"d": fmt_int(dropped_val), "r": dropped_val},
        "live_felt_n": {"d": fmt_int(len(bmkg_live["felt"])), "r": len(bmkg_live["felt"])},
        "live_latest_n": {"d": fmt_int(len(bmkg_live["latest"])), "r": len(bmkg_live["latest"])},
        "mag7plus": {"d": fmt_int(mag7plus_total), "r": mag7plus_total},
        "pct_dalam": {"d": fmt_float(pct_dalam, 1), "r": pct_dalam},
        "pct_dangkal": {"d": fmt_float(pct_dangkal, 1), "r": pct_dangkal},
        "pct_menengah": {"d": fmt_float(pct_menengah, 1), "r": pct_menengah},
        "province_count": {"d": fmt_int(build_report.get("provinces", 38)), "r": build_report.get("provinces", 38)},
        "rate_month": {"d": str(rate_month), "r": rate_month},
        "rate_week": {"d": fmt_float(rate_week_val, 1), "r": rate_week_raw},
        "rate_year": {"d": str(rate_year), "r": rate_year},
        "raw_events": {"d": fmt_int(build_report["raw_events"]), "r": build_report["raw_events"]},
        "span_first_wib": {"d": span_first_wib_str, "r": first_row_wib},
        "span_last_wib": {"d": span_last_wib_str, "r": last_row_wib},
        "span_years": {"d": str(distinct_years), "r": distinct_years},
        "top_prov_n": {"d": fmt_int(top_prov_n), "r": top_prov_n},
        "top_prov_name": {"d": top_prov_name, "r": top_prov_name},
        "total": {"d": fmt_int(total_events), "r": total_events}
    }

    # Live section
    live_data = {
        "built_input_sha": input_sha,
        "felt": bmkg_live["felt"],
        "latest": bmkg_live["latest"]
    }

    # Method section
    raw_dropped = build_report.get("dropped_breakdown")
    if not raw_dropped:
        raise RuntimeError("build_report.json is missing or dropped_breakdown is empty")

    sorted_dropped = sorted(raw_dropped.items(), key=lambda kv: kv[1], reverse=True)
    if len(sorted_dropped) > 6:
        dropped_breakdown_list = [[name, count] for name, count in sorted_dropped[:6]]
        remainder_sum = sum(count for _, count in sorted_dropped[6:])
        dropped_breakdown_list.append(["lainnya", remainder_sum])
    else:
        dropped_breakdown_list = [[name, count] for name, count in sorted_dropped]

    method_data = {
        "dropped_breakdown": dropped_breakdown_list,
        "files": [
            "usgs_idn_m5plus_1976_2026.csv",
            "provinces_bapanas.json",
            "bmkg_gempaterkini.json"
        ],
        "input_sha": input_sha,
        "province_note": (
            "Daftar dan sentroid 38 provinsi bersumber dari berkas referensi Badan Pangan Nasional (Bapanas) "
            "yang diunduh pada 12 Oktober 2026. Digunakan semata untuk agregasi jarak terdekat (haversine) "
            "episentrum gempa, bukan sebagai acuan batas administratif resmi wilayah."
        ),
        "scope_note": (
            f"Data mencakup {fmt_int(total_events)} gempa bumi M5+ di Indonesia (1976-2026, {distinct_years} tahun kalender). "
            f"rate_year = round({total_events} / {distinct_years}) = {rate_year}/tahun; "
            f"rate_month = round({total_events} / {total_months}) = {rate_month}/bulan (rentang 1976-01 hingga 2026-10 inklusif); "
            f"rate_week = {total_events} / ((last_event - first_event) dalam hari / 7) = {fmt_float(rate_week_val, 1)}/minggu. "
            "Persentase kedalaman dialokasikan menggunakan metode sisa terbesar agar tepat berjumlah 100,0%."
        )
    }

    # Complete data payload
    data_payload = {
        "decades": decades_list,
        "deep_prov": deep_prov,
        "depth_bands": depth_bands_list,
        "events": events_data,
        "field": field_data,
        "fig": fig_data,
        "live": live_data,
        "mag_bands": mag_bands_list,
        "method": method_data,
        "province": province_list,
        "top5": top5_list
    }

    # Compact JSON output (sort_keys=True, separators=(',', ':'))
    compact_json = json.dumps(data_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    data_json_path = os.path.join(ROOT, "gempa/data.json")
    with open(data_json_path, "w", encoding="utf-8") as f:
        f.write(compact_json)

    # Encode fonts as data URIs and build @font-face rules
    # Map them as:
    # - Archivo-400.woff2        -> family "Archivo", weight 400, format woff2
    # - ArchivoBlack.ttf         -> family "Archivo Black", weight 400, format truetype
    # - IBMPlexMono-400.woff2    -> family "IBM Plex Mono", weight 400, format woff2
    # - IBMPlexMono-500.woff2    -> family "IBM Plex Mono", weight 500, format woff2
    font_configs = [
        ("ChakraPetch-400.woff2", "Chakra Petch", "400", "woff2", "font/woff2"),
        ("ChakraPetch-600.woff2", "Chakra Petch", "600", "woff2", "font/woff2"),
        ("ChakraPetch-700.woff2", "Chakra Petch", "700", "woff2", "font/woff2"),
        ("JetBrainsMono-400.woff2", "JetBrains Mono", "400", "woff2", "font/woff2"),
        ("JetBrainsMono-500.woff2", "JetBrains Mono", "500", "woff2", "font/woff2"),
    ]

    font_rules = []
    for fname, family, weight, fmt, mime in font_configs:
        fpath = os.path.join(fonts_dir, fname)
        if not os.path.exists(fpath):
            raise RuntimeError(f"Required font file not found: {fpath}")
        with open(fpath, "rb") as ff:
            b64_data = base64.b64encode(ff.read()).decode("ascii")
        rule = (
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f"  font-display: swap;\n"
            f"  src: url('data:{mime};base64,{b64_data}') format('{fmt}');\n"
            f"}}"
        )
        font_rules.append(rule)

    fonts_css_block = "\n".join(font_rules)

    # Read template bytes to compute sha256 stamp
    with open(template_path, "rb") as tf:
        template_bytes = tf.read()
    template_sha256 = hashlib.sha256(template_bytes).hexdigest()
    template_str = template_bytes.decode("utf-8")

    # Validate template SEO marker pair
    tmpl_begin_count = template_str.count("<!--SEO:BEGIN-->")
    tmpl_end_count = template_str.count("<!--SEO:END-->")
    if tmpl_begin_count != 1 or tmpl_end_count != 1:
        raise RuntimeError(
            f"gempa.template.html must contain exactly one SEO marker pair (found {tmpl_begin_count} BEGIN, {tmpl_end_count} END)"
        )
    if template_str.index("<!--SEO:BEGIN-->") >= template_str.index("<!--SEO:END-->"):
        raise RuntimeError("In gempa.template.html, <!--SEO:BEGIN--> must come before <!--SEO:END-->")

    # Extract theme color from :root { --ground: ... }
    ground_match = re.search(r":root\s*\{[^}]*?--ground:\s*([^;]+);", template_str)
    if not ground_match:
        raise RuntimeError("Could not parse :root { --ground: ... } from gempa.template.html")
    theme_color = ground_match.group(1).strip()

    site_url = "https://dashboard.idzharulhuda.com"
    page_slug = "gempa"
    canonical_url = f"{site_url}/{page_slug}/"

    first_year = fig_data["span_first_wib"]["r"][:4]
    last_year = fig_data["span_last_wib"]["r"][:4]
    total_val = fig_data["total"]["d"]
    rate_year_val = fig_data["rate_year"]["d"]
    span_first_date = fig_data["span_first_wib"]["r"][:10]
    span_last_date = fig_data["span_last_wib"]["r"][:10]

    title = f"Setengah Abad Gempa Indonesia {first_year}-{last_year}"
    desc = (
        f"{total_val} gempa M5+ di Indonesia sejak {first_year}, "
        f"rata-rata {rate_year_val} per tahun: episentrum, kedalaman, magnitudo, "
        f"dan provinsi paling sering diguncang (USGS)."
    )
    ogalt = (
        f"Kartu pratinjau pelat gempa: judul SETENGAH ABAD, {total_val} GEMPA M5+, "
        f"dengan ringkasan laju tahunan dan median kedalaman."
    )

    # Gates assertions
    if len(title) > 60:
        raise RuntimeError(f"Title exceeds 60 chars ({len(title)}): '{title}'")
    if len(desc) > 155:
        raise RuntimeError(f"Description exceeds 155 chars ({len(desc)}): '{desc}'")
    for text, label in [(title, "title"), (desc, "desc"), (ogalt, "ogalt")]:
        if "\u2014" in text:
            raise RuntimeError(f"U+2014 em dash found in {label}")

    title_nums = re.findall(r"\b\d+(?:[\.,]\d+)*\b", title)
    for num in title_nums:
        if num not in (first_year, last_year):
            raise RuntimeError(f"Unexpected numeric token in title: {num}")

    desc_nums = re.findall(r"\b\d+(?:[\.,]\d+)*\b", desc)
    expected_desc_nums = {total_val, "5", first_year, rate_year_val}
    for num in desc_nums:
        if num not in expected_desc_nums:
            raise RuntimeError(f"Unexpected numeric token in desc: {num}")

    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Dataset",
                "name": title,
                "description": desc,
                "url": canonical_url,
                "inLanguage": "id",
                "keywords": ["gempa bumi", "Indonesia", "USGS"],
                "creator": {
                    "@type": "Person",
                    "name": "Idzharul Huda",
                    "url": "https://idzharulhuda.com/",
                },
                "license": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
                "spatialCoverage": {
                    "@type": "Place",
                    "name": "Indonesia",
                },
                "temporalCoverage": f"{span_first_date}/{span_last_date}",
                "isBasedOn": {
                    "@type": "Dataset",
                    "name": "ANSS Comprehensive Earthquake Catalog (ComCat)",
                    "url": "https://earthquake.usgs.gov/data/catalog/",
                },
                "citation": "U.S. Geological Survey, ANSS Comprehensive Earthquake Catalog (ComCat), katalog gempa bumi M5+ Indonesia yang diunduh pipeline ini.",
            },
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Dashboard Data Publik",
                        "item": f"{site_url}/",
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": title,
                        "item": canonical_url,
                    },
                ],
            },
        ],
    }
    jsonld_str = json.dumps(graph, ensure_ascii=True, separators=(",", ":"))

    def escape_attr(val: str) -> str:
        return str(val).replace("&", "&amp;").replace('"', "&quot;")

    esc_title = escape_attr(title)
    esc_desc = escape_attr(desc)
    esc_canonical = escape_attr(canonical_url)
    esc_theme = escape_attr(theme_color)
    esc_site = escape_attr(site_url)
    esc_slug = escape_attr(page_slug)
    esc_ogalt = escape_attr(ogalt)

    seo_inner_lines = [
        f"  <title>{esc_title}</title>",
        f"  <meta name=\"description\" content=\"{esc_desc}\">",
        f"  <link rel=\"canonical\" href=\"{esc_canonical}\">",
        f"  <meta name=\"theme-color\" content=\"{esc_theme}\">",
        '  <meta property="og:type" content="website">',
        f"  <meta property=\"og:url\" content=\"{esc_canonical}\">",
        f"  <meta property=\"og:title\" content=\"{esc_title}\">",
        f"  <meta property=\"og:description\" content=\"{esc_desc}\">",
        f"  <meta property=\"og:image\" content=\"{esc_site}/assets/og/{esc_slug}.png\">",
        '  <meta property="og:image:width" content="1200">',
        '  <meta property="og:image:height" content="630">',
        f"  <meta property=\"og:image:alt\" content=\"{esc_ogalt}\">",
        '  <meta property="og:site_name" content="Dashboard Data Publik">',
        '  <meta property="og:locale" content="id_ID">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f"  <meta name=\"twitter:title\" content=\"{esc_title}\">",
        f"  <meta name=\"twitter:description\" content=\"{esc_desc}\">",
        f"  <meta name=\"twitter:image\" content=\"{esc_site}/assets/og/{esc_slug}.png\">",
        '  <link rel="icon" href="/favicon.ico" sizes="32x32">',
        '  <link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        '  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">',
        '  <link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        f"  <script type=\"application/ld+json\">{jsonld_str}</script>",
    ]
    seo_inner_block = "\n".join(seo_inner_lines)

    if "\u2014" in seo_inner_block or "\u2014" in jsonld_str:
        raise RuntimeError("U+2014 em dash found in generated SEO block")

    if "/*__FONTS__*/" not in template_str:
        raise RuntimeError("Placeholder /*__FONTS__*/ not found in gempa.template.html")

    html_rendered = template_str.replace("/*__FONTS__*/", fonts_css_block)

    seo_pattern = re.compile(r"(<!--SEO:BEGIN-->)(.*?)(<!--SEO:END-->)", re.DOTALL)
    html_rendered = seo_pattern.sub(lambda m: f"{m.group(1)}\n{seo_inner_block}\n  {m.group(3)}", html_rendered)

    out_begin_count = html_rendered.count("<!--SEO:BEGIN-->")
    out_end_count = html_rendered.count("<!--SEO:END-->")
    if out_begin_count != 1 or out_end_count != 1:
        raise RuntimeError(
            f"Emitted page must contain exactly one SEO marker pair (found {out_begin_count} BEGIN, {out_end_count} END)"
        )
    if html_rendered.index("<!--SEO:BEGIN-->") >= html_rendered.index("<!--SEO:END-->"):
        raise RuntimeError("In emitted page, <!--SEO:BEGIN--> must come before <!--SEO:END-->")

    # Replace DATA placeholder so line becomes const DATA = <gempa/data.json content>;
    if "/*__DATA__*/ {}" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/ {}", compact_json)
    elif "/*__DATA__*/" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/", compact_json)
    else:
        raise RuntimeError("Placeholder /*__DATA__*/ not found in gempa.template.html")

    # Fill every <span ... data-fig="k"> that is still empty with payload's formatted value fig[k]["d"]
    def fill_data_fig(match: re.Match) -> str:
        open_tag = match.group(1)
        key = match.group(2)
        inner = match.group(3)
        if key not in fig_data or "d" not in fig_data[key]:
            raise RuntimeError(f"data-fig key '{key}' is not in payload fig data")
        if not inner.strip():
            val = str(fig_data[key]["d"])
            if not val:
                raise RuntimeError(f"data-fig key '{key}' produced empty formatted value")
            return f"{open_tag}{val}</span>"
        return match.group(0)

    span_fig_re = re.compile(
        r'(<span\b[^>]*?\bdata-fig=["\']([^"\']+)["\'][^>]*?>)(.*?)</span>',
        re.DOTALL
    )
    html_rendered = span_fig_re.sub(fill_data_fig, html_rendered)

    # Validate that every data-fig span is non-empty and names a key in payload
    all_spans = list(span_fig_re.finditer(html_rendered))
    for m in all_spans:
        k = m.group(2)
        content = m.group(3)
        if k not in fig_data or "d" not in fig_data[k]:
            raise RuntimeError(f"data-fig key '{k}' is not in payload fig data")
        if not content.strip():
            raise RuntimeError(f"data-fig span with key '{k}' remained empty")

    all_fig_tags = re.findall(r'<([a-zA-Z0-9]+)\b[^>]*\bdata-fig=["\']([^"\']+)["\'][^>]*>', html_rendered)
    for tag_name, k in all_fig_tags:
        if tag_name.lower() != "span":
            raise RuntimeError(f"Found data-fig on non-span tag <{tag_name}> with key '{k}'")
        if k not in fig_data or "d" not in fig_data[k]:
            raise RuntimeError(f"data-fig key '{k}' is not in payload fig data")

    # Prepend BUILD stamp on line 1
    # Strip "Data per <tanggal>" ditulis ulang dari tanggal gempa terakhir (WIB), bukan dibiarkan
    # seperti di template: template statis membuat halaman mengaku "data per 20 Oktober" selamanya
    # padahal harvest menambah gempa tiap hari.
    spec = importlib.util.spec_from_file_location(
        "pasar_dateline", os.path.join(ROOT, "pasar", "dateline.py"))
    assert spec and spec.loader, "pasar/dateline.py tidak bisa dimuat"
    dateline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dateline)

    as_of_wib = (t_last + timedelta(hours=7)).date().isoformat()
    html_rendered = dateline.stamp(html_rendered, as_of_wib)

    stamp_line = f"<!--BUILD sha256:{template_sha256}-->\n"
    final_html = stamp_line + html_rendered

    index_html_path = os.path.join(ROOT, "gempa/index.html")
    with open(index_html_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_html)


if __name__ == "__main__":
    main()
