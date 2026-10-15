#!/usr/bin/env python3
"""Build generator for the public data dashboard hub (index.html).

Standard library only. Deterministic: frozen inputs, no wall clock,
no network, no randomness.
"""

import base64
import csv
import glob
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://dashboard.idzharulhuda.com"


def fmt_float(v: float, dp: int = 1) -> str:
    s = f"{float(v):.{dp}f}"
    if s in ("-0.0", "-0"):
        s = "0.0" if dp > 0 else "0"
    return s.replace(".", ",")


def html_esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;")


def main():
    template_path = os.path.join(ROOT, "hub.template.html")
    annex_prof_path = os.path.join(ROOT, "data", "pisa", "annex_proficiency.csv")
    gempa_data_path = os.path.join(ROOT, "gempa", "data.json")
    pisa_index_path = os.path.join(ROOT, "pisa", "index.html")
    fonts_dir = os.path.join(ROOT, "fonts")
    font_paths = sorted(glob.glob(os.path.join(fonts_dir, "*.woff2")))
    output_path = os.path.join(ROOT, "index.html")
    sitemap_path = os.path.join(ROOT, "sitemap.xml")

    required_inputs = [
        (template_path, "hub.template.html"),
        (annex_prof_path, "data/pisa/annex_proficiency.csv"),
        (gempa_data_path, "gempa/data.json"),
        (pisa_index_path, "pisa/index.html"),
    ]
    for p, name in required_inputs:
        if not os.path.exists(p) or not os.access(p, os.R_OK):
            raise RuntimeError(f"Missing or unreadable input file: {name} ({p})")

    if len(font_paths) != 5:
        raise RuntimeError(f"Expected 5 font files in {fonts_dir}, found {len(font_paths)}")

    input_paths = [annex_prof_path, gempa_data_path, template_path] + font_paths

    stamp_hasher = hashlib.sha256()
    for p in input_paths:
        rel_path = os.path.relpath(p, ROOT).replace("\\", "/")
        with open(p, "rb") as f:
            data = f.read()
        stamp_hasher.update(rel_path.encode("utf-8"))
        stamp_hasher.update(b"\x00")
        stamp_hasher.update(data)
        print(f"{rel_path}: {len(data)} bytes")

    stamp_hex = stamp_hasher.hexdigest()
    stamp_line = f"<!--BUILD sha256:{stamp_hex}-->\n"

    with open(template_path, "r", encoding="utf-8") as f:
        template_str = f.read()

    tmpl_begin_count = template_str.count("<!--SEO:BEGIN-->")
    tmpl_end_count = template_str.count("<!--SEO:END-->")
    if tmpl_begin_count != 1 or tmpl_end_count != 1:
        raise RuntimeError(f"hub.template.html must contain exactly 1 SEO marker pair (begin: {tmpl_begin_count}, end: {tmpl_end_count})")
    if template_str.index("<!--SEO:BEGIN-->") >= template_str.index("<!--SEO:END-->"):
        raise RuntimeError("hub.template.html <!--SEO:BEGIN--> must precede <!--SEO:END-->")

    m_theme = re.search(r"--ground:\s*([^;]+);", template_str)
    if not m_theme:
        raise RuntimeError("Failed to parse --ground from hub.template.html")
    theme = m_theme.group(1).strip()

    m_h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", template_str)
    if not m_h1:
        raise RuntimeError("Failed to parse body H1 text from hub.template.html")
    h1_text = m_h1.group(1).strip()

    plate_entries = re.findall(r'<a href="(/[^"]*/)" class="entry-link-title">([^<]+)</a>', template_str)
    if len(plate_entries) < 2:
        raise RuntimeError(f"Anchor parse found fewer than 2 plates in template (found: {len(plate_entries)})")

    with open(annex_prof_path, "r", encoding="utf-8") as f:
        annex_prof_rows = list(csv.DictReader(f))

    prof_sums = {}
    for domain in ["mathematics", "reading", "science"]:
        d_rows = [r for r in annex_prof_rows if r["domain"] == domain]
        idn_rows = [r for r in d_rows if r["subject"] == "Indonesia"]
        oecd_rows = [r for r in d_rows if r["subject"] == "OECD average"]

        stats = [r["stat"] for r in idn_rows]
        if "level_2" not in stats:
            raise RuntimeError(f"Token 'level_2' not found in stats for {domain}")
        l2_idx = stats.index("level_2")
        below_l2_stats = set(stats[:l2_idx])

        idn_sum = sum(float(r["value"]) for r in idn_rows if r["stat"] in below_l2_stats)
        oecd_sum = sum(float(r["value"]) for r in oecd_rows if r["stat"] in below_l2_stats)

        idn_val = round(idn_sum, 5)
        oecd_val = round(oecd_sum, 5)
        prof_sums[domain] = {
            "idn": idn_val,
            "oecd": oecd_val,
            "idn_d": fmt_float(idn_val, 1),
            "oecd_d": fmt_float(oecd_val, 1),
        }

    with open(gempa_data_path, "r", encoding="utf-8") as f:
        gempa_json = json.load(f)
    field_data = gempa_json["field"]
    gempa_fig = gempa_json.get("fig", {})

    span_first_wib = gempa_fig.get("span_first_wib", {}).get("r")
    if not span_first_wib:
        raise RuntimeError("Missing fig.span_first_wib.r in gempa/data.json")
    gempa_first_year = span_first_wib[:4]

    span_last_wib = gempa_fig.get("span_last_wib", {}).get("r")
    if not span_last_wib:
        raise RuntimeError("Missing fig.span_last_wib.r in gempa/data.json")
    gempa_lastmod = span_last_wib[:10]

    with open(pisa_index_path, "r", encoding="utf-8") as f:
        pisa_content = f.read()
    m_pisa_data = re.search(r"const DATA = (.*?);\n", pisa_content)
    if not m_pisa_data:
        m_pisa_data = re.search(r"const DATA = (.*?);", pisa_content)
    if not m_pisa_data:
        raise RuntimeError("Could not find const DATA in pisa/index.html")
    pisa_data = json.loads(m_pisa_data.group(1))
    pisa_lastmod = pisa_data.get("meta", {}).get("retrieved")
    if not pisa_lastmod:
        raise RuntimeError("Could not find meta.retrieved in pisa/index.html DATA")

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", gempa_lastmod):
        raise RuntimeError(f"Invalid gempa lastmod format: {gempa_lastmod!r}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", pisa_lastmod):
        raise RuntimeError(f"Invalid pisa lastmod format: {pisa_lastmod!r}")

    root_lastmod = max(gempa_lastmod, pisa_lastmod)

    prof_math_idn_d = prof_sums["mathematics"]["idn_d"]

    CANONICAL = f"{SITE}/"
    SLUG = "hub"
    TITLE = "Dashboard Data Publik: Katalog Gempa dan PISA 2025"
    DESC = (
        f"Dua pelat data: katalog gempa M5+ Indonesia sejak {gempa_first_year} "
        f"dan PISA 2025; {prof_math_idn_d}% siswa di bawah ambang minimum kemahiran. "
        "Angka dihitung ulang sebelum terbit."
    )
    OGALT = "Kartu pratinjau indeks Dashboard Data Publik: pelat katalog gempa M5+ Indonesia dan pelat hasil PISA 2025."

    if len(TITLE) > 60:
        raise RuntimeError(f"TITLE exceeds 60 characters: {len(TITLE)}")
    if len(DESC) > 155:
        raise RuntimeError(f"DESC exceeds 155 characters: {len(DESC)}")
    for name, s in [("TITLE", TITLE), ("DESC", DESC), ("OGALT", OGALT)]:
        if "\u2014" in s:
            raise RuntimeError(f"{name} contains U+2014 em dash")

    gempa_total_d = gempa_fig.get("total", {}).get("d", "")
    if gempa_total_d and (gempa_total_d in TITLE or gempa_total_d in DESC):
        raise RuntimeError("Gempa total found in hub TITLE or DESC")
    raw_events_d = gempa_fig.get("raw_events", {}).get("d", "")
    if raw_events_d and (raw_events_d in TITLE or raw_events_d in DESC):
        raise RuntimeError("Gempa raw_events found in hub TITLE or DESC")

    website = {
        "@type": "WebSite",
        "name": h1_text,
        "url": CANONICAL,
        "inLanguage": "id",
        "description": DESC,
    }
    list_items = []
    for pos, (href, name) in enumerate(plate_entries, 1):
        plate_url = f"{SITE}{href}" if href.startswith("/") else href
        list_items.append({
            "@type": "ListItem",
            "position": pos,
            "name": name,
            "url": plate_url,
        })
    collection_page = {
        "@type": "CollectionPage",
        "name": TITLE,
        "url": CANONICAL,
        "inLanguage": "id",
        "description": DESC,
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": list_items,
        },
    }
    graph = {
        "@context": "https://schema.org",
        "@graph": [website, collection_page],
    }
    jsonld = json.dumps(graph, ensure_ascii=True, separators=(",", ":"))

    seo_lines = [
        "  <!--SEO:BEGIN-->",
        f"  <title>{html_esc(TITLE)}</title>",
        f'  <meta name="description" content="{html_esc(DESC)}">',
        f'  <link rel="canonical" href="{html_esc(CANONICAL)}">',
        f'  <meta name="theme-color" content="{html_esc(theme)}">',
        '  <meta property="og:type" content="website">',
        f'  <meta property="og:url" content="{html_esc(CANONICAL)}">',
        f'  <meta property="og:title" content="{html_esc(TITLE)}">',
        f'  <meta property="og:description" content="{html_esc(DESC)}">',
        f'  <meta property="og:image" content="{SITE}/assets/og/{SLUG}.png">',
        '  <meta property="og:image:width" content="1200">',
        '  <meta property="og:image:height" content="630">',
        f'  <meta property="og:image:alt" content="{html_esc(OGALT)}">',
        '  <meta property="og:site_name" content="Dashboard Data Publik">',
        '  <meta property="og:locale" content="id_ID">',
        '  <meta name="twitter:card" content="summary_large_image">',
        f'  <meta name="twitter:title" content="{html_esc(TITLE)}">',
        f'  <meta name="twitter:description" content="{html_esc(DESC)}">',
        f'  <meta name="twitter:image" content="{SITE}/assets/og/{SLUG}.png">',
        '  <link rel="icon" href="/favicon.ico" sizes="32x32">',
        '  <link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        '  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png">',
        '  <link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        f'  <script type="application/ld+json">{jsonld}</script>',
        "  <!--SEO:END-->",
    ]
    seo_block = "\n".join(seo_lines)

    fig = {
        "prof_math_idn": {"v": prof_sums["mathematics"]["idn"], "d": prof_sums["mathematics"]["idn_d"]},
        "prof_math_oecd": {"v": prof_sums["mathematics"]["oecd"], "d": prof_sums["mathematics"]["oecd_d"]},
        "prof_read_idn": {"v": prof_sums["reading"]["idn"], "d": prof_sums["reading"]["idn_d"]},
        "prof_read_oecd": {"v": prof_sums["reading"]["oecd"], "d": prof_sums["reading"]["oecd_d"]},
        "prof_scie_idn": {"v": prof_sums["science"]["idn"], "d": prof_sums["science"]["idn_d"]},
        "prof_scie_oecd": {"v": prof_sums["science"]["oecd"], "d": prof_sums["science"]["oecd_d"]},
    }

    prof_bars = [
        {
            "domain": "matematika",
            "idn": prof_sums["mathematics"]["idn"],
            "oecd": prof_sums["mathematics"]["oecd"],
            "idn_d": prof_sums["mathematics"]["idn_d"],
            "oecd_d": prof_sums["mathematics"]["oecd_d"],
        },
        {
            "domain": "membaca",
            "idn": prof_sums["reading"]["idn"],
            "oecd": prof_sums["reading"]["oecd"],
            "idn_d": prof_sums["reading"]["idn_d"],
            "oecd_d": prof_sums["reading"]["oecd_d"],
        },
        {
            "domain": "sains",
            "idn": prof_sums["science"]["idn"],
            "oecd": prof_sums["science"]["oecd"],
            "idn_d": prof_sums["science"]["idn_d"],
            "oecd_d": prof_sums["science"]["oecd_d"],
        },
    ]

    payload = {
        "fig": fig,
        "profBars": prof_bars,
        "field": field_data,
    }

    compact_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    font_configs = [
        ("ChakraPetch-400.woff2", "Chakra Petch", "400", "woff2", "font/woff2"),
        ("ChakraPetch-600.woff2", "Chakra Petch", "600", "woff2", "font/woff2"),
        ("ChakraPetch-700.woff2", "Chakra Petch", "700", "woff2", "font/woff2"),
        ("JetBrainsMono-400.woff2", "JetBrains Mono", "400", "woff2", "font/woff2"),
        ("JetBrainsMono-500.woff2", "JetBrains Mono", "500", "woff2", "font/woff2"),
    ]

    font_rules = []
    for fname, family, weight, fmt, mime in font_configs:
        fpath = os.path.join(fonts_dir, fname)
        if not os.path.exists(fpath):
            raise RuntimeError(f"Required font file not found: {fpath}")
        with open(fpath, "rb") as ff:
            b64_data = base64.b64encode(ff.read()).decode("ascii")
        rule = (
            f"@font-face {{\n"
            f"  font-family: '{family}';\n"
            f"  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            f"  font-display: swap;\n"
            f"  src: url('data:{mime};base64,{b64_data}') format('{fmt}');\n"
            f"}}"
        )
        font_rules.append(rule)

    fonts_css = "\n".join(font_rules)

    if "/*__FONTS__*/" not in template_str:
        raise RuntimeError("Placeholder /*__FONTS__*/ not found in hub.template.html")

    html_rendered = template_str.replace("/*__FONTS__*/", fonts_css)

    if "/*__DATA__*/ {}" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/ {}", compact_json)
    elif "/*__DATA__*/" in html_rendered:
        html_rendered = html_rendered.replace("/*__DATA__*/", compact_json)
    else:
        raise RuntimeError("Placeholder /*__DATA__*/ not found in hub.template.html")

    html_rendered = re.sub(
        r"[ \t]*<!--SEO:BEGIN-->.*?<!--SEO:END-->",
        seo_block,
        html_rendered,
        count=1,
        flags=re.DOTALL,
    )

    final_html = stamp_line + html_rendered

    page_begin = final_html.count("<!--SEO:BEGIN-->")
    page_end = final_html.count("<!--SEO:END-->")
    if page_begin != 1 or page_end != 1:
        raise RuntimeError(f"Emitted page must contain exactly 1 SEO marker pair (found begin={page_begin}, end={page_end})")
    if final_html.index("<!--SEO:BEGIN-->") >= final_html.index("<!--SEO:END-->"):
        raise RuntimeError("Emitted page <!--SEO:BEGIN--> must precede <!--SEO:END-->")

    with open(output_path, "w", encoding="utf-8") as out_f:
        out_f.write(final_html)

    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'  <url><loc>{SITE}/</loc><lastmod>{root_lastmod}</lastmod></url>',
    ]
    plate_lastmod_map = {
        f"{SITE}/gempa/": gempa_lastmod,
        f"{SITE}/pisa/": pisa_lastmod,
    }
    for href, _ in sorted(plate_entries, key=lambda x: x[0]):
        loc = f"{SITE}{href}" if href.startswith("/") else href
        lastmod = plate_lastmod_map.get(loc, root_lastmod)
        sitemap_lines.append(f'  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>')

    sitemap_lines.append('</urlset>\n')
    sitemap_content = "\n".join(sitemap_lines)

    with open(sitemap_path, "w", encoding="utf-8") as sm_f:
        sm_f.write(sitemap_content)

    print(f"Emitted {len(fig)} fig entries and {len(prof_bars)} proficiency bars")
    print(f"Emitted SEO region and sitemap.xml (lastmod: root={root_lastmod}, gempa={gempa_lastmod}, pisa={pisa_lastmod})")
    print(stamp_line.strip())


if __name__ == "__main__":
    main()
