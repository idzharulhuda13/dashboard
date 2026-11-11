#!/usr/bin/env python3
"""harvest.py - tarik titik panas SiPongi+ (Kemenhut) dan langsung ringkas, tanpa menyimpan titik mentah.

Sumber: https://opsroom.sipongidata.my.id/api/opsroom/indoHotspot (terbuka, tanpa key).
Filter from/to API memakai tanggal WIB (dicek 11 Nov 2026: 2026-11-10 -> ori 09T17:32Z..10T16:17Z),
jadi potongan 7 hari per tanggal WIB tidak tumpang tindih dan tidak bolong.

Keluaran (data/karhutla/):
  agg/<tahun>.csv.gz     date,sensor,conf,province,kabkota,n   (date = tanggal WIB, dari date_hotspot_ori + 7 jam)
  cells.csv.gz           date,sensor,conf,lat,lon,n            (sel 0,25 derajat, hanya CELL_DAYS hari terakhir)
  harvest_state.json     waktu tarik terakhir + titik terbaru (UTC) per tahun

Pemakaian:
  python3 karhutla/harvest.py --year 2019 2023 2024 2025 2026   # tarik penuh per tahun
  python3 karhutla/harvest.py --recent 4                       # ulang 4 hari WIB terakhir (cron)
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "karhutla"
AGG = DATA / "agg"
CELLS = DATA / "cells.csv.gz"
LUAS = DATA / "source" / "luas_provinsi.json"
LUAS_URL = "https://opsroom.sipongidata.my.id/api/list/luas-kebakaran/provinsi?tahunAwal=2015&tahunAkhir={year}"
STATE = DATA / "harvest_state.json"
BASE = "https://opsroom.sipongidata.my.id/api/opsroom/indoHotspot"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
SATS = ["NASA-MODIS", "NASA-NOAA20", "NASA-NOAA21"]
CONF = ["low", "medium", "high"]
CELL = 0.25
CELL_DAYS = 35
WIB = dt.timezone(dt.timedelta(hours=7))
AGG_COLS = ["date", "sensor", "conf", "province", "kabkota", "n"]
CELL_COLS = ["date", "sensor", "conf", "lat", "lon", "n"]


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(WIB):%Y-%m-%d %H:%M:%S} WIB] {msg}", flush=True)


def fetch(d0: dt.date, d1: dt.date, tries: int = 4) -> list[dict]:
    q = [("wilayah", "IN"), ("filterperiode", "true"), ("from", d0.isoformat()), ("to", d1.isoformat()),
         ("late", "custom")]
    q += [("satelit[]", s) for s in SATS] + [("confidence[]", c) for c in CONF]
    url = BASE + "?" + urllib.parse.urlencode(q, safe="[]")
    last: Exception | None = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                                       "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=240) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            doc = json.loads(body.decode("utf-8"))
            feats = doc.get("features")
            if not isinstance(feats, list):
                raise ValueError("respon tanpa 'features'")
            return feats
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{d0}..{d1}: {last}")


def wib_date(ori: str) -> str:
    t = dt.datetime.strptime(ori[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(WIB).date().isoformat()


def cell_of(v: float) -> float:
    return round(math.floor(v / CELL) * CELL + CELL / 2, 3)


def reduce(feats: list[dict], d0: dt.date, d1: dt.date, agg: collections.Counter,
           cells: collections.Counter | None, cell_from: str) -> tuple[int, int, str]:
    """Tambah titik ke counter; kembalikan (dipakai, di luar rentang, titik terbaru UTC)."""
    used = outside = 0
    newest = ""
    lo, hi = d0.isoformat(), d1.isoformat()
    for f in feats:
        p = f.get("properties") or {}
        ori = p.get("date_hotspot_ori") or ""
        if len(ori) < 19:
            outside += 1
            continue
        day = wib_date(ori)
        if not (lo <= day <= hi):
            outside += 1
            continue
        sensor = p.get("sumber") or "?"
        conf = p.get("confidence_level") or "?"
        agg[(day, sensor, conf, (p.get("nama_provinsi") or "?").strip(), (p.get("kabkota") or "?").strip())] += 1
        if cells is not None and day >= cell_from:
            lat, lon = p.get("lat"), p.get("long")
            if lat is not None and lon is not None:
                cells[(day, sensor, conf, cell_of(float(lat)), cell_of(float(lon)))] += 1
        newest = max(newest, ori[:19])
        used += 1
    return used, outside, newest


def read_csv_gz(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv_gz(path: Path, cols: list[str], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in sorted(rows, key=lambda r: tuple(str(x) for x in r)):
        w.writerow(r)
    tmp = path.with_suffix(".tmp")
    # mtime=0 supaya isi sama -> byte sama (determinisme, diff git bersih)
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    tmp.replace(path)


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")


def today_wib() -> dt.date:
    return dt.datetime.now(WIB).date()


def harvest_range(d0: dt.date, d1: dt.date, cell_from: str) -> tuple[collections.Counter, collections.Counter, str, int]:
    agg: collections.Counter = collections.Counter()
    cells: collections.Counter = collections.Counter()
    newest, total, d = "", 0, d0
    while d <= d1:
        e = min(d + dt.timedelta(days=6), d1)
        t0 = time.time()
        feats = fetch(d, e)
        used, outside, nw = reduce(feats, d, e, agg, cells, cell_from)
        newest = max(newest, nw)
        total += used
        log(f"{d}..{e}: {used} titik ({outside} di luar rentang) {time.time() - t0:.1f}s")
        d = e + dt.timedelta(days=1)
    return agg, cells, newest, total


def merge_cells(new: collections.Counter, refreshed_from: str, cell_from: str) -> int:
    keep = [(r["date"], r["sensor"], r["conf"], float(r["lat"]), float(r["lon"]), int(r["n"]))
            for r in read_csv_gz(CELLS) if cell_from <= r["date"] < refreshed_from]
    rows = keep + [(*k, n) for k, n in new.items() if k[0] >= cell_from]
    write_csv_gz(CELLS, CELL_COLS, rows)
    return len(rows)


def full_year(year: int) -> None:
    end = min(dt.date(year, 12, 31), today_wib())
    cell_from = (today_wib() - dt.timedelta(days=CELL_DAYS - 1)).isoformat()
    agg, cells, newest, total = harvest_range(dt.date(year, 1, 1), end, cell_from)
    write_csv_gz(AGG / f"{year}.csv.gz", AGG_COLS, [(*k, n) for k, n in agg.items()])
    if year == today_wib().year:
        n = merge_cells(cells, f"{year}-01-01", cell_from)
        log(f"cells.csv.gz: {n} baris")
    st = load_state()
    st.setdefault("years", {})[str(year)] = {"through": end.isoformat(), "points": total, "newest_utc": newest,
                                             "harvested_at": dt.datetime.now(WIB).isoformat(timespec="seconds")}
    save_state(st)
    log(f"TAHUN {year}: {total} titik -> agg/{year}.csv.gz")


def recent(days: int) -> None:
    today = today_wib()
    d0 = today - dt.timedelta(days=days - 1)
    if d0.year != today.year:
        d0 = dt.date(today.year, 1, 1)
    path = AGG / f"{today.year}.csv.gz"
    if not path.exists():
        sys.exit(f"FATAL: {path} belum ada; jalankan --year {today.year} dulu")
    cell_from = (today - dt.timedelta(days=CELL_DAYS - 1)).isoformat()
    agg, cells, newest, total = harvest_range(d0, today, cell_from)
    old = read_csv_gz(path)
    kept = [(r["date"], r["sensor"], r["conf"], r["province"], r["kabkota"], int(r["n"]))
            for r in old if r["date"] < d0.isoformat()]
    prev_total = sum(int(r["n"]) for r in old if r["date"] >= d0.isoformat())
    if total < prev_total * 0.5 and prev_total > 200:
        # API kadang balas sebagian; jangan timpa data bagus dengan data bolong
        sys.exit(f"FATAL: tarikan baru {total} titik < separuh data tersimpan {prev_total} untuk {d0}..{today}")
    write_csv_gz(path, AGG_COLS, kept + [(*k, n) for k, n in agg.items()])
    n = merge_cells(cells, d0.isoformat(), cell_from)
    st = load_state()
    y = st.setdefault("years", {}).setdefault(str(today.year), {})
    y.update({"through": today.isoformat(), "newest_utc": max(newest, y.get("newest_utc", "")),
              "harvested_at": dt.datetime.now(WIB).isoformat(timespec="seconds")})
    save_state(st)
    log(f"RECENT {d0}..{today}: {total} titik (sebelumnya {prev_total}); cells {n} baris; terbaru {newest}Z")


def luas() -> None:
    """Rekap luas indikasi karhutla resmi (ha) per provinsi per tahun. Kecil (~18 KB) dan jarang berubah.

    Gagal tarik tidak menggagalkan pembaruan titik panas: berkas lama tetap dipakai dan halaman menyebut
    periodenya sendiri (`last`). Respons yang bentuknya aneh tidak pernah menimpa berkas yang sehat.
    """
    url = LUAS_URL.format(year=today_wib().year)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            doc = json.loads(r.read().decode("utf-8"))
        if not (isinstance(doc.get("data"), list) and len(doc["data"]) >= 30 and doc.get("total") and doc.get("last")):
            raise ValueError("bentuk respons tidak dikenal")
    except Exception as e:  # noqa: BLE001
        log(f"PERINGATAN: luas gagal ditarik ({e}); pakai berkas lama")
        return
    LUAS.parent.mkdir(parents=True, exist_ok=True)
    new = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    changed = not LUAS.exists() or LUAS.read_text() != new
    LUAS.write_text(new)
    log(f"luas: {len(doc['data'])} provinsi, periode terakhir {doc['last']}{' (berubah)' if changed else ''}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, nargs="*", default=[])
    ap.add_argument("--recent", type=int, default=0)
    ap.add_argument("--luas", action="store_true", help="tarik rekap luas indikasi karhutla per provinsi")
    a = ap.parse_args()
    if a.luas:
        luas()
    for y in a.year:
        full_year(y)
    if a.recent:
        recent(a.recent)
    if not a.year and not a.recent and not a.luas:
        ap.print_help()


if __name__ == "__main__":
    main()
