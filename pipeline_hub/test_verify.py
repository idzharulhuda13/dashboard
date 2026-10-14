#!/usr/bin/env python3
"""Test harness proving pipeline_hub/verify.py can fail.

Runs six verification scenarios in an isolated temporary directory:
  a) clean copy exits 0
  b) mutated proficiency value in data/pisa/annex_proficiency.csv exits non-zero
  c) mutated number inside the page's payload exits non-zero
  d) one narrative word changed in the page body (outside the payload) exits non-zero
  e) one data-fig fallback literal changed exits non-zero
  f) CSS rule injected inside the FONTS region exits non-zero
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
    os.makedirs(os.path.join(dst_dir, "data", "pisa"), exist_ok=True)
    os.makedirs(os.path.join(dst_dir, "gempa"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "pipeline_hub"), os.path.join(dst_dir, "pipeline_hub"))
    shutil.copy2(os.path.join(ROOT, "data", "pisa", "annex_proficiency.csv"), os.path.join(dst_dir, "data", "pisa", "annex_proficiency.csv"))
    shutil.copy2(os.path.join(ROOT, "gempa", "data.json"), os.path.join(dst_dir, "gempa", "data.json"))
    shutil.copytree(os.path.join(ROOT, "fonts"), os.path.join(dst_dir, "fonts"))
    shutil.copy2(os.path.join(ROOT, "hub.template.html"), os.path.join(dst_dir, "hub.template.html"))
    shutil.copy2(os.path.join(ROOT, "index.html"), os.path.join(dst_dir, "index.html"))


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


def main():
    temp_dir = tempfile.mkdtemp(prefix="gate_hub_test_")
    try:
        copy_tree(temp_dir)
        test_scenario_a(temp_dir)

        test_scenario_b(temp_dir)

        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_hub_test_")
        copy_tree(temp_dir)

        test_scenario_c(temp_dir)

        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_hub_test_")
        copy_tree(temp_dir)

        test_scenario_d(temp_dir)

        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_hub_test_")
        copy_tree(temp_dir)

        test_scenario_e(temp_dir)

        shutil.rmtree(temp_dir)
        temp_dir = tempfile.mkdtemp(prefix="gate_hub_test_")
        copy_tree(temp_dir)

        test_scenario_f(temp_dir)
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
