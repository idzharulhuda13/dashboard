#!/usr/bin/env python3
"""verify_ihsg.py - gate untuk pasar/ihsg.html (PASAR 02, IHSG).

Enam lapis:
  1. hitung ulang dari sumber mentah (CSV + jsonl), bandingkan dengan payload
  2. stempel build = sha256 template
  3. semua slot data-fig terisi dan nilainya sama dengan payload
  4. larangan desain: em dash, linear-gradient, URL eksternal, jargon
  5. Playwright: 1440x900 dan 390x844 - error konsol, overflow horizontal, ukuran h1,
     lantai tipografi 12px, semantik tabel, grafik benar-benar tergambar
  6. figur kunci muncul di teks DOM yang dirender

Jalankan: python3 pasar/verify_ihsg.py   (exit 0 kalau semua lolos)
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_CSV = ROOT / "data" / "pasar" / "source" / "ihsg-daily.csv"
SRC_APPEND = ROOT / "data" / "pasar" / "source" / "ihsg-appended.jsonl"
PAYLOAD = ROOT / "data" / "pasar" / "ihsg.json"
TEMPLATE = ROOT / "pasar" / "ihsg.template.html"
PAGE = ROOT / "pasar" / "ihsg.html"

BANNED = ["bearish", "bullish", "rebound", "drawdown", "support level", "resistance",
          "volatilitas", "likuiditas", "koreksi teknikal", "yield", "bobot", "kapitalisasi"]

errors: list[str] = []


def ok(msg: str) -> None:
    print(f"[PASS] {msg}")


def bad(msg: str) -> None:
    errors.append(msg)
    print(f"[FAIL] {msg}")


def fmt_ind(v: float, dec: int = 2) -> str:
    return f"{v:,.{dec}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def load_series() -> list[tuple[str, float]]:
    rows = []
    with SRC_CSV.open(newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows.append((r["Date"], float(r["IHSG"])))
    if SRC_APPEND.exists():
        for line in SRC_APPEND.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                rows.append((rec["date"], float(rec["close"])))
    rows.sort()
    return rows


def main() -> int:
    print("=== VERIFY pasar/ihsg.html ===")
    for p in (SRC_CSV, PAYLOAD, TEMPLATE, PAGE):
        if not p.exists():
            bad(f"berkas tidak ada: {p.relative_to(ROOT)}")
            print("=== GAGAL (berkas) ===")
            return 1

    payload = json.loads(PAYLOAD.read_text())
    series = load_series()
    closes = [c for _, c in series]
    fig = payload["fig"]

    # 1. Hitung ulang
    if len(series) != fig["sessions"]["r"]:
        bad(f"jumlah sesi: sumber {len(series)} vs payload {fig['sessions']['r']}")
    else:
        ok(f"jumlah sesi cocok: {len(series)}")

    if max(closes) != fig["peak_value"]["r"]:
        bad(f"puncak: sumber {max(closes)} vs payload {fig['peak_value']['r']}")
    else:
        ok(f"puncak cocok: {fmt_ind(max(closes))}")

    if series[-1][1] != fig["last_value"]["r"]:
        bad(f"penutupan terakhir: sumber {series[-1][1]} vs payload {fig['last_value']['r']}")
    else:
        ok(f"penutupan terakhir cocok: {fmt_ind(series[-1][1])}")

    dd = (closes[-1] / max(closes) - 1.0) * 100.0
    if abs(dd - fig["drawdown_pct"]["r"]) > 1e-9:
        bad(f"turun dari puncak: hitung {dd:.6f} vs payload {fig['drawdown_pct']['r']:.6f}")
    else:
        ok(f"turun dari puncak cocok: {fig['drawdown_pct']['d']}")

    daily = [(series[i][0], (series[i][1] / series[i - 1][1] - 1) * 100.0) for i in range(1, len(series))]
    down = sum(1 for _, r in daily if r < 0)
    if down != fig["down_days"]["r"]:
        bad(f"hari turun: hitung {down} vs payload {fig['down_days']['r']}")
    else:
        ok(f"hari turun cocok: {down} dari {len(daily)}")

    worst = min(daily, key=lambda x: x[1])
    best = max(daily, key=lambda x: x[1])
    if abs(worst[1] - fig["worst_day_ret"]["r"]) > 1e-9 or abs(best[1] - fig["best_day_ret"]["r"]) > 1e-9:
        bad(f"hari ekstrem: terburuk {worst} terbaik {best} vs payload {fig['worst_day_ret']['r']} / {fig['best_day_ret']['r']}")
    else:
        ok(f"hari ekstrem cocok: {worst[0]} {fmt_ind(worst[1])}%, {best[0]} {fmt_ind(best[1])}%")

    # bulanan: rekap ulang penutupan akhir bulan
    months: dict[str, list[float]] = {}
    for d, c in series:
        months.setdefault(d[:7], []).append(c)
    keys = sorted(months)
    if len(keys) != len(payload["monthly"]):
        bad(f"jumlah bulan: hitung {len(keys)} vs payload {len(payload['monthly'])}")
    else:
        ok(f"jumlah bulan cocok: {len(keys)}")
    prev_end = None
    month_errors = 0
    for mk, row in zip(keys, payload["monthly"]):
        end = months[mk][-1]
        base = months[mk][0] if prev_end is None else prev_end
        ret = (end / base - 1.0) * 100.0
        if row["month"] != mk or abs(row["end"] - end) > 1e-9 or abs(row["ret"] - ret) > 1e-9:
            month_errors += 1
            bad(f"bulan {mk}: payload end={row['end']} ret={row['ret']:.4f}; hitung end={end} ret={ret:.4f}")
        prev_end = end
    if not month_errors:
        ok(f"semua {len(keys)} return bulanan cocok dengan sumber")

    # 2. Stempel build
    html = PAGE.read_text(encoding="utf-8")
    tpl_sha = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    first = html.splitlines()[0] if html else ""
    m = re.match(r"<!--BUILD sha256:([0-9a-f]{64})-->$", first)
    if not m:
        bad(f"baris 1 bukan stempel build: {first[:60]}")
    elif m.group(1) != tpl_sha:
        bad(f"stempel {m.group(1)[:16]} != sha template {tpl_sha[:16]}")
    else:
        ok(f"stempel build cocok dengan template sha {tpl_sha[:16]}")

    # 3. Slot data-fig vs payload
    slots = re.findall(r'data-fig="([^"]+)"[^>]*>([^<]*)<', html)
    if not slots:
        bad("tidak ada slot data-fig di halaman")
    else:
        wrong = []
        for key, val in slots:
            entry = fig.get(key) or payload["meta"].get(key)
            if entry is None:
                wrong.append(f"{key}: tidak ada di payload")
                continue
            want = str(entry["d"] if isinstance(entry, dict) else entry)
            if val.strip() != want:
                wrong.append(f"{key}: '{val.strip()}' != '{want}'")
        if wrong:
            bad(f"{len(wrong)} slot data-fig menyimpang: {wrong[:6]}")
        else:
            ok(f"semua {len(slots)} slot data-fig sama dengan payload")

    payload_as_of = payload["meta"]["as_of_formatted"]
    if payload_as_of not in html:
        bad(f"tanggal data '{payload_as_of}' tidak muncul di halaman")
    else:
        ok(f"halaman menyebut tanggal data terbaru: {payload_as_of}")

    # 4. Larangan desain & jargon
    if "\u2014" in html:
        bad("ada em dash (U+2014)")
    else:
        ok("nol em dash")
    if "linear-gradient" in html:
        bad("ada linear-gradient")
    else:
        ok("nol linear-gradient")
    body_no_data = re.sub(r"const DATA = \{.*?\};", "", html, count=1, flags=re.S)
    # nama kunci data (data-fig="drawdown_pct") bukan prosa; buang sebelum cek jargon
    body_no_data = re.sub(r'data-fig="[^"]*"', 'data-fig=""', body_no_data)
    urls = [l.strip()[:80] for l in body_no_data.splitlines() if "http" in l.lower()]
    if urls:
        bad(f"URL eksternal di luar baris sumber: {urls[:3]}")
    else:
        ok("nol URL eksternal")
    hits = [t for t in BANNED if t in body_no_data.lower()]
    if hits:
        bad(f"jargon pembaca awam muncul: {hits}")
    else:
        ok(f"nol dari {len(BANNED)} istilah jargon")
    if 'name="viewport"' not in html:
        bad("meta viewport hilang")
    else:
        ok("meta viewport ada")

    # 5. Playwright
    key_figs = [fig["last_value"]["d"], fig["drawdown_pct"]["d"], fig["cur_ret_pct"]["d"],
                fig["neg_months"]["d"], fig["sessions"]["d"]]
    pw_errors = asyncio.run(playwright_checks(key_figs))
    errors.extend(pw_errors)

    print()
    if errors:
        print(f"=== GAGAL: {len(errors)} masalah ===")
        for e in errors[:12]:
            print(f" - {e}")
        return 1
    print("=== SEMUA GATE LOLOS (exit 0) ===")
    return 0


async def playwright_checks(expected_figures: list[str]) -> list[str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ["playwright tidak terpasang, gate visual tidak dijalankan"]

    out: list[str] = []
    url = f"file://{PAGE}"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for w, h in ((1440, 900), (390, 844)):
            page = await browser.new_page(viewport={"width": w, "height": h})
            page_errors: list[str] = []
            console_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

            await page.goto(url, wait_until="networkidle")
            await page.evaluate("() => document.fonts.ready")

            if page_errors or console_errors:
                out.append(f"{w}x{h}: error konsol/halaman: {page_errors} {console_errors}")
            else:
                out and None
                print(f"[PASS] {w}x{h}: 0 error konsol")

            sw, iw = await page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
            if sw > iw + 1:
                out.append(f"{w}x{h}: overflow horizontal (scrollWidth {sw} > {iw})")
            else:
                print(f"[PASS] {w}x{h}: tanpa overflow horizontal")

            h1 = await page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('h1')).fontSize)")
            if w == 1440 and h1 < 50:
                out.append(f"1440: h1 {h1}px < 50px")
            elif w == 390 and not (30 <= h1 <= 36):
                out.append(f"390: h1 {h1}px di luar 30-36px")
            else:
                print(f"[PASS] {w}: h1 {h1}px")

            small = await page.evaluate("""() => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null);
                let n; const fails = [];
                while (n = walker.nextNode()) {
                    const tag = n.tagName.toLowerCase();
                    if (tag === 'script' || tag === 'style') continue;
                    const hasText = Array.from(n.childNodes).some(c => c.nodeType === 3 && c.textContent.trim().length);
                    const isSvgText = (tag === 'text' || tag === 'tspan');
                    if (!hasText && !isSvgText) continue;
                    const cs = getComputedStyle(n);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    const fs = parseFloat(cs.fontSize);
                    if (fs < 11.95) fails.push({tag, text: n.textContent.trim().slice(0,24), fs});
                }
                return fails;
            }""")
            if small:
                out.append(f"{w}: {len(small)} teks di bawah 12px: {small[:3]}")
            else:
                print(f"[PASS] {w}: lantai tipografi 12px")

            # grafik dan tabel benar-benar terbangun
            chart = await page.evaluate("""() => {
                const path = document.getElementById('line-path');
                return {
                    d: path ? path.getAttribute('d').split('L').length : 0,
                    bars: document.querySelectorAll('#bars .bar').length,
                    months: document.querySelectorAll('#months-body tr').length,
                    worst: document.querySelectorAll('#worst-body tr').length,
                    best: document.querySelectorAll('#best-body tr').length,
                    caption: !!document.querySelector('table caption'),
                    colTh: document.querySelectorAll('table thead th[scope=col]').length,
                    rowTh: document.querySelectorAll('table tbody th[scope=row]').length
                };
            }""")
            expected_series = payload_sessions = len(json.loads(PAYLOAD.read_text())["series"])
            if chart["d"] != expected_series:
                out.append(f"{w}: garis chart {chart['d']} titik, harusnya {expected_series}")
            if chart["bars"] != 10:
                out.append(f"{w}: batang bulanan {chart['bars']}, harusnya 10")
            if chart["months"] != 10 or chart["worst"] != 5 or chart["best"] != 5:
                out.append(f"{w}: baris tabel bulan/terburuk/terbaik {chart['months']}/{chart['worst']}/{chart['best']}")
            if not chart["caption"] or chart["colTh"] < 2 or chart["rowTh"] < 20:
                out.append(f"{w}: semantik tabel kurang: {chart}")
            if not out or (chart["d"] == expected_series and chart["bars"] == 10 and chart["months"] == 10):
                print(f"[PASS] {w}: chart {chart['d']} titik, bar {chart['bars']}, tabel {chart['months']}/{chart['worst']}/{chart['best']}, th {chart['colTh']}/{chart['rowTh']}")

            if w == 1440:
                missing = await page.evaluate("""(exp) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                    let s = ''; let n;
                    while (n = walker.nextNode()) s += ' ' + n.nodeValue;
                    return exp.filter(f => !s.includes(f));
                }""", expected_figures)
                if missing:
                    out.append(f"teks DOM tanpa figur kunci: {missing}")
                else:
                    print(f"[PASS] teks DOM memuat semua {len(expected_figures)} figur kunci")

            await page.close()
        await browser.close()
    return out


if __name__ == "__main__":
    sys.exit(main())
