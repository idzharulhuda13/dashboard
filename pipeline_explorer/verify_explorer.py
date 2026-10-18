#!/usr/bin/env python3
"""Independent acceptance gate for pisa-explorer/data.json."""

import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAYLOAD = ROOT / "pisa-explorer" / "data.json"
DEFAULT_CSV_DIR = ROOT / "data" / "pisa"
MAX_BYTES = 220 * 1024

INPUT_FILES = (
    "annex_lowtop.csv",
    "annex_means.csv",
    "annex_proficiency.csv",
    "annex_trend_science.csv",
    "annex_variation_science.csv",
    "puf_counts.csv",
)

FORBIDDEN_COUNTRY_SOURCES = frozenset({
    "OECD total",
    "OECD average-23",
    "OECD average-35",
})
ALL_AGGREGATES = FORBIDDEN_COUNTRY_SOURCES | {"OECD average"}

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
TOP_KEYS = (
    "meta",
    "domains",
    "domain_label",
    "score_keys",
    "band_keys",
    "variation_keys",
    "countries",
    "scores",
    "levels",
    "levels_se",
    "share",
    "trend_science",
    "variation_science",
)
TIMESTAMP_KEYS = frozenset({
    "timestamp",
    "generated_at",
    "created_at",
    "updated_at",
    "retrieved",
    "built_at",
    "date",
    "time",
})
ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def subject_to_id(subject: str) -> str:
    if subject == "OECD average":
        return "oecd-average"
    if subject == "OECD average-23":
        return "oecd-23"
    return slugify(subject.rstrip("*").strip())


def is_num(val: object) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def is_int(val: object) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def clean_table_id(raw_table: str) -> str:
    t = raw_table.strip()
    return t[len("Table "):].strip() if t.startswith("Table ") else t


def load_csv_bundle(csv_dir: Path) -> dict[str, object]:
    # ponytail: loads all ~8.4k annex rows into memory at once; fine under 100k rows, stream per file if raw PUF is added.
    rows_by_file: dict[str, list[dict[str, str]]] = {}
    shas: dict[str, str] = {}
    for fname in INPUT_FILES:
        fpath = csv_dir / fname
        if not fpath.is_file():
            raise FileNotFoundError(f"missing CSV file: {fpath}")
        shas[fname] = sha256_file(fpath)
        if fname != "puf_counts.csv":
            with open(fpath, "r", encoding="utf-8", newline="") as f:
                rows_by_file[fname] = list(csv.DictReader(f))

    puf_counts: dict[str, int] = {}
    with open(csv_dir / "puf_counts.csv", "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            k = (row.get("key") or "").strip()
            v = (row.get("value") or "").strip()
            if k:
                puf_counts[k] = int(v)

    all_subjects: set[str] = set()
    for rows in rows_by_file.values():
        for r in rows:
            sub = (r.get("subject") or "").strip()
            if sub:
                all_subjects.add(sub)

    participants = sorted(s for s in all_subjects if s not in ALL_AGGREGATES)
    return {
        "rows": rows_by_file,
        "shas": shas,
        "puf_counts": puf_counts,
        "participants": participants,
    }


def check_1_schema(payload: object) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload root is not a JSON object"

    missing = [k for k in TOP_KEYS if k not in payload]
    extra = [k for k in payload if k not in TOP_KEYS]
    if missing or extra:
        return False, f"top-level keys mismatch (missing={missing}, extra={extra})"

    if payload["domains"] != DOMAINS:
        return False, f"domains {payload['domains']!r} != {DOMAINS!r}"
    if payload["domain_label"] != DOMAIN_LABEL:
        return False, f"domain_label {payload['domain_label']!r} != {DOMAIN_LABEL!r}"
    if payload["score_keys"] != SCORE_KEYS:
        return False, f"score_keys {payload['score_keys']!r} != {SCORE_KEYS!r}"
    if payload["band_keys"] != BAND_KEYS:
        return False, f"band_keys {payload['band_keys']!r} != {BAND_KEYS!r}"
    if payload["variation_keys"] != VARIATION_KEYS:
        return False, f"variation_keys {payload['variation_keys']!r} != {VARIATION_KEYS!r}"

    meta = payload["meta"]
    if not isinstance(meta, dict):
        return False, "meta is not a dict"
    for mk, mtype in (
        ("licence", str),
        ("source_note", str),
        ("input_sha256", dict),
        ("counts", dict),
        ("tables", dict),
    ):
        if mk not in meta or not isinstance(meta[mk], mtype):
            return False, f"meta.{mk} missing or wrong type"
    if meta["licence"] != "CC BY 4.0" or not meta["source_note"].strip():
        return False, "meta.licence or meta.source_note invalid"
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in meta["input_sha256"].items()):
        return False, "meta.input_sha256 entries must be strings"
    if not all(isinstance(k, str) and is_int(v) for k, v in meta["counts"].items()):
        return False, "meta.counts values must be integers"

    tables = meta["tables"]
    for tk in ("scores", "proficiency", "share", "trend", "variation"):
        if tk not in tables:
            return False, f"meta.tables missing {tk!r}"
    if not isinstance(tables["proficiency"], dict) or set(tables["proficiency"].keys()) != set(DOMAINS):
        return False, "meta.tables.proficiency must cover all 3 domains"

    countries = payload["countries"]
    if not isinstance(countries, list) or not countries:
        return False, "countries must be a non-empty list"
    for c in countries:
        if not isinstance(c, dict) or set(c.keys()) != {"id", "name", "source", "kind"}:
            return False, f"country entry has wrong keys: {c!r}"
        if not all(isinstance(c[k], str) and c[k] for k in ("id", "name", "source", "kind")):
            return False, f"country entry has non-string or empty field: {c!r}"
        if c["kind"] not in ("participant", "aggregate"):
            return False, f"invalid country kind {c['kind']!r}"

    for block_name, expected_len in (("scores", len(SCORE_KEYS)), ("levels", len(BAND_KEYS)), ("levels_se", len(BAND_KEYS))):
        block = payload[block_name]
        if not isinstance(block, dict):
            return False, f"{block_name} must be a dict"
        for cid, dom_map in block.items():
            if not isinstance(dom_map, dict):
                return False, f"{block_name}[{cid!r}] must be a dict"
            for dom, arr in dom_map.items():
                if dom not in DOMAINS or not isinstance(arr, list) or len(arr) != expected_len:
                    return False, f"{block_name}[{cid!r}][{dom!r}] must be a list of length {expected_len}"
                if not all(v is None or is_num(v) for v in arr):
                    return False, f"{block_name}[{cid!r}][{dom!r}] contains non-numeric entry"

    if not isinstance(payload["share"], dict):
        return False, "share must be a dict"
    for cid, dom_map in payload["share"].items():
        if not isinstance(dom_map, dict) or set(dom_map.keys()) != set(DOMAINS):
            return False, f"share[{cid!r}] must map all 3 domains"
        for dom, s in dom_map.items():
            if not isinstance(s, dict) or set(s.keys()) != {"years", "below_l2", "above_l4"}:
                return False, f"share[{cid!r}][{dom!r}] has wrong keys"
            if not isinstance(s["years"], list) or not all(is_int(y) for y in s["years"]):
                return False, f"share[{cid!r}][{dom!r}].years must be list of ints"
            for k in ("below_l2", "above_l4"):
                if not isinstance(s[k], list) or not all(v is None or is_num(v) for v in s[k]):
                    return False, f"share[{cid!r}][{dom!r}].{k} must be list of numbers/nulls"

    if not isinstance(payload["trend_science"], dict):
        return False, "trend_science must be a dict"
    for cid, s in payload["trend_science"].items():
        if not isinstance(s, dict) or set(s.keys()) != {"years", "mean", "se"}:
            return False, f"trend_science[{cid!r}] has wrong keys"
        if not isinstance(s["years"], list) or not all(is_int(y) for y in s["years"]):
            return False, f"trend_science[{cid!r}].years must be list of ints"
        for k in ("mean", "se"):
            if not isinstance(s[k], list) or not all(v is None or is_num(v) for v in s[k]):
                return False, f"trend_science[{cid!r}].{k} must be list of numbers/nulls"

    if not isinstance(payload["variation_science"], dict):
        return False, "variation_science must be a dict"
    for cid, arr in payload["variation_science"].items():
        if not isinstance(arr, list) or len(arr) != len(VARIATION_KEYS):
            return False, f"variation_science[{cid!r}] must be list of length {len(VARIATION_KEYS)}"
        if not all(v is None or is_num(v) for v in arr):
            return False, f"variation_science[{cid!r}] contains non-numeric entry"

    return True, "all INTERFACE.md keys, types, and key orders match"


def check_2_recompute(payload: dict, bundle: dict[str, object]) -> tuple[bool, str]:
    rows_by_file: dict[str, list[dict[str, str]]] = bundle["rows"]  # type: ignore[assignment]
    scores = payload.get("scores", {})
    levels = payload.get("levels", {})
    levels_se = payload.get("levels_se")
    if not isinstance(levels_se, dict):
        return False, "missing levels_se block"
    share = payload.get("share", {})
    trend = payload.get("trend_science", {})
    variation = payload.get("variation_science", {})
    checked_rows = 0

    for r in rows_by_file["annex_means.csv"]:
        sub = r["subject"]
        if sub in FORBIDDEN_COUNTRY_SOURCES:
            continue
        cid = subject_to_id(sub)
        dom = r["domain"]
        stat = r["stat"]
        if cid not in scores or dom not in scores[cid] or stat not in SCORE_KEYS:
            return False, f"missing scores entry for CSV row ({sub!r}, {dom!r}, {stat!r})"
        arr = scores[cid][dom]
        raw_val = (r.get("value") or "").strip()
        if raw_val != "":
            csv_val = float(raw_val)
            got = arr[SCORE_KEYS.index(stat)]
            if got is None or not is_num(got) or not math.isfinite(got) or abs(got - csv_val) > 0.005:
                return False, f"scores[{cid!r}][{dom!r}].{stat} = {got!r}, CSV = {csv_val} (tol 0.005)"
        if stat == "mean":
            raw_se = (r.get("se") or "").strip()
            if raw_se != "":
                csv_se = float(raw_se)
                got_se = arr[SCORE_KEYS.index("se")]
                if got_se is None or not is_num(got_se) or not math.isfinite(got_se) or abs(got_se - csv_se) > 0.0005:
                    return False, f"scores[{cid!r}][{dom!r}].se = {got_se!r}, CSV = {csv_se} (tol 0.0005)"
        checked_rows += 1

    for r in rows_by_file["annex_proficiency.csv"]:
        sub = r["subject"]
        if sub in FORBIDDEN_COUNTRY_SOURCES:
            continue
        cid = subject_to_id(sub)
        dom = r["domain"]
        stat = r["stat"]
        if cid not in levels or dom not in levels[cid] or stat not in BAND_KEYS:
            return False, f"missing levels entry for CSV row ({sub!r}, {dom!r}, {stat!r})"
        raw_val = (r.get("value") or "").strip()
        got_se = (levels_se.get(cid) or {}).get(dom, [None] * len(BAND_KEYS))[BAND_KEYS.index(stat)]
        if raw_val != "":
            csv_val = float(raw_val)
            got = levels[cid][dom][BAND_KEYS.index(stat)]
            if got is None or not is_num(got) or not math.isfinite(got) or abs(got - csv_val) > 0.005:
                return False, f"levels[{cid!r}][{dom!r}].{stat} = {got!r}, CSV = {csv_val} (tol 0.005)"
        raw_se = (r.get("se") or "").strip()
        if raw_se == "":
            if got_se is not None:
                return False, f"levels_se[{cid!r}][{dom!r}].{stat} = {got_se!r} but CSV se is empty"
        else:
            csv_se = round(float(raw_se), 2)
            if got_se is None or not is_num(got_se) or not math.isfinite(got_se) or abs(got_se - csv_se) > 0.0005:
                return False, f"levels_se[{cid!r}][{dom!r}].{stat} = {got_se!r}, CSV se = {csv_se} (tol 0.0005)"
        checked_rows += 1

    for r in rows_by_file["annex_lowtop.csv"]:
        sub = r["subject"]
        if sub in FORBIDDEN_COUNTRY_SOURCES:
            continue
        cid = subject_to_id(sub)
        dom = r["domain"]
        stat = r["stat"]
        yr = int(r["cycle"])
        if cid not in share or dom not in share[cid]:
            return False, f"missing share entry for CSV row ({sub!r}, {dom!r}, {yr})"
        s = share[cid][dom]
        yrs = s.get("years", [])
        if yr not in yrs:
            return False, f"share[{cid!r}][{dom!r}].years missing cycle {yr} from CSV"
        idx = yrs.index(yr)
        arr = s.get(stat, [])
        if idx >= len(arr):
            return False, f"share[{cid!r}][{dom!r}].{stat} shorter than years at cycle {yr}"
        raw_val = (r.get("value") or "").strip()
        if raw_val != "":
            csv_val = float(raw_val)
            got = arr[idx]
            if got is None or not is_num(got) or not math.isfinite(got) or abs(got - csv_val) > 0.005:
                return False, f"share[{cid!r}][{dom!r}].{stat}[{yr}] = {got!r}, CSV = {csv_val} (tol 0.005)"
        checked_rows += 1

    for r in rows_by_file["annex_trend_science.csv"]:
        sub = r["subject"]
        if sub in {"OECD total", "OECD average-35"}:
            continue
        if r["domain"] != "science" or r["stat"] != "mean":
            continue
        cid = subject_to_id(sub)
        yr = int(r["cycle"])
        if cid not in trend:
            return False, f"missing trend_science entry for {sub!r}"
        s = trend[cid]
        yrs = s.get("years", [])
        if yr not in yrs:
            return False, f"trend_science[{cid!r}].years missing cycle {yr} from CSV"
        idx = yrs.index(yr)
        if idx >= len(s.get("mean", [])) or idx >= len(s.get("se", [])):
            return False, f"trend_science[{cid!r}] mean/se shorter than years at {yr}"
        raw_val = (r.get("value") or "").strip()
        if raw_val != "":
            csv_val = float(raw_val)
            got_m = s["mean"][idx]
            if got_m is None or not is_num(got_m) or not math.isfinite(got_m) or abs(got_m - csv_val) > 0.005:
                return False, f"trend_science[{cid!r}].mean[{yr}] = {got_m!r}, CSV = {csv_val} (tol 0.005)"
        raw_se = (r.get("se") or "").strip()
        if raw_se != "":
            csv_se = float(raw_se)
            got_se = s["se"][idx]
            # annex_trend_science.csv SE is rounded to 2 decimals in payload per INTERFACE.md (e.g. 5.73); check against rounded CSV SE within 0.0005.
            exp_se = round(csv_se, 3)
            if got_se is None or not is_num(got_se) or not math.isfinite(got_se) or abs(got_se - exp_se) > 0.0005:
                return False, f"trend_science[{cid!r}].se[{yr}] = {got_se!r}, CSV = {exp_se} (tol 0.0005)"
        checked_rows += 1

    for r in rows_by_file["annex_variation_science.csv"]:
        sub = r["subject"]
        if sub in FORBIDDEN_COUNTRY_SOURCES:
            continue
        if r["domain"] != "science":
            continue
        cid = subject_to_id(sub)
        stat = r["stat"]
        if cid not in variation or stat not in VARIATION_CSV_STATS:
            return False, f"missing variation_science entry for ({sub!r}, {stat!r})"
        idx = VARIATION_CSV_STATS.index(stat)
        arr = variation[cid]
        if idx >= len(arr):
            return False, f"variation_science[{cid!r}] too short for {stat!r}"
        raw_val = (r.get("value") or "").strip()
        if raw_val != "":
            csv_val = float(raw_val)
            got = arr[idx]
            if got is None or not is_num(got) or not math.isfinite(got) or abs(got - csv_val) > 0.005:
                return False, f"variation_science[{cid!r}][{idx}] = {got!r}, CSV = {csv_val} (tol 0.005)"
        checked_rows += 1

    return True, f"recomputed {checked_rows} CSV rows across 5 annex tables within tolerance"


def check_3_completeness(payload: dict, bundle: dict[str, object]) -> tuple[bool, str]:
    countries = payload.get("countries")
    if not isinstance(countries, list):
        return False, "countries is not a list"

    ids = [c.get("id") for c in countries if isinstance(c, dict)]
    sources = [c.get("source") for c in countries if isinstance(c, dict)]
    if len(ids) != len(set(ids)):
        return False, "duplicate country id found in countries"
    if len(sources) != len(set(sources)):
        return False, "duplicate country source found in countries"

    forbidden_ids = {"oecd-total", "oecd-23", "oecd-average-23", "oecd-35", "oecd-average-35"}
    for c in countries:
        if not isinstance(c, dict):
            return False, "non-dict country entry"
        if c.get("source") in FORBIDDEN_COUNTRY_SOURCES or c.get("id") in forbidden_ids:
            return False, f"forbidden aggregate in countries: {c!r}"

    participants = [c for c in countries if c.get("kind") == "participant"]
    aggregates = [c for c in countries if c.get("kind") == "aggregate"]
    if len(participants) != 91:
        return False, f"expected 91 participants in countries, found {len(participants)}"
    if len(aggregates) != 1 or aggregates[0].get("id") != "oecd-average" or aggregates[0].get("source") != "OECD average":
        return False, f"expected exactly 1 oecd-average aggregate, found {aggregates!r}"

    expected_participants: list[str] = bundle["participants"]  # type: ignore[assignment]
    if sorted(c["source"] for c in participants) != expected_participants:
        return False, "participant sources in countries do not match the 91 CSV participants"

    for c in participants:
        stripped = c["source"].rstrip("*").strip()
        exp_id = slugify(stripped)
        exp_name = COUNTRY_MAP.get(stripped, stripped)
        if c["id"] != exp_id or c["name"] != exp_name:
            return False, f"country {c['source']!r} expected id={exp_id!r} name={exp_name!r}, got {c!r}"

    expected_ids = {c["id"] for c in countries}
    for block_name in ("scores", "levels", "share"):
        block = payload.get(block_name, {})
        if set(block.keys()) != expected_ids:
            return False, f"{block_name} country keys do not match countries list"
        for cid in expected_ids:
            if set(block[cid].keys()) != set(DOMAINS):
                return False, f"{block_name}[{cid!r}] does not have all 3 domains"

    if set(payload.get("variation_science", {}).keys()) != expected_ids:
        return False, "variation_science keys do not match countries list"
    if set(payload.get("trend_science", {}).keys()) != (expected_ids | {"oecd-23"}):
        return False, "trend_science keys must equal countries + oecd-23"

    return True, "91 participants + 1 oecd-average complete across all domains and blocks"


def check_4_nulls(payload: dict, bundle: dict[str, object]) -> tuple[bool, str]:
    rows_by_file: dict[str, list[dict[str, str]]] = bundle["rows"]  # type: ignore[assignment]
    participants: list[str] = bundle["participants"]  # type: ignore[assignment]
    all_country_sources = [(subject_to_id(s), s) for s in participants] + [("oecd-average", "OECD average")]

    means_idx = {(r["subject"], r["domain"], r["stat"]): r for r in rows_by_file["annex_means.csv"]}
    prof_idx = {(r["subject"], r["domain"], r["stat"]): r for r in rows_by_file["annex_proficiency.csv"]}

    lowtop_idx: dict[tuple[str, str, str, int], dict[str, str]] = {}
    lowtop_years: dict[tuple[str, str], set[int]] = {}
    for r in rows_by_file["annex_lowtop.csv"]:
        yr = int(r["cycle"])
        lowtop_years.setdefault((r["subject"], r["domain"]), set()).add(yr)
        lowtop_idx[(r["subject"], r["domain"], r["stat"], yr)] = r

    trend_idx: dict[tuple[str, int], dict[str, str]] = {}
    trend_years: dict[str, set[int]] = {}
    for r in rows_by_file["annex_trend_science.csv"]:
        if r["domain"] == "science" and r["stat"] == "mean":
            yr = int(r["cycle"])
            trend_years.setdefault(r["subject"], set()).add(yr)
            trend_idx[(r["subject"], yr)] = r

    var_idx = {
        (r["subject"], r["stat"]): r
        for r in rows_by_file["annex_variation_science.csv"]
        if r["domain"] == "science"
    }

    csv_missing = {"scores": 0, "levels": 0, "share": 0, "trend_science": 0, "variation_science": 0}
    scores = payload.get("scores", {})
    for cid, src in all_country_sources:
        for dom in DOMAINS:
            arr = scores.get(cid, {}).get(dom, [])
            mean_row = means_idx.get((src, dom, "mean"))
            cells = [
                ("mean", mean_row.get("value") if mean_row else None),
                ("se", mean_row.get("se") if mean_row else None),
            ]
            for st in SCORE_KEYS[2:]:
                st_row = means_idx.get((src, dom, st))
                cells.append((st, st_row.get("value") if st_row else None))
            for idx, (st, raw) in enumerate(cells):
                is_blank = raw is None or raw.strip() == ""
                if is_blank:
                    csv_missing["scores"] += 1
                val = arr[idx] if idx < len(arr) else None
                if is_blank and val is not None:
                    if val == 0:
                        return False, f"0 substituted for missing CSV value at scores[{cid!r}][{dom!r}].{st}"
                    return False, f"non-null value {val!r} for missing CSV cell at scores[{cid!r}][{dom!r}].{st}"
                if not is_blank and val is None:
                    return False, f"null in scores[{cid!r}][{dom!r}].{st} where CSV has {raw!r}"

    levels = payload.get("levels", {})
    for cid, src in all_country_sources:
        for dom in DOMAINS:
            arr = levels.get(cid, {}).get(dom, [])
            for idx, bkey in enumerate(BAND_KEYS):
                p_row = prof_idx.get((src, dom, bkey))
                raw = p_row.get("value") if p_row else None
                is_blank = raw is None or raw.strip() == ""
                if is_blank:
                    csv_missing["levels"] += 1
                val = arr[idx] if idx < len(arr) else None
                if is_blank and val is not None:
                    if val == 0:
                        return False, f"0 substituted for missing CSV value at levels[{cid!r}][{dom!r}].{bkey}"
                    return False, f"non-null value {val!r} for missing CSV cell at levels[{cid!r}][{dom!r}].{bkey}"
                if not is_blank and val is None:
                    return False, f"null in levels[{cid!r}][{dom!r}].{bkey} where CSV has {raw!r}"

    share = payload.get("share", {})
    for cid, src in all_country_sources:
        for dom in DOMAINS:
            s = share.get(cid, {}).get(dom, {})
            yrs = sorted(lowtop_years.get((src, dom), set()))
            for idx, yr in enumerate(yrs):
                for st in ("below_l2", "above_l4"):
                    r = lowtop_idx.get((src, dom, st, yr))
                    raw = r.get("value") if r else None
                    is_blank = raw is None or raw.strip() == ""
                    if is_blank:
                        csv_missing["share"] += 1
                    arr = s.get(st, [])
                    val = arr[idx] if idx < len(arr) else None
                    if is_blank and val is not None:
                        return False, f"non-null value {val!r} for missing CSV cell at share[{cid!r}][{dom!r}].{st}[{yr}]"
                    if not is_blank and val is None:
                        return False, f"null in share[{cid!r}][{dom!r}].{st}[{yr}] where CSV has {raw!r}"

    trend = payload.get("trend_science", {})
    for cid, src in all_country_sources + [("oecd-23", "OECD average-23")]:
        s = trend.get(cid, {})
        yrs = sorted(trend_years.get(src, set()))
        for idx, yr in enumerate(yrs):
            t_row = trend_idx.get((src, yr))
            for field, col in (("mean", "value"), ("se", "se")):
                raw = t_row.get(col) if t_row else None
                is_blank = raw is None or raw.strip() == ""
                if is_blank:
                    csv_missing["trend_science"] += 1
                arr = s.get(field, [])
                val = arr[idx] if idx < len(arr) else None
                if is_blank and val is not None:
                    return False, f"non-null value {val!r} for missing CSV cell at trend_science[{cid!r}].{field}[{yr}]"
                if not is_blank and val is None:
                    return False, f"null in trend_science[{cid!r}].{field}[{yr}] where CSV has {raw!r}"

    variation = payload.get("variation_science", {})
    for cid, src in all_country_sources:
        arr = variation.get(cid, [])
        for idx, st in enumerate(VARIATION_CSV_STATS):
            v_row = var_idx.get((src, st))
            raw = v_row.get("value") if v_row else None
            is_blank = raw is None or raw.strip() == ""
            if is_blank:
                csv_missing["variation_science"] += 1
            val = arr[idx] if idx < len(arr) else None
            if is_blank and val is not None:
                return False, f"non-null value {val!r} for missing CSV cell at variation_science[{cid!r}].{st}"
            if not is_blank and val is None:
                return False, f"null in variation_science[{cid!r}].{st} where CSV has {raw!r}"

    observed_nulls = {
        "scores": sum(1 for dm in scores.values() for a in dm.values() for v in a if v is None),
        "levels": sum(1 for dm in levels.values() for a in dm.values() for v in a if v is None),
        "share": sum(
            1
            for dm in share.values()
            for s in dm.values()
            for k in ("below_l2", "above_l4")
            for v in s.get(k, [])
            if v is None
        ),
        "trend_science": sum(
            1 for s in trend.values() for k in ("mean", "se") for v in s.get(k, []) if v is None
        ),
        "variation_science": sum(1 for a in variation.values() for v in a if v is None),
    }
    if observed_nulls != csv_missing:
        return False, f"null counts {observed_nulls} != CSV missing counts {csv_missing}"

    return True, f"null counts match CSV blank cells {observed_nulls} with zero 0-substitutions"


def check_5_bands(payload: dict, bundle: dict[str, object]) -> tuple[bool, str]:
    rows_by_file: dict[str, list[dict[str, str]]] = bundle["rows"]  # type: ignore[assignment]
    present_prof_domains = {
        (subject_to_id(r["subject"]), r["domain"])
        for r in rows_by_file["annex_proficiency.csv"]
        if r["subject"] not in FORBIDDEN_COUNTRY_SOURCES
    }

    levels = payload.get("levels", {})
    checked = 0
    for cid, dom_map in levels.items():
        if not isinstance(dom_map, dict):
            continue
        for dom, arr in dom_map.items():
            if not isinstance(arr, list) or len(arr) != len(BAND_KEYS):
                return False, f"levels[{cid!r}][{dom!r}] length != {len(BAND_KEYS)}"
            if (cid, dom) not in present_prof_domains:
                continue

            bmap = dict(zip(BAND_KEYS, arr))
            if dom == "science":
                # Treat 0 in a missing slot as a null-to-zero substitution for check 4; flag non-zero domain band violations here.
                for forbidden in ("level_below_1c", "level_1c"):
                    if bmap[forbidden] is not None and bmap[forbidden] != 0:
                        return False, f"science levels[{cid!r}] has non-null {forbidden}={bmap[forbidden]!r}"
                for req in BAND_KEYS[2:]:
                    if bmap[req] is None:
                        return False, f"science levels[{cid!r}] missing required band {req}"
            elif dom in ("mathematics", "reading"):
                if bmap["level_below_1b"] is not None and bmap["level_below_1b"] != 0:
                    return False, f"{dom} levels[{cid!r}] has non-null level_below_1b={bmap['level_below_1b']!r}"
                for req in ("level_below_1c", "level_1c", *BAND_KEYS[3:]):
                    if bmap[req] is None:
                        return False, f"{dom} levels[{cid!r}] missing required band {req}"

            non_null = [v for v in arr if is_num(v) and math.isfinite(v)]
            total = sum(non_null)
            if abs(total - 100.0) > 0.6:
                return False, f"levels[{cid!r}][{dom!r}] sum is {total:.2f}, outside 100 +/- 0.6"
            checked += 1

    return True, f"{checked} country-domain band sets match domain rules and sum to 100 +/- 0.6"


def check_6_series(payload: dict, bundle: dict[str, object]) -> tuple[bool, str]:
    rows_by_file: dict[str, list[dict[str, str]]] = bundle["rows"]  # type: ignore[assignment]
    min_year_by_domain = {"science": 2006, "mathematics": 2003, "reading": 2000}

    expected_share_years: dict[tuple[str, str], list[int]] = {}
    lowtop_sets: dict[tuple[str, str], set[int]] = {}
    for r in rows_by_file["annex_lowtop.csv"]:
        if r["subject"] in FORBIDDEN_COUNTRY_SOURCES:
            continue
        lowtop_sets.setdefault((subject_to_id(r["subject"]), r["domain"]), set()).add(int(r["cycle"]))
    for key, yset in lowtop_sets.items():
        expected_share_years[key] = sorted(yset)

    share = payload.get("share", {})
    for cid, dom_map in share.items():
        if not isinstance(dom_map, dict):
            continue
        for dom, s in dom_map.items():
            yrs = s.get("years", [])
            b2 = s.get("below_l2", [])
            a4 = s.get("above_l4", [])
            exp_yrs = expected_share_years.get((cid, dom), [])
            if yrs != exp_yrs:
                return False, f"share[{cid!r}][{dom!r}].years {yrs} != CSV cycles {exp_yrs}"
            if len(b2) != len(yrs) or len(a4) != len(yrs):
                return False, f"share[{cid!r}][{dom!r}] array lengths ({len(b2)}, {len(a4)}) != years ({len(yrs)})"
            if yrs and min(yrs) < min_year_by_domain[dom]:
                return False, f"share[{cid!r}][{dom!r}] contains cycle before {min_year_by_domain[dom]}"

    expected_trend_years: dict[str, list[int]] = {}
    trend_sets: dict[str, set[int]] = {}
    for r in rows_by_file["annex_trend_science.csv"]:
        if r["subject"] in {"OECD total", "OECD average-35"}:
            continue
        if r["domain"] == "science" and r["stat"] == "mean":
            trend_sets.setdefault(subject_to_id(r["subject"]), set()).add(int(r["cycle"]))
    for cid, yset in trend_sets.items():
        expected_trend_years[cid] = sorted(yset)

    trend = payload.get("trend_science", {})
    for cid, s in trend.items():
        yrs = s.get("years", [])
        if any(yrs[i] >= yrs[i + 1] for i in range(len(yrs) - 1)):
            return False, f"trend_science[{cid!r}].years not strictly ascending: {yrs}"
        exp_yrs = expected_trend_years.get(cid, [])
        if yrs != exp_yrs:
            return False, f"trend_science[{cid!r}].years {yrs} != CSV cycles {exp_yrs}"
        if len(s.get("mean", [])) != len(yrs) or len(s.get("se", [])) != len(yrs):
            return False, f"trend_science[{cid!r}] mean/se array lengths do not match years"

    variation = payload.get("variation_science", {})
    for cid, arr in variation.items():
        if not isinstance(arr, list) or len(arr) != len(VARIATION_KEYS):
            return False, f"variation_science[{cid!r}] has wrong length"
        for idx, v in enumerate(arr):
            if v is not None and (not is_num(v) or not math.isfinite(v)):
                return False, f"variation_science[{cid!r}][{idx}] is not finite: {v!r}"

    return True, "share years, trend_science ascending cycles, and finite variation arrays verified"


def check_7_provenance(payload: dict, bundle: dict[str, object]) -> tuple[bool, str]:
    meta = payload.get("meta", {})
    if not isinstance(meta, dict):
        return False, "meta is not a dict"

    expected_shas: dict[str, str] = bundle["shas"]  # type: ignore[assignment]
    got_shas = meta.get("input_sha256", {})
    if set(got_shas.keys()) != set(INPUT_FILES):
        return False, f"meta.input_sha256 keys {sorted(got_shas.keys())} != {list(INPUT_FILES)}"
    for fname, exp_sha in expected_shas.items():
        if got_shas.get(fname) != exp_sha:
            return False, f"meta.input_sha256[{fname!r}] mismatch ({got_shas.get(fname)!r} != {exp_sha!r})"

    puf_counts: dict[str, int] = bundle["puf_counts"]  # type: ignore[assignment]
    participants: list[str] = bundle["participants"]  # type: ignore[assignment]
    expected_counts = {"participants": len(participants), "domains": len(DOMAINS), **puf_counts}
    got_counts = meta.get("counts", {})
    if got_counts != expected_counts:
        return False, f"meta.counts mismatch vs puf_counts.csv"

    rows_by_file: dict[str, list[dict[str, str]]] = bundle["rows"]  # type: ignore[assignment]
    prof_tables: dict[str, str] = {}
    for r in rows_by_file["annex_proficiency.csv"]:
        d = r["domain"]
        if d not in prof_tables and r.get("table"):
            prof_tables[d] = clean_table_id(r["table"])
    expected_tables = {
        "scores": next(clean_table_id(r["table"]) for r in rows_by_file["annex_means.csv"] if r["domain"] == "science" and r.get("table")),
        "proficiency": {d: prof_tables[d] for d in DOMAINS},
        "share": next(clean_table_id(r["table"]) for r in rows_by_file["annex_lowtop.csv"] if r["domain"] == "science" and r.get("table")),
        "trend": next(clean_table_id(r["table"]) for r in rows_by_file["annex_trend_science.csv"] if r["domain"] == "science" and r.get("table")),
        "variation": next(clean_table_id(r["table"]) for r in rows_by_file["annex_variation_science.csv"] if r["domain"] == "science" and r.get("table")),
    }
    got_tables = meta.get("tables", {})
    if got_tables != expected_tables:
        return False, f"meta.tables {got_tables!r} != {expected_tables!r}"

    return True, f"sha256 verified for {len(INPUT_FILES)} input files, counts and tables match CSVs"


def check_8_sanity(raw_text: str, payload: dict) -> tuple[bool, str]:
    if re.search(r"\b(?:NaN|Infinity|-Infinity)\b", raw_text):
        return False, "raw JSON contains NaN or Infinity token"

    bad_strings = {"null", "nan", "infinity", "-infinity", "undefined"}

    def walk(node: object, path: str) -> str | None:
        if isinstance(node, float):
            if not math.isfinite(node):
                return f"non-finite float at {path}: {node!r}"
        elif isinstance(node, str):
            s = node.strip()
            if s.lower() in bad_strings:
                return f"forbidden null/NaN string literal {node!r} at {path}"
            if ISO_TIMESTAMP_RE.match(s):
                return f"wall-clock timestamp value {node!r} at {path}"
        elif isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k.lower() in TIMESTAMP_KEYS:
                    return f"wall-clock timestamp key {k!r} at {path}"
                err = walk(k, f"{path}.{k}") or walk(v, f"{path}.{k}")
                if err:
                    return err
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                err = walk(item, f"{path}[{idx}]")
                if err:
                    return err
        return None

    err = walk(payload, "$")
    if err:
        return False, err

    countries = payload.get("countries", [])
    if len(countries) < 2:
        return False, "countries has fewer than 2 entries"
    if countries[0].get("id") != "indonesia" or countries[1].get("id") != "oecd-average":
        return False, (
            f"countries[0..1].id must be ['indonesia', 'oecd-average'], "
            f"got [{countries[0].get('id')!r}, {countries[1].get('id')!r}]"
        )

    rest = countries[2:]
    expected_rest = sorted(rest, key=lambda c: (c.get("name", "").lower(), c.get("name", ""), c.get("id", "")))
    if rest != expected_rest:
        return False, "countries[2:] are not sorted alphabetically by display name"

    return True, "no NaN/Infinity/null-strings/timestamps; countries start with indonesia, oecd-average"


def check_9_budget(raw_bytes: bytes) -> tuple[bool, str]:
    size = len(raw_bytes)
    kb = size / 1024.0
    if size == 0:
        return False, "payload file is empty (0 bytes)"
    if size > MAX_BYTES:
        return False, f"payload size {size} bytes ({kb:.2f} KB) exceeds {MAX_BYTES} bytes (220 KB)"
    return True, f"payload size {size} bytes ({kb:.2f} KB) <= {MAX_BYTES} bytes (220 KB)"


def check_10_determinism(raw_bytes: bytes, csv_dir: Path) -> tuple[bool, str]:
    extract_path = Path(__file__).resolve().parent / "extract_explorer.py"
    spec = importlib.util.spec_from_file_location("extract_explorer", extract_path)
    if spec is None or spec.loader is None:
        return False, f"unable to load {extract_path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rebuilt_payload, _ = mod.build_payload(csv_dir)
    rebuilt_bytes = mod.serialize_payload(rebuilt_payload)
    rebuilt_payload2, _ = mod.build_payload(csv_dir)
    rebuilt_bytes2 = mod.serialize_payload(rebuilt_payload2)

    if rebuilt_bytes != rebuilt_bytes2:
        return False, "extract_explorer consecutive rebuilds are not byte-identical"
    if raw_bytes != rebuilt_bytes:
        return False, f"payload bytes (len={len(raw_bytes)}) differ from fresh rebuild (len={len(rebuilt_bytes)})"
    return True, f"rebuild is byte-identical ({len(raw_bytes)} bytes)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify pisa-explorer/data.json against annex CSVs and INTERFACE.md.")
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD, help="Path to data.json")
    parser.add_argument("--csv-dir", type=Path, default=DEFAULT_CSV_DIR, help="Directory with source CSVs")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-check PASS lines")
    args = parser.parse_args(argv)

    if not args.payload.is_file():
        print(f"FAIL schema: [check 1] payload file not found: {args.payload}", file=sys.stderr)
        return 1

    raw_bytes = args.payload.read_bytes()
    try:
        raw_text = raw_bytes.decode("utf-8")
        payload = json.loads(raw_text)
    except Exception as exc:
        print(f"FAIL schema: [check 1] invalid UTF-8 or JSON: {exc}", file=sys.stderr)
        return 1

    try:
        bundle = load_csv_bundle(args.csv_dir)
    except Exception as exc:
        print(f"FAIL provenance: [check 7] failed to load CSVs from {args.csv_dir}: {exc}", file=sys.stderr)
        return 1

    # Ordered from specific structural/domain invariants to full-table recompute and byte determinism
    # so targeted mutations report their specific check first while every failing check is still emitted.
    checks = [
        (1, "schema", lambda: check_1_schema(payload)),
        (3, "completeness", lambda: check_3_completeness(payload, bundle)),
        (8, "sanity", lambda: check_8_sanity(raw_text, payload)),
        (9, "budget", lambda: check_9_budget(raw_bytes)),
        (7, "provenance", lambda: check_7_provenance(payload, bundle)),
        (5, "bands", lambda: check_5_bands(payload, bundle)),
        (4, "nulls", lambda: check_4_nulls(payload, bundle)),
        (6, "series", lambda: check_6_series(payload, bundle)),
        (2, "recompute", lambda: check_2_recompute(payload, bundle)),
        (10, "determinism", lambda: check_10_determinism(raw_bytes, args.csv_dir)),
    ]

    results: dict[int, tuple[str, bool, str]] = {}
    failures: list[tuple[int, str, str]] = []
    for num, name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"unexpected error: {exc}"
        results[num] = (name, ok, msg)
        if not ok:
            failures.append((num, name, msg))

    if failures:
        for num, name, msg in failures:
            print(f"FAIL {name}: [check {num}] {msg}", file=sys.stderr)
        return 1

    if not args.quiet:
        for num in sorted(results):
            name, _, msg = results[num]
            print(f"PASS {num} ({name}): {msg}")

    size_kb = len(raw_bytes) / 1024.0
    print(f"PASS {len(checks)} checks ({len(raw_bytes)} bytes, {size_kb:.2f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
