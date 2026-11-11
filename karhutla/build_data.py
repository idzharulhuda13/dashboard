#!/usr/bin/env python3
"""build_data.py - ringkas data/karhutla/agg/*.csv.gz + cells.csv.gz jadi payload halaman karhutla.

Payload: data/karhutla/karhutla.json (bentuk dibekukan di karhutla/INTERFACE.md).
Titik mentah tidak pernah sampai ke browser: halaman hanya menerima hitungan per hari, per provinsi,
per kabupaten (top), dan sel peta 0,25 derajat.

Dua basis sensor, masing-masing dipakai hanya untuk perbandingan yang memang setara:
- di dalam tahun berjalan (pekan ini vs pekan lalu, provinsi, peta): MODIS + NOAA-20, ada sepanjang 2026.
  NOAA-21 baru masuk data SiPongi 2 Nov 2026; menghitungnya akan menggandakan angka tanpa api bertambah.
- antar tahun (2026 vs 2025/2024/2023/2019): MODIS saja, satu-satunya sensor yang ada di data SiPongi 2019.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "karhutla"
OUT = DATA / "karhutla.json"
BASIS = ("NASA-MODIS", "NASA-NOAA20")
YEAR_BASIS = ("NASA-MODIS",)
CMP_YEARS = (2025, 2024, 2023, 2019)
BUDGET_BYTES = 320_000
LUAS = DATA / "source" / "luas_provinsi.json"
# Tahun yang angka luasnya di SiPongi+ tidak sejalan dengan pernyataan SiPongi+ sendiri. Ditandai "belum lengkap"
# hanya selama angkanya masih di bawah ambang; begitu SiPongi+ memperbaikinya, tanda hilang sendiri.
LUAS_SUSPECT = {"2025": 50_000}
LON0, LAT0, CELL = 94.0, 7.0, 0.25
WIB = dt.timezone(dt.timedelta(hours=7))
MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
          "Oktober", "November", "Desember")
MON_S = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


def rows(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def num(n: float, dec: int = 0) -> str:
    s = f"{n:,.{dec}f}"
    return s.replace(",", "#").replace(".", ",").replace("#", ".")


def pct(x: float | None) -> str | None:
    if x is None:
        return None
    return ("+" if x > 0 else "") + num(x, 0) + "%" if abs(x) >= 10 else ("+" if x > 0 else "") + num(x, 1) + "%"


def versus(cur: int, prev: int) -> str | None:
    """Perbandingan antar tahun: di atas dua kali lipat ditulis 'N,N kali', di bawahnya persen."""
    if prev == 0:
        return None
    if cur >= 2 * prev:
        return num(cur / prev, 1) + " kali"
    return pct((cur - prev) / prev * 100)


def change(cur: int, prev: int) -> float | None:
    return None if prev == 0 else (cur - prev) / prev * 100


def long_date(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def short_date(d: dt.date) -> str:
    return f"{d.day} {MON_S[d.month - 1]}"


def span(a: dt.date, b: dt.date) -> str:
    if a.month == b.month:
        return f"{a.day}-{b.day} {MON_S[b.month - 1]}"
    return f"{short_date(a)} - {short_date(b)}"


def ha(s) -> float:
    s = str(s or "").strip()
    return float(s.replace(".", "").replace(",", ".")) if s and s != "-" else 0.0


def build_luas(nat_basis: list[int], d0: dt.date, li: int) -> tuple[dict, dict]:
    doc = json.loads(LUAS.read_text())
    months = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
    mname, myear = doc["last"].split()
    last_m, last_y = months[mname.lower()], int(myear)
    years = sorted({str(x["tahun"]) for p in doc["data"] for x in p["data_luas"]})
    total = {y: 0.0 for y in years}
    prov: dict[str, dict[str, float]] = {}
    for p in doc["data"]:
        nm = p["nama_provinsi"].strip()
        prov[nm] = {str(x["tahun"]): ha(x.get("luas")) for x in p["data_luas"]}
        for y, v in prov[nm].items():
            total[y] += v
    api_total = {str(t["tahun"]): ha(t["total"]) for t in doc["total"]}
    for y in years:
        if abs(api_total.get(y, 0) - total[y]) > 1:
            sys.exit(f"FATAL: luas {y}: jumlah provinsi {total[y]:.2f} != total API {api_total.get(y)}")
    cur = str(last_y)
    full = [y for y in years if y != cur]
    suspect = {y: True for y, lim in LUAS_SUSPECT.items() if y in total and total[y] < lim}
    clean_full = [y for y in full if y not in suspect]
    avg_full = sum(total[y] for y in clean_full) / len(clean_full)
    prov_cur = sorted(((nm, round(v[cur], 2)) for nm, v in prov.items() if v.get(cur, 0) > 0), key=lambda t: (-t[1], t[0]))
    # porsi titik panas tahun ini yang sudah terjadi sampai akhir bulan data luas
    end_last = (dt.date(last_y, last_m % 12 + 1, 1) if last_m < 12 else dt.date(last_y + 1, 1, 1)) - dt.timedelta(days=1)
    upto = min((end_last - d0).days, li - 1)
    hs_upto = sum(nat_basis[: upto + 1])
    hs_ytd = sum(nat_basis[:li])
    share = hs_upto / hs_ytd * 100 if hs_ytd else None
    big = max(clean_full, key=lambda y: total[y])
    luas = {
        "last": doc["last"], "last_month": last_m, "year": cur, "years": years,
        "total": {y: round(total[y], 2) for y in years}, "suspect": sorted(suspect),
        "prov": prov_cur, "avg_full": round(avg_full, 2), "avg_years": [clean_full[0], clean_full[-1]],
        "hs_share_upto": share, "source_url": "https://sipongi.gakkum.kehutanan.go.id/indikasi-luas-kebakaran",
    }
    fig = {
        "luas_cur": {"r": round(total[cur], 2), "d": num(total[cur])},
        "luas_last": {"r": doc["last"], "d": doc["last"]},
        "luas_span": {"r": [1, last_m], "d": f"Januari - {MONTHS[last_m - 1]} {cur}" if last_m > 1 else f"Januari {cur}"},
        "luas_top_prov": {"r": prov_cur[0][0], "d": prov_cur[0][0]},
        "luas_top_ha": {"r": prov_cur[0][1], "d": num(prov_cur[0][1])},
        "luas_avg_full": {"r": round(avg_full, 2), "d": num(avg_full)},
        "luas_avg_span": {"r": [clean_full[0], clean_full[-1]], "d": f"{clean_full[0]}-{clean_full[-1]}"},
        "luas_big_year": {"r": big, "d": big},
        "luas_big": {"r": round(total[big], 2), "d": num(total[big])},
        "luas_hs_share": {"r": share, "d": (num(share, 0) + "%") if share is not None else None},
        "luas_hs_upto": {"r": end_last.isoformat(), "d": long_date(end_last)},
    }
    for y in suspect:
        fig[f"luas_{y}"] = {"r": round(total[y], 2), "d": num(total[y])}
    return luas, fig


def main() -> None:
    state = json.loads((DATA / "harvest_state.json").read_text())
    years = state["years"]
    cur_year = max(int(y) for y in years)
    newest_utc = years[str(cur_year)]["newest_utc"]
    newest = dt.datetime.strptime(newest_utc, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(WIB)
    as_of = newest.date()
    last_full = as_of - dt.timedelta(days=1)
    d0 = dt.date(cur_year, 1, 1)
    n_days = (as_of - d0).days + 1
    idx = lambda d: (dt.date.fromisoformat(d) - d0).days  # noqa: E731

    cur = rows(DATA / "agg" / f"{cur_year}.csv.gz")
    if not cur:
        sys.exit("FATAL: agg tahun berjalan kosong")

    nat_basis = [0] * n_days
    nat_all = [0] * n_days
    nat_high = [0] * n_days
    nat_modis = [0] * n_days
    sensors = collections.Counter()
    sensor_first: dict[str, str] = {}
    prov_daily: dict[str, list[int]] = collections.defaultdict(lambda: [0] * n_days)
    kab_daily: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for r in cur:
        i = idx(r["date"])
        if not (0 <= i < n_days):
            continue
        n = int(r["n"])
        nat_all[i] += n
        sensors[r["sensor"]] += n
        sensor_first[r["sensor"]] = min(sensor_first.get(r["sensor"], "9999"), r["date"])
        if r["sensor"] in YEAR_BASIS:
            nat_modis[i] += n
        if r["sensor"] not in BASIS:
            continue
        nat_basis[i] += n
        if r["conf"] == "high":
            nat_high[i] += n
        prov_daily[r["province"]][i] += n
        kab_daily[(r["province"], r["kabkota"])][i] += n

    # tahun pembanding, diselaraskan per tanggal-bulan ke hari tahun berjalan
    cmp: dict[str, list[int | None]] = {}
    cmp_meta: dict[str, dict] = {}
    for y in CMP_YEARS:
        p = DATA / "agg" / f"{y}.csv.gz"
        if not p.exists() or str(y) not in years:
            continue
        by_md = collections.Counter()
        tot = 0
        for r in rows(p):
            if r["sensor"] in YEAR_BASIS:
                md = r["date"][5:]
                # 29 Feb tahun kabisat digabung ke 28 Feb supaya "1 Jan s.d. tanggal sama" tetap utuh
                by_md["02-28" if md == "02-29" else md] += int(r["n"])
                tot += int(r["n"])
        series: list[int | None] = []
        for i in range(n_days):
            md = (d0 + dt.timedelta(days=i)).isoformat()[5:]
            series.append(by_md.get(md, 0))
        cmp[str(y)] = series
        cmp_meta[str(y)] = {"year_total": tot, "through": years[str(y)]["through"]}

    # jendela: hari terakhir (parsial), 7 dan 30 hari penuh yang berakhir kemarin
    li = n_days - 1
    def win(k: int) -> tuple[int, int]:
        return (li - k, li - 1) if k > 1 else (li, li)
    windows = {"1": win(1), "7": win(7), "30": win(30), "ytd": (0, li - 1)}
    prev_windows = {"7": (li - 14, li - 8), "30": (li - 60, li - 31)}

    def ssum(arr, a, b):
        return sum(v for v in arr[a:b + 1] if v is not None)

    provs = sorted(prov_daily, key=lambda p: -sum(prov_daily[p]))
    kab_top: dict[str, dict[str, list]] = {}
    for w, (a, b) in windows.items():
        per = collections.defaultdict(list)
        for (p, k), c in kab_daily.items():
            n = sum(v for i, v in c.items() if a <= i <= b)
            if n:
                per[p].append((k, n))
        kab_top[w] = {str(provs.index(p)): [[k, n] for k, n in sorted(v, key=lambda t: (-t[1], t[0]))[:6]]
                      for p, v in per.items()}

    # sel peta: basis sensor, semua tingkat keyakinan
    cell_rows = rows(DATA / "cells.csv.gz")
    cells: dict[str, list[list[int]]] = {}
    for w in ("1", "7", "30"):
        a, b = windows[w]
        da, db = (d0 + dt.timedelta(days=a)).isoformat(), (d0 + dt.timedelta(days=b)).isoformat()
        c = collections.Counter()
        for r in cell_rows:
            if r["sensor"] in BASIS and da <= r["date"] <= db:
                gx = round((float(r["lon"]) - LON0 - CELL / 2) / CELL)
                gy = round((LAT0 - float(r["lat"]) - CELL / 2) / CELL)
                c[(gx, gy)] += int(r["n"])
        cells[w] = [[gx, gy, n] for (gx, gy), n in sorted(c.items())]

    # angka utama
    w7a, w7b = windows["7"]
    p7a, p7b = prev_windows["7"]
    k7 = ssum(nat_basis, w7a, w7b)
    k7_prev = ssum(nat_basis, p7a, p7b)
    k7m = ssum(nat_modis, w7a, w7b)
    k7_cmp = {y: ssum(s, w7a, w7b) for y, s in cmp.items()}
    ytd = ssum(nat_basis, 0, li - 1)
    ytdm = ssum(nat_modis, 0, li - 1)
    ytd_cmp = {y: ssum(s, 0, li - 1) for y, s in cmp.items()}
    today_n = nat_basis[li]
    yday_n = nat_basis[li - 1]
    peak_i = max(range(n_days - 1), key=lambda i: nat_basis[i])
    top_prov = max(provs, key=lambda p: ssum(prov_daily[p], w7a, w7b)) if k7 else None
    top_prov_n = ssum(prov_daily[top_prov], w7a, w7b) if top_prov else 0

    first_noaa21 = sensor_first.get("NASA-NOAA21")
    day = lambda i: d0 + dt.timedelta(days=i)  # noqa: E731
    fig = {
        "k7": {"r": k7, "d": num(k7)},
        "k7_prev": {"r": k7_prev, "d": num(k7_prev)},
        "k7_chg": {"r": change(k7, k7_prev), "d": pct(change(k7, k7_prev))},
        "k7_span": {"r": [day(w7a).isoformat(), day(w7b).isoformat()], "d": span(day(w7a), day(w7b))},
        "k7_prev_span": {"r": [day(p7a).isoformat(), day(p7b).isoformat()], "d": span(day(p7a), day(p7b))},
        "today": {"r": today_n, "d": num(today_n)},
        "today_label": {"r": as_of.isoformat(), "d": short_date(as_of)},
        "today_time": {"r": newest.strftime("%H:%M"), "d": newest.strftime("%H.%M")},
        "yday": {"r": yday_n, "d": num(yday_n)},
        "yday_label": {"r": last_full.isoformat(), "d": short_date(last_full)},
        "ytd": {"r": ytd, "d": num(ytd)},
        "k7m": {"r": k7m, "d": num(k7m)},
        "ytdm": {"r": ytdm, "d": num(ytdm)},
        "ytd_span": {"r": [d0.isoformat(), last_full.isoformat()], "d": f"1 Jan - {short_date(last_full)}"},
        "peak": {"r": nat_basis[peak_i], "d": num(nat_basis[peak_i])},
        "peak_date": {"r": day(peak_i).isoformat(), "d": long_date(day(peak_i))},
        "top_prov": {"r": top_prov, "d": top_prov or "tidak ada"},
        "top_prov_n": {"r": top_prov_n, "d": num(top_prov_n)},
        "top_prov_share": {"r": (top_prov_n / k7 * 100) if k7 else None,
                           "d": (num(top_prov_n / k7 * 100, 0) + "%") if k7 else None},
        "all_ytd": {"r": sum(nat_all[: li]), "d": num(sum(nat_all[: li]))},
        "noaa21_n": {"r": sensors.get("NASA-NOAA21", 0), "d": num(sensors.get("NASA-NOAA21", 0))},
        "noaa21_from": {"r": first_noaa21, "d": long_date(dt.date.fromisoformat(first_noaa21)) if first_noaa21 else None},
    }
    for y in cmp:
        fig[f"k7_{y}"] = {"r": k7_cmp[y], "d": num(k7_cmp[y])}
        fig[f"k7_vs_{y}"] = {"r": change(k7m, k7_cmp[y]), "d": versus(k7m, k7_cmp[y])}
        fig[f"ytd_{y}"] = {"r": ytd_cmp[y], "d": num(ytd_cmp[y])}
        fig[f"ytd_vs_{y}"] = {"r": change(ytdm, ytd_cmp[y]), "d": versus(ytdm, ytd_cmp[y])}

    payload = {
        "meta": {
            "as_of": as_of.isoformat(),
            "as_of_time": newest.strftime("%H:%M"),
            "newest_utc": newest_utc,
            "harvested_at": years[str(cur_year)]["harvested_at"],
            "year": cur_year,
            "d0": d0.isoformat(),
            "n_days": n_days,
            "basis": list(BASIS),
            "year_basis": list(YEAR_BASIS),
            "cmp_years": list(cmp),
            "cmp_meta": cmp_meta,
            "sensors_ytd": dict(sorted(sensors.items())),
            "points_ytd": sum(sensors.values()),
            "grid": {"lon0": LON0, "lat0": LAT0, "cell": CELL},
            "windows": {k: list(v) for k, v in windows.items()},
            "prev_windows": {k: list(v) for k, v in prev_windows.items()},
            "source": "SiPongi+ Direktorat Pengendalian Kebakaran Hutan dan Lahan, Kementerian Kehutanan",
            "source_url": "https://sipongi.gakkum.kehutanan.go.id",
            "api": "https://opsroom.sipongidata.my.id/api/opsroom/indoHotspot",
        },
        "nat": {"basis": nat_basis, "high": nat_high, "modis": nat_modis, "all": nat_all},
        "cmp": cmp,
        "prov": {"names": provs, "daily": [prov_daily[p] for p in provs]},
        "kab": kab_top,
        "cells": cells,
        "fig": fig,
    }
    payload["luas"], luas_fig = build_luas(nat_basis, d0, li)
    payload["fig"].update(luas_fig)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode()) > BUDGET_BYTES:
        sys.exit(f"FATAL: payload {len(text.encode())} byte > anggaran {BUDGET_BYTES}")
    OUT.write_text(text + "\n")
    print(f"payload {len(text.encode())} byte; as_of {as_of} {newest:%H:%M} WIB; 7 hari {k7} (prev {k7_prev}); "
          f"ytd {ytd}; provinsi {len(provs)}; sel 1/7/30 = {[len(cells[w]) for w in ('1', '7', '30')]}; "
          f"pembanding {list(cmp)}")


if __name__ == "__main__":
    main()
