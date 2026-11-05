#!/usr/bin/env python3
"""Turunkan halaman berbahasa Inggris dari halaman Indonesia yang sudah lolos gate.

Halaman Indonesia tetap sumber kebenaran: setiap builder menulisnya dan setiap gate memeriksanya.
Skrip ini membaca hasil akhirnya lalu menulis kembarannya di `en/`, dengan tiga lapis terjemahan:

1. Blok teks (p, li, h1..h6, td, ...) dicocokkan utuh lewat katalog `i18n/en.json`. Isi span
   `data-fig` dikosongkan dulu sebelum dicocokkan, jadi kunci katalog tidak ikut berubah saat
   angkanya bergeser; angkanya diisi kembali dengan format Inggris (4,904.29; 4 November 2026).
2. Data yang disematkan (`const DATA = {...}` dan script application/json) diterjemahkan per string.
3. Potongan kode JS yang berisi teks (nama bulan, label tooltip) diganti lewat daftar `js` di katalog;
   setiap entri wajib ketemu, supaya katalog tidak diam-diam basi saat template berubah.

Mode:
  python3 i18n/localize.py            tulis semua halaman en/ lalu periksa sisa teks Indonesia
  python3 i18n/localize.py --missing  cetak unit yang belum ada di katalog (bahan menerjemahkan)

Keluar 1 kalau ada teks Indonesia yang tersisa atau entri JS yang tidak ketemu; halaman yang
sudah ditulis tetap ada, tapi job yang memanggil ini tidak boleh commit `en/` dalam keadaan itu.
"""

from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "i18n" / "en.json"
SITE = "https://dashboard.idzharulhuda.com/"

PAGES = {
    "index.html": "en/index.html",
    "gempa/index.html": "en/gempa/index.html",
    "pisa/index.html": "en/pisa/index.html",
    "pisa-explorer/index.html": "en/pisa-explorer/index.html",
    "pasar/ihsg.html": "en/pasar/ihsg.html",
    "pasar/rupiah.html": "en/pasar/rupiah.html",
    "pasar/msci.html": "en/pasar/msci.html",
}

BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "caption", "td", "th",
         "dt", "dd", "label", "button", "summary", "blockquote", "title", "option", "legend",
         "text", "tspan", "desc"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr", "path", "circle", "rect", "line", "use", "stop", "polyline", "polygon",
        "ellipse"}
TEXT_ATTRS = ("aria-label", "title", "alt", "placeholder", "aria-description", "data-label",
              "data-tip", "data-title")
META_TEXT = {"description", "og:description", "og:title", "twitter:title", "twitter:description",
             "og:image:alt", "twitter:image:alt", "og:site_name", "application-name",
             "apple-mobile-web-app-title"}

MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
             "September", "Oktober", "November", "Desember"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
ABBR_ID = {"Jan": "Jan", "Feb": "Feb", "Mar": "Mar", "Apr": "Apr", "Mei": "May", "Jun": "Jun",
           "Jul": "Jul", "Agu": "Aug", "Ags": "Aug", "Agt": "Aug", "Sep": "Sep", "Okt": "Oct",
           "Nov": "Nov", "Des": "Dec"}

# Kata yang hampir pasti Indonesia. Dipakai untuk mendeteksi sisa terjemahan, bukan untuk menerjemahkan.
ID_WORDS = set("""terhadap tanpa belum sudah hingga antara yang dan di ke dari untuk dengan tidak ini itu pada adalah atau juga lebih sejak sampai
hingga bulan tahun hari gempa siswa bawah atas terakhir sumber catatan belum sesi kurs puncak terendah naik
turun persen cuma hanya setiap semua antara sebelum setelah karena oleh dalam akan bisa masih sudah jadi kalau
saat tiap baca lihat halaman tanggal skor nilai rata kedalaman kekuatan lautan daratan negara terbaru
berapa seberapa diambil dihitung dijumlah sebelumnya pertama kedua ketiga terbesar terkecil tertinggi
terendahnya penutupan harian bulanan tanpa memakai pakai lewat tetap paling sama beda selisih jumlah
kejadian tercatat wilayah provinsi pusat episentrum terasa kuat dangkal dalamnya mana bagaimana kenapa
mengapa sekitar hampir kurang lebihnya kali bagian sektor bobot porsi saham pasar cakupan melemah
menguat sumbu garis batang titik angka metode catatan unduh tentang ringkasan buka tutup pilih semua
""".split()) | {m.lower() for m in MONTHS_ID if m not in ("April", "September", "November")} | {"okt", "des", "agu", "ags"}
KEEP_PHRASES = [
    # nama diri dan istilah sumber yang sengaja tidak diterjemahkan
    "Bank Rakyat Indonesia", "Bank Negara Indonesia", "Bank Central Asia", "Bank Mandiri",
    "Telkom Indonesia", "Badan Meteorologi, Klimatologi, dan Geofisika", "Nusa Tenggara",
    "Sumatera", "Sulawesi", "Kalimantan", "Jawa", "Papua", "Maluku", "Kepulauan", "Laut",
    "Bahasa Indonesia", "Selat", "Teluk", "Samudra", "Pegunungan", "Barat Daya", "Barat",
    "Timur", "Utara", "Selatan", "Tengah", "Tenggara", "Daya", "Kota", "Kabupaten", "Kab.",
    "Pusat gempa berada di", "Pusmendik",
]


# ---------------------------------------------------------------------------------------------
# angka dan tanggal

# Satu regex untuk semua angka berformat Indonesia (titik ribuan, koma desimal), diganti sekali jalan
# supaya hasil konversi tidak terkonversi ulang. "Rp" boleh menempel di depan angka.
NUM_ID = re.compile(r"(?:(?<=Rp)|(?<![\w.,]))(\d{1,3}(?:\.\d{3})+|\d+)(,\d+)?(?!\w|[.,]\d)")


def num_id_to_en(s: str) -> str:
    def rep(m: re.Match) -> str:
        whole, dec = m.group(1), m.group(2)
        if "." not in whole and not dec:
            return m.group(0)
        return whole.replace(".", ",") + ("." + dec[1:] if dec else "")

    return NUM_ID.sub(rep, s)


def dates_id_to_en(s: str) -> str:
    for i, m in enumerate(MONTHS_ID):
        s = re.sub(rf"\b{m}\b", MONTHS_EN[i], s)
    s = re.sub(r"\b(Mei|Agu|Ags|Agt|Okt|Des)\b(?=[\s.,)\-–]|$)", lambda m: ABBR_ID[m.group(1)], s)
    return s


# satuan dan kata pendek yang menempel pada angka di span data-fig dan string data
UNIT_RULES = [
    (r"(\d) persen\b", r"\1 percent"), (r"\bpersen\b", "percent"),
    (r"(\d) poin\b", r"\1 points"), (r"(\d) sesi\b", r"\1 sessions"), (r"\b1 sessions\b", "1 session"),
    (r"(\d) hari\b", r"\1 days"), (r"\b1 days\b", "1 day"), (r"(\d) bulan\b", r"\1 months"),
    (r"\b1 months\b", "1 month"), (r"(\d) tahun\b", r"\1 years"), (r"\b1 years\b", "1 year"),
    (r"(\d) kejadian\b", r"\1 events"), (r"\b1 events\b", "1 event"), (r"(\d) negara\b", r"\1 countries"),
    (r"(\d) siswa\b", r"\1 students"), (r"(\d) poin skor\b", r"\1 score points"),
    (r"(\d) kali\b", r"\1 times"), (r"(\d) jt\b", r"\1 mn"), (r"(\d) miliar\b", r"\1 billion"),
    (r"(\d) triliun\b", r"\1 trillion"), (r"(\d) juta\b", r"\1 million"), (r"(\d) ribu\b", r"\1 thousand"),
    (r"(\d) km\b", r"\1 km"), (r"\bkm (\d)", r"km \1"),
    (r"(\d) WIB\b", r"\1 WIB"), (r"\bpukul\b", "at"), (r"\bsampai\b", "to"), (r"\bhingga\b", "to"),
    (r"(\d{4}) dan (\d{4})", r"\1 and \2"), (r"(\d) s\.d\. (\d)", r"\1 to \2"),
]


def convert(s: str) -> str:
    s = dates_id_to_en(num_id_to_en(s))
    for pat, rep in UNIT_RULES:
        s = re.sub(pat, rep, s)
    return s


# ---------------------------------------------------------------------------------------------
# katalog

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


FIG_SPAN = re.compile(r'(<span\b[^>]*\bdata-fig="[^"]+"[^>]*>)(.*?)(</span>)', re.S)
DYN_SPAN = re.compile(r'(<(span|strong|b|em|time)\b[^>]*\b(?:data-fig|data-dateline-text|data-live|data-bind)[^>]*>)(.*?)(</\2>)', re.S)


def blank_figs(inner: str) -> tuple[str, list[str]]:
    vals: list[str] = []

    def rep(m: re.Match) -> str:
        vals.append(m.group(2))
        return m.group(1) + m.group(3)

    return FIG_SPAN.sub(rep, inner), vals


def refill_figs(inner: str, vals: list[str], tr) -> str:
    it = iter(vals)

    def rep(m: re.Match) -> str:
        v = next(it, None)
        return m.group(1) + (tr(v) if v is not None else "") + m.group(3)

    return FIG_SPAN.sub(rep, inner)


# Angka dan tanggal di dalam kalimat diganti {0}, {1}, ... sebelum dicocokkan ke katalog,
# supaya kalimat yang angkanya diperbarui tiap hari tetap memakai entri katalog yang sama.
_MON = "|".join(MONTHS_ID + ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Ags", "Agt",
                             "Sep", "Okt", "Nov", "Des"])
TOKEN = re.compile(
    rf"(?<![\w{{}}])(?:\d{{1,2}} (?:{_MON})\b(?: \d{{4}})?|(?:{_MON}) \d{{4}}\b"
    rf"|[-−+]?(?:Rp)?\d+(?:[.,]\d+)*)(?![\w{{}}])"
)


def mask(s: str) -> tuple[str, list[str]]:
    toks: list[str] = []

    def rep(m: re.Match) -> str:
        toks.append(m.group(0))
        return "{" + str(len(toks) - 1) + "}"

    return TOKEN.sub(rep, s), toks


def unmask(tpl: str, toks: list[str]) -> str:
    return re.sub(r"\{(\d+)\}", lambda m: convert(toks[int(m.group(1))]) if int(m.group(1)) < len(toks) else m.group(0), tpl)


class Catalog:
    def __init__(self, path: Path):
        raw = json.loads(path.read_text()) if path.exists() else {}
        self.strings: dict[str, str] = {norm(k): v for k, v in raw.get("strings", {}).items()}
        self.patterns = [(re.compile(p), r) for p, r in raw.get("patterns", [])]
        self.js_all = raw.get("js_all", [])
        self.js = raw.get("js", {})
        self.js_used: set[str] = set()
        self.missing: dict[str, set[str]] = {}
        self.page = ""

    def lookup(self, s: str) -> str | None:
        k = norm(s)
        if k in self.strings:
            return self.strings[k]
        mk, toks = mask(k)
        if mk in self.strings:
            return unmask(self.strings[mk], toks)
        for pat, rep in self.patterns:
            if pat.fullmatch(k):
                return pat.sub(lambda m: convert(m.expand(rep)), k, count=1)
        return None

    def text(self, s: str, record: bool = True) -> str:
        """Terjemahkan satu string polos. Yang tidak dikenal dikonversi angka/tanggalnya saja."""
        if not s.strip() or not re.search(r"[A-Za-z]", s):
            return convert(s)
        hit = self.lookup(s)
        if hit is not None:
            lead = s[: len(s) - len(s.lstrip())]
            trail = s[len(s.rstrip()):]
            return lead + hit + trail
        out = convert(s)
        if record and looks_indonesian(out):
            self.missing.setdefault(self.page, set()).add(mask(norm(s))[0])
        return out


def looks_indonesian(s: str) -> bool:
    t = re.sub(r"(?:https?://\S+|/[\w./-]*|[\w./-]+\.(?:json|html|csv|png|svg|js)\b)", " ", s)
    for ph in KEEP_PHRASES:
        t = t.replace(ph, " ")
    t = re.sub(r"<[^>]+>", " ", t)
    words = re.findall(r"[A-Za-z]+", t.lower())
    return any(w in ID_WORDS for w in words)


# ---------------------------------------------------------------------------------------------
# pemetaan posisi HTML

class Mapper(HTMLParser):
    """Catat offset absolut untuk tag, blok daun, node teks, dan isi script."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.line_starts = [0]
        for m in re.finditer("\n", html):
            self.line_starts.append(m.end())
        self.stack: list[dict] = []
        self.blocks: list[tuple[int, int, str]] = []   # (inner_start, inner_end, tag)
        self.texts: list[tuple[int, int]] = []
        self.starttags: list[tuple[int, int, str, list]] = []
        self.scripts: list[tuple[int, int, str]] = []  # (start, end, type)
        self.feed(html)
        self.close()

    def off(self) -> int:
        ln, col = self.getpos()
        return self.line_starts[ln - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self.off()
        raw = self.get_starttag_text() or ""
        end = start + len(raw)
        self.starttags.append((start, end, tag, attrs))
        if tag in VOID or raw.endswith("/>"):
            return
        self.stack.append({"tag": tag, "inner": end, "has_block": False, "attrs": dict(attrs)})

    def handle_startendtag(self, tag, attrs):
        start = self.off()
        raw = self.get_starttag_text() or ""
        self.starttags.append((start, start + len(raw), tag, attrs))

    def handle_endtag(self, tag):
        pos = self.off()
        while self.stack:
            node = self.stack.pop()
            if node["tag"] == tag:
                if tag in ("script", "style"):
                    if tag == "script":
                        self.scripts.append((node["inner"], pos, node["attrs"].get("type") or ""))
                    return
                if tag in BLOCK and not node["has_block"]:
                    self.blocks.append((node["inner"], pos, tag))
                if tag in BLOCK or node["has_block"]:
                    for parent in self.stack:
                        parent["has_block"] = True
                return

    def handle_data(self, data):
        if self.stack and self.stack[-1]["tag"] in ("script", "style"):
            return
        start = self.off()
        self.texts.append((start, start + len(data)))


# ---------------------------------------------------------------------------------------------
# data tersemat

def translate_json(obj, cat: Catalog, key: str | None = None):
    if isinstance(obj, dict):
        return {k: translate_json(v, cat, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [translate_json(v, cat, key) for v in obj]
    if isinstance(obj, str):
        if key in ("id", "iso", "iso3", "code", "ticker", "date", "month", "key", "slug", "cls", "class"):
            return obj
        if "<" in obj and ">" in obj:
            return translate_fragment(obj, cat)
        return cat.text(obj)
    return obj


def translate_fragment(frag: str, cat: Catalog) -> str:
    """String berisi HTML kecil (mis. dari DATA): coba utuh dulu, lalu per node teks."""
    blank, vals = blank_figs(frag)
    hit = cat.lookup(blank)
    if hit is not None:
        return refill_figs(hit, vals, lambda v: cat.text(v, record=False))
    parts = re.split(r"(<[^>]+>)", frag)
    return "".join(p if p.startswith("<") else cat.text(p) for p in parts)


def translate_scripts(html: str, cat: Catalog, page: str, problems: list[str]) -> str:
    # 1) DATA = {...} dan script JSON
    def json_at(s: str, i: int) -> tuple[object, int]:
        return json.JSONDecoder().raw_decode(s, i)

    out = []
    pos = 0
    for m in re.finditer(r"(<script\b[^>]*>)(.*?)(</script>)", html, flags=re.S):
        out.append(html[pos:m.start(2)])
        body = m.group(2)
        tag = m.group(1)
        if "application/json" in tag or "application/ld+json" in tag:
            try:
                obj = json.loads(body)
                lead = body[: len(body) - len(body.lstrip())]
                body = lead + json.dumps(translate_json(obj, cat), ensure_ascii=False) + "\n"
            except json.JSONDecodeError as e:
                problems.append(f"{page}: JSON tersemat tidak terbaca ({e})")
        else:
            for dm in list(re.finditer(r"\bconst DATA = ", body))[::-1]:
                try:
                    obj, end = json_at(body, dm.end())
                except json.JSONDecodeError as e:
                    problems.append(f"{page}: const DATA tidak terbaca ({e})")
                    continue
                body = body[: dm.end()] + json.dumps(translate_json(obj, cat), ensure_ascii=False) + body[end:]
        out.append(body)
        pos = m.end(2)
    out.append(html[pos:])
    html = "".join(out)

    # 2) potongan kode JS
    for find, rep in cat.js_all + cat.js.get(page, []):
        n = html.count(find)
        if n:
            cat.js_used.add(find)
        if n == 0:
            if [find, rep] in cat.js.get(page, []):
                problems.append(f"{page}: entri js tidak ketemu: {find[:80]!r}")
            continue
        html = html.replace(find, rep)
    return html


# ---------------------------------------------------------------------------------------------
# satu halaman

def en_url(id_path: str) -> str:
    p = PAGES[id_path]
    return SITE + (p[: -len("index.html")] if p.endswith("index.html") else p)


def id_url(id_path: str) -> str:
    return SITE + (id_path[: -len("index.html")] if id_path.endswith("index.html") else id_path)


def localize(id_path: str, cat: Catalog, problems: list[str]) -> str:
    cat.page = id_path
    html = (ROOT / id_path).read_text()
    mp = Mapper(html)
    edits: list[tuple[int, int, str]] = []
    covered: list[tuple[int, int]] = []
    pending: list[tuple[int, int]] = []  # blok yang belum ada di katalog: node teksnya tidak dicatat terpisah

    # blok daun utuh
    for a, b, tag in mp.blocks:
        inner = html[a:b]
        if not re.search(r"[A-Za-z]{2}", re.sub(r"<[^>]+>", "", inner)):
            continue
        blank, vals = blank_figs(inner)
        hit = cat.lookup(blank) if "<" in blank else None
        if hit is not None:
            new = refill_figs(hit, vals, lambda v: cat.text(v, record=False))
            edits.append((a, b, new))
            covered.append((a, b))
        elif "<" in blank and looks_indonesian(re.sub(r"<[^>]+>", " ", blank)):
            # catat versi utuhnya sebagai bahan katalog; node teksnya tetap dicoba satu per satu
            hit2 = all(cat.lookup(t) is not None or not looks_indonesian(t)
                       for t in re.split(r"<[^>]+>", blank) if t.strip())
            if not hit2:
                cat.missing.setdefault(id_path, set()).add(mask(norm(blank))[0])
                pending.append((a, b))

    def inside(x: int) -> bool:
        return any(a <= x < b for a, b in covered)

    # node teks sisa
    in_fig = []
    for m in FIG_SPAN.finditer(html):
        in_fig.append((m.start(2), m.end(2)))
    for a, b in mp.texts:
        if inside(a):
            continue
        t = html[a:b]
        if not re.search(r"[A-Za-z0-9]", t):
            continue
        fig = any(x <= a < y for x, y in in_fig)
        quiet = fig or any(x <= a < y for x, y in pending)
        new = cat.text(t, record=not quiet)
        if new != t:
            edits.append((a, b, new))

    # atribut teks
    for a, b, tag, attrs in mp.starttags:
        if inside(a):
            continue
        raw = html[a:b]
        new_raw = raw
        ad = dict(attrs)
        for k, v in attrs:
            if v is None:
                continue
            do = k in TEXT_ATTRS or (tag == "meta" and k == "content" and
                                     (ad.get("name") in META_TEXT or ad.get("property") in META_TEXT))
            if not do or not re.search(r"[A-Za-z]{2}", v):
                continue
            nv = cat.text(v)
            if nv != v:
                new_raw = replace_attr(new_raw, k, v, nv)
        if new_raw != raw:
            edits.append((a, b, new_raw))

    for a, b, new in sorted(edits, key=lambda e: e[0], reverse=True):
        html = html[:a] + new + html[b:]

    html = translate_scripts(html, cat, id_path, problems)
    html = head_fixups(html, id_path)
    html = rebase_links(html, id_path, problems)
    return html


def replace_attr(raw: str, k: str, old: str, new: str) -> str:
    esc = lambda s: s.replace("&", "&amp;").replace('"', "&quot;")
    for q in ('"', "'"):
        for ov in (old, esc(old)):
            needle = f"{k}={q}{ov}{q}"
            if needle in raw:
                return raw.replace(needle, f'{k}="{esc(new)}"', 1)
    return raw


SWITCH_RE = re.compile(r'<span class="i18n-switch">[^<]*<a class="lang-switch"[^>]*>[^<]*</a></span>')
ALT_RE = re.compile(r'\n?\s*<!-- i18n:alternate -->.*?<!-- /i18n:alternate -->', re.S)


def switch_html(href: str, hreflang: str, label: str) -> str:
    return (f'<span class="i18n-switch"> &middot; <a class="lang-switch" href="{href}" hreflang="{hreflang}" '
            f'lang="{hreflang}" style="color:inherit;text-decoration:underline;text-underline-offset:3px">'
            f'{label}</a></span>')


def alternates(id_path: str) -> str:
    return ("\n  <!-- i18n:alternate -->"
            f'<link rel="alternate" hreflang="id" href="{id_url(id_path)}">'
            f'<link rel="alternate" hreflang="en" href="{en_url(id_path)}">'
            f'<link rel="alternate" hreflang="x-default" href="{id_url(id_path)}">'
            "<!-- /i18n:alternate -->")


def add_switch(html: str, id_path: str, to_en: bool) -> str:
    html = ALT_RE.sub("", html)
    html = SWITCH_RE.sub("", html)
    html = html.replace("</head>", alternates(id_path) + "\n</head>", 1)
    sw = (switch_html("/" + PAGES[id_path].replace("index.html", ""), "en", "English") if to_en
          else switch_html(id_url(id_path), "id", "Bahasa Indonesia"))
    html, n = re.subn(r'(<div class="dateline-strip"[^>]*>\s*<span data-dateline-text>[^<]*</span>)',
                      lambda m: m.group(1) + sw, html, count=1)
    if n != 1:
        raise SystemExit(f"{id_path}: dateline-strip tidak ketemu, tombol bahasa tidak bisa dipasang")
    return html


def head_fixups(html: str, id_path: str) -> str:
    html = re.sub(r'<html lang="id"', '<html lang="en"', html, count=1)
    html = re.sub(r'(<meta property="og:locale" content=")[^"]*', r"\1en_US", html)
    html = re.sub(r'(<link rel="canonical" href=")[^"]*', lambda m: m.group(1) + en_url(id_path), html)
    html = re.sub(r'(<meta property="og:url" content=")[^"]*', lambda m: m.group(1) + en_url(id_path), html)
    html = re.sub(r'"inLanguage":\s*"id(-ID)?"', '"inLanguage": "en"', html)
    # tombol bahasa menunjuk balik ke versi Indonesia
    return add_switch(html, id_path, to_en=False)


def rebase_links(html: str, id_path: str, problems: list[str]) -> str:
    """Link relatif ke halaman yang punya versi EN dibiarkan (en/ punya struktur sama);
    link relatif ke berkas lain dijadikan absolut ke situs supaya tidak patah di bawah /en/."""
    base_dir = Path(id_path).parent
    page_targets = {Path(p) for p in PAGES}

    def fix(m: re.Match) -> str:
        attr, q, url = m.group(1), m.group(2), m.group(3)
        if url.startswith("/") and not url.startswith("//"):
            path = url.split("#")[0].split("?")[0]
            rel = path.lstrip("/")
            cand = rel + "index.html" if (rel == "" or rel.endswith("/")) else rel
            if Path(cand) in page_targets and not url.startswith("/en/"):
                return f"{attr}={q}/en{url}{q}"
            return m.group(0)
        if re.match(r"^(?:[a-z]+:|//|#|data:|mailto:|\$\{)", url) or not url:
            return m.group(0)
        path, _, frag = url.partition("#")
        path, _, query = path.partition("?")
        tgt = (base_dir / path)
        tgt_norm = Path(*[p for p in tgt.parts if p != "."])
        parts: list[str] = []
        for p in tgt_norm.parts:
            if p == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(p)
        rel = "/".join(parts)
        cand = rel + "index.html" if (url.endswith("/") or rel == "") else rel
        if url.endswith("/") and rel:
            cand = rel + "/index.html"
        if Path(cand) in page_targets:
            return m.group(0)
        if not (ROOT / cand).exists() and not (ROOT / rel).exists():
            problems.append(f"{id_path}: link relatif tidak ada berkasnya: {url}")
        absu = "/" + rel + (("?" + query) if query else "") + (("#" + frag) if frag else "")
        return f"{attr}={q}{absu}{q}"

    html = re.sub(r'\b(href|src)=(["\'])([^"\']*)\2', fix, html)

    # url(...) di CSS (mis. @font-face '../fonts/x.woff2') juga harus absolut
    def fix_css(m: re.Match) -> str:
        q, url = m.group(1), m.group(2)
        if re.match(r"^(?:[a-z]+:|//|#|/|data:)", url) or not url:
            return m.group(0)
        out = fix(re.match(r'(src)=(["\'])(.*)\2$', f"src={q or chr(34)}{url}{q or chr(34)}"))
        return "url(" + (q or "") + out.split("=", 1)[1][1:-1] + (q or "") + ")"

    return re.sub(r"url\((['\"]?)([^'\")]+)\1\)", fix_css, html)


# ---------------------------------------------------------------------------------------------

def leftover_report(html: str) -> list[str]:
    """Teks terlihat + atribut yang masih Indonesia di halaman EN."""
    mp = Mapper(html)
    bad = []
    for a, b in mp.texts:
        t = html[a:b]
        if looks_indonesian(t):
            bad.append(norm(t)[:140])
    for a, b, tag, attrs in mp.starttags:
        ad = dict(attrs)
        for k, v in attrs:
            if v and (k in TEXT_ATTRS or (tag == "meta" and k == "content" and
                      (ad.get("name") in META_TEXT or ad.get("property") in META_TEXT))):
                if looks_indonesian(v):
                    bad.append(f"[{k}] {norm(v)[:140]}")
    return bad


def main() -> int:
    missing_mode = "--missing" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    cat = Catalog(CATALOG)
    problems: list[str] = []
    leftovers: dict[str, list[str]] = {}
    for id_path, en_path in PAGES.items():
        if only and id_path not in only:
            continue
        out = localize(id_path, cat, problems)
        if missing_mode:
            continue
        dest = ROOT / en_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(out)
        lo = leftover_report(out)
        if lo:
            leftovers[id_path] = lo
        print(f"{en_path}: {len(out):,} byte, sisa Indonesia {len(lo)}")
    if missing_mode:
        print(json.dumps({k: sorted(v) for k, v in cat.missing.items()}, ensure_ascii=False, indent=1))
        return 0
    if not only:
        for find, _ in cat.js_all:
            if find not in cat.js_used:
                problems.append(f"entri js_all tidak ketemu di halaman mana pun: {find[:80]!r}")
    for p in problems:
        print("MASALAH", p)
    for pg, lo in leftovers.items():
        for t in lo[:40]:
            print(f"SISA {pg}: {t}")
    return 1 if (problems or leftovers) else 0


if __name__ == "__main__":
    sys.exit(main())
