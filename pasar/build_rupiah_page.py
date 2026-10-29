#!/usr/bin/env python3
"""build_rupiah_page.py - compile pasar/rupiah.html dari pasar/rupiah.template.html + data/pasar/rupiah.json.

Konvensi sama dengan build_ihsg_page.py: font inline base64, payload ditempel sebagai const DATA,
tiap slot data-fig diisi nilai tampilan supaya pembaca tanpa JavaScript tetap dapat angkanya.
Chart, bar, scatter, dan tabel lahir dari payload, bukan dari template.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import re

from dateline import stamp as dateline_stamp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASAR = ROOT / "pasar"
FONTS = ROOT / "fonts" / "pasar"
TEMPLATE = PASAR / "rupiah.template.html"
OUT = PASAR / "rupiah.html"
DATA = ROOT / "data" / "pasar" / "rupiah.json"

FONT_DEFS = [
    ("ArchivoBlack.ttf", "Archivo Black", "900", "truetype", "font/ttf"),
    ("ArchivoBlack.ttf", "Archivo", "900", "truetype", "font/ttf"),
    ("Archivo-var.woff2", "Archivo", "100 900", "woff2", "font/woff2"),
    ("IBMPlexMono-400.woff2", "IBM Plex Mono", "400", "woff2", "font/woff2"),
    ("IBMPlexMono-500.woff2", "IBM Plex Mono", "500", "woff2", "font/woff2"),
]

MON_S = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
         7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des"}
MON_L = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
         7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"}


def num(x: float, dec: int = 2) -> str:
    s = f"{x:,.{dec}f}"
    return s.replace(",", "#").replace(".", ",").replace("#", ".")


def pct(x: float, sign: bool = True) -> str:
    return (f"{x:+.2f}" if sign else f"{x:.2f}").replace(".", ",") + "%"


def long_date(iso: str) -> str:
    y, m, d = (int(v) for v in iso.split("-"))
    return f"{d} {MON_L[m]} {y}"


def short_date(iso: str) -> str:
    y, m, d = (int(v) for v in iso.split("-"))
    return f"{d} {MON_S[m]}"


def fonts_css() -> str:
    rules, cache = [], {}
    for filename, family, weight, fmt, mime in FONT_DEFS:
        if filename not in cache:
            path = FONTS / filename
            if not path.exists():
                sys.exit(f"FATAL: font tidak ada: {path}")
            cache[filename] = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            "@font-face {\n"
            f"  font-family: '{family}';\n"
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            "  font-display: swap;\n"
            f"  src: url('data:{mime};base64,{cache[filename]}') format('{fmt}');\n"
            "}"
        )
    return "\n".join(rules)


def chart_svg(series: list[dict], peak: dict, trough: dict) -> str:
    w, h, pad_l, pad_t, pad_b = 1056, 330, 8, 18, 8
    vals = [r["rate"] for r in series]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(vals)

    def x(i: int) -> float:
        return pad_l + i / (n - 1) * (w - pad_l * 2)

    def y(v: float) -> float:
        return pad_t + (1 - (v - lo) / span) * (h - pad_t - pad_b)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    area = f"{x(0):.1f},{h - pad_b:.1f} " + pts + f" {x(n - 1):.1f},{h - pad_b:.1f}"
    grid = "".join(
        f'<line x1="{pad_l}" y1="{y(lo + span * f):.1f}" x2="{w - pad_l}" y2="{y(lo + span * f):.1f}" '
        f'stroke="#DCCFB8" stroke-width="1" opacity="0.7"></line>'
        for f in (0.25, 0.5, 0.75)
    )
    pk_i = next((i for i, r in enumerate(series) if r["date"] == peak["date"]), n - 1)
    tr_i = next((i for i, r in enumerate(series) if r["date"] == trough["date"]), 0)
    marks = (
        f'<rect x="{x(pk_i) - 5:.1f}" y="{y(peak["r"]) - 5:.1f}" width="10" height="10" fill="#1A1410" '
        f'aria-hidden="true"></rect>'
        f'<rect x="{x(tr_i) - 5:.1f}" y="{y(trough["r"]) - 5:.1f}" width="10" height="10" fill="#1A1410" '
        f'aria-hidden="true"></rect>'
    )
    return (
        f'<svg class="chart-svg" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="Kurs USD/IDR harian, {num(vals[0], 0)} pada {series[0]["date"]} ke {num(vals[-1], 0)} pada {series[-1]["date"]}">'
        f'{grid}'
        f'<polygon points="{area}" fill="#F0E6D4" opacity="0.85"></polygon>'
        f'<polyline points="{pts}" fill="none" stroke="#2D4ECF" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"></polyline>'
        f'{marks}</svg>'
    )


def bars_html(months: list[dict]) -> str:
    chart = [m for m in months if m["month"] != "2025-12"]
    mx = max(abs(m["ret"]) for m in chart) or 1
    rows = []
    for m in chart:
        mon = MON_S[int(m["month"][5:7])]
        width = abs(m["ret"]) / mx * 46
        cls = "pos" if m["ret"] > 0 else "neg"
        rows.append(
            f'        <div class="bar-row">\n'
            f'          <div class="bar-m">{mon}</div>\n'
            f'          <div class="bar-track"><div class="bar-fill {cls}" style="width:{width:.1f}%"></div></div>\n'
            f'          <div class="bar-v {"" if m["ret"] > 0 else "neg"}">{pct(m["ret"])}</div>\n'
            f'        </div>'
        )
    return '      <div class="bars">\n' + "\n".join(rows) + "\n      </div>\n"


def scatter_svg(series: list[dict], ihsg: dict[str, float]) -> tuple[str, int]:
    pairs = []
    for a, b in zip(series, series[1:]):
        d0, d1 = a["date"], b["date"]
        c0, c1 = ihsg.get(d0), ihsg.get(d1)
        if c0 and c1:
            pairs.append(((b["rate"] / a["rate"] - 1) * 100, (c1 / c0 - 1) * 100))
    if not pairs:
        sys.exit("FATAL: tidak ada hari beririsan untuk scatter")
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    xm = max(abs(min(xs)), abs(max(xs)))
    ym = max(abs(min(ys)), abs(max(ys)))
    w, h, pad = 520, 420, 26

    def sx(v: float) -> float:
        return pad + (v + xm) / (2 * xm) * (w - pad * 2)

    def sy(v: float) -> float:
        return pad + (1 - (v + ym) / (2 * ym)) * (h - pad * 2)

    pts = "".join(
        f'<rect x="{sx(px) - 2.2:.1f}" y="{sy(py) - 2.2:.1f}" width="4.4" height="4.4" '
        f'fill="#2D4ECF" opacity="0.55"></rect>'
        for px, py in pairs
    )
    axes = (
        f'<line x1="{pad}" y1="{sy(0):.1f}" x2="{w - pad}" y2="{sy(0):.1f}" stroke="#1A1410" stroke-width="1" opacity="0.5"></line>'
        f'<line x1="{sx(0):.1f}" y1="{pad}" x2="{sx(0):.1f}" y2="{h - pad}" stroke="#1A1410" stroke-width="1" opacity="0.5"></line>'
        f'<text x="{w - pad}" y="{sy(0) - 8:.1f}" text-anchor="end" font-family="IBM Plex Mono, monospace" '
        f'font-size="11" fill="#6B5B4A">kurs (%)</text>'
        f'<text x="{sx(0) + 8:.1f}" y="{pad + 10}" font-family="IBM Plex Mono, monospace" '
        f'font-size="11" fill="#6B5B4A">IHSG (%)</text>'
    )
    return (
        f'<svg class="scatter-svg" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="Sebaran return harian kurs dan IHSG, {len(pairs)} hari">'
        f'{axes}{pts}</svg>',
        len(pairs),
    )


def table_html(payload: dict) -> str:
    fx = {m["month"]: m["ret"] for m in payload["monthly"] if m["month"] != "2025-12"}
    ihsg = {m["month"]: m["ret"] for m in payload["monthly_ihsg"]}
    rows = []
    for m in sorted(fx):
        if m not in ihsg:
            continue
        f, i = fx[m], ihsg[m]
        same = (f > 0) == (i > 0)
        rows.append(
            f'        <tr>\n'
            f'          <td>{MON_L[int(m[5:7])]}</td>\n'
            f'          <td class="{"muted" if f > 0 else ""}">{pct(f)}</td>\n'
            f'          <td class="{"muted" if i > 0 else ""}">{pct(i)}</td>\n'
            f'          <td class="muted">{"sama" if same else "berlawanan"}</td>\n'
            f'        </tr>'
        )
    return (
        '      <table class="pair-table">\n'
        '        <caption class="band-lead" style="text-align:left;caption-side:top;padding-bottom:8px">'
        'Arah bulanan, kurs lawan IHSG</caption>\n'
        '        <thead><tr><th scope="col">Bulan</th><th scope="col">USD/IDR</th>'
        '<th scope="col">IHSG</th><th scope="col">Arah</th></tr></thead>\n'
        '        <tbody>\n' + "\n".join(rows) + "\n        </tbody>\n      </table>\n"
    )


def main() -> int:
    for path in (TEMPLATE, DATA):
        if not path.exists():
            sys.exit(f"FATAL: berkas tidak ada: {path}")

    raw = DATA.read_bytes()
    payload = json.loads(raw)
    payload_sha = hashlib.sha256(raw).hexdigest()
    template_bytes = TEMPLATE.read_bytes()
    template_sha = hashlib.sha256(template_bytes).hexdigest()
    template = template_bytes.decode("utf-8")

    for ph in ("/*__DATA__*/ {}", "/*__FONTS__*/", "<!--__CHART__-->", "<!--__BARS__-->",
               "<!--__SCATTER__-->", "<!--__TABLE__-->"):
        if ph not in template:
            sys.exit(f"FATAL: placeholder '{ph}' tidak ditemukan di template")

    fig = payload["fig"]
    meta = payload["meta"]
    ihsg_series = json.loads((ROOT / "data" / "pasar" / "ihsg.json").read_text())["series"]
    ihsg_by_date = {r["date"]: r["close"] for r in ihsg_series}

    scatter, n_pairs = scatter_svg(payload["series"], ihsg_by_date)
    if n_pairs != fig["n_obs"]:
        sys.exit(f"FATAL: scatter {n_pairs} hari, payload n_obs {fig['n_obs']}")

    as_of = meta["as_of"]
    figs = {
        "as_of_long": long_date(as_of),
        "first": fig["first"]["d"],
        "last": fig["last"]["d"],
        "first_date_short": short_date(fig["first_date"]),
        "last_date_short": short_date(as_of),
        "change_pct": pct(fig["change_pct"]),
        "corr": num(fig["corr"], 3),
        "n_obs": str(fig["n_obs"]),
        "peak": fig["peak"]["d"],
        "peak_date": long_date(fig["peak"]["date"]),
        "trough": fig["trough"]["d"],
        "trough_date": long_date(fig["trough"]["date"]),
        "band_width": num(fig["peak"]["r"] - fig["trough"]["r"], 0),
        "n_diff": str(len(payload["diff_dir"])),
        "n_same": str(len(payload["same_dir"])),
        "oct_fx": pct(fig["oct_fx_pct"]),
        "oct_ihsg": pct(fig["oct_ihsg_pct"]),
        "oct_month_label": fig.get("month_label", "Oktober"),
        "payload_sha": payload_sha,
    }

    rendered = template.replace("/*__DATA__*/ {}", json.dumps(payload, ensure_ascii=False))
    rendered = rendered.replace("/*__FONTS__*/", fonts_css())
    rendered = rendered.replace("<!--__CHART__-->", chart_svg(payload["series"], fig["peak"], fig["trough"]))
    rendered = rendered.replace("<!--__BARS__-->", bars_html(payload["monthly"]))
    rendered = rendered.replace("<!--__SCATTER__-->", scatter)
    rendered = rendered.replace("<!--__TABLE__-->", table_html(payload))

    missing: list[str] = []

    def fill(match: re.Match) -> str:
        key = match.group("key")
        value = figs.get(key)
        if value is None:
            missing.append(key)
            return match.group(0)
        return match.group("open") + _html.escape(value) + match.group("close")

    rendered, filled = re.subn(
        r'(?P<open><(?P<tag>\w+)[^>]*data-fig="(?P<key>[^"]+)"[^>]*>)(?P<inner>[^<]*)(?P<close></(?P=tag)>)',
        fill,
        rendered,
    )
    if missing:
        sys.exit(f"FATAL: {len(missing)} slot data-fig tanpa entri payload: {sorted(set(missing))}")

    slots = rendered.count('data-fig="')
    if filled != slots:
        sys.exit(f"FATAL: {slots} slot data-fig tapi {filled} terisi; ada slot dengan markup dalam")

    for bad in ("—", "–"):
        if bad in rendered.split("<script>")[0]:
            sys.exit(f"FATAL: em/en dash di markup: {bad}")

    rendered = dateline_stamp(rendered, as_of)

    final = f"<!--BUILD sha256:{template_sha}-->\n" + rendered
    OUT.write_text(final, encoding="utf-8")

    print(f"Filled {filled} data-fig fallbacks")
    print(f"Generated {OUT.relative_to(ROOT)} ({len(final)} bytes) sha256 template:{template_sha[:16]} payload:{payload_sha[:16]}")
    print(f"as_of {as_of} | {meta['points']} titik kurs | korelasi {figs['corr']} | {n_pairs} hari beririsan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
