#!/usr/bin/env python3
"""Probe for the front door (index.html): does the page actually DRAW, and is it sane?

The shared probe (pipeline_pisa/probe_browser.py) already checks the hub for console errors, overflow,
contrast, keyboard order and em dashes. This one adds the checks only the hub needs: its two data windows
are drawn by JavaScript from the injected payload, so nothing in a static gate can prove they rendered.

Run from the repository root:  python3 pipeline_hub/probe_hub.py
Exit code 0 = every check passed. Any failure exits non-zero and names the check.
"""
import json
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except Exception as exc:  # noqa: BLE001
    print(f"INFRA-FAIL: playwright not importable: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB_PATH = os.path.join(ROOT, "index.html")
VIEWPORTS = [(390, 844), (1440, 900)]


def payload_display_values():
    """Read the display strings straight out of the generated page's payload."""
    with open(HUB_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("const DATA = "):
                data = json.loads(stripped[len("const DATA = "):].rstrip(";"))
                return {k: v["d"] for k, v in data.get("fig", {}).items()}
    raise RuntimeError("payload line not found in index.html")


def check_viewport(browser, url, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_timeout(400)

    out = []
    label = f"{width}x{height}"

    out.append((f"[{label}] console", "PASS" if not errors else "FAIL",
                f"{len(errors)} console/page errors" + (f": {errors[:2]}" if errors else "")))

    overflow = page.evaluate("""() => ({sw: document.documentElement.scrollWidth,
                                        cw: document.documentElement.clientWidth})""")
    ok = overflow["sw"] <= overflow["cw"] + 1
    out.append((f"[{label}] overflow", "PASS" if ok else "FAIL",
                f"scrollWidth {overflow['sw']} vs clientWidth {overflow['cw']}"))

    gempa = page.evaluate("""() => {
        const w = document.querySelector('#window-gempa');
        if (!w) return {found: false};
        const svg = w.querySelector('svg');
        return {found: true, svg: !!svg, paths: svg ? svg.querySelectorAll('path').length : 0,
                text: svg ? svg.querySelectorAll('text').length : 0};
    }""")
    ok = gempa.get("svg") and gempa.get("paths", 0) >= 1 and gempa.get("text", 0) == 0
    out.append((f"[{label}] gempa window", "PASS" if ok else "FAIL",
                f"svg={gempa.get('svg')} paths={gempa.get('paths')} text_nodes={gempa.get('text')}"))

    pisa = page.evaluate("""() => {
        const w = document.querySelector('#window-pisa');
        if (!w) return {found: false};
        const svg = w.querySelector('svg');
        return {found: true, svg: !!svg,
                bars: svg ? svg.querySelectorAll('rect.bar-idn').length : 0,
                ticks: svg ? svg.querySelectorAll('line.tick-oecd, rect.tick-oecd').length : 0};
    }""")
    ok = pisa.get("svg") and pisa.get("bars", 0) >= 3 and pisa.get("ticks", 0) >= 3
    out.append((f"[{label}] pisa window", "PASS" if ok else "FAIL",
                f"svg={pisa.get('svg')} bars={pisa.get('bars')} ticks={pisa.get('ticks')}"))

    expected = payload_display_values()
    shown = page.evaluate("""() => {
        const o = {};
        document.querySelectorAll('span[data-fig]').forEach(s => { o[s.getAttribute('data-fig')] = s.textContent.trim(); });
        return o;
    }""")
    mismatched = {k: (shown.get(k), v) for k, v in expected.items() if shown.get(k) != v}
    out.append((f"[{label}] figures", "PASS" if not mismatched else "FAIL",
                f"{len(expected)} spans, mismatched {mismatched}" if mismatched else f"{len(expected)} spans match the payload"))

    smallest = page.evaluate("""() => {
        let min = 999, who = '';
        document.querySelectorAll('body *').forEach(el => {
            if (!el.childNodes.length) return;
            const hasText = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
            if (!hasText) return;
            const size = parseFloat(getComputedStyle(el).fontSize);
            if (size && size < min) { min = size; who = (el.className || el.tagName).toString().slice(0, 40); }
        });
        return {min: min, who: who};
    }""")
    ok = smallest["min"] >= 12
    out.append((f"[{label}] text size", "PASS" if ok else "FAIL",
                f"smallest {smallest['min']}px ({smallest['who']})"))

    page.close()
    return out


def main():
    url = f"file://{os.path.abspath(HUB_PATH)}"
    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in VIEWPORTS:
            for name, status, message in check_viewport(browser, url, width, height):
                print(f"{status} {name}: {message}")
                if status != "PASS":
                    failures += 1
        browser.close()
    print(f"VIEWPORTS {len(VIEWPORTS)} / FAILURES {failures}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
