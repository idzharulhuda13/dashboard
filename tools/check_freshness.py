#!/usr/bin/env python3
"""check_freshness.py - jaga strip "Data per <tanggal>" di semua dashboard tetap jujur.

Yang dicek per halaman:
1. Halaman punya strip dateline dan atribut data-as-of.
2. Angka di strip sama dengan payload sumber (kalau payload dan halaman beda, berarti build
   ketinggalan dan pembaca melihat tanggal lama).
3. Umur data terhadap hari ini masih di bawah cadence halaman itu.

Exit 0 kalau semua sehat, exit 1 kalau ada yang berhenti atau tidak sinkron (cron memakai kode
keluar ini untuk memicu notifikasi). Tanpa LLM, tanpa jaringan: cuma baca berkas lokal.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (halaman, payload, kunci as_of, cadence, batas hari)
PAGES = [
    ("pasar/ihsg.html", "data/pasar/ihsg.json", ("meta", "as_of"), "daily", 4),
    ("pasar/rupiah.html", "data/pasar/rupiah.json", ("meta", "as_of"), "daily", 4),
    ("pasar/msci.html", "data/pasar/msci.json", ("meta", "as_of"), "monthly", 45),
    ("index.html", None, None, "daily", 4),
    ("gempa/index.html", None, None, "daily", 4),
    ("pisa/index.html", None, None, "yearly", 400),
    ("pisa-explorer/index.html", None, None, "yearly", 400),
]


def read_json(path: Path, keys: tuple[str, ...]):
    node = json.loads(path.read_text())
    for k in keys:
        node = node[k]
    return node


def iso_from(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    return m.group(0) if m else None


def main() -> int:
    today = dt.date.today()
    problems: list[str] = []
    rows: list[str] = []

    for page_rel, payload_rel, keys, cadence, limit in PAGES:
        page = ROOT / page_rel
        if not page.exists():
            problems.append(f"{page_rel}: halaman tidak ada")
            continue
        html = page.read_text()

        m = re.search(r'data-dateline[^>]*data-cadence="([^"]+)"[^>]*data-as-of="([^"]*)"', html)
        if not m:
            m2 = re.search(r'data-dateline[\s\S]{0,400}?data-as-of="([^"]*)"', html)
            if not m2:
                problems.append(f"{page_rel}: strip dateline tidak ada")
                continue
            shown = iso_from(m2.group(1))
        else:
            shown = iso_from(m.group(2))

        source = shown
        if payload_rel and keys:
            payload = ROOT / payload_rel
            if not payload.exists():
                problems.append(f"{page_rel}: payload {payload_rel} tidak ada")
                continue
            source = iso_from(str(read_json(payload, keys)))
            if source and shown and source != shown:
                problems.append(f"{page_rel}: strip menampilkan {shown}, payload {source} (build ketinggalan)")

        if not source:
            problems.append(f"{page_rel}: tanggal data tidak diketahui")
            continue

        age = (today - dt.date.fromisoformat(source)).days
        status = "OK" if age <= limit else "BERHENTI"
        if age > limit:
            problems.append(f"{page_rel}: data per {source}, sudah {age} hari (batas {limit})")
        rows.append(f"{page_rel:26} {source}  umur {age:>4} hari  {cadence:8} batas {limit:>3}  {status}")

    print("Dashboard freshness, data per tanggal terakhir:")
    for row in rows:
        print("  " + row)

    if problems:
        print(f"\n{len(problems)} masalah:")
        for p in problems:
            print("  - " + p)
        return 1

    print("\nSemua halaman menampilkan tanggal data yang benar dan masih segar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
