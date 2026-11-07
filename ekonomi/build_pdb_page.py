#!/usr/bin/env python3
"""build_pdb_page.py - compile ekonomi/pdb.html dari data/ekonomi/pdb_q3_2026.json.

Kerangka (token warna, font, strip dateline) dipinjam dari pasar/rupiah.template.html supaya satu
rumah dengan seri Pasar. Semua angka lahir dari payload. Gate sebelum menulis:
  1. tiap angka sumber di payload harus ketemu verbatim (format Indonesia) di teks BRS BPS;
  2. tiap slot data-fig terisi;
  3. nol em/en dash di markup.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pasar"))
from build_rupiah_page import fonts_css  # noqa: E402
from dateline import stamp as dateline_stamp  # noqa: E402

SHELL = ROOT / "pasar" / "rupiah.template.html"
DATA = ROOT / "data" / "ekonomi" / "pdb_q3_2026.json"
SRC = ROOT / "data" / "ekonomi" / "source"
OUT = ROOT / "ekonomi" / "pdb.html"
URL = "https://dashboard.idzharulhuda.com/ekonomi/pdb.html"

# Angka turunan (dihitung, bukan dikutip): dikecualikan dari gate verbatim.
DERIVED = {"sp_jt_25", "sp_jt_26", "sp_jt_delta"}


def num(x: float, dec: int = 2) -> str:
    s = f"{abs(x):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def pp(x: float) -> str:
    return ("+" if x > 0 else "") + num(x)


CSS_EXTRA = """
.hbars { margin-top: 36px; border-top: 1px solid var(--line); }
.hrow { display: grid; grid-template-columns: 200px 1fr 92px; gap: 16px; align-items: center;
  padding: 11px 0; border-bottom: 1px solid var(--line); }
.hrow .hk { font-size: 15px; }
.hrow .ht { position: relative; height: 18px; background: var(--plane-2); }
.sheet-plane-2 .hrow .ht { background: var(--plane); }
.hrow .hf { position: absolute; left: 0; top: 0; height: 18px; background: var(--control); }
.hrow.on .hf { background: var(--accent); }
.hrow .hv { font-family: var(--font-mono); font-size: 15px; font-weight: 500; text-align: right;
  font-variant-numeric: tabular-nums; }
.hrow.on .hk, .hrow.on .hv { color: var(--accent); font-weight: 600; }
.hrow .ref { position: absolute; top: -5px; bottom: -5px; width: 0; border-left: 2px dashed var(--ink); }
.ref-note { font-family: var(--font-mono); font-size: 12px; color: var(--muted); margin-top: 12px; }
.pair-table td.up { color: var(--accent); font-weight: 600; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 56px; margin-top: 40px; align-items: start; }
.two h3 { font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--control); font-weight: 500; }
.two .pair-table { margin-top: 14px; }
@media (max-width: 900px) {
  .hrow { grid-template-columns: 120px 1fr 64px; gap: 10px; }
  .hrow .hk { font-size: 13px; }
  .two { grid-template-columns: 1fr; gap: 40px; }
}
"""


def hbars(rows: list[tuple[str, float, bool]], scale: float, ref: float | None = None) -> str:
    out = ['<div class="hbars">']
    for label, v, on in rows:
        r = f'<div class="ref" style="left:{ref / scale * 100:.2f}%"></div>' if ref is not None else ""
        out.append(
            f'<div class="hrow{" on" if on else ""}"><div class="hk">{_html.escape(label)}</div>'
            f'<div class="ht"><div class="hf" style="width:{max(v, 0) / scale * 100:.2f}%"></div>{r}</div>'
            f'<div class="hv">{num(v)}{"%" if scale < 30 else ""}</div></div>')
    out.append("</div>")
    return "\n".join(out)


def pair_table(head: tuple[str, str, str, str], rows: list[tuple[str, float, float]]) -> str:
    h = "".join(f"<th>{c}</th>" for c in head)
    body = []
    for label, a, b in rows:
        d = b - a
        body.append(f'<tr><td>{_html.escape(label)}</td><td class="muted">{num(a)}</td><td>{num(b)}</td>'
                    f'<td class="{"up" if d > 0 else "muted"}">{pp(d)}</td></tr>')
    return f'<table class="pair-table"><thead><tr>{h}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def gate_sources(payload: dict) -> int:
    txt = "".join((SRC / f).read_text(encoding="utf-8") for f in
                  ("brs_pdb_q3_2026.txt", "brs_sakernas_agu_2026.txt"))
    flat = re.sub(r"\s+", " ", txt)
    want: list[tuple[str, float]] = [(k, v) for k, v in payload["fig"].items() if k not in DERIVED]
    want += [(r["q"], r["yoy"]) for r in payload["quarters"]]
    for r in payload["age"] + payload["edu"]:
        want += [(f"{r.get('group', r.get('level'))} 2025", r["y2025"]), (f"{r.get('group', r.get('level'))} 2026", r["y2026"])]
    want += [(r["sector"], r["juta"]) for r in payload["wage"]]
    miss = []
    for key, v in want:
        cands = {num(abs(v)), num(abs(v), 1), num(v), num(v, 1), num(v, 0) if float(v).is_integer() else num(v)}
        if not any(c in flat for c in cands):
            miss.append(f"{key}={v}")
    if miss:
        sys.exit(f"FATAL: {len(miss)} angka tidak ketemu di BRS: {miss}")
    return len(want)


def main() -> int:
    raw = DATA.read_bytes()
    P = json.loads(raw)
    F, meta = P["fig"], P["meta"]
    payload_sha = hashlib.sha256(raw).hexdigest()
    for fn, sha in meta["sha256"].items():
        if hashlib.sha256((SRC / fn).read_bytes()).hexdigest() != sha:
            sys.exit(f"FATAL: sha256 {fn} tidak cocok dengan payload")
    n_src = gate_sources(P)

    shell = SHELL.read_text(encoding="utf-8")
    head, _, _ = shell.partition('  <main class="sheet">')
    head = re.sub(r"<title>.*?</title>", "<title>EKONOMI · 01: Ekonomi Ngebut, Anak Muda Ketinggalan</title>", head)
    head = re.sub(r'<meta name="description" content="[^"]*">',
                  f'<meta name="description" content="PDB TW III 2026 tumbuh {num(F["yoy"])} persen, pengangguran turun ke '
                  f'{num(F["tpt_26"])} persen, tapi pengangguran usia 15-24 naik ke {num(F["muda_26"])} persen. Data BPS 5 November 2026.">', head)
    head = head.replace('href="https://dashboard.idzharulhuda.com/pasar/rupiah.html"', f'href="{URL}"')
    head = re.sub(r'<span class="i18n-switch">.*?</span></div>', "</div>", head, count=1, flags=re.S)
    head = head.replace('data-cadence="daily"', 'data-cadence="daily" data-fixed')
    head = head.replace("  </style>", CSS_EXTRA + "  </style>", 1)
    head = head.replace("/*__FONTS__*/", fonts_css())
    if "rupiah" in re.sub(r"<script>.*?</script>", "", head, flags=re.S):
        sys.exit("FATAL: sisa kata 'rupiah' di kepala halaman")

    q = P["quarters"]
    q_bars = hbars([(r["q"], r["yoy"], i == len(q) - 1) for i, r in enumerate(q)], 7.0)
    fastest = sorted(q, key=lambda r: -r["yoy"])
    rank = [r["q"] for r in fastest].index(q[-1]["q"]) + 1
    comp = hbars([("Konsumsi pemerintah", F["pem_q3"], True), ("Investasi (PMTB)", F["pmtb_q3"], True),
                  ("Rumah tangga", F["rt_q3"], False)], 10.0, ref=F["yoy"])
    src = [("Rumah tangga", F["src_rt"]), ("Investasi (PMTB)", F["src_pmtb"]), ("Ekspor", F["src_ekspor"]),
           ("Pemerintah", F["src_pem"]), ("Impor (dikurangkan)", F["src_impor"])]
    src_tbl = ('<table class="pair-table"><thead><tr><th>Komponen</th><th>Poin persen</th></tr></thead><tbody>'
               + "".join(f'<tr><td>{k}</td><td>{pp(v)}</td></tr>' for k, v in src) + "</tbody></table>")
    age_tbl = pair_table(("Kelompok", "Agu 2025", "Agu 2026", "Selisih"), [(r["group"], r["y2025"], r["y2026"]) for r in P["age"]])
    edu_tbl = pair_table(("Pendidikan", "Agu 2025", "Agu 2026", "Selisih"), [(r["level"], r["y2025"], r["y2026"]) for r in P["edu"]])
    wage = sorted(P["wage"], key=lambda r: -r["juta"])
    wage_bars = hbars([(r["sector"], r["juta"], r["juta"] < F["upah"]) for r in wage], 30.0 if False else 6.0, ref=F["upah"])
    wage_bars = wage_bars.replace("%</div></div>", "</div></div>")
    n_up = sum(1 for r in P["age"] if r["y2026"] > r["y2025"])
    n_below = sum(1 for r in wage if r["juta"] < F["upah"])
    hari = lambda juta: num(juta * 1e6 / 30 / 1000, 0)

    figs = {
        "yoy": num(F["yoy"]) + "%", "qoq": num(F["qoq"]) + "%", "ctc": num(F["ctc"]) + "%",
        "yoy_q2": num(F["yoy_q2"]) + "%", "rank": str(rank), "nq": str(len(q)),
        "tpt_25": num(F["tpt_25"]) + "%", "tpt_26": num(F["tpt_26"]) + "%",
        "muda_25": num(F["muda_25"]) + "%", "muda_26": num(F["muda_26"]) + "%", "n_up": str(n_up),
        "pem": num(F["pem_q3"]) + "%", "pmtb": num(F["pmtb_q3"]) + "%", "rt": num(F["rt_q3"]) + "%",
        "rt_sh25": num(F["rt_share_q3_25"]) + "%", "rt_sh26": num(F["rt_share_q3_26"]) + "%",
        "sp_25": num(F["sp_25"]) + "%", "sp_26": num(F["sp_26"]) + "%",
        "sp_jt_25": num(F["sp_jt_25"]), "sp_jt_26": num(F["sp_jt_26"]), "sp_jt_delta": num(F["sp_jt_delta"]),
        "informal": num(F["informal_pct"]) + "%", "informal_jt": num(F["informal_jt"]),
        "upah": num(F["upah"]), "upah_hari": hari(F["upah"]), "upah_p": num(F["upah_p"]), "upah_p_hari": hari(F["upah_p"]),
        "n_below": str(n_below), "smk": num(P["edu"][0]["y2026"]) + "%",
        "sha_pdb": meta["sha256"]["brs_pdb_q3_2026.pdf"], "sha_ker": meta["sha256"]["brs_sakernas_agu_2026.pdf"],
        "payload_sha": payload_sha, "n_src": str(n_src),
    }

    def f(k: str) -> str:
        return f'<span data-fig="{k}">-</span>'

    main_html = f"""  <main class="sheet">

    <section class="sheet sheet-ground band-hero" aria-labelledby="hero-heading">
      <div class="container">
        <a class="nav-back" href="../index.html">Data by Arc</a>
        <div class="hero-grid" style="margin-top:32px">
          <div class="hero-main">
            <span class="micro-label">EKONOMI · 01 · PDB DAN SAKERNAS</span>
            <h1 id="hero-heading" class="hero-title">Ekonomi ngebut, anak muda ketinggalan</h1>
            <p class="hero-lead">Pada 5 November 2026 BPS merilis dua angka di hari yang sama. Ekonomi kuartal ketiga tumbuh
              <strong>{f("yoy")}</strong>, dan pengangguran nasional turun dari {f("tpt_25")} ke {f("tpt_26")}. Tapi untuk usia
              15 sampai 24 tahun, pengangguran justru naik ke <strong>{f("muda_26")}</strong>. Dua-duanya benar.</p>
            <p class="hero-note">Data resmi BPS, bukan prediksi. Seri Ekonomi, bagian pertama.</p>
          </div>
          <aside class="scale-strip" aria-label="Angka kunci">
            <div class="scale-item"><div class="scale-k">PDB TW III 2026</div>
              <div class="scale-v">{f("yoy")}</div><div class="scale-n">tumbuh y-on-y</div></div>
            <div class="scale-item"><div class="scale-k">Pengangguran Agu 2026</div>
              <div class="scale-v">{f("tpt_26")}</div><div class="scale-n">turun dari {f("tpt_25")}</div></div>
            <div class="scale-item"><div class="scale-k">Usia 15-24</div>
              <div class="scale-v accent">{f("muda_26")}</div><div class="scale-n">naik dari {f("muda_25")}</div></div>
          </aside>
        </div>
      </div>
    </section>

    <section class="sheet sheet-plane band" aria-labelledby="q-heading">
      <div class="container">
        <h2 id="q-heading" class="band-title">Kuartal tercepat ke-{f("rank")} dari {f("nq")}</h2>
        <p class="band-lead">Pertumbuhan PDB y-on-y tiap kuartal sejak TW III 2024. Kuartal ini {f("yoy")}, naik dari {f("yoy_q2")}
          di kuartal sebelumnya. Dibanding kuartal lalu (q-to-q) tumbuh {f("qoq")}, dan kumulatif Januari sampai September {f("ctc")}.</p>
{q_bars}
      </div>
    </section>

    <section class="sheet sheet-plane-2 band" aria-labelledby="komp-heading">
      <div class="container">
        <h2 id="komp-heading" class="band-title">Yang ngegas pemerintah dan investasi</h2>
        <p class="band-lead">Pertumbuhan tiap komponen pengeluaran, garis putus-putus adalah ekonomi secara keseluruhan ({f("yoy")}).
          Belanja rumah tangga tumbuh {f("rt")}, lebih pelan dari ekonominya, sehingga porsinya di PDB turun dari {f("rt_sh25")}
          ke {f("rt_sh26")}. <em>Tetap penyumbang terbesar, karena porsinya paling besar.</em></p>
{comp}
        <p class="ref-note">garis putus-putus = PDB {f("yoy")}</p>
        <div class="two">
          <div><h3>Sumber pertumbuhan</h3>{src_tbl}</div>
          <div class="note-box" style="margin-top:0"><div class="note-k">Cara membaca</div>
            <p>Tumbuh cepat belum tentu menyumbang banyak. Pemerintah tumbuh {f("pem")} tapi porsinya kecil, jadi sumbangannya
              ke pertumbuhan lebih kecil dari rumah tangga yang tumbuh {f("rt")}. Impor bernilai negatif karena dikurangkan dari PDB.</p></div>
        </div>
      </div>
    </section>

    <section class="sheet sheet-plane band" aria-labelledby="muda-heading">
      <div class="container">
        <h2 id="muda-heading" class="band-title">Cuma {f("n_up")} kelompok umur yang naik</h2>
        <p class="band-lead">Tingkat pengangguran terbuka (TPT) Agustus 2025 dibanding Agustus 2026, persen. Semua kelompok umur turun
          kecuali 15 sampai 24 tahun. Menurut pendidikan, lulusan SMK tetap paling tinggi di {f("smk")}, meski turun.</p>
        <div class="two">
          <div><h3>Menurut umur</h3>{age_tbl}</div>
          <div><h3>Menurut pendidikan</h3>{edu_tbl}</div>
        </div>
      </div>
    </section>

    <section class="sheet sheet-plane-2 band" aria-labelledby="kerja-heading">
      <div class="container">
        <h2 id="kerja-heading" class="band-title">Kerja, tapi belum cukup</h2>
        <p class="band-lead">Setengah penganggur (kerja di bawah 35 jam seminggu dan masih mencari atau mau menerima kerja lain) naik
          dari {f("sp_25")} ke {f("sp_26")} penduduk bekerja, kira-kira {f("sp_jt_25")} ke {f("sp_jt_26")} juta orang
          (tambah {f("sp_jt_delta")} juta). {f("informal")} pekerja, atau {f("informal_jt")} juta orang, ada di kegiatan informal.</p>
        <p class="band-lead">Upah rata-rata buruh Rp{f("upah")} juta sebulan, sekitar Rp{f("upah_hari")} ribu sehari. Buruh perempuan
          Rp{f("upah_p")} juta, sekitar Rp{f("upah_p_hari")} ribu sehari. Di bawah ini rata-rata upah per lapangan usaha, juta rupiah;
          garis putus-putus adalah rata-rata semua buruh. <em>{f("n_below")} lapangan usaha di bawah rata-rata.</em></p>
{wage_bars}
      </div>
    </section>

    <section class="sheet sheet-plane band band-tight" aria-labelledby="batas-heading">
      <div class="container">
        <h2 id="batas-heading" class="band-title">Batas dari angka ini</h2>
        <p class="band-lead">Data ini tidak menunjukkan daya beli anjlok: belanja rumah tangga masih tumbuh {f("rt")}. Data ini juga
          tidak memberi sebab kenapa anak muda lebih sulit dapat kerja, dan perbandingannya hanya Agustus 2025 ke Agustus 2026,
          belum cukup untuk menyebut tren panjang. Jumlah setengah penganggur dalam juta orang adalah hitungan kami
          (tingkat dikali penduduk bekerja), bukan angka yang diterbitkan BPS.</p>
      </div>
    </section>

    <footer class="sheet sheet-ground foot">
      <div class="container">
        <a class="nav-back" href="../index.html">Kembali ke semua cerita data</a>
        <div class="foot-grid">
          <div>
            <h2>Sumber</h2>
            <p>BPS, Berita Resmi Statistik No. 111/11/Th. XXIX (Pertumbuhan Ekonomi Indonesia Triwulan III-2026) dan
              No. 113/11/Th. XXIX (Keadaan Ketenagakerjaan Indonesia Agustus 2026), keduanya 5 November 2026.</p>
            <p>sha256 BRS PDB: <code data-fig="sha_pdb">-</code></p>
            <p>sha256 BRS Sakernas: <code data-fig="sha_ker">-</code></p>
          </div>
          <div>
            <h2>Metode</h2>
            <p>Semua angka lahir dari <code>data/ekonomi/pdb_q3_2026.json</code> lewat generator. Sebelum terbit, {f("n_src")} angka
              sumber dicocokkan verbatim ke teks kedua BRS; build berhenti kalau satu saja tidak ketemu. Upah per hari = upah sebulan dibagi 30.</p>
            <p>sha256 payload: <code data-fig="payload_sha">-</code></p>
            <p>Data by Arc, 7 November 2026.</p>
          </div>
        </div>
      </div>
    </footer>

  </main>
  <script>const DATA = {json.dumps(P, ensure_ascii=False)};</script>
</body>
</html>
"""
    page = head + main_html
    missing: list[str] = []

    def fill(m: re.Match) -> str:
        v = figs.get(m.group("key"))
        if v is None:
            missing.append(m.group("key"))
            return m.group(0)
        return m.group("open") + _html.escape(v) + m.group("close")

    page, filled = re.subn(r'(?P<open><(?P<tag>\w+)[^>]*data-fig="(?P<key>[^"]+)"[^>]*>)(?P<inner>[^<]*)(?P<close></(?P=tag)>)',
                           fill, page)
    if missing:
        sys.exit(f"FATAL: slot data-fig tanpa nilai: {sorted(set(missing))}")
    if filled != page.count('data-fig="'):
        sys.exit("FATAL: ada slot data-fig yang tidak terisi")
    for bad in ("\u2014", "\u2013"):
        if bad in page.split("<script>const DATA")[0]:
            sys.exit(f"FATAL: em/en dash di markup: {bad!r}")
    page = dateline_stamp(page, meta["as_of"])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"OK {OUT.relative_to(ROOT)} {len(page)} bytes | {filled} data-fig | {n_src} angka sumber cocok verbatim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
