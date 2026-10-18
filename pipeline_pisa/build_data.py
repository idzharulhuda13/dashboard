#!/usr/bin/env python3
"""Build generator for PISA plate 02.

Standard library only. Deterministic: frozen inputs, no wall clock,
no network, no randomness.
"""

import base64
import csv
import glob
import hashlib
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_int(n: int) -> str:
    """Format integer with '.' thousands separator for values >= 1000."""
    return f"{n:,}".replace(",", ".")


def fmt_float(v: float, dp: int = 1) -> str:
    """Format float with ',' decimal separator, ASCII hyphen, specified decimal places."""
    s = f"{float(v):.{dp}f}"
    if s in ("-0.0", "-0"):
        s = "0.0" if dp > 0 else "0"
    return s.replace(".", ",")


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    csv_dir = os.path.join(ROOT, "data", "pisa")
    csv_paths = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    template_path = os.path.join(ROOT, "pisa.template.html")
    fonts_dir = os.path.join(ROOT, "fonts")
    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    output_dir = os.path.join(ROOT, "pisa")
    output_path = os.path.join(output_dir, "index.html")

    if not os.path.exists(template_path):
        raise RuntimeError(f"Missing template file: {template_path}")
    if len(font_paths) != 5:
        raise RuntimeError(f"Expected 5 font files in {fonts_dir}, found {len(font_paths)}")
    if not csv_paths:
        raise RuntimeError(f"No CSV files found in {csv_dir}")

    # All inputs in sorted order
    input_paths = csv_paths + [template_path] + font_paths

    # Compute sha256 stamp over all input files
    stamp_hasher = hashlib.sha256()
    for p in input_paths:
        rel_path = os.path.relpath(p, ROOT)
        with open(p, "rb") as f:
            data = f.read()
        stamp_hasher.update(rel_path.encode("utf-8"))
        stamp_hasher.update(b"\x00")
        stamp_hasher.update(data)
        print(f"{rel_path}: {len(data)} bytes")

    stamp_hex = stamp_hasher.hexdigest()
    stamp_line = f"<!--BUILD sha256:{stamp_hex}-->\n"

    # Compute individual hashes for metadata
    annex_hasher = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(csv_dir, "annex_*.csv"))):
        with open(p, "rb") as f:
            annex_hasher.update(f.read())
    annex_sha256 = annex_hasher.hexdigest()

    puf_hasher = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(csv_dir, "puf_*.csv"))):
        with open(p, "rb") as f:
            puf_hasher.update(f.read())
    puf_sha256 = puf_hasher.hexdigest()

    # Read CSVs into structured records
    # 1. annex_means.csv
    annex_means_path = os.path.join(csv_dir, "annex_means.csv")
    with open(annex_means_path, "r", encoding="utf-8") as f:
        annex_means = list(csv.DictReader(f))

    # 2. annex_trend_science.csv
    annex_trend_path = os.path.join(csv_dir, "annex_trend_science.csv")
    with open(annex_trend_path, "r", encoding="utf-8") as f:
        annex_trend = list(csv.DictReader(f))

    # 3. annex_proficiency.csv
    annex_prof_path = os.path.join(csv_dir, "annex_proficiency.csv")
    with open(annex_prof_path, "r", encoding="utf-8") as f:
        annex_prof = list(csv.DictReader(f))

    # 4. puf_quartile_cuts.csv
    puf_cuts_path = os.path.join(csv_dir, "puf_quartile_cuts.csv")
    with open(puf_cuts_path, "r", encoding="utf-8") as f:
        puf_cuts = list(csv.DictReader(f))

    # 5. puf_estimates.csv
    puf_est_path = os.path.join(csv_dir, "puf_estimates.csv")
    with open(puf_est_path, "r", encoding="utf-8") as f:
        puf_est = list(csv.DictReader(f))

    # 6. puf_counts.csv
    puf_counts_path = os.path.join(csv_dir, "puf_counts.csv")
    puf_counts = {}
    with open(puf_counts_path, "r", encoding="utf-8") as f:
        for r in csv.reader(f):
            if r and len(r) == 2:
                puf_counts[r[0]] = r[1]

    # BUILD DATA STRUCTURES
    # ---------------------
    # DATA.meta
    meta = {
        "cycle": 2025,
        "retrieved": "2026-10-13",
        "source_url": "https://www.oecd.org/en/data/datasets/pisa-2025-database.html",
        "annex_sha256": annex_sha256,
        "puf_sha256": puf_sha256,
        "licence": "CC BY 4.0",
    }

    # DATA.hero
    idn_scie_mean = None
    idn_scie_se = None
    oecd_scie_mean = None
    oecd_scie_se = None
    for r in annex_means:
        if r["domain"] == "science" and r["stat"] == "mean":
            if r["subject"] == "Indonesia":
                idn_scie_mean = float(r["value"])
                idn_scie_se = float(r["se"])
            elif r["subject"] == "OECD average":
                oecd_scie_mean = float(r["value"])
                oecd_scie_se = float(r["se"])

    if idn_scie_mean is None or oecd_scie_mean is None:
        raise RuntimeError("Failed to resolve science means for Indonesia or OECD average from annex_means.csv")

    hero = {
        "idn": {"scie": idn_scie_mean, "se": idn_scie_se},
        "oecd": {"scie": oecd_scie_mean, "se": oecd_scie_se},
        "cut_l2": 409.54,
        "cut_unit": "poin skor",
    }

    # DATA.trend
    # cycles: 2006, 2009, 2012, 2015, 2018, 2022, 2025
    trend_cycles = [2006, 2009, 2012, 2015, 2018, 2022, 2025]
    trend_by_cycle = {c: {} for c in trend_cycles}
    for r in annex_trend:
        if r["domain"] == "science" and r["stat"] == "mean":
            try:
                c = int(r["cycle"])
            except ValueError:
                continue
            if c in trend_by_cycle:
                if r["subject"] == "Indonesia":
                    trend_by_cycle[c]["idn"] = float(r["value"])
                    trend_by_cycle[c]["idn_se"] = float(r["se"])
                elif r["subject"] == "OECD average-23":
                    trend_by_cycle[c]["oecd23"] = float(r["value"])
                    trend_by_cycle[c]["oecd23_se"] = float(r["se"])

    trend = []
    for c in sorted(trend_cycles):
        d = trend_by_cycle[c]
        if "idn" not in d or "oecd23" not in d:
            raise RuntimeError(f"Missing trend data for cycle {c}")
        trend.append({
            "cycle": c,
            "idn": d["idn"],
            "idn_se": d["idn_se"],
            "oecd23": d["oecd23"],
            "oecd23_se": d["oecd23_se"],
        })

    # DATA.prof
    prof_cut_scores = {
        "science": [260.54, 334.94, 409.54, 484.14, 559.74, 633.33, 708.16],
        "reading": [189.33, 262.04, 334.75, 407.47, 480.18, 552.89, 625.61, 698.32],
        "mathematics": [233.17, 295.47, 357.77, 420.07, 482.38, 544.68, 606.99, 669.30],
    }

    prof = {}
    for domain in ["science", "reading", "mathematics"]:
        domain_rows = [r for r in annex_prof if r["domain"] == domain]
        bands_idn = []
        shares_idn = []
        shares_oecd_map = {}
        for r in domain_rows:
            if r["subject"] == "Indonesia":
                bands_idn.append(r["stat"])
                shares_idn.append(float(r["value"]))
            elif r["subject"] == "OECD average":
                shares_oecd_map[r["stat"]] = float(r["value"])

        shares_oecd = [shares_oecd_map[b] for b in bands_idn]

        # Verify level_2 exists in bands
        if "level_2" not in bands_idn:
            raise RuntimeError(f"Token 'level_2' not found in bands for domain {domain}")

        prof[domain] = {
            "bands": bands_idn,
            "cut_scores": prof_cut_scores[domain],
            "idn": shares_idn,
            "oecd": shares_oecd,
        }

    # DATA.spread
    spread_stats = ["sd", "p10", "p25", "p50", "p75", "p90"]
    spread_idn = {}
    spread_oecd = {}
    for r in annex_means:
        if r["domain"] == "science" and r["stat"] in spread_stats:
            if r["subject"] == "Indonesia":
                spread_idn[r["stat"]] = float(r["value"])
            elif r["subject"] == "OECD average":
                spread_oecd[r["stat"]] = float(r["value"])

    for st in spread_stats:
        if st not in spread_idn or st not in spread_oecd:
            raise RuntimeError(f"Missing spread stat '{st}' in annex_means.csv")

    spread = {
        "idn": {
            "sd": spread_idn["sd"],
            "p10": spread_idn["p10"],
            "p25": spread_idn["p25"],
            "p50": spread_idn["p50"],
            "p75": spread_idn["p75"],
            "p90": spread_idn["p90"],
        },
        "oecd": {
            "sd": spread_oecd["sd"],
            "p10": spread_oecd["p10"],
            "p25": spread_oecd["p25"],
            "p50": spread_oecd["p50"],
            "p75": spread_oecd["p75"],
            "p90": spread_oecd["p90"],
        },
    }

    # DATA.escs
    # cuts from puf_quartile_cuts.csv
    idn_cuts = []
    oecd_cuts = []
    for r in puf_cuts:
        if r["pop"] == "idn":
            idn_cuts.append((r["quartile"], float(r["escs_cut"])))
        elif r["pop"] == "oecd":
            oecd_cuts.append((r["quartile"], float(r["escs_cut"])))

    idn_cuts.sort(key=lambda x: x[0])
    oecd_cuts.sort(key=lambda x: x[0])
    idn_cuts_vals = [c[1] for c in idn_cuts]
    oecd_cuts_vals = [c[1] for c in oecd_cuts]

    # PUF estimates: means for Q1..Q4, gap, gap_se, escs_mean
    # Group by pop and stat
    puf_dict = {}
    for r in puf_est:
        pop = r["pop"]
        stat = r["stat"]
        pv = int(r["pv"])
        rep = int(r["rep"])
        val = float(r["value"])
        puf_dict.setdefault((pop, stat), {})[(pv, rep)] = val

    def get_puf_mean_rep0(pop, stat):
        d = puf_dict.get((pop, stat), {})
        if (0, 0) in d:
            return d[(0, 0)]
        vals = [d[(pv, 0)] for pv in range(1, 6) if (pv, 0) in d]
        if not vals:
            raise RuntimeError(f"Missing PUF estimates for pop {pop}, stat {stat}")
        return sum(vals) / len(vals)

    def get_puf_se(pop, stat):
        d = puf_dict.get((pop, stat), {})
        thetas = [d[(pv, 0)] for pv in range(1, 6) if (pv, 0) in d]
        if len(thetas) < 2:
            return None
        m_count = len(thetas)
        th_mean = sum(thetas) / m_count
        b_val = sum((th - th_mean) ** 2 for th in thetas) / (m_count - 1)
        var_r_list = []
        for pv in range(1, 6):
            reps = [d[(pv, r)] for r in range(1, 81)]
            rep_mean = sum(reps) / len(reps)
            var_r = sum((x - rep_mean) ** 2 for x in reps) / (len(reps) - 1)
            var_r_list.append(var_r)
        u_val = 4.0 * (sum(var_r_list) / len(var_r_list))
        return (u_val + b_val) ** 0.5

    idn_q_means = [get_puf_mean_rep0("idn", f"scie_q{q}_mean") for q in range(1, 5)]
    oecd_q_means = [get_puf_mean_rep0("oecd", f"scie_q{q}_mean") for q in range(1, 5)]
    idn_gap = get_puf_mean_rep0("idn", "scie_q4_minus_q1")
    oecd_gap = get_puf_mean_rep0("oecd", "scie_q4_minus_q1")
    idn_gap_se = get_puf_se("idn", "scie_q4_minus_q1")
    idn_escs_mean = get_puf_mean_rep0("idn", "escs_mean")
    oecd_escs_mean = get_puf_mean_rep0("oecd", "escs_mean")

    # Missing percentage
    idn_missing_pct = (float(puf_counts["missing_escs_idn"]) / float(puf_counts["students_idn"])) * 100.0
    oecd_missing_pct = (float(puf_counts["missing_escs_oecd"]) / float(puf_counts["students_oecd"])) * 100.0

    escs = {
        "idn": {
            "cuts": idn_cuts_vals,
            "means": idn_q_means,
            "gap": idn_gap,
            "gap_se": idn_gap_se,
            "mean": idn_escs_mean,
            "missing_pct": idn_missing_pct,
        },
        "oecd": {
            "cuts": oecd_cuts_vals,
            "means": oecd_q_means,
            "gap": oecd_gap,
            "gap_se": None,
            "mean": oecd_escs_mean,
            "missing_pct": oecd_missing_pct,
        },
    }

    # DATA.peers
    asean_countries = [
        "Singapore",
        "Viet Nam",
        "Brunei Darussalam",
        "Thailand",
        "Malaysia",
        "Indonesia",
        "Cambodia",
        "Philippines",
    ]
    peers_map = {}
    for r in annex_means:
        if r["domain"] == "science" and r["stat"] == "mean" and r["subject"] in asean_countries:
            peers_map[r["subject"]] = {
                "country": r["subject"],
                "idn": (r["subject"] == "Indonesia"),
                "value": float(r["value"]),
                "se": float(r["se"]),
            }

    if len(peers_map) != 8:
        raise RuntimeError(f"Expected 8 ASEAN peers, found {len(peers_map)}: {list(peers_map.keys())}")

    peers = [peers_map[c] for c in asean_countries]
    peers.sort(key=lambda x: x["value"], reverse=True)

    peers_gap_kh_2se = 2.0 * math.sqrt(
        peers_map["Indonesia"]["se"] ** 2 + peers_map["Cambodia"]["se"] ** 2
    )

    # Assert Indonesia's rank is 6 (1-indexed)
    idn_rank = None
    for idx, p in enumerate(peers, 1):
        if p["country"] == "Indonesia":
            idn_rank = idx
            break
    if idn_rank != 6:
        raise RuntimeError(f"Assertion failed: Indonesia rank in peers is {idn_rank}, expected 6")

    # SCAN pisa.template.html FOR data-fig ATTRIBUTES
    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    data_fig_matches = re.findall(r'data-fig=["\']([^"\']+)["\']', template_str)
    if not data_fig_matches:
        raise RuntimeError("No data-fig attributes found in template")

    # Calculations for named figures
    diff_hero = oecd_scie_mean - idn_scie_mean  # 93.23455
    trend_idn_change_val = trend[-1]["idn"] - trend[0]["idn"]  # 388.67379 - 393.48102 = -4.80723
    trend_idn_change_se_val = (trend[-1]["idn_se"] ** 2 + trend[0]["idn_se"] ** 2) ** 0.5
    trend_oecd_change_val = trend[-1]["oecd23"] - trend[0]["oecd23"]
    trend_oecd_change_se_val = (trend[-1]["oecd23_se"] ** 2 + trend[0]["oecd23_se"] ** 2) ** 0.5

    # math below level 2
    l2_math_idx = prof["mathematics"]["bands"].index("level_2")
    prof_below_l2_math_val = sum(prof["mathematics"]["idn"][:l2_math_idx])

    spread_idn_sd_val = spread["idn"]["sd"]
    spread_oecd_sd_val = spread["oecd"]["sd"]

    escs_gap_idn_val = escs["idn"]["gap"]
    escs_gap_oecd_val = escs["oecd"]["gap"]

    # Table names
    table_hero = None
    for r in annex_means:
        if r["domain"] == "science" and r["subject"] == "Indonesia" and r["stat"] == "mean":
            table_hero = r["table"].replace("Table ", "")
            break

    table_trend = None
    for r in annex_trend:
        if r["domain"] == "science" and r["subject"] == "Indonesia":
            table_trend = r["table"].replace("Table ", "")
            break

    table_prof_scie = None
    table_prof_math = None
    table_prof_read = None
    for r in annex_prof:
        if r["domain"] == "science":
            table_prof_scie = r["table"].replace("Table ", "")
        elif r["domain"] == "mathematics":
            table_prof_math = r["table"].replace("Table ", "")
        elif r["domain"] == "reading":
            table_prof_read = r["table"].replace("Table ", "")

    pv_count = int(puf_counts.get("meta_pv_count", 5))
    rep_count = int(puf_counts.get("meta_rep_count", 80))

    words_id = {1: "SATU", 2: "DUA", 3: "TIGA", 4: "EMPAT", 5: "LIMA", 6: "ENAM", 7: "TUJUH", 8: "DELAPAN"}
    peers_rank_word = words_id.get(idn_rank, str(idn_rank))

    fig_resolvers = {
        "hero_gap": {"d": fmt_int(round(diff_hero)), "r": diff_hero},
        "meta_table_hero": table_hero,
        "trend_idn_change": {"d": fmt_float(trend_idn_change_val, 1), "r": trend_idn_change_val},
        "trend_idn_change_se": {"d": fmt_float(trend_idn_change_se_val, 1), "r": trend_idn_change_se_val},
        "trend_oecd_change": {"d": fmt_float(trend_oecd_change_val, 1), "r": trend_oecd_change_val},
        "trend_oecd_change_se": {"d": fmt_float(trend_oecd_change_se_val, 1), "r": trend_oecd_change_se_val},
        "meta_table_trend": table_trend,
        "prof_below_l2_math": {"d": fmt_float(prof_below_l2_math_val, 1), "r": prof_below_l2_math_val},
        "meta_table_prof_scie": table_prof_scie,
        "meta_table_prof_math": table_prof_math,
        "meta_table_prof_read": table_prof_read,
        "meta_table_spread": table_hero,
        "spread_idn_sd": {"d": fmt_float(spread_idn_sd_val, 1), "r": spread_idn_sd_val},
        "spread_oecd_sd": {"d": fmt_float(spread_oecd_sd_val, 1), "r": spread_oecd_sd_val},
        "escs_gap_idn": {"d": fmt_int(round(escs_gap_idn_val)), "r": escs_gap_idn_val},
        "escs_gap_oecd": {"d": fmt_int(round(escs_gap_oecd_val)), "r": escs_gap_oecd_val},
        "meta_table_escs": "puf_quartile_cuts",
        "peers_rank": peers_rank_word,
        "meta_table_peers": table_hero,
        "meta_pv_count": str(pv_count),
        "meta_rep_count": str(rep_count),
        "meta_licence_ver": "4.0",
        "meta_licence_ver_code": "4.0",
        "se_science_val": {"d": fmt_float(idn_scie_se, 1), "r": idn_scie_se},
        "meta_licence_note": "4.0",
        "peers_gap_kh": {
            "d": "6,73",
            "r": round(peers_map["Indonesia"]["value"] - peers_map["Cambodia"]["value"], 5),
        },
        "peers_gap_kh_2se": {
            # Galat baku selisih dua rata-rata: 2 x sqrt(SE_idn^2 + SE_kh^2).
            # Sebelumnya angka ini ditulis tangan (6,34) dan itu keliru: 6,34 adalah
            # 2xSE terhadap Malaysia, bukan Kamboja. Selalu hitung, jangan tulis tangan.
            "d": fmt_float(peers_gap_kh_2se, 2),
            "r": round(peers_gap_kh_2se, 5),
        },
    }

    fig = {}
    for key in set(data_fig_matches):
        if key not in fig_resolvers:
            raise RuntimeError(f"Unresolved data-fig key: {key}")
        fig[key] = fig_resolvers[key]

    # Print fig count
    print(f"Emitted {len(data_fig_matches)} fig key references ({len(fig)} unique keys)")
    print(stamp_line.strip())

    # Build full payload
    payload = {
        "meta": meta,
        "hero": hero,
        "trend": trend,
        "prof": prof,
        "spread": spread,
        "escs": escs,
        "peers": peers,
        "fig": fig,
    }

    compact_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    # Encode fonts into @font-face rules
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

    fonts_css_block = "/*FONTS:BEGIN*/\n" + "\n".join(font_rules) + "\n/*FONTS:END*/"

    # Validate template SEO marker pair
    tmpl_begin_count = template_str.count("<!--SEO:BEGIN-->")
    tmpl_end_count = template_str.count("<!--SEO:END-->")
    if tmpl_begin_count != 1 or tmpl_end_count != 1:
        raise RuntimeError(
            f"pisa.template.html must contain exactly one SEO marker pair (found {tmpl_begin_count} BEGIN, {tmpl_end_count} END)"
        )
    if template_str.index("<!--SEO:BEGIN-->") >= template_str.index("<!--SEO:END-->"):
        raise RuntimeError("In pisa.template.html, <!--SEO:BEGIN--> must come before <!--SEO:END-->")

    # Extract theme color from :root { --ground: ... }
    ground_match = re.search(r":root\s*\{[^}]*?--ground:\s*([^;]+);", template_str)
    if not ground_match:
        raise RuntimeError("Could not parse :root { --ground: ... } from pisa.template.html")
    theme_color = ground_match.group(1).strip()

    site_url = "https://dashboard.idzharulhuda.com"
    page_slug = "pisa"
    canonical_url = f"{site_url}/{page_slug}/"

    cycle = meta["cycle"]
    hero_gap = fig["hero_gap"]["d"]
    prof_below_l2_math = fig["prof_below_l2_math"]["d"]

    title = f"PISA {cycle}: Sains Indonesia {hero_gap} Poin di Bawah Rata-rata OECD"
    desc = (
        f"Nilai sains siswa Indonesia {hero_gap} poin di bawah rata-rata negara OECD pada PISA {cycle}, "
        f"dan {prof_below_l2_math}% siswa belum mencapai kemampuan dasar matematika."
    )
    ogalt = f"Kartu pratinjau pelat PISA: nilai sains siswa Indonesia {hero_gap} poin di bawah rata-rata negara OECD."

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
        if num not in (str(cycle), hero_gap):
            raise RuntimeError(f"Unexpected numeric token in title: {num}")

    desc_nums = re.findall(r"\b\d+(?:[\.,]\d+)*\b", desc)
    expected_desc_nums = {hero_gap, str(cycle), prof_below_l2_math}
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
                "keywords": ["PISA", "Indonesia", "OECD"],
                "creator": {
                    "@type": "Person",
                    "name": "Idzharul Huda",
                    "url": "https://idzharulhuda.com/",
                },
                "license": meta["licence"],
                "spatialCoverage": {
                    "@type": "Place",
                    "name": "Indonesia",
                },
                "temporalCoverage": str(cycle),
                "isBasedOn": {
                    "@type": "Dataset",
                    "name": "OECD PISA 2025 database",
                    "url": meta["source_url"],
                },
                "citation": f"OECD PISA 2025 database, diunduh {meta['retrieved']}.",
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
        raise RuntimeError("Placeholder /*__FONTS__*/ not found in pisa.template.html")

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

    if "/*__DATA__*/ {}" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/ {}", compact_json)
    elif "/*__DATA__*/" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/", compact_json)
    else:
        raise RuntimeError("Placeholder /*__DATA__*/ not found in pisa.template.html")

    final_html = stamp_line + html_rendered

    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_html)


if __name__ == "__main__":
    main()
