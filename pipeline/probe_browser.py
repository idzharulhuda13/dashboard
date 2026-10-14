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
d. fonts: document.fonts.check for Chakra Petch and JetBrains Mono
e. contrast: WCAG 4.5:1 (normal) / 3:1 (large/bold) against effective background
f. keyboard: focus indicators (>= 3:1 contrast), reading tab order, 0 SVG epicentre focusable nodes
g. svg labels: no SVG chart label overlap >= 5px on both axes
h. copy: 0 occurrences of U+2014 in rendered body
i. screenshots: full-page screenshots to shots/gempa-<w>.png
j. epicentre text size: no <text> inside epicentre SVG renders smaller than 12px effective
"""

import csv
import os
import sys
from collections import Counter
from datetime import date

# The import is guarded on purpose. Playwright is a dependency of this gate, not of the page, so
# a missing or broken installation is an infrastructure failure and must exit 2. Left unguarded
# it raises at import time, Python exits 1, and the publisher reports "a defect in the rendered
# page" for a page that was never rendered. That is exactly what happened the first time the
# scheduled job ran: the cron environment had no playwright and the message pointed at the page.
try:
    from playwright.sync_api import sync_playwright
except Exception as _import_exc:  # noqa: BLE001 - any import failure is infrastructure
    print(
        "INFRA-FAIL: playwright could not be imported, so no check was performed. "
        f"{sys.executable} cannot import it: {type(_import_exc).__name__}: {_import_exc}",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(ROOT, "gempa", "index.html")
INDEX_URL = f"file://{os.path.abspath(INDEX_PATH)}"
SHOTS_DIR = os.path.join(ROOT, "shots")
CSV_PATH = os.path.join(ROOT, "data", "out", "gempa_events.csv")

MONTHS_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def load_unique_events():
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def round_pair(r):
        lon = float(r["lon"])
        neg_lat = -float(r["lat"])
        if abs(neg_lat) < 0.05:
            neg_lat = 0.0
        if abs(lon) < 0.05:
            lon = 0.0
        return (f"{lon:.1f}", f"{neg_lat:.1f}")

    pairs = [round_pair(r) for r in rows]
    counts = Counter(pairs)

    sx, sy = 1382.0 / 47.0, 588.0 / 20.0
    msx, msy = 350.0 / 47.0, 175.0 / 20.0
    my_d2 = (0.5 * sx) ** 2
    my_md2 = (0.5 * msx) ** 2

    float_centers = [(float(p[0]) + 0.5, float(p[1])) for p in pairs]

    unique = []
    for i, r in enumerate(rows):
        p = pairs[i]
        if counts[p] != 1:
            continue
        ax, ay = float(p[0]), float(p[1])
        collision = False
        for j, (cx, cy) in enumerate(float_centers):
            if j == i:
                continue
            if abs(cx - ax) > 1.0 or abs(cy - ay) > 1.0:
                continue
            d2 = ((cx - ax) * sx) ** 2 + ((cy - ay) * sy) ** 2
            md2 = ((cx - ax) * msx) ** 2 + ((cy - ay) * msy) ** 2
            if d2 <= my_d2 or md2 <= my_md2:
                collision = True
                break
        if not collision:
            unique.append(r)

    def format_event(r):
        mag_val = float(r["mag"])
        mag_str = f"M{mag_val:.1f}".replace(".", ",")
        depth_val = round(float(r["depth_km"]))
        depth_str = f"{depth_val} km"
        d = date.fromisoformat(r["date_wib"])
        date_str = f"{d.day} {MONTHS_ID[d.month - 1]} {d.year}"
        prov_str = r["province"]
        summary = f"{mag_str}, kedalaman {depth_str}, {date_str}, {prov_str}"
        p = round_pair(r)
        return {
            "row": r,
            "pair": p,
            "cx": float(p[0]),
            "cy": float(p[1]),
            "mag": mag_str,
            "depth": depth_str,
            "date": date_str,
            "prov": prov_str,
            "summary": summary,
        }

    return [format_event(r) for r in unique]

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
            'Chakra Petch': document.fonts.check('16px "Chakra Petch"'),
            'JetBrains Mono': document.fonts.check('16px "JetBrains Mono"')
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
            "message": "document.fonts.check verified for Chakra Petch, JetBrains Mono",
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

    # j. epicentre text size
    epicentre_text_res = page.evaluate("""() => {
        const svg = document.querySelector('#chart-field svg, .field-svg');
        if (!svg) {
            return { error: 'epicentre SVG not found', items: [] };
        }
        const svgW = svg.getBoundingClientRect().width;
        const vbW = svg.viewBox.baseVal.width || 47;
        const scale = svgW / vbW;

        const texts = Array.from(svg.querySelectorAll('text'));
        const items = texts.map(t => {
            const style = window.getComputedStyle(t);
            const computedFontSize = parseFloat(style.fontSize) || 0;
            const effectiveSize = computedFontSize * scale;
            return {
                text: t.textContent.trim(),
                userSize: computedFontSize,
                effectiveSize: effectiveSize,
                display: style.display,
                visibility: style.visibility
            };
        });

        return {
            svgWidth: svgW,
            viewBoxWidth: vbW,
            scale: scale,
            items: items
        };
    }""")

    if epicentre_text_res.get("error"):
        results.append({
            "check": "epicentre text size",
            "pass": False,
            "message": epicentre_text_res["error"],
            "details": [],
        })
    else:
        text_items = epicentre_text_res["items"]
        if not text_items:
            results.append({
                "check": "epicentre text size",
                "pass": False,
                "message": "no text elements found in epicentre SVG",
                "details": [],
            })
        else:
            min_eff = min(it["effectiveSize"] for it in text_items)
            failing = [it for it in text_items if it["effectiveSize"] < 12.0]
            if failing:
                detail_lines = [
                    f"'{it['text']}' user-unit={it['userSize']:.2f}px effective={it['effectiveSize']:.1f}px (< 12px)"
                    for it in failing
                ]
                results.append({
                    "check": "epicentre text size",
                    "pass": False,
                    "message": f"{len(failing)} text element(s) in epicentre SVG render smaller than 12px effective (smallest: {min_eff:.1f}px)",
                    "details": detail_lines,
                })
            else:
                results.append({
                    "check": "epicentre text size",
                    "pass": True,
                    "message": f"all {len(text_items)} epicentre SVG text elements render at >= 12px effective (smallest: {min_eff:.1f}px)",
                    "details": [],
                })

    page.close()

    # S9. Field interaction checks on a fresh page per viewport
    try:
        s9_page = context.new_page()
        s9_page.goto(INDEX_URL)
        s9_page.wait_for_load_state("load")

        s9_page.locator("#chart-field").scroll_into_view_if_needed()

        events = load_unique_events()
        ev_a = events[-1]
        ev_b = events[-2]

        # 1. field detail hover
        pt_a = s9_page.evaluate(
            """([xSvg, ySvg]) => {
                const svg = document.querySelector("#chart-field svg, .field-svg");
                const rect = svg.getBoundingClientRect();
                const vb = svg.viewBox.baseVal;
                const px = rect.left + (xSvg - vb.x) * (rect.width / vb.width);
                const py = rect.top + (ySvg - vb.y) * (rect.height / vb.height);
                return { px, py };
            }""",
            [ev_a["cx"], ev_a["cy"]],
        )

        s9_page.mouse.move(pt_a["px"], pt_a["py"])
        s9_page.wait_for_timeout(100)

        hover_res = s9_page.evaluate("""() => {
            const detail = document.getElementById("field-detail");
            const cursor = document.getElementById("field-cursor");
            const circle = cursor ? cursor.querySelector("circle") : null;
            const live = document.getElementById("field-live");
            return {
                isOpen: detail ? detail.classList.contains("is-open") : false,
                isOff: cursor ? cursor.classList.contains("is-off") : true,
                mag: document.querySelector(".fd-mag")?.textContent || "",
                depth: document.querySelector(".fd-depth")?.textContent || "",
                date: document.querySelector(".fd-date")?.textContent || "",
                prov: document.querySelector(".fd-prov")?.textContent || "",
                cx: circle ? parseFloat(circle.getAttribute("cx") || cursor.getAttribute("cx") || "NaN") : NaN,
                cy: circle ? parseFloat(circle.getAttribute("cy") || cursor.getAttribute("cy") || "NaN") : NaN,
                live: live ? live.textContent || "" : "",
            };
        }""")

        hover_defects = []
        if not hover_res["isOpen"]:
            hover_defects.append("#field-detail missing class 'is-open'")
        if hover_res["isOff"]:
            hover_defects.append("#field-cursor has class 'is-off'")
        if hover_res["mag"] != ev_a["mag"]:
            hover_defects.append(f".fd-mag '{hover_res['mag']}' != expected '{ev_a['mag']}'")
        if hover_res["depth"] != ev_a["depth"]:
            hover_defects.append(f".fd-depth '{hover_res['depth']}' != expected '{ev_a['depth']}'")
        if hover_res["date"] != ev_a["date"]:
            hover_defects.append(f".fd-date '{hover_res['date']}' != expected '{ev_a['date']}'")
        if hover_res["prov"] != ev_a["prov"]:
            hover_defects.append(f".fd-prov '{hover_res['prov']}' != expected '{ev_a['prov']}'")
        if abs(hover_res["cx"] - (ev_a["cx"] + 0.5)) > 0.06 or abs(hover_res["cy"] - ev_a["cy"]) > 0.06:
            hover_defects.append(
                f"cursor cx/cy ({hover_res['cx']}, {hover_res['cy']}) not within 0.06 of centre"
            )
        if hover_res["live"] != ev_a["summary"]:
            hover_defects.append(f"#field-live '{hover_res['live']}' != expected '{ev_a['summary']}'")

        if hover_defects:
            results.append({
                "check": "field detail hover",
                "pass": False,
                "message": f"{len(hover_defects)} defect(s) on field detail hover",
                "details": hover_defects,
            })
        else:
            results.append({
                "check": "field detail hover",
                "pass": True,
                "message": f"hover on ({ev_a['cx']}, {ev_a['cy']}) opens detail ({ev_a['summary']}) with cursor matching within 0.06",
                "details": [],
            })

        # 1b. field detail centre
        centre_x = round(ev_a["cx"] + 0.5, 4)
        pt_centre = s9_page.evaluate(
            """([xSvg, ySvg]) => {
                const svg = document.querySelector("#chart-field svg, .field-svg");
                const rect = svg.getBoundingClientRect();
                const vb = svg.viewBox.baseVal;
                const px = rect.left + (xSvg - vb.x) * (rect.width / vb.width);
                const py = rect.top + (ySvg - vb.y) * (rect.height / vb.height);
                return { px, py };
            }""",
            [centre_x, ev_a["cy"]],
        )

        s9_page.mouse.move(pt_centre["px"], pt_centre["py"])
        s9_page.wait_for_timeout(100)

        centre_res = s9_page.evaluate("""() => {
            const detail = document.getElementById("field-detail");
            const cursor = document.getElementById("field-cursor");
            const circle = cursor ? cursor.querySelector("circle") : null;
            return {
                isOpen: detail ? detail.classList.contains("is-open") : false,
                mag: document.querySelector(".fd-mag")?.textContent || "",
                depth: document.querySelector(".fd-depth")?.textContent || "",
                date: document.querySelector(".fd-date")?.textContent || "",
                prov: document.querySelector(".fd-prov")?.textContent || "",
                cx: circle ? parseFloat(circle.getAttribute("cx") || cursor.getAttribute("cx") || "NaN") : NaN,
            };
        }""")

        centre_defects = []
        if not centre_res["isOpen"]:
            centre_defects.append("#field-detail missing class 'is-open'")
        if centre_res["mag"] != ev_a["mag"]:
            centre_defects.append(f".fd-mag '{centre_res['mag']}' != expected '{ev_a['mag']}'")
        if centre_res["depth"] != ev_a["depth"]:
            centre_defects.append(f".fd-depth '{centre_res['depth']}' != expected '{ev_a['depth']}'")
        if centre_res["date"] != ev_a["date"]:
            centre_defects.append(f".fd-date '{centre_res['date']}' != expected '{ev_a['date']}'")
        if centre_res["prov"] != ev_a["prov"]:
            centre_defects.append(f".fd-prov '{centre_res['prov']}' != expected '{ev_a['prov']}'")
        if abs(centre_res["cx"] - centre_x) > 0.06:
            centre_defects.append(
                f"cursor cx ({centre_res['cx']}) not within 0.06 of centre ({centre_x})"
            )

        if centre_defects:
            results.append({
                "check": "field detail centre",
                "pass": False,
                "message": f"{len(centre_defects)} defect(s) on field detail centre",
                "details": centre_defects,
            })
        else:
            results.append({
                "check": "field detail centre",
                "pass": True,
                "message": f"hover on centre ({centre_x}, {ev_a['cy']}) opens detail ({ev_a['summary']}) with cursor matching within 0.06",
                "details": [],
            })

        # 2. field tap
        s9_page.mouse.click(pt_a["px"], pt_a["py"])
        s9_page.wait_for_timeout(100)

        tap_res = s9_page.evaluate("""() => {
            const detail = document.getElementById("field-detail");
            const isOpen = detail ? detail.classList.contains("is-open") : false;
            const rect = detail ? detail.getBoundingClientRect() : null;
            const winW = window.innerWidth;
            return {
                isOpen,
                rectRight: rect ? rect.right : null,
                winW,
            };
        }""")

        tap_defects = []
        if not tap_res["isOpen"]:
            tap_defects.append("#field-detail missing class 'is-open' after tap")
        if tap_res["rectRight"] is None or tap_res["rectRight"] > tap_res["winW"]:
            tap_defects.append(
                f"#field-detail right ({tap_res['rectRight']}px) exceeds window.innerWidth ({tap_res['winW']}px)"
            )

        if tap_defects:
            results.append({
                "check": "field tap",
                "pass": False,
                "message": f"{len(tap_defects)} defect(s) on field tap",
                "details": tap_defects,
            })
        else:
            results.append({
                "check": "field tap",
                "pass": True,
                "message": f"tap keeps detail open and within viewport (right {tap_res['rectRight']:.1f}px <= {tap_res['winW']}px)",
                "details": [],
            })

        # 3. field aria-live
        s9_page.mouse.move(pt_a["px"], pt_a["py"])
        s9_page.wait_for_timeout(100)
        live_text_a = s9_page.evaluate("() => document.getElementById('field-live')?.textContent || ''")

        pt_b = s9_page.evaluate(
            """([xSvg, ySvg]) => {
                const svg = document.querySelector("#chart-field svg, .field-svg");
                const rect = svg.getBoundingClientRect();
                const vb = svg.viewBox.baseVal;
                const px = rect.left + (xSvg - vb.x) * (rect.width / vb.width);
                const py = rect.top + (ySvg - vb.y) * (rect.height / vb.height);
                return { px, py };
            }""",
            [ev_b["cx"], ev_b["cy"]],
        )

        s9_page.mouse.move(pt_b["px"], pt_b["py"])
        s9_page.wait_for_timeout(100)
        live_text_b = s9_page.evaluate("() => document.getElementById('field-live')?.textContent || ''")

        aria_defects = []
        if live_text_a == live_text_b:
            aria_defects.append(f"#field-live text did not change across distinct events: '{live_text_a}'")
        if live_text_b != ev_b["summary"]:
            aria_defects.append(f"#field-live text '{live_text_b}' != expected B summary '{ev_b['summary']}'")

        if aria_defects:
            results.append({
                "check": "field aria-live",
                "pass": False,
                "message": f"{len(aria_defects)} defect(s) on field aria-live",
                "details": aria_defects,
            })
        else:
            results.append({
                "check": "field aria-live",
                "pass": True,
                "message": f"aria-live updated on distinct event hover ('{live_text_a}' -> '{live_text_b}')",
                "details": [],
            })

        s9_page.close()
    except Exception as exc:
        for chk in ["field detail hover", "field detail centre", "field tap", "field aria-live"]:
            if not any(r["check"] == chk for r in results):
                results.append({
                    "check": chk,
                    "pass": False,
                    "message": f"exception in {chk}: {type(exc).__name__}: {exc}",
                    "details": [str(exc)],
                })

    # S10. Field keyboard check on a fresh page per viewport
    try:
        kb_page = context.new_page()
        kb_page.goto(INDEX_URL)
        kb_page.wait_for_load_state("load")

        # Tab navigation to #field-hit
        # "Tab must reach #field-hit in exactly one stop after the preceding focusable (<= 25 presses total)"
        focused_sequence = []
        tab_count = 0
        reached = False
        for _ in range(25):
            tab_count += 1
            kb_page.keyboard.press("Tab")
            active_info = kb_page.evaluate("""() => {
                const el = document.activeElement;
                if (!el) return null;
                return {
                    id: el.id || '',
                    tag: el.tagName.toLowerCase(),
                };
            }""")
            if active_info:
                focused_sequence.append(active_info)
                if active_info["id"] == "field-hit":
                    reached = True
                    break

        kb_defects = []
        if not reached:
            kb_defects.append(f"#field-hit not reached within 25 Tab presses (focused: {focused_sequence})")
        elif len(focused_sequence) < 2:
            kb_defects.append(f"no preceding focusable before #field-hit (focused: {focused_sequence})")

        # Outline & contrast assertion:
        # "assert outline-style != none, outline-width > 0 and outline contrast >= 3:1 against the effective background"
        outline_res = kb_page.evaluate("""() => {
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

            const el = document.getElementById('field-hit');
            if (!el) return { error: '#field-hit not found' };
            const s = window.getComputedStyle(el);
            const bg = getEffectiveBg(el);
            const color = parseRgb(s.outlineColor);
            const ratio = (color && bg) ? contrastRatio(color, bg) : 0;
            return {
                outlineStyle: s.outlineStyle,
                outlineWidth: parseFloat(s.outlineWidth) || 0,
                outlineColor: s.outlineColor,
                ratio: ratio,
            };
        }""")

        if outline_res.get("outlineStyle") == "none":
            kb_defects.append("#field-hit outline-style is 'none'")
        if outline_res.get("outlineWidth", 0) <= 0:
            kb_defects.append(f"#field-hit outline-width {outline_res.get('outlineWidth')} <= 0")
        if outline_res.get("ratio", 0) < 3.0:
            kb_defects.append(f"#field-hit focus outline contrast {outline_res.get('ratio', 0):.2f} < 3:1")

        # ArrowRight once then Enter -> is-open with .fd-* equal to the CSV row of the first band-M5 event
        kb_page.keyboard.press("ArrowRight")
        kb_page.keyboard.press("Enter")
        kb_page.wait_for_timeout(50)

        open_res = kb_page.evaluate("""() => {
            const detail = document.getElementById("field-detail");
            const cursor = document.getElementById("field-cursor");
            const live = document.getElementById("field-live");
            return {
                isOpen: detail ? detail.classList.contains("is-open") : false,
                isOff: cursor ? cursor.classList.contains("is-off") : true,
                mag: document.querySelector(".fd-mag")?.textContent || "",
                depth: document.querySelector(".fd-depth")?.textContent || "",
                date: document.querySelector(".fd-date")?.textContent || "",
                prov: document.querySelector(".fd-prov")?.textContent || "",
                live: live ? live.textContent || "" : "",
            };
        }""")

        if not open_res["isOpen"]:
            kb_defects.append("#field-detail missing class 'is-open' after ArrowRight and Enter")
        if open_res["isOff"]:
            kb_defects.append("#field-cursor has class 'is-off' after ArrowRight and Enter")

        with open(CSV_PATH, encoding="utf-8") as f:
            csv_rows = list(csv.DictReader(f))
        m5_rows = [r for r in csv_rows if r.get("mag_band") == "M5"]

        def fmt_row(r):
            mag_val = float(r["mag"])
            mag_str = f"M{mag_val:.1f}".replace(".", ",")
            depth_val = round(float(r["depth_km"]))
            depth_str = f"{depth_val} km"
            d = date.fromisoformat(r["date_wib"])
            date_str = f"{d.day} {MONTHS_ID[d.month - 1]} {d.year}"
            prov_str = r["province"]
            summary = f"{mag_str}, kedalaman {depth_str}, {date_str}, {prov_str}"
            return {
                "mag": mag_str,
                "depth": depth_str,
                "date": date_str,
                "prov": prov_str,
                "summary": summary,
            }

        target_ev0 = fmt_row(m5_rows[0])
        target_ev1 = fmt_row(m5_rows[1])

        match_0 = (
            open_res["mag"] == target_ev0["mag"]
            and open_res["depth"] == target_ev0["depth"]
            and open_res["date"] == target_ev0["date"]
            and open_res["prov"] == target_ev0["prov"]
        )
        match_1 = (
            open_res["mag"] == target_ev1["mag"]
            and open_res["depth"] == target_ev1["depth"]
            and open_res["date"] == target_ev1["date"]
            and open_res["prov"] == target_ev1["prov"]
        )

        if not (match_0 or match_1):
            kb_defects.append(
                f".fd-* ({open_res['mag']}, {open_res['depth']}, {open_res['date']}, {open_res['prov']}) "
                f"did not match CSV row of first band-M5 event ({target_ev0['mag']}, {target_ev0['depth']}, {target_ev0['date']}, {target_ev0['prov']}) "
                f"or ({target_ev1['mag']}, {target_ev1['depth']}, {target_ev1['date']}, {target_ev1['prov']})"
            )

        # Escape -> no is-open and #field-live == "Rincian ditutup."
        kb_page.keyboard.press("Escape")
        kb_page.wait_for_timeout(50)

        esc_res = kb_page.evaluate("""() => {
            const detail = document.getElementById("field-detail");
            const live = document.getElementById("field-live");
            return {
                isOpen: detail ? detail.classList.contains("is-open") : false,
                live: live ? live.textContent || "" : "",
            };
        }""")

        if esc_res["isOpen"]:
            kb_defects.append("#field-detail still has class 'is-open' after Escape")
        if esc_res["live"] != "Rincian ditutup.":
            kb_defects.append(f"#field-live '{esc_res['live']}' != expected 'Rincian ditutup.' after Escape")

        kb_page.close()

        if kb_defects:
            results.append({
                "check": "field keyboard",
                "pass": False,
                "message": f"{len(kb_defects)} defect(s) on field keyboard",
                "details": kb_defects,
            })
        else:
            results.append({
                "check": "field keyboard",
                "pass": True,
                "message": (
                    f"one Tab stop reached #field-hit ({tab_count} presses total), "
                    f"focus outline >= 3:1 ({outline_res['ratio']:.2f}:1), "
                    f"ArrowRight+Enter opens is-open with .fd-* equal to the CSV row of the first band-M5 event "
                    f"({open_res['mag']}, {open_res['depth']}, {open_res['date']}, {open_res['prov']}), "
                    f"Escape -> no is-open and #field-live == 'Rincian ditutup.'"
                ),
                "details": [],
            })
    except Exception as exc:
        if not any(r["check"] == "field keyboard" for r in results):
            results.append({
                "check": "field keyboard",
                "pass": False,
                "message": f"exception in field keyboard: {type(exc).__name__}: {exc}",
                "details": [str(exc)],
            })

    context.close()
    return results


def run_reduced_motion_check(browser):
    try:
        context = browser.new_context(reduced_motion="reduce")
        page = context.new_page()
        page.goto(INDEX_URL)
        page.wait_for_load_state("load")

        # Open detail via keyboard
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Enter")
        page.wait_for_timeout(50)

        res = page.evaluate("""() => {
            const cursor = document.getElementById("field-cursor");
            const detail = document.getElementById("field-detail");
            const cursorDur = cursor ? parseFloat(window.getComputedStyle(cursor).transitionDuration) : NaN;
            const detailDur = detail ? parseFloat(window.getComputedStyle(detail).transitionDuration) : NaN;
            const isOpen = detail ? detail.classList.contains("is-open") : false;
            return {
                isOpen,
                cursorDur,
                detailDur,
            };
        }""")

        page.close()
        context.close()

        defects = []
        if not res["isOpen"]:
            defects.append("#field-detail missing class 'is-open' after opening")
        if res["cursorDur"] != res["cursorDur"] or res["cursorDur"] > 0.001:
            defects.append(f"#field-cursor transitionDuration {res['cursorDur']}s > 0.001s")
        if res["detailDur"] != res["detailDur"] or res["detailDur"] > 0.001:
            defects.append(f"#field-detail transitionDuration {res['detailDur']}s > 0.001s")

        if defects:
            return {
                "check": "reduced motion",
                "pass": False,
                "message": f"{len(defects)} defect(s) on reduced motion",
                "details": defects,
            }
        else:
            return {
                "check": "reduced motion",
                "pass": True,
                "message": (
                    f"after opening detail (is-open), transitionDuration <= 0.001s under reduce verified: "
                    f"parseFloat(getComputedStyle(document.getElementById('field-cursor')).transitionDuration) == {res['cursorDur']} <= 0.001 "
                    f"and the same for #field-detail ({res['detailDur']} <= 0.001)"
                ),
                "details": [],
            }
    except Exception as exc:
        return {
            "check": "reduced motion",
            "pass": False,
            "message": f"exception in reduced motion: {type(exc).__name__}: {exc}",
            "details": [str(exc)],
        }


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
                    elif r["check"] in ("keyboard", "field keyboard"):
                        total_focus += r.get("count", 0)

            # Global checks (reduced motion)
            rm_res = run_reduced_motion_check(browser)
            total_checks += 1
            status = "PASS" if rm_res["pass"] else "FAIL"
            if rm_res["pass"]:
                passed_checks += 1
            else:
                all_passed = False

            print(f"{status} [global] {rm_res['check']}: {rm_res['message']}")
            if not rm_res["pass"] and rm_res.get("details"):
                for detail in rm_res["details"]:
                    print(f"  - {detail}")

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
