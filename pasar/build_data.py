#!/usr/bin/env python3
"""
build_data.py - Compile pasar/msci.html from pasar/msci.template.html and data/pasar/msci.json.
Follows the raschlab-viz build pattern with zero external runtime dependencies.
"""

import os
import sys
import json
import base64
import hashlib

def get_base_dir():
    # Base dashboard project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, ".."))

TICKER_MAP = {
    "Bank Central Asia": "BBCA",
    "Bank Rakyat Indonesia": "BBRI",
    "Bank Mandiri": "BMRI",
    "Telkom Indonesia": "TLKM",
    "Astra International": "ASII",
    "Bank Negara Indonesia": "BBNI",
    "Bumi Resources Minerals": "BRMS",
    "United Tractors": "UNTR",
    "Barito Pacific": "BRPT"
}

SECTOR_DISPLAY_MAP = {
    "Financials": "Keuangan",
    "Comm Srvcs": "Komunikasi",
    "Industrials": "Barang industri",
    "Materials": "Bahan baku",
    "Energy": "Energi"
}

def fmt_num(val, decimals=2):
    return f"{float(val):.{decimals}f}".replace(".", ",")

def main():
    base_dir = get_base_dir()
    pasar_dir = os.path.join(base_dir, "pasar")
    data_path = os.path.join(base_dir, "data", "pasar", "msci.json")
    fonts_dir = os.path.join(base_dir, "fonts", "pasar")
    template_path = os.path.join(pasar_dir, "msci.template.html")
    output_path = os.path.join(pasar_dir, "msci.html")

    if not os.path.exists(data_path):
        print(f"FATAL: Source data not found: {data_path}")
        sys.exit(1)

    if not os.path.exists(template_path):
        print(f"FATAL: Template file not found: {template_path}")
        sys.exit(1)

    # 1. Load source json
    with open(data_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    meta_raw = raw_data.get("meta", {})
    constituents_raw = raw_data.get("constituents", [])
    sectors_raw = raw_data.get("sectors", [])

    # Recompute and verify data consistency
    weights = [c["weight_pct"] for c in constituents_raw]
    float_caps = [c["float_cap_bn"] for c in constituents_raw]
    weights_sum = round(sum(weights), 2)
    caps_sum = round(sum(float_caps), 2)

    sector_weights = [s["weight_pct"] for s in sectors_raw]
    sector_sum = round(sum(sector_weights), 2)

    banks = [c for c in constituents_raw if "Bank" in c["name"]]
    bank_weight = round(sum(c["weight_pct"] for c in banks), 2)
    bca_weight = round(constituents_raw[0]["weight_pct"], 2)
    non_bca_weight = round(100.0 - bca_weight, 2)
    top5_weight = round(sum(c["weight_pct"] for c in constituents_raw[:5]), 2)
    sim_drop_pct = 10
    sim_index_drop = round(bank_weight * (sim_drop_pct / 100.0), 1)

    factsheet_cap = meta_raw.get("index_float_cap_bn", 53.36)

    # Build constituent items
    constituents_data = []
    for idx, c in enumerate(constituents_raw):
        name = c["name"]
        ticker = TICKER_MAP.get(name, name[:4].upper())
        is_bank = "Bank" in name
        constituents_data.append({
            "rank": idx + 1,
            "rank_str": f"{idx + 1:02d}",
            "name": name,
            "ticker": ticker,
            "sector": SECTOR_DISPLAY_MAP.get(c["sector"], c["sector"]),
            "sector_short": SECTOR_DISPLAY_MAP.get(c["sector"], c["sector"]).upper(),
            "float_cap_bn": c["float_cap_bn"],
            "float_cap_bn_d": fmt_num(c["float_cap_bn"], 2),
            "weight_pct": c["weight_pct"],
            "weight_pct_d": f"{fmt_num(c['weight_pct'], 2)}%",
            "is_bank": is_bank
        })

    # Build sector items
    sectors_data = []
    for s in sectors_raw:
        sec_name = s["sector"]
        display_name = SECTOR_DISPLAY_MAP.get(sec_name, sec_name)
        sectors_data.append({
            "sector": sec_name,
            "sector_display": display_name,
            "weight_pct": s["weight_pct"],
            "weight_pct_d": f"{fmt_num(s['weight_pct'], 2)}%"
        })

    # Prepare structured DATA dictionary
    DATA = {
        "meta": {
            "index": meta_raw.get("index", "MSCI Indonesia Index (USD, net)"),
            "as_of": meta_raw.get("as_of", "2026-09-30"),
            "as_of_formatted": "30 September 2026",
            "constituents_count": len(constituents_raw),
            "coverage_pct": meta_raw.get("coverage_pct", 85),
            "index_float_cap_bn": factsheet_cap,
            "source_pdf": "MSCI Indonesia Index Factsheet, 30 September 2026",
            "source_sha256": meta_raw.get("source_sha256", ""),
            "scope_note": "MSCI Indonesia Index (USD, net), bukan IHSG.",
            "method_note": "porsi dan nilai pasar diambil dari factsheet; total sektor dijumlah dari lima baris sektor"
        },
        "fig": {
            "bank_weight_pct": {"r": bank_weight, "d": fmt_num(bank_weight, 2)},
            "bank_weight_pct_str": {"r": bank_weight, "d": f"{fmt_num(bank_weight, 2)}%"},
            "bca_weight_pct": {"r": bca_weight, "d": f"{fmt_num(bca_weight, 2)}%"},
            "bca_name": {"d": constituents_raw[0]["name"]},
            "non_bca_weight_pct": {"r": non_bca_weight, "d": f"{fmt_num(non_bca_weight, 2)}%"},
            "top5_weight_pct": {"r": top5_weight, "d": f"{fmt_num(top5_weight, 2)}%"},
            "top5_weight_pct_plain": {"r": top5_weight, "d": fmt_num(top5_weight, 2)},
            "weights_sum_pct": {"r": weights_sum, "d": f"{fmt_num(weights_sum, 2)}%"},
            "sectors_sum_pct": {"r": sector_sum, "d": f"{fmt_num(sector_sum, 2)} persen"},
            "sim_drop_pct": {"r": sim_drop_pct, "d": str(sim_drop_pct)},
            "sim_index_drop_pct": {"r": sim_index_drop, "d": fmt_num(sim_index_drop, 1)},
            "total_float_cap_factsheet": {"r": factsheet_cap, "d": fmt_num(factsheet_cap, 2)},
            "total_float_cap_sum": {"r": caps_sum, "d": fmt_num(caps_sum, 2)},
            "constituents_count": {"r": len(constituents_raw), "d": str(len(constituents_raw))},
            "coverage_pct": {"r": meta_raw.get("coverage_pct", 85), "d": str(meta_raw.get("coverage_pct", 85))},
            "as_of_formatted": {"d": "30 September 2026"}
        },
        "constituents": constituents_data,
        "sectors": sectors_data
    }

    # 2. Base64 font encoding
    font_defs = [
        ("ArchivoBlack.ttf", "Archivo Black", "900", "truetype", "font/ttf"),
        ("ArchivoBlack.ttf", "Archivo", "900", "truetype", "font/ttf"),
        ("Archivo-var.woff2", "Archivo", "100 900", "woff2", "font/woff2"),
        ("IBMPlexMono-400.woff2", "IBM Plex Mono", "400", "woff2", "font/woff2"),
        ("IBMPlexMono-500.woff2", "IBM Plex Mono", "500", "woff2", "font/woff2"),
    ]

    font_css_rules = []
    loaded_cache = {}
    for filename, family, weight, fmt, mime in font_defs:
        if filename not in loaded_cache:
            font_file_path = os.path.join(fonts_dir, filename)
            if not os.path.exists(font_file_path):
                print(f"FATAL: Font file not found: {font_file_path}")
                sys.exit(1)
            with open(font_file_path, "rb") as ff:
                loaded_cache[filename] = base64.b64encode(ff.read()).decode("ascii")

        b64 = loaded_cache[filename]
        rule = (
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f"  font-display: swap;\n"
            f"  src: url('data:{mime};base64,{b64}') format('{fmt}');\n"
            f"}}"
        )
        font_css_rules.append(rule)

    fonts_replacement = "\n".join(font_css_rules)

    # 3. Read template and assert placeholders
    with open(template_path, "rb") as tf:
        template_bytes = tf.read()

    template_sha256 = hashlib.sha256(template_bytes).hexdigest()
    template_str = template_bytes.decode("utf-8")

    placeholder_data = "/*__DATA__*/ {}"
    placeholder_fonts = "/*__FONTS__*/"

    if placeholder_data not in template_str:
        print(f"FATAL: Placeholder '{placeholder_data}' not found in template")
        sys.exit(1)

    if placeholder_fonts not in template_str:
        print(f"FATAL: Placeholder '{placeholder_fonts}' not found in template")
        sys.exit(1)

    # Inject
    compact_json = json.dumps(DATA, ensure_ascii=False)
    rendered_html = template_str.replace(placeholder_data, compact_json)
    rendered_html = rendered_html.replace(placeholder_fonts, fonts_replacement)

    # 3b. Fill data-fig fallbacks so a reader without JavaScript sees the same values
    import re as _re
    import html as _html

    fig_map = DATA["fig"]
    meta_map = DATA["meta"]

    def _display_for(key):
        if key in fig_map:
            return str(fig_map[key]["d"])
        if key in meta_map:
            return str(meta_map[key])
        return None

    def _fill_fig(match):
        key = match.group("key")
        value = _display_for(key)
        if value is None:
            print(f"FATAL: data-fig '{key}' has no payload entry")
            sys.exit(1)
        return match.group("open") + _html.escape(value) + match.group("close")

    rendered_html, filled = _re.subn(
        r'(?P<open><(?P<tag>\w+)[^>]*data-fig="(?P<key>[^"]+)"[^>]*>)(?P<close></(?P=tag)>)',
        _fill_fig,
        rendered_html,
    )
    slots = rendered_html.count('data-fig="')
    if filled != slots:
        print(f"FATAL: {slots} data-fig slots but {filled} filled; a slot has markup this builder cannot fill")
        sys.exit(1)
    print(f"Filled {filled} data-fig fallbacks")

    # Add SHA256 build stamp as the very first line
    build_stamp = f"<!--BUILD sha256:{template_sha256}-->\n"
    final_output = build_stamp + rendered_html

    with open(output_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_output)

    print(f"Generated {output_path} ({len(final_output)} bytes) with sha256:{template_sha256[:16]}...")
    print("=== BUILD COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()
