#!/usr/bin/env python3
"""Open Graph preview image renderer and acceptance gate.

Generates 1200x630 social preview PNG images for hub, gempa, and pisa pages using
Playwright sync API, saving to assets/og/<slug>.png and writing sidecar
assets/og/<slug>.srcsha. Validates PNGs with pure Python stdlib decoder against
C8 gate bands.

Exit codes:
  0 = ok (all requested images exist and pass gate bands)
  1 = render or band failure
  2 = browser / font infrastructure failure
"""

import argparse
import hashlib
import os
import struct
import sys
import zlib

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except Exception as _import_exc:
    sync_playwright = None
    PlaywrightTimeoutError = Exception
    _import_error = str(_import_exc)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [
    ("hub", "index.html"),
    ("gempa", "gempa/index.html"),
    ("pisa", "pisa/index.html"),
    ("pisa-explorer", "pisa-explorer/index.html"),
]

SPECS = {
    "hub": "hub|viewport|1200x630",
    "gempa": "gempa|card|left400|map-right",
    "pisa": "pisa|viewport|1200x630",
    "pisa-explorer": "pisa-explorer|viewport|1200x630",
}


def spec_line(slug: str) -> str:
    """Return a stable single-line description of that slug's capture spec."""
    return SPECS.get(slug, f"{slug}|viewport|1200x630")


def compute_src_sha(html_bytes: bytes, slug: str) -> str:
    """Compute sha256(html_bytes + b'\\0' + spec_line)."""
    return hashlib.sha256(html_bytes + b"\0" + spec_line(slug).encode("utf-8")).hexdigest()


def decode_png(png_bytes: bytes):
    """Decode PNG image using Python standard library only (struct + zlib + scanline unfilter)."""
    if len(png_bytes) < 8 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid PNG signature")

    pos = 8
    ihdr_data = None
    idat_chunks = []

    while pos < len(png_bytes):
        if pos + 8 > len(png_bytes):
            raise ValueError("Truncated PNG chunk header")
        length, = struct.unpack(">I", png_bytes[pos:pos + 4])
        chunk_type = png_bytes[pos + 4:pos + 8]
        chunk_data = png_bytes[pos + 8:pos + 8 + length]
        if pos + 12 + length > len(png_bytes):
            raise ValueError("Truncated PNG chunk data")
        pos += 12 + length

        if chunk_type == b"IHDR":
            ihdr_data = chunk_data
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if not ihdr_data:
        raise ValueError("Missing IHDR chunk")

    width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr_data)
    if bit_depth != 8:
        raise ValueError(f"Unsupported bit depth: {bit_depth}")
    if interlace != 0:
        raise ValueError("Interlaced PNG not supported")

    if color_type == 2:    # RGB
        bpp = 3
    elif color_type == 6:  # RGBA
        bpp = 4
    elif color_type == 0:  # Grayscale
        bpp = 1
    elif color_type == 4:  # Grayscale + Alpha
        bpp = 2
    else:
        raise ValueError(f"Unsupported color type: {color_type}")

    decomp = zlib.decompress(b"".join(idat_chunks))
    line_bytes = width * bpp
    stride = line_bytes + 1
    expected_decomp_len = height * stride
    if len(decomp) != expected_decomp_len:
        raise ValueError(f"Decompressed length mismatch: {len(decomp)} != {expected_decomp_len}")

    prior = bytearray(line_bytes)
    rows = []

    for y in range(height):
        offset = y * stride
        ftype = decomp[offset]
        filt_row = decomp[offset + 1:offset + 1 + line_bytes]
        recon = bytearray(line_bytes)

        if ftype == 0:  # None
            recon[:] = filt_row
        elif ftype == 1:  # Sub
            for x in range(bpp):
                recon[x] = filt_row[x]
            for x in range(bpp, line_bytes):
                recon[x] = (filt_row[x] + recon[x - bpp]) & 0xff
        elif ftype == 2:  # Up
            for x in range(line_bytes):
                recon[x] = (filt_row[x] + prior[x]) & 0xff
        elif ftype == 3:  # Average
            for x in range(bpp):
                recon[x] = (filt_row[x] + (prior[x] >> 1)) & 0xff
            for x in range(bpp, line_bytes):
                recon[x] = (filt_row[x] + ((recon[x - bpp] + prior[x]) >> 1)) & 0xff
        elif ftype == 4:  # Paeth
            for x in range(bpp):
                recon[x] = (filt_row[x] + prior[x]) & 0xff
            for x in range(bpp, line_bytes):
                a = recon[x - bpp]
                b = prior[x]
                c = prior[x - bpp]
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                recon[x] = (filt_row[x] + pr) & 0xff
        else:
            raise ValueError(f"Unknown filter type: {ftype}")

        prior = recon
        rows.append(recon)

    return width, height, color_type, bpp, rows


def verify_og_bands(png_path: str, sidecar_path: str, expected_sha: str, slug: str = None):
    """Validate image and sidecar against C8 gate bands."""
    if not os.path.exists(sidecar_path):
        print(f"BAND-FAIL: sidecar {sidecar_path} missing", file=sys.stderr)
        sys.exit(1)
    with open(sidecar_path, "r", encoding="utf-8") as sf:
        sidecar_sha = sf.read().strip()
    if sidecar_sha != expected_sha:
        print(f"BAND-FAIL: sidecar sha {sidecar_sha} != expected {expected_sha}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(png_path):
        print(f"BAND-FAIL: png {png_path} missing", file=sys.stderr)
        sys.exit(1)
    size = os.path.getsize(png_path)
    if not (15000 <= size <= 300000):
        print(f"BAND-FAIL: png size {size} not in [15000, 300000]", file=sys.stderr)
        sys.exit(1)

    with open(png_path, "rb") as f:
        png_data = f.read()

    try:
        width, height, color_type, bpp, rows = decode_png(png_data)
    except Exception as exc:
        print(f"BAND-FAIL: failed to decode PNG {png_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    if width != 1200 or height != 630:
        print(f"BAND-FAIL: dimensions {width}x{height} != 1200x630", file=sys.stderr)
        sys.exit(1)

    # Step-4 sample: sample every 4th pixel along both axes
    colors = set()
    lums = []
    gempa_ink_samples = 0
    hero_bg = (168, 63, 20)
    slot_bg = (143, 52, 16)

    for y in range(0, height, 4):
        row = rows[y]
        for x in range(0, width, 4):
            idx = x * bpp
            if bpp >= 3:
                r, g, b = row[idx], row[idx + 1], row[idx + 2]
            else:
                r = g = b = row[idx]
            colors.add((r, g, b))
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            lums.append(lum)
            if slug == "gempa" and x >= 480:
                if (r, g, b) != hero_bg and (r, g, b) != slot_bg:
                    gempa_ink_samples += 1

    if len(colors) < 64:
        print(f"BAND-FAIL: distinct RGB colours on step-4 sample: {len(colors)} < 64", file=sys.stderr)
        sys.exit(1)

    lum_range = max(lums) - min(lums)
    if lum_range < 60.0:
        print(f"BAND-FAIL: luminance range: {lum_range:.1f} < 60.0", file=sys.stderr)
        sys.exit(1)

    if slug == "gempa" and gempa_ink_samples < 400:
        print(f"BAND-FAIL: gempa right-hand ink samples on step-4: {gempa_ink_samples} < 400", file=sys.stderr)
        sys.exit(1)


GEMPA_CARD_CSS = """
.nav-home, #loader, #err-box,
#band-province, #band-depth, #band-mag, #band-decade, #band-live, #band-footer {
  display: none !important;
}

html, body {
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  width: 1200px !important;
  height: 630px !important;
}

.dashboard-root {
  margin: 0 !important;
  padding: 0 !important;
  max-width: 1200px !important;
  width: 1200px !important;
  height: 630px !important;
}

#band-hero {
  box-sizing: border-box !important;
  width: 1200px !important;
  height: 630px !important;
  padding: 40px !important;
  display: grid !important;
  grid-template-columns: 400px 1fr !important;
  grid-template-rows: auto 1fr !important;
  column-gap: 40px !important;
}

.band-subtitle,
#scale-hero,
.stat-cell-dominant,
.field-note,
#scale-field-key,
#field-detail,
.stat-meta {
  display: none !important;
}

.hero-header-grid {
  grid-column: 1 !important;
  grid-row: 1 !important;
  display: block !important;
  margin: 0 !important;
  padding: 0 !important;
}

h1.band-headline {
  margin: 0 0 16px 0 !important;
  padding: 0 !important;
}

.hero-stats {
  grid-column: 1 !important;
  grid-row: 2 !important;
  margin: 0 !important;
  padding: 0 !important;
  display: block !important;
}

.hero-stats-sub {
  display: flex !important;
  flex-direction: column !important;
  gap: 8px !important;
  margin: 0 !important;
  padding: 0 !important;
}

.stat-cell-sub {
  display: flex !important;
  flex-direction: row !important;
  justify-content: space-between !important;
  align-items: baseline !important;
  padding: 6px 0 !important;
}

.stat-cell-sub .stat-value {
  margin: 0 !important;
}

#chart-field {
  grid-column: 2 !important;
  grid-row: 1 / -1 !important;
  margin: 0 !important;
  align-self: center !important;
  width: 100% !important;
}
"""


def render_page(browser, slug: str, html_path: str, png_path: str):
    """Render page with Playwright following C7 renderer contract."""
    page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
    file_url = f"file://{os.path.abspath(html_path)}"

    try:
        page.goto(file_url, wait_until="load")
    except Exception as exc:
        print(f"RENDER-FAIL: goto failed for {html_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    page.add_style_tag(content="*{animation:none!important;transition:none!important;caret-color:transparent!important}")

    page.evaluate("""() => {
        document.querySelectorAll('[aria-live]').forEach(el => el.setAttribute('aria-live', 'off'));
        const loader = document.getElementById('loader');
        if (loader) {
            loader.style.display = 'none';
        }
    }""")

    try:
        page.wait_for_function('document.fonts.status === "loaded"', timeout=5000)
    except PlaywrightTimeoutError as exc:
        print(f"INFRA-FAIL: font loading timed out (5000ms): {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"INFRA-FAIL: font status check failed: {exc}", file=sys.stderr)
        sys.exit(2)

    page.wait_for_timeout(300)

    if slug == "gempa":
        page.add_style_tag(content=GEMPA_CARD_CSS)
        field_box = page.locator("#chart-field").bounding_box()
        if not field_box:
            print(f"RENDER-FAIL: #chart-field not found on {html_path}", file=sys.stderr)
            sys.exit(1)
        fx, fy, fw, fh = field_box["x"], field_box["y"], field_box["width"], field_box["height"]
        if not (fx >= 0 and fy >= 0 and (fx + fw) <= 1200 and (fy + fh) <= 630 and fw > 0 and fh > 0):
            print(
                f"RENDER-FAIL: #chart-field bounding box ({fx}, {fy}, {fw}, {fh}) not inside 0..1200 x 0..630 frame",
                file=sys.stderr,
            )
            sys.exit(1)

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)

    try:
        page.screenshot(path=png_path)
    except Exception as exc:
        print(f"RENDER-FAIL: screenshot failed for {html_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description="Open Graph image renderer and gate.")
    parser.add_argument("--only", choices=["hub", "gempa", "pisa", "pisa-explorer"], help="Only process the given slug")
    args = parser.parse_args()

    targets = TARGETS
    if args.only:
        targets = [t for t in TARGETS if t[0] == args.only]

    og_dir = os.path.join(ROOT, "assets", "og")
    os.makedirs(og_dir, exist_ok=True)

    plan = []
    needs_generation = []

    for slug, rel_path in targets:
        html_path = os.path.join(ROOT, rel_path)
        if not os.path.exists(html_path):
            print(f"RENDER-FAIL: source page {html_path} does not exist", file=sys.stderr)
            sys.exit(1)

        with open(html_path, "rb") as f:
            html_bytes = f.read()
        src_sha = compute_src_sha(html_bytes, slug)

        png_path = os.path.join(og_dir, f"{slug}.png")
        sidecar_path = os.path.join(og_dir, f"{slug}.srcsha")

        skip = False
        if os.path.exists(sidecar_path) and os.path.exists(png_path):
            with open(sidecar_path, "r", encoding="utf-8") as sf:
                existing_sha = sf.read().strip()
            if existing_sha == src_sha:
                skip = True

        plan.append({
            "slug": slug,
            "html_path": html_path,
            "png_path": png_path,
            "sidecar_path": sidecar_path,
            "src_sha": src_sha,
            "skip": skip,
        })
        if not skip:
            needs_generation.append(plan[-1])

    if needs_generation:
        if sync_playwright is None:
            print(f"INFRA-FAIL: playwright could not be imported: {_import_error}", file=sys.stderr)
            sys.exit(2)
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch()
                except Exception as exc:
                    print(f"INFRA-FAIL: could not launch browser: {exc}", file=sys.stderr)
                    sys.exit(2)

                try:
                    for item in needs_generation:
                        render_page(browser, item["slug"], item["html_path"], item["png_path"])
                        with open(item["sidecar_path"], "w", encoding="utf-8") as sf:
                            sf.write(f"{item['src_sha']}\n")
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            print(f"INFRA-FAIL: playwright timeout: {exc}", file=sys.stderr)
            sys.exit(2)
        except Exception as exc:
            # Re-raise or exit with appropriate code if not already exited
            print(f"RENDER-FAIL: unexpected error during generation: {exc}", file=sys.stderr)
            sys.exit(1)

    # Verify gate bands and report
    for item in plan:
        verify_og_bands(item["png_path"], item["sidecar_path"], item["src_sha"], item["slug"])
        status = "skipped" if item["skip"] else "written"
        png_bytes = os.path.getsize(item["png_path"])
        print(f"{item['slug']} png={png_bytes} sha={item['src_sha'][:8]} {status}")

    sys.exit(0)


if __name__ == "__main__":
    main()
