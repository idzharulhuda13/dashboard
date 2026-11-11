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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dateline import long_date, stamp as dateline_stamp  # noqa: E402

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


def fmt_signed(val, decimals=2):
    return ("+" if val > 0 else "") + fmt_num(val, decimals)


RETURN_ROWS = (
    ("1m", "Oktober saja"),
    ("3m", "3 bulan terakhir"),
    ("ytd", "2026 sampai akhir Oktober"),
    ("1y", "12 bulan terakhir"),
    ("3y", "Rata-rata per tahun, 3 tahun"),
    ("5y", "Rata-rata per tahun, 5 tahun"),
    ("10y", "Rata-rata per tahun, 10 tahun"),
)

VALUATION_ROWS = (
    ("pe", "P/E: harga untuk Rp1 laba setahun", "Rp"),
    ("pe_fwd", "P/E perkiraan: harga untuk Rp1 laba tahun depan", "Rp"),
    ("pbv", "P/BV: harga untuk Rp1 aset bersih", "Rp"),
)

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
    top5_weight = round(sum(c["weight_pct"] for c in constituents_raw[:5]), 2)
    # Dijumlah dari baris, bukan 100 dikurangi: total factsheet kena pembulatan (100,02 / 100,01).
    nonbank_weight = round(sum(c["weight_pct"] for c in constituents_raw if "Bank" not in c["name"]), 2)
    nonfin_sector = round(sum(s["weight_pct"] for s in sectors_raw if s["sector"] != "Financials"), 2)
    sim_drop_pct = 10
    sim_index_exact = round(bank_weight * (sim_drop_pct / 100.0), 3)
    sim_index_drop = round(sim_index_exact, 1)

    factsheet_cap = meta_raw["index_float_cap_bn"]
    as_of = meta_raw["as_of"]
    as_of_long = long_date(as_of)

    perf = raw_data["performance"]
    fund = raw_data["fundamentals"]
    cum = raw_data["cumulative"]
    prev = raw_data["prev"]
    ojk = raw_data["context"]["ojk"]
    rel = raw_data["context"]["msci_release"]
    # Hasil Index Review November: nama di daftar MSCI (huruf besar) dicocokkan ke nama di factsheet.
    review_res = rel.get("result") or {}
    out_names = {n.upper() for n in review_res.get("deletions", [])}
    by_upper = {c["name"].upper(): c for c in constituents_raw}
    if not out_names <= set(by_upper):
        print(f"FATAL: deletions not in factsheet constituents: {sorted(out_names - set(by_upper))}")
        sys.exit(1)
    if review_res.get("additions"):
        print("FATAL: additions present; page copy assumes none")
        sys.exit(1)
    out_rows = [c for c in constituents_raw if c["name"].upper() in out_names]
    out_count = len(out_rows)
    out_weight = round(sum(c["weight_pct"] for c in out_rows), 2)
    remain_count = len(constituents_raw) - out_count

    pe_change = round((fund["indonesia"]["pe"] / prev["pe"] - 1) * 100, 2)
    # Laba trailing tersirat: harga turun 1 Mo, P/E turun pe_change, sisanya laba.
    eps_change = round(((1 + perf["indonesia"]["1m"] / 100) / (1 + pe_change / 100) - 1) * 100, 2)
    pe_vs_em = round(fund["indonesia"]["pe"] / fund["em"]["pe"] * 100)
    ytd_gap = round(perf["em"]["ytd"] - perf["indonesia"]["ytd"], 2)
    cum_multiple = round(cum["em"] / cum["indonesia"], 1)

    def f(r, d):
        return {"r": r, "d": d}

    def pct(v, dp=2):
        return f(v, f"{fmt_signed(v, dp)}%")

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
            "is_bank": is_bank,
            "is_out": name.upper() in out_names
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

    series = (("indonesia", "Indonesia"), ("em", "Negara berkembang"), ("acwi_imi", "Global"))
    returns_rows = []
    for key, label in RETURN_ROWS:
        returns_rows.append({
            "key": key, "label": label,
            "values": [{"series": s, "name": n, "r": perf[s][key], "d": f"{fmt_signed(perf[s][key], 2)}%"}
                       for s, n in series],
        })
    cumulative_rows = [{"series": s, "name": n, "r": cum[s], "d": f"US${fmt_num(cum[s], 2)}"} for s, n in series]
    valuation = []
    for key, title, unit in VALUATION_ROWS:
        valuation.append({
            "key": key, "title": title, "unit": unit,
            "values": [{"series": s, "name": n, "r": fund[s][key], "d": fmt_num(fund[s][key], 2)} for s, n in series],
        })

    def wib(stamp):
        day, hm = stamp.split(" ")
        return f"{long_date(day)}, {hm.replace(':', '.')} WIB"

    timeline = [
        {"date": rel["consultation"]["date"], "d": long_date(rel["consultation"]["date"]),
         "text": "MSCI membuka konsultasi untuk menurunkan Indonesia ke Standalone."},
        {"date": rel["review"]["announce_wib"][:10], "d": wib(rel["review"]["announce_wib"]), "done": bool(review_res),
         "text": (f"Hasil Index Review November: tidak ada saham masuk, {out_count} dikeluarkan "
                  f"({' dan '.join(c['name'] for c in out_rows)}).") if review_res else
                 "Hasil Index Review November diumumkan (11 November, 23.00 waktu Eropa Tengah)."},
        {"date": rel["review"]["effective_close"], "d": long_date(rel["review"]["effective_close"]),
         "text": (f"Perubahan berlaku saat penutupan bursa; indeks tinggal {remain_count} saham."
                  if review_res else "Perubahan isi indeks berlaku saat penutupan bursa.")},
        {"date": rel["consultation"]["feedback_until"], "d": long_date(rel["consultation"]["feedback_until"]),
         "text": "Batas akhir masukan untuk konsultasi."},
        {"date": rel["consultation"]["decision_by"], "d": long_date(rel["consultation"]["decision_by"]),
         "text": "Keputusan MSCI paling lambat."},
    ]

    # Prepare structured DATA dictionary
    DATA = {
        "meta": {
            "index": meta_raw["index"],
            "as_of": as_of,
            "as_of_formatted": as_of_long,
            "constituents_count": len(constituents_raw),
            "coverage_pct": meta_raw["coverage_pct"],
            "index_float_cap_bn": factsheet_cap,
            "source_pdf": f"MSCI Indonesia Index Factsheet (USD), {as_of_long}",
            "source_prev": f"MSCI Indonesia Index Factsheet (USD), {long_date(prev['as_of'])}, untuk P/E dan P/BV bulan sebelumnya",
            "source_ojk": ojk["source"],
            "source_sha256": meta_raw["source_sha256"],
            "scope_note": "MSCI Indonesia Index (USD, net), bukan IHSG. Bukan rekomendasi investasi.",
            "method_note": "return, valuasi, porsi, dan nilai pasar diambil apa adanya dari factsheet; total porsi empat bank dan lima saham teratas dijumlah dari baris saham"
        },
        "fig": {
            "as_of_formatted": {"d": as_of_long},
            "prev_as_of_formatted": {"d": long_date(prev["as_of"])},
            "cum_id": f(cum["indonesia"], f"US${fmt_num(cum['indonesia'], 2)}"),
            "cum_em": f(cum["em"], f"US${fmt_num(cum['em'], 2)}"),
            "cum_acwi": f(cum["acwi_imi"], f"US${fmt_num(cum['acwi_imi'], 2)}"),
            "cum_multiple": f(cum_multiple, fmt_num(cum_multiple, 1)),
            "ytd_id": pct(perf["indonesia"]["ytd"]),
            "ytd_em": pct(perf["em"]["ytd"]),
            "ytd_acwi": pct(perf["acwi_imi"]["ytd"]),
            "ytd_gap": f(ytd_gap, fmt_num(ytd_gap, 2)),
            "m1_id": pct(perf["indonesia"]["1m"]),
            "m1_em": pct(perf["em"]["1m"]),
            "y10_id": pct(perf["indonesia"]["10y"]),
            "y10_em": pct(perf["em"]["10y"]),
            "pe_id": f(fund["indonesia"]["pe"], fmt_num(fund["indonesia"]["pe"], 2)),
            "pe_em": f(fund["em"]["pe"], fmt_num(fund["em"]["pe"], 2)),
            "pe_acwi": f(fund["acwi_imi"]["pe"], fmt_num(fund["acwi_imi"]["pe"], 2)),
            "pe_prev": f(prev["pe"], fmt_num(prev["pe"], 2)),
            "pe_change": pct(pe_change),
            "eps_change": pct(eps_change),
            "pe_vs_em": f(pe_vs_em, str(pe_vs_em)),
            "div_yld_id": f(fund["indonesia"]["div_yld"], f"{fmt_num(fund['indonesia']['div_yld'], 2)}%"),
            "ojk_sell": f(ojk["net_sell_saham_t"], f"Rp{fmt_num(ojk['net_sell_saham_t'], 2)} T"),
            "ojk_sell_sep": f(ojk["net_sell_saham_sep_t"], f"Rp{fmt_num(ojk['net_sell_saham_sep_t'], 2)} T"),
            "ojk_sbn": f(ojk["net_buy_sbn_t"], f"Rp{fmt_num(ojk['net_buy_sbn_t'], 2)} T"),
            "ojk_sbn_sep": f(ojk["net_sell_sbn_sep_t"], f"Rp{fmt_num(ojk['net_sell_sbn_sep_t'], 2)} T"),
            "ojk_inv_new": f(ojk["investor_new_jt"], f"{fmt_num(ojk['investor_new_jt'], 2)} juta"),
            "ojk_inv_total": f(ojk["investor_total_jt"], f"{fmt_num(ojk['investor_total_jt'], 2)} juta"),
            "ojk_ritel": f(ojk["ritel_rnth_pct"], f"{fmt_num(ojk['ritel_rnth_pct'], 2)}%"),
            "ojk_ritel_sep": f(ojk["ritel_rnth_sep_pct"], f"{fmt_num(ojk['ritel_rnth_sep_pct'], 2)}%"),
            "review_wib": {"d": wib(rel["review"]["announce_wib"])},
            "review_out_count": f(out_count, str(out_count)),
            "review_out_weight_pct": f(out_weight, f"{fmt_num(out_weight, 2)}%"),
            "review_remain_count": f(remain_count, str(remain_count)),
            "review_effective": {"d": long_date(rel["review"]["effective_close"])},
            "decision_by": {"d": long_date(rel["consultation"]["decision_by"])},
            "bank_weight_pct": f(bank_weight, fmt_num(bank_weight, 2)),
            "bank_weight_pct_str": f(bank_weight, f"{fmt_num(bank_weight, 2)}%"),
            "bca_weight_pct": f(bca_weight, f"{fmt_num(bca_weight, 2)}%"),
            "nonbank_weight_pct": f(nonbank_weight, fmt_num(nonbank_weight, 2)),
            "nonfin_sector_pct": f(nonfin_sector, fmt_num(nonfin_sector, 2)),
            "top5_weight_pct": f(top5_weight, f"{fmt_num(top5_weight, 2)}%"),
            "weights_sum_pct": f(weights_sum, f"{fmt_num(weights_sum, 2)}%"),
            "sectors_sum_pct": f(sector_sum, f"{fmt_num(sector_sum, 2)} persen"),
            "sim_drop_pct": f(sim_drop_pct, str(sim_drop_pct)),
            "sim_index_exact": f(sim_index_exact, f"{fmt_num(sim_index_exact, 3)}%"),
            "sim_index_drop_pct": f(sim_index_drop, fmt_num(sim_index_drop, 1)),
            "total_float_cap_factsheet": f(factsheet_cap, fmt_num(factsheet_cap, 2)),
            "total_float_cap_sum": f(caps_sum, fmt_num(caps_sum, 2)),
            "constituents_count": f(len(constituents_raw), str(len(constituents_raw))),
            "coverage_pct": f(meta_raw["coverage_pct"], str(meta_raw["coverage_pct"])),
        },
        "cumulative": cumulative_rows,
        "returns": returns_rows,
        "valuation": valuation,
        "timeline": timeline,
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

    # 3a. Linimasa statis (terbaca tanpa JavaScript; JS hanya menambah penanda lewat/berikutnya)
    tl_marker = "<!-- populated from DATA; fallback below for no-JS -->"
    if tl_marker not in rendered_html:
        print("FATAL: timeline placeholder not found in template")
        sys.exit(1)
    import html as _h
    tl_html = "".join(
        f'<li><span class="tl-date">{_h.escape(e["d"])}</span><span class="tl-text">{_h.escape(e["text"])}</span></li>'
        for e in DATA["timeline"])
    rendered_html = rendered_html.replace(tl_marker, tl_html)

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

    rendered_html = dateline_stamp(rendered_html, as_of)

    # Add SHA256 build stamp as the very first line
    build_stamp = f"<!--BUILD sha256:{template_sha256}-->\n"
    final_output = build_stamp + rendered_html

    with open(output_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_output)

    print(f"Generated {output_path} ({len(final_output)} bytes) with sha256:{template_sha256[:16]}...")
    print("=== BUILD COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()
