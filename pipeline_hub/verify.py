#!/usr/bin/env python3
"""Numeric acceptance gate for the public data dashboard hub (index.html).

Standard library only. Recomputes all metrics and payload values independently
from raw CSV sources and template.
"""

import base64
import csv
import glob
import hashlib
import importlib.util
import json
import os
import re
import struct
import sys
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://dashboard.idzharulhuda.com"


def html_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")


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


def check_1_stamp(index_path: str, template_path: str, annex_prof_path: str, gempa_data_path: str, fonts_dir: str, explorer_payload_path: str):
    with open(index_path, "r", encoding="utf-8") as f:
        line1 = f.readline().rstrip("\r\n")

    m = re.match(r"^<!--BUILD sha256:([0-9a-fA-F]{64})-->$", line1)
    if not m:
        return False, f"index.html line 1 invalid stamp format: {line1!r}"

    act_hex = m.group(1).lower()

    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    pasar_paths = [os.path.join(ROOT, "data", "pasar", f"{name}.json") for name in ("ihsg", "rupiah", "msci")]
    input_paths = [annex_prof_path, gempa_data_path, template_path, explorer_payload_path] + pasar_paths + font_paths

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


def check_2_payload_rebuilt(embedded_data: dict, annex_prof_path: str, explorer_payload_path: str):
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

    # Pelat 03: angka explorer dihitung ulang dari muatan /pisa-explorer/data.json, bukan dibaca
    # dari payload halaman ini. Kalau payload explorer berubah tapi hub nggak dibangun ulang,
    # cek ini gagal.
    with open(explorer_payload_path, "r", encoding="utf-8") as f:
        exp_src = json.load(f)

    exp_band_keys = exp_src.get("band_keys")
    if not isinstance(exp_band_keys, list) or "level_4" not in exp_band_keys:
        return False, f"explorer payload band_keys missing level_4 (actual: {exp_band_keys!r})"
    l4_idx = exp_band_keys.index("level_4")
    exp_levels = exp_src.get("levels") or {}

    def exp_expected(country_id: str, domain: str):
        arr = (exp_levels.get(country_id) or {}).get(domain)
        if not isinstance(arr, list):
            return None
        tail = arr[l4_idx:]
        if any(v is None for v in tail):
            return None
        return round(sum(tail), 5)

    exp_mapping = {
        "exp_math_idn": ("indonesia", "mathematics"),
        "exp_math_vn": ("viet-nam", "mathematics"),
        "exp_read_idn": ("indonesia", "reading"),
        "exp_read_vn": ("viet-nam", "reading"),
        "exp_scie_idn": ("indonesia", "science"),
        "exp_scie_vn": ("viet-nam", "science"),
    }
    for key, (cid, domain) in exp_mapping.items():
        if key not in fig:
            return False, f"missing explorer fig key in payload: {key!r}"
        entry = fig[key]
        exp_v = exp_expected(cid, domain)
        if exp_v is None:
            return False, f"explorer payload has no usable Level 4+ bands for {cid}/{domain}"
        if not isinstance(entry, dict) or entry.get("v") is None:
            return False, f"payload fig[{key!r}] is not an object with a numeric value (actual: {entry!r})"
        if abs(entry["v"] - exp_v) > 0.01:
            return False, f"{key} mismatch (payload: {entry['v']}, expected: {exp_v:.5f})"
        exp_d = fmt_float(exp_v, 2)
        if entry.get("d") != exp_d:
            return False, f"{key} display mismatch (payload: {entry.get('d')!r}, expected: {exp_d!r})"

    pyramid = embedded_data.get("expPyramid")
    if not isinstance(pyramid, dict):
        return False, f"expPyramid missing from payload (actual: {pyramid!r})"
    idn_sci = (exp_levels.get("indonesia") or {}).get("science")
    vn_sci = (exp_levels.get("viet-nam") or {}).get("science")
    if not isinstance(idn_sci, list) or not isinstance(vn_sci, list):
        return False, "explorer payload science levels missing for indonesia/viet-nam"

    exp_pairs = []
    for i in range(len(exp_band_keys)):
        if idn_sci[i] is None and vn_sci[i] is None:
            continue
        exp_pairs.append((idn_sci[i], vn_sci[i]))

    act_bands = pyramid.get("bands")
    if not isinstance(act_bands, list):
        return False, f"expPyramid bands is not a list (actual: {act_bands!r})"
    if len(act_bands) != len(exp_pairs):
        return False, f"expPyramid band count mismatch (payload: {len(act_bands)}, expected: {len(exp_pairs)})"
    for got, (exp_idn, exp_vn) in zip(act_bands, exp_pairs):
        label = got.get("label") if isinstance(got, dict) else None
        for side, exp_val in (("idn", exp_idn), ("vn", exp_vn)):
            act_val = got.get(side) if isinstance(got, dict) else None
            if exp_val is None:
                if act_val is not None:
                    return False, f"expPyramid band {label!r} {side} should be null (actual: {act_val})"
            elif act_val is None or abs(act_val - exp_val) > 0.01:
                return False, f"expPyramid band {label!r} {side} mismatch (actual: {act_val}, expected: {exp_val})"

    exp_max = max(max(v or 0.0 for v in pair) for pair in exp_pairs)
    exp_axis = float(int(exp_max / 10 + 1) * 10)
    if abs(float(pyramid.get("axisMax") or 0) - exp_axis) > 0.001:
        return False, f"expPyramid axisMax mismatch (actual: {pyramid.get('axisMax')}, expected: {exp_axis})"

    return True, (
        "rebuilt payload matches annex_proficiency.csv (6 figures, 3 bars) and pisa-explorer/data.json "
        f"(6 figures, {len(exp_pairs)} pyramid bands) within 0.01 tolerance"
    )


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

    if "<!--FIG:" in index_content:
        return False, "index.html masih memuat placeholder <!--FIG:...--> yang belum terisi"
    svg_count = index_content.count('class="window-fig"')
    if svg_count != 3:
        return False, f"jumlah gambar pasar statis di index.html bukan 3 (actual: {svg_count})"

    return True, (
        f"all {len(spans)} span[data-fig] fallback literals equal payload display values, "
        f"{svg_count} gambar pasar statis terisi"
    )


def check_5_skeleton(index_path: str, template_path: str):
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    with open(index_path, "r", encoding="utf-8") as f:
        index_lines = f.readlines()

    if not index_lines:
        return False, "index.html is empty"

    page_lines = index_lines[1:]
    page_content = "".join(page_lines)

    tmpl_begin_count = template_content.count("<!--SEO:BEGIN-->")
    tmpl_end_count = template_content.count("<!--SEO:END-->")
    if tmpl_begin_count != 1 or tmpl_end_count != 1:
        return False, f"template SEO markers count mismatch (begin: {tmpl_begin_count}, end: {tmpl_end_count})"
    if template_content.index("<!--SEO:BEGIN-->") >= template_content.index("<!--SEO:END-->"):
        return False, "template <!--SEO:BEGIN--> must precede <!--SEO:END-->"

    page_begin_count = page_content.count("<!--SEO:BEGIN-->")
    page_end_count = page_content.count("<!--SEO:END-->")
    if page_begin_count != 1 or page_end_count != 1:
        return False, f"page SEO markers count mismatch (begin: {page_begin_count}, end: {page_end_count})"
    if page_content.index("<!--SEO:BEGIN-->") >= page_content.index("<!--SEO:END-->"):
        return False, "page <!--SEO:BEGIN--> must precede <!--SEO:END-->"

    tmpl_skeleton_lines = []
    in_tmpl_seo = False
    for line in template_content.splitlines(keepends=True):
        if "<!--SEO:BEGIN-->" in line:
            in_tmpl_seo = True
            tmpl_skeleton_lines.append(line)
            continue
        if "<!--SEO:END-->" in line:
            in_tmpl_seo = False
            tmpl_skeleton_lines.append(line)
            continue
        if in_tmpl_seo:
            continue
        tmpl_skeleton_lines.append(line)
    template_skeleton = "".join(tmpl_skeleton_lines)

    skeleton_lines = []
    in_fonts = False
    fonts_begin_found = False
    fonts_end_found = False
    in_page_seo = False

    for line in page_lines:
        if "<!--SEO:BEGIN-->" in line:
            in_page_seo = True
            skeleton_lines.append(line)
            continue
        if "<!--SEO:END-->" in line:
            in_page_seo = False
            skeleton_lines.append(line)
            continue
        if in_page_seo:
            continue
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

    # Gambar pasar disuntikkan saat build: satu placeholder di template diganti satu <svg>
    # inline, jadi keduanya dikuncikan ke sentinel yang sama sebelum dibandingkan baris demi baris.
    reconstructed = re.sub(r'<svg class="window-fig".*?</svg>', '<!--__FIG__-->', reconstructed, flags=re.DOTALL)
    template_skeleton = re.sub(r'<!--FIG:pasar_[a-z]+-->', '<!--__FIG__-->', template_skeleton)

    # Isi span[data-fig] ditulis ulang builder dari payload, jadi teksnya dinormalkan dulu;
    # check_4 yang memastikan nilai di halaman benar-benar sama dengan payload.
    reconstructed = re.sub(r'(data-fig="[^"]+"[^>]*>)[^<]*(<)', r'\1__FIGVAL__\2', reconstructed)
    template_skeleton = re.sub(r'(data-fig="[^"]+"[^>]*>)[^<]*(<)', r'\1__FIGVAL__\2', template_skeleton)
    if reconstructed == template_skeleton:
        return True, "page skeleton matches hub.template.html byte for byte after collapsing injected regions"

    recon_lines = reconstructed.splitlines(keepends=True)
    tmpl_lines = template_skeleton.splitlines(keepends=True)
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
    allowed_hosts = {
        "www.w3.org",
        "w3.org",
        "dashboard.idzharulhuda.com",
        "idzharulhuda.com",
        "schema.org",
    }
    disallowed_urls = [
        u for u in urls
        if (urlparse(u).hostname or "") not in allowed_hosts and u != "http://www.w3.org/2000/svg"
    ]
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


def check_8_head_and_jsonld(index_path: str, template_path: str, gempa_data_path: str, embedded_data: dict):
    with open(index_path, "r", encoding="utf-8") as f:
        index_content = f.read()

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    with open(gempa_data_path, "r", encoding="utf-8") as f:
        gempa_json = json.load(f)

    begin_count = index_content.count("<!--SEO:BEGIN-->")
    end_count = index_content.count("<!--SEO:END-->")
    if begin_count != 1:
        return False, f"index.html <!--SEO:BEGIN--> count mismatch (actual: {begin_count}, expected: 1)"
    if end_count != 1:
        return False, f"index.html <!--SEO:END--> count mismatch (actual: {end_count}, expected: 1)"
    if index_content.count("<!--SEO") != 2:
        return False, f"index.html contains leftover marker text (actual: {index_content.count('<!--SEO')}, expected: 2)"

    idx_begin = index_content.find("<!--SEO:BEGIN-->")
    idx_end = index_content.find("<!--SEO:END-->")
    if idx_begin >= idx_end:
        return False, "index.html <!--SEO:BEGIN--> must precede <!--SEO:END-->"

    m_block = re.search(r"([ \t]*<!--SEO:BEGIN-->\n.*?\n[ \t]*<!--SEO:END-->)", index_content, re.DOTALL)
    if not m_block:
        return False, "index.html could not extract SEO block"
    actual_block = m_block.group(1)

    if "\u2014" in actual_block:
        return False, "index.html SEO block contains U+2014 em dash"

    placeholders = ["{TITLE}", "{DESC}", "{CANONICAL}", "{THEME}", "{SLUG}", "{OGALT}", "{JSONLD}"]
    for ph in placeholders:
        if ph in actual_block:
            return False, f"index.html SEO block contains placeholder {ph}"
    if re.search(r"\{[A-Z_]+\}", actual_block):
        return False, "index.html SEO block contains unexpanded placeholder"

    m_theme = re.search(r":root\s*\{[^}]*--ground:\s*([^;]+);", template_content)
    if not m_theme:
        return False, "hub.template.html missing :root --ground color"
    theme = m_theme.group(1).strip()

    m_h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", template_content)
    if not m_h1:
        return False, "hub.template.html missing body H1 text"
    h1_text = m_h1.group(1).strip()

    plate_entries = re.findall(r'<a href="(/[^"]+)" class="entry-link-title">([^<]+)</a>', template_content)
    if len(plate_entries) < 2:
        return False, f"hub.template.html plate entries count < 2 (actual: {len(plate_entries)})"

    gempa_fig = gempa_json.get("fig", {})
    span_first_wib = gempa_fig.get("span_first_wib", {}).get("r", "")
    if not span_first_wib or len(span_first_wib) < 4:
        return False, "gempa/data.json missing or invalid fig.span_first_wib.r"
    gempa_first_year = span_first_wib[:4]

    prof_entry = embedded_data.get("fig", {}).get("prof_math_idn")
    prof_math_idn_d = prof_entry.get("d") if isinstance(prof_entry, dict) else str(prof_entry)
    if not prof_math_idn_d:
        return False, "missing fig.prof_math_idn.d in embedded data"

    TITLE = "Dashboard Data Publik: Gempa, PISA 2025, dan Pasar"
    CANONICAL = f"{SITE}/"
    SLUG = "hub"
    DESC = (
        f"Enam pelat: katalog gempa M5+ sejak {gempa_first_year}, PISA 2025, explorer, "
        f"IHSG, rupiah, dan MSCI Indonesia; {prof_math_idn_d}% siswa di bawah ambang minimum."
    )
    OGALT = "Kartu pratinjau indeks Dashboard Data Publik: katalog gempa, PISA 2025, dan tiga pelat pasar (IHSG, rupiah, MSCI Indonesia)."

    if len(TITLE) > 60:
        return False, f"title exceeds 60 characters (actual: {len(TITLE)}, expected: <= 60)"
    if len(DESC) > 155:
        return False, f"description exceeds 155 characters (actual: {len(DESC)}, expected: <= 155)"
    for name, s in [("TITLE", TITLE), ("DESC", DESC), ("OGALT", OGALT)]:
        if "\u2014" in s:
            return False, f"{name} contains U+2014 em dash"

    gempa_total_d = gempa_fig.get("total", {}).get("d", "")
    if gempa_total_d and (gempa_total_d in TITLE or gempa_total_d in DESC):
        return False, f"earthquake total ({gempa_total_d!r}) found in hub TITLE or DESC"
    raw_events_d = gempa_fig.get("raw_events", {}).get("d", "")
    if raw_events_d and (raw_events_d in TITLE or raw_events_d in DESC):
        return False, f"earthquake raw_events ({raw_events_d!r}) found in hub TITLE or DESC"

    title_nums = re.findall(r"\b\d+(?:[.,]\d+)?\b", TITLE)
    if title_nums != ["2025"]:
        return False, f"title numeric tokens mismatch (actual: {title_nums}, expected: ['2025'])"

    desc_nums = re.findall(r"\b\d+(?:[.,]\d+)?\b", DESC)
    expected_desc_nums = [gempa_first_year, "2025", prof_math_idn_d]
    if desc_nums != expected_desc_nums:
        return False, f"description numeric tokens mismatch (actual: {desc_nums}, expected: {expected_desc_nums})"

    website = {
        "@type": "WebSite",
        "name": h1_text,
        "url": CANONICAL,
        "inLanguage": "id",
        "description": DESC,
    }
    list_items = []
    for pos, (href, name) in enumerate(plate_entries, 1):
        plate_url = f"{SITE}{href}" if href.startswith("/") else href
        list_items.append({
            "@type": "ListItem",
            "position": pos,
            "name": name,
            "url": plate_url,
        })
    collection_page = {
        "@type": "CollectionPage",
        "name": TITLE,
        "url": CANONICAL,
        "inLanguage": "id",
        "description": DESC,
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": list_items,
        },
    }
    exp_graph = {
        "@context": "https://schema.org",
        "@graph": [website, collection_page],
    }

    m_script = re.search(r'<script type="application/ld\+json">(.*?)</script>', actual_block)
    if not m_script:
        return False, 'index.html SEO block missing <script type="application/ld+json">'
    try:
        act_graph = json.loads(m_script.group(1))
    except Exception as e:
        return False, f"index.html JSON-LD invalid JSON: {e}"

    if act_graph.get("@context") != exp_graph["@context"]:
        return False, f"JSON-LD @context mismatch (actual: {act_graph.get('@context')!r}, expected: {exp_graph['@context']!r})"
    act_nodes = act_graph.get("@graph", [])
    exp_nodes = exp_graph["@graph"]
    if len(act_nodes) != len(exp_nodes):
        return False, f"JSON-LD @graph length mismatch (actual: {len(act_nodes)}, expected: {len(exp_nodes)})"
    for i, (an, en) in enumerate(zip(act_nodes, exp_nodes)):
        if an.get("@type") != en.get("@type"):
            return False, f"JSON-LD node {i} @type mismatch (actual: {an.get('@type')!r}, expected: {en.get('@type')!r})"
        if set(an.keys()) != set(en.keys()):
            return False, f"JSON-LD node {i} field set mismatch (actual: {sorted(an.keys())}, expected: {sorted(en.keys())})"
        for k in en:
            if an[k] != en[k]:
                return False, f"JSON-LD node {i} field '{k}' mismatch (actual: {an[k]!r}, expected: {en[k]!r})"

    cp_node = act_nodes[1]
    item_list = cp_node.get("mainEntity", {}).get("itemListElement", [])
    if len(item_list) != len(plate_entries):
        return False, f"JSON-LD ItemList length mismatch (actual: {len(item_list)}, expected: {len(plate_entries)})"
    for pos, (item, (href, exp_name)) in enumerate(zip(item_list, plate_entries), 1):
        if item.get("name") != exp_name:
            return False, f"JSON-LD ListItem {pos} name mismatch (actual: {item.get('name')!r}, expected: {exp_name!r})"
        exp_url = f"{SITE}{href}" if href.startswith("/") else href
        if item.get("url") != exp_url:
            return False, f"JSON-LD ListItem {pos} url mismatch (actual: {item.get('url')!r}, expected: {exp_url!r})"
        if item.get("position") != pos:
            return False, f"JSON-LD ListItem {pos} position mismatch (actual: {item.get('position')!r}, expected: {pos})"

    required_icons = [
        '<link rel="icon" href="/favicon.ico" sizes="32x32">',
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        '<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">',
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
    ]
    for icon in required_icons:
        if icon not in actual_block:
            return False, f"index.html SEO block missing icon link (actual: missing, expected: {icon!r})"

    exp_jsonld = json.dumps(exp_graph, ensure_ascii=True, separators=(",", ":"))
    expected_lines = [
        "  <!--SEO:BEGIN-->",
        f"  <title>{html_esc(TITLE)}</title>",
        f'  <meta name="description" content="{html_esc(DESC)}">',
        f'  <link rel="canonical" href="{html_esc(CANONICAL)}">',
        f'  <meta name="theme-color" content="{html_esc(theme)}">',
        '  <meta property="og:type" content="website">',
        f'  <meta property="og:url" content="{html_esc(CANONICAL)}">',
        f'  <meta property="og:title" content="{html_esc(TITLE)}">',
        f'  <meta property="og:description" content="{html_esc(DESC)}">',
        f'  <meta property="og:image" content="{SITE}/assets/og/{SLUG}.png">',
        '  <meta property="og:image:width" content="1200">',
        '  <meta property="og:image:height" content="630">',
        f'  <meta property="og:image:alt" content="{html_esc(OGALT)}">',
        '  <meta property="og:site_name" content="Dashboard Data Publik">',
        '  <meta property="og:locale" content="id_ID">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f'  <meta name="twitter:title" content="{html_esc(TITLE)}">',
        f'  <meta name="twitter:description" content="{html_esc(DESC)}">',
        f'  <meta name="twitter:image" content="{SITE}/assets/og/{SLUG}.png">',
        '  <link rel="icon" href="/favicon.ico" sizes="32x32">',
        '  <link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        '  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">',
        '  <link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        f'  <script type="application/ld+json">{exp_jsonld}</script>',
        "  <!--SEO:END-->",
    ]
    actual_lines = actual_block.splitlines()
    if len(actual_lines) != len(expected_lines):
        return False, f"index.html SEO block line count mismatch (actual: {len(actual_lines)}, expected: {len(expected_lines)})"
    for idx, (act_l, exp_l) in enumerate(zip(actual_lines, expected_lines)):
        if act_l != exp_l:
            return False, f"index.html SEO block line {idx + 1} mismatch (actual: {act_l!r}, expected: {exp_l!r})"

    return True, "C2 head kit region, tags, attributes, copy constraints, icon links, and C5 JSON-LD graph verified"


def check_9_sitemap(sitemap_path: str, gempa_data_path: str, pisa_index_path: str):
    if not os.path.exists(sitemap_path):
        return False, f"sitemap.xml not found at {sitemap_path}"

    try:
        tree = ET.parse(sitemap_path)
        root = tree.getroot()
    except Exception as e:
        return False, f"sitemap.xml XML parse error: {e}"

    for elem in root.iter():
        tag_local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag_local in ("priority", "changefreq"):
            return False, f"sitemap.xml contains forbidden <{tag_local}> tag"

    discovered_locs = set()
    if os.path.isfile(os.path.join(ROOT, "index.html")):
        discovered_locs.add(f"{SITE}/")
    for entry in sorted(os.listdir(ROOT)):
        if entry.startswith(".") or entry.startswith("_"):
            continue
        dir_path = os.path.join(ROOT, entry)
        if not os.path.isdir(dir_path):
            continue
        if os.path.isfile(os.path.join(dir_path, "index.html")):
            discovered_locs.add(f"{SITE}/{entry}/")
        for page in sorted(os.listdir(dir_path)):
            if not page.endswith(".html") or page.endswith(".template.html") or page == "index.html":
                continue
            discovered_locs.add(f"{SITE}/{entry}/{page}")

    url_elems = [e for e in root if (e.tag.split("}")[-1] if "}" in e.tag else e.tag) == "url"]
    if not url_elems:
        return False, "sitemap.xml contains no <url> entries"

    sitemap_locs = []
    sitemap_map = {}
    for idx, u in enumerate(url_elems, 1):
        loc_elem = None
        lastmod_elem = None
        for child in u:
            c_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if c_tag == "loc":
                loc_elem = child
            elif c_tag == "lastmod":
                lastmod_elem = child
        if loc_elem is None or not loc_elem.text:
            return False, f"sitemap.xml <url> #{idx} missing <loc>"
        if lastmod_elem is None or not lastmod_elem.text:
            return False, f"sitemap.xml <url> #{idx} missing <lastmod>"
        loc_url = loc_elem.text.strip()
        lastmod_val = lastmod_elem.text.strip()
        sitemap_locs.append(loc_url)
        sitemap_map[loc_url] = lastmod_val

    if set(sitemap_locs) != discovered_locs:
        return False, f"sitemap.xml <loc> set mismatch (actual: {sorted(set(sitemap_locs))}, expected: {sorted(discovered_locs)})"

    if not os.path.exists(gempa_data_path):
        return False, f"gempa/data.json not found to re-derive lastmod: {gempa_data_path}"
    with open(gempa_data_path, "r", encoding="utf-8") as f:
        gempa_json = json.load(f)
    gempa_span_last = gempa_json.get("fig", {}).get("span_last_wib", {}).get("r", "")
    gempa_lastmod = gempa_span_last[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", gempa_lastmod):
        return False, f"gempa re-derived lastmod invalid format: {gempa_lastmod!r}"

    if not os.path.exists(pisa_index_path):
        return False, f"pisa/index.html not found to re-derive lastmod: {pisa_index_path}"
    with open(pisa_index_path, "r", encoding="utf-8") as f:
        pisa_html = f.read()
    m_pisa_data = re.search(r"const DATA = (.*?);\n", pisa_html)
    if not m_pisa_data:
        m_pisa_data = re.search(r"const DATA = (.*?);", pisa_html)
    if not m_pisa_data:
        return False, "could not find const DATA in pisa/index.html to re-derive lastmod"
    try:
        pisa_data = json.loads(m_pisa_data.group(1))
    except Exception as e:
        return False, f"failed to parse const DATA in pisa/index.html: {e}"
    pisa_lastmod = pisa_data.get("meta", {}).get("retrieved", "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", pisa_lastmod):
        return False, f"pisa re-derived lastmod invalid format: {pisa_lastmod!r}"

    root_lastmod = max(gempa_lastmod, pisa_lastmod)

    pasar_lastmods = {}
    for name in ("ihsg", "rupiah", "msci"):
        payload_path = os.path.join(ROOT, "data", "pasar", f"{name}.json")
        if not os.path.isfile(payload_path):
            return False, f"data/pasar/{name}.json tidak ada untuk menurunkan lastmod"
        with open(payload_path, "r", encoding="utf-8") as pf:
            pasar_payload = json.load(pf)
        as_of = (pasar_payload.get("meta") or {}).get("as_of", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of):
            return False, f"data/pasar/{name}.json meta.as_of tidak sah: {as_of!r}"
        pasar_lastmods[f"{SITE}/pasar/{name}.html"] = as_of

    expected_lastmods = {
        f"{SITE}/": root_lastmod,
        f"{SITE}/gempa/": gempa_lastmod,
        f"{SITE}/pisa/": pisa_lastmod,
    }
    expected_lastmods.update(pasar_lastmods)

    for loc, exp_lm in expected_lastmods.items():
        if loc in sitemap_map and sitemap_map[loc] != exp_lm:
            return False, f"sitemap.xml <lastmod> for {loc} mismatch (actual: {sitemap_map[loc]!r}, expected: {exp_lm!r})"

    expected_loc_order = [f"{SITE}/"] + sorted(loc for loc in discovered_locs if loc != f"{SITE}/")
    if sitemap_locs != expected_loc_order:
        return False, f"sitemap.xml url ordering mismatch (actual: {sitemap_locs}, expected: {expected_loc_order})"

    return True, f"sitemap.xml parsed with ElementTree: {len(sitemap_locs)} URLs matching repo discovery, re-derived lastmods verified, no priority/changefreq"


def check_10_robots(robots_path: str):
    if not os.path.exists(robots_path):
        return False, f"robots.txt not found at {robots_path}"

    with open(robots_path, "rb") as f:
        actual_bytes = f.read()

    expected_bytes = (
        b"User-agent: *\n"
        b"Allow: /\n"
        b"Content-Signal: search=yes, ai-input=yes, ai-train=yes\n"
        b"Sitemap: https://dashboard.idzharulhuda.com/sitemap.xml\n"
    )

    if actual_bytes != expected_bytes:
        return False, f"robots.txt byte mismatch (actual: {actual_bytes!r}, expected: {expected_bytes!r})"

    return True, "robots.txt byte-equal to four-line contract body"


def decode_png(png_bytes: bytes):
    """Decode PNG image using Python standard library only (struct + zlib + scanline unfilter)."""
    if len(png_bytes) < 8 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature")

    pos = 8
    ihdr_data = None
    idat_chunks = []

    while pos < len(png_bytes):
        if pos + 8 > len(png_bytes):
            raise ValueError("Truncated PNG chunk header")
        length, = struct.unpack(">I", png_bytes[pos:pos + 4])
        chunk_type = png_bytes[pos + 4:pos + 8]
        chunk_data = png_bytes[pos + 8:pos + 8 + length]
        if pos + 12 + length > len(png_bytes):
            raise ValueError("Truncated PNG chunk data")
        pos += 12 + length

        if chunk_type == b"IHDR":
            ihdr_data = chunk_data
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if not ihdr_data:
        raise ValueError("Missing IHDR chunk")

    width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr_data)
    if bit_depth != 8:
        raise ValueError(f"Unsupported bit depth: {bit_depth}")
    if interlace != 0:
        raise ValueError("Interlaced PNG not supported")

    if color_type == 2:    # RGB
        bpp = 3
    elif color_type == 6:  # RGBA
        bpp = 4
    elif color_type == 0:  # Grayscale
        bpp = 1
    elif color_type == 4:  # Grayscale + Alpha
        bpp = 2
    else:
        raise ValueError(f"Unsupported color type: {color_type}")

    decomp = zlib.decompress(b"".join(idat_chunks))
    line_bytes = width * bpp
    stride = line_bytes + 1
    expected_decomp_len = height * stride
    if len(decomp) != expected_decomp_len:
        raise ValueError(f"Decompressed length mismatch: {len(decomp)} != {expected_decomp_len}")

    prior = bytearray(line_bytes)
    rows = []

    for y in range(height):
        offset = y * stride
        ftype = decomp[offset]
        filt_row = decomp[offset + 1:offset + 1 + line_bytes]
        recon = bytearray(line_bytes)

        if ftype == 0:  # None
            recon[:] = filt_row
        elif ftype == 1:  # Sub
            for x in range(bpp):
                recon[x] = filt_row[x]
            for x in range(bpp, line_bytes):
                recon[x] = (filt_row[x] + recon[x - bpp]) & 0xff
        elif ftype == 2:  # Up
            for x in range(line_bytes):
                recon[x] = (filt_row[x] + prior[x]) & 0xff
        elif ftype == 3:  # Average
            for x in range(bpp):
                recon[x] = (filt_row[x] + (prior[x] >> 1)) & 0xff
            for x in range(bpp, line_bytes):
                recon[x] = (filt_row[x] + ((recon[x - bpp] + prior[x]) >> 1)) & 0xff
        elif ftype == 4:  # Paeth
            for x in range(bpp):
                recon[x] = (filt_row[x] + prior[x]) & 0xff
            for x in range(bpp, line_bytes):
                a = recon[x - bpp]
                b = prior[x]
                c = prior[x - bpp]
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                recon[x] = (filt_row[x] + pr) & 0xff
        else:
            raise ValueError(f"Unknown filter type: {ftype}")

        prior = recon
        rows.append(recon)

    return width, height, color_type, bpp, rows


def load_build_og_spec_line():
    candidate_paths = [
        os.path.join(ROOT, "pipeline", "build_og.py"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline", "build_og.py"),
        "/root/projects/dashboard/pipeline/build_og.py",
    ]
    for p in candidate_paths:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("build_og", p)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "spec_line"):
                    return mod.spec_line
    raise RuntimeError("Could not find pipeline/build_og.py to import spec_line")


def check_11_og_images():
    targets = [
        ("hub", os.path.join(ROOT, "index.html")),
        ("gempa", os.path.join(ROOT, "gempa", "index.html")),
        ("pisa", os.path.join(ROOT, "pisa", "index.html")),
        ("pisa-explorer", os.path.join(ROOT, "pisa-explorer", "index.html")),
    ]
    og_dir = os.path.join(ROOT, "assets", "og")
    get_spec_line = load_build_og_spec_line()

    for slug, html_path in targets:
        sidecar_path = os.path.join(og_dir, f"{slug}.srcsha")
        png_path = os.path.join(og_dir, f"{slug}.png")

        if not os.path.exists(html_path):
            return False, f"source page for {slug} not found at {html_path}"
        with open(html_path, "rb") as f:
            html_bytes = f.read()
        expected_sha = hashlib.sha256(html_bytes + b"\0" + get_spec_line(slug).encode("utf-8")).hexdigest()

        if not os.path.exists(sidecar_path):
            return False, f"OG sidecar for {slug} not found at {sidecar_path}"
        with open(sidecar_path, "r", encoding="utf-8") as sf:
            sidecar_sha = sf.read().strip()
        if sidecar_sha != expected_sha:
            return False, f"OG sidecar sha256 mismatch for {slug} (actual: {sidecar_sha}, expected: {expected_sha})"

        if not os.path.exists(png_path):
            return False, f"OG image for {slug} not found at {png_path}"
        size = os.path.getsize(png_path)
        if not (15000 <= size <= 300000):
            return False, f"OG image size for {slug} out of bounds: {size} bytes (expected 15000 <= bytes <= 300000)"

        with open(png_path, "rb") as f:
            png_bytes = f.read()

        try:
            width, height, color_type, bpp, rows = decode_png(png_bytes)
        except Exception as exc:
            return False, f"failed to decode OG PNG for {slug}: {exc}"

        if width != 1200 or height != 630:
            return False, f"OG image dimensions for {slug} mismatch: {width}x{height} (expected 1200x630)"

        colors = set()
        lums = []
        gempa_ink_samples = 0
        hero_bg = (168, 63, 20)
        slot_bg = (143, 52, 16)

        for y in range(0, height, 4):
            row = rows[y]
            for x in range(0, width, 4):
                idx = x * bpp
                if bpp >= 3:
                    r, g, b = row[idx], row[idx + 1], row[idx + 2]
                else:
                    r = g = b = row[idx]
                colors.add((r, g, b))
                lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
                lums.append(lum)
                if slug == "gempa" and x >= 480:
                    if (r, g, b) != hero_bg and (r, g, b) != slot_bg:
                        gempa_ink_samples += 1

        if len(colors) < 64:
            return False, f"OG image {slug} distinct RGB colours on step-4 sample: {len(colors)} < 64"

        lum_range = max(lums) - min(lums)
        if lum_range < 60.0:
            return False, f"OG image {slug} luminance range: {lum_range:.1f} < 60.0"

        if slug == "gempa" and gempa_ink_samples < 400:
            return False, f"OG image gempa ink samples on right-hand 60% (x >= 480): {gempa_ink_samples} < 400"

    return True, "all 4 OG images (hub, gempa, pisa, pisa-explorer) satisfy C8 gate bands and sidecar sha256"


def main():
    template_path = os.path.join(ROOT, "hub.template.html")
    annex_prof_path = os.path.join(ROOT, "data", "pisa", "annex_proficiency.csv")
    gempa_data_path = os.path.join(ROOT, "gempa", "data.json")
    pisa_index_path = os.path.join(ROOT, "pisa", "index.html")
    explorer_payload_path = os.path.join(ROOT, "pisa-explorer", "data.json")
    fonts_dir = os.path.join(ROOT, "fonts")
    index_path = os.path.join(ROOT, "index.html")
    sitemap_path = os.path.join(ROOT, "sitemap.xml")
    robots_path = os.path.join(ROOT, "robots.txt")

    required_files = [
        (annex_prof_path, "data/pisa/annex_proficiency.csv"),
        (gempa_data_path, "gempa/data.json"),
        (pisa_index_path, "pisa/index.html"),
        (explorer_payload_path, "pisa-explorer/data.json"),
        (template_path, "hub.template.html"),
        (index_path, "index.html"),
        (sitemap_path, "sitemap.xml"),
        (robots_path, "robots.txt"),
    ]

    for path, name in required_files:
        if not os.path.exists(path):
            print(f"FAIL Input file missing: {name} (actual: not found at {path}, expected: file present)")
            print("TOTAL 0/11")
            sys.exit(1)

    with open(index_path, "r", encoding="utf-8") as f:
        index_content = f.read()

    prefix = "const DATA = "
    idx = index_content.find(prefix)
    if idx == -1:
        print("FAIL Embedded DATA not found in index.html")
        print("TOTAL 0/11")
        sys.exit(1)

    start_json = idx + len(prefix)
    end_json = index_content.find(";\n", start_json)
    if end_json == -1:
        end_json = index_content.find(";", start_json)
    if end_json == -1:
        print("FAIL Embedded DATA end delimiter not found in index.html")
        print("TOTAL 0/11")
        sys.exit(1)

    try:
        embedded_data = json.loads(index_content[start_json:end_json])
    except Exception as e:
        print(f"FAIL Embedded DATA JSON parse failed: {e}")
        print("TOTAL 0/11")
        sys.exit(1)

    checks = [
        ("stamp", lambda: check_1_stamp(index_path, template_path, annex_prof_path, gempa_data_path, fonts_dir, explorer_payload_path)),
        ("payload rebuilt", lambda: check_2_payload_rebuilt(embedded_data, annex_prof_path, explorer_payload_path)),
        ("field provenance", lambda: check_3_field_provenance(embedded_data, gempa_data_path)),
        ("fallback literals", lambda: check_4_fallback_literals(index_content, embedded_data)),
        ("skeleton", lambda: check_5_skeleton(index_path, template_path)),
        ("font region", lambda: check_6_font_region(index_path, fonts_dir)),
        ("hygiene", lambda: check_7_hygiene(index_content, gempa_data_path)),
        ("head kit and JSON-LD", lambda: check_8_head_and_jsonld(index_path, template_path, gempa_data_path, embedded_data)),
        ("sitemap", lambda: check_9_sitemap(sitemap_path, gempa_data_path, pisa_index_path)),
        ("robots", lambda: check_10_robots(robots_path)),
        ("og images", lambda: check_11_og_images()),
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
