#!/usr/bin/env python3
"""Acceptance gate for Indonesia earthquake data story.

Recomputes numbers and structural requirements from frozen source files,
independently of the generator.
"""

from collections import Counter
import csv
from datetime import date, datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def fmt_decimal(v: float, dp: int = 1) -> str:
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


def check_1_fig(rows, build_report, data_json):
    total = len(rows)
    years = set(int(r["year"]) for r in rows)
    distinct_years = len(years)
    rate_year = round(total / distinct_years)
    total_months = (2026 - 1976) * 12 + (10 - 1) + 1
    rate_month = round(total / total_months)

    t_first = datetime.fromisoformat(rows[0]["time_utc"].replace("Z", "+00:00"))
    t_last = datetime.fromisoformat(rows[-1]["time_utc"].replace("Z", "+00:00"))
    span_days = (t_last - t_first).total_seconds() / 86400.0
    rate_week_val = total / (span_days / 7.0)
    rate_week_raw = round(rate_week_val, 2)
    rate_week_d = fmt_decimal(rate_week_val, 1)

    depths = sorted(float(r["depth_km"]) for r in rows)
    n_depths = len(depths)
    if n_depths % 2 == 1:
        depth_median = depths[n_depths // 2]
    else:
        depth_median = (depths[n_depths // 2 - 1] + depths[n_depths // 2]) / 2.0
    depth_max = depths[-1]
    depth_min = depths[0]

    n_dangkal = sum(1 for r in rows if r["depth_band"] == "dangkal")
    n_menengah = sum(1 for r in rows if r["depth_band"] == "menengah")
    n_dalam = sum(1 for r in rows if r["depth_band"] == "dalam")
    pct_dangkal, pct_menengah, pct_dalam = largest_remainder_percentages(
        [n_dangkal, n_menengah, n_dalam], total
    )
    mag7plus = sum(1 for r in rows if float(r["mag"]) >= 7.0)

    raw_events = build_report["raw_events"]
    dropped_total = build_report["dropped_outside_indonesia"]
    province_count = build_report.get("provinces", 38)

    expected = {
        "total": (total, fmt_thousands(total)),
        "rate_year": (rate_year, str(rate_year)),
        "rate_month": (rate_month, str(rate_month)),
        "rate_week": (rate_week_raw, rate_week_d),
        "span_first_wib": (rows[0]["time_wib"], f"{rows[0]['time_wib']} WIB"),
        "span_last_wib": (rows[-1]["time_wib"], f"{rows[-1]['time_wib']} WIB"),
        "span_years": (distinct_years, str(distinct_years)),
        "pct_dangkal": (pct_dangkal, fmt_decimal(pct_dangkal, 1)),
        "pct_menengah": (pct_menengah, fmt_decimal(pct_menengah, 1)),
        "pct_dalam": (pct_dalam, fmt_decimal(pct_dalam, 1)),
        "depth_median": (depth_median, fmt_decimal(depth_median, 1)),
        "depth_max": (depth_max, fmt_decimal(depth_max, 1)),
        "depth_min": (depth_min, fmt_decimal(depth_min, 1)),
        "deep_total": (n_dalam, fmt_thousands(n_dalam)),
        "mag7plus": (mag7plus, fmt_thousands(mag7plus)),
        "raw_events": (raw_events, fmt_thousands(raw_events)),
        "dropped_total": (dropped_total, fmt_thousands(dropped_total)),
        "province_count": (province_count, fmt_thousands(province_count)),
    }

    fig = data_json.get("fig", {})
    fig_depth_keys = ["pct_dangkal", "pct_menengah", "pct_dalam"]
    if all(k in fig and "d" in fig[k] for k in fig_depth_keys):
        fig_depth_d_sum = round(
            sum(float(fig[k]["d"].replace(",", ".")) for k in fig_depth_keys), 4
        )
        if fig_depth_d_sum != 100.0:
            return (
                False,
                f"fig displayed depth percentage sum mismatch (actual: {fmt_decimal(fig_depth_d_sum, 1)}, expected: 100,0)",
            )

    for key, (exp_r, exp_d) in expected.items():
        if key not in fig:
            return False, f"missing fig key '{key}' (actual: missing, expected: present)"
        entry = fig[key]
        act_r = entry.get("r")
        act_d = entry.get("d")
        if act_r != exp_r:
            return False, f"fig.{key}.r mismatch (actual: {act_r}, expected: {exp_r})"
        if act_d != exp_d:
            return False, f"fig.{key}.d mismatch (actual: {act_d!r}, expected: {exp_d!r})"

    return True, "gempa/data.json 'fig' values match recomputed source data"


def check_2_arrays(rows, data_json):
    total = len(rows)

    mag_counts = Counter(r["mag_band"] for r in rows)
    mag_bands = ["M5", "M6", "M7", "M8", "M9"]
    mag_counts_list = [mag_counts.get(b, 0) for b in mag_bands]
    mag_pcts = largest_remainder_percentages(mag_counts_list, total)
    exp_mag_bands = []
    for b, n, pct in zip(mag_bands, mag_counts_list, mag_pcts):
        exp_mag_bands.append({
            "band": b,
            "n": n,
            "n_d": fmt_thousands(n),
            "pct": pct,
            "pct_d": fmt_decimal(pct, 1),
        })
    act_mag = data_json.get("mag_bands", [])
    if len(act_mag) != 5:
        return False, f"mag_bands count mismatch (actual: {len(act_mag)}, expected: 5)"
    act_mag_sum = round(
        sum(float(b.get("pct_d", "0").replace(",", ".")) for b in act_mag), 4
    )
    if act_mag_sum != 100.0:
        return (
            False,
            f"mag_bands displayed percentage sum mismatch (actual: {fmt_decimal(act_mag_sum, 1)}, expected: 100,0)",
        )
    if act_mag != exp_mag_bands:
        return False, f"mag_bands values mismatch (actual: {act_mag}, expected: {exp_mag_bands})"

    depth_counts = Counter(r["depth_band"] for r in rows)
    ranges = {
        "dangkal": "kurang dari 70 km",
        "menengah": "70 sampai kurang dari 300 km",
        "dalam": "300 km atau lebih",
    }
    depth_bands = ["dangkal", "menengah", "dalam"]
    depth_counts_list = [depth_counts.get(b, 0) for b in depth_bands]
    depth_pcts = largest_remainder_percentages(depth_counts_list, total)
    exp_depth_bands = []
    for b, n, pct in zip(depth_bands, depth_counts_list, depth_pcts):
        exp_depth_bands.append({
            "band": b,
            "n": n,
            "n_d": fmt_thousands(n),
            "pct": pct,
            "pct_d": fmt_decimal(pct, 1),
            "range": ranges[b],
        })
    act_depth = data_json.get("depth_bands", [])
    if len(act_depth) != 3:
        return False, f"depth_bands count mismatch (actual: {len(act_depth)}, expected: 3)"
    act_depth_sum = round(
        sum(float(b.get("pct_d", "0").replace(",", ".")) for b in act_depth), 4
    )
    if act_depth_sum != 100.0:
        return (
            False,
            f"depth_bands displayed percentage sum mismatch (actual: {fmt_decimal(act_depth_sum, 1)}, expected: 100,0)",
        )
    if act_depth != exp_depth_bands:
        return False, f"depth_bands values mismatch (actual: {act_depth}, expected: {exp_depth_bands})"

    exp_decades = []
    for dec in [1970, 1980, 1990, 2000, 2010, 2020]:
        n = sum(1 for r in rows if dec <= int(r["year"]) < dec + 10)
        m7 = sum(1 for r in rows if dec <= int(r["year"]) < dec + 10 and float(r["mag"]) >= 7.0)
        exp_decades.append({
            "label": f"{dec}an",
            "m7": m7,
            "m7_d": fmt_thousands(m7),
            "n": n,
            "n_d": fmt_thousands(n),
        })
    act_decades = data_json.get("decades", [])
    if len(act_decades) != 6:
        return False, f"decades count mismatch (actual: {len(act_decades)}, expected: 6)"
    if act_decades != exp_decades:
        return False, f"decades values mismatch (actual: {act_decades}, expected: {exp_decades})"

    prov_counts = Counter(r["province"] for r in rows).most_common(12)
    exp_province = [{"name": name, "n": count, "n_d": fmt_thousands(count)} for name, count in prov_counts]
    act_prov = data_json.get("province", [])
    if len(act_prov) != 12:
        return False, f"province count mismatch (actual: {len(act_prov)}, expected: 12)"
    if act_prov != exp_province:
        return False, f"province values mismatch (actual: {act_prov}, expected: {exp_province})"

    deep_rows = [r for r in rows if float(r["depth_km"]) >= 300.0]
    deep_prov_counts = Counter(r["province"] for r in deep_rows).most_common(5)
    exp_deep_prov = [{"name": name, "n": count, "n_d": fmt_thousands(count)} for name, count in deep_prov_counts]
    act_deep = data_json.get("deep_prov", [])
    if len(act_deep) != 5:
        return False, f"deep_prov count mismatch (actual: {len(act_deep)}, expected: 5)"
    if act_deep != exp_deep_prov:
        return False, f"deep_prov values mismatch (actual: {act_deep}, expected: {exp_deep_prov})"

    sorted_top = sorted(rows, key=lambda r: (float(r["mag"]), r["time_utc"]), reverse=True)[:5]
    exp_top5 = []
    for r in sorted_top:
        m = float(r["mag"])
        d = float(r["depth_km"])
        exp_top5.append({
            "date_wib": r["date_wib"],
            "depth_d": fmt_decimal(d, 1),
            "depth_km": d,
            "id": r["id"],
            "mag": m,
            "mag_d": fmt_decimal(m, 1),
            "place": r["place"],
        })
    act_top5 = data_json.get("top5", [])
    if len(act_top5) != 5:
        return False, f"top5 count mismatch (actual: {len(act_top5)}, expected: 5)"
    if act_top5 != exp_top5:
        return False, f"top5 values mismatch (actual: {act_top5}, expected: {exp_top5})"

    return True, "array counts and values match CSV recomputation (mag_bands, depth_bands, decades, province, deep_prov, top5)"


def check_3_top5_events(rows, data_json):
    sorted_top = sorted(rows, key=lambda r: (float(r["mag"]), r["time_utc"]), reverse=True)[:5]
    exp_mags = [float(r["mag"]) for r in sorted_top]
    exp_dates = [r["date_wib"] for r in sorted_top]
    exp_depths = [float(r["depth_km"]) for r in sorted_top]

    act_top5 = data_json.get("top5", [])
    if len(act_top5) != 5:
        return False, f"top5 count mismatch (actual: {len(act_top5)}, expected: 5)"

    act_mags = [item.get("mag") for item in act_top5]
    act_dates = [item.get("date_wib") for item in act_top5]
    act_depths = [item.get("depth_km") for item in act_top5]

    if act_mags != exp_mags:
        return False, f"top 5 magnitudes mismatch (actual: {act_mags}, expected: {exp_mags})"
    if act_dates != exp_dates:
        return False, f"top 5 dates in WIB mismatch (actual: {act_dates}, expected: {exp_dates})"
    if act_depths != exp_depths:
        return False, f"top 5 depths mismatch (actual: {act_depths}, expected: {exp_depths})"

    return True, "five largest events magnitudes, dates in WIB, and depths match recomputation"


def check_4_field_paths(rows, data_json):
    band_paths = {"M5": [], "M6": [], "M7": [], "M8": [], "M9": []}
    for r in rows:
        b = r["mag_band"]
        lon = float(r["lon"])
        neg_lat = -float(r["lat"])
        if abs(neg_lat) < 0.05:
            neg_lat = 0.0
        if abs(lon) < 0.05:
            lon = 0.0
        band_paths[b].append(f"M{lon:.1f} {neg_lat:.1f}h1")

    act_paths = data_json.get("field", {}).get("paths", {})
    for b in ["M5", "M6", "M7", "M8", "M9"]:
        exp_path = "".join(band_paths[b])
        if b not in act_paths:
            return False, f"field.paths missing band {b} (actual: missing, expected: path string)"
        act_path = act_paths[b]
        if act_path != exp_path:
            for i in range(min(len(act_path), len(exp_path))):
                if act_path[i] != exp_path[i]:
                    return False, f"field path {b} mismatch at index {i} (actual: {act_path[i:i+10]!r}, expected: {exp_path[i:i+10]!r})"
            return False, f"field path {b} length mismatch (actual: {len(act_path)}, expected: {len(exp_path)})"

    return True, "field paths match rebuilt CSV strings character by character"


def check_5_live_block(bmkg_live, data_json):
    live_data = data_json.get("live", {})
    latest = live_data.get("latest", [])
    felt = live_data.get("felt", [])

    if len(latest) != 15:
        return False, f"live.latest length mismatch (actual: {len(latest)}, expected: 15)"
    if len(felt) != 15:
        return False, f"live.felt length mismatch (actual: {len(felt)}, expected: 15)"

    exp_latest = bmkg_live.get("latest", [])
    exp_felt = bmkg_live.get("felt", [])

    if latest != exp_latest:
        return False, f"live.latest values mismatch (actual: {latest[:1]}, expected: {exp_latest[:1]})"
    if felt != exp_felt:
        return False, f"live.felt values mismatch (actual: {felt[:1]}, expected: {exp_felt[:1]})"

    return True, "live block carries 15 latest and 15 felt matching bmkg_live.json"


def check_6_index_html_data_and_stamp(index_html_path, template_path, data_json):
    with open(index_html_path, "r", encoding="utf-8") as f:
        line1 = f.readline().rstrip("\r\n")
        full_html = line1 + "\n" + f.read()

    m = re.match(r"^<!--BUILD sha256:([0-9a-fA-F]{64})-->$", line1)
    if not m:
        return False, f"gempa/index.html line 1 BUILD stamp invalid format (actual: {line1!r}, expected: '<!--BUILD sha256:<hex>-->')"
    act_hex = m.group(1).lower()

    with open(template_path, "rb") as tf:
        exp_hex = hashlib.sha256(tf.read()).hexdigest().lower()

    if act_hex != exp_hex:
        return False, f"gempa/index.html BUILD stamp sha256 mismatch (actual: {act_hex}, expected: {exp_hex})"

    prefix = "const DATA = "
    idx = full_html.find(prefix)
    if idx == -1:
        return False, "gempa/index.html missing 'const DATA = ' object (actual: not found, expected: present)"
    start_json = idx + len(prefix)
    end_json = full_html.find(";\n", start_json)
    if end_json == -1:
        end_json = full_html.find(";", start_json)
    if end_json == -1:
        return False, "gempa/index.html embedded DATA end delimiter not found"

    json_str = full_html[start_json:end_json]
    try:
        embedded_obj = json.loads(json_str)
    except Exception as e:
        return False, f"gempa/index.html embedded DATA JSON parse error: {e}"

    if embedded_obj != data_json:
        return False, "gempa/index.html embedded DATA is not value-equal to gempa/data.json (actual: mismatch found, expected: equal)"

    return True, "gempa/index.html embedded data is value-equal to gempa/data.json and line 1 BUILD stamp matches template sha256"


def check_7_template_structure(template_path):
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    sections = [
        "band-hero",
        "band-province",
        "band-depth",
        "band-mag",
        "band-decade",
        "band-live",
        "band-footer",
    ]
    for s in sections:
        if f'id="{s}"' not in content and f"id='{s}'" not in content:
            return False, f"gempa.template.html missing section id '{s}' (actual: missing, expected: present)"

    stub_count = content.count("STUB")
    if stub_count != 0:
        return False, f"gempa.template.html contains STUB marker (actual: {stub_count} occurrences, expected: 0)"

    em_dash_count = content.count("\u2014")
    if em_dash_count != 0:
        return False, f"gempa.template.html contains U+2014 em dash (actual: {em_dash_count} occurrences, expected: 0)"

    return True, "gempa.template.html contains all 7 section ids, 0 STUB, 0 U+2014"


def check_8_index_html_tokens(index_html_path):
    with open(index_html_path, "r", encoding="utf-8") as f:
        content = f.read()

    em_dash_count = content.count("\u2014")
    if em_dash_count != 0:
        return False, f"gempa/index.html contains U+2014 em dash (actual: {em_dash_count} occurrences, expected: 0)"

    data_count = content.count("__DATA__")
    if data_count != 0:
        return False, f"gempa/index.html contains placeholder __DATA__ (actual: {data_count} occurrences, expected: 0)"

    fonts_count = content.count("__FONTS__")
    if fonts_count != 0:
        return False, f"gempa/index.html contains placeholder __FONTS__ (actual: {fonts_count} occurrences, expected: 0)"

    return True, "gempa/index.html source contains 0 U+2014, 0 __DATA__, 0 __FONTS__"


def check_9_no_network_urls(index_html_path):
    with open(index_html_path, "r", encoding="utf-8") as f:
        content = f.read()

    urls = re.findall(r"https?://[^\s\"\'<>]+", content)
    network_urls = [u for u in urls if u != "http://www.w3.org/2000/svg"]
    if network_urls:
        return False, f"gempa/index.html contains network http URLs (actual: {network_urls}, expected: none)"

    return True, "gempa/index.html contains no network http URLs"


def check_10_breakdown_and_accounting(rows, build_report, data_json):
    if build_report.get("accounting_ok") is not True:
        return False, f"build_report.json accounting_ok is not true (actual: {build_report.get('accounting_ok')}, expected: True)"

    raw_dropped = build_report.get("dropped_breakdown")
    if not raw_dropped:
        return False, "build_report.json missing or empty dropped_breakdown (actual: missing/empty, expected: non-empty dict)"

    sorted_dropped = sorted(raw_dropped.items(), key=lambda kv: kv[1], reverse=True)
    if len(sorted_dropped) > 6:
        exp_breakdown = [[name, count] for name, count in sorted_dropped[:6]]
        remainder_sum = sum(count for _, count in sorted_dropped[6:])
        exp_breakdown.append(["lainnya", remainder_sum])
    else:
        exp_breakdown = [[name, count] for name, count in sorted_dropped]

    act_breakdown = data_json.get("method", {}).get("dropped_breakdown")
    if act_breakdown != exp_breakdown:
        return False, f"gempa/data.json method.dropped_breakdown mismatch (actual: {act_breakdown}, expected: {exp_breakdown})"

    dropped_sum = sum(item[1] for item in act_breakdown) if isinstance(act_breakdown, list) else None
    fig_dropped_total = data_json.get("fig", {}).get("dropped_total", {}).get("r")
    if dropped_sum != fig_dropped_total:
        return False, f"sum of dropped_breakdown entries mismatch (actual: {dropped_sum}, expected: {fig_dropped_total})"

    if "kept_breakdown" in build_report:
        exp_kept = dict(sorted(Counter(r["region"] for r in rows).items(), key=lambda kv: -kv[1]))
        act_kept = build_report["kept_breakdown"]
        if act_kept != exp_kept:
            return False, f"build_report.json kept_breakdown mismatch (actual: {act_kept}, expected: {exp_kept})"

    return True, "breakdown matches build_report.json, breakdown sum equals dropped_total, kept_breakdown matches CSV recomputation, and accounting_ok is true"


def check_11_headline_claims(template_path, data_json):
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    pct_dangkal = data_json.get("fig", {}).get("pct_dangkal", {}).get("r")
    if pct_dangkal is None:
        for b in data_json.get("depth_bands", []):
            if b.get("band") == "dangkal":
                pct_dangkal = b.get("pct")
                break

    m9_count = None
    for b in data_json.get("mag_bands", []):
        if b.get("band") == "M9":
            m9_count = b.get("n")
            break
    if m9_count is None:
        m9_count = data_json.get("field", {}).get("counts", {}).get("M9")

    span_years = data_json.get("fig", {}).get("span_years", {}).get("r")

    province_list = data_json.get("province", [])
    top_province = (
        province_list[0].get("name")
        if province_list
        else data_json.get("fig", {}).get("top_prov_name", {}).get("r")
    )

    java_provinces = {
        "DKI Jakarta",
        "Jawa Barat",
        "Jawa Tengah",
        "Jawa Timur",
        "Banten",
        "DI Yogyakarta",
    }

    claims_table = [
        {
            "name": 'Depth band headline "TIGA PER EMPAT GEMPA ITU DANGKAL"',
            "section_id": "band-depth",
            "search_str": "TIGA PER EMPAT",
            "base_value": "75%",
            "actual_value": f"{pct_dangkal}%" if pct_dangkal is not None else "None",
            "predicate": pct_dangkal is not None and round(pct_dangkal) == 75,
            "target_desc": f"{pct_dangkal}%",
        },
        {
            "name": 'Magnitude band headline "PIRAMIDA CURAM: M5 MENDOMINASI, M9 CUMA SATU"',
            "section_id": "band-mag",
            "search_str": "M9 CUMA SATU",
            "base_value": "1",
            "actual_value": str(m9_count),
            "predicate": m9_count == 1,
            "target_desc": str(m9_count),
        },
        {
            "name": 'Hero headline "SETENGAH ABAD, ... GEMPA M5+"',
            "section_id": "band-hero",
            "search_str": "SETENGAH ABAD",
            "base_value": "50 or 51 years",
            "actual_value": f"{span_years} years" if span_years is not None else "None",
            "predicate": span_years in (50, 51) if span_years is not None else False,
            "target_desc": f"{span_years} years",
        },
        {
            "name": 'Province band headline "YANG PALING SERING DIGUNCANG BUKAN JAWA"',
            "section_id": "band-province",
            "search_str": "BUKAN JAWA",
            "base_value": "non-Java province",
            "actual_value": str(top_province),
            "predicate": top_province is not None and top_province not in java_provinces,
            "target_desc": str(top_province),
        },
    ]

    for entry in claims_table:
        name = entry["name"]
        sec_id = entry["section_id"]
        search_str = entry["search_str"]

        sec_pattern = rf'<section[^>]*id=["\']{sec_id}["\'][^>]*>(.*?)</section>'
        m = re.search(sec_pattern, template_content, re.DOTALL)
        if not m:
            return (
                False,
                f"gempa.template.html missing section '#{sec_id}' for claim '{name}'",
            )
        section_html = m.group(1)

        if search_str not in section_html:
            return (
                False,
                f"headline string '{search_str}' for claim '{name}' no longer found in #{sec_id} of gempa.template.html (re-register claim or restore headline)",
            )

        if not entry["predicate"]:
            return (
                False,
                f"claim '{name}' went stale: based on {entry['base_value']}, actual is {entry['actual_value']} (update copy to match {entry['target_desc']})",
            )

    return (
        True,
        "all 4 headline claims verified: 'TIGA PER EMPAT' (depth), 'M9 CUMA SATU' (mag), 'SETENGAH ABAD' (hero), 'BUKAN JAWA' (province)",
    )



def check_12_basemap(basemap_data, data_json, template_path):
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    field_basemap = data_json.get("field", {}).get("basemap", {})
    if not field_basemap:
        return False, "gempa/data.json missing field.basemap"

    for k in ("land", "plates", "labels"):
        if field_basemap.get(k) != basemap_data.get(k):
            return False, f"gempa/data.json field.basemap.{k} does not equal basemap.json {k}"

    land = field_basemap.get("land", "")
    plates = field_basemap.get("plates", "")

    land_m = land.count("M")
    if not (500 <= land_m <= 900):
        return False, f"land subpaths count mismatch (actual: {land_m}, expected: 500..900)"
    if len(land) > 70000:
        return False, f"len(land) exceeds 70000 (actual: {len(land)}, expected: <= 70000)"

    plates_m = plates.count("M")
    if not (30 <= plates_m <= 60):
        return False, f"plates subpaths count mismatch (actual: {plates_m}, expected: 30..60)"
    if len(plates) > 6000:
        return False, f"len(plates) exceeds 6000 (actual: {len(plates)}, expected: <= 6000)"

    for name, path in [("land", land), ("plates", plates)]:
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+)", path)]
        if len(nums) % 2 != 0:
            return False, f"{name} coordinates count is odd ({len(nums)})"
        for i in range(0, len(nums), 2):
            x, y = nums[i], nums[i + 1]
            if not (94.5 <= x <= 141.5 and -6.5 <= y <= 11.5):
                return False, f"{name} coordinate ({x}, {y}) outside bbox [94.5..141.5, -6.5..11.5]"

    m_sw = re.search(
        r"field-basemap-land.*?stroke-width['\"]?\s*[:=,]\s*['\"]?([0-9.]+)",
        template_content,
        re.DOTALL,
    )
    if not m_sw:
        m_sw = re.search(
            r"stroke-width['\"]?\s*[:=,]\s*['\"]?([0-9.]+)['\"]?.*?field-basemap-land",
            template_content,
            re.DOTALL,
        )
    if not m_sw:
        return False, "could not find land stroke-width in gempa.template.html"
    land_sw = float(m_sw.group(1))
    if land_sw > 0.10:
        return False, f"template land stroke-width exceeds 0.10 (actual: {land_sw}, expected: <= 0.10)"

    labels = field_basemap.get("labels", [])
    if len(labels) != 5:
        return False, f"field.basemap.labels count mismatch (actual: {len(labels)}, expected: 5)"
    for l in labels:
        if l.get("kind") != "island":
            return False, f"field.basemap.labels kind mismatch (actual: {l.get('kind')!r}, expected: 'island')"
        x, y = l.get("x"), l.get("y")
        if x is None or y is None or not (94.5 <= x <= 141.5 and -6.5 <= y <= 11.5):
            return False, f"field.basemap label anchor ({x}, {y}) outside bbox [94.5..141.5, -6.5..11.5]"

    scale = field_basemap.get("scale", {})
    exp_km = round(3 * 111.195)
    if exp_km != 334 or scale.get("km") != 334:
        return False, f"scale km mismatch (actual: {scale.get('km')}, expected: 334)"
    if scale.get("label") != "334 km":
        return False, f"scale label mismatch (actual: {scale.get('label')!r}, expected: '334 km')"

    basemap_ids = [
        "field-basemap-land",
        "field-basemap-plates",
        "field-labels",
        "field-scale",
    ]
    for bid in basemap_ids:
        if (
            f'id="{bid}"' not in template_content
            and f"id='{bid}'" not in template_content
            and f"'id', '{bid}'" not in template_content
            and f'"id", "{bid}"' not in template_content
        ):
            return False, f"gempa.template.html missing id '{bid}'"

    if 'id="field-hit"' in template_content or "field-hit" in template_content:
        interaction_tokens = [
            'id="field-cursor"',
            'id="field-hit"',
            'id="field-detail"',
            'id="field-live"',
            'tabindex="0"',
            'aria-live="polite"',
        ]
        for itok in interaction_tokens:
            if itok not in template_content and itok.replace('"', "'") not in template_content:
                ident = itok.split('=', 1)[1].strip('"\'') if '=' in itok else itok
                if f"'id', '{ident}'" not in template_content and f'"id", "{ident}"' not in template_content:
                    return False, f"gempa.template.html missing {itok}"
    else:
        if 'aria-live="polite"' not in template_content and "aria-live='polite'" not in template_content:
            return False, 'gempa.template.html missing aria-live="polite"'

    for lic in (
        "Natural Earth",
        "domain publik",
        "Bird 2003 PB2002",
        "fraxen/tectonicplates",
        "ODC-By 1.0",
    ):
        if lic not in template_content:
            return False, f"gempa.template.html missing licence literal '{lic}'"

    if "@keyframes" in template_content:
        return False, "gempa.template.html contains @keyframes"

    for m in re.finditer(r'transition(?:-duration)?\s*:\s*([^;]+);', template_content):
        prop_val = m.group(1)
        if "loader" in template_content[max(0, m.start() - 200):m.end()]:
            continue
        for num, unit in re.findall(r'(\d+(?:\.\d+)?)(ms|s)\b', prop_val):
            val_ms = float(num) * 1000 if unit == "s" else float(num)
            if val_ms > 150:
                return False, f"gempa.template.html transition duration exceeds 150ms ({val_ms}ms)"

    return (
        True,
        "basemap geometry, provenance, bounds, labels, scale, licence literals, and template structure verified",
    )


def check_13_contrast_and_motion(template_path):
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    m_root = re.search(r":root\s*\{([^}]+)\}", template_content)
    if not m_root:
        return False, "gempa.template.html missing :root block"
    root_block = m_root.group(1)

    required_tokens = [
        "--land",
        "--plate",
        "--deep",
        "--deep-2",
        "--on-deep-muted",
        "--mag-1",
        "--mag-2",
        "--mag-3",
        "--mag-4",
        "--mag-5",
    ]
    tokens = {}
    for tok in required_tokens:
        m = re.search(rf"{tok}\s*:\s*([^;]+);", root_block)
        if not m:
            return False, f":root missing token {tok}"
        tokens[tok] = m.group(1).strip()

    def hex_to_rgb(h):
        h = h.strip().lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def srgb_to_linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(h):
        r, g, b = hex_to_rgb(h)
        return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)

    def contrast(h1, h2):
        l1, l2 = luminance(h1), luminance(h2)
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

    lum_land = luminance(tokens["--land"])
    lum_deep2 = luminance(tokens["--deep-2"])
    if not (lum_land < lum_deep2):
        return False, f"luminance(--land) is not < luminance(--deep-2) ({lum_land:.4f} vs {lum_deep2:.4f})"

    for i in range(1, 6):
        mag_tok = f"--mag-{i}"
        c_land = contrast(tokens[mag_tok], tokens["--land"])
        if c_land < 3.0:
            return False, f"contrast({mag_tok}, --land) < 3.0 ({c_land:.2f})"
        c_deep2 = contrast(tokens[mag_tok], tokens["--deep-2"])
        if c_deep2 < 3.0:
            return False, f"contrast({mag_tok}, --deep-2) < 3.0 ({c_deep2:.2f})"

    c_muted_land = contrast(tokens["--on-deep-muted"], tokens["--land"])
    if c_muted_land < 4.5:
        return False, f"contrast(--on-deep-muted, --land) < 4.5 ({c_muted_land:.2f})"
    c_muted_deep2 = contrast(tokens["--on-deep-muted"], tokens["--deep-2"])
    if c_muted_deep2 < 4.5:
        return False, f"contrast(--on-deep-muted, --deep-2) < 4.5 ({c_muted_deep2:.2f})"

    c_plate_deep2 = contrast(tokens["--plate"], tokens["--deep-2"])
    if c_plate_deep2 < 3.0:
        return False, f"contrast(--plate, --deep-2) < 3.0 ({c_plate_deep2:.2f})"
    c_plate_land = contrast(tokens["--plate"], tokens["--land"])
    if c_plate_land < 3.0:
        return False, f"contrast(--plate, --land) < 3.0 ({c_plate_land:.2f})"

    return True, "luminance order and all contrast thresholds verified against tokens"


def check_14_events_pairing(rows, data_json):
    events = data_json.get("events")
    if not events or not isinstance(events, dict):
        return False, "gempa/data.json missing or invalid 'events' object"

    row_count = len(rows)
    events_n = events.get("n")
    if events_n != row_count:
        return False, f"events.n mismatch (actual: {events_n}, expected: {row_count})"

    split_lists = {}
    for key in ("mag", "depth", "day", "prov"):
        if key not in events or not isinstance(events[key], str):
            return False, f"events.{key} missing or not a string"
        val_str = events[key]
        split_lists[key] = val_str.split(",") if val_str else []
        if len(split_lists[key]) != row_count:
            return False, f"events.{key} split length mismatch (actual: {len(split_lists[key])}, expected: {row_count})"

    epoch = date(1970, 1, 1)
    exp_provinces = sorted(set(r["province"] for r in rows))
    prov_to_idx = {p: i for i, p in enumerate(exp_provinces)}

    bands = ["M5", "M6", "M7", "M8", "M9"]
    rebuilt_mag = []
    rebuilt_depth = []
    rebuilt_day = []
    rebuilt_prov = []
    for b in bands:
        for r in rows:
            if r["mag_band"] == b:
                rebuilt_mag.append(str(round(float(r["mag"]) * 10)))
                rebuilt_depth.append(str(round(float(r["depth_km"]))))
                rebuilt_day.append(str((date.fromisoformat(r["date_wib"]) - epoch).days))
                rebuilt_prov.append(str(prov_to_idx[r["province"]]))

    rebuilt = {
        "mag": rebuilt_mag,
        "depth": rebuilt_depth,
        "day": rebuilt_day,
        "prov": rebuilt_prov,
    }

    for key in ("mag", "depth", "day", "prov"):
        act = split_lists[key]
        exp = rebuilt[key]
        for idx, (a, e) in enumerate(zip(act, exp)):
            if a != e:
                return False, f"events.{key} mismatch at index {idx} (actual: {a!r}, expected: {e!r})"

    field = data_json.get("field", {})
    field_order = field.get("order")
    if field_order != "M5,M6,M7,M8,M9:csv-asc":
        return False, f"field.order mismatch (actual: {field_order!r}, expected: 'M5,M6,M7,M8,M9:csv-asc')"

    field_provinces = field.get("provinces")
    if field_provinces != exp_provinces:
        return False, f"field.provinces mismatch (actual: {field_provinces}, expected: {exp_provinces})"

    n_provinces = len(field_provinces)
    for idx, p_str in enumerate(split_lists["prov"]):
        try:
            p_val = int(p_str)
        except ValueError:
            return False, f"events.prov non-integer value at index {idx}: {p_str!r}"
        if p_val < 0 or p_val >= n_provinces:
            return False, f"events.prov index {p_val} at position {idx} out of range [0..{n_provinces - 1}]"

    return True, "events pairing, field order, and provinces match CSV recomputation"


def main():
    csv_path = os.path.join(ROOT, "data", "out", "gempa_events.csv")
    live_path = os.path.join(ROOT, "data", "out", "bmkg_live.json")
    report_path = os.path.join(ROOT, "data", "out", "build_report.json")
    data_path = os.path.join(ROOT, "gempa/data.json")
    template_path = os.path.join(ROOT, "gempa.template.html")
    index_path = os.path.join(ROOT, "gempa/index.html")
    basemap_path = os.path.join(ROOT, "data", "raw", "basemap.json")

    for path, name in [
        (csv_path, "gempa_events.csv"),
        (live_path, "bmkg_live.json"),
        (report_path, "build_report.json"),
        (data_path, "gempa/data.json"),
        (template_path, "gempa.template.html"),
        (index_path, "gempa/index.html"),
        (basemap_path, "basemap.json"),
    ]:
        if not os.path.exists(path):
            print(f"FAIL Input file missing: {name} (actual: not found at {path}, expected: file present)")
            print("TOTAL 0/14")
            sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with open(report_path, "r", encoding="utf-8") as f:
        build_report = json.load(f)

    with open(live_path, "r", encoding="utf-8") as f:
        bmkg_live = json.load(f)

    with open(data_path, "r", encoding="utf-8") as f:
        data_json = json.load(f)

    with open(basemap_path, "r", encoding="utf-8") as f:
        basemap_data = json.load(f)

    checks = [
        ("check 1: gempa/data.json 'fig' values", lambda: check_1_fig(rows, build_report, data_json)),
        ("check 2: array counts and values", lambda: check_2_arrays(rows, data_json)),
        ("check 3: five largest events", lambda: check_3_top5_events(rows, data_json)),
        ("check 4: field paths", lambda: check_4_field_paths(rows, data_json)),
        ("check 5: live block", lambda: check_5_live_block(bmkg_live, data_json)),
        ("check 6: gempa/index.html data and stamp", lambda: check_6_index_html_data_and_stamp(index_path, template_path, data_json)),
        ("check 7: gempa.template.html structure", lambda: check_7_template_structure(template_path)),
        ("check 8: gempa/index.html forbidden tokens", lambda: check_8_index_html_tokens(index_path)),
        ("check 9: gempa/index.html no network URLs", lambda: check_9_no_network_urls(index_path)),
        ("check 10: region breakdown and accounting", lambda: check_10_breakdown_and_accounting(rows, build_report, data_json)),
        ("check 11: headline prose claims", lambda: check_11_headline_claims(template_path, data_json)),
        ("check 12: basemap geometry and template structure", lambda: check_12_basemap(basemap_data, data_json, template_path)),
        ("check 13: contrast tokens and motion rules", lambda: check_13_contrast_and_motion(template_path)),
        ("check 14: events pairing", lambda: check_14_events_pairing(rows, data_json)),
    ]

    passed = 0
    total = len(checks)

    for i, (label, func) in enumerate(checks, 1):
        ok, msg = func()
        if ok:
            passed += 1
            print(f"PASS {i}/{total}: {msg}")
        else:
            print(f"FAIL {i}/{total}: {msg}")

    print(f"TOTAL {passed}/{total}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
