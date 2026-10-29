#!/usr/bin/env python3
"""Pelat pasar (04 IHSG, 05 rupiah, 06 MSCI) untuk hub.

Modul ini memegang satu pekerjaan: mengubah tiga muatan pasar yang sudah jadi
(`data/pasar/ihsg.json`, `rupiah.json`, `msci.json`) menjadi bagian hub tanpa satu pun
angka atau koordinat yang diketik tangan.

- `fig`      angka display untuk `span[data-fig]`, formatnya dari payload (bukan dari template)
- `svg`      tiga gambar SVG statis, dihitung dari deret payload, jadi tetap terbaca tanpa JS
- `lastmod`  tanggal `as_of` per halaman, dipakai sitemap
- `inject`   menempelkan SVG ke placeholder `<!--FIG:...-->` di hub.template.html
"""

from __future__ import annotations

import importlib.util
import json
import os

# Satu sumber format tanggal: pasar/dateline.py dipakai halaman pasar, hub, dan builder gempa.
_spec = importlib.util.spec_from_file_location(
    "pasar_dateline",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pasar", "dateline.py"),
)
assert _spec and _spec.loader, "pasar/dateline.py tidak bisa dimuat"
_dateline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dateline)
long_date = _dateline.long_date
short_date = _dateline.short_date


def stamp(html: str, iso: str) -> str:
    """Selaraskan strip "Data per <tanggal>" dengan tanggal data yang ditampilkan."""
    return _dateline.stamp(html, iso)

PAGES = (
    ("pasar_ihsg", "pasar/ihsg.html", "data/pasar/ihsg.json"),
    ("pasar_rupiah", "pasar/rupiah.html", "data/pasar/rupiah.json"),
    ("pasar_msci", "pasar/msci.html", "data/pasar/msci.json"),
)


def fmt_id(v: float, dp: int = 2) -> str:
    """1234.5 -> '1.234,50' (titik ribuan, koma desimal)."""
    s = f"{float(v):,.{dp}f}"
    return s.replace(",", "\x01").replace(".", ",").replace("\x01", ".")


def _load(root: str, rel: str) -> dict:
    path = os.path.join(root, rel)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _png_num(v: float, dp: int = 0) -> str:
    return fmt_id(v, dp)


def _line_svg(points: list[float], *, label_hi: str, label_lo: str, tick_left: str, tick_right: str,
              mark_hi: int | None = None, mark_last: bool = True) -> str:
    """Grafik garis 320x180 dengan label nilai di ujung dan dua penanda waktu.

    Satu warna saja (var(--accent-pasar)): warna di sini menandai bagian halaman, bukan
    arah angka. Arah dibaca dari posisi garis, bukan dari hijau/merah.
    """
    if len(points) < 2:
        raise RuntimeError("deret pasar terlalu pendek untuk digambar")
    w, h = 320.0, 180.0
    pad_l, pad_r, pad_t, pad_b = 12.0, 56.0, 16.0, 24.0
    lo = min(points)
    hi = max(points)
    span = (hi - lo) or 1.0
    inner_w = w - pad_l - pad_r
    inner_h = h - pad_t - pad_b
    step = inner_w / (len(points) - 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        x = pad_l + step * i
        y = pad_t + (1 - (v - lo) / span) * inner_h
        return x, y

    coords = [xy(i, v) for i, v in enumerate(points)]
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in coords)

    parts = [
        '<svg class="window-fig" viewBox="0 0 320 180" aria-hidden="true" focusable="false">',
        '<path d="' + d + '" fill="none" stroke="var(--accent-pasar)" stroke-width="1.6" '
        'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>',
    ]
    if mark_hi is not None:
        hx, hy = coords[mark_hi]
        parts.append(
            f'<line x1="{pad_l:.1f}" y1="{hy:.1f}" x2="{w - pad_r + 4:.1f}" y2="{hy:.1f}" '
            'stroke="var(--line)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>'
        )
        parts.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="2.4" fill="var(--accent-pasar)"/>')
    if mark_last:
        lx, ly = coords[-1]
        parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="var(--accent-pasar)"/>')

    parts.append(
        f'<text x="{pad_l:.1f}" y="11" class="fig-label" text-anchor="start">{label_hi}</text>'
    )
    parts.append(
        f'<text x="{pad_l:.1f}" y="{h - 7:.1f}" class="fig-label" text-anchor="start">{label_lo}</text>'
    )
    parts.append(
        f'<text x="{w - pad_r + 4:.1f}" y="{h - 7:.1f}" class="fig-label" text-anchor="end">{tick_right}</text>'
    )
    parts.append(
        f'<text x="{pad_l:.1f}" y="{h - 7:.1f}" class="fig-label" text-anchor="start" opacity="0">{tick_left}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _bar_svg(rows: list[tuple[str, float]], note: str) -> str:
    """Lima batang bobot terbesar plus catatan jumlah sisanya."""
    w, h = 320.0, 180.0
    label_w = 132.0
    pad_t = 12.0
    bar_h = 9.0
    gap = 8.0
    max_v = max(v for _, v in rows) or 1.0
    bar_space = w - label_w - 46.0
    parts = [
        '<svg class="window-fig" viewBox="0 0 320 180" aria-hidden="true" focusable="false">',
    ]
    y = pad_t
    for name, val in rows:
        bw = max(2.0, bar_space * (val / max_v))
        parts.append(
            f'<text x="0" y="{y + bar_h:.1f}" class="fig-label" text-anchor="start">{name}</text>'
        )
        parts.append(
            f'<rect x="{label_w:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bar_h}" fill="var(--accent-pasar)" opacity="0.85"/>'
        )
        parts.append(
            f'<text x="{label_w + bw + 5:.1f}" y="{y + bar_h:.1f}" class="fig-label" text-anchor="start">{fmt_id(val, 2)}%</text>'
        )
        y += bar_h + gap
    parts.append(
        f'<text x="0" y="{h - 6:.1f}" class="fig-label" text-anchor="start">{note}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def load(root: str) -> dict:
    ihsg = _load(root, "data/pasar/ihsg.json")
    rupiah = _load(root, "data/pasar/rupiah.json")
    msci = _load(root, "data/pasar/msci.json")

    for name, payload in (("ihsg", ihsg), ("rupiah", rupiah), ("msci", msci)):
        as_of = (payload.get("meta") or {}).get("as_of")
        if not as_of or len(as_of) != 10:
            raise RuntimeError(f"data/pasar/{name}.json tanpa meta.as_of yang sah: {as_of!r}")

    ihsg_fig = ihsg["fig"]
    rupiah_fig = rupiah["fig"]
    ihsg_as_of, rupiah_as_of, msci_as_of = ihsg["meta"]["as_of"], rupiah["meta"]["as_of"], msci["meta"]["as_of"]

    sectors = msci.get("sectors") or []
    fin = next((s for s in sectors if s.get("sector") == "Financials"), None)
    if fin is None:
        raise RuntimeError("data/pasar/msci.json tanpa sektor Financials")

    constituents = sorted(msci["constituents"], key=lambda c: c["weight_pct"], reverse=True)
    if not constituents:
        raise RuntimeError("data/pasar/msci.json tanpa konstituen")

    corr = float(rupiah_fig["corr"])
    fig = {
        "pasar_ihsg_last": {"r": ihsg_fig["last_value"]["r"], "d": ihsg_fig["last_value"]["d"]},
        "pasar_ihsg_peak": {"r": ihsg_fig["peak_value"]["r"], "d": ihsg_fig["peak_value"]["d"]},
        "pasar_ihsg_drawdown": {"r": ihsg_fig["drawdown_pct"]["r"], "d": ihsg_fig["drawdown_pct"]["d"]},
        "pasar_ihsg_trough": {"r": ihsg_fig["trough_value"]["r"], "d": ihsg_fig["trough_value"]["d"]},
        "pasar_ihsg_trough_dd": {"r": ihsg_fig["trough_drawdown_pct"]["r"], "d": ihsg_fig["trough_drawdown_pct"]["d"]},
        "pasar_ihsg_rebound": {"r": ihsg_fig["since_trough_pct"]["r"], "d": ihsg_fig["since_trough_pct"]["d"]},
        "pasar_rupiah_first": {"r": rupiah_fig["first"]["r"], "d": rupiah_fig["first"]["d"]},
        "pasar_rupiah_last": {"r": rupiah_fig["last"]["r"], "d": rupiah_fig["last"]["d"]},
        "pasar_rupiah_peak": {"r": rupiah_fig["peak"]["r"], "d": rupiah_fig["peak"]["d"]},
        "pasar_rupiah_corr": {"r": corr, "d": fmt_id(corr, 3)},
        "pasar_msci_names": {"r": len(constituents), "d": str(len(constituents))},
        "pasar_msci_coverage": {"r": msci["meta"]["coverage_pct"], "d": f"{msci['meta']['coverage_pct']}%"},
        "pasar_msci_float": {"r": msci["meta"]["index_float_cap_bn"], "d": fmt_id(msci["meta"]["index_float_cap_bn"], 2)},
        "pasar_msci_fin": {"r": fin["weight_pct"], "d": fmt_id(fin["weight_pct"], 2) + "%"},
        "pasar_msci_top5": {"r": round(sum(c["weight_pct"] for c in constituents[:5]), 2),
                            "d": fmt_id(sum(c["weight_pct"] for c in constituents[:5]), 2) + "%"},
        "pasar_msci_bca": {"r": constituents[0]["weight_pct"], "d": fmt_id(constituents[0]["weight_pct"], 2) + "%"},
        # Tanggal dan jumlah yang tampil di kartu hub. Dulu semuanya ditulis mati di template,
        # jadi kartu mengaku "Terakhir 21 Okt" sambil menampilkan angka 29 Okt.
        "pasar_ihsg_asof_short": {"r": ihsg_as_of, "d": short_date(ihsg_as_of)},
        "pasar_ihsg_asof_long": {"r": ihsg_as_of, "d": long_date(ihsg_as_of)},
        "pasar_ihsg_sessions": {"r": len(ihsg["series"]), "d": str(len(ihsg["series"]))},
        "pasar_rupiah_asof_long": {"r": rupiah_as_of, "d": long_date(rupiah_as_of)},
        "pasar_rupiah_points": {"r": len(rupiah["series"]), "d": str(len(rupiah["series"]))},
        "pasar_rupiah_obs": {"r": int(rupiah_fig["n_obs"]), "d": str(int(rupiah_fig["n_obs"]))},
        "pasar_msci_asof_long": {"r": msci_as_of, "d": long_date(msci_as_of)},
    }

    ihsg_series = [row["close"] for row in ihsg["series"]]
    peak_idx = max(range(len(ihsg_series)), key=lambda i: ihsg_series[i])
    rupiah_series = [row["rate"] for row in rupiah["series"]]
    rupiah_peak_idx = max(range(len(rupiah_series)), key=lambda i: rupiah_series[i])

    svg = {
        "pasar_ihsg": _line_svg(
            ihsg_series,
            label_hi=ihsg_fig["peak_value"]["d"],
            label_lo=ihsg_fig["last_value"]["d"],
            tick_left="2 Jan",
            tick_right=short_date(ihsg_as_of),
            mark_hi=peak_idx,
        ),
        "pasar_rupiah": _line_svg(
            rupiah_series,
            label_hi=rupiah_fig["last"]["d"],
            label_lo=rupiah_fig["first"]["d"],
            tick_left="31 Des",
            tick_right=short_date(rupiah_as_of),
            mark_hi=rupiah_peak_idx,
        ),
        "pasar_msci": _bar_svg(
            [(c["name"], c["weight_pct"]) for c in constituents[:5]],
            note=f"{len(constituents) - 5} emiten lain mengisi sisa bobot",
        ),
    }

    lastmod = {
        f"https://dashboard.idzharulhuda.com/{rel}": payload["meta"]["as_of"]
        for (_, rel, _), payload in zip(PAGES, (ihsg, rupiah, msci))
    }

    def inject(html: str) -> str:
        for key, markup in svg.items():
            marker = f"<!--FIG:{key}-->"
            if marker not in html:
                raise RuntimeError(f"placeholder {marker} tidak ada di hub.template.html")
            html = html.replace(marker, markup)
        leftover = html.count("<!--FIG:")
        if leftover:
            raise RuntimeError(f"masih ada {leftover} placeholder <!--FIG:...--> yang belum terisi")
        return html

    return {"fig": fig, "svg": svg, "lastmod": lastmod, "inject": inject,
            "as_of": {name: p["meta"]["as_of"] for name, p in (("ihsg", ihsg), ("rupiah", rupiah), ("msci", msci))}}
