#!/usr/bin/env python3
"""Strip "Data per <tanggal>" untuk halaman pasar.

Template menuliskan tanggal secara statis supaya strip terbaca tanpa JavaScript. Kalau payload
bertambah dan template tidak ikut berubah, HTML statis jadi ketinggalan satu sesi. Jadi setiap
builder menulis ulang strip dari `meta.as_of` payload sebelum halaman disimpan.
"""

from __future__ import annotations

import re

MONTHS = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
          "September", "Oktober", "November", "Desember")


def long_date(iso: str) -> str:
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]} {iso[:4]}"


def stamp(html: str, iso: str) -> str:
    """Selaraskan data-as-of dan teks strip di HTML dengan tanggal payload."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso or ""):
        raise ValueError(f"as_of tidak sah: {iso!r}")

    html, n_attr = re.subn(
        r'(data-dateline[\s\S]{0,300}?data-as-of=")[^"]*(")',
        lambda m: m.group(1) + iso + m.group(2),
        html,
        count=1,
    )
    html, n_text = re.subn(
        r'(<span data-dateline-text>)[^<]*(</span>)',
        lambda m: m.group(1) + "Data per " + long_date(iso) + m.group(2),
        html,
        count=1,
    )
    if n_attr != 1 or n_text != 1:
        raise RuntimeError(f"strip dateline tidak ditemukan rapi (data-as-of {n_attr}, teks {n_text})")
    return html
