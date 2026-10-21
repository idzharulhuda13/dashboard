#!/usr/bin/env python3
"""build_ihsg_page.py - compile pasar/ihsg.html from pasar/ihsg.template.html + data/pasar/ihsg.json.

Sama seperti build_data.py (MSCI): font di-inline base64, payload ditempel sebagai const DATA,
setiap slot data-fig diisi nilai tampilan supaya pembaca tanpa JavaScript tetap dapat angkanya.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASAR = ROOT / "pasar"
FONTS = ROOT / "fonts" / "pasar"
TEMPLATE = PASAR / "ihsg.template.html"
OUT = PASAR / "ihsg.html"
DATA = ROOT / "data" / "pasar" / "ihsg.json"

FONT_DEFS = [
    ("ArchivoBlack.ttf", "Archivo Black", "900", "truetype", "font/ttf"),
    ("ArchivoBlack.ttf", "Archivo", "900", "truetype", "font/ttf"),
    ("Archivo-var.woff2", "Archivo", "100 900", "woff2", "font/woff2"),
    ("IBMPlexMono-400.woff2", "IBM Plex Mono", "400", "woff2", "font/woff2"),
    ("IBMPlexMono-500.woff2", "IBM Plex Mono", "500", "woff2", "font/woff2"),
]


def fonts_css() -> str:
    rules, cache = [], {}
    for filename, family, weight, fmt, mime in FONT_DEFS:
        if filename not in cache:
            path = FONTS / filename
            if not path.exists():
                sys.exit(f"FATAL: font tidak ada: {path}")
            cache[filename] = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            "@font-face {\n"
            f"  font-family: '{family}';\n"
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            "  font-display: swap;\n"
            f"  src: url('data:{mime};base64,{cache[filename]}') format('{fmt}');\n"
            "}"
        )
    return "\n".join(rules)


def main() -> int:
    for path in (TEMPLATE, DATA):
        if not path.exists():
            sys.exit(f"FATAL: berkas tidak ada: {path}")

    payload = json.loads(DATA.read_text())
    template_bytes = TEMPLATE.read_bytes()
    template_sha = hashlib.sha256(template_bytes).hexdigest()
    template = template_bytes.decode("utf-8")

    placeholders = ("/*__DATA__*/ {}", "/*__FONTS__*/")
    for ph in placeholders:
        if ph not in template:
            sys.exit(f"FATAL: placeholder '{ph}' tidak ditemukan di template")

    rendered = template.replace(placeholders[0], json.dumps(payload, ensure_ascii=False))
    rendered = rendered.replace(placeholders[1], fonts_css())

    fig = payload["fig"]
    meta = payload["meta"]

    def display(key: str):
        if key in fig:
            return str(fig[key]["d"])
        if key in meta:
            return str(meta[key])
        return None

    missing: list[str] = []

    def fill(match: re.Match) -> str:
        key = match.group("key")
        value = display(key)
        if value is None:
            missing.append(key)
            return match.group(0)
        return match.group("open") + _html.escape(value) + match.group("close")

    rendered, filled = re.subn(
        r'(?P<open><(?P<tag>\w+)[^>]*data-fig="(?P<key>[^"]+)"[^>]*>)(?P<close></(?P=tag)>)',
        fill,
        rendered,
    )
    if missing:
        sys.exit(f"FATAL: {len(missing)} slot data-fig tanpa entri payload: {sorted(set(missing))}")

    slots = rendered.count('data-fig="')
    if filled != slots:
        sys.exit(f"FATAL: {slots} slot data-fig tapi {filled} terisi; ada slot dengan markup dalam")

    final = f"<!--BUILD sha256:{template_sha}-->\n" + rendered
    OUT.write_text(final, encoding="utf-8")

    print(f"Filled {filled} data-fig fallbacks")
    print(f"Generated {OUT.relative_to(ROOT)} ({len(final)} bytes) with sha256:{template_sha[:16]}...")
    print(f"as_of {meta['as_of']} | {meta['sessions']} sesi | payload sha {meta['source_sha256'][:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
