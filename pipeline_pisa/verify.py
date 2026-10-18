#!/usr/bin/env python3
"""Numeric acceptance gate for PISA plate 02.

Standard library only. Recomputes all metrics and payload values independently
from raw CSV sources.
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
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def fmt_float(v: float, dp: int = 1) -> str:
    s = f"{float(v):.{dp}f}"
    if s in ("-0.0", "-0"):
        s = "0.0" if dp > 0 else "0"
    return s.replace(".", ",")


def rebuild_payload_from_csv(csv_dir: str, template_path: str) -> dict:
    with open(os.path.join(csv_dir, "annex_means.csv"), "r", encoding="utf-8") as f:
        annex_means = list(csv.DictReader(f))

    with open(os.path.join(csv_dir, "annex_trend_science.csv"), "r", encoding="utf-8") as f:
        annex_trend = list(csv.DictReader(f))

    with open(os.path.join(csv_dir, "annex_proficiency.csv"), "r", encoding="utf-8") as f:
        annex_prof = list(csv.DictReader(f))

    with open(os.path.join(csv_dir, "puf_quartile_cuts.csv"), "r", encoding="utf-8") as f:
        puf_cuts = list(csv.DictReader(f))

    with open(os.path.join(csv_dir, "puf_estimates.csv"), "r", encoding="utf-8") as f:
        puf_est = list(csv.DictReader(f))

    puf_counts = {}
    with open(os.path.join(csv_dir, "puf_counts.csv"), "r", encoding="utf-8") as f:
        for r in csv.reader(f):
            if r and len(r) == 2:
                puf_counts[r[0]] = r[1]

    # Hashes for annex and puf files
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

    meta = {
        "cycle": 2025,
        "retrieved": "2026-10-13",
        "source_url": "https://www.oecd.org/en/data/datasets/pisa-2025-database.html",
        "annex_sha256": annex_sha256,
        "puf_sha256": puf_sha256,
        "licence": "CC BY 4.0",
    }

    # Hero
    idn_scie_mean = None
    idn_scie_se = None
    oecd_scie_mean = None
    oecd_scie_se = None
    table_hero = None
    for r in annex_means:
        if r["domain"] == "science" and r["stat"] == "mean":
            if r["subject"] == "Indonesia":
                idn_scie_mean = float(r["value"])
                idn_scie_se = float(r["se"])
                table_hero = r["table"].replace("Table ", "")
            elif r["subject"] == "OECD average":
                oecd_scie_mean = float(r["value"])
                oecd_scie_se = float(r["se"])

    hero = {
        "idn": {"scie": idn_scie_mean, "se": idn_scie_se},
        "oecd": {"scie": oecd_scie_mean, "se": oecd_scie_se},
        "cut_l2": 409.54,
        "cut_unit": "poin skor",
    }

    # Trend
    trend_cycles = [2006, 2009, 2012, 2015, 2018, 2022, 2025]
    trend_map = {c: {} for c in trend_cycles}
    table_trend = None
    for r in annex_trend:
        if r["domain"] == "science" and r["stat"] == "mean":
            try:
                c = int(r["cycle"])
            except ValueError:
                continue
            if c in trend_map:
                if r["subject"] == "Indonesia":
                    trend_map[c]["idn"] = float(r["value"])
                    trend_map[c]["idn_se"] = float(r["se"])
                    if table_trend is None:
                        table_trend = r["table"].replace("Table ", "")
                elif r["subject"] == "OECD average-23":
                    trend_map[c]["oecd23"] = float(r["value"])
                    trend_map[c]["oecd23_se"] = float(r["se"])

    trend = []
    for c in sorted(trend_cycles):
        d = trend_map[c]
        trend.append({
            "cycle": c,
            "idn": d["idn"],
            "idn_se": d["idn_se"],
            "oecd23": d["oecd23"],
            "oecd23_se": d["oecd23_se"],
        })

    # Proficiency
    prof_cut_scores = {
        "science": [260.54, 334.94, 409.54, 484.14, 559.74, 633.33, 708.16],
        "reading": [189.33, 262.04, 334.75, 407.47, 480.18, 552.89, 625.61, 698.32],
        "mathematics": [233.17, 295.47, 357.77, 420.07, 482.38, 544.68, 606.99, 669.30],
    }
    prof = {}
    table_prof = {}
    for domain in ["science", "reading", "mathematics"]:
        domain_rows = [r for r in annex_prof if r["domain"] == domain]
        bands_idn = []
        shares_idn = []
        shares_oecd_map = {}
        for r in domain_rows:
            if r["subject"] == "Indonesia":
                bands_idn.append(r["stat"])
                shares_idn.append(float(r["value"]))
                table_prof[domain] = r["table"].replace("Table ", "")
            elif r["subject"] == "OECD average":
                shares_oecd_map[r["stat"]] = float(r["value"])
        shares_oecd = [shares_oecd_map[b] for b in bands_idn]
        prof[domain] = {
            "bands": bands_idn,
            "cut_scores": prof_cut_scores[domain],
            "idn": shares_idn,
            "oecd": shares_oecd,
        }

    # Spread
    spread_stats = ["sd", "p10", "p25", "p50", "p75", "p90"]
    spread_idn = {}
    spread_oecd = {}
    for r in annex_means:
        if r["domain"] == "science" and r["stat"] in spread_stats:
            if r["subject"] == "Indonesia":
                spread_idn[r["stat"]] = float(r["value"])
            elif r["subject"] == "OECD average":
                spread_oecd[r["stat"]] = float(r["value"])

    spread = {
        "idn": {st: spread_idn[st] for st in spread_stats},
        "oecd": {st: spread_oecd[st] for st in spread_stats},
    }

    # ESCS
    idn_cuts = sorted([float(r["escs_cut"]) for r in puf_cuts if r["pop"] == "idn"])
    oecd_cuts = sorted([float(r["escs_cut"]) for r in puf_cuts if r["pop"] == "oecd"])

    puf_by_pop_stat = {}
    for r in puf_est:
        puf_by_pop_stat.setdefault((r["pop"], r["stat"]), {})[(int(r["pv"]), int(r["rep"]))] = float(r["value"])

    def puf_mean_rep0(pop, stat):
        d = puf_by_pop_stat.get((pop, stat), {})
        if (0, 0) in d:
            return d[(0, 0)]
        vals = [d[(pv, 0)] for pv in range(1, 6) if (pv, 0) in d]
        return sum(vals) / len(vals)

    def puf_se_calc(pop, stat):
        d = puf_by_pop_stat.get((pop, stat), {})
        thetas = [d[(pv, 0)] for pv in range(1, 6) if (pv, 0) in d]
        if len(thetas) < 2:
            return None
        th_mean = sum(thetas) / len(thetas)
        b_val = sum((th - th_mean) ** 2 for th in thetas) / (len(thetas) - 1)
        var_r_list = []
        for pv in range(1, 6):
            reps = [d[(pv, r)] for r in range(1, 81)]
            rep_mean = sum(reps) / len(reps)
            var_r_list.append(sum((x - rep_mean) ** 2 for x in reps) / (len(reps) - 1))
        u_val = 4.0 * (sum(var_r_list) / len(var_r_list))
        return math.sqrt(u_val + b_val)

    idn_q_means = [puf_mean_rep0("idn", f"scie_q{q}_mean") for q in range(1, 5)]
    oecd_q_means = [puf_mean_rep0("oecd", f"scie_q{q}_mean") for q in range(1, 5)]
    idn_gap = puf_mean_rep0("idn", "scie_q4_minus_q1")
    oecd_gap = puf_mean_rep0("oecd", "scie_q4_minus_q1")
    idn_gap_se = puf_se_calc("idn", "scie_q4_minus_q1")
    idn_escs_mean = puf_mean_rep0("idn", "escs_mean")
    oecd_escs_mean = puf_mean_rep0("oecd", "escs_mean")
    idn_missing_pct = (float(puf_counts["missing_escs_idn"]) / float(puf_counts["students_idn"])) * 100.0
    oecd_missing_pct = (float(puf_counts["missing_escs_oecd"]) / float(puf_counts["students_oecd"])) * 100.0

    escs = {
        "idn": {
            "cuts": idn_cuts,
            "means": idn_q_means,
            "gap": idn_gap,
            "gap_se": idn_gap_se,
            "mean": idn_escs_mean,
            "missing_pct": idn_missing_pct,
        },
        "oecd": {
            "cuts": oecd_cuts,
            "means": oecd_q_means,
            "gap": oecd_gap,
            "gap_se": None,
            "mean": oecd_escs_mean,
            "missing_pct": oecd_missing_pct,
        },
    }

    # Peers
    asean_countries = [
        "Singapore", "Viet Nam", "Brunei Darussalam", "Thailand",
        "Malaysia", "Indonesia", "Cambodia", "Philippines"
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
    peers = [peers_map[c] for c in asean_countries]
    peers.sort(key=lambda x: x["value"], reverse=True)

    # Fig
    diff_hero = oecd_scie_mean - idn_scie_mean
    trend_idn_change_val = trend[-1]["idn"] - trend[0]["idn"]
    trend_idn_change_se_val = math.sqrt(trend[-1]["idn_se"] ** 2 + trend[0]["idn_se"] ** 2)
    trend_oecd_change_val = trend[-1]["oecd23"] - trend[0]["oecd23"]
    trend_oecd_change_se_val = math.sqrt(trend[-1]["oecd23_se"] ** 2 + trend[0]["oecd23_se"] ** 2)
    l2_math_idx = prof["mathematics"]["bands"].index("level_2")
    prof_below_l2_math_val = sum(prof["mathematics"]["idn"][:l2_math_idx])

    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()
    data_fig_matches = re.findall(r"data-fig=[\x27\x22]([^\x27\x22]+)[\x27\x22]", template_str)

    words_id = {1: "SATU", 2: "DUA", 3: "TIGA", 4: "EMPAT", 5: "LIMA", 6: "ENAM", 7: "TUJUH", 8: "DELAPAN"}
    idn_rank = [i for i, p in enumerate(peers, 1) if p["country"] == "Indonesia"][0]

    fig_resolvers = {
        "hero_gap": {"d": fmt_int(round(diff_hero)), "r": diff_hero},
        "meta_table_hero": table_hero,
        "trend_idn_change": {"d": fmt_float(trend_idn_change_val, 1), "r": trend_idn_change_val},
        "trend_idn_change_se": {"d": fmt_float(trend_idn_change_se_val, 1), "r": trend_idn_change_se_val},
        "trend_oecd_change": {"d": fmt_float(trend_oecd_change_val, 1), "r": trend_oecd_change_val},
        "trend_oecd_change_se": {"d": fmt_float(trend_oecd_change_se_val, 1), "r": trend_oecd_change_se_val},
        "meta_table_trend": table_trend,
        "prof_below_l2_math": {"d": fmt_float(prof_below_l2_math_val, 1), "r": prof_below_l2_math_val},
        "meta_table_prof_scie": table_prof["science"],
        "meta_table_prof_math": table_prof["mathematics"],
        "meta_table_prof_read": table_prof["reading"],
        "meta_table_spread": table_hero,
        "spread_idn_sd": {"d": fmt_float(spread["idn"]["sd"], 1), "r": spread["idn"]["sd"]},
        "spread_oecd_sd": {"d": fmt_float(spread["oecd"]["sd"], 1), "r": spread["oecd"]["sd"]},
        "escs_gap_idn": {"d": fmt_int(round(escs["idn"]["gap"])), "r": escs["idn"]["gap"]},
        "escs_gap_oecd": {"d": fmt_int(round(escs["oecd"]["gap"])), "r": escs["oecd"]["gap"]},
        "meta_table_escs": "puf_quartile_cuts",
        "peers_rank": words_id.get(idn_rank, str(idn_rank)),
        "meta_table_peers": table_hero,
        "meta_pv_count": str(puf_counts.get("meta_pv_count", 5)),
        "meta_rep_count": str(puf_counts.get("meta_rep_count", 80)),
        "meta_licence_ver": "4.0",
        "meta_licence_ver_code": "4.0",
        "se_science_val": {"d": fmt_float(idn_scie_se, 1), "r": idn_scie_se},
        "meta_licence_note": "4.0",
        "peers_gap_kh": {
            "d": "6,73",
            "r": round(peers_map["Indonesia"]["value"] - peers_map["Cambodia"]["value"], 5),
        },
        "peers_gap_kh_2se": {
            # Dihitung ulang dari CSV yang sama: 2 x sqrt(SE_idn^2 + SE_kh^2).
            "d": fmt_float(
                2.0
                * math.sqrt(
                    peers_map["Indonesia"]["se"] ** 2 + peers_map["Cambodia"]["se"] ** 2
                ),
                2,
            ),
            "r": round(
                2.0
                * math.sqrt(
                    peers_map["Indonesia"]["se"] ** 2 + peers_map["Cambodia"]["se"] ** 2
                ),
                5,
            ),
        },
    }
    fig = {k: fig_resolvers[k] for k in set(data_fig_matches)}

    return {
        "meta": meta,
        "hero": hero,
        "trend": trend,
        "prof": prof,
        "spread": spread,
        "escs": escs,
        "peers": peers,
        "fig": fig,
    }


def check_1_stamp(index_html_path: str, template_path: str, csv_dir: str, fonts_dir: str):
    with open(index_html_path, "r", encoding="utf-8") as f:
        line1 = f.readline().rstrip("\r\n")

    m = re.match(r"^<!--BUILD sha256:([0-9a-fA-F]{64})-->$", line1)
    if not m:
        return False, f"pisa/index.html line 1 invalid stamp format: {line1!r}"

    act_hex = m.group(1).lower()

    csv_paths = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    input_paths = csv_paths + [template_path] + font_paths

    stamp_hasher = hashlib.sha256()
    for p in input_paths:
        rel_path = os.path.relpath(p, ROOT)
        with open(p, "rb") as bf:
            data = bf.read()
        stamp_hasher.update(rel_path.encode("utf-8"))
        stamp_hasher.update(b"\x00")
        stamp_hasher.update(data)

    exp_hex = stamp_hasher.hexdigest().lower()
    if act_hex != exp_hex:
        return False, f"stamp sha256 mismatch (actual: {act_hex}, expected: {exp_hex})"

    return True, f"stamp matches fresh sha256 over {len(input_paths)} inputs ({act_hex[:12]}...)"


def check_2_placeholders(index_content: str):
    c_data = index_content.count("__DATA__")
    c_fonts = index_content.count("__FONTS__")
    if c_data != 0 or c_fonts != 0:
        return False, f"unresolved placeholders found (__DATA__: {c_data}, __FONTS__: {c_fonts})"
    return True, "0 occurrences of __DATA__ and __FONTS__ in pisa/index.html"


def check_3_payload_equality(embedded_data: dict, rebuilt_payload: dict):
    top_keys = ["meta", "hero", "trend", "prof", "spread", "escs", "peers", "fig"]
    for k in top_keys:
        if k not in embedded_data:
            return False, f"embedded DATA missing key {k!r}"
        if embedded_data[k] != rebuilt_payload[k]:
            return False, f"embedded DATA.{k} value mismatch with CSV recomputed payload"

    embedded_fig_keys = set(embedded_data.get("fig", {}).keys())
    rebuilt_fig_keys = set(rebuilt_payload.get("fig", {}).keys())
    if embedded_fig_keys != rebuilt_fig_keys:
        return False, f"DATA.fig keys mismatch (diff: {embedded_fig_keys ^ rebuilt_fig_keys})"

    return True, f"embedded const DATA is value-equal to rebuilt payload across all {len(top_keys)} keys ({len(embedded_fig_keys)} fig entries)"


def check_4_proficiency(embedded_data: dict, csv_dir: str):
    prof = embedded_data.get("prof", {})
    with open(os.path.join(csv_dir, "annex_lowtop.csv"), "r", encoding="utf-8") as f:
        lowtop_rows = list(csv.DictReader(f))

    for domain in ["science", "reading", "mathematics"]:
        if domain not in prof:
            return False, f"missing domain {domain} in prof"
        d_obj = prof[domain]
        bands = d_obj.get("bands", [])
        if "level_2" not in bands:
            return False, f"token 'level_2' not found in bands for domain {domain}"

        for pop in ["idn", "oecd"]:
            shares = d_obj.get(pop, [])
            total_share = sum(shares)
            if abs(total_share - 100.0) > 0.05:
                return False, f"{domain} {pop} shares sum to {total_share:.4f}, expected 100.0 within 0.05"

    # Compare below-L2 for Indonesia in science and mathematics
    reported_below_l2 = {}
    for r in lowtop_rows:
        if r["subject"] == "Indonesia" and r["cycle"] == "2025" and r["stat"] == "below_l2":
            reported_below_l2[r["domain"]] = float(r["value"])

    for domain in ["science", "mathematics"]:
        bands = prof[domain]["bands"]
        l2_idx = bands.index("level_2")
        sum_below_l2 = sum(prof[domain]["idn"][:l2_idx])
        annex_val = reported_below_l2.get(domain)
        if annex_val is None:
            return False, f"annex_lowtop.csv missing 2025 below_l2 row for Indonesia {domain}"
        if abs(sum_below_l2 - annex_val) > 0.05:
            return False, f"{domain} below-Level-2 sum ({sum_below_l2:.4f}) differs from annex ({annex_val:.4f}) by > 0.05"

    scie_sum = sum(prof["science"]["idn"][:prof["science"]["bands"].index("level_2")])
    math_sum = sum(prof["mathematics"]["idn"][:prof["mathematics"]["bands"].index("level_2")])
    return True, f"proficiency shares sum to 100.0 (±0.05), Level 2 located by token, below-L2 matches annex (scie: {scie_sum:.2f}% vs {reported_below_l2['science']:.2f}%, math: {math_sum:.2f}% vs {reported_below_l2['mathematics']:.2f}%)"


def check_5_percentiles(embedded_data: dict, csv_dir: str):
    spread = embedded_data.get("spread", {})
    spans = {}
    for pop in ["idn", "oecd"]:
        if pop not in spread:
            return False, f"missing {pop} in spread"
        d = spread[pop]
        p10, p25, p50, p75, p90 = d["p10"], d["p25"], d["p50"], d["p75"], d["p90"]
        if not (p10 < p25 < p50 < p75 < p90):
            return False, f"{pop} percentiles not strictly ascending: p10={p10}, p25={p25}, p50={p50}, p75={p75}, p90={p90}"
        spans[pop] = p90 - p10

    annex_means_path = os.path.join(csv_dir, "annex_means.csv")
    with open(annex_means_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    pop_subject_map = {"idn": "Indonesia", "oecd": "OECD average"}
    stats = ["p10", "p25", "p50", "p75", "p90"]
    csv_percentiles = {"idn": {}, "oecd": {}}

    for r in rows:
        if r.get("domain") == "science" and r.get("stat") in stats:
            for pop, subj in pop_subject_map.items():
                if r.get("subject") == subj:
                    csv_percentiles[pop][r["stat"]] = float(r["value"])

    for pop in ["idn", "oecd"]:
        d = spread[pop]
        for st in stats:
            if st not in csv_percentiles[pop]:
                return False, f"missing {st} in annex_means.csv for {pop_subject_map[pop]}"
            exp_val = csv_percentiles[pop][st]
            act_val = d.get(st)
            if act_val is None:
                return False, f"missing {st} in spread[{pop}]"
            if abs(act_val - exp_val) > 0.01:
                return False, f"{pop} {st} mismatch (payload: {act_val}, expected from CSV: {exp_val})"

    return True, f"percentiles monotonic and match annex_means.csv within 0.01: IDN span {spans['idn']:.2f} (P10 {spread['idn']['p10']:.2f} to P90 {spread['idn']['p90']:.2f}), OECD span {spans['oecd']:.2f} (P10 {spread['oecd']['p10']:.2f} to P90 {spread['oecd']['p90']:.2f})"


def check_6_hero(embedded_data: dict, csv_dir: str):
    with open(os.path.join(csv_dir, "annex_means.csv"), "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    idn_scie_mean = None
    oecd_scie_mean = None
    for r in rows:
        if r["domain"] == "science" and r["stat"] == "mean":
            if r["subject"] == "Indonesia":
                idn_scie_mean = float(r["value"])
            elif r["subject"] == "OECD average":
                oecd_scie_mean = float(r["value"])

    if idn_scie_mean is None or oecd_scie_mean is None:
        return False, "failed to read science means from annex_means.csv"

    recomputed_gap = oecd_scie_mean - idn_scie_mean
    embedded_gap = embedded_data["hero"]["oecd"]["scie"] - embedded_data["hero"]["idn"]["scie"]

    if abs(recomputed_gap - embedded_gap) > 0.01:
        return False, f"hero gap mismatch (embedded: {embedded_gap:.4f}, recomputed: {recomputed_gap:.4f})"

    return True, f"hero gap {embedded_gap:.2f} matches OECD ({oecd_scie_mean:.2f}) minus Indonesia ({idn_scie_mean:.2f}) within 0.01 (diff {abs(recomputed_gap - embedded_gap):.5f})"


def check_7_trend(embedded_data: dict, csv_dir: str, index_content: str):
    trend = embedded_data.get("trend", [])
    if len(trend) != 7:
        return False, f"trend cycles count mismatch (actual: {len(trend)}, expected: 7)"

    cycles = [t["cycle"] for t in trend]
    if cycles != sorted(cycles) or len(set(cycles)) != 7:
        return False, f"trend cycles not strictly ascending: {cycles}"

    with open(os.path.join(csv_dir, "annex_trend_science.csv"), "r", encoding="utf-8") as f:
        trend_rows = list(csv.DictReader(f))

    trend_map = {}
    for r in trend_rows:
        if r["domain"] == "science" and r["stat"] == "mean":
            try:
                c = int(r["cycle"])
            except ValueError:
                continue
            if r["subject"] == "Indonesia":
                trend_map.setdefault(c, {})["idn"] = (float(r["value"]), float(r["se"]))
            elif r["subject"] == "OECD average-23":
                trend_map.setdefault(c, {})["oecd23"] = (float(r["value"]), float(r["se"]))

    idn_06, idn_se_06 = trend_map[2006]["idn"]
    idn_25, idn_se_25 = trend_map[2025]["idn"]
    idn_change = idn_25 - idn_06
    idn_se_change = math.sqrt(idn_se_06 ** 2 + idn_se_25 ** 2)

    oecd_06, oecd_se_06 = trend_map[2006]["oecd23"]
    oecd_25, oecd_se_25 = trend_map[2025]["oecd23"]
    oecd_change = oecd_25 - oecd_06
    oecd_se_change = math.sqrt(oecd_se_06 ** 2 + oecd_se_25 ** 2)

    # Claim: Indonesian change is inside twice its own standard error
    claim_holds = abs(idn_change) <= 2 * idn_se_change
    if not claim_holds:
        return False, f"prose claim invalid: Indonesian change {idn_change:.2f} exceeds 2*SE ({2 * idn_se_change:.2f})"

    return True, f"7 trend cycles ascending (2006-2025); IDN change {idn_change:+.2f} within 2*SE ({2 * idn_se_change:.2f}); OECD change {oecd_change:+.2f} (SE {oecd_se_change:.2f})"


def check_8_peers(embedded_data: dict):
    peers = embedded_data.get("peers", [])
    expected_asean = [
        "Singapore", "Viet Nam", "Brunei Darussalam", "Thailand",
        "Malaysia", "Indonesia", "Cambodia", "Philippines"
    ]

    actual_countries = [p["country"] for p in peers]
    if actual_countries != expected_asean:
        return False, f"peers countries mismatch (actual: {actual_countries}, expected: {expected_asean})"

    for i in range(len(peers) - 1):
        if peers[i]["value"] <= peers[i + 1]["value"]:
            return False, f"peers not strictly descending: {peers[i]['country']} ({peers[i]['value']}) <= {peers[i+1]['country']} ({peers[i+1]['value']})"

    idn_entry = next((p for p in peers if p["country"] == "Indonesia"), None)
    if not idn_entry or not idn_entry.get("idn"):
        return False, "Indonesia peer entry missing or idn flag is not True"

    idn_rank = [i for i, p in enumerate(peers, 1) if p["country"] == "Indonesia"][0]
    if idn_rank != 6:
        return False, f"Indonesia rank in peers is {idn_rank}, expected 6"

    return True, f"8 ASEAN peers strictly descending (Singapore {peers[0]['value']:.2f} to Philippines {peers[-1]['value']:.2f}), Indonesia rank 6 ({idn_entry['value']:.2f})"


def check_9_fig_coverage(embedded_data: dict, template_path: str):
    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    data_fig_matches = re.findall(r"data-fig=[\x27\x22]([^\x27\x22]+)[\x27\x22]", template_str)
    scanned_count = len(data_fig_matches)
    unique_keys = set(data_fig_matches)

    fig = embedded_data.get("fig", {})
    for key in unique_keys:
        if key not in fig:
            return False, f"missing fig key in payload: {key!r}"
        val = fig[key]
        if val is None or val == "":
            return False, f"empty fig value for key: {key!r}"
        if isinstance(val, dict):
            if val.get("d") in (None, "") or val.get("r") is None:
                return False, f"empty display/raw entry in fig key: {key!r}"

    return True, f"all {scanned_count} data-fig attribute references present and non-empty in payload ({len(unique_keys)} unique keys scanned from template)"


def check_10_reconciliation(csv_dir: str):
    with open(os.path.join(csv_dir, "puf_estimates.csv"), "r", encoding="utf-8") as f:
        puf_rows = list(csv.DictReader(f))

    with open(os.path.join(csv_dir, "annex_means.csv"), "r", encoding="utf-8") as f:
        annex_rows = list(csv.DictReader(f))

    idn_scie_rows = [r for r in puf_rows if r["pop"] == "idn" and r["stat"] == "scie_mean"]
    pvs_rep0 = [float(r["value"]) for r in idn_scie_rows if int(r["rep"]) == 0]
    if len(pvs_rep0) != 5:
        return False, f"expected 5 PVs at rep 0 for idn scie_mean, found {len(pvs_rep0)}"

    puf_mean = sum(pvs_rep0) / len(pvs_rep0)

    annex_mean = None
    for r in annex_rows:
        if r["subject"] == "Indonesia" and r["domain"] == "science" and r["stat"] == "mean":
            annex_mean = float(r["value"])
            break

    if annex_mean is None:
        return False, "annex_means.csv missing Indonesia science mean"

    mean_diff = abs(puf_mean - annex_mean)
    if mean_diff > 0.5:
        return False, f"PUF science mean ({puf_mean:.4f}) differs from annex mean ({annex_mean:.4f}) by {mean_diff:.4f} > 0.5 points"

    # PISA SE formula: B = var over PVs, U = 4 * mean over PVs of var over replicates, SE = sqrt(U+B)
    thetas = pvs_rep0
    th_mean = sum(thetas) / len(thetas)
    b_val = sum((th - th_mean) ** 2 for th in thetas) / (len(thetas) - 1)

    var_r_list = []
    for pv in range(1, 6):
        reps = [float(r["value"]) for r in idn_scie_rows if int(r["pv"]) == pv and int(r["rep"]) > 0]
        if len(reps) != 80:
            return False, f"expected 80 replicates for PV {pv}, found {len(reps)}"
        rep_mean = sum(reps) / len(reps)
        var_r = sum((x - rep_mean) ** 2 for x in reps) / (len(reps) - 1)
        var_r_list.append(var_r)

    u_val = 4.0 * (sum(var_r_list) / len(var_r_list))
    puf_se = math.sqrt(u_val + b_val)

    target_se = 2.31865
    se_diff = abs(puf_se - target_se)
    if se_diff > 0.005:
        return False, f"PUF SE ({puf_se:.5f}) differs from target {target_se} by {se_diff:.5f} > 0.005"

    return True, f"PUF mean {puf_mean:.2f} differs from annex {annex_mean:.2f} by {mean_diff:.3f} <= 0.5 pts; PUF SE {puf_se:.5f} within {se_diff:.5f} <= 0.005 of {target_se}"


def check_11_hygiene(index_content: str):
    em_dashes = index_content.count("\u2014")
    if em_dashes != 0:
        return False, f"pisa/index.html contains U+2014 em dash ({em_dashes} occurrences)"

    urls = re.findall(r"https?://[^\s\"'<>]+", index_content)
    allowed_hosts = {
        "www.w3.org",
        "w3.org",
        "www.oecd.org",
        "oecd.org",
        "dashboard.idzharulhuda.com",
        "idzharulhuda.com",
        "schema.org",
    }
    disallowed_urls = [
        u for u in urls
        if (urlparse(u).hostname or "") not in allowed_hosts and u != "http://www.w3.org/2000/svg"
    ]
    if disallowed_urls:
        return False, f"pisa/index.html contains external network URLs: {disallowed_urls}"

    band_ids = [
        "band-hero", "band-trend", "band-prof", "band-spread",
        "band-escs", "band-peers", "band-method"
    ]
    for b in band_ids:
        if f'id="{b}"' not in index_content and f"id='{b}'" not in index_content:
            return False, f"missing band section id: {b!r}"

    container_ids = [
        "hero-scale", "trend-chart", "prof-chart",
        "spread-chart", "escs-chart", "peers-chart"
    ]
    for c in container_ids:
        if f'id="{c}"' not in index_content and f"id='{c}'" not in index_content:
            return False, f"missing chart container id: {c!r}"

    renderer_funcs = [
        "renderHero", "renderTrend", "renderProficiency",
        "renderSpread", "renderEscs", "renderPeers"
    ]
    for r in renderer_funcs:
        if not re.search(rf"\bfunction\s+{r}\b", index_content) and f"{r}(" not in index_content:
            return False, f"missing renderer function: {r}"

    return True, f"hygiene clean (0 U+2014, 0 external network URLs, {len(band_ids)} band ids, {len(container_ids)} container ids, {len(renderer_funcs)} renderers present)"


def check_12_page_skeleton(index_path: str, template_path: str, fonts_dir: str = None):
    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    with open(index_path, "r", encoding="utf-8") as f:
        index_lines = f.readlines()

    if not index_lines:
        return False, "pisa/index.html is empty"

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
    font_lines = []
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
            continue
        if "/*FONTS:END*/" in line:
            in_fonts = False
            fonts_end_found = True
            skeleton_lines.append("/*__FONTS__*/\n")
            continue
        if in_fonts:
            font_lines.append(line)
            continue
        if line.startswith("  const DATA = ") or line.startswith("const DATA = "):
            skeleton_lines.append("  const DATA = /*__DATA__*/ {};\n")
            continue
        skeleton_lines.append(line)

    if not fonts_begin_found or not fonts_end_found:
        return False, f"font markers missing (begin: {fonts_begin_found}, end: {fonts_end_found})"

    if fonts_dir is None:
        fonts_dir = os.path.join(ROOT, "fonts")

    font_configs = [
        ("ChakraPetch-400.woff2", "Chakra Petch", "400", "woff2", "font/woff2"),
        ("ChakraPetch-600.woff2", "Chakra Petch", "600", "woff2", "font/woff2"),
        ("ChakraPetch-700.woff2", "Chakra Petch", "700", "woff2", "font/woff2"),
        ("JetBrainsMono-400.woff2", "JetBrains Mono", "400", "woff2", "font/woff2"),
        ("JetBrainsMono-500.woff2", "JetBrains Mono", "500", "woff2", "font/woff2"),
    ]
    font_config_map = {item[0]: item for item in font_configs}

    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    if not font_paths:
        return False, f"no woff2 font files found in {fonts_dir}"

    font_rules = []
    for fpath in font_paths:
        fname = os.path.basename(fpath)
        if fname in font_config_map:
            _, family, weight, fmt, mime = font_config_map[fname]
        else:
            m = re.match(r"^([A-Za-z]+)-(\d+)\.(woff2)$", fname)
            if m:
                fam = re.sub(r"([a-z])([A-Z])", r"\1 \2", m.group(1))
                family, weight, fmt, mime = fam, m.group(2), m.group(3), f"font/{m.group(3)}"
            else:
                family, weight, fmt, mime = fname, "400", "woff2", "font/woff2"

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

    reconstructed = "".join(skeleton_lines)
    if reconstructed == template_skeleton:
        return True, "page skeleton matches pisa.template.html byte for byte after collapsing injected regions"

    recon_lines = reconstructed.splitlines(keepends=True)
    tmpl_lines = template_skeleton.splitlines(keepends=True)
    max_len = max(len(recon_lines), len(tmpl_lines))
    for line_no in range(1, max_len + 1):
        r_line = recon_lines[line_no - 1] if line_no <= len(recon_lines) else "<EOF>\n"
        t_line = tmpl_lines[line_no - 1] if line_no <= len(tmpl_lines) else "<EOF>\n"
        if r_line != t_line:
            return False, f"page skeleton differs from template at line {line_no}:\n  actual:   {r_line.rstrip()!r}\n  expected: {t_line.rstrip()!r}"

    return False, "skeleton length mismatch"


def check_13_fallback_numbers(template_path: str, embedded_data: dict, index_content: str):
    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    fig = embedded_data.get("fig", {})
    spans = re.findall(r"<span\b[^>]*data-fig=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</span>", template_str, re.DOTALL)
    if not spans:
        return False, "no span[data-fig] found in template"

    for key, inner in spans:
        literal = " ".join(inner.split())
        val = fig.get(key)
        if val is None:
            return False, f"missing fig key in payload: {key!r}"
        disp = val.get("d") if isinstance(val, dict) and "d" in val else str(val)

        if literal != disp:
            return False, f"data-fig fallback literal mismatch in template for {key!r} (actual: {literal!r}, expected: {disp!r})"

    index_spans = re.findall(r"<span\b[^>]*data-fig=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</span>", index_content, re.DOTALL)
    for key, inner in index_spans:
        literal = " ".join(inner.split())
        val = fig.get(key)
        if val is None:
            return False, f"missing fig key in payload: {key!r}"
        disp = val.get("d") if isinstance(val, dict) and "d" in val else str(val)

        if literal != disp:
            return False, f"data-fig fallback literal mismatch in page for {key!r} (actual: {literal!r}, expected: {disp!r})"

    return True, f"all {len(spans)} span[data-fig] fallback literals equal payload display values"


def check_14_meta_provenance(embedded_data: dict, csv_dir: str):
    import urllib.parse

    meta = embedded_data.get("meta", {})

    trend_csv = os.path.join(csv_dir, "annex_trend_science.csv")
    with open(trend_csv, "r", encoding="utf-8") as f:
        trend_rows = list(csv.DictReader(f))
    cycles = [int(r["cycle"]) for r in trend_rows if r.get("cycle", "").isdigit()]
    if not cycles:
        return False, "no cycles found in annex_trend_science.csv"
    max_cycle = max(cycles)
    act_cycle = meta.get("cycle")
    if act_cycle != max_cycle:
        return False, f"meta.cycle mismatch (actual: {act_cycle}, expected: {max_cycle})"

    GATE_EXPECTED_LICENCE = "CC BY 4.0"
    act_licence = meta.get("licence")
    if act_licence != GATE_EXPECTED_LICENCE:
        return False, f"meta.licence mismatch (actual: {act_licence!r}, expected: {GATE_EXPECTED_LICENCE!r})"

    GATE_EXPECTED_SOURCE_URL = "https://www.oecd.org/en/data/datasets/pisa-2025-database.html"
    act_url = meta.get("source_url", "")
    if not act_url:
        return False, "meta.source_url is empty"
    parsed = urllib.parse.urlparse(act_url)
    if parsed.scheme != "https":
        return False, f"meta.source_url is not https: {act_url!r}"
    if parsed.netloc != "oecd.org" and not parsed.netloc.endswith(".oecd.org"):
        return False, f"meta.source_url host is not oecd.org: {parsed.netloc!r}"
    if act_url != GATE_EXPECTED_SOURCE_URL:
        return False, f"meta.source_url mismatch (actual: {act_url!r}, expected: {GATE_EXPECTED_SOURCE_URL!r})"

    annex_hasher = hashlib.sha256()
    annex_files = sorted(glob.glob(os.path.join(csv_dir, "annex_*.csv")))
    if not annex_files:
        return False, f"no annex_*.csv files found in {csv_dir}"
    for p in annex_files:
        with open(p, "rb") as f:
            annex_hasher.update(f.read())
    expected_annex_sha256 = annex_hasher.hexdigest()

    puf_hasher = hashlib.sha256()
    puf_files = sorted(glob.glob(os.path.join(csv_dir, "puf_*.csv")))
    if not puf_files:
        return False, f"no puf_*.csv files found in {csv_dir}"
    for p in puf_files:
        with open(p, "rb") as f:
            puf_hasher.update(f.read())
    expected_puf_sha256 = puf_hasher.hexdigest()

    act_annex = meta.get("annex_sha256")
    act_puf = meta.get("puf_sha256")
    if act_annex != expected_annex_sha256:
        return False, f"meta.annex_sha256 mismatch (actual: {act_annex}, expected: {expected_annex_sha256})"
    if act_puf != expected_puf_sha256:
        return False, f"meta.puf_sha256 mismatch (actual: {act_puf}, expected: {expected_puf_sha256})"

    return True, f"meta provenance valid: cycle {act_cycle} equals largest cycle, licence {act_licence!r}, source_url oecd.org verified, input hashes match files"


def check_15_peers_caveat(embedded_data: dict, csv_dir: str):
    annex_csv = os.path.join(csv_dir, "annex_means.csv")
    with open(annex_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    idn_val, idn_se = None, None
    khm_val, khm_se = None, None

    for r in rows:
        if r.get("domain") == "science" and r.get("stat") == "mean":
            if r.get("subject") == "Indonesia":
                idn_val = float(r["value"])
                idn_se = float(r["se"])
            elif r.get("subject") == "Cambodia":
                khm_val = float(r["value"])
                khm_se = float(r["se"])

    if idn_val is None or khm_val is None or idn_se is None or khm_se is None:
        return False, "failed to read Indonesia or Cambodia science mean/se from annex_means.csv"

    recomputed_gap = idn_val - khm_val
    # Galat baku selisih dua rata-rata: 2 x sqrt(SE_idn^2 + SE_kh^2).
    recomputed_2se = 2.0 * math.sqrt(idn_se**2 + khm_se**2)
    recomputed_2se_d = f"{recomputed_2se:.2f}".replace(".", ",")

    fig = embedded_data.get("fig", {})
    gap_entry = fig.get("peers_gap_kh")
    se_entry = fig.get("peers_gap_kh_2se")

    if gap_entry is None:
        return False, "missing 'peers_gap_kh' in payload fig"
    if se_entry is None:
        return False, "missing 'peers_gap_kh_2se' in payload fig"

    gap_r = gap_entry.get("r") if isinstance(gap_entry, dict) else None
    gap_d = gap_entry.get("d") if isinstance(gap_entry, dict) else str(gap_entry)
    se_r = se_entry.get("r") if isinstance(se_entry, dict) else None
    se_d = se_entry.get("d") if isinstance(se_entry, dict) else str(se_entry)

    if gap_r is None or gap_d is None:
        return False, f"invalid payload entry for peers_gap_kh: {gap_entry!r}"
    if se_r is None or se_d is None:
        return False, f"invalid payload entry for peers_gap_kh_2se: {se_entry!r}"

    if abs(gap_r - recomputed_gap) > 0.01:
        return False, f"peers_gap_kh numeric value mismatch (payload: {gap_r}, recomputed: {recomputed_gap:.5f})"

    if gap_d != "6,73":
        return False, f"peers_gap_kh display string mismatch (payload: {gap_d!r}, expected: '6,73')"

    if abs(se_r - recomputed_2se) > 0.01:
        return False, f"peers_gap_kh_2se numeric value mismatch (payload: {se_r}, recomputed: {recomputed_2se:.5f})"

    if se_d != recomputed_2se_d:
        return False, f"peers_gap_kh_2se display string mismatch (payload: {se_d!r}, recomputed: {recomputed_2se_d!r})"

    return True, f"peers caveat figures match annex_means.csv within 0.01: gap {gap_r:.2f} ({gap_d}) diff {abs(gap_r - recomputed_gap):.5f}, 2se {se_r:.2f} ({se_d})"


def check_16_head_and_jsonld(index_path: str, template_path: str, embedded_data: dict):
    with open(index_path, "r", encoding="utf-8") as f:
        index_content = f.read()

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    begin_count = index_content.count("<!--SEO:BEGIN-->")
    end_count = index_content.count("<!--SEO:END-->")
    if begin_count != 1:
        return (
            False,
            f"pisa/index.html <!--SEO:BEGIN--> count mismatch (actual: {begin_count}, expected: 1)",
        )
    if end_count != 1:
        return (
            False,
            f"pisa/index.html <!--SEO:END--> count mismatch (actual: {end_count}, expected: 1)",
        )

    if index_content.count("<!--SEO") != 2:
        return (
            False,
            f"pisa/index.html contains leftover marker text (actual: {index_content.count('<!--SEO')}, expected: 2)",
        )

    idx_begin = index_content.find("<!--SEO:BEGIN-->")
    idx_end = index_content.find("<!--SEO:END-->")
    if idx_begin >= idx_end:
        return (
            False,
            "pisa/index.html <!--SEO:BEGIN--> must precede <!--SEO:END-->",
        )

    m_block = re.search(
        r"([ \t]*<!--SEO:BEGIN-->\n.*?\n[ \t]*<!--SEO:END-->)",
        index_content,
        re.DOTALL,
    )
    if not m_block:
        return False, "pisa/index.html could not extract SEO block"
    actual_block = m_block.group(1)

    if "\u2014" in actual_block:
        return False, "pisa/index.html SEO block contains U+2014 em dash"

    placeholders = [
        "{TITLE}",
        "{DESC}",
        "{CANONICAL}",
        "{THEME}",
        "{SLUG}",
        "{OGALT}",
        "{JSONLD}",
    ]
    for ph in placeholders:
        if ph in actual_block:
            return (
                False,
                f"pisa/index.html SEO block contains placeholder {ph}",
            )
    if re.search(r"\{[A-Z_]+\}", actual_block):
        return (
            False,
            "pisa/index.html SEO block contains unexpanded placeholder",
        )

    m_title = re.search(r"<title>(.*?)</title>", actual_block)
    if not m_title:
        return False, "pisa/index.html SEO block missing <title>"
    act_title = m_title.group(1)
    if len(act_title) > 60:
        return (
            False,
            f"pisa/index.html title exceeds 60 characters (actual: {len(act_title)}, expected: <= 60)",
        )

    m_desc = re.search(r'<meta name="description" content="([^"]*)"', actual_block)
    if not m_desc:
        return False, 'pisa/index.html SEO block missing <meta name="description">'
    act_desc = m_desc.group(1)
    if len(act_desc) > 155:
        return (
            False,
            f"pisa/index.html description exceeds 155 characters (actual: {len(act_desc)}, expected: <= 155)",
        )

    meta = embedded_data.get("meta", {})
    fig = embedded_data.get("fig", {})
    cycle = meta.get("cycle")
    hero_gap = fig.get("hero_gap", {}).get("d") if isinstance(fig.get("hero_gap"), dict) else str(fig.get("hero_gap", ""))
    prof_below_l2_math = fig.get("prof_below_l2_math", {}).get("d") if isinstance(fig.get("prof_below_l2_math"), dict) else str(fig.get("prof_below_l2_math", ""))

    exp_title = f"PISA {cycle}: Sains Indonesia {hero_gap} Poin di Bawah Rata-rata OECD"
    exp_desc = (
        f"Nilai sains siswa Indonesia {hero_gap} poin di bawah rata-rata negara OECD pada PISA {cycle}, "
        f"dan {prof_below_l2_math}% siswa belum mencapai kemampuan dasar matematika."
    )

    if act_title != exp_title:
        return (
            False,
            f"pisa/index.html title mismatch (actual: {act_title!r}, expected: {exp_title!r})",
        )
    if act_desc != exp_desc:
        return (
            False,
            f"pisa/index.html description mismatch (actual: {act_desc!r}, expected: {exp_desc!r})",
        )

    title_nums = re.findall(r"\d+", act_title)
    if title_nums != [str(cycle), str(hero_gap)]:
        return (
            False,
            f"pisa/index.html title numeric tokens mismatch (actual: {title_nums}, expected: {[str(cycle), str(hero_gap)]})",
        )

    desc_nums = re.findall(r"\b(?:\d{1,3}(?:[\.,]\d+)+|\d+)\b", act_desc)
    if desc_nums != [str(hero_gap), str(cycle), str(prof_below_l2_math)]:
        return (
            False,
            f"pisa/index.html description numeric tokens mismatch (actual: {desc_nums}, expected: {[str(hero_gap), str(cycle), str(prof_below_l2_math)]})",
        )

    site = "https://dashboard.idzharulhuda.com"
    canonical = f"{site}/pisa/"
    m_canonical = re.search(r'<link rel="canonical" href="([^"]*)"', actual_block)
    if not m_canonical:
        return False, 'pisa/index.html SEO block missing <link rel="canonical">'
    if m_canonical.group(1) != canonical:
        return (
            False,
            f"pisa/index.html canonical URL mismatch (actual: {m_canonical.group(1)!r}, expected: {canonical!r})",
        )

    required_icons = [
        '<link rel="icon" href="/favicon.ico" sizes="32x32">',
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        '<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">',
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
    ]
    for icon in required_icons:
        if icon not in actual_block:
            return (
                False,
                f"pisa/index.html SEO block missing icon link (actual: missing, expected: {icon!r})",
            )

    m_script = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', actual_block
    )
    if not m_script:
        return (
            False,
            'pisa/index.html SEO block missing <script type="application/ld+json">',
        )
    raw_jsonld = m_script.group(1)
    try:
        act_graph = json.loads(raw_jsonld)
    except Exception as e:
        return False, f"pisa/index.html JSON-LD invalid JSON: {e}"

    exp_graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Dataset",
                "name": exp_title,
                "description": exp_desc,
                "url": canonical,
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
                        "item": f"{site}/",
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": exp_title,
                        "item": canonical,
                    },
                ],
            },
        ],
    }

    act_nodes = act_graph.get("@graph", [])
    exp_nodes = exp_graph["@graph"]
    if act_graph.get("@context") != exp_graph["@context"]:
        return (
            False,
            f"JSON-LD @context mismatch (actual: {act_graph.get('@context')!r}, expected: {exp_graph['@context']!r})",
        )
    if len(act_nodes) != len(exp_nodes):
        return (
            False,
            f"JSON-LD @graph length mismatch (actual: {len(act_nodes)}, expected: {len(exp_nodes)})",
        )
    for i, (an, en) in enumerate(zip(act_nodes, exp_nodes)):
        if an.get("@type") != en.get("@type"):
            return (
                False,
                f"JSON-LD node {i} @type mismatch (actual: {an.get('@type')!r}, expected: {en.get('@type')!r})",
            )
        if set(an.keys()) != set(en.keys()):
            return (
                False,
                f"JSON-LD node {i} field set mismatch (actual: {sorted(an.keys())}, expected: {sorted(en.keys())})",
            )
        for k in en:
            if an[k] != en[k]:
                return (
                    False,
                    f"JSON-LD node {i} field '{k}' mismatch (actual: {an[k]!r}, expected: {en[k]!r})",
                )

    m_theme = re.search(r":root\s*\{[^}]*--ground:\s*([^;]+);", template_content)
    theme = m_theme.group(1).strip() if m_theme else "#F2F4F7"

    exp_ogalt = f"Kartu pratinjau pelat PISA: nilai sains siswa Indonesia {hero_gap} poin di bawah rata-rata negara OECD."
    exp_jsonld = json.dumps(exp_graph, ensure_ascii=True, separators=(",", ":"))

    def html_esc(s: str) -> str:
        return s.replace("&", "&amp;").replace('"', "&quot;")

    expected_lines = [
        "  <!--SEO:BEGIN-->",
        f"  <title>{html_esc(exp_title)}</title>",
        f'  <meta name="description" content="{html_esc(exp_desc)}">',
        f'  <link rel="canonical" href="{html_esc(canonical)}">',
        f'  <meta name="theme-color" content="{html_esc(theme)}">',
        '  <meta property="og:type" content="website">',
        f'  <meta property="og:url" content="{html_esc(canonical)}">',
        f'  <meta property="og:title" content="{html_esc(exp_title)}">',
        f'  <meta property="og:description" content="{html_esc(exp_desc)}">',
        f'  <meta property="og:image" content="{site}/assets/og/pisa.png">',
        '  <meta property="og:image:width" content="1200">',
        '  <meta property="og:image:height" content="630">',
        f'  <meta property="og:image:alt" content="{html_esc(exp_ogalt)}">',
        '  <meta property="og:site_name" content="Dashboard Data Publik">',
        '  <meta property="og:locale" content="id_ID">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f'  <meta name="twitter:title" content="{html_esc(exp_title)}">',
        f'  <meta name="twitter:description" content="{html_esc(exp_desc)}">',
        f'  <meta name="twitter:image" content="{site}/assets/og/pisa.png">',
        '  <link rel="icon" href="/favicon.ico" sizes="32x32">',
        '  <link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        '  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">',
        '  <link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        f'  <script type="application/ld+json">{exp_jsonld}</script>',
        "  <!--SEO:END-->",
    ]

    actual_lines = actual_block.splitlines()
    if len(actual_lines) != len(expected_lines):
        return (
            False,
            f"pisa/index.html SEO block line count mismatch (actual: {len(actual_lines)}, expected: {len(expected_lines)})",
        )

    for idx, (act_l, exp_l) in enumerate(zip(actual_lines, expected_lines)):
        if act_l != exp_l:
            return (
                False,
                f"pisa/index.html SEO block line {idx + 1} mismatch (actual: {act_l!r}, expected: {exp_l!r})",
            )

    return (
        True,
        "C2 head kit region, tags, attributes, copy constraints, icon links, and C5 JSON-LD graph verified",
    )


def check_17_plain_language(index_content: str):
    clean_content = re.sub(r"<!--.*?-->", "", index_content, flags=re.DOTALL)
    clean_content = re.sub(r"<style[^>]*>.*?</style>", "", clean_content, flags=re.DOTALL | re.IGNORECASE)

    banned_terms = [
        "galat baku",
        "standard error",
        "standar error",
        "plausible values",
        "nilai kemungkinan",
        "margin of error",
        "sosio-ekonomi",
        "socio-economic",
        "rerata",
        "galat",
        "kuartil",
        "persentil",
        "median",
        "proporsi",
        "estimasi",
        "kemahiran",
        "kecakapan",
        "sosioekonomi",
        "escs",
        "replikasi",
        "koefisien",
        "varians",
        "imputasi",
        "deterministik",
        "reprodusibilitas",
        "populasi",
        "parameter",
        "deskriptif",
        "kausal",
        "asimetris",
    ]
    pattern_parts = [r"\b" + re.escape(t) + r"\b" for t in banned_terms]
    pattern_parts.append(r"\bsd:")
    pattern_parts.append(r"\bLevel\s*[1-6][abc]?\b")
    pattern_parts.append(r"\bP(?:10|25|50|75|90)\b")
    banned_regex = re.compile("|".join(pattern_parts), re.IGNORECASE)

    section_re = re.compile(
        r'<section\s+[^>]*id=["\'](band-[^"\']+)["\'][^>]*>(.*?)</section>',
        re.DOTALL,
    )
    pos = 0
    segments = []
    for m in section_re.finditer(clean_content):
        if m.start() > pos:
            segments.append(("outside-bands", clean_content[pos : m.start()]))
        band_id = m.group(1)
        band_content = m.group(2)
        segments.append((band_id, band_content))
        pos = m.end()
    if pos < len(clean_content):
        segments.append(("outside-bands", clean_content[pos:]))

    total_candidates = 0
    covered_bands = set()

    for band_id, text in segments:
        if band_id == "band-method":
            text = re.sub(r"\([^)]*\)", "", text)

        html_no_script = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text_nodes = re.findall(r">([^<]+)<", html_no_script)
        sq_literals = re.findall(r"'([^'\n\r]*)'", text)
        dq_literals = re.findall(r'"([^"\n\r]*)"', text)

        for item in text_nodes + sq_literals + dq_literals:
            if " " in item:
                total_candidates += 1
                if band_id != "outside-bands":
                    covered_bands.add(band_id)
                candidate = re.sub(r"[#.][\w-]*escs[\w-]*", "", item, flags=re.IGNORECASE)
                m = banned_regex.search(candidate)
                if m:
                    display = " ".join(item.split())
                    return (
                        False,
                        f"plain language: banned term '{m.group(0)}' found in {band_id}: {display[:120]!r}",
                    )

    return (
        True,
        f"plain language clean: 0 jargon hits across {total_candidates} reader-facing strings in {len(covered_bands)} bands",
    )


def main():
    csv_dir = os.path.join(ROOT, "data", "pisa")
    template_path = os.path.join(ROOT, "pisa.template.html")
    fonts_dir = os.path.join(ROOT, "fonts")
    index_path = os.path.join(ROOT, "pisa", "index.html")

    required_files = [
        (os.path.join(csv_dir, "annex_means.csv"), "annex_means.csv"),
        (os.path.join(csv_dir, "annex_trend_science.csv"), "annex_trend_science.csv"),
        (os.path.join(csv_dir, "annex_proficiency.csv"), "annex_proficiency.csv"),
        (os.path.join(csv_dir, "annex_lowtop.csv"), "annex_lowtop.csv"),
        (os.path.join(csv_dir, "annex_variation_science.csv"), "annex_variation_science.csv"),
        (os.path.join(csv_dir, "puf_quartile_cuts.csv"), "puf_quartile_cuts.csv"),
        (os.path.join(csv_dir, "puf_estimates.csv"), "puf_estimates.csv"),
        (os.path.join(csv_dir, "puf_counts.csv"), "puf_counts.csv"),
        (template_path, "pisa.template.html"),
        (index_path, "pisa/index.html"),
    ]

    for path, name in required_files:
        if not os.path.exists(path):
            print(f"FAIL Input file missing: {name} (actual: not found at {path}, expected: file present)")
            print("TOTAL 0/17")
            sys.exit(1)

    with open(index_path, "r", encoding="utf-8") as f:
        line1 = f.readline().rstrip("\r\n")
        index_content = line1 + "\n" + f.read()

    prefix = "const DATA = "
    idx = index_content.find(prefix)
    if idx == -1:
        print("FAIL Embedded DATA not found in pisa/index.html")
        print("TOTAL 0/17")
        sys.exit(1)

    start_json = idx + len(prefix)
    end_json = index_content.find(";\n", start_json)
    if end_json == -1:
        end_json = index_content.find(";", start_json)
    if end_json == -1:
        print("FAIL Embedded DATA end delimiter not found in pisa/index.html")
        print("TOTAL 0/17")
        sys.exit(1)

    try:
        embedded_data = json.loads(index_content[start_json:end_json])
    except Exception as e:
        print(f"FAIL Embedded DATA JSON parse failed: {e}")
        print("TOTAL 0/17")
        sys.exit(1)

    rebuilt_payload = rebuild_payload_from_csv(csv_dir, template_path)

    checks = [
        ("stamp", lambda: check_1_stamp(index_path, template_path, csv_dir, fonts_dir)),
        ("placeholders", lambda: check_2_placeholders(index_content)),
        ("payload equality", lambda: check_3_payload_equality(embedded_data, rebuilt_payload)),
        ("proficiency", lambda: check_4_proficiency(embedded_data, csv_dir)),
        ("percentiles", lambda: check_5_percentiles(embedded_data, csv_dir)),
        ("hero", lambda: check_6_hero(embedded_data, csv_dir)),
        ("trend", lambda: check_7_trend(embedded_data, csv_dir, index_content)),
        ("peers", lambda: check_8_peers(embedded_data)),
        ("fig coverage", lambda: check_9_fig_coverage(embedded_data, template_path)),
        ("reconciliation", lambda: check_10_reconciliation(csv_dir)),
        ("hygiene", lambda: check_11_hygiene(index_content)),
        ("page skeleton", lambda: check_12_page_skeleton(index_path, template_path, fonts_dir)),
        ("fallback numbers", lambda: check_13_fallback_numbers(template_path, embedded_data, index_content)),
        ("meta provenance", lambda: check_14_meta_provenance(embedded_data, csv_dir)),
        ("peers caveat", lambda: check_15_peers_caveat(embedded_data, csv_dir)),
        ("head kit and JSON-LD", lambda: check_16_head_and_jsonld(index_path, template_path, embedded_data)),
        ("plain language", lambda: check_17_plain_language(index_content)),
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
