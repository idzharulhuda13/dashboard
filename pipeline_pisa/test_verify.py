#!/usr/bin/env python3
"""Test harness proving pipeline_pisa/verify.py can fail.

Runs five verification scenarios in an isolated temporary directory:
  a) clean copy exits 0
  b) scalar mutation in annex_means.csv exits non-zero
  c) structural mutation in embedded payload array exits non-zero
  d) a narrative character changed in the page body outside the payload exits non-zero
  e) one data-fig fallback literal in the page changed to a different number exits non-zero
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
    shutil.copytree(os.path.join(ROOT, "p02"), os.path.join(dst_dir, "p02"))
    shutil.copytree(os.path.join(ROOT, "fonts"), os.path.join(dst_dir, "fonts"))
    shutil.copy2(os.path.join(ROOT, "p02.template.html"), os.path.join(dst_dir, "p02.template.html"))


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
    index_html = os.path.join(temp_dir, "p02", "index.html")
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
    index_html = os.path.join(temp_dir, "p02", "index.html")
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
    index_html = os.path.join(temp_dir, "p02", "index.html")
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
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
