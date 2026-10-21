#!/usr/bin/env python3
"""refresh_ihsg.py - append the latest IHSG session, rebuild payload + page, optionally push.

Dipakai cron harian (Sen-Jum 16.20 WIB). Rp0, tanpa LLM.

Alur:
  1. baca payload terakhir (data/pasar/ihsg.json)
  2. ambil quote IDX:COMPOSITE dari TradingView scanner
  3. validasi silang: prev_close turunan (close - change_abs) harus sama dengan close terakhir
     yang tersimpan; kalau beda, berhenti (jangan menulis data yang tidak konsisten)
  4. kalau quote bukan sesi baru (libur / pasar datar), tidak menulis apa pun
  5. kalau sesi baru: append ke data/pasar/source/ihsg-appended.jsonl, rebuild payload
  6. --page: rebuild pasar/ihsg.html + verify; --push: commit + push

Contoh:
  python3 pasar/refresh_ihsg.py --dry-run
  python3 pasar/refresh_ihsg.py --page --push
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "data" / "pasar" / "ihsg.json"
APPEND = ROOT / "data" / "pasar" / "source" / "ihsg-appended.jsonl"
WIB = ZoneInfo("Asia/Jakarta")
TV_URL = "https://scanner.tradingview.com/indonesia/scan"
TV_BODY = json.dumps({
    "symbols": {"tickers": ["IDX:COMPOSITE"], "query": {"types": []}},
    "columns": ["close", "change", "change_abs", "open", "high", "low", "update_mode"],
}).encode()
MARKET_CLOSE = dtime(16, 0)


def update_sitemap(date_str: str) -> None:
    """Perbarui lastmod entri pasar/ihsg.html di sitemap."""
    path = ROOT / "sitemap.xml"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    entry = f"https://dashboard.idzharulhuda.com/pasar/ihsg.html"
    if entry not in text:
        return
    text = re.sub(
        r"(<loc>" + re.escape(entry) + r"</loc><lastmod>)[^<]*(</lastmod>)",
        r"\g<1>" + date_str + r"\g<2>",
        text,
    )
    path.write_text(text, encoding="utf-8")
    log(f"sitemap lastmod -> {date_str}")


def log(msg: str) -> None:
    print(f"[{datetime.now(WIB):%Y-%m-%d %H:%M:%S} WIB] {msg}")


def fetch_quote() -> dict:
    req = urllib.request.Request(
        TV_URL, data=TV_BODY,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        body = json.load(r)
    rows = body.get("data") or []
    if len(rows) != 1:
        raise SystemExit(f"FATAL: quote IDX:COMPOSITE tidak tunggal: {body}")
    close, change_pct, change_abs, open_, high, low, mode = rows[0]["d"]
    if close is None or change_abs is None:
        raise SystemExit(f"FATAL: quote tidak lengkap: {rows[0]['d']}")
    prev_close = round(float(close) - float(change_abs), 3)
    return {
        "close": round(float(close), 3),
        "change_pct": float(change_pct) if change_pct is not None else None,
        "change_abs": round(float(change_abs), 3),
        "open": open_, "high": high, "low": low,
        "update_mode": mode,
        "prev_close": prev_close,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="cetak keputusan, jangan tulis")
    ap.add_argument("--force", action="store_true", help="izinkan jalan sebelum bursa tutup")
    ap.add_argument("--page", action="store_true", help="rebuild pasar/ihsg.html + verify")
    ap.add_argument("--push", action="store_true", help="commit + push setelah sukses")
    args = ap.parse_args()

    now = datetime.now(WIB)
    if not args.force and now.time() < MARKET_CLOSE:
        log(f"belum tutup bursa ({now:%H:%M} < 16:00), tidak ada yang dikerjakan")
        return 0
    if now.weekday() >= 5 and not args.force:
        log("akhir pekan, tidak ada sesi")
        return 0

    if not PAYLOAD.exists():
        raise SystemExit(f"FATAL: payload belum ada: {PAYLOAD}")
    payload = json.loads(PAYLOAD.read_text())
    last_date = payload["fig"]["last_date"]["d"]
    last_close = payload["fig"]["last_value"]["r"]
    log(f"tersimpan: {last_date} close {last_close}")

    q = fetch_quote()
    log(f"quote   : close {q['close']} prev {q['prev_close']} ({q['change_pct']:+.2f}%) mode {q['update_mode']}")

    # idempotensi: kalau sesi ini sudah tercatat, jangan validasi prev_close
    # (prev_close quote = close sesi sebelumnya, sementara last_close = sesi terakhir)
    session_date = now.date().isoformat()
    if last_date >= session_date:
        if abs(q["close"] - last_close) < 0.005:
            log("sesi terakhir sudah tercatat, tidak ada yang ditulis")
            return 0
        log(f"TOLAK: payload sudah memuat {last_date} close {last_close}, "
            f"tapi quote {session_date} close {q['close']}. Cek manual.")
        return 2

    # validasi: perubahan harian harus dihitung dari close yang kita punya
    drift = abs(q["prev_close"] - last_close)
    scale = max(abs(last_close), 1.0)
    if drift > max(0.02 * (scale / 5000), 0.005) and drift / scale > 1e-6:
        log(f"TOLAK: prev_close turunan {q['prev_close']} beda {drift:.3f} poin dari close tersimpan {last_close}. "
            f"Kemungkinan sumber tidak sinkron atau ada sesi yang terlewat.")
        return 2
    if q["change_pct"] is not None and q["prev_close"]:
        implied = (q["close"] / q["prev_close"] - 1.0) * 100.0
        if abs(implied - q["change_pct"]) > 0.05:
            log(f"TOLAK: change {q['change_pct']:.2f}% tidak konsisten dengan close/prev ({implied:.2f}%)")
            return 2

    if q["change_abs"] == 0 and abs(q["close"] - last_close) < 1e-9:
        log("quote datar dan sama dengan close tersimpan: kemungkinan libur bursa, tidak menulis")
        return 0

    rec = {
        "date": session_date,
        "close": q["close"],
        "source": "tradingview:IDX:COMPOSITE",
        "prev_close": q["prev_close"],
        "method": "close - change_abs",
        "change_pct": q["change_pct"],
        "fetched_at": now.isoformat(timespec="seconds"),
    }
    log(f"sesi baru: {session_date} close {q['close']} ({q['change_pct']:+.2f}%)")
    if args.dry_run:
        log("dry-run: tidak menulis")
        return 0

    with APPEND.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    build = subprocess.run([sys.executable, str(ROOT / "pasar" / "build_ihsg.py")],
                           cwd=ROOT, capture_output=True, text=True)
    print(build.stdout.strip())
    if build.returncode != 0:
        print(build.stderr, file=sys.stderr)
        log("FATAL: build_ihsg gagal setelah append (berkas append TIDAK di-rollback, cek manual)")
        return 3

    if args.page:
        page = subprocess.run([sys.executable, str(ROOT / "pasar" / "build_ihsg_page.py")],
                              cwd=ROOT, capture_output=True, text=True)
        print(page.stdout.strip())
        if page.returncode != 0:
            print(page.stderr, file=sys.stderr)
            return 4
        ver = subprocess.run([sys.executable, str(ROOT / "pasar" / "verify_ihsg.py")],
                             cwd=ROOT, capture_output=True, text=True)
        tail = [l for l in ver.stdout.splitlines() if l.startswith(("[PASS]", "[FAIL]", "==="))]
        print("\n".join(tail[-24:]))
        if ver.returncode != 0:
            print(ver.stdout[-2000:], file=sys.stderr)
            log("FATAL: verify_ihsg gagal, tidak push")
            return 5

    if args.push:
        update_sitemap(session_date)
        msg = f"IHSG harian: tambah sesi {session_date} ({q['change_pct']:+.2f}%)"
        subprocess.run(["git", "add", "data/pasar", "pasar/ihsg.html", "sitemap.xml"], cwd=ROOT)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
        cmds = [["git", "push", "origin", "HEAD"]] if staged.returncode == 0 else \
               [["git", "commit", "-m", msg], ["git", "push", "origin", "HEAD"]]
        if staged.returncode == 0:
            log("tidak ada perubahan untuk di-commit")
        for cmd in cmds:
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout, r.stderr, file=sys.stderr)
                log(f"FATAL: {' '.join(cmd)} gagal")
                return 6
        log("push selesai")

    log("selesai")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
