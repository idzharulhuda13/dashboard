#!/usr/bin/env python3
"""build_page.py - karhutla/index.template.html + data/karhutla/karhutla.json + basemap.json -> karhutla/index.html.

Konvensi seri pasar: font base64 inline, payload ditempel sebagai const DATA, tiap slot data-fig diisi nilai
tampilan saat build supaya pembaca tanpa JavaScript tetap dapat angkanya. Strip "Data per" ditulis dari
meta.as_of + meta.as_of_time. Halaman ditolak kalau melewati anggaran byte.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "karhutla" / "index.template.html"
OUT = ROOT / "karhutla" / "index.html"
DATA = ROOT / "data" / "karhutla" / "karhutla.json"
BASEMAP = ROOT / "data" / "karhutla" / "basemap.json"
FONTS = ROOT / "fonts" / "pasar"
PAGE_BUDGET = 640_000
MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
          "Oktober", "November", "Desember")
FONT_DEFS = [
    ("ArchivoBlack.ttf", "Archivo Black", "900", "truetype", "font/ttf"),
    ("Archivo-var.woff2", "Archivo", "100 900", "woff2", "font/woff2"),
    ("IBMPlexMono-400.woff2", "IBM Plex Mono", "400", "woff2", "font/woff2"),
    ("IBMPlexMono-500.woff2", "IBM Plex Mono", "500", "woff2", "font/woff2"),
]


def fonts_css() -> str:
    out = []
    for fn, fam, wt, fmt, mime in FONT_DEFS:
        b64 = base64.b64encode((FONTS / fn).read_bytes()).decode()
        out.append(f"@font-face {{ font-family: '{fam}'; font-style: normal; font-weight: {wt}; font-display: swap; "
                   f"src: url('data:{mime};base64,{b64}') format('{fmt}'); }}")
    return "\n".join(out)


def as_of_long(meta: dict) -> str:
    y, m, d = meta["as_of"].split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}, {meta['as_of_time'].replace(':', '.')} WIB"


def fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def luas_blocks(t: str, data: dict) -> str:
    """Batang luas per tahun + provinsi ditulis statis (terbaca tanpa JavaScript)."""
    L = data["luas"]
    tot, cur, sus = L["total"], L["year"], set(L["suspect"])
    mx = max(tot.values())
    rows = []
    for y in L["years"]:
        cls = "lrow" + (" cur" if y == cur else "") + (" sus" if y in sus else "")
        tag = f"{y} *" if y in sus else (f"{y}**" if y == cur else y)
        rows.append(f'<div class="{cls}"><span class="y">{tag}</span><span><span class="bar" '
                    f'style="width:{tot[y] / mx * 100:.2f}%"></span></span><span class="n">{fmt(tot[y])}</span></div>')
    month = MONTHS[L["last_month"] - 1]
    notes = [f"** {cur}: Januari sampai {month} saja; tahun lain setahun penuh."]
    if sus:
        ys = ", ".join(sorted(sus))
        notes.insert(0, f"* {ys}: angka SiPongi+ ({', '.join(fmt(tot[y]) for y in sorted(sus))} ha) tidak sejalan dengan "
                        f"ringkasan di halaman SiPongi+ sendiri; dianggap belum lengkap dan tidak masuk rata-rata.")
    note = "".join(f'<p class="lnote">{html.escape(n)}</p>' for n in notes)
    prov = L["prov"]
    pm = prov[0][1]
    prow = [f'<div class="lrow p"><span class="y">{html.escape(nm)}</span><span><span class="bar" '
            f'style="width:{v / pm * 100:.2f}%"></span></span><span class="n">{fmt(v)}</span></div>' for nm, v in prov[:10]]
    pnote = f"10 dari {len(prov)} provinsi yang tercatat punya luas terbakar tahun ini."
    return (t.replace("__LUAS_YEARS__", "".join(rows)).replace("__LUAS_NOTE__", note)
             .replace("__LUAS_PROV__", "".join(prow)).replace("__LUAS_PROV_NOTE__", html.escape(pnote)))


def build() -> str:
    data = json.loads(DATA.read_text())
    base = json.loads(BASEMAP.read_text())
    t = TEMPLATE.read_text()
    fig = data["fig"]

    def figval(m: re.Match) -> str:
        k = m.group(1)
        if k not in fig or fig[k]["d"] is None:
            sys.exit(f"FATAL: slot __FIG_{k}__ tidak punya nilai di payload")
        return html.escape(str(fig[k]["d"]))

    t = re.sub(r"__FIG_([a-z0-9_]+)__", figval, t)
    long = as_of_long(data["meta"])
    y, mo, d = data["meta"]["as_of"].split("-")
    t = (t.replace("__AS_OF_DATE__", f"{int(d)} {MONTHS[int(mo) - 1]} {y}")
          .replace("/*__DATELINE_JS__*/", (ROOT / "assets" / "dateline.js").read_text())
          .replace("__AS_OF_LONG__", html.escape(long))
          .replace("__AS_OF__", data["meta"]["as_of"])
          .replace("__MAP_W__", str(base["w"])).replace("__MAP_H__", str(base["h"]))
          .replace("__MAP_LAND__", base["land"])
          .replace("/*__FONTS__*/", fonts_css()))
    t = luas_blocks(t, data)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    t = t.replace("__DATA__", payload)
    left = re.findall(r"__[A-Z][A-Z0-9_]+__", t)
    if left:
        sys.exit(f"FATAL: placeholder tersisa: {sorted(set(left))}")
    tsha = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    psha = hashlib.sha256(DATA.read_bytes()).hexdigest()
    t += f"<!--BUILD template:{tsha[:16]} payload:{psha[:16]}-->\n"
    if len(t.encode()) > PAGE_BUDGET:
        sys.exit(f"FATAL: halaman {len(t.encode())} byte > anggaran {PAGE_BUDGET}")
    return t


if __name__ == "__main__":
    page = build()
    OUT.write_text(page)
    print(f"karhutla/index.html {len(page.encode())} byte")
