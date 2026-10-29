#!/usr/bin/env python3
"""Harness untuk dua jalur yang gampang rusak diam-diam di refresh pasar.

A. Rollover bulan: payload kurs memakai label/return bulan berjalan. Dulu label "Oktober"
   ditulis mati di template dan kuncinya dihitung dengan bulan hardcode "2026-10", jadi
   tanggal 1 November halaman akan menampilkan angka November dengan label Oktober.
B. Urutan git: repo ini punya dua penulis (job IHSG 16.20 WIB dan GitHub Actions gempa 06.30 WIB).
   Kalau push dilakukan tanpa pull --rebase lebih dulu, push selalu ditolak "fetch first".

Jalankan: /usr/bin/python3 pasar/test_refresh_flows.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          env={**os.environ,
                               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


# ---------------------------------------------------------------- A. rollover bulan
def test_month_rollover() -> None:
    rupiah = load_module("refresh_rupiah", HERE / "refresh_rupiah.py")

    fx = {
        "2025-12-31": 16000, "2026-09-30": 17500, "2026-10-30": 18000,
        "2026-11-02": 18100, "2026-11-03": 18200,
    }
    ihsg = {
        "series": [
            {"date": "2025-12-31", "close": 8000}, {"date": "2026-09-30", "close": 7000},
            {"date": "2026-10-30", "close": 6900}, {"date": "2026-11-02", "close": 6990},
            {"date": "2026-11-03", "close": 7100},
        ],
        "monthly": [
            {"month": "2026-09", "ret": -7.0}, {"month": "2026-10", "ret": -1.5},
            {"month": "2026-11", "ret": +2.9},
        ],
    }
    payload = rupiah.build_payload(fx, ihsg)
    fig = payload["fig"]

    check(payload["meta"]["as_of"] == "2026-11-03", "as_of ikut titik kurs terakhir",
          payload["meta"]["as_of"])
    check(fig["month_label"] == "November", "label bulan berjalan ikut data", fig["month_label"])
    check(abs(fig["oct_fx_pct"] - (18200 / 18000 - 1) * 100) < 1e-9,
          "return kurs = bulan berjalan, bukan Oktober", f"{fig['oct_fx_pct']:.4f}%")
    check(abs(fig["oct_ihsg_pct"] - (7100 / 6900 - 1) * 100) < 1e-9,
          "return IHSG = jendela bulan berjalan", f"{fig['oct_ihsg_pct']:.4f}%")
    check(payload["same_dir"] == ["2026-11"] and payload["diff_dir"] == ["2026-09", "2026-10"],
          "arah bulanan dihitung dari bulan yang ada di dua sumber",
          f"same={payload['same_dir']} diff={payload['diff_dir']}")
    check(fig["peak"]["date"] == "2026-11-03" and fig["trough"]["date"] == "2025-12-31",
          "puncak/dasar dari seri kurs", f"{fig['peak']['date']} / {fig['trough']['date']}")

    # Kontrol negatif: seri IHSG yang belum punya sesi bulan berjalan harus berteriak, bukan
    # diam-diam memakai angka bulan lalu.
    try:
        rupiah.build_payload({"2025-12-31": 16000, "2026-11-03": 18200},
                             {"series": [{"date": "2025-12-31", "close": 8000}], "monthly": []})
    except SystemExit as exc:
        check("FATAL" in str(exc), "bulan tanpa sesi IHSG ditolak", str(exc)[:60])
    else:
        check(False, "bulan tanpa sesi IHSG ditolak", "tidak ada SystemExit")


# ---------------------------------------------------------------- B. urutan git
def make_pair(tmp: Path) -> tuple[Path, Path, Path]:
    bare = tmp / "origin.git"
    git(tmp, "init", "--bare", "-b", "main", str(bare))
    up = tmp / "upstream"
    git(tmp, "clone", str(bare), str(up))
    (up / "a.txt").write_text("base\n")
    git(up, "add", "a.txt")
    git(up, "commit", "-m", "base")
    git(up, "push", "origin", "HEAD:main")
    local = tmp / "local"
    git(tmp, "clone", str(bare), str(local))
    return bare, up, local


def commits(repo: Path) -> list[str]:
    out = git(repo, "log", "--format=%s", "origin/main")
    return out.stdout.splitlines()


def test_push_flow() -> None:
    ihsg = load_module("refresh_ihsg", HERE / "refresh_ihsg.py")

    with tempfile.TemporaryDirectory() as td:
        bare, up, local = make_pair(Path(td))
        # GitHub Actions menulis duluan...
        (up / "gempa.txt").write_text("quake\n")
        git(up, "add", "gempa.txt")
        git(up, "commit", "-m", "Append gempa baru")
        git(up, "push", "origin", "HEAD:main")

        # ...lalu job IHSG jalan dari clone lokal yang masih ketinggalan 1 commit.
        (local / "ihsg.txt").write_text("session\n")
        git(local, "add", "ihsg.txt")
        git(local, "commit", "-m", "IHSG harian")

        setattr(ihsg, "ROOT", local)  # modul memakai ROOT global untuk semua perintah git
        pull = ihsg.git("pull", "--rebase", "--autostash", "origin", "main")
        check(pull.returncode == 0, "pull --rebase lolos walau origin bergerak", pull.stderr[:60])
        push = ihsg.git("push", "origin", "HEAD:main")
        check(push.returncode == 0, "push lolos setelah rebase", push.stderr[:60])
        log = commits(local)
        check(log == ["IHSG harian", "Append gempa baru", "base"], "riwayat linier, dua penulis utuh",
              str(log))

    # Kontrol negatif: tanpa pull, push harus ditolak. Kalau kontrol ini hijau juga, berarti
    # test di atas tidak membuktikan apa-apa.
    with tempfile.TemporaryDirectory() as td:
        bare, up, local = make_pair(Path(td))
        (up / "gempa.txt").write_text("quake\n")
        git(up, "add", "gempa.txt")
        git(up, "commit", "-m", "Append gempa baru")
        git(up, "push", "origin", "HEAD:main")
        (local / "ihsg.txt").write_text("session\n")
        git(local, "add", "ihsg.txt")
        git(local, "commit", "-m", "IHSG harian")
        push = git(local, "push", "origin", "HEAD:main")
        check(push.returncode != 0 and "rejected" in (push.stderr + push.stdout),
              "tanpa pull, push ditolak (bug yang dicegah)", (push.stderr.strip().splitlines() or [""])[0][:70])


def main() -> int:
    test_month_rollover()
    test_push_flow()
    print()
    if failures:
        print(f"GAGAL {len(failures)} cek: {failures}")
        return 1
    print("SEMUA CEK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
