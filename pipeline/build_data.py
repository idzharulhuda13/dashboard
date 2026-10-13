#!/usr/bin/env python3
"""Build data.json and generate index.html for Indonesia earthquake data story.

Deterministic generator: standard library only, frozen inputs, no wall clock,
no network, no randomness.
"""

import base64
from collections import Counter
import csv
from datetime import datetime
import hashlib
import json
import os
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
    template_path = os.path.join(ROOT, "story.template.html")
    fonts_dir = os.path.join(ROOT, "fonts")

    for p in [csv_path, live_path, report_path]:
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

    # ASSERTIONS
    # Assertion 1: len(rows) == 10516
    if len(rows) != 10516:
        raise RuntimeError(f"Assertion failed: expected 10516 rows, got {len(rows)}")

    # Assertion 2: magnitude band counts == {"M5":9774,"M6":662,"M7":75,"M8":4,"M9":1}
    mag_counter = Counter(r["mag_band"] for r in rows)
    expected_mag_counts = {"M5": 9774, "M6": 662, "M7": 75, "M8": 4, "M9": 1}
    actual_mag_counts = {k: mag_counter.get(k, 0) for k in ["M5", "M6", "M7", "M8", "M9"]}
    if actual_mag_counts != expected_mag_counts:
        raise RuntimeError(
            f"Assertion failed: magnitude band counts mismatch: expected {expected_mag_counts}, got {actual_mag_counts}"
        )

    # Assertion 3: depth band counts == {"dangkal":7852,"menengah":2384,"dalam":280}
    depth_counter = Counter(r["depth_band"] for r in rows)
    expected_depth_counts = {"dangkal": 7852, "menengah": 2384, "dalam": 280}
    actual_depth_counts = {k: depth_counter.get(k, 0) for k in ["dangkal", "menengah", "dalam"]}
    if actual_depth_counts != expected_depth_counts:
        raise RuntimeError(
            f"Assertion failed: depth band counts mismatch: expected {expected_depth_counts}, got {actual_depth_counts}"
        )

    # Assertion 4: decade counts for the six decades 1970s..2020s == [924,1989,2064,2538,1833,1168]
    decade_buckets = [1970, 1980, 1990, 2000, 2010, 2020]
    actual_decade_counts = [
        sum(1 for r in rows if dec <= int(r["year"]) < dec + 10) for dec in decade_buckets
    ]
    expected_decade_counts = [924, 1989, 2064, 2538, 1833, 1168]
    if actual_decade_counts != expected_decade_counts:
        raise RuntimeError(
            f"Assertion failed: decade counts mismatch: expected {expected_decade_counts}, got {actual_decade_counts}"
        )

    # Assertion 5: build_report["dropped_outside_indonesia"] == 2173
    dropped_val = build_report.get("dropped_outside_indonesia")
    if dropped_val != 2173:
        raise RuntimeError(
            f"Assertion failed: build_report['dropped_outside_indonesia'] expected 2173, got {dropped_val}"
        )

    # Assertion 6: the five largest events have magnitudes [9.1,8.6,8.4,8.3,8.1]
    sorted_by_mag = sorted(rows, key=lambda r: (float(r["mag"]), r["time_utc"]), reverse=True)
    top5_rows = sorted_by_mag[:5]
    top5_mags = [float(r["mag"]) for r in top5_rows]
    expected_top5_mags = [9.1, 8.6, 8.4, 8.3, 8.1]
    if top5_mags != expected_top5_mags:
        raise RuntimeError(
            f"Assertion failed: top 5 magnitudes mismatch: expected {expected_top5_mags}, got {top5_mags}"
        )

    # CALCULATIONS
    total_events = len(rows)

    # Distinct calendar years touched
    years_set = set(int(r["year"]) for r in rows)
    distinct_years = len(years_set)  # 51 (1976..2026)
    rate_year = round(total_events / distinct_years)  # 208

    # Months between 1976-01 and 2026-10 inclusive = 50 * 12 + 10 = 610
    total_months = (2026 - 1976) * 12 + (10 - 1) + 1  # 610
    rate_month = round(total_events / total_months)  # 17

    # Rate per week
    first_iso = build_report["first_event"].replace("Z", "+00:00")
    last_iso = build_report["last_event"].replace("Z", "+00:00")
    t_first = datetime.fromisoformat(first_iso)
    t_last = datetime.fromisoformat(last_iso)
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

    # Epicentre field SVG paths per magnitude band
    # Each run: M{lon rounded to 1 dp} {negated lat rounded to 1 dp}h1
    band_paths = {"M5": [], "M6": [], "M7": [], "M8": [], "M9": []}
    for r in rows:
        band = r["mag_band"]
        lon = float(r["lon"])
        neg_lat = -float(r["lat"])
        if abs(neg_lat) < 0.05:
            neg_lat = 0.0
        if abs(lon) < 0.05:
            lon = 0.0
        band_paths[band].append(f"M{lon:.1f} {neg_lat:.1f}h1")

    field_data = {
        "counts": actual_mag_counts,
        "paths": {b: "".join(band_paths[b]) for b in ["M5", "M6", "M7", "M8", "M9"]},
        "viewbox": [94.5, -6.5, 47, 18]
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

    data_json_path = os.path.join(ROOT, "data.json")
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

    if "/*__FONTS__*/" not in template_str:
        raise RuntimeError("Placeholder /*__FONTS__*/ not found in story.template.html")

    html_rendered = template_str.replace("/*__FONTS__*/", fonts_css_block)

    # Replace DATA placeholder so line becomes const DATA = <data.json content>;
    if "/*__DATA__*/ {}" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/ {}", compact_json)
    elif "/*__DATA__*/" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/", compact_json)
    else:
        raise RuntimeError("Placeholder /*__DATA__*/ not found in story.template.html")

    # Prepend BUILD stamp on line 1
    stamp_line = f"<!--BUILD sha256:{template_sha256}-->\n"
    final_html = stamp_line + html_rendered

    index_html_path = os.path.join(ROOT, "index.html")
    with open(index_html_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_html)


if __name__ == "__main__":
    main()
