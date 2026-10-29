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


def refresh_hub() -> int:
    """Bangun ulang hub index.html + sitemap.xml + gambar OG supaya stamp dan lastmod ikut data
    terbaru.

    sitemap.xml dan gambar OG di-generate oleh pipeline_hub/build_hub.py + pipeline/build_og.py;
    menambal satu baris lastmod di sini akan bentrok tiap kali rebase dan bikin lastmod basi untuk
    halaman lain.
    """
    build = subprocess.run([sys.executable, str(ROOT / "pipeline_hub" / "build_hub.py")],
                           cwd=ROOT, capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stdout, build.stderr, file=sys.stderr)
        log("FATAL: build_hub gagal; hub + sitemap tidak diperbarui")
        return 7
    og = subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_og.py")],
                        cwd=ROOT, capture_output=True, text=True)
    if og.returncode != 0:
        print(og.stdout[-2000:], og.stderr[-2000:], file=sys.stderr)
        log(f"FATAL: build_og gagal (exit {og.returncode}); gambar OG basi")
        return 7
    ver = subprocess.run([sys.executable, str(ROOT / "pipeline_hub" / "verify.py")],
                         cwd=ROOT, capture_output=True, text=True)
    tail = [l for l in ver.stdout.splitlines() if l.startswith(("FAIL", "TOTAL"))]
    log("hub: " + (" | ".join(tail) if tail else f"verify exit {ver.returncode}"))
    if ver.returncode != 0:
        print("\n".join(tail), file=sys.stderr)
        log("FATAL: verify hub gagal setelah build")
        return 7
    return 0


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


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
        # Ada penulis lain di repo yang sama (GitHub Actions publish gempa tiap 06.30 WIB).
        # Majukan dulu, jangan pernah menimpa riwayat orang; konflik = manusia yang lihat.
        pull = git("pull", "--rebase", "--autostash", "origin", "main")
        if pull.returncode != 0:
            print(pull.stdout, pull.stderr, file=sys.stderr)
            log("FATAL: git pull --rebase gagal; tidak push")
            return 6

        rc_hub = refresh_hub()

        msg = f"IHSG harian: tambah sesi {session_date} ({q['change_pct']:+.2f}%)"
        git("add", "data/pasar/ihsg.json", "data/pasar/source/ihsg-appended.jsonl",
            "pasar/ihsg.html", "index.html", "sitemap.xml")
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
        if rc_hub != 0:
            return rc_hub

    log("selesai")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
