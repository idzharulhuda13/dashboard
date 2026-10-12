#!/usr/bin/env python3
"""Probe browser acceptance test for Indonesia earthquake data story.

Renders index.html in a real Chromium browser via Playwright across multiple
fresh browser contexts (viewports: 390x844 and 1440x900) and reports measured defects.

Exit codes:
0 = all checks passed
1 = at least one check failed
2 = the browser could not be started, so no check was performed

Checks:
a. network: offline proof (only file: and data: URLs allowed)
b. console: zero console errors and page errors
c. horizontal overflow: scrollWidth <= clientWidth and no rect.right > innerWidth + 1
d. fonts: document.fonts.check for Archivo, Archivo Black, IBM Plex Mono
e. contrast: WCAG 4.5:1 (normal) / 3:1 (large/bold) against effective background
f. keyboard: focus indicators (>= 3:1 contrast), reading tab order, 0 SVG epicentre focusable nodes
g. svg labels: no SVG chart label overlap >= 5px on both axes
h. copy: 0 occurrences of U+2014 in rendered body
i. screenshots: full-page screenshots to shots/gempa-<w>.png
"""

import os
import sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.path.exists(os.path.join(ROOT, "index.html")) and os.path.exists(os.path.join(os.getcwd(), "index.html")):
    ROOT = os.getcwd()
INDEX_PATH = os.path.join(ROOT, "index.html")
INDEX_URL = f"file://{os.path.abspath(INDEX_PATH)}"
SHOTS_DIR = os.path.join(ROOT, "shots")

VIEWPORTS = [
    (390, 844),
    (1440, 900),
]


def handle_infra_failure(exc: Exception):
    msg = " ".join(str(exc).split())
    print(f"INFRA-FAIL: {type(exc).__name__}: {msg}", file=sys.stderr)
    sys.exit(2)


def run_checks_for_viewport(browser, width: int, height: int):
    results = []
    # Fresh browser context per viewport (never shared or cache-warm)
    try:
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
    except Exception as exc:
        handle_infra_failure(exc)

    requests = []
    console_errors = []
    page_errors = []

    page.on("request", lambda req: requests.append(req.url))
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(INDEX_URL)
    page.wait_for_load_state("load")

    vp_tag = f"{width}x{height}"

    # a. network
    offending_requests = [
        u for u in requests
        if not (u.startswith("file:") or u.startswith("data:"))
    ]
    if offending_requests:
        results.append({
            "check": "network",
            "pass": False,
            "message": f"non-offline URLs requested: {', '.join(offending_requests)}",
            "details": offending_requests,
        })
    else:
        results.append({
            "check": "network",
            "pass": True,
            "message": f"all {len(requests)} requests start with file: or data:",
            "details": [],
        })

    # b. console
    total_console = len(console_errors) + len(page_errors)
    if total_console > 0:
        err_msgs = [f"console: {e}" for e in console_errors] + [f"pageerror: {e}" for e in page_errors]
        results.append({
            "check": "console",
            "pass": False,
            "message": f"{total_console} console/page error(s)",
            "details": err_msgs,
            "count": total_console,
        })
    else:
        results.append({
            "check": "console",
            "pass": True,
            "message": "0 console and page errors",
            "details": [],
            "count": 0,
        })

    # c. horizontal overflow
    overflow_res = page.evaluate("""() => {
        const doc = document.documentElement;
        const scrollW = doc.scrollWidth;
        const clientW = doc.clientWidth;
        const winW = window.innerWidth;
        const scrollOverflow = scrollW > clientW;
        const offending = [];
        let excludedScrollableCount = 0;

        function hasScrollableAncestor(el) {
            let parent = el.parentElement;
            while (parent) {
                const s = window.getComputedStyle(parent);
                if (s.overflowX === 'auto' || s.overflowX === 'scroll') {
                    return true;
                }
                parent = parent.parentElement;
            }
            return false;
        }

        const all = document.querySelectorAll('*');
        for (const el of all) {
            const rect = el.getBoundingClientRect();
            if (rect.right > winW + 1) {
                if (hasScrollableAncestor(el)) {
                    excludedScrollableCount++;
                } else {
                    const cls = typeof el.className === 'string' ? el.className : (el.className?.baseVal || '');
                    offending.push({
                        tag: el.tagName.toLowerCase(),
                        className: cls,
                        right: rect.right
                    });
                }
            }
        }
        return {
            scrollW,
            clientW,
            winW,
            scrollOverflow,
            offending,
            excludedScrollableCount
        };
    }""")

    scroll_overflow = overflow_res["scrollOverflow"]
    offending_elements = overflow_res["offending"]
    excluded_scrollable = overflow_res["excludedScrollableCount"]
    overflow_defects = len(offending_elements) + (1 if scroll_overflow else 0)

    if scroll_overflow or offending_elements:
        parts = []
        if scroll_overflow:
            parts.append(f"scrollWidth ({overflow_res['scrollW']}px) > clientWidth ({overflow_res['clientW']}px)")
        if offending_elements:
            parts.append(f"{len(offending_elements)} element(s) exceed innerWidth ({overflow_res['winW']}px) by >1px")
        
        detail_lines = [
            f"<{item['tag']}" + (f' class="{item["className"]}"' if item["className"] else "") + f"> right={item['right']:.1f}px"
            for item in offending_elements
        ]
        detail_lines.append(f"{excluded_scrollable} element(s) inside a scroll region excluded")
        results.append({
            "check": "horizontal overflow",
            "pass": False,
            "message": "; ".join(parts),
            "details": detail_lines,
            "count": overflow_defects,
            "excluded_scrollable": excluded_scrollable,
        })
    else:
        results.append({
            "check": "horizontal overflow",
            "pass": True,
            "message": f"scrollWidth ({overflow_res['scrollW']}px) <= clientWidth ({overflow_res['clientW']}px) and 0 elements exceed innerWidth",
            "details": [],
            "count": 0,
            "excluded_scrollable": excluded_scrollable,
        })

    # d. fonts
    page.evaluate("document.fonts.ready")
    fonts_res = page.evaluate("""() => {
        return {
            'Archivo': document.fonts.check('16px "Archivo"'),
            'Archivo Black': document.fonts.check('16px "Archivo Black"'),
            'IBM Plex Mono': document.fonts.check('16px "IBM Plex Mono"')
        };
    }""")
    failing_fonts = [f for f, ok in fonts_res.items() if not ok]
    if failing_fonts:
        results.append({
            "check": "fonts",
            "pass": False,
            "message": f"document.fonts.check failed for: {', '.join(failing_fonts)}",
            "details": failing_fonts,
        })
    else:
        results.append({
            "check": "fonts",
            "pass": True,
            "message": "document.fonts.check verified for Archivo, Archivo Black, IBM Plex Mono",
            "details": [],
        })

    # e. contrast
    contrast_res = page.evaluate("""() => {
        function parseRgb(str) {
            if (!str) return null;
            const m = str.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
            if (!m) return null;
            return {
                r: parseInt(m[1]),
                g: parseInt(m[2]),
                b: parseInt(m[3]),
                a: m[4] !== undefined ? parseFloat(m[4]) : 1.0
            };
        }

        function lum(c) {
            const a = [c.r, c.g, c.b].map(v => {
                v /= 255;
                return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
            });
            return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
        }

        function contrastRatio(c1, c2) {
            const l1 = lum(c1);
            const l2 = lum(c2);
            return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        }

        function getEffectiveBg(el) {
            let curr = el;
            while (curr) {
                const s = window.getComputedStyle(curr);
                const bg = parseRgb(s.backgroundColor);
                if (bg && bg.a > 0) {
                    return { color: bg, str: s.backgroundColor };
                }
                curr = curr.parentElement;
            }
            return { color: { r: 8, g: 13, b: 30, a: 1.0 }, str: 'rgb(8, 13, 30)' };
        }

        const skipTags = new Set(['SCRIPT', 'STYLE', 'TITLE', 'DESC']);
        const failing = [];
        const all = document.querySelectorAll('*');

        for (const el of all) {
            if (skipTags.has(el.tagName)) continue;

            let hasDirectText = false;
            for (const child of el.childNodes) {
                if (child.nodeType === Node.TEXT_NODE && child.textContent.trim().length > 0) {
                    hasDirectText = true;
                    break;
                }
            }
            const isSvgText = el.tagName.toLowerCase() === 'text';
            if (!hasDirectText && !isSvgText) continue;
            if (isSvgText && (!el.textContent || !el.textContent.trim())) continue;

            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) continue;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') continue;

            let colorStr = style.color;
            if (isSvgText && style.fill && style.fill !== 'none') {
                colorStr = style.fill;
            }
            const fg = parseRgb(colorStr);
            const bg = getEffectiveBg(el);
            if (!fg || !bg) continue;

            const ratio = contrastRatio(fg, bg.color);
            const fontSize = parseFloat(style.fontSize) || 16;
            const isBold = parseInt(style.fontWeight) >= 700 || style.fontWeight === 'bold';
            const isLarge = fontSize >= 18 || (fontSize >= 14 && isBold);
            const threshold = isLarge ? 3.0 : 4.5;

            if (ratio < threshold) {
                const cls = typeof el.className === 'string' ? el.className : (el.className?.baseVal || '');
                failing.push({
                    tag: el.tagName.toLowerCase(),
                    className: cls,
                    text: el.textContent.trim().slice(0, 30),
                    fg: colorStr,
                    bg: bg.str,
                    ratio: ratio.toFixed(2),
                    threshold: threshold.toFixed(1)
                });
            }
        }
        return failing;
    }""")

    if contrast_res:
        detail_lines = [
            f"<{item['tag']}" + (f' class="{item["className"]}"' if item["className"] else "") +
            f"> text='{item['text']}' fg={item['fg']} bg={item['bg']} ratio={item['ratio']} (threshold {item['threshold']})"
            for item in contrast_res
        ]
        results.append({
            "check": "contrast",
            "pass": False,
            "message": f"{len(contrast_res)} element(s) below WCAG contrast threshold",
            "details": detail_lines,
            "count": len(contrast_res),
        })
    else:
        results.append({
            "check": "contrast",
            "pass": True,
            "message": "all rendered text elements satisfy WCAG thresholds (>= 4.5:1 normal, >= 3:1 large/bold)",
            "details": [],
            "count": 0,
        })

    # f. keyboard
    keyboard_res = page.evaluate("""() => {
        function parseRgb(str) {
            if (!str) return null;
            const m = str.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/);
            if (!m) return null;
            return {
                r: parseInt(m[1]),
                g: parseInt(m[2]),
                b: parseInt(m[3]),
                a: m[4] !== undefined ? parseFloat(m[4]) : 1.0
            };
        }

        function lum(c) {
            const a = [c.r, c.g, c.b].map(v => {
                v /= 255;
                return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
            });
            return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
        }

        function contrastRatio(c1, c2) {
            const l1 = lum(c1);
            const l2 = lum(c2);
            return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        }

        function getEffectiveBg(el) {
            let curr = el;
            while (curr) {
                const s = window.getComputedStyle(curr);
                const bg = parseRgb(s.backgroundColor);
                if (bg && bg.a > 0) return bg;
                curr = curr.parentElement;
            }
            return { r: 8, g: 13, b: 30, a: 1.0 };
        }

        const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), details > summary:first-of-type';
        const allCandidates = Array.from(document.querySelectorAll(focusableSelector));
        const focusable = allCandidates.filter(el => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
        });

        const epicentreSvg = document.querySelector('#chart-field svg, .field-svg');
        const epicentreFocusable = epicentreSvg ? Array.from(epicentreSvg.querySelectorAll('*')).filter(el => {
            return el.matches(focusableSelector) || (el.hasAttribute('tabindex') && el.tabIndex >= 0);
        }) : [];

        let tabOrderDisrupted = false;
        for (const el of focusable) {
            if (el.tabIndex > 0) {
                tabOrderDisrupted = true;
                break;
            }
        }

        const failures = [];
        if (epicentreFocusable.length > 0) {
            failures.push(`SVG epicentre field contributes ${epicentreFocusable.length} focusable nodes (expected 0)`);
        }
        if (tabOrderDisrupted) {
            failures.push('tab order does not match reading order (positive tabIndex detected)');
        }

        for (const el of focusable) {
            const preStyle = window.getComputedStyle(el);
            const preOutline = `${preStyle.outlineStyle} ${preStyle.outlineWidth} ${preStyle.outlineColor}`;
            const preShadow = preStyle.boxShadow;

            el.focus();

            const postStyle = window.getComputedStyle(el);
            const postOutline = `${postStyle.outlineStyle} ${postStyle.outlineWidth} ${postStyle.outlineColor}`;
            const postShadow = postStyle.boxShadow;

            const hasOutlineChange = postOutline !== preOutline && parseFloat(postStyle.outlineWidth) > 0 && postStyle.outlineStyle !== 'none';
            const hasShadowChange = postShadow !== preShadow && postShadow !== 'none';

            if (!hasOutlineChange && !hasShadowChange) {
                failures.push(`element <${el.tagName.toLowerCase()}> lacks visible focus indicator`);
                continue;
            }

            const bg = getEffectiveBg(el);
            let indicatorColor = null;
            if (hasOutlineChange) {
                indicatorColor = parseRgb(postStyle.outlineColor);
            } else if (hasShadowChange) {
                indicatorColor = parseRgb(postStyle.color);
            }
            if (indicatorColor) {
                const ratio = contrastRatio(indicatorColor, bg);
                if (ratio < 3.0) {
                    failures.push(`element <${el.tagName.toLowerCase()}> focus indicator contrast is ${ratio.toFixed(2)} (< 3:1)`);
                }
            }
        }

        return {
            focusableCount: focusable.length,
            epicentreCount: epicentreFocusable.length,
            failures
        };
    }""")

    keyboard_failures = keyboard_res["failures"]
    if keyboard_failures:
        results.append({
            "check": "keyboard",
            "pass": False,
            "message": f"{len(keyboard_failures)} focus defect(s)",
            "details": keyboard_failures,
            "count": len(keyboard_failures),
        })
    else:
        results.append({
            "check": "keyboard",
            "pass": True,
            "message": f"reading tab order verified, focus indicators verified ({keyboard_res['focusableCount']} focusable elements), SVG epicentre has 0 focusable nodes",
            "details": [],
            "count": 0,
        })

    # g. svg labels
    svg_labels_res = page.evaluate("""() => {
        const svgs = document.querySelectorAll('svg');
        const overlaps = [];
        for (const svg of svgs) {
            const texts = Array.from(svg.querySelectorAll('text'));
            const boxes = texts.map(t => ({
                el: t,
                text: t.textContent.trim(),
                rect: t.getBoundingClientRect()
            })).filter(b => b.rect.width > 0 && b.rect.height > 0);

            for (let i = 0; i < boxes.length; i++) {
                for (let j = i + 1; j < boxes.length; j++) {
                    const b1 = boxes[i].rect;
                    const b2 = boxes[j].rect;
                    const xOverlap = Math.min(b1.right, b2.right) - Math.max(b1.left, b2.left);
                    const yOverlap = Math.min(b1.bottom, b2.bottom) - Math.max(b1.top, b2.top);
                    if (xOverlap >= 5 && yOverlap >= 5) {
                        overlaps.push({
                            text1: boxes[i].text,
                            text2: boxes[j].text,
                            xOverlap: xOverlap.toFixed(1),
                            yOverlap: yOverlap.toFixed(1)
                        });
                    }
                }
            }
        }
        return overlaps;
    }""")

    if svg_labels_res:
        detail_lines = [
            f"'{item['text1']}' and '{item['text2']}' overlap by {item['xOverlap']}px x {item['yOverlap']}px"
            for item in svg_labels_res
        ]
        results.append({
            "check": "svg labels",
            "pass": False,
            "message": f"{len(svg_labels_res)} overlapping text label pair(s) in chart SVGs",
            "details": detail_lines,
        })
    else:
        results.append({
            "check": "svg labels",
            "pass": True,
            "message": "0 chart SVG label bounding boxes overlap by >= 5px on both axes",
            "details": [],
        })

    # h. copy
    em_dash_count = page.evaluate("""() => {
        const text = document.body.innerText || document.body.textContent || '';
        let count = 0;
        for (let i = 0; i < text.length; i++) {
            if (text.charCodeAt(i) === 0x2014) {
                count++;
            }
        }
        return count;
    }""")

    if em_dash_count > 0:
        results.append({
            "check": "copy",
            "pass": False,
            "message": f"found {em_dash_count} occurrence(s) of U+2014 in body text (must be 0)",
            "details": [f"count: {em_dash_count}"],
        })
    else:
        results.append({
            "check": "copy",
            "pass": True,
            "message": "0 occurrences of U+2014 in rendered body text",
            "details": [],
        })

    # i. screenshots
    os.makedirs(SHOTS_DIR, exist_ok=True)
    shot_filename = f"gempa-{width}.png"
    shot_path = os.path.join(SHOTS_DIR, shot_filename)
    page.screenshot(path=shot_path, full_page=True)

    if os.path.exists(shot_path) and os.path.getsize(shot_path) > 0:
        results.append({
            "check": "screenshots",
            "pass": True,
            "message": f"saved full page screenshot to shots/{shot_filename} ({os.path.getsize(shot_path)} bytes)",
            "details": [],
        })
    else:
        results.append({
            "check": "screenshots",
            "pass": False,
            "message": f"failed to save screenshot to shots/{shot_filename}",
            "details": [],
        })

    context.close()
    return results


def main():
    if not os.path.exists(INDEX_PATH):
        print(f"ERROR: {INDEX_PATH} does not exist", file=sys.stderr)
        sys.exit(1)

    total_checks = 0
    passed_checks = 0
    total_overflow = 0
    total_contrast = 0
    total_console = 0
    total_focus = 0

    all_passed = True

    startup_phase = True
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                handle_infra_failure(exc)
            startup_phase = False

            for width, height in VIEWPORTS:
                vp_label = f"{width}x{height}"
                results = run_checks_for_viewport(browser, width, height)

                for r in results:
                    total_checks += 1
                    status = "PASS" if r["pass"] else "FAIL"
                    if r["pass"]:
                        passed_checks += 1
                    else:
                        all_passed = False

                    print(f"{status} [{vp_label}] {r['check']}: {r['message']}")
                    if not r["pass"] and r.get("details"):
                        for detail in r["details"]:
                            print(f"  - {detail}")

                    # Accumulate defect totals
                    if r["check"] == "horizontal overflow":
                        total_overflow += r.get("count", 0)
                    elif r["check"] == "contrast":
                        total_contrast += r.get("count", 0)
                    elif r["check"] == "console":
                        total_console += r.get("count", 0)
                    elif r["check"] == "keyboard":
                        total_focus += r.get("count", 0)

            browser.close()
    except Exception as exc:
        if startup_phase:
            handle_infra_failure(exc)
        raise

    # Final summary block
    print(
        f"VIEWPORTS {len(VIEWPORTS)} / CHECKS {passed_checks}/{total_checks} / "
        f"OVERFLOW {total_overflow} / CONTRAST {total_contrast} / "
        f"CONSOLE {total_console} / FOCUS {total_focus}"
    )

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
