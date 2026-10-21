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
SEBAB = ROOT / "data" / "pasar" / "sebab.json"

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


def sebab_html(payload: dict) -> str:
    """Blok 'Sebabnya' dari data/pasar/sebab.json (konteks bersitasi, bukan angka harga)."""
    if not SEBAB.exists():
        sys.exit(f"FATAL: berkas tidak ada: {SEBAB}")
    doc = json.loads(SEBAB.read_text())
    items = doc["items"]
    if len(items) < 4:
        sys.exit("FATAL: butir sebab kurang dari 4")

    # satu-satunya angka yang dihitung payload: lompatan 20 Oktober
    series = payload["series"]
    idx = next((i for i, r in enumerate(series) if r["date"] == "2026-10-20"), None)
    jump = "5,65"
    if idx and idx > 0:
        jump = f"{(series[idx]['close'] / series[idx - 1]['close'] - 1) * 100:.2f}".replace(".", ",")

    cards = []
    for i, it in enumerate(items, 1):
        body = it["b"].replace("{jump}", jump)
        if "{" in body:
            sys.exit(f"FATAL: placeholder tersisa di butir sebab {i}")
        cards.append(
            '      <article class="sebab-card">\n'
            f'        <div class="sebab-k">{i:02d} · {_html.escape(it["k"])}</div>\n'
            f'        <h3 class="sebab-t">{_html.escape(it["t"])}</h3>\n'
            f'        <p class="sebab-b">{_html.escape(body)}</p>\n'
            f'        <p class="sebab-src">Sumber: {_html.escape(it["src"])}</p>\n'
            '      </article>'
        )
    return (
        '  <section class="sheet sheet-plane-2 band" aria-labelledby="sebab-heading">\n'
        '    <div class="container">\n'
        '      <h2 id="sebab-heading" class="band-title">Sebabnya</h2>\n'
        '      <p class="band-lead">Turun setahun bukan karena laba emiten jatuh. Pemicunya kepercayaan dan arus dana, '
        'lalu diperbesar oleh bank yang jadi bagian terbesar indeks. Kenaikan 20 Oktober juga bukan karena asing kembali.</p>\n'
        '      <div class="sebab-grid">\n'
        + "\n".join(cards) + "\n"
        '      </div>\n'
        '    </div>\n'
        '  </section>\n'
    )


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
    if "<!--__SEBAB__-->" not in rendered:
        sys.exit("FATAL: placeholder '<!--__SEBAB__-->' tidak ditemukan di template")
    rendered = rendered.replace("<!--__SEBAB__-->", sebab_html(payload))

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
