#!/usr/bin/env python3
"""verify.py - gate halaman karhutla. Exit 0 = semua lolos.

Yang dibuktikan:
  A. angka payload dihitung ulang dari data/karhutla/agg/*.csv.gz lewat jalur lain (pandas-free, per string
     tanggal, bukan indeks hari) dan dicocokkan nilai mentah + string tampilannya;
  B. sel peta per jendela berjumlah sama dengan total nasional dari agg (dua ringkasan dari titik yang sama);
  C. halaman: slot data-fig = payload, strip dateline = meta, stamp build = sha template + payload;
  D. kontras token warna (WCAG) pada ground yang dipakai;
  E. browser 1440 + 390: nol galat konsol, nol overflow, lantai 12px, tiap kontrol mengubah tampilan,
     drill + kembali + Escape, state kosong dan galat bisa dicapai;
  F. uji negatif: angka payload diganggu -> gate A wajib gagal.

  python3 karhutla/verify.py            # semua
  python3 karhutla/verify.py --no-browser
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "karhutla"
PAGE = ROOT / "karhutla" / "index.html"
TEMPLATE = ROOT / "karhutla" / "index.template.html"
BASIS = {"NASA-MODIS", "NASA-NOAA20"}
YEAR_BASIS = {"NASA-MODIS"}

results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> bool:
    results.append((bool(ok), msg))
    return bool(ok)


def fmt(n: float, dec: int = 0) -> str:
    return f"{n:,.{dec}f}".replace(",", "#").replace(".", ",").replace("#", ".")


def load_agg(year: int) -> list[tuple[str, str, int, str, str]]:
    out = []
    with gzip.open(DATA / "agg" / f"{year}.csv.gz", "rt") as fh:
        next(fh)
        for line in fh:
            # format kolom tetap; nama kabupaten bisa berkoma -> pakai csv hanya bila perlu
            parts = next(csv.reader([line]))
            out.append((parts[0], parts[1], int(parts[5]), parts[3], parts[2]))
    return out


def dsum(rows, a: str, b: str, sensors: set[str], md: bool = False) -> int:
    if md:
        return sum(n for d, s, n, *_ in rows if s in sensors and a <= d[5:] <= b)
    return sum(n for d, s, n, *_ in rows if s in sensors and a <= d <= b)


def gate_numbers(p: dict) -> None:
    m, fig = p["meta"], p["fig"]
    d0 = dt.date.fromisoformat(m["d0"])
    day = lambda i: (d0 + dt.timedelta(days=i)).isoformat()  # noqa: E731
    cur = load_agg(m["year"])
    as_of = m["as_of"]
    yday = (dt.date.fromisoformat(as_of) - dt.timedelta(days=1)).isoformat()
    w7 = ((dt.date.fromisoformat(as_of) - dt.timedelta(days=7)).isoformat(), yday)
    p7 = ((dt.date.fromisoformat(as_of) - dt.timedelta(days=14)).isoformat(),
          (dt.date.fromisoformat(as_of) - dt.timedelta(days=8)).isoformat())
    check(fig["k7_span"]["r"] == list(w7), f"jendela 7 hari {fig['k7_span']['r']} = {list(w7)}")
    exp = {
        "k7": dsum(cur, *w7, BASIS),
        "k7_prev": dsum(cur, *p7, BASIS),
        "today": dsum(cur, as_of, as_of, BASIS),
        "yday": dsum(cur, yday, yday, BASIS),
        "ytd": dsum(cur, f"{m['year']}-01-01", yday, BASIS),
        "k7m": dsum(cur, *w7, YEAR_BASIS),
        "ytdm": dsum(cur, f"{m['year']}-01-01", yday, YEAR_BASIS),
        "all_ytd": dsum(cur, f"{m['year']}-01-01", yday, {"NASA-MODIS", "NASA-NOAA20", "NASA-NOAA21"}),
        "noaa21_n": sum(n for d, s, n, *_ in cur if s == "NASA-NOAA21"),
    }
    for k, v in exp.items():
        check(fig[k]["r"] == v and fig[k]["d"] == fmt(v), f"{k}: payload {fig[k]['r']} / '{fig[k]['d']}' vs hitung ulang {v}")
    chg = (exp["k7"] - exp["k7_prev"]) / exp["k7_prev"] * 100
    check(abs(fig["k7_chg"]["r"] - chg) < 1e-9, f"k7_chg {fig['k7_chg']['r']:.4f} vs {chg:.4f}")
    # puncak harian
    daily: dict[str, int] = {}
    for d, s, n, *_ in cur:
        if s in BASIS and d <= yday:
            daily[d] = daily.get(d, 0) + n
    pk = max(sorted(daily), key=lambda d: daily[d])
    check(fig["peak"]["r"] == daily[pk] and fig["peak_date"]["r"] == pk, f"puncak {fig['peak_date']['r']} {fig['peak']['r']} vs {pk} {daily[pk]}")
    # provinsi teratas
    prov: dict[str, int] = {}
    for d, s, n, pr, _ in cur:
        if s in BASIS and w7[0] <= d <= w7[1]:
            prov[pr] = prov.get(pr, 0) + n
    top = max(sorted(prov), key=lambda k: prov[k])
    check(fig["top_prov"]["r"] == top and fig["top_prov_n"]["r"] == prov[top], f"provinsi teratas {fig['top_prov']['r']} vs {top} {prov[top]}")
    check(fig["top_prov_share"]["d"] == fmt(prov[top] / exp["k7"] * 100) + "%", "porsi provinsi teratas")
    # deret provinsi di payload = hitung ulang
    names = p["prov"]["names"]
    bad = 0
    for i, nm in enumerate(names):
        s = p["prov"]["daily"][i]
        if sum(s[(dt.date.fromisoformat(w7[0]) - d0).days:(dt.date.fromisoformat(w7[1]) - d0).days + 1]) != prov.get(nm, 0):
            bad += 1
    check(bad == 0, f"deret 39 provinsi cocok untuk jendela 7 hari ({bad} beda)")
    check(sum(p["nat"]["basis"]) == dsum(cur, m["d0"], as_of, BASIS), "deret nasional basis = agg")
    # antar tahun (MODIS, per tanggal-bulan)
    for y in m["cmp_years"]:
        rows = load_agg(int(y))
        ytd_y = dsum(rows, "01-01", yday[5:], YEAR_BASIS, md=True)
        k7_y = dsum(rows, w7[0][5:], w7[1][5:], YEAR_BASIS, md=True)
        check(fig[f"ytd_{y}"]["r"] == ytd_y, f"ytd {y}: {fig[f'ytd_{y}']['r']} vs {ytd_y}")
        check(fig[f"k7_{y}"]["r"] == k7_y, f"7 hari {y}: {fig[f'k7_{y}']['r']} vs {k7_y}")
        ratio = exp["ytdm"] / ytd_y
        want = fmt(ratio, 1) + " kali" if ratio >= 2 else None
        if want:
            check(fig[f"ytd_vs_{y}"]["d"] == want, f"ytd_vs_{y} '{fig[f'ytd_vs_{y}']['d']}' vs '{want}'")
    # B: sel peta = total nasional per jendela
    for w in ("1", "7", "30"):
        a, b = m["windows"][w]
        tot = sum(c[2] for c in p["cells"][w])
        check(tot == dsum(cur, day(a), day(b), BASIS), f"sel peta jendela {w}: {tot} = agg {dsum(cur, day(a), day(b), BASIS)}")


def gate_page(p: dict) -> None:
    html = PAGE.read_text()
    for k, v in re.findall(r'data-fig="([a-z0-9_]+)">([^<]*)<', html):
        check(k in p["fig"] and p["fig"][k]["d"] == v.replace("&amp;", "&"), f"slot {k} = '{v}'")
    m = re.search(r'data-as-of="([^"]+)"', html)
    check(m and m.group(1) == p["meta"]["as_of"], f"dateline data-as-of {m.group(1) if m else None}")
    t = re.search(r"<span data-dateline-text>([^<]*)</span>", html)
    y, mo, d = p["meta"]["as_of"].split("-")
    months = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
              "November", "Desember")
    want = f"Data per {int(d)} {months[int(mo) - 1]} {y}"
    check(t and t.group(1) == want, f"teks dateline '{t.group(1) if t else None}' = '{want}'")
    stamp = re.search(r"<!--BUILD template:([0-9a-f]+) payload:([0-9a-f]+)-->", html)
    check(stamp and stamp.group(1) == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()[:16], "stamp template")
    check(stamp and stamp.group(2) == hashlib.sha256((DATA / "karhutla.json").read_bytes()).hexdigest()[:16], "stamp payload")
    check("\u2014" not in html, "nol em dash")
    check(len(html.encode()) <= 640_000, f"ukuran halaman {len(html.encode())} byte")


def lum(hx: str) -> float:
    c = [int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def gate_contrast() -> None:
    grounds = {"ground": "#F7F1E6", "plane": "#FFFBF3", "plane-2": "#F0E6D4"}
    for g, hx in grounds.items():
        check(ratio("#6B5B4A", hx) >= 4.5, f"muted di {g} {ratio('#6B5B4A', hx):.2f}:1 >= 4.5")
        check(ratio("#1A1410", hx) >= 4.5, f"ink di {g} {ratio('#1A1410', hx):.2f}:1")
        check(ratio("#B42F14", hx) >= 3, f"api (tanda) di {g} {ratio('#B42F14', hx):.2f}:1 >= 3")
    check(ratio("#8C7A62", "#F0E6D4") >= 3, f"bar tahun pembanding di plane-2 {ratio('#8C7A62', '#F0E6D4'):.2f}:1 >= 3")
    check(ratio("#FFFBF3", "#1A1410") >= 4.5, "teks tombol aktif")


def gate_browser(p: dict) -> None:
    from playwright.sync_api import sync_playwright

    url = PAGE.resolve().as_uri()
    html = PAGE.read_text()
    with tempfile.TemporaryDirectory() as td:
        empty = copy.deepcopy(p)
        empty["cells"] = {"1": [], "7": [], "30": []}
        for arr in empty["prov"]["daily"]:
            for i in range(len(arr)):
                arr[i] = 0
        pe = Path(td) / "empty.html"
        a = html.index('<script type="application/json" id="payload">') + len('<script type="application/json" id="payload">')
        z = html.index("</script>", a)
        pe.write_text(html[:a] + json.dumps(empty).replace("</", "<\\/") + html[z:])
        pb = Path(td) / "broken.html"
        pb.write_text(html[:a] + '{"meta": ' + html[z:])
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for w, h in ((1440, 900), (390, 844)):
                pg = b.new_page(viewport={"width": w, "height": h})
                errs: list[str] = []
                pg.on("console", lambda msg: errs.append(msg.text) if msg.type == "error" else None)
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.goto(url)
                pg.wait_for_function("!document.body.classList.contains('is-loading')", timeout=5000)
                info = pg.evaluate("""() => ({
                  over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                  small: [...document.querySelectorAll('body *')].filter(e => e.getClientRects().length &&
                     [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) &&
                     parseFloat(getComputedStyle(e).fontSize) < 12).length,
                  tap: [...document.querySelectorAll('button')].filter(e => e.getClientRects().length &&
                     e.getBoundingClientRect().height < 44).length,
                  err: document.body.classList.contains('has-error')})""")
                check(info["over"] <= 0, f"{w}px: overflow {info['over']}")
                check(info["small"] == 0, f"{w}px: teks < 12px = {info['small']}")
                check(info["tap"] == 0, f"{w}px: tombol < 44px = {info['tap']}")
                check(not info["err"], f"{w}px: tidak masuk state galat")
                # peta: tiap periode mengubah jumlah sel dan teks baca
                seen = {}
                for wnd in ("1", "30", "7"):
                    pg.click(f'[data-map="{wnd}"]')
                    pg.wait_for_function(f"document.querySelectorAll('.dot').length === {len(p['cells'][wnd])}", timeout=3000)
                    seen[wnd] = pg.inner_text("#map-read")
                    check(pg.get_attribute(f'[data-map="{wnd}"]', "aria-pressed") == "true", f"{w}px: tombol peta {wnd} aktif")
                check(len(set(seen.values())) == 3, f"{w}px: teks peta berbeda untuk 3 periode")
                tot7 = fmt(sum(c[2] for c in p["cells"]["7"]))
                check(tot7 in seen["7"], f"{w}px: peta 7 hari menyebut {tot7}")
                pg.locator(".dot").first.dispatch_event("click")
                check("titik panas." in pg.inner_text("#map-read") and "Sel sekitar" in pg.inner_text("#map-read"), f"{w}px: ketuk sel menampilkan jumlah")
                # tren: keyboard
                pg.focus("#trend")
                before = pg.inner_text("#trend-read")
                pg.keyboard.press("ArrowLeft")
                after = pg.inner_text("#trend-read")
                check(before != after and p["fig"]["yday_label"]["d"] in after, f"{w}px: panah kiri ke {p['fig']['yday_label']['d']}")
                # provinsi
                leads = {}
                for wnd in ("30", "ytd", "7"):
                    pg.click(f'[data-prov="{wnd}"]')
                    pg.wait_for_timeout(50)
                    leads[wnd] = pg.inner_text("#prov-lead")
                check(len(set(leads.values())) == 3, f"{w}px: daftar provinsi berubah per periode")
                first = pg.inner_text(".prow .pn")
                check(first == p["fig"]["top_prov"]["d"], f"{w}px: baris pertama {first}")
                n0 = pg.locator(".prow").count()
                pg.click("#prov-more")
                n1 = pg.locator(".prow").count()
                check(n1 > n0, f"{w}px: tampilkan semua {n0} -> {n1}")
                pg.click("#prov-more")
                pg.locator(".prow").first.click()
                check(pg.is_visible("#drill") and pg.inner_text("#drill-name") == first, f"{w}px: drill buka {first}")
                check(pg.locator("#drill-kab div").count() > 0, f"{w}px: drill ada kabupaten")
                pg.click("#drill-back")
                check(not pg.is_visible("#drill") and pg.is_visible("#prov-list"), f"{w}px: tombol kembali")
                pg.locator(".prow").nth(1).click()
                pg.keyboard.press("Escape")
                check(not pg.is_visible("#drill"), f"{w}px: Escape menutup drill")
                check(not errs, f"{w}px: galat konsol {errs[:2]}")
                pg.close()
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.goto(pe.as_uri())
            pg.wait_for_timeout(300)
            check(pg.is_visible(".map-wrap.is-empty .empty"), "state kosong peta tampil")
            check(pg.is_visible("#prov-empty"), "state kosong provinsi tampil")
            pg.goto(pb.as_uri())
            pg.wait_for_timeout(300)
            check(pg.is_visible(".err"), "state galat tampil saat payload rusak")
            b.close()


def negative(p: dict) -> None:
    global results
    saved = results
    bad = copy.deepcopy(p)
    bad["fig"]["k7"]["r"] += 1
    bad["fig"]["ytd_2019"]["r"] -= 5
    results = []
    gate_numbers(bad)
    fails = [m for ok, m in results if not ok]
    results = saved
    check(any(m.startswith("k7:") for m in fails) and any("ytd 2019" in m for m in fails),
          f"uji negatif: gangguan k7 + ytd_2019 tertangkap ({len(fails)} gagal)")


def main() -> int:
    p = json.loads((DATA / "karhutla.json").read_text())
    gate_numbers(p)
    gate_page(p)
    gate_contrast()
    negative(p)
    if "--no-browser" not in sys.argv:
        gate_browser(p)
    fails = [m for ok, m in results if not ok]
    for ok, m in results:
        if not ok:
            print("FAIL", m)
    print(f"TOTAL {len(results) - len(fails)}/{len(results)} lolos")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
