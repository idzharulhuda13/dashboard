#!/usr/bin/env python3
"""Test harness proving pipeline/verify.py can fail.

Runs eight verification scenarios in an isolated temporary directory:
  a) clean copy exits 0
  b) one data-fig literal mutated exits non-zero
  c) meta name="description" deleted exits non-zero
  d) Memuat narasi data seismik Indonesia… re-injected into the body outside a script exits non-zero
  e) canonical host changed exits non-zero
  f) apple-touch-icon link removed exits non-zero
  g) JSON-LD temporalCoverage mutated exits non-zero
  h) https://evil.example.com/x injected exits non-zero
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def copy_tree(dst_dir: str):
    os.makedirs(os.path.join(dst_dir, "data", "out"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "data", "raw"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "gempa"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "pipeline"), os.path.join(dst_dir, "pipeline"))
    shutil.copy2(os.path.join(ROOT, "data", "out", "gempa_events.csv"), os.path.join(dst_dir, "data", "out", "gempa_events.csv"))
    shutil.copy2(os.path.join(ROOT, "data", "out", "bmkg_live.json"), os.path.join(dst_dir, "data", "out", "bmkg_live.json"))
    shutil.copy2(os.path.join(ROOT, "data", "out", "build_report.json"), os.path.join(dst_dir, "data", "out", "build_report.json"))
    shutil.copy2(os.path.join(ROOT, "data", "raw", "basemap.json"), os.path.join(dst_dir, "data", "raw", "basemap.json"))
    shutil.copy2(os.path.join(ROOT, "gempa", "data.json"), os.path.join(dst_dir, "gempa", "data.json"))
    shutil.copy2(os.path.join(ROOT, "gempa", "index.html"), os.path.join(dst_dir, "gempa", "index.html"))
    shutil.copy2(os.path.join(ROOT, "gempa.template.html"), os.path.join(dst_dir, "gempa.template.html"))
    shutil.copytree(os.path.join(ROOT, "fonts"), os.path.join(dst_dir, "fonts"))


def run_gate(cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "pipeline/verify.py"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_scenario_a(temp_dir: str):
    proc = run_gate(temp_dir)
    if proc.returncode != 0:
        print(f"Scenario A output:\n{proc.stdout}\n{proc.stderr}")
        raise AssertionError(f"Expected clean copy to exit 0, got {proc.returncode}")
    print("PASS: scenario a: untouched copy exits 0")


def test_scenario_b(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'(<span\b[^>]*\bdata-fig="total"[^>]*>)(.*?)(</span>)', content)
    if not m:
        raise RuntimeError("data-fig total span not found in gempa/index.html")
    mutated_content = content[:m.start(2)] + "99.999" + content[m.end(2):]

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario B output:\n{proc.stdout}")
        raise AssertionError("Expected data-fig literal mutation in gempa/index.html to cause non-zero exit")
    print(f"PASS: scenario b: one data-fig literal mutated exits non-zero (rc={proc.returncode})")


def test_scenario_c(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    mutated_content = re.sub(r'  <meta name="description" content="[^"]*">\n', '', content, count=1)
    if mutated_content == content:
        raise RuntimeError('meta name="description" not found in gempa/index.html')

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario C output:\n{proc.stdout}")
        raise AssertionError('Expected deleted meta description in gempa/index.html to cause non-zero exit')
    print(f"PASS: scenario c: meta name=\"description\" deleted exits non-zero (rc={proc.returncode})")


def test_scenario_d(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = "<body>"
    if target not in content:
        raise RuntimeError("<body> not found in gempa/index.html")
    mutated_content = content.replace(target, "<body>\n  <p>Memuat narasi data seismik Indonesia\u2026</p>", 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario D output:\n{proc.stdout}")
        raise AssertionError("Expected re-injected loading text outside script to cause non-zero exit")
    print(f"PASS: scenario d: Memuat narasi data seismik Indonesia\u2026 re-injected into body outside script exits non-zero (rc={proc.returncode})")


def test_scenario_e(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '<link rel="canonical" href="https://dashboard.idzharulhuda.com/gempa/">'
    if target not in content:
        raise RuntimeError(f"Target canonical link {target!r} not found in gempa/index.html")
    mutated_content = content.replace(target, '<link rel="canonical" href="https://example.com/gempa/">', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario E output:\n{proc.stdout}")
        raise AssertionError("Expected canonical host mutation in gempa/index.html to cause non-zero exit")
    print(f"PASS: scenario e: canonical host changed exits non-zero (rc={proc.returncode})")


def test_scenario_f(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = '  <link rel="apple-touch-icon" href="/apple-touch-icon.png">\n'
    if target not in content:
        raise RuntimeError(f"Target apple-touch-icon {target!r} not found in gempa/index.html")
    mutated_content = content.replace(target, "", 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario F output:\n{proc.stdout}")
        raise AssertionError("Expected removal of apple-touch-icon link to cause non-zero exit")
    print(f"PASS: scenario f: apple-touch-icon link removed exits non-zero (rc={proc.returncode})")


def test_scenario_g(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'("temporalCoverage":")([^"]+)(")', content)
    if not m:
        raise RuntimeError('"temporalCoverage" not found in gempa/index.html')
    mutated_content = content[:m.start(2)] + "1999-01-01/2026-10-13" + content[m.end(2):]

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario G output:\n{proc.stdout}")
        raise AssertionError("Expected JSON-LD temporalCoverage mutation to cause non-zero exit")
    print(f"PASS: scenario g: JSON-LD temporalCoverage mutated exits non-zero (rc={proc.returncode})")


def test_scenario_h(temp_dir: str):
    index_html = os.path.join(temp_dir, "gempa", "index.html")
    with open(index_html, "r", encoding="utf-8") as f:
        content = f.read()

    target = "</body>"
    if target not in content:
        raise RuntimeError("</body> not found in gempa/index.html")
    mutated_content = content.replace(target, '<a href="https://evil.example.com/x">evil</a></body>', 1)

    with open(index_html, "w", encoding="utf-8") as f:
        f.write(mutated_content)

    proc = run_gate(temp_dir)
    if proc.returncode == 0:
        print(f"Scenario H output:\n{proc.stdout}")
        raise AssertionError("Expected evil URL injection to cause non-zero exit")
    print(f"PASS: scenario h: https://evil.example.com/x injected exits non-zero (rc={proc.returncode})")


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
    ]

    for name, func in scenarios:
        temp_dir = tempfile.mkdtemp(prefix="gate_gempa_test_")
        try:
            copy_tree(temp_dir)
            func(temp_dir)
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
