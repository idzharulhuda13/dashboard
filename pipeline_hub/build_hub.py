#!/usr/bin/env python3
"""Build generator for the public data dashboard hub (index.html).

Standard library only. Deterministic: frozen inputs, no wall clock,
no network, no randomness.
"""

import base64
import csv
import glob
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_float(v: float, dp: int = 1) -> str:
    s = f"{float(v):.{dp}f}"
    if s in ("-0.0", "-0"):
        s = "0.0" if dp > 0 else "0"
    return s.replace(".", ",")


def main():
    template_path = os.path.join(ROOT, "hub.template.html")
    annex_prof_path = os.path.join(ROOT, "data", "pisa", "annex_proficiency.csv")
    gempa_data_path = os.path.join(ROOT, "gempa", "data.json")
    fonts_dir = os.path.join(ROOT, "fonts")
    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    output_path = os.path.join(ROOT, "index.html")

    if not os.path.exists(template_path):
        raise RuntimeError(f"Missing template file: {template_path}")
    if not os.path.exists(annex_prof_path):
        raise RuntimeError(f"Missing proficiency CSV: {annex_prof_path}")
    if not os.path.exists(gempa_data_path):
        raise RuntimeError(f"Missing gempa data JSON: {gempa_data_path}")
    if len(font_paths) != 5:
        raise RuntimeError(f"Expected 5 font files in {fonts_dir}, found {len(font_paths)}")

    input_paths = [annex_prof_path, gempa_data_path, template_path] + font_paths

    stamp_hasher = hashlib.sha256()
    for p in input_paths:
        rel_path = os.path.relpath(p, ROOT).replace("\\", "/")
        with open(p, "rb") as f:
            data = f.read()
        stamp_hasher.update(rel_path.encode("utf-8"))
        stamp_hasher.update(b"\x00")
        stamp_hasher.update(data)
        print(f"{rel_path}: {len(data)} bytes")

    stamp_hex = stamp_hasher.hexdigest()
    stamp_line = f"<!--BUILD sha256:{stamp_hex}-->\n"

    with open(annex_prof_path, "r", encoding="utf-8") as f:
        annex_prof_rows = list(csv.DictReader(f))

    prof_sums = {}
    for domain in ["mathematics", "reading", "science"]:
        d_rows = [r for r in annex_prof_rows if r["domain"] == domain]
        idn_rows = [r for r in d_rows if r["subject"] == "Indonesia"]
        oecd_rows = [r for r in d_rows if r["subject"] == "OECD average"]

        stats = [r["stat"] for r in idn_rows]
        if "level_2" not in stats:
            raise RuntimeError(f"Token 'level_2' not found in stats for {domain}")
        l2_idx = stats.index("level_2")
        below_l2_stats = set(stats[:l2_idx])

        idn_sum = sum(float(r["value"]) for r in idn_rows if r["stat"] in below_l2_stats)
        oecd_sum = sum(float(r["value"]) for r in oecd_rows if r["stat"] in below_l2_stats)

        idn_val = round(idn_sum, 5)
        oecd_val = round(oecd_sum, 5)
        prof_sums[domain] = {
            "idn": idn_val,
            "oecd": oecd_val,
            "idn_d": fmt_float(idn_val, 1),
            "oecd_d": fmt_float(oecd_val, 1),
        }

    with open(gempa_data_path, "r", encoding="utf-8") as f:
        gempa_json = json.load(f)
    field_data = gempa_json["field"]

    fig = {
        "prof_math_idn": {"v": prof_sums["mathematics"]["idn"], "d": prof_sums["mathematics"]["idn_d"]},
        "prof_math_oecd": {"v": prof_sums["mathematics"]["oecd"], "d": prof_sums["mathematics"]["oecd_d"]},
        "prof_read_idn": {"v": prof_sums["reading"]["idn"], "d": prof_sums["reading"]["idn_d"]},
        "prof_read_oecd": {"v": prof_sums["reading"]["oecd"], "d": prof_sums["reading"]["oecd_d"]},
        "prof_scie_idn": {"v": prof_sums["science"]["idn"], "d": prof_sums["science"]["idn_d"]},
        "prof_scie_oecd": {"v": prof_sums["science"]["oecd"], "d": prof_sums["science"]["oecd_d"]},
    }

    prof_bars = [
        {
            "domain": "matematika",
            "idn": prof_sums["mathematics"]["idn"],
            "oecd": prof_sums["mathematics"]["oecd"],
            "idn_d": prof_sums["mathematics"]["idn_d"],
            "oecd_d": prof_sums["mathematics"]["oecd_d"],
        },
        {
            "domain": "membaca",
            "idn": prof_sums["reading"]["idn"],
            "oecd": prof_sums["reading"]["oecd"],
            "idn_d": prof_sums["reading"]["idn_d"],
            "oecd_d": prof_sums["reading"]["oecd_d"],
        },
        {
            "domain": "sains",
            "idn": prof_sums["science"]["idn"],
            "oecd": prof_sums["science"]["oecd"],
            "idn_d": prof_sums["science"]["idn_d"],
            "oecd_d": prof_sums["science"]["oecd_d"],
        },
    ]

    payload = {
        "fig": fig,
        "profBars": prof_bars,
        "field": field_data,
    }

    compact_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

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

    fonts_css = "\n".join(font_rules)

    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    if "/*__FONTS__*/" not in template_str:
        raise RuntimeError("Placeholder /*__FONTS__*/ not found in hub.template.html")

    html_rendered = template_str.replace("/*__FONTS__*/", fonts_css)

    if "/*__DATA__*/ {}" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/ {}", compact_json)
    elif "/*__DATA__*/" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/", compact_json)
    else:
        raise RuntimeError("Placeholder /*__DATA__*/ not found in hub.template.html")

    final_html = stamp_line + html_rendered

    with open(output_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_html)

    print(f"Emitted {len(fig)} fig entries and {len(prof_bars)} proficiency bars")
    print(stamp_line.strip())


if __name__ == "__main__":
    main()
