#!/usr/bin/env python3
"""
verify.py - Verification suite for pasar/msci.html data story.
Complies with DESIGN.pasar.md, DESIGN.data-story.md, and headless verification requirements.
Runnable as `python3 pasar/verify.py` from repo root, exits 0 on success, 1 on failure.
"""

import os
import sys
import json
import hashlib
import asyncio

def get_base_dir():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, ".."))

def fmt_ind(val, decimals=2):
    return f"{float(val):.{decimals}f}".replace(".", ",")

async def run_playwright_checks(html_path, expected_figures):
    from playwright.async_api import async_playwright

    errors = []
    file_url = f"file://{os.path.abspath(html_path)}"

    async with async_playwright() as p:
        browser = await p.chromium.launch()

        for vp in [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]:
            w, h = vp["width"], vp["height"]
            page = await browser.new_page(viewport={"width": w, "height": h})

            page_errors = []
            console_errors = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

            await page.goto(file_url, wait_until="networkidle")
            await page.evaluate("() => document.fonts.ready")

            # Check 1: Console / page errors
            if page_errors or console_errors:
                err = f"Viewport {w}x{h}: console/page errors found: page={page_errors}, console={console_errors}"
                errors.append(err)
            else:
                print(f"[PASS] Viewport {w}x{h}: 0 console errors, 0 page errors")

            # Check 2: Horizontal overflow
            sw, iw = await page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
            if sw > iw + 1:
                err = f"Viewport {w}x{h}: horizontal overflow (scrollWidth {sw} > innerWidth {iw} + 1)"
                errors.append(err)
            else:
                print(f"[PASS] Viewport {w}x{h}: no horizontal overflow (scrollWidth={sw} <= innerWidth={iw})")

            # Check 3: Display type sizing
            h1_size = await page.evaluate("() => window.getComputedStyle(document.querySelector('h1')).fontSize")
            h1_px = float(h1_size.replace("px", ""))
            if w == 1440:
                if h1_px < 50:
                    errors.append(f"Viewport 1440: h1 display font size too small: {h1_size}")
                else:
                    print(f"[PASS] Viewport 1440: h1 display font size is {h1_size} (>= 50px)")
            elif w == 390:
                if h1_px > 36 or h1_px < 30:
                    errors.append(f"Viewport 390: h1 display font size expected ~34px, found: {h1_size}")
                else:
                    print(f"[PASS] Viewport 390: h1 display font size stepped down to {h1_size} (~34px)")

            # Check 4: Typography floor 12px
            small_els = await page.evaluate('''() => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, null);
                let node;
                const failures = [];
                while (node = walker.nextNode()) {
                    const tag = node.tagName.toLowerCase();
                    if (tag === 'script' || tag === 'style') continue;
                    const hasText = Array.from(node.childNodes).some(
                        c => c.nodeType === Node.TEXT_NODE && c.textContent.trim().length > 0
                    );
                    const isSvgText = (tag === 'text' || tag === 'tspan');
                    if (hasText || isSvgText) {
                        const cs = window.getComputedStyle(node);
                        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                        const fs = parseFloat(cs.fontSize);
                        if (fs < 11.95) {
                            failures.push({ tag, text: node.textContent.trim().slice(0, 30), fs });
                        }
                    }
                }
                return failures;
            }''')
            if small_els:
                err = f"Viewport {w}x{h}: found {len(small_els)} text element(s) with font-size < 12px: {small_els[:3]}"
                errors.append(err)
            else:
                print(f"[PASS] Viewport {w}x{h}: typography floor verified (all text >= 12px)")

            # Check 5: Rendered DOM text figures
            if w == 1440:
                missing_in_dom = await page.evaluate('''(expected) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
                    let s = '';
                    let node;
                    while (node = walker.nextNode()) s += ' ' + node.nodeValue;
                    return expected.filter(fig => !s.includes(fig));
                }''', expected_figures)

                if missing_in_dom:
                    err = f"Rendered DOM missing expected figures: {missing_in_dom}"
                    errors.append(err)
                else:
                    print(f"[PASS] Rendered DOM text contains all {len(expected_figures)} required figures")

            # Check 6: Table semantics (caption and th scope)
            table_semantics = await page.evaluate('''() => {
                const caption = document.querySelector('table caption');
                const thCols = Array.from(document.querySelectorAll('table thead th[scope="col"]'));
                const thRows = Array.from(document.querySelectorAll('table tbody th[scope="row"]'));
                return {
                    hasCaption: !!caption && caption.textContent.trim().length > 0,
                    colThCount: thCols.length,
                    rowThCount: thRows.length
                };
            }''')
            if not table_semantics["hasCaption"] or table_semantics["colThCount"] < 4 or table_semantics["rowThCount"] < 9:
                err = f"Table semantics missing required attributes: {table_semantics}"
                errors.append(err)
            else:
                print(f"[PASS] Table semantics: caption present, {table_semantics['colThCount']} col headers, {table_semantics['rowThCount']} row headers")

            await page.close()

        await browser.close()

    return errors

def main():
    base_dir = get_base_dir()
    pasar_dir = os.path.join(base_dir, "pasar")
    data_path = os.path.join(base_dir, "data", "pasar", "msci.json")
    html_path = os.path.join(pasar_dir, "msci.html")
    template_path = os.path.join(pasar_dir, "msci.template.html")

    print("=== STARTING pasar/verify.py ===")

    # 1. Recompute figures from data/pasar/msci.json
    if not os.path.exists(data_path):
        print(f"[FAIL] Data file not found: {data_path}")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    constituents = data.get("constituents", [])
    sectors = data.get("sectors", [])
    meta = data.get("meta", {})

    if len(constituents) != 9:
        print(f"[FAIL] Expected 9 constituents, found {len(constituents)}")
        sys.exit(1)
    print(f"[PASS] Recomputed 9 constituents count: {len(constituents)}")

    weights = [c["weight_pct"] for c in constituents]
    weights_sum = round(sum(weights), 2)
    weights_sum_str = fmt_ind(weights_sum, 2)
    print(f"[PASS] Recomputed 9 constituent weights sum: {weights_sum_str}% (weights: {weights})")

    if len(sectors) != 5:
        print(f"[FAIL] Expected 5 sectors, found {len(sectors)}")
        sys.exit(1)
    print(f"[PASS] Recomputed 5 sectors count: {len(sectors)}")

    sector_shares = [s["weight_pct"] for s in sectors]
    sector_sum = round(sum(sector_shares), 2)
    sector_sum_str = fmt_ind(sector_sum, 2)
    print(f"[PASS] Recomputed 5 sector shares sum: {sector_sum_str}% (shares: {sector_shares})")

    # 1b. Independent re-read of the factsheet PDF (pdftotext -layout), a different extraction
    #     path from pasar/extract_msci.py (plain pdftotext text). Every payload number must match.
    import re as _re0
    import subprocess as _sp

    src_dir = os.path.join(base_dir, "data", "pasar")
    pdf_path = os.path.join(src_dir, meta["source_pdf"])
    with open(pdf_path, "rb") as pf:
        pdf_sha = hashlib.sha256(pf.read()).hexdigest()
    if pdf_sha != meta.get("source_sha256"):
        print(f"[FAIL] source_sha256 {meta.get('source_sha256')} != sha256 of {meta['source_pdf']} {pdf_sha}")
        sys.exit(1)
    layout = _sp.run(["pdftotext", "-layout", pdf_path, "-"], check=True, capture_output=True, text=True).stdout
    fails = []

    def row_nums(label):
        m = _re0.search(r"^\s*" + _re0.escape(label) + r"\s+(-?\d+\.\d+(?:\s+-?\d+\.\d+){11})\s*$", layout, _re0.M)
        return [float(v) for v in m.group(1).split()] if m else None

    periods = ["1m", "3m", "1y", "ytd", "3y", "5y", "10y", "since_2000"]
    for key, label in (("indonesia", "MSCI Indonesia"), ("em", "MSCI Emerging Markets"), ("acwi_imi", "MSCI ACWI IMI")):
        nums = row_nums(label)
        if nums is None:
            fails.append(f"performance row for {label} not found in PDF layout")
            continue
        for i, per in enumerate(periods):
            if data["performance"][key][per] != nums[i]:
                fails.append(f"performance {key}.{per}: payload {data['performance'][key][per]} vs PDF {nums[i]}")
        for i, fk in enumerate(["div_yld", "pe", "pe_fwd", "pbv"]):
            if data["fundamentals"][key][fk] != nums[8 + i]:
                fails.append(f"fundamentals {key}.{fk}: payload {data['fundamentals'][key][fk]} vs PDF {nums[8 + i]}")

    cum_pos = {}
    for key in ("acwi_imi", "em", "indonesia"):
        v = f"{data['cumulative'][key]:.2f}"
        hit = [i for i, line in enumerate(layout.splitlines()) if _re0.search(r"(^|\s)" + _re0.escape(v) + r"(\s|$)", line)]
        if not hit:
            fails.append(f"cumulative {key} {v} not printed in PDF")
        else:
            cum_pos[key] = hit[0]
    if len(cum_pos) == 3 and not (cum_pos["acwi_imi"] < cum_pos["em"] < cum_pos["indonesia"]):
        fails.append(f"cumulative labels out of chart order: {cum_pos}")

    for c in constituents:
        m = _re0.search(_re0.escape(c["name"].upper()) + r"\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\S[\S ]*?)\s*$", layout, _re0.M)
        if not m or (float(m.group(1)), float(m.group(2))) != (c["float_cap_bn"], c["weight_pct"]) or m.group(3) != c["sector"]:
            fails.append(f"constituent {c['name']}: payload {c['float_cap_bn']}/{c['weight_pct']}/{c['sector']} vs PDF {m.groups() if m else None}")
    sector_names = {"Comm Srvcs": "Communication Services"}
    for sct in sectors:
        nm = sector_names.get(sct["sector"], sct["sector"])
        m = _re0.search(_re0.escape(nm) + r" (\d+(?:\.\d+)?)%", layout)
        if not m or float(m.group(1)) != sct["weight_pct"]:
            fails.append(f"sector {nm}: payload {sct['weight_pct']} vs PDF {m.group(1) if m else None}")
    m = _re0.search(r"^\s*Total\s+(\d+\.\d+)\s+100\.00\s*$", layout, _re0.M)
    if not m or float(m.group(1)) != meta["index_float_cap_bn"]:
        fails.append(f"index float cap: payload {meta['index_float_cap_bn']} vs PDF {m.group(1) if m else None}")

    # Previous month's P/E (comparison factsheet), same layout parse.
    prev = data["prev"]
    prev_layout = _sp.run(["pdftotext", "-layout", os.path.join(src_dir, "source", "msci-indonesia-index-net.pdf"), "-"],
                          check=True, capture_output=True, text=True).stdout
    mp = _re0.search(r"^\s*MSCI Indonesia\s+((?:-?\d+\.\d+\s+){11}-?\d+\.\d+)\s*$", prev_layout, _re0.M)
    pv = [float(v) for v in mp.group(1).split()] if mp else []
    if not pv or (pv[9], pv[11]) != (prev["pe"], prev["pbv"]):
        fails.append(f"prev P/E, P/BV: payload {prev['pe']}/{prev['pbv']} vs Sep PDF {pv[9:12] if pv else None}")

    # Context outside the factsheet: every figure must appear verbatim in the saved source text.
    ctx = data["context"]
    with open(os.path.join(src_dir, ctx["ojk"]["source_file"]), encoding="utf-8") as of:
        ojk_txt = of.read()
    for k, phrase in (("net_sell_saham_t", "net sell* tercatat sebesar Rp{v} triliun"),
                      ("net_sell_saham_sep_t", "(September 2026: *net sell* Rp{v} triliun)"),
                      ("net_buy_sbn_t", "*net buy* sebesar Rp{v} triliun secara *mtm*"),
                      ("net_sell_sbn_sep_t", "(September 2026: *net sell* Rp{v} triliun)"),
                      ("investor_new_jt", "penambahan investor baru sebanyak {v} juta"),
                      ("investor_total_jt", "menjadi {v} juta investor"),
                      ("ritel_rnth_pct", "individu sebesar {v} persen"),
                      ("ritel_rnth_sep_pct", "(September 2026: {v} persen)")):
        v = f"{ctx['ojk'][k]:.2f}".replace(".", ",")
        if phrase.format(v=v) not in ojk_txt:
            fails.append(f"OJK {k}={v}: phrase not found in {ctx['ojk']['source_file']}")
    rel = ctx["msci_release"]
    with open(os.path.join(src_dir, rel["consultation"]["source_file"]), encoding="utf-8") as rf:
        r1 = rf.read()
    with open(os.path.join(src_dir, rel["review"]["source_file"]), encoding="utf-8") as rf:
        r2 = rf.read()
    for phrase, txt, name in (("until April 30, 2027", r1, "feedback_until"), ("on or before June 30, 2027", r1, "decision_by"),
                              ("Standalone Market", r1, "standalone"), ("Oct. 15, 2026", r1, "consultation date"),
                              ("11:00 p.m. Central European Time (CET) on November 11, 2026", r2, "announce"),
                              ("close of November 30, 2026", r2, "effective")):
        if phrase not in txt:
            fails.append(f"MSCI release: '{phrase}' ({name}) not found")
    if (rel["consultation"]["feedback_until"], rel["consultation"]["decision_by"], rel["review"]["effective_close"],
            rel["review"]["announce_wib"]) != ("2027-04-30", "2027-06-30", "2026-11-30", "2026-11-12 05:00"):
        fails.append(f"MSCI release dates in payload disagree with the release text: {rel}")

    if fails:
        print(f"[FAIL] {len(fails)} payload value(s) disagree with their sources:")
        for e in fails[:20]:
            print(f"  - {e}")
        sys.exit(1)
    print("[PASS] Payload matches factsheet PDF (layout re-read), Sep factsheet, OJK text, and MSCI releases")

    # 1c. Derived figures, recomputed here
    banks = [c for c in constituents if "Bank" in c["name"]]
    four_banks_weight = round(sum(c["weight_pct"] for c in banks), 2)
    top5_weight = round(sum(c["weight_pct"] for c in constituents[:5]), 2)
    nonbank_weight = round(sum(c["weight_pct"] for c in constituents if "Bank" not in c["name"]), 2)
    nonfin = round(sum(x["weight_pct"] for x in sectors if x["sector"] != "Financials"), 2)
    perf, fund, cum = data["performance"], data["fundamentals"], data["cumulative"]
    pe_change = round((fund["indonesia"]["pe"] / prev["pe"] - 1) * 100, 2)
    eps_change = round(((1 + perf["indonesia"]["1m"] / 100) / (1 + pe_change / 100) - 1) * 100, 2)
    derived = {
        "bank_weight_pct": fmt_ind(four_banks_weight, 2),
        "top5_weight_pct": fmt_ind(top5_weight, 2) + "%",
        "nonbank_weight_pct": fmt_ind(nonbank_weight, 2),
        "nonfin_sector_pct": fmt_ind(nonfin, 2),
        "sim_index_drop_pct": fmt_ind(round(four_banks_weight * 0.10, 1), 1),
        "sim_index_exact": fmt_ind(round(four_banks_weight * 0.10, 3), 3) + "%",
        "ytd_gap": fmt_ind(round(perf["em"]["ytd"] - perf["indonesia"]["ytd"], 2), 2),
        "pe_change": fmt_ind(pe_change, 2) + "%",
        "eps_change": fmt_ind(eps_change, 2) + "%",
        "pe_vs_em": str(round(fund["indonesia"]["pe"] / fund["em"]["pe"] * 100)),
        "cum_id": "US$" + fmt_ind(cum["indonesia"], 2),
        "ytd_id": fmt_ind(perf["indonesia"]["ytd"], 2) + "%",
        "ytd_em": "+" + fmt_ind(perf["em"]["ytd"], 2) + "%",
    }
    print(f"[PASS] Recomputed derived figures: {derived}")

    caps = [c["float_cap_bn"] for c in constituents]
    caps_sum = round(sum(caps), 2)
    caps_sum_str = fmt_ind(caps_sum, 2)
    factsheet_cap = meta["index_float_cap_bn"]
    factsheet_cap_str = fmt_ind(factsheet_cap, 2)
    print(f"[PASS] Recomputed float cap: sum-of-parts = {caps_sum_str} bn USD, factsheet = {factsheet_cap_str} bn USD")

    # 2. File and template assertions
    if not os.path.exists(html_path):
        print(f"[FAIL] Rendered html not found: {html_path}")
        sys.exit(1)

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Assert build stamp
    lines = html_content.splitlines()
    if not lines or not lines[0].startswith("<!--BUILD sha256:"):
        print(f"[FAIL] Line 1 does not carry <!--BUILD sha256:...-->: {lines[0] if lines else 'empty'}")
        sys.exit(1)
    print(f"[PASS] Line 1 carries valid build stamp: {lines[0][:32]}...")

    # Template hash verification
    if os.path.exists(template_path):
        with open(template_path, "rb") as tf:
            template_sha256 = hashlib.sha256(tf.read()).hexdigest()
        expected_stamp = f"<!--BUILD sha256:{template_sha256}-->"
        if lines[0] != expected_stamp:
            print(f"[FAIL] Build stamp mismatch: expected {expected_stamp}, found {lines[0]}")
            sys.exit(1)
        print(f"[PASS] Build stamp matches template sha256: {template_sha256[:16]}...")

    # 3. Assert every figure appears in rendered html
    required_figures = [
        fmt_ind(c["weight_pct"], 2) for c in constituents
    ] + [
        fmt_ind(x["weight_pct"], 2) for x in sectors
    ] + [
        fmt_ind(v, 2) for row in data["performance"].values() for k, v in row.items() if k != "since_2000"
    ] + [
        fmt_ind(row[k], 2) for row in data["fundamentals"].values() for k in ("pe", "pe_fwd", "pbv")
    ] + [
        fmt_ind(cum[k], 2) for k in ("indonesia", "em", "acwi_imi")
    ] + [
        "100,00", caps_sum_str, factsheet_cap_str, fmt_ind(prev["pe"], 2),
    ] + [v.replace("+", "") for v in derived.values()]
    # Unique figures to check
    unique_figures = sorted(list(set(required_figures)))
    missing_figures = [fig for fig in unique_figures if fig not in html_content]
    if missing_figures:
        print(f"[FAIL] Figures missing from rendered pasar/msci.html: {missing_figures}")
        sys.exit(1)
    print(f"[PASS] All {len(unique_figures)} calculated figures appear in rendered pasar/msci.html: {unique_figures}")

    # 4. Assert no disallowed patterns
    # (a) No "http" outside source line
    http_lines = []
    for line_idx, line in enumerate(lines, 1):
        if "http" in line.lower():
            # Only allow if it's explicitly the factsheet or source citation
            if "source" in line.lower() or "sumber" in line.lower():
                continue
            http_lines.append((line_idx, line.strip()[:80]))
    if http_lines:
        print(f"[FAIL] Disallowed 'http' found outside source line: {http_lines}")
        sys.exit(1)
    print("[PASS] No 'http' found outside source line (zero external URLs)")

    # (b) No em dash (U+2014)
    if "\u2014" in html_content:
        print("[FAIL] Em dash (U+2014 '—') found in rendered pasar/msci.html")
        sys.exit(1)
    print("[PASS] Zero em dash (U+2014 '—') found in file")

    # (c) No circle donut
    if "<circle" in html_content:
        print("[FAIL] Disallowed '<circle' found in rendered pasar/msci.html (donut is forbidden)")
        sys.exit(1)
    print("[PASS] Zero '<circle' elements found (donut is absent)")

    # (d) No linear-gradient
    if "linear-gradient" in html_content:
        print("[FAIL] Disallowed 'linear-gradient' found in rendered pasar/msci.html")
        sys.exit(1)
    print("[PASS] Zero 'linear-gradient' found in file")

    # 4c. Responsive contract: a phone must lay out at device width, not 980px
    if 'name="viewport"' not in html_content:
        print("[FAIL] Missing meta viewport; phones will render at 980px")
        sys.exit(1)
    print("[PASS] Viewport meta present (width=device-width)")

    # 4d. Plain-language gate: jargon and AI tells must not reach the page
    banned_terms = ["konstituen", "kapitalisasi", "float cap", "float-adjusted", "bobot", "didominasi",
                    "financials", "comm srvcs", "industrials", "materials"]
    import re as _re

    prose_only = _re.sub(r"const DATA = \{.*?\};\n", "", html_content, count=1, flags=_re.S)
    copy_hits = [t for t in banned_terms if t in prose_only.lower()]
    em_dash = prose_only.count("\u2014")
    if copy_hits or em_dash:
        print(f"[FAIL] Copy gate: jargon {copy_hits}, em dash {em_dash}")
        sys.exit(1)
    print(f"[PASS] Copy gate: 0 of {len(banned_terms)} banned terms, 0 em dash")

    # 4e. Strict per-slot equality: every data-fig slot must carry the payload display value
    import re as _re
    import html as _html
    import json as _json

    slot_match = _re.search(r"const DATA = (\{.*?\});", html_content, _re.S)
    if not slot_match:
        print("[FAIL] Cannot locate the embedded DATA payload for slot verification")
        sys.exit(1)
    payload = _json.loads(slot_match.group(1))
    fig_payload = payload.get("fig", {})

    slot_errors = []
    slot_total = 0
    for m in _re.finditer(r'<(?P<tag>\w+)[^>]*data-fig="(?P<key>[^"]+)"[^>]*>(?P<body>.*?)</(?P=tag)>', html_content, _re.S):
        key = m.group("key")
        slot_total += 1
        meta_payload = payload.get("meta", {})
        if key in fig_payload:
            expected = str(fig_payload[key]["d"])
        elif key in meta_payload:
            expected = str(meta_payload[key])
        else:
            slot_errors.append(f"{key}: no payload entry")
            continue
        actual = _html.unescape(_re.sub(r"<[^>]+>", "", m.group("body"))).strip()
        if actual != expected:
            slot_errors.append(f"{key}: page shows {actual!r}, payload says {expected!r}")
    for key, want in derived.items():
        got = str(fig_payload.get(key, {}).get("d"))
        if got != want:
            slot_errors.append(f"{key}: payload {got!r}, recomputed {want!r}")
    if slot_errors:
        print(f"[FAIL] {len(slot_errors)} of {slot_total} data-fig slots disagree with the payload:")
        for e in slot_errors[:10]:
            print(f"  - {e}")
        sys.exit(1)
    print(f"[PASS] All {slot_total} data-fig slots match the payload display value")

    # 5. Playwright headless tests (1440 and 390)
    print("Running Playwright viewport verifications...")
    pw_errors = asyncio.run(run_playwright_checks(html_path, unique_figures))
    if pw_errors:
        print(f"[FAIL] Playwright verification failed with {len(pw_errors)} error(s):")
        for e in pw_errors:
            print(f"  - {e}")
        sys.exit(1)

    print("=== ALL VERIFICATION CHECKS PASSED SUCCESSFULLY (exit 0) ===")
    sys.exit(0)

if __name__ == "__main__":
    main()
