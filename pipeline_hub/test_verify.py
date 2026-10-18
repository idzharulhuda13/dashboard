#!/usr/bin/env python3
"""Test harness proving pipeline_hub/verify.py can fail.

Runs fifteen verification scenarios in an isolated temporary directory:
  a) clean copy exits 0
  b) mutated proficiency value in data/pisa/annex_proficiency.csv exits non-zero
  c) mutated number inside the page's payload exits non-zero
  d) one narrative word changed in the page body (outside the payload) exits non-zero
  e) one data-fig fallback literal changed exits non-zero
  f) CSS rule injected inside the FONTS region exits non-zero
  g) meta name="description" deleted exits non-zero
  h) hub canonical changed to {SITE}/gempa/ exits non-zero
  i) /favicon.ico link dropped exits non-zero
  j) the /pisa/ <url> removed from sitemap.xml exits non-zero
  k) the Sitemap: line removed from robots.txt exits non-zero
  l) one byte of favicon.ico flipped exits non-zero
  m) one ListItem name in the hub JSON-LD mutated exits non-zero
  n) og image size out of band exits non-zero
  o) the map region of the gempa card is blank exits non-zero
"""

import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://dashboard.idzharulhuda.com"


def copy_tree(dst_dir: str):
    os.makedirs(os.path.join(dst_dir, "data", "pisa"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "gempa"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "pisa"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "pipeline_hub"), os.path.join(dst_dir, "pipeline_hub"))
    shutil.copy2(os.path.join(ROOT, "data", "pisa", "annex_proficiency.csv"), os.path.join(dst_dir, "data", "pisa", "annex_proficiency.csv"))
    shutil.copy2(os.path.join(ROOT, "gempa", "data.json"), os.path.join(dst_dir, "gempa", "data.json"))
    shutil.copy2(os.path.join(ROOT, "gempa", "index.html"), os.path.join(dst_dir, "gempa", "index.html"))
    shutil.copy2(os.path.join(ROOT, "pisa", "index.html"), os.path.join(dst_dir, "pisa", "index.html"))
    # Halaman turunan yang bukan pelat: ikut disalin supaya penemuan URL di salinan bersih
    # (root + setiap direktori ber-index.html) cocok dengan sitemap.xml yang diuji.
    if os.path.isdir(os.path.join(ROOT, "pisa-explorer")):
        shutil.copytree(os.path.join(ROOT, "pisa-explorer"), os.path.join(dst_dir, "pisa-explorer"))
    shutil.copytree(os.path.join(ROOT, "fonts"), os.path.join(dst_dir, "fonts"))
    shutil.copy2(os.path.join(ROOT, "hub.template.html"), os.path.join(dst_dir, "hub.template.html"))
    shutil.copy2(os.path.join(ROOT, "index.html"), os.path.join(dst_dir, "index.html"))
    shutil.copy2(os.path.join(ROOT, "robots.txt"), os.path.join(dst_dir, "robots.txt"))
    shutil.copy2(os.path.join(ROOT, "sitemap.xml"), os.path.join(dst_dir, "sitemap.xml"))

    icon_files = [
        "favicon.ico",
        "favicon.svg",
        "favicon-96.png",
        "apple-touch-icon.png",
    ]
    for fname in icon_files:
        src = os.path.join(ROOT, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst_dir, fname))

    og_src = os.path.join(ROOT, "assets", "og")
    if os.path.exists(og_src):
        dst_og = os.path.join(dst_dir, "assets", "og")
        os.makedirs(os.path.dirname(dst_og), exist_ok=True)
        shutil.copytree(og_src, dst_og, dirs_exist_ok=True)


def run_gate(cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "pipeline_hub/verify.py"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_scenario_a(temp_dir: str):
    proc = run_gate(temp_dir)
    if proc.returncode != 0:
        print(f"Scenario A output:\n{proc.stdout}\n{proc.stderr}")
        raise AssertionError(f"Expected clean copy to exit 0, got {proc.returncode}")
    print("PASS: scenario a: clean copy exits 0")


def test_scenario_b(temp_dir: str):
    prof_csv = os.path.join(temp_dir, "data", "pisa", "annex_proficiency.csv")
    with open(prof_csv, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(csv.DictReader(f, fieldnames=header))

    modified = False
    for r in rows:
        if r.get("subject") == "Indonesia" and r.get("domain") == "mathematics" and r.get("stat") == "level_below_1c":
            r["value"] = "999.0"
            modified = True
            break

    if not modified:
        raise RuntimeError("Failed to locate target row in annex_proficiency.csv")

    with open(prof_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows[1:])

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario B output:\n{proc.stdout}")
        raise AssertionError("Expected scalar modification in annex_proficiency.csv to cause non-zero exit")
    print(f"PASS: scenario b: mutated proficiency value in annex_proficiency.csv exits non-zero (rc={proc.returncode})")


def test_scenario_c(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    prefix = "const DATA = "
    idx = content.find(prefix)
    if idx == -1:
        raise RuntimeError("const DATA not found in index.html")
    start_json = idx + len(prefix)
    end_json = content.find(";\n", start_json)
    if end_json == -1:
        end_json = content.find(";", start_json)

    data = json.loads(content[start_json:end_json])
    data["fig"]["prof_math_idn"]["v"] = 999.0

    mutated_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    mutated_content = content[:start_json] + mutated_json + content[end_json:]

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario C output:\n{proc.stdout}")
        raise AssertionError("Expected mutated number inside payload to cause non-zero exit")
    print(f"PASS: scenario c: mutated number inside payload exits non-zero (rc={proc.returncode})")


def test_scenario_d(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = "Kumpulan cerita data yang dibangun dari data publik."
    if target not in content:
        raise RuntimeError(f"Target phrase {target!r} not found in index.html")
    mutated_content = content.replace(target, "Kumpulan laporan data yang dibangun dari data publik.", 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario D output:\n{proc.stdout}")
        raise AssertionError("Expected narrative prose mutation in index.html to cause non-zero exit")
    print(f"PASS: scenario d: narrative word changed in page body exits non-zero (rc={proc.returncode})")


def test_scenario_e(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '<span data-fig="prof_math_idn">83,1</span>'
    if target not in content:
        raise RuntimeError(f"Target data-fig fallback {target!r} not found in index.html")
    mutated_content = content.replace(target, '<span data-fig="prof_math_idn">99,9</span>', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario E output:\n{proc.stdout}")
        raise AssertionError("Expected data-fig fallback literal mutation in index.html to cause non-zero exit")
    print(f"PASS: scenario e: data-fig fallback literal changed exits non-zero (rc={proc.returncode})")


def test_scenario_f(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = "/*FONTS:BEGIN*/\n"
    if target not in content:
        raise RuntimeError(f"Marker {target!r} not found in index.html")
    mutated_content = content.replace(target, target + "body{display:none}\n", 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario F output:\n{proc.stdout}")
        raise AssertionError("Expected font region CSS injection in index.html to cause non-zero exit")
    print(f"PASS: scenario f: injected CSS in font region exits non-zero (rc={proc.returncode})")


def test_scenario_g(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    mutated_content = re.sub(r'  <meta name="description" content="[^"]*">\n', '', content, count=1)
    if mutated_content == content:
        raise RuntimeError('meta name="description" not found in index.html')

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario G output:\n{proc.stdout}")
        raise AssertionError("Expected deleted meta description in index.html to cause non-zero exit")
    print(f"PASS: scenario g: meta name=\"description\" deleted exits non-zero (rc={proc.returncode})")


def test_scenario_h(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = f'  <link rel="canonical" href="{SITE}/">\n'
    if target not in content:
        raise RuntimeError(f"Target canonical link {target!r} not found in index.html")
    mutated_content = content.replace(target, f'  <link rel="canonical" href="{SITE}/gempa/">\n', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario H output:\n{proc.stdout}")
        raise AssertionError("Expected canonical link change in index.html to cause non-zero exit")
    print(f"PASS: scenario h: hub canonical changed to {SITE}/gempa/ exits non-zero (rc={proc.returncode})")


def test_scenario_i(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '  <link rel="icon" href="/favicon.ico" sizes="32x32">\n'
    if target not in content:
        raise RuntimeError(f"Target favicon link {target!r} not found in index.html")
    mutated_content = content.replace(target, '', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario I output:\n{proc.stdout}")
        raise AssertionError("Expected dropped /favicon.ico link to cause non-zero exit")
    print(f"PASS: scenario i: /favicon.ico link dropped exits non-zero (rc={proc.returncode})")


def test_scenario_j(temp_dir: str):
    sitemap_xml = os.path.join(temp_dir, "sitemap.xml")
    with open(sitemap_xml, "r", encoding="utf-8") as f:
        content = f.read()

    mutated_content = re.sub(r'  <url><loc>[^<]*/pisa/</loc><lastmod>[^<]*</lastmod></url>\n?', '', content, count=1)
    if mutated_content == content:
        raise RuntimeError("/pisa/ <url> entry not found in sitemap.xml")

    with open(sitemap_xml, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario J output:\n{proc.stdout}")
        raise AssertionError("Expected removal of /pisa/ from sitemap.xml to cause non-zero exit")
    print(f"PASS: scenario j: the /pisa/ <url> removed from sitemap.xml exits non-zero (rc={proc.returncode})")


def test_scenario_k(temp_dir: str):
    robots_txt = os.path.join(temp_dir, "robots.txt")
    with open(robots_txt, "r", encoding="utf-8") as f:
        content = f.read()

    mutated_content = re.sub(r'Sitemap: [^\n]*\n?', '', content, count=1)
    if mutated_content == content:
        raise RuntimeError("Sitemap: line not found in robots.txt")

    with open(robots_txt, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario K output:\n{proc.stdout}")
        raise AssertionError("Expected removal of Sitemap line from robots.txt to cause non-zero exit")
    print(f"PASS: scenario k: the Sitemap: line removed from robots.txt exits non-zero (rc={proc.returncode})")


def test_scenario_l(temp_dir: str):
    fav_file = os.path.join(temp_dir, "favicon.ico")
    if os.path.exists(fav_file):
        with open(fav_file, "rb") as f:
            data = bytearray(f.read())
        if data:
            data[0] ^= 0xFF
            with open(fav_file, "wb") as f:
                f.write(data)

    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = 'href="/favicon.ico"'
    if target not in content:
        raise RuntimeError('href="/favicon.ico" not found in index.html')
    mutated_content = content.replace(target, 'href="/favicox.ico"', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario L output:\n{proc.stdout}")
        raise AssertionError("Expected flipped byte in favicon.ico to cause non-zero exit")
    print(f"PASS: scenario l: one byte of favicon.ico flipped exits non-zero (rc={proc.returncode})")


def test_scenario_m(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '"@type":"ListItem","position":1,"name":"'
    idx = content.find(target)
    if idx == -1:
        raise RuntimeError("ListItem position 1 not found in index.html")
    start_name = idx + len(target)
    end_name = content.find('"', start_name)
    if end_name == -1:
        raise RuntimeError("ListItem name end quote not found in index.html")

    mutated_content = content[:start_name] + "Mutated Plate Title" + content[end_name:]

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario M output:\n{proc.stdout}")
        raise AssertionError("Expected JSON-LD ListItem name mutation in index.html to cause non-zero exit")
    print(f"PASS: scenario m: one ListItem name in the hub JSON-LD mutated exits non-zero (rc={proc.returncode})")


def make_synthetic_gempa_blank_map_png(width: int = 1200, height: int = 630) -> bytes:
    hero_bg = (168, 63, 20)
    raw = bytearray()
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            if x < 480:
                r = int(x / 480 * 255)
                g = int(y / height * 255)
                b = 128
                row.extend((r, g, b))
            else:
                row.extend(hero_bg)
        raw.extend(row)

    comp = zlib.compress(raw, level=6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")


def test_scenario_n(temp_dir: str):
    hub_png = os.path.join(temp_dir, "assets", "og", "hub.png")
    if not os.path.exists(hub_png):
        raise RuntimeError(f"Target OG image not found at {hub_png}")
    with open(hub_png, "rb") as f:
        data = f.read()
    if len(data) < 8000:
        raise RuntimeError(f"Target OG image {hub_png} smaller than 8000 bytes ({len(data)} bytes)")
    with open(hub_png, "wb") as f:
        f.write(data[:8000])

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario N output:\n{proc.stdout}")
        raise AssertionError("Expected truncated assets/og/hub.png to cause non-zero exit")
    fail_lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
    if not any("og image" in l.lower() for l in fail_lines):
        print(f"Scenario N output:\n{proc.stdout}")
        raise AssertionError(f"Expected FAIL line mentioning og image, got: {fail_lines}")
    print(f"PASS: scenario n: og image size out of band exits non-zero (rc={proc.returncode})")


def test_scenario_o(temp_dir: str):
    gempa_png = os.path.join(temp_dir, "assets", "og", "gempa.png")
    png_bytes = make_synthetic_gempa_blank_map_png(1200, 630)
    os.makedirs(os.path.dirname(gempa_png), exist_ok=True)
    with open(gempa_png, "wb") as f:
        f.write(png_bytes)

    build_og_path = os.path.join(ROOT, "pipeline", "build_og.py")
    if not os.path.exists(build_og_path):
        raise RuntimeError(f"pipeline/build_og.py not found at {build_og_path}")
    spec = importlib.util.spec_from_file_location("build_og", build_og_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to create module spec for pipeline/build_og.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "spec_line"):
        raise RuntimeError("spec_line function not found in pipeline/build_og.py")

    gempa_html_path = os.path.join(temp_dir, "gempa", "index.html")
    if not os.path.exists(gempa_html_path):
        raise RuntimeError(f"gempa/index.html not found at {gempa_html_path}")
    with open(gempa_html_path, "rb") as f:
        html_bytes = f.read()

    sidecar_sha = hashlib.sha256(html_bytes + b"\0" + mod.spec_line("gempa").encode("utf-8")).hexdigest()
    gempa_srcsha = os.path.join(temp_dir, "assets", "og", "gempa.srcsha")
    with open(gempa_srcsha, "w", encoding="utf-8") as f:
        f.write(f"{sidecar_sha}\n")

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario O output:\n{proc.stdout}")
        raise AssertionError("Expected blank map region in gempa OG image to cause non-zero exit")
    fail_lines = [l for l in proc.stdout.splitlines() if l.startswith("FAIL")]
    if not any("ink samples" in l and "60%" in l for l in fail_lines):
        print(f"Scenario O output:\n{proc.stdout}")
        raise AssertionError(f"Expected FAIL line naming ink samples on right-hand 60%, got: {fail_lines}")
    print(f"PASS: scenario o: the map region of the gempa card is blank exits non-zero (rc={proc.returncode})")


def test_scenario_p(temp_dir: str):
    payload_path = os.path.join(temp_dir, "pisa-explorer", "data.json")
    with open(payload_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Level 5 sains Indonesia digeser: hub harus nolak terbit kalau muatan explorer berubah
    # tapi halaman indeks belum dibangun ulang.
    data["levels"]["indonesia"]["science"][8] = 9.99

    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario P output:\n{proc.stdout}")
        raise AssertionError("Expected drift in pisa-explorer/data.json to cause non-zero exit")
    print(f"PASS: scenario p: drift in pisa-explorer/data.json exits non-zero (rc={proc.returncode})")


def test_scenario_q(temp_dir: str):
    index_html = os.path.join(temp_dir, "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    prefix = "const DATA = "
    idx = content.find(prefix)
    if idx == -1:
        raise RuntimeError("const DATA not found in index.html")
    start_json = idx + len(prefix)
    end_json = content.find(";\n", start_json)
    if end_json == -1:
        end_json = content.find(";", start_json)

    data = json.loads(content[start_json:end_json])
    data["expPyramid"]["bands"][0]["idn"] = 42.0

    mutated_json = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    mutated_content = content[:start_json] + mutated_json + content[end_json:]

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario Q output:\n{proc.stdout}")
        raise AssertionError("Expected mutated expPyramid band to cause non-zero exit")
    print(f"PASS: scenario q: mutated expPyramid band exits non-zero (rc={proc.returncode})")


def main():
    scenarios = [
        ("scenario a", test_scenario_a),
        ("scenario b", test_scenario_b),
        ("scenario c", test_scenario_c),
        ("scenario d", test_scenario_d),
        ("scenario e", test_scenario_e),
        ("scenario f", test_scenario_f),
        ("scenario g", test_scenario_g),
        ("scenario h", test_scenario_h),
        ("scenario i", test_scenario_i),
        ("scenario j", test_scenario_j),
        ("scenario k", test_scenario_k),
        ("scenario l", test_scenario_l),
        ("scenario m", test_scenario_m),
        ("scenario n", test_scenario_n),
        ("scenario o", test_scenario_o),
        ("scenario p", test_scenario_p),
        ("scenario q", test_scenario_q),
    ]

    for name, func in scenarios:
        temp_dir = tempfile.mkdtemp(prefix="gate_hub_test_")
        try:
            copy_tree(temp_dir)
            func(temp_dir)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
