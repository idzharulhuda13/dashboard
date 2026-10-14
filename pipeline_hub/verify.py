#!/usr/bin/env python3
"""Numeric acceptance gate for the public data dashboard hub (index.html).

Standard library only. Recomputes all metrics and payload values independently
from raw CSV sources and template.
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


def recompute_proficiency_from_csv(csv_path: str) -> dict:
    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    results = {}
    for domain in ["mathematics", "reading", "science"]:
        d_rows = [r for r in rows if r["domain"] == domain]
        idn_rows = [r for r in d_rows if r["subject"] == "Indonesia"]
        oecd_rows = [r for r in d_rows if r["subject"] == "OECD average"]

        stats = [r["stat"] for r in idn_rows]
        if "level_2" not in stats:
            raise RuntimeError(f"Token 'level_2' not found in stats for {domain}")
        l2_idx = stats.index("level_2")
        below_l2 = set(stats[:l2_idx])

        idn_val = sum(float(r["value"]) for r in idn_rows if r["stat"] in below_l2)
        oecd_val = sum(float(r["value"]) for r in oecd_rows if r["stat"] in below_l2)

        results[domain] = {
            "idn": idn_val,
            "oecd": oecd_val,
            "idn_d": fmt_float(idn_val, 1),
            "oecd_d": fmt_float(oecd_val, 1),
        }

    return results


def check_1_stamp(index_path: str, template_path: str, annex_prof_path: str, gempa_data_path: str, fonts_dir: str):
    with open(index_path, "r", encoding="utf-8") as f:
        line1 = f.readline().rstrip("\r\n")

    m = re.match(r"^<!--BUILD sha256:([0-9a-fA-F]{64})-->$", line1)
    if not m:
        return False, f"index.html line 1 invalid stamp format: {line1!r}"

    act_hex = m.group(1).lower()

    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    input_paths = [annex_prof_path, gempa_data_path, template_path] + font_paths

    stamp_hasher = hashlib.sha256()
    for p in input_paths:
        rel_path = os.path.relpath(p, ROOT).replace("\\", "/")
        with open(p, "rb") as bf:
            data = bf.read()
        stamp_hasher.update(rel_path.encode("utf-8"))
        stamp_hasher.update(b"\x00")
        stamp_hasher.update(data)

    exp_hex = stamp_hasher.hexdigest().lower()
    if act_hex != exp_hex:
        return False, f"stamp sha256 mismatch (actual: {act_hex}, expected: {exp_hex})"

    return True, f"stamp matches fresh sha256 over {len(input_paths)} inputs ({act_hex[:12]}...)"


def check_2_payload_rebuilt(embedded_data: dict, annex_prof_path: str):
    recomputed = recompute_proficiency_from_csv(annex_prof_path)

    fig = embedded_data.get("fig", {})
    fig_mapping = {
        "prof_math_idn": ("mathematics", "idn"),
        "prof_math_oecd": ("mathematics", "oecd"),
        "prof_read_idn": ("reading", "idn"),
        "prof_read_oecd": ("reading", "oecd"),
        "prof_scie_idn": ("science", "idn"),
        "prof_scie_oecd": ("science", "oecd"),
    }

    for key, (domain, pop) in fig_mapping.items():
        if key not in fig:
            return False, f"missing fig key in payload: {key!r}"
        entry = fig[key]
        if not isinstance(entry, dict):
            return False, f"payload fig[{key!r}] is not an object: {entry!r}"

        act_v = entry.get("v")
        act_d = entry.get("d")
        exp_v = recomputed[domain][pop]
        exp_d = recomputed[domain][f"{pop}_d"]

        if act_v is None or act_d is None:
            return False, f"missing 'v' or 'd' in payload fig[{key!r}]"
        if abs(act_v - exp_v) > 0.01:
            return False, f"{key} numeric value mismatch (payload: {act_v}, expected: {exp_v:.5f}, diff: {abs(act_v - exp_v):.5f})"
        if act_d != exp_d:
            return False, f"{key} display string mismatch (payload: {act_d!r}, expected: {exp_d!r})"

    bars = embedded_data.get("profBars")
    if not isinstance(bars, list) or len(bars) != 3:
        return False, f"expected 3 bars in profBars, found {len(bars) if isinstance(bars, list) else type(bars)}"

    domain_map = {
        "matematika": "mathematics",
        "membaca": "reading",
        "sains": "science",
    }

    for bar in bars:
        dom_label = bar.get("domain")
        if dom_label not in domain_map:
            return False, f"unexpected domain {dom_label!r} in profBars"
        dom = domain_map[dom_label]

        exp_idn_v = recomputed[dom]["idn"]
        exp_oecd_v = recomputed[dom]["oecd"]
        exp_idn_d = recomputed[dom]["idn_d"]
        exp_oecd_d = recomputed[dom]["oecd_d"]

        act_idn_v = bar.get("idn")
        act_oecd_v = bar.get("oecd")
        act_idn_d = bar.get("idn_d")
        act_oecd_d = bar.get("oecd_d")

        if act_idn_v is None or abs(act_idn_v - exp_idn_v) > 0.01:
            return False, f"profBars {dom_label} idn value mismatch (payload: {act_idn_v}, expected: {exp_idn_v:.5f})"
        if act_oecd_v is None or abs(act_oecd_v - exp_oecd_v) > 0.01:
            return False, f"profBars {dom_label} oecd value mismatch (payload: {act_oecd_v}, expected: {exp_oecd_v:.5f})"
        if act_idn_d != exp_idn_d:
            return False, f"profBars {dom_label} idn_d mismatch (payload: {act_idn_d!r}, expected: {exp_idn_d!r})"
        if act_oecd_d != exp_oecd_d:
            return False, f"profBars {dom_label} oecd_d mismatch (payload: {act_oecd_d!r}, expected: {exp_oecd_d!r})"

    return True, "rebuilt payload matches annex_proficiency.csv within 0.01 tolerance across all 6 figures and 3 bars"


def check_3_field_provenance(embedded_data: dict, gempa_data_path: str):
    with open(gempa_data_path, "r", encoding="utf-8") as f:
        gempa_json = json.load(f)

    field_exp = gempa_json.get("field")
    field_act = embedded_data.get("field")
    if field_act is None:
        return False, "missing 'field' object in embedded DATA"
    if field_act != field_exp:
        return False, "payload field object does not equal gempa/data.json field object exactly"

    return True, f"field provenance verified: payload field matches gempa/data.json exactly ({len(field_act.get('paths', {}))} paths)"


def check_4_fallback_literals(index_content: str, embedded_data: dict):
    fig = embedded_data.get("fig", {})
    spans = re.findall(r"<span\b[^>]*data-fig=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</span>", index_content, re.DOTALL)
    if not spans:
        return False, "no span[data-fig] found in index.html"

    for key, inner in spans:
        literal = " ".join(inner.split())
        val = fig.get(key)
        if val is None:
            return False, f"missing fig key in payload: {key!r}"
        disp = val.get("d") if isinstance(val, dict) and "d" in val else str(val)

        if literal != disp:
            return False, f"data-fig fallback literal mismatch in page for {key!r} (actual: {literal!r}, expected: {disp!r})"

    return True, f"all {len(spans)} span[data-fig] fallback literals equal payload display values"


def check_5_skeleton(index_path: str, template_path: str):
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    with open(index_path, "r", encoding="utf-8") as f:
        index_lines = f.readlines()

    if not index_lines:
        return False, "index.html is empty"

    page_lines = index_lines[1:]

    skeleton_lines = []
    in_fonts = False
    fonts_begin_found = False
    fonts_end_found = False

    for line in page_lines:
        if "/*FONTS:BEGIN*/" in line:
            in_fonts = True
            fonts_begin_found = True
            skeleton_lines.append(line)
            continue
        if "/*FONTS:END*/" in line:
            in_fonts = False
            fonts_end_found = True
            skeleton_lines.append("/*__FONTS__*/\n")
            skeleton_lines.append(line)
            continue
        if in_fonts:
            continue
        if line.startswith("  const DATA = ") or line.startswith("const DATA = "):
            skeleton_lines.append("  const DATA = /*__DATA__*/ {};\n")
            continue
        skeleton_lines.append(line)

    if not fonts_begin_found or not fonts_end_found:
        return False, f"font markers missing (begin: {fonts_begin_found}, end: {fonts_end_found})"

    reconstructed = "".join(skeleton_lines)
    if reconstructed == template_content:
        return True, "page skeleton matches hub.template.html byte for byte after collapsing injected regions"

    recon_lines = reconstructed.splitlines(keepends=True)
    tmpl_lines = template_content.splitlines(keepends=True)
    max_len = max(len(recon_lines), len(tmpl_lines))
    for line_no in range(1, max_len + 1):
        r_line = recon_lines[line_no - 1] if line_no <= len(recon_lines) else "<EOF>\n"
        t_line = tmpl_lines[line_no - 1] if line_no <= len(tmpl_lines) else "<EOF>\n"
        if r_line != t_line:
            return False, f"page skeleton differs from template at line {line_no}:\n  actual:   {r_line.rstrip()!r}\n  expected: {t_line.rstrip()!r}"

    return False, "skeleton length mismatch"


def check_6_font_region(index_path: str, fonts_dir: str):
    with open(index_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    in_fonts = False
    font_lines = []
    for line in lines:
        if "/*FONTS:BEGIN*/" in line:
            in_fonts = True
            continue
        if "/*FONTS:END*/" in line:
            in_fonts = False
            break
        if in_fonts:
            font_lines.append(line)

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
            return False, f"required font file not found: {fpath}"
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

    expected_fonts = "\n".join(font_rules) + "\n"
    actual_fonts = "".join(font_lines)

    if actual_fonts != expected_fonts:
        act_flines = actual_fonts.splitlines(keepends=True)
        exp_flines = expected_fonts.splitlines(keepends=True)
        max_flen = max(len(act_flines), len(exp_flines))
        for line_no in range(1, max_flen + 1):
            a_line = act_flines[line_no - 1] if line_no <= len(act_flines) else "<EOF>\n"
            e_line = exp_flines[line_no - 1] if line_no <= len(exp_flines) else "<EOF>\n"
            if a_line != e_line:
                return False, f"font region differs at line {line_no}:\n  actual:   {a_line.rstrip()!r}\n  expected: {e_line.rstrip()!r}"
        return False, "font region mismatch"

    return True, f"injected font region matches expected CSS from {len(font_configs)} font files byte for byte"


def check_7_hygiene(index_content: str, gempa_data_path: str):
    em_dashes = index_content.count("\u2014")
    if em_dashes != 0:
        return False, f"index.html contains U+2014 em dash ({em_dashes} occurrences)"

    urls = re.findall(r"https?://[^\s\"'<>]+", index_content)
    disallowed_urls = [u for u in urls if u != "http://www.w3.org/2000/svg"]
    if disallowed_urls:
        return False, f"index.html contains external network URLs: {disallowed_urls}"

    c_data = index_content.count("__DATA__")
    c_fonts = index_content.count("__FONTS__")
    if c_data != 0 or c_fonts != 0:
        return False, f"unresolved placeholders found (__DATA__: {c_data}, __FONTS__: {c_fonts})"

    if 'href="/gempa/"' not in index_content:
        return False, 'missing required entry link href="/gempa/"'
    if 'href="/pisa/"' not in index_content:
        return False, 'missing required entry link href="/pisa/"'

    with open(gempa_data_path, "r", encoding="utf-8") as f:
        gempa_json = json.load(f)

    gempa_fig = gempa_json.get("fig", {})
    for total_key in ["total", "raw_events"]:
        total_entry = gempa_fig.get(total_key)
        if total_entry:
            disp = total_entry.get("d") if isinstance(total_entry, dict) else str(total_entry)
            if disp and disp in index_content:
                return False, f"earthquake total from gempa/data.json ({total_key}={disp!r}) printed on hub"

    return True, "hygiene clean (0 U+2014, 0 external network URLs, placeholders resolved, entry links present, no earthquake total printed)"


def main():
    template_path = os.path.join(ROOT, "hub.template.html")
    annex_prof_path = os.path.join(ROOT, "data", "pisa", "annex_proficiency.csv")
    gempa_data_path = os.path.join(ROOT, "gempa", "data.json")
    fonts_dir = os.path.join(ROOT, "fonts")
    index_path = os.path.join(ROOT, "index.html")

    required_files = [
        (annex_prof_path, "data/pisa/annex_proficiency.csv"),
        (gempa_data_path, "gempa/data.json"),
        (template_path, "hub.template.html"),
        (index_path, "index.html"),
    ]

    for path, name in required_files:
        if not os.path.exists(path):
            print(f"FAIL Input file missing: {name} (actual: not found at {path}, expected: file present)")
            print("TOTAL 0/7")
            sys.exit(1)

    with open(index_path, "r", encoding="utf-8") as f:
        index_content = f.read()

    prefix = "const DATA = "
    idx = index_content.find(prefix)
    if idx == -1:
        print("FAIL Embedded DATA not found in index.html")
        print("TOTAL 0/7")
        sys.exit(1)

    start_json = idx + len(prefix)
    end_json = index_content.find(";\n", start_json)
    if end_json == -1:
        end_json = index_content.find(";", start_json)
    if end_json == -1:
        print("FAIL Embedded DATA end delimiter not found in index.html")
        print("TOTAL 0/7")
        sys.exit(1)

    try:
        embedded_data = json.loads(index_content[start_json:end_json])
    except Exception as e:
        print(f"FAIL Embedded DATA JSON parse failed: {e}")
        print("TOTAL 0/7")
        sys.exit(1)

    checks = [
        ("stamp", lambda: check_1_stamp(index_path, template_path, annex_prof_path, gempa_data_path, fonts_dir)),
        ("payload rebuilt", lambda: check_2_payload_rebuilt(embedded_data, annex_prof_path)),
        ("field provenance", lambda: check_3_field_provenance(embedded_data, gempa_data_path)),
        ("fallback literals", lambda: check_4_fallback_literals(index_content, embedded_data)),
        ("skeleton", lambda: check_5_skeleton(index_path, template_path)),
        ("font region", lambda: check_6_font_region(index_path, fonts_dir)),
        ("hygiene", lambda: check_7_hygiene(index_content, gempa_data_path)),
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
