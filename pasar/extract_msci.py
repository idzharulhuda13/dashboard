#!/usr/bin/env python3
"""Tulis data/pasar/msci.json dari teks factsheet MSCI Indonesia Index (USD, net).

  python3 pasar/extract_msci.py data/pasar/source/msci-indonesia-2026-10-30.txt \
      --pdf data/pasar/source/msci-indonesia-index-net-2026-10-30.pdf \
      --prev-pdf data/pasar/source/msci-indonesia-index-net.pdf

Teks = `pdftotext` (tanpa -layout) dari PDF factsheet. Factsheet bulan sebelumnya hanya dipakai untuk
P/E dan P/BV pembanding (berapa valuasi bergeser dalam sebulan). Konteks di luar factsheet
(siaran pers OJK, rilis MSCI) diambil dari data/pasar/msci_context.json, yang isinya disalin verbatim
dari sumbernya dan diperiksa ulang oleh pasar/verify.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "pasar" / "msci.json"
CONTEXT = ROOT / "data" / "pasar" / "msci_context.json"

INDICES = ("MSCI Indonesia", "MSCI Emerging Markets", "MSCI ACWI IMI")
KEYS = ("indonesia", "em", "acwi_imi")
PERIODS = ("1m", "3m", "1y", "ytd", "3y", "5y", "10y", "since_2000")
SECTOR_KEYS = {"Communication Services": "Comm Srvcs"}
NUM = re.compile(r"^-?\d[\d,]*\.\d+$")
MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def nums_after(lines: list[str], start: int, count: int) -> list[float]:
    out = []
    for line in lines[start:]:
        s = line.strip()
        if NUM.match(s):
            out.append(float(s.replace(",", "")))
            if len(out) == count:
                return out
    raise ValueError(f"cuma ketemu {len(out)} dari {count} angka setelah baris {start}")


def find(lines: list[str], text: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if lines[i].strip().startswith(text):
            return i
    raise ValueError(f"baris {text!r} tidak ada setelah {start}")


def as_of(lines: list[str]) -> str:
    i = find(lines, "INDEX PERFORMANCE")
    m = re.search(r"\((\w{3}) (\d{1,2}), (\d{4})\)", lines[i])
    assert m, lines[i]
    return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))).isoformat()


def parse(lines: list[str]) -> dict:
    perf_at = find(lines, "INDEX PERFORMANCE")
    performance = {}
    for name, key in zip(INDICES, KEYS):
        row = find(lines, name, perf_at)
        performance[key] = dict(zip(PERIODS, nums_after(lines, row + 1, len(PERIODS))))

    fund_at = find(lines, "FUNDAMENTALS")
    vals = nums_after(lines, fund_at + 1, 12)
    fundamentals = {key: dict(zip(("div_yld", "pe", "pe_fwd", "pbv"), vals[i * 4:i * 4 + 4]))
                    for i, key in enumerate(KEYS)}

    # Grafik kumulatif: tiga label nilai akhir dicetak sesudah legenda, urut posisi di grafik
    # (atas ke bawah). Urutan itu ditegaskan, bukan diasumsikan diam-diam.
    leg = find(lines, "MSCI ACWI IMI", find(lines, "CUMULATIVE INDEX PERFORMANCE"))
    top, mid, low = nums_after(lines, leg + 1, 3)
    assert top > mid > low, (top, mid, low)
    cumulative = {"acwi_imi": top, "em": mid, "indonesia": low}

    cons_at = find(lines, "TOP 9 CONSTITUENTS")
    sec_at = find(lines, "Sector", cons_at) + 1
    total_at = find(lines, "Total", sec_at)
    block = [l.strip() for l in lines[sec_at:total_at] if l.strip()]
    assert len(block) % 4 == 0, block
    constituents = [{"name": block[i].title(), "float_cap_bn": float(block[i + 1]),
                     "weight_pct": float(block[i + 2]), "sector": block[i + 3]}
                    for i in range(0, len(block), 4)]
    total_cap, total_wt = nums_after(lines, total_at + 1, 2)

    count_at = find(lines, "Number of")
    n_const = int(lines[count_at + 2].strip())
    cover = re.search(r"With\s+(\d+) constituents, the index covers about (\d+)%", " ".join(lines))
    assert cover and int(cover.group(1)) == n_const == len(constituents), cover

    sectors = []
    for line in lines[find(lines, "SECTOR WEIGHTS") + 1:]:
        m = re.match(r"^([A-Za-z ]+?) (\d+(?:\.\d+)?)%$", line.strip())
        if not m:
            if sectors:
                break
            continue
        sectors.append({"sector": SECTOR_KEYS.get(m.group(1), m.group(1)), "weight_pct": float(m.group(2))})

    return {"as_of": as_of(lines), "performance": performance, "fundamentals": fundamentals,
            "cumulative": cumulative, "constituents": constituents, "sectors": sectors,
            "coverage_pct": int(cover.group(2)), "total_cap": total_cap, "total_wt": total_wt}


def prev_fundamentals(pdf: Path) -> dict:
    """Factsheet bulan lalu, mode -layout: baris 'MSCI Indonesia' di tabel performa, 4 angka terakhir."""
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True,
                          capture_output=True, text=True).stdout
    head = re.search(r"INDEX PERFORMANCE.*?\((\w{3}) (\d{1,2}), (\d{4})\)", text)
    assert head, "tabel performa tidak ketemu di factsheet pembanding"
    row = re.search(r"^\s*MSCI Indonesia\s+(.+)$", text[head.end():], re.M)
    assert row, "baris MSCI Indonesia tidak ketemu di factsheet pembanding"
    vals = [float(v) for v in row.group(1).split()]
    div_yld, pe, pe_fwd, pbv = vals[-4:]
    return {"as_of": dt.date(int(head.group(3)), MONTHS[head.group(1)], int(head.group(2))).isoformat(),
            "ytd": vals[3], "div_yld": div_yld, "pe": pe, "pe_fwd": pe_fwd, "pbv": pbv,
            "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("txt")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--prev-pdf", required=True)
    a = ap.parse_args()

    lines = Path(a.txt).read_text(encoding="utf-8").splitlines()
    p = parse(lines)
    pdf = Path(a.pdf).resolve()
    weights = round(sum(c["weight_pct"] for c in p["constituents"]), 2)
    caps = round(sum(c["float_cap_bn"] for c in p["constituents"]), 2)
    assert p["total_wt"] == 100.0 and abs(weights - 100.0) <= 0.05, weights  # pembulatan factsheet
    context = json.loads(CONTEXT.read_text(encoding="utf-8"))

    out = {
        "meta": {
            "index": "MSCI Indonesia Index (USD, net)",
            "as_of": p["as_of"],
            "constituents": len(p["constituents"]),
            "coverage_pct": p["coverage_pct"],
            "index_float_cap_bn": p["total_cap"],
            "source_pdf": str(pdf.relative_to(ROOT / "data" / "pasar")),
            "source_txt": str(Path(a.txt).resolve().relative_to(ROOT / "data" / "pasar")),
            "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "extracted": dt.date.today().isoformat(),
            "note": f"weights sum {weights:.2f} vs 100.00; cap sum {caps:.2f} vs {p['total_cap']:.2f} di factsheet (pembulatan)",
        },
        "constituents": p["constituents"],
        "sectors": p["sectors"],
        "performance": p["performance"],
        "fundamentals": p["fundamentals"],
        "cumulative": {"base": 100, "start": "2011-10", **p["cumulative"]},
        "prev": prev_fundamentals(Path(a.prev_pdf)),
        "context": context,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: as_of {p['as_of']}, {len(p['constituents'])} saham, "
          f"{len(p['sectors'])} sektor, ID ytd {p['performance']['indonesia']['ytd']}")


if __name__ == "__main__":
    main()
