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


# Tata bahasa grafik hub (dipakai juga oleh renderPisa/renderExplorer di hub.template.html):
# kanvas 384x216 (16:9), area plot x 9.6..268.8 / y 19.2..192, kolom nilai rata kanan di x 374.4, label 12px mono.
# Seri utama = warna aksen pelat; pembanding = garis tipis tinta (tick atau garis putus-putus).
W, H = 384.0, 216.0
PL, PR, PT, PB = 9.6, 268.8, 19.2, 192.0
VX = 374.4
TICK_Y = 208.8


def _spread(ys: list[float], gap: float = 14.4) -> list[float]:
    """Geser label kolom nilai supaya tidak bertumpuk, urutan vertikalnya tetap."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a_, b_ in zip(order, order[1:]):
        if out[b_] - out[a_] < gap:
            out[b_] = out[a_] + gap
    lo_clip = max(0.0, max(out) - (PB + 4.8))
    return [min(max(y - lo_clip, PT), PB + 4.8) for y in out]


def _line_svg(points: list[float], *, ref_idx: int, ref_label: str, last_label: str,
              tick_left: str, tick_right: str, figs: tuple[str, str, str]) -> str:
    """Grafik garis: seri aksen, garis acuan putus-putus di titik ref_idx, dua nilai di kolom kanan.

    Satu warna saja (var(--accent-pasar)): warna menandai bagian halaman, bukan arah angka.
    figs = kunci payload untuk (label acuan, label terakhir, tick kanan), dipasang sebagai data-fig.
    """
    if len(points) < 2:
        raise RuntimeError("deret pasar terlalu pendek untuk digambar")
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    step = (PR - PL) / (len(points) - 1)

    def xy(i: int, v: float) -> tuple[float, float]:
        return PL + step * i, PT + (1 - (v - lo) / span) * (PB - PT)

    coords = [xy(i, v) for i, v in enumerate(points)]
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    rx, ry = coords[ref_idx]
    lx, ly = coords[-1]
    ref_ty, last_ty = _spread([ry + 4.2, ly + 4.2])
    f_ref, f_last, f_tick = figs
    return "".join([
        '<svg class="window-fig" viewBox="0 0 384 216" aria-hidden="true" focusable="false">',
        f'<line class="fig-ref" x1="{PL:.1f}" y1="{ry:.1f}" x2="{PR + 4.8:.1f}" y2="{ry:.1f}"/>',
        f'<path class="fig-line" d="{d}"/>',
        f'<circle class="fig-dot-ref" cx="{rx:.1f}" cy="{ry:.1f}" r="2.9"/>',
        f'<circle class="fig-dot" cx="{lx:.1f}" cy="{ly:.1f}" r="3.6"/>',
        f'<text x="{VX:.1f}" y="{ref_ty:.1f}" class="fig-label" text-anchor="end" data-fig="{f_ref}">{ref_label}</text>',
        f'<text x="{VX:.1f}" y="{last_ty:.1f}" class="fig-val" text-anchor="end" data-fig="{f_last}">{last_label}</text>',
        f'<text x="{PL:.1f}" y="{TICK_Y:.1f}" class="fig-label" text-anchor="start">{tick_left}</text>',
        f'<text x="{PR:.1f}" y="{TICK_Y:.1f}" class="fig-label" text-anchor="end"{f' data-fig="{f_tick}"' if f_tick else ""}>{tick_right}</text>',
        "</svg>",
    ])


def _bar_svg(rows: list[tuple[str, float]], note: str) -> str:
    """Batang horizontal: nama di atas batang, jalur skala 0..max dibulatkan ke 10, nilai di kolom kanan."""
    scale = max(10.0, -(-max(v for _, v in rows) // 10) * 10)
    pitch = (PB - PT) / len(rows)
    off = max(0.0, (pitch - 32.4) / 2)
    parts = ['<svg class="window-fig" viewBox="0 0 384 216" aria-hidden="true" focusable="false">']
    for i, (name, val) in enumerate(rows):
        top = PT + i * pitch + off
        by = top + 18
        bw = max(1.5, (PR - PL) * (val / scale))
        parts.append(f'<text x="{PL:.1f}" y="{top + 12:.1f}" class="fig-label" text-anchor="start">{name}</text>')
        parts.append(f'<rect class="fig-track" x="{PL:.1f}" y="{by:.1f}" width="{PR - PL:.1f}" height="12"/>')
        parts.append(f'<rect class="fig-bar" x="{PL:.1f}" y="{by:.1f}" width="{bw:.1f}" height="12"/>')
        parts.append(f'<text x="{VX:.1f}" y="{by + 10.2:.1f}" class="fig-val" text-anchor="end">{fmt_id(val, 2)}%</text>')
    parts.append(f'<text x="{PL:.1f}" y="{TICK_Y:.1f}" class="fig-label" text-anchor="start">{note}</text>')
    parts.append(f'<text x="{PR:.1f}" y="{TICK_Y:.1f}" class="fig-label" text-anchor="end">0-{int(scale)}%</text>')
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

    svg = {
        "pasar_ihsg": _line_svg(
            ihsg_series,
            ref_idx=peak_idx,
            ref_label=ihsg_fig["peak_value"]["d"],
            last_label=ihsg_fig["last_value"]["d"],
            tick_left=short_date(ihsg["series"][0]["date"]),
            tick_right=short_date(ihsg_as_of),
            figs=("pasar_ihsg_peak", "pasar_ihsg_last", "pasar_ihsg_asof_short"),
        ),
        "pasar_rupiah": _line_svg(
            rupiah_series,
            ref_idx=0,
            ref_label=rupiah_fig["first"]["d"],
            last_label=rupiah_fig["last"]["d"],
            tick_left=short_date(rupiah["series"][0]["date"]),
            tick_right=short_date(rupiah_as_of),
            figs=("pasar_rupiah_first", "pasar_rupiah_last", ""),
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

    # Pelat 07 (Ekonomi): PDB TW III dan Sakernas Agustus 2026, payload statis dari BRS BPS.
    eko = _load(root, "data/ekonomi/pdb_q3_2026.json")
    ef, eko_as_of = eko["fig"], eko["meta"]["as_of"]
    for k, v in {
        "eko_yoy": (ef["yoy"], fmt_id(ef["yoy"], 2) + "%"),
        "eko_tpt": (ef["tpt_26"], fmt_id(ef["tpt_26"], 2) + "%"),
        "eko_muda_25": (ef["muda_25"], fmt_id(ef["muda_25"], 2) + "%"),
        "eko_muda_26": (ef["muda_26"], fmt_id(ef["muda_26"], 2) + "%"),
        "eko_asof_long": (eko_as_of, long_date(eko_as_of)),
    }.items():
        fig[k] = {"r": v[0], "d": v[1]}
    svg["ekonomi_pdb"] = _bar_svg(
        [("Konsumsi pemerintah", ef["pem_q3"]), ("Investasi (PMTB)", ef["pmtb_q3"]),
         ("PDB", ef["yoy"]), ("Rumah tangga", ef["rt_q3"])],
        note="tumbuh y-on-y, TW III 2026",
    )
    lastmod["https://dashboard.idzharulhuda.com/ekonomi/pdb.html"] = eko_as_of

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
            "as_of": {name: p["meta"]["as_of"] for name, p in (("ihsg", ihsg), ("rupiah", rupiah), ("msci", msci), ("ekonomi", eko))}}
