#!/usr/bin/env python3
"""Aggregate OECD PISA 2025 annex CSVs and PUF counts into pisa-explorer/data.json."""

import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV_DIR = ROOT / "data" / "pisa"
DEFAULT_OUT = ROOT / "pisa-explorer" / "data.json"
MAX_BYTES = 220 * 1024

INPUT_FILES = (
    "annex_lowtop.csv",
    "annex_means.csv",
    "annex_proficiency.csv",
    "annex_trend_science.csv",
    "annex_variation_science.csv",
    "puf_counts.csv",
)

AGGREGATES = frozenset({
    "OECD average",
    "OECD total",
    "OECD average-23",
    "OECD average-35",
})

# Verbatim Indonesian display name map from pisa.template.html (around line 1375)
# so /pisa/ and /pisa-explorer/ never disagree on country spelling.
COUNTRY_MAP = {
    "Singapore": "Singapura",
    "Viet Nam": "Vietnam",
    "Brunei Darussalam": "Brunei Darussalam",
    "Malaysia": "Malaysia",
    "Thailand": "Thailand",
    "Indonesia": "Indonesia",
    "Cambodia": "Kamboja",
    "Philippines": "Filipina",
}

DOMAINS = ["science", "mathematics", "reading"]
DOMAIN_LABEL = {
    "science": "Sains",
    "mathematics": "Matematika",
    "reading": "Membaca",
}
SCORE_KEYS = ["mean", "se", "sd", "p10", "p25", "p50", "p75", "p90"]
# Band keys = the CSV stat names verbatim (single source of truth = annex_proficiency.csv),
# in the fixed display order from INTERFACE.md (bottom band first).
BAND_KEYS = [
    "level_below_1c",
    "level_1c",
    "level_below_1b",
    "level_1b",
    "level_1a",
    "level_2",
    "level_3",
    "level_4",
    "level_5",
    "level_6",
]
VARIATION_KEYS = ["total", "between", "within", "between_pct"]
VARIATION_CSV_STATS = [
    "total_variance",
    "between_school_variance",
    "within_school_variance",
    "between_school_pct_of_oecd_average",
]


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def parse_float(raw: str | None, decimals: int) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    val = float(raw)
    if not math.isfinite(val):
        raise ValueError(f"Non-finite numeric value in CSV: {raw!r}")
    rounded = round(val, decimals)
    return 0.0 if rounded == 0.0 else rounded


def clean_table_id(raw_table: str) -> str:
    t = raw_table.strip()
    if t.startswith("Table "):
        t = t[len("Table "):].strip()
    if not t:
        raise ValueError("Empty table identifier in CSV")
    return t


def load_inputs(csv_dir: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, str], dict[str, str]]:
    if not csv_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {csv_dir}")

    input_sha256: dict[str, str] = {}
    for fname in INPUT_FILES:
        fpath = csv_dir / fname
        if not fpath.is_file():
            raise FileNotFoundError(f"Missing required input file: {fpath}")
        input_sha256[fname] = sha256_file(fpath)

    # ponytail: loads all ~8.4k annex rows into dicts in memory; fine up to ~100k rows, stream by key if raw PUF is ever read here.
    tables_rows: dict[str, list[dict[str, str]]] = {}
    for fname in INPUT_FILES:
        if fname == "puf_counts.csv":
            continue
        with open(csv_dir / fname, "r", encoding="utf-8", newline="") as f:
            tables_rows[fname] = list(csv.DictReader(f))

    puf_counts: dict[str, str] = {}
    with open(csv_dir / "puf_counts.csv", "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            k = (row.get("key") or "").strip()
            v = (row.get("value") or "").strip()
            if k:
                puf_counts[k] = v

    return tables_rows, puf_counts, input_sha256


def build_payload(csv_dir: Path = DEFAULT_CSV_DIR) -> tuple[dict, dict[str, int]]:
    tables_rows, puf_counts, input_sha256 = load_inputs(csv_dir)

    means_rows = tables_rows["annex_means.csv"]
    prof_rows = tables_rows["annex_proficiency.csv"]
    lowtop_rows = tables_rows["annex_lowtop.csv"]
    trend_rows = tables_rows["annex_trend_science.csv"]
    var_rows = tables_rows["annex_variation_science.csv"]

    all_subjects: set[str] = set()
    for rows in tables_rows.values():
        for r in rows:
            sub = (r.get("subject") or "").strip()
            if sub:
                all_subjects.add(sub)

    participant_sources = sorted(s for s in all_subjects if s not in AGGREGATES)
    if len(participant_sources) != 91:
        raise RuntimeError(f"Expected 91 participants, found {len(participant_sources)}")

    idn_entry = None
    other_participants = []
    seen_ids: set[str] = set()

    for raw_sub in participant_sources:
        stripped = raw_sub.rstrip("*").strip()
        display_name = COUNTRY_MAP.get(stripped, stripped)
        # Keep ASCII slug stable against stripped English source name where INTERFACE.md specifies "Viet Nam" -> "viet-nam".
        cid = slugify(stripped)
        if not cid or cid in seen_ids:
            raise RuntimeError(f"Duplicate or empty country id {cid!r} for {raw_sub!r}")
        seen_ids.add(cid)
        entry = {
            "id": cid,
            "name": display_name,
            "source": raw_sub,
            "kind": "participant",
        }
        if cid == "indonesia":
            idn_entry = entry
        else:
            other_participants.append(entry)

    if idn_entry is None:
        raise RuntimeError("Indonesia not found among participants")

    other_participants.sort(key=lambda c: (c["name"].lower(), c["name"], c["id"]))
    oecd_entry = {
        "id": "oecd-average",
        "name": "Rata-rata OECD",
        "source": "OECD average",
        "kind": "aggregate",
    }
    countries = [idn_entry, oecd_entry] + other_participants

    means_idx: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in means_rows:
        means_idx[(r["subject"], r["domain"], r["stat"])] = r

    scores: dict[str, dict[str, list[float | None]]] = {}
    csv_missing_scores = 0
    for c in countries:
        src = c["source"]
        cid = c["id"]
        scores[cid] = {}
        for dom in DOMAINS:
            mean_row = means_idx.get((src, dom, "mean"))
            mean_val = parse_float(mean_row["value"], 2) if mean_row else None
            se_val = parse_float(mean_row["se"], 3) if mean_row else None
            if mean_val is None:
                csv_missing_scores += 1
            if se_val is None:
                csv_missing_scores += 1
            row_vals: list[float | None] = [mean_val, se_val]
            for st in SCORE_KEYS[2:]:
                st_row = means_idx.get((src, dom, st))
                v = parse_float(st_row["value"], 2) if st_row else None
                if v is None:
                    csv_missing_scores += 1
                row_vals.append(v)
            scores[cid][dom] = row_vals

    prof_idx: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in prof_rows:
        stat_raw = r["stat"]
        if stat_raw not in BAND_KEYS:
            raise RuntimeError(f"Unexpected proficiency stat: {stat_raw!r}")
        prof_idx[(r["subject"], r["domain"], stat_raw)] = r

    levels: dict[str, dict[str, list[float | None]]] = {}
    levels_se: dict[str, dict[str, list[float | None]]] = {}
    csv_missing_levels = 0
    csv_missing_levels_se = 0
    for c in countries:
        src = c["source"]
        cid = c["id"]
        levels[cid] = {}
        levels_se[cid] = {}
        for dom in DOMAINS:
            band_vals: list[float | None] = []
            band_ses: list[float | None] = []
            for bkey in BAND_KEYS:
                p_row = prof_idx.get((src, dom, bkey))
                v = parse_float(p_row["value"], 2) if p_row else None
                se_v = parse_float(p_row["se"], 2) if p_row else None
                if v is None:
                    csv_missing_levels += 1
                if se_v is None:
                    csv_missing_levels_se += 1
                band_vals.append(v)
                band_ses.append(se_v)
            levels[cid][dom] = band_vals
            levels_se[cid][dom] = band_ses

    lowtop_idx: dict[tuple[str, str, str, int], dict[str, str]] = {}
    lowtop_years: dict[tuple[str, str], set[int]] = {}
    for r in lowtop_rows:
        yr = int(r["cycle"])
        lowtop_years.setdefault((r["subject"], r["domain"]), set()).add(yr)
        lowtop_idx[(r["subject"], r["domain"], r["stat"], yr)] = r

    share: dict[str, dict[str, dict[str, list]]] = {}
    csv_missing_share = 0
    for c in countries:
        src = c["source"]
        cid = c["id"]
        share[cid] = {}
        for dom in DOMAINS:
            yrs = sorted(lowtop_years.get((src, dom), set()))
            below_l2: list[float | None] = []
            above_l4: list[float | None] = []
            for yr in yrs:
                r_b2 = lowtop_idx.get((src, dom, "below_l2", yr))
                r_a4 = lowtop_idx.get((src, dom, "above_l4", yr))
                v_b2 = parse_float(r_b2["value"], 2) if r_b2 else None
                v_a4 = parse_float(r_a4["value"], 2) if r_a4 else None
                if v_b2 is None:
                    csv_missing_share += 1
                if v_a4 is None:
                    csv_missing_share += 1
                below_l2.append(v_b2)
                above_l4.append(v_a4)
            share[cid][dom] = {
                "years": yrs,
                "below_l2": below_l2,
                "above_l4": above_l4,
            }

    trend_idx: dict[tuple[str, int], dict[str, str]] = {}
    trend_years: dict[str, set[int]] = {}
    for r in trend_rows:
        if r["domain"] == "science" and r["stat"] == "mean":
            yr = int(r["cycle"])
            trend_years.setdefault(r["subject"], set()).add(yr)
            trend_idx[(r["subject"], yr)] = r

    trend_science: dict[str, dict[str, list]] = {}
    csv_missing_trend = 0
    trend_entities = [(c["id"], c["source"]) for c in countries] + [("oecd-23", "OECD average-23")]
    for cid, src in trend_entities:
        yrs = sorted(trend_years.get(src, set()))
        mean_arr: list[float | None] = []
        se_arr: list[float | None] = []
        for yr in yrs:
            t_row = trend_idx.get((src, yr))
            v_m = parse_float(t_row["value"], 2) if t_row else None
            v_se = parse_float(t_row["se"], 3) if t_row else None
            if v_m is None:
                csv_missing_trend += 1
            if v_se is None:
                csv_missing_trend += 1
            mean_arr.append(v_m)
            se_arr.append(v_se)
        trend_science[cid] = {
            "years": yrs,
            "mean": mean_arr,
            "se": se_arr,
        }

    var_idx: dict[tuple[str, str], dict[str, str]] = {}
    for r in var_rows:
        if r["domain"] == "science":
            var_idx[(r["subject"], r["stat"])] = r

    variation_science: dict[str, list[float | None]] = {}
    csv_missing_variation = 0
    for c in countries:
        src = c["source"]
        cid = c["id"]
        v_arr: list[float | None] = []
        for st in VARIATION_CSV_STATS:
            v_row = var_idx.get((src, st))
            v = parse_float(v_row["value"], 2) if v_row else None
            if v is None:
                csv_missing_variation += 1
            v_arr.append(v)
        variation_science[cid] = v_arr

    prof_tables: dict[str, str] = {}
    for r in prof_rows:
        d = r["domain"]
        if d not in prof_tables and r.get("table"):
            prof_tables[d] = clean_table_id(r["table"])

    tables_meta = {
        "scores": next(clean_table_id(r["table"]) for r in means_rows if r["domain"] == "science" and r.get("table")),
        "proficiency": {d: prof_tables[d] for d in DOMAINS},
        "share": next(clean_table_id(r["table"]) for r in lowtop_rows if r["domain"] == "science" and r.get("table")),
        "trend": next(clean_table_id(r["table"]) for r in trend_rows if r["domain"] == "science" and r.get("table")),
        "variation": next(clean_table_id(r["table"]) for r in var_rows if r["domain"] == "science" and r.get("table")),
    }

    # Setiap key/value dari puf_counts.csv apa adanya (tanpa key karangan, tanpa null), plus dua hitungan turunan.
    counts_meta: dict[str, int] = {
        "participants": len(participant_sources),
        "domains": len(DOMAINS),
    }
    for k, v in puf_counts.items():
        counts_meta[k] = int(v)

    payload = {
        "meta": {
            "licence": "CC BY 4.0",
            "source_note": "tabel annex OECD PISA 2025 + berkas siswa (PUF)",
            "input_sha256": input_sha256,
            "counts": counts_meta,
            "tables": tables_meta,
        },
        "domains": list(DOMAINS),
        "domain_label": dict(DOMAIN_LABEL),
        "score_keys": list(SCORE_KEYS),
        "band_keys": list(BAND_KEYS),
        "variation_keys": list(VARIATION_KEYS),
        "countries": countries,
        "scores": scores,
        "levels": levels,
        "levels_se": levels_se,
        "share": share,
        "trend_science": trend_science,
        "variation_science": variation_science,
    }

    expected_nulls = {
        "scores": csv_missing_scores,
        "levels": csv_missing_levels,
        "levels_se": csv_missing_levels_se,
        "share": csv_missing_share,
        "trend_science": csv_missing_trend,
        "variation_science": csv_missing_variation,
    }
    return payload, expected_nulls


def serialize_payload(payload: dict) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (text + "\n").encode("utf-8")


def count_payload_nulls(payload: dict) -> dict[str, int]:
    scores_nulls = sum(
        1
        for dom_map in payload["scores"].values()
        for arr in dom_map.values()
        for v in arr
        if v is None
    )
    levels_nulls = sum(
        1
        for dom_map in payload["levels"].values()
        for arr in dom_map.values()
        for v in arr
        if v is None
    )
    levels_se_nulls = sum(
        1
        for dom_map in payload["levels_se"].values()
        for arr in dom_map.values()
        for v in arr
        if v is None
    )
    share_nulls = sum(
        1
        for dom_map in payload["share"].values()
        for series in dom_map.values()
        for key in ("below_l2", "above_l4")
        for v in series[key]
        if v is None
    )
    trend_nulls = sum(
        1
        for series in payload["trend_science"].values()
        for key in ("mean", "se")
        for v in series[key]
        if v is None
    )
    variation_nulls = sum(
        1
        for arr in payload["variation_science"].values()
        for v in arr
        if v is None
    )
    return {
        "scores": scores_nulls,
        "levels": levels_nulls,
        "levels_se": levels_se_nulls,
        "share": share_nulls,
        "trend_science": trend_nulls,
        "variation_science": variation_nulls,
    }


def _walk_finite(node: object) -> None:
    if isinstance(node, float):
        if not math.isfinite(node):
            raise AssertionError(f"Non-finite float in payload: {node!r}")
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk_finite(k)
            _walk_finite(v)
    elif isinstance(node, list):
        for item in node:
            _walk_finite(item)


def verify_payload(payload: dict, expected_nulls: dict[str, int], csv_dir: Path = DEFAULT_CSV_DIR) -> bytes:
    _walk_finite(payload)
    raw_bytes = serialize_payload(payload)
    if len(raw_bytes) > MAX_BYTES:
        raise AssertionError(f"Payload size {len(raw_bytes)} exceeds {MAX_BYTES} bytes")

    text = raw_bytes.decode("utf-8")
    if "NaN" in text or "Infinity" in text:
        raise AssertionError("NaN or Infinity token found in serialized JSON")

    countries = payload["countries"]
    participants = [c for c in countries if c.get("kind") == "participant"]
    aggregates = [c for c in countries if c.get("kind") == "aggregate"]
    if len(participants) != 91 or len(aggregates) != 1:
        raise AssertionError(f"Expected 91 participants + 1 aggregate, got {len(participants)} + {len(aggregates)}")
    if countries[0]["id"] != "indonesia" or countries[1]["id"] != "oecd-average":
        raise AssertionError("countries must start with indonesia followed by oecd-average")

    for c in countries:
        cid = c["id"]
        if cid not in payload["scores"] or set(payload["scores"][cid].keys()) != set(DOMAINS):
            raise AssertionError(f"Country {cid} missing domains in scores")
        if cid not in payload["levels"] or set(payload["levels"][cid].keys()) != set(DOMAINS):
            raise AssertionError(f"Country {cid} missing domains in levels")
        if cid not in payload["levels_se"] or set(payload["levels_se"][cid].keys()) != set(DOMAINS):
            raise AssertionError(f"Country {cid} missing domains in levels_se")

    with open(csv_dir / "annex_means.csv", "r", encoding="utf-8", newline="") as f:
        means_rows = list(csv.DictReader(f))
    csv_idn_scie = next(
        float(r["value"])
        for r in means_rows
        if r["subject"] == "Indonesia" and r["domain"] == "science" and r["stat"] == "mean"
    )
    csv_oecd_scie = next(
        float(r["value"])
        for r in means_rows
        if r["subject"] == "OECD average" and r["domain"] == "science" and r["stat"] == "mean"
    )

    idn_scie = payload["scores"]["indonesia"]["science"][0]
    oecd_scie = payload["scores"]["oecd-average"]["science"][0]
    if idn_scie is None or abs(idn_scie - csv_idn_scie) > 0.005:
        raise AssertionError(f"Indonesia science mean {idn_scie} not within 0.005 of CSV {csv_idn_scie}")
    if oecd_scie is None or abs(oecd_scie - csv_oecd_scie) > 0.005:
        raise AssertionError(f"OECD average science mean {oecd_scie} not within 0.005 of CSV {csv_oecd_scie}")

    for cid, dom_map in payload["levels"].items():
        for dom, arr in dom_map.items():
            non_null = [v for v in arr if v is not None]
            if not non_null:
                continue
            total = sum(non_null)
            if abs(total - 100.0) > 0.6:
                raise AssertionError(f"Levels sum for {cid}/{dom} is {total:.2f}, outside 100 +/- 0.6")

    # Setiap SE band harus SE asli dari annex_proficiency.csv, bukan estimasi.
    src_to_id = {c["source"]: c["id"] for c in countries}
    with open(csv_dir / "annex_proficiency.csv", "r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            cid = src_to_id.get(r["subject"])
            if cid is None or r["stat"] not in BAND_KEYS:
                continue
            idx = BAND_KEYS.index(r["stat"])
            got_se = payload["levels_se"][cid][r["domain"]][idx]
            csv_se = parse_float(r["se"], 2)
            if csv_se is None:
                if got_se is not None:
                    raise AssertionError(
                        f"levels_se[{cid}][{r['domain']}].{r['stat']} = {got_se!r} but CSV se is empty"
                    )
            elif got_se is None or abs(got_se - csv_se) > 0.005:
                raise AssertionError(
                    f"levels_se[{cid}][{r['domain']}].{r['stat']} = {got_se!r}, CSV se = {csv_se}"
                )

    observed_nulls = count_payload_nulls(payload)
    if observed_nulls != expected_nulls:
        raise AssertionError(f"Null count mismatch: observed={observed_nulls} vs expected={expected_nulls}")

    return raw_bytes


def run_self_check(csv_dir: Path = DEFAULT_CSV_DIR) -> tuple[bytes, dict[str, int], int]:
    payload1, expected_nulls1 = build_payload(csv_dir)
    bytes1 = verify_payload(payload1, expected_nulls1, csv_dir)

    payload2, expected_nulls2 = build_payload(csv_dir)
    bytes2 = verify_payload(payload2, expected_nulls2, csv_dir)

    if bytes1 != bytes2:
        raise AssertionError("Two consecutive builds were not byte-identical")

    # Prove the self-check actually rejects torn data.
    torn = copy.deepcopy(payload1)
    torn["scores"]["indonesia"]["science"][0] += 1.0
    try:
        verify_payload(torn, expected_nulls1, csv_dir)
    except AssertionError:
        pass
    else:
        raise AssertionError("Self-check failed to reject torn Indonesia science mean")

    torn_null = copy.deepcopy(payload1)
    torn_null["levels"]["indonesia"]["science"][0] = 0.0
    try:
        verify_payload(torn_null, expected_nulls1, csv_dir)
    except AssertionError:
        pass
    else:
        raise AssertionError("Self-check failed to reject null-to-zero substitution")

    torn_se = copy.deepcopy(payload1)
    se_arr = torn_se["levels_se"]["indonesia"]["science"]
    idx = next(i for i, v in enumerate(se_arr) if v is not None)
    se_arr[idx] = round(se_arr[idx] + 0.5, 2)
    try:
        verify_payload(torn_se, expected_nulls1, csv_dir)
    except AssertionError:
        pass
    else:
        raise AssertionError("Self-check failed to reject a tampered band SE")

    participants_count = payload1["meta"]["counts"]["participants"]
    return bytes1, count_payload_nulls(payload1), participants_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract PISA 2025 explorer payload JSON.")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON path (default: pisa-explorer/data.json)")
    parser.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR, help="Directory with input CSVs")
    parser.add_argument("--check", action="store_true", help="Rebuild in memory and run self-checks")
    args = parser.parse_args(argv)

    try:
        raw_bytes, nulls_by_block, participants_count = run_self_check(args.csv_dir)
        if not args.check or args.out is not None:
            out_path = args.out if args.out is not None else DEFAULT_OUT
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(raw_bytes)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    short_sha = hashlib.sha256(raw_bytes).hexdigest()[:12]
    null_summary = " ".join(f"{k}={v}" for k, v in nulls_by_block.items())
    print(
        f"OK participants={participants_count} nulls({null_summary}) "
        f"bytes={len(raw_bytes)} sha256={short_sha}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
