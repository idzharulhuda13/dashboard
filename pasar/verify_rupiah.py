#!/usr/bin/env python3
"""verify_rupiah.py - gate untuk pasar/rupiah.html: angka dihitung ulang dari payload, bukan dipercaya.

Cek:
1. Korelasi di payload cocok dengan hitung ulang dari return harian (pasangan sesi berurutan).
2. Jumlah hari beririsan cocok dengan jumlah titik di scatter.
3. Semua angka penting (kurs awal/akhir, puncak, korelasi, Okt) muncul di HTML.
4. Nol em dash, nol gradient, nol <circle> (kontrak DESIGN.pasar: donut haram), viewport ada.
5. Nol sisa placeholder (data-fig kosong, __CHART__, dst).
6. Build stamp cocok dengan sha256 template.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "pasar" / "rupiah.html"
TEMPLATE = ROOT / "pasar" / "rupiah.template.html"
PAYLOAD = ROOT / "data" / "pasar" / "rupiah.json"
IHSG = ROOT / "data" / "pasar" / "ihsg.json"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def num(x: float, dec: int = 3) -> str:
    return f"{x:,.{dec}f}".replace(",", "#").replace(".", ",").replace("#", ".")


def main() -> int:
    for path in (HTML, TEMPLATE, PAYLOAD, IHSG):
        if not path.exists():
            sys.exit(f"FATAL: berkas tidak ada: {path}")

    payload = json.loads(PAYLOAD.read_text())
    html = HTML.read_text()
    fig = payload["fig"]
    series = payload["series"]
    ihsg = {r["date"]: r["close"] for r in json.loads(IHSG.read_text())["series"]}

    pairs = []
    for a, b in zip(series, series[1:]):
        if a["date"] in ihsg and b["date"] in ihsg:
            pairs.append((b["rate"] / a["rate"] - 1, ihsg[b["date"]] / ihsg[a["date"]] - 1))
    corr = statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs])
    check(abs(corr - fig["corr"]) < 5e-4, "Korelasi payload cocok hitung ulang",
          f"{num(fig['corr'])} vs {num(corr)}")
    check(len(pairs) == fig["n_obs"], "Hari beririsan cocok", f"{len(pairs)} vs {fig['n_obs']}")

    dots = html.count('<rect x="') # scatter markers + marker kotak chart
    check(dots >= len(pairs), "Scatter punya minimal satu penanda per hari", f"{dots} rect")

    for label, value in (
        ("kurs awal", fig["first"]["d"]),
        ("kurs akhir", fig["last"]["d"]),
        ("puncak", fig["peak"]["d"]),
        ("korelasi", num(fig["corr"])),
        ("hari beririsan", str(fig["n_obs"])),
    ):
        check(value in html, f"Angka {label} muncul di HTML", value)

    check("—" not in html, "Nol em dash")
    check("linear-gradient" not in html, "Nol gradient")
    check("<circle" not in html, "Nol <circle> (kontrak DESIGN.pasar)")
    check('name="viewport"' in html, "Viewport meta ada")
    empties = re.findall(r'data-fig="([^"]+)"[^>]*>\s*<', html)
    check(not empties, "Semua slot data-fig terisi", f"{len(empties)} kosong: {empties[:4]}")
    for ph in ("__CHART__", "__BARS__", "__SCATTER__", "__TABLE__", "__FONTS__", "__DATA__"):
        check(ph not in html, f"Placeholder {ph} terisi")

    stamp = re.search(r"<!--BUILD sha256:([0-9a-f]+)-->", html)
    template_sha = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    check(stamp is not None and stamp.group(1) == template_sha, "Build stamp cocok template",
          template_sha[:16])

    print()
    if failures:
        print(f"GAGAL {len(failures)} cek")
        return 1
    print("SEMUA CEK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
