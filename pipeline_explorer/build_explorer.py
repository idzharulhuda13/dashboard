#!/usr/bin/env python3
"""Build and verify pisa-explorer/index.html from pisa-explorer.template.html and pisa-explorer/data.json."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "pisa-explorer" / "data.json"
DEFAULT_TEMPLATE = ROOT / "pisa-explorer.template.html"
DEFAULT_OUT = ROOT / "pisa-explorer" / "index.html"
MAX_OUTPUT_BYTES = 400 * 1024
DATA_MARKER = "/*__DATA__*/"

ALLOWED_EXTERNAL_URLS = frozenset({
    "https://www.oecd.org/en/data/datasets/pisa-2025-database.html",
    "https://creativecommons.org/licenses/by/4.0/",
})

SPAN_RE = re.compile(
    r'<span\b([^>]*?)\bdata-fig=(["\'])([^"\']*)\2([^>]*)>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def fmt_num(v: float, dp: int) -> str:
    val = float(v)
    if not math.isfinite(val):
        raise ValueError(f"Non-finite numeric value: {v!r}")
    if abs(val) < 0.5 * (10 ** (-dp)):
        val = 0.0
    s = f"{val:,.{dp}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def resolve_fig_key(key: str, payload: dict) -> str:
    """Resolve a data-fig key against the frozen INTERFACE.md payload."""
    # ponytail: flat dot-path grammar assumes country ids and band keys never contain dots; switch to bracket/JSONPointer syntax if dotted keys are introduced.
    if not key or not isinstance(key, str):
        raise ValueError("Empty data-fig key")

    parts = key.split(".")

    # 1. meta.<...>
    if parts[0] == "meta":
        if len(parts) < 2:
            raise ValueError(f"Incomplete meta key: {key!r}")
        cur: object = payload.get("meta")
        for p in parts[1:]:
            if not isinstance(cur, dict) or p not in cur:
                raise ValueError(f"Unknown meta path in data-fig key: {key!r}")
            cur = cur[p]
        if cur is None or isinstance(cur, (dict, list, bool)):
            raise ValueError(f"Invalid terminal value at {key!r}: {cur!r}")
        if isinstance(cur, int):
            return fmt_int(cur)
        if isinstance(cur, float):
            return fmt_num(cur, 2)
        s = str(cur).strip()
        if not s:
            raise ValueError(f"Empty string at {key!r}")
        return s

    # 2. trend.<id>.<year>[.<mean|se>]
    if parts[0] == "trend":
        if len(parts) not in (3, 4):
            raise ValueError(f"Invalid trend key syntax: {key!r}")
        cid = parts[1]
        try:
            year = int(parts[2])
        except ValueError as exc:
            raise ValueError(f"Invalid year in trend key {key!r}") from exc
        field = parts[3] if len(parts) == 4 else "mean"
        if field not in ("mean", "se"):
            raise ValueError(f"Unsupported trend field {field!r} in {key!r}")
        trend_block = payload.get("trend_science", {})
        if cid not in trend_block:
            raise ValueError(f"Unknown country id {cid!r} in {key!r}")
        series = trend_block[cid]
        years = series.get("years", [])
        if year not in years:
            raise ValueError(f"Year {year} not present in trend_science[{cid!r}] for {key!r}")
        idx = years.index(year)
        val = series.get(field, [None])[idx]
        if val is None:
            raise ValueError(f"Null value at {key!r}")
        return fmt_num(val, 1)

    # 3. var.<id>.<element>
    if parts[0] == "var":
        if len(parts) != 3:
            raise ValueError(f"Invalid variation key syntax: {key!r}")
        _, cid, element = parts
        var_block = payload.get("variation_science", {})
        var_keys = payload.get("variation_keys", [])
        if cid not in var_block:
            raise ValueError(f"Unknown country id {cid!r} in {key!r}")
        if element not in var_keys:
            raise ValueError(f"Unknown variation element {element!r} in {key!r}")
        val = var_block[cid][var_keys.index(element)]
        if val is None:
            raise ValueError(f"Null value at {key!r}")
        return fmt_num(val, 2)

    # 4. <id>.<domain>.band.<bandKey>
    if len(parts) == 4 and parts[2] == "band":
        cid, dom, _, band_key = parts
        levels = payload.get("levels", {})
        band_keys = payload.get("band_keys", [])
        if cid not in levels or dom not in levels[cid]:
            raise ValueError(f"Unknown country/domain in {key!r}")
        if band_key not in band_keys:
            raise ValueError(f"Unknown band key {band_key!r} in {key!r}")
        val = levels[cid][dom][band_keys.index(band_key)]
        if val is None:
            raise ValueError(f"Null value at {key!r}")
        return fmt_num(val, 2)

    # 5. <id>.<domain>.share.<below_l2|above_l4>.<year>
    if len(parts) == 5 and parts[2] == "share":
        cid, dom, _, share_kind, yr_str = parts
        share_block = payload.get("share", {})
        if cid not in share_block or dom not in share_block[cid]:
            raise ValueError(f"Unknown country/domain in {key!r}")
        if share_kind not in ("below_l2", "above_l4"):
            raise ValueError(f"Unknown share kind {share_kind!r} in {key!r}")
        try:
            year = int(yr_str)
        except ValueError as exc:
            raise ValueError(f"Invalid year in {key!r}") from exc
        s_obj = share_block[cid][dom]
        years = s_obj.get("years", [])
        if year not in years:
            raise ValueError(f"Year {year} not found in share[{cid!r}][{dom!r}] for {key!r}")
        val = s_obj[share_kind][years.index(year)]
        if val is None:
            raise ValueError(f"Null value at {key!r}")
        return fmt_num(val, 2)

    # 6. <id>.<domain>.<stat>
    if len(parts) == 3:
        cid, dom, stat = parts
        scores = payload.get("scores", {})
        score_keys = payload.get("score_keys", [])
        if cid not in scores or dom not in scores[cid]:
            raise ValueError(f"Unknown country/domain in {key!r}")
        if stat not in score_keys:
            raise ValueError(f"Unknown score stat {stat!r} in {key!r}")
        val = scores[cid][dom][score_keys.index(stat)]
        if val is None:
            raise ValueError(f"Null value at {key!r}")
        return fmt_num(val, 1)

    raise ValueError(f"Unrecognized data-fig key format: {key!r}")


def strip_non_visible(html: str) -> str:
    no_comments = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    no_script = re.sub(r"<script\b[^>]*>.*?</script>", " ", no_comments, flags=re.DOTALL | re.IGNORECASE)
    no_style = re.sub(r"<style\b[^>]*>.*?</style>", " ", no_script, flags=re.DOTALL | re.IGNORECASE)
    text_only = re.sub(r"<[^>]+>", " ", no_style)
    return text_only


def build_and_verify(data_path: Path, template_path: Path) -> tuple[bytes, dict[str, int]]:
    if not data_path.is_file():
        raise FileNotFoundError(f"Missing payload file: {data_path}")
    if not template_path.is_file():
        raise FileNotFoundError(f"Missing template file: {template_path}")

    raw_data_bytes = data_path.read_bytes()
    payload = json.loads(raw_data_bytes.decode("utf-8"))
    compact_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    template_str = template_path.read_text(encoding="utf-8")

    # 1. Verify marker count in template before replacement
    marker_count = template_str.count(DATA_MARKER)
    if marker_count != 1:
        raise AssertionError(f"Expected {DATA_MARKER} exactly once in template, found {marker_count}")

    # 2. Extract and verify all <span data-fig="KEY">fallback</span>
    raw_fig_attrs = re.findall(r"\bdata-fig\s*=", template_str, flags=re.IGNORECASE)
    span_matches = list(SPAN_RE.finditer(template_str))
    if not span_matches:
        raise AssertionError("No <span data-fig=\"...\"> elements found in template")
    if len(span_matches) != len(raw_fig_attrs):
        raise AssertionError(
            f"Malformed data-fig element: found {len(raw_fig_attrs)} data-fig attributes but {len(span_matches)} valid spans"
        )

    for m in span_matches:
        key = m.group(3).strip()
        fallback = " ".join(m.group(5).split())
        if not key:
            raise AssertionError("Found data-fig attribute with empty key")
        if not fallback:
            raise AssertionError(f"Empty fallback text inside <span data-fig=\"{key}\">")
        expected = resolve_fig_key(key, payload)
        if fallback != expected:
            raise AssertionError(
                f"data-fig mismatch for {key!r}: template fallback is {fallback!r}, payload resolves to {expected!r}"
            )

    # 3. Replace /*__DATA__*/ exactly once (never modifying span contents)
    output_str = template_str.replace(DATA_MARKER, compact_json, 1)
    if DATA_MARKER in output_str:
        raise AssertionError(f"Leftover {DATA_MARKER} found in output after replacement")

    # 4. Verify no NaN / Infinity anywhere in output, and no null / NaN / Infinity in visible text
    if re.search(r"\b(?:NaN|Infinity|-Infinity)\b", output_str):
        raise AssertionError("Forbidden NaN or Infinity token found in output")

    visible_text = strip_non_visible(output_str)
    bad_visible = re.search(r"\b(?:NaN|Infinity|null|undefined)\b", visible_text, flags=re.IGNORECASE)
    if bad_visible:
        raise AssertionError(f"Forbidden token {bad_visible.group(0)!r} found in rendered HTML text")

    # 5. Verify no em dash and no external scripts or disallowed external URLs
    if "\u2014" in output_str:
        raise AssertionError("Forbidden em dash (U+2014) found in output")
    if re.search(r"<script\b[^>]*\bsrc\s*=", output_str, flags=re.IGNORECASE):
        raise AssertionError("Forbidden external <script src=...> found in output")

    urls = re.findall(r"https?://[^\s\"'<>)]+", output_str, flags=re.IGNORECASE)
    disallowed = [u for u in urls if u not in ALLOWED_EXTERNAL_URLS]
    if disallowed:
        raise AssertionError(f"Disallowed external URLs found: {disallowed}")

    # 6. Verify required motion dial header comment, reduced-motion media query, font preloads, and panels
    required_dial = "dial: ENERGY 2 / RHYTHM 2 / MOTION 2 (pisa-explorer: interaktif, animasi transisi saja)"
    if required_dial not in output_str:
        raise AssertionError("Missing required MOTION 2 dial amendment comment at top of template")
    if "prefers-reduced-motion: reduce" not in output_str:
        raise AssertionError("Missing mandatory prefers-reduced-motion: reduce rule")

    required_panels = (
        "band-hero",
        "band-controls",
        "band-pyramid",
        "band-percentile",
        "band-ladder",
        "band-timeline",
        "band-variation",
        "band-method",
    )
    for pid in required_panels:
        if f'id="{pid}"' not in output_str:
            raise AssertionError(f"Missing required panel section id={pid!r}")

    output_bytes = output_str.encode("utf-8")
    if len(output_bytes) > MAX_OUTPUT_BYTES:
        raise AssertionError(
            f"Output size {len(output_bytes)} bytes exceeds limit of {MAX_OUTPUT_BYTES} bytes (400 KB)"
        )

    stats = {
        "bytes": len(output_bytes),
        "spans": len(span_matches),
        "unique_keys": len({m.group(3).strip() for m in span_matches}),
        "panels": len(required_panels),
        "checks": 8,
    }
    return output_bytes, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and verify pisa-explorer/index.html.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Path to pisa-explorer/data.json")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="Path to pisa-explorer.template.html")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output HTML path")
    parser.add_argument("--check", action="store_true", help="Build to a temp file and verify all assertions")
    args = parser.parse_args(argv)

    try:
        output_bytes, stats = build_and_verify(args.data, args.template)
        if args.check:
            with tempfile.NamedTemporaryFile(prefix="pisa-explorer-", suffix=".html", delete=True) as tmp:
                tmp.write(output_bytes)
                tmp.flush()
                re_read = Path(tmp.name).read_bytes()
                if re_read != output_bytes:
                    raise AssertionError("Temp file write verification failed")
        else:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(output_bytes)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    kb = stats["bytes"] / 1024.0
    sha_short = hashlib.sha256(output_bytes).hexdigest()[:12]
    mode_label = "CHECK" if args.check else "BUILD"
    print(
        f"PASS [{mode_label}] checks={stats['checks']}/{stats['checks']} "
        f"spans={stats['spans']} (unique={stats['unique_keys']}) "
        f"panels={stats['panels']} bytes={stats['bytes']} ({kb:.2f} KB <= 400 KB) sha256={sha_short}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
