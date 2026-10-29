#!/usr/bin/env python3
"""refresh_rupiah.py - append kurs USD/IDR terbaru, rebuild payload + pasar/rupiah.html, opsi push.

Sumber angka, tidak ada yang diketik tangan:
- Kurs USD/IDR harian: Frankfurter (referensi ECB), https://api.frankfurter.app
- IHSG harian: data/pasar/ihsg.json (job 16.20 WIB)

Langkah: ambil seri kurs -> hitung payload (level, puncak/dasar, korelasi return harian,
arah bulanan lawan IHSG) -> tulis data/pasar/rupiah.json -> [--page] rebuild + verify ->
[--push] pull --rebase, commit berkas ini saja, push.

Contoh cron (ECB terbit ~16.00 CET = 21.00-22.00 WIB):
  python3 pasar/refresh_rupiah.py --page --push
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "data" / "pasar" / "rupiah.json"
IHSG = ROOT / "data" / "pasar" / "ihsg.json"
BASE_DATE = "2025-12-31"
FX_URL = f"https://api.frankfurter.app/{BASE_DATE}..?from=USD&to=IDR"
WIB = ZoneInfo("Asia/Jakarta")

LONG_MONTH = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


def log(msg: str) -> None:
    print(f"[{datetime.now(WIB):%Y-%m-%d %H:%M:%S} WIB] {msg}")


def fetch_fx() -> dict[str, float]:
    req = urllib.request.Request(FX_URL, headers={"User-Agent": "databyarc/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = json.loads(r.read())
    return {d: v["IDR"] for d, v in raw["rates"].items() if v.get("IDR")}


def monthly_fx(dates: list[str], fx: dict[str, float]) -> list[dict]:
    by_month: dict[str, list[str]] = {}
    for d in dates:
        by_month.setdefault(d[:7], []).append(d)
    out, prev = [], None
    for m in sorted(by_month):
        ds = sorted(by_month[m])
        end = fx[ds[-1]]
        base = prev if prev is not None else end
        out.append({"month": m, "label": ds[-1], "end": end,
                    "ret": (end / base - 1) * 100 if base else 0.0})
        prev = end
    return out


def build_payload(fx: dict[str, float], ihsg: dict) -> dict:
    base_date = BASE_DATE if BASE_DATE in fx else min(fx)
    dates = [d for d in sorted(fx) if d >= base_date]
    first, last = dates[0], dates[-1]
    lvl0, lvl1 = fx[first], fx[last]
    hi_d = max(dates, key=lambda d: fx[d])
    lo_d = min(dates, key=lambda d: fx[d])
    months = monthly_fx(dates, fx)

    series = ihsg["series"]
    ihsg_by_date = {r["date"]: r["close"] for r in series}

    # korelasi return harian: pasangan hari berurutan yang ada di dua sumber sekaligus
    rets = []
    for a, b in zip(dates, dates[1:]):
        if a in ihsg_by_date and b in ihsg_by_date:
            rets.append((fx[b] / fx[a] - 1, ihsg_by_date[b] / ihsg_by_date[a] - 1))
    if len(rets) < 2:
        raise SystemExit(f"FATAL: cuma {len(rets)} hari beririsan antara kurs dan IHSG; "
                         "korelasi tidak bisa dihitung (cek data/pasar/ihsg.json)")
    corr = statistics.correlation([x for x, _ in rets], [y for _, y in rets])

    # bulan berjalan: kurs dan IHSG dihitung pada JENDELA YANG SAMA (sesudah `last` dibuang)
    cur_month = last[:7]
    cur_fx = next(m["ret"] for m in months if m["month"] == cur_month)
    window = [r for r in series if r["date"] <= last]
    month_rows = [r for r in window if r["date"].startswith(cur_month)]
    if not month_rows:
        raise SystemExit(f"FATAL: IHSG tidak punya sesi {cur_month} (jendela sampai {last})")
    before = [r for r in window if r["date"] < f"{cur_month}-01"]
    if not before:
        raise SystemExit(f"FATAL: IHSG tidak punya sesi sebelum {cur_month}; dasar bulan tidak bisa dihitung")
    base_close = before[-1]["close"]
    cur_ihsg = (month_rows[-1]["close"] / base_close - 1) * 100
    if month_rows[-1]["date"] != last:
        log(f"catatan: IHSG {cur_month} dihitung sampai {month_rows[-1]['date']}, kurs sampai {last}")

    # bulan: arah kurs vs arah IHSG
    fx_month = {m["month"]: m["ret"] for m in months}
    ihsg_month = {m["month"]: m["ret"] for m in ihsg["monthly"]}
    same_dir, diff_dir = [], []
    for m in sorted(fx_month):
        if m not in ihsg_month:
            continue
        (same_dir if (fx_month[m] > 0) == (ihsg_month[m] > 0) else diff_dir).append(m)

    return {
        "meta": {"pair": "USD/IDR", "source": "Frankfurter (referensi ECB)",
                 "as_of": last, "points": len(dates)},
        "fig": {
            "first": {"r": lvl0, "d": f"{lvl0:,.0f}".replace(",", ".")},
            "first_date": first,
            "last": {"r": lvl1, "d": f"{lvl1:,.0f}".replace(",", ".")},
            "last_date": last,
            "change_pct": (lvl1 / lvl0 - 1) * 100,
            "peak": {"r": fx[hi_d], "d": f"{fx[hi_d]:,.0f}".replace(",", "."), "date": hi_d},
            "trough": {"r": fx[lo_d], "d": f"{fx[lo_d]:,.0f}".replace(",", "."), "date": lo_d},
            "corr": corr, "n_obs": len(rets),
            "oct_fx_pct": cur_fx,
            "oct_ihsg_pct": cur_ihsg,
            "month_label": LONG_MONTH[int(cur_month[5:7])],
        },
        "monthly": months,
        "series": [{"date": d, "rate": fx[d]} for d in dates],
        "monthly_ihsg": ihsg["monthly"],
        "same_dir": same_dir,
        "diff_dir": diff_dir,
    }


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="cetak keputusan, jangan tulis")
    ap.add_argument("--page", action="store_true", help="rebuild pasar/rupiah.html + verify")
    ap.add_argument("--push", action="store_true", help="commit + push setelah sukses")
    args = ap.parse_args()

    old = json.loads(PAYLOAD.read_text())
    old_as_of = old["meta"]["as_of"]
    log(f"tersimpan: {old_as_of} kurs {old['fig']['last']['r']}")

    fx = fetch_fx()
    ihsg = json.loads(IHSG.read_text())
    payload = build_payload(fx, ihsg)
    new_as_of = payload["meta"]["as_of"]
    log(f"terbaru  : {new_as_of} kurs {payload['fig']['last']['r']} "
        f"(korelasi {payload['fig']['corr']:+.3f}, {payload['fig']['n_obs']} hari)")

    if new_as_of <= old_as_of:
        log(f"tidak ada titik kurs baru (tersimpan {old_as_of}), tidak ada yang ditulis")
        return 0
    if args.dry_run:
        log("dry-run: tidak menulis")
        return 0

    PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    log(f"payload ditulis: {new_as_of} ({payload['meta']['points']} titik)")

    if args.page:
        page = subprocess.run([sys.executable, str(ROOT / "pasar" / "build_rupiah_page.py")],
                              cwd=ROOT, capture_output=True, text=True)
        print(page.stdout.strip())
        if page.returncode != 0:
            print(page.stderr, file=sys.stderr)
            log("FATAL: build_rupiah_page gagal")
            return 4
        ver = subprocess.run([sys.executable, str(ROOT / "pasar" / "verify_rupiah.py")],
                             cwd=ROOT, capture_output=True, text=True)
        print("\n".join(l for l in ver.stdout.splitlines() if l.startswith("[FAIL]")
                        or l.startswith("SEMUA") or l.startswith("GAGAL")))
        if ver.returncode != 0:
            print(ver.stdout[-2000:], file=sys.stderr)
            log("FATAL: verify_rupiah gagal, tidak push")
            return 5

    if args.push:
        # Ada penulis lain (GitHub Actions publish gempa tiap 06.30 WIB): majukan dulu,
        # jangan pernah menimpa riwayat orang.
        pull = git("pull", "--rebase", "--autostash", "origin", "main")
        if pull.returncode != 0:
            print(pull.stdout, pull.stderr, file=sys.stderr)
            log("FATAL: git pull --rebase gagal; tidak push (perlu dilihat manusia)")
            return 6
        msg = f"Rupiah harian: kurs {new_as_of} ({payload['fig']['last']['d']})"
        add = git("add", "data/pasar/rupiah.json", "pasar/rupiah.html")
        if add.returncode != 0:
            print(add.stderr, file=sys.stderr)
            log("FATAL: git add gagal")
            return 6
        staged = git("diff", "--cached", "--quiet")
        if staged.returncode == 0:
            log("tidak ada perubahan untuk di-commit")
        else:
            c = git("commit", "-m", msg)
            if c.returncode != 0:
                print(c.stdout, c.stderr, file=sys.stderr)
                log("FATAL: git commit gagal")
                return 6
            log(f"commit: {msg}")
        p = git("push", "origin", "HEAD:main")
        if p.returncode != 0:
            print(p.stdout, p.stderr, file=sys.stderr)
            log("FATAL: git push gagal")
            return 6
        log("push selesai")

    log("selesai")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
