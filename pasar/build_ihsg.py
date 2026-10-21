#!/usr/bin/env python3
"""build_ihsg.py - compile data/pasar/ihsg.json from raw sources.

Sumber:
  1. data/pasar/source/ihsg-daily.csv      - Refinitiv via Datawrapper 5srr7 (2 Jan - 19 Okt 2026)
  2. data/pasar/source/ihsg-appended.jsonl - sesi yang ditambahkan job harian (mulai 20 Okt 2026)

Tidak ada angka yang diketik di sini: semua figur dihitung dari dua berkas di atas.
Jalankan:  python3 pasar/build_ihsg.py [--quiet]
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_CSV = ROOT / "data" / "pasar" / "source" / "ihsg-daily.csv"
SRC_APPEND = ROOT / "data" / "pasar" / "source" / "ihsg-appended.jsonl"
SRC_FIX = ROOT / "data" / "pasar" / "source" / "ihsg-corrections.jsonl"
OUT = ROOT / "data" / "pasar" / "ihsg.json"

BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
         "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
BULAN_PENDEK = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
                "Jul", "Ags", "Sep", "Okt", "Nov", "Des"]


def num(x: float, dec: int = 2) -> str:
    """Format Indonesia: titik ribuan, koma desimal."""
    return f"{x:,.{dec}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def pct(x: float, dec: int = 2) -> str:
    return f"{x:,.{dec}f}".replace(",", "\0").replace(".", ",").replace("\0", ".") + "%"


def load() -> list[dict]:
    series: list[dict] = []
    with SRC_CSV.open(newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            series.append({"date": row["Date"], "close": float(row["IHSG"]), "src": "datawrapper"})
    if SRC_APPEND.exists():
        for line in SRC_APPEND.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            series.append({"date": rec["date"], "close": float(rec["close"]), "src": "append"})
    series.sort(key=lambda r: r["date"])

    # Koreksi penutupan yang salah di sumber (mis. baris terakhir Datawrapper
    # tertangkap intraday). Setiap koreksi wajib punya bukti + URL di berkasnya.
    if SRC_FIX.exists():
        fixes = {}
        for line in SRC_FIX.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                fixes[rec["date"]] = float(rec["close"])
        seen = set()
        for row in series:
            if row["date"] in fixes:
                row["close"] = fixes[row["date"]]
                row["src"] = "correction"
                seen.add(row["date"])
        missing = set(fixes) - seen
        assert not missing, f"koreksi untuk tanggal yang tidak ada di sumber: {sorted(missing)}"

    # integritas: tanggal unik dan naik ketat
    dates = [r["date"] for r in series]
    assert len(dates) == len(set(dates)), "tanggal ganda di sumber"
    assert dates == sorted(dates), "tanggal tidak urut"
    assert all(r["close"] > 0 for r in series), "ada close <= 0"
    for a, b in zip(series, series[1:]):
        assert b["date"] > a["date"], f"urutan tanggal rusak: {a['date']} -> {b['date']}"
    return series


def month_key(d: str) -> str:
    return d[:7]


def main() -> int:
    quiet = "--quiet" in sys.argv
    series = load()
    SRC_LAST = next(r for r in reversed(series) if r["src"] == "datawrapper")
    SRC_FIRST_APPEND = next((r for r in series if r["src"] == "append"), None)

    closes = [r["close"] for r in series]
    n = len(series)
    first, last = series[0], series[-1]

    # puncak & penurunan
    peak = max(series, key=lambda r: r["close"])
    assert closes.count(peak["close"]) == 1, "harga puncak muncul lebih dari sekali"
    trough = min(series, key=lambda r: r["close"])
    dd_peak = (last["close"] / peak["close"] - 1.0) * 100.0
    dd_trough = (trough["close"] / peak["close"] - 1.0) * 100.0
    since_trough = (last["close"] / trough["close"] - 1.0) * 100.0
    ytd = (last["close"] / first["close"] - 1.0) * 100.0
    need_recover = (peak["close"] / last["close"] - 1.0) * 100.0

    # return harian
    daily = [{"date": series[i]["date"], "close": series[i]["close"],
              "ret": (series[i]["close"] / series[i - 1]["close"] - 1.0) * 100.0}
             for i in range(1, n)]
    down = [d for d in daily if d["ret"] < 0]
    up = [d for d in daily if d["ret"] > 0]
    flat = [d for d in daily if d["ret"] == 0]
    worst = sorted(daily, key=lambda d: d["ret"])[:5]
    best = sorted(daily, key=lambda d: -d["ret"])[:5]

    # return bulanan: Januari diukur dari sesi pertama (tidak ada close Des 2025 di sumber)
    months: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in series:
        months.setdefault(month_key(r["date"]), []).append(r)
    monthly = []
    prev_end = None
    for mk, rows in months.items():
        end = rows[-1]["close"]
        if prev_end is None:
            base = rows[0]["close"]
            base_label = "sesi pertama"
        else:
            base = prev_end
            base_label = "bulan sebelumnya"
        mi = int(mk[5:7]) - 1
        monthly.append({
            "month": mk,
            "label": BULAN_PENDEK[mi],
            "label_long": BULAN[mi] + " " + mk[:4],
            "sessions": len(rows),
            "open_ref": rows[0]["close"],
            "end": end,
            "ret": (end / base - 1.0) * 100.0,
            "base": base,
            "base_label": base_label,
        })
        prev_end = end
    assert len(monthly) == 10, f"bulan terkumpul {len(monthly)}, harusnya 10"
    neg_months = [m for m in monthly if m["ret"] < 0]

    # Bulan berjalan (bulan terakhir di payload)
    cur_row = monthly[-1]

    # sesi terburuk/terbaik: sesi ke berapa di bulan itu
    def session_no(d: str) -> int:
        rows = months[month_key(d)]
        return next(i for i, r in enumerate(rows, 1) if r["date"] == d)

    for d in worst + best:
        d["session_no"] = session_no(d["date"])
        d["month_label"] = BULAN[int(d["date"][5:7]) - 1]
        d["day"] = int(d["date"][8:10])

    # jarak puncak -> titik terendah
    days_peak_to_trough = None
    idx_peak = next(i for i, r in enumerate(series) if r["date"] == peak["date"])
    idx_trough = next(i for i, r in enumerate(series) if r["date"] == trough["date"])
    if idx_trough > idx_peak:
        days_peak_to_trough = idx_trough - idx_peak

    fig = {
        "peak_value": {"r": peak["close"], "d": num(peak["close"])},
        "peak_date": {"d": peak["date"]},
        "peak_date_long": {"d": f"{int(peak['date'][8:10])} {BULAN[int(peak['date'][5:7]) - 1]} {peak['date'][:4]}"},
        "peak_session_no": {"r": idx_peak + 1, "d": str(idx_peak + 1)},
        "last_value": {"r": last["close"], "d": num(last["close"])},
        "last_date": {"d": last["date"]},
        "last_date_long": {"d": f"{int(last['date'][8:10])} {BULAN[int(last['date'][5:7]) - 1]} {last['date'][:4]}"},
        "trough_value": {"r": trough["close"], "d": num(trough["close"])},
        "trough_date_long": {"d": f"{int(trough['date'][8:10])} {BULAN[int(trough['date'][5:7]) - 1]} {trough['date'][:4]}"},
        "drawdown_pct": {"r": dd_peak, "d": pct(dd_peak)},
        "drawdown_abs": {"r": abs(dd_peak), "d": pct(abs(dd_peak))},
        "trough_drawdown_pct": {"r": dd_trough, "d": pct(dd_trough)},
        "since_trough_pct": {"r": since_trough, "d": pct(since_trough)},
        "ytd_pct": {"r": ytd, "d": pct(ytd)},
        "need_recover_pct": {"r": need_recover, "d": pct(need_recover)},
        "sessions": {"r": n, "d": str(n)},
        "sessions_with_change": {"r": len(daily), "d": str(len(daily))},
        "source_primary_until": {"d": f"{int(SRC_LAST['date'][8:10])} {BULAN[int(SRC_LAST['date'][5:7]) - 1]} {SRC_LAST['date'][:4]}"},
        "appended_from": {"d": f"{int(SRC_FIRST_APPEND['date'][8:10])} {BULAN[int(SRC_FIRST_APPEND['date'][5:7]) - 1]} {SRC_FIRST_APPEND['date'][:4]}" if SRC_FIRST_APPEND else "sesi sesudahnya"},
        "source_sha_short": {"d": hashlib.sha256(SRC_CSV.read_bytes()).hexdigest()[:16]},
        "down_days": {"r": len(down), "d": str(len(down))},
        "down_days_pct": {"r": len(down) / len(daily) * 100.0, "d": pct(len(down) / len(daily) * 100.0)},
        "up_days": {"r": len(up), "d": str(len(up))},
        "flat_days": {"r": len(flat), "d": str(len(flat))},
        "neg_months": {"r": len(neg_months), "d": str(len(neg_months))},
        "total_months": {"r": len(monthly), "d": str(len(monthly))},
        "cur_month_label": {"d": cur_row["label_long"]},
        "cur_ret_pct": {"r": cur_row["ret"], "d": pct(cur_row["ret"])},
        "cur_abs_pct": {"r": abs(cur_row["ret"]), "d": pct(abs(cur_row["ret"]))},
        "cur_sessions": {"r": cur_row["sessions"], "d": str(cur_row["sessions"])},
        "worst_day_ret": {"r": worst[0]["ret"], "d": pct(worst[0]["ret"])},
        "worst_day_abs": {"r": abs(worst[0]["ret"]), "d": pct(abs(worst[0]["ret"]))},
        "worst_day_date_long": {"d": f"{worst[0]['day']} {worst[0]['month_label']}"},
        "best_day_ret": {"r": best[0]["ret"], "d": pct(best[0]["ret"])},
        "best_day_date_long": {"d": f"{best[0]['day']} {best[0]['month_label']}"},
        "last_day_ret": {"r": daily[-1]["ret"], "d": pct(daily[-1]["ret"])},
        "last_day_ret_plain": {"r": daily[-1]["ret"], "d": pct(daily[-1]["ret"], 2).lstrip("+")},
    }

    payload = {
        "meta": {
            "index": "IHSG (Jakarta Composite Index)",
            "as_of": last["date"],
            "as_of_formatted": f"{int(last['date'][8:10])} {BULAN[int(last['date'][5:7]) - 1]} {last['date'][:4]}",
            "first_session": first["date"],
            "sessions": n,
            "source": "Refinitiv via Datawrapper 5srr7 (2 Jan - 19 Okt 2026); sesi setelahnya dari job harian (TradingView IDX:COMPOSITE)",
            "source_sha256": hashlib.sha256(SRC_CSV.read_bytes()).hexdigest(),
            "appended_sessions": sum(1 for r in series if r["src"] == "append"),
            "scope_note": "IHSG harian, harga penutupan. Bukan MSCI Indonesia Index.",
            "method_note": "return harian = close/close sebelumnya; return bulanan = close terakhir bulan lawan close terakhir bulan sebelumnya; Januari diukur dari sesi pertama 2 Jan 2026.",
            "days_peak_to_trough": days_peak_to_trough,
        },
        "fig": fig,
        "series": [{"date": r["date"], "close": r["close"]} for r in series],
        "monthly": [{k: v for k, v in m.items() if k != "base_label"} | {"base_label": m["base_label"]} for m in monthly],
        "worst": [{**d, "ret_d": pct(d["ret"])} for d in worst],
        "best": [{**d, "ret_d": pct(d["ret"])} for d in best],
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    if not quiet:
        print(f"payload  : {OUT.relative_to(ROOT)} ({OUT.stat().st_size} B)")
        print(f"sesi     : {n}  ({first['date']} -> {last['date']})")
        print(f"puncak   : {fig['peak_value']['d']} @ {fig['peak_date_long']['d']}")
        print(f"terakhir : {fig['last_value']['d']} @ {fig['last_date_long']['d']}")
        print(f"turun    : {fig['drawdown_pct']['d']} dari puncak; dasar {fig['trough_value']['d']} ({fig['trough_date_long']['d']})")
        print(f"sejak dasar: {fig['since_trough_pct']['d']}")
        print(f"hari turun : {fig['down_days']['d']} dari {fig['sessions_with_change']['d']} ({fig['down_days_pct']['d']})")
        print(f"bulan merah: {fig['neg_months']['d']} dari {fig['total_months']['d']}")
        print(f"bulan ini  : {fig['cur_month_label']['d']} {fig['cur_ret_pct']['d']} dalam {fig['cur_sessions']['d']} sesi")

        print("bulanan    : " + " ".join(f"{m['label']} {pct(m['ret'])}" for m in monthly))
        print("5 terburuk : " + " | ".join(f"{d['date']} {pct(d['ret'])}" for d in worst))
        print("5 terbaik  : " + " | ".join(f"{d['date']} {pct(d['ret'])}" for d in best))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
