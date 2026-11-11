#!/usr/bin/env python3
"""refresh.py - pembaruan otomatis halaman karhutla. Dipakai cron no_agent 3x sehari (Rp0, tanpa LLM).

Alur: tarik ulang 4 hari WIB terakhir dari SiPongi+ -> bangun payload -> bangun halaman -> gate penuh
(angka + browser) -> pull --rebase -> commit + push hanya kalau gate lolos dan ada data baru.
Gagal di langkah mana pun -> exit != 0 (cron memberi notifikasi), halaman publik tidak tersentuh.

  python3 karhutla/refresh.py            # bangun + verify lokal saja
  python3 karhutla/refresh.py --push     # + commit + push
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "data" / "karhutla" / "karhutla.json"
WIB = dt.timezone(dt.timedelta(hours=7))


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(WIB):%Y-%m-%d %H:%M:%S} WIB] {msg}", flush=True)


def run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), cwd=ROOT, capture_output=True, text=True)


def step(name: str, *cmd: str, code: int) -> None:
    r = run(*cmd)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    log(f"{name}: {tail}")
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:], file=sys.stderr)
        log(f"FATAL: {name} gagal (exit {r.returncode}); tidak publish")
        raise SystemExit(code)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--days", type=int, default=4)
    a = ap.parse_args()

    old = json.loads(PAYLOAD.read_text())["meta"]["newest_utc"] if PAYLOAD.exists() else ""
    py = sys.executable
    step("harvest", py, "karhutla/harvest.py", "--recent", str(a.days), code=2)
    step("payload", py, "karhutla/build_data.py", code=3)
    step("halaman", py, "karhutla/build_page.py", code=4)
    step("verify", py, "karhutla/verify.py", code=5)
    meta = json.loads(PAYLOAD.read_text())["meta"]
    log(f"titik terbaru {meta['newest_utc']}Z (sebelumnya {old or '-'})")
    if not a.push:
        return 0
    pull = run("git", "pull", "--rebase", "--autostash", "origin", "main")
    if pull.returncode != 0:
        print(pull.stdout, pull.stderr, file=sys.stderr)
        log("FATAL: git pull --rebase gagal; tidak push")
        return 6
    run("git", "add", "data/karhutla/karhutla.json", "karhutla/index.html")
    if run("git", "diff", "--cached", "--quiet").returncode == 0:
        log("tidak ada perubahan, tidak commit")
        return 0
    msg = f"Karhutla: data per {meta['as_of']} {meta['as_of_time']} WIB"
    c = run("git", "commit", "-m", msg)
    if c.returncode != 0:
        print(c.stdout, c.stderr, file=sys.stderr)
        log("FATAL: git commit gagal")
        return 6
    p = run("git", "push", "origin", "HEAD:main")
    if p.returncode != 0:
        print(p.stdout, p.stderr, file=sys.stderr)
        log("FATAL: git push gagal")
        return 6
    log(f"push: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
