#!/usr/bin/env python3
"""Test harness proving pipeline_pisa/verify.py can fail.

Runs twelve verification scenarios in an isolated temporary directory:
  a) clean copy exits 0
  b) scalar mutation in annex_means.csv exits non-zero
  c) structural mutation in embedded payload array exits non-zero
  d) a narrative character changed in the page body outside the payload exits non-zero
  e) one data-fig fallback literal in the page changed to a different number exits non-zero
  f) injected CSS rule inside the font region exits non-zero
  g) a percentile in the payload changed to a wrong but still ascending value exits non-zero
  h) meta name="description" deleted exits non-zero
  i) canonical path changed exits non-zero
  j) JSON-LD license changed to CC0 exits non-zero
  k) apple-touch-icon link removed exits non-zero
  l) https://evil.example.com/x injected exits non-zero
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def copy_tree(dst_dir: str):
    os.makedirs(os.path.join(dst_dir, "data"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "pipeline_pisa"), os.path.join(dst_dir, "pipeline_pisa"))
    shutil.copytree(os.path.join(ROOT, "data", "pisa"), os.path.join(dst_dir, "data", "pisa"))
    shutil.copytree(os.path.join(ROOT, "pisa"), os.path.join(dst_dir, "pisa"))
    shutil.copytree(os.path.join(ROOT, "fonts"), os.path.join(dst_dir, "fonts"))
    shutil.copy2(os.path.join(ROOT, "pisa.template.html"), os.path.join(dst_dir, "pisa.template.html"))

    icon_files = [
        "favicon.ico",
        "favicon.svg",
        "favicon-96.png",
        "apple-touch-icon.png",
        "robots.txt",
        "sitemap.xml",
    ]
    for fname in icon_files:
        src = os.path.join(ROOT, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst_dir, fname))

    og_src = os.path.join(ROOT, "assets", "og")
    if os.path.exists(og_src):
        dst_og = os.path.join(dst_dir, "assets", "og")
        os.makedirs(os.path.dirname(dst_og), exist_ok=True)
        shutil.copytree(og_src, dst_og)


def run_gate(cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "pipeline_pisa/verify.py"],
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
    means_csv = os.path.join(temp_dir, "data", "pisa", "annex_means.csv")
    with open(means_csv, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = f.seek(0) or csv.reader(f) and next(csv.reader(f))

    modified = False
    for r in rows:
        if r.get("subject") == "Indonesia" and r.get("domain") == "science" and r.get("stat") == "mean":
            r["value"] = "999.0"
            modified = True
            break

    if not modified:
        raise RuntimeError("Failed to locate Indonesia science mean in annex_means.csv")

    with open(means_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario B output:\n{proc.stdout}")
        raise AssertionError("Expected scalar modification in annex_means.csv to cause non-zero exit")
    print(f"PASS: scenario b: scalar mutation in annex_means.csv exits non-zero (rc={proc.returncode})")


def test_scenario_c(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
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
    # Mutate a numeric value inside a payload array (peers ladder score)
    data["peers"][0]["value"] = 999.9

    mutated_json = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    mutated_content = content[:start_json] + mutated_json + content[end_json:]

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario C output:\n{proc.stdout}")
        raise AssertionError("Expected structural payload mutation in index.html to cause non-zero exit")
    print(f"PASS: scenario c: structural payload array mutation exits non-zero (rc={proc.returncode})")


def test_scenario_d(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = "Pengukuran literasi sains PISA"
    if target not in content:
        raise RuntimeError(f"Target subtitle phrase {target!r} not found in index.html")
    mutated_content = content.replace(target, "Pengukuran literasi sains UNESCO", 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario D output:\n{proc.stdout}")
        raise AssertionError("Expected narrative prose mutation in index.html to cause non-zero exit")
    print(f"PASS: scenario d: narrative character changed in page body exits non-zero (rc={proc.returncode})")


def test_scenario_e(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '<span data-fig="hero_gap">93</span>'
    if target not in content:
        raise RuntimeError(f"Target data-fig fallback {target!r} not found in index.html")
    mutated_content = content.replace(target, '<span data-fig="hero_gap">999</span>', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario E output:\n{proc.stdout}")
        raise AssertionError("Expected data-fig fallback literal mutation in index.html to cause non-zero exit")
    print(f"PASS: scenario e: data-fig fallback literal changed exits non-zero (rc={proc.returncode})")


def test_scenario_f(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
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
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    target_idx = None
    indent = ""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("const DATA = "):
            target_idx = i
            indent = line[: len(line) - len(line.lstrip(" "))]
            break
    if target_idx is None:
        raise RuntimeError("Payload line 'const DATA = ' not found in index.html")

    payload = json.loads(lines[target_idx].strip()[len("const DATA = "):].rstrip(";"))
    median = payload["spread"]["idn"]["p50"]
    payload["spread"]["idn"]["p50"] = median + 20
    lines[target_idx] = indent + "const DATA = " + json.dumps(payload) + ";"

    with open(index_html, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario G output:\n{proc.stdout}")
        raise AssertionError(
            "Expected an ascending-but-wrong percentile in the payload to cause non-zero exit"
        )
    print(f"PASS: scenario g: still-ascending wrong percentile exits non-zero (rc={proc.returncode})")


def test_scenario_h(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    mutated_content = re.sub(r'  <meta name="description" content="[^"]*">\n', '', content, count=1)
    if mutated_content == content:
        raise RuntimeError('meta name="description" not found in pisa/index.html')

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario H output:\n{proc.stdout}")
        raise AssertionError('Expected deleted meta description in pisa/index.html to cause non-zero exit')
    print(f"PASS: scenario h: meta name=\"description\" deleted exits non-zero (rc={proc.returncode})")


def test_scenario_i(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '<link rel="canonical" href="https://dashboard.idzharulhuda.com/pisa/">'
    if target not in content:
        raise RuntimeError(f"Target canonical link {target!r} not found in pisa/index.html")
    mutated_content = content.replace(target, '<link rel="canonical" href="https://dashboard.idzharulhuda.com/pisa-changed/">', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario I output:\n{proc.stdout}")
        raise AssertionError("Expected canonical path mutation in pisa/index.html to cause non-zero exit")
    print(f"PASS: scenario i: canonical path changed exits non-zero (rc={proc.returncode})")


def test_scenario_j(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '"license":"CC BY 4.0"'
    if target not in content:
        raise RuntimeError(f"Target license {target!r} not found in pisa/index.html")
    mutated_content = content.replace(target, '"license":"CC0"', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario J output:\n{proc.stdout}")
        raise AssertionError("Expected JSON-LD license mutation to cause non-zero exit")
    print(f"PASS: scenario j: JSON-LD license changed to CC0 exits non-zero (rc={proc.returncode})")


def test_scenario_k(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '  <link rel="apple-touch-icon" href="/apple-touch-icon.png">\n'
    if target not in content:
        raise RuntimeError(f"Target apple-touch-icon {target!r} not found in pisa/index.html")
    mutated_content = content.replace(target, "", 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario K output:\n{proc.stdout}")
        raise AssertionError("Expected removal of apple-touch-icon link to cause non-zero exit")
    print(f"PASS: scenario k: apple-touch-icon link removed exits non-zero (rc={proc.returncode})")


def test_scenario_l(temp_dir: str):
    index_html = os.path.join(temp_dir, "pisa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = "</body>"
    if target not in content:
        raise RuntimeError("</body> not found in pisa/index.html")
    mutated_content = content.replace(target, '<a href="https://evil.example.com/x">evil</a></body>', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario L output:\n{proc.stdout}")
        raise AssertionError("Expected evil URL injection to cause non-zero exit")
    print(f"PASS: scenario l: https://evil.example.com/x injected exits non-zero (rc={proc.returncode})")


def main():
    temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
    try:
        # Scenario A
        copy_tree(temp_dir)
        test_scenario_a(temp_dir)

        # Scenario B (mutate scalar in CSV)
        test_scenario_b(temp_dir)

        # Reset temp tree for Scenario C
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario C (mutate structural payload in HTML)
        test_scenario_c(temp_dir)

        # Reset temp tree for Scenario D
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario D (narrative character changed in page body outside payload)
        test_scenario_d(temp_dir)

        # Reset temp tree for Scenario E
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario E (data-fig fallback literal in page changed to different number)
        test_scenario_e(temp_dir)

        # Reset temp tree for Scenario F
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario F (injected CSS rule inside font region)
        test_scenario_f(temp_dir)

        # Reset temp tree for Scenario G
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario G (percentile value wrong but still ascending)
        test_scenario_g(temp_dir)

        # Reset temp tree for Scenario H
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario H (meta name="description" deleted)
        test_scenario_h(temp_dir)

        # Reset temp tree for Scenario I
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario I (canonical path changed)
        test_scenario_i(temp_dir)

        # Reset temp tree for Scenario J
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario J (JSON-LD license changed to CC0)
        test_scenario_j(temp_dir)

        # Reset temp tree for Scenario K
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario K (apple-touch-icon link removed)
        test_scenario_k(temp_dir)

        # Reset temp tree for Scenario L
        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_pisa_test_")
        copy_tree(temp_dir)

        # Scenario L (https://evil.example.com/x injected)
        test_scenario_l(temp_dir)
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
