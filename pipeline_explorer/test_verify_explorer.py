#!/usr/bin/env python3
"""Mutation test suite for pipeline_explorer/verify_explorer.py."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VERIFY_SCRIPT = ROOT / "pipeline_explorer" / "verify_explorer.py"
REAL_PAYLOAD = ROOT / "pisa-explorer" / "data.json"
CSV_DIR = ROOT / "data" / "pisa"


def dump_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=True) + "\n"


def mutate_idn_science_mean(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    d["scores"]["indonesia"]["science"][0] = round(d["scores"]["indonesia"]["science"][0] + 1.5, 2)
    return dump_json(d)


def mutate_delete_country(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    d["countries"].pop()
    return dump_json(d)


def mutate_null_to_zero(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    d["levels"]["indonesia"]["science"][0] = 0.0
    return dump_json(d)


def mutate_band_sum(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    idx = d["band_keys"].index("level_3")
    d["levels"]["indonesia"]["science"][idx] = round(d["levels"]["indonesia"]["science"][idx] + 3.0, 2)
    return dump_json(d)


def mutate_remove_share_year(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    d["share"]["indonesia"]["science"]["years"].pop()
    return dump_json(d)


def mutate_flip_sha256(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    orig = d["meta"]["input_sha256"]["annex_means.csv"]
    flipped = ("0" if orig[0] != "0" else "1") + orig[1:]
    d["meta"]["input_sha256"]["annex_means.csv"] = flipped
    return dump_json(d)


def mutate_inject_nan(_data: dict, raw: str) -> str:
    target = '"science":[388.67,'
    if target not in raw:
        raise RuntimeError(f"Expected substring {target!r} in payload JSON")
    return raw.replace(target, '"science":[NaN,', 1)


def mutate_pad_past_budget(_data: dict, raw: str) -> str:
    padding = " " * (225 * 1024 - len(raw.encode("utf-8")) + 64)
    return raw.rstrip("\n") + padding + "\n"


def mutate_move_oecd_average(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    oecd = d["countries"].pop(1)
    d["countries"].append(oecd)
    return dump_json(d)


def mutate_duplicate_country_id(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    dup = copy.deepcopy(d["countries"][-1])
    d["countries"].append(dup)
    return dump_json(d)


def mutate_break_band_set(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    d["levels"]["indonesia"]["science"][0] = 1.25
    return dump_json(d)


def mutate_rename_band_key(data: dict, _raw: str) -> str:
    d = copy.deepcopy(data)
    idx = d["band_keys"].index("level_below_1b")
    d["band_keys"][idx] = "below_1b"
    return dump_json(d)


def mutate_band_se(data: dict, _raw: str) -> str:
    # A wrong band SE (e.g. an estimate instead of the OECD annex column) must fail check 2.
    d = copy.deepcopy(data)
    arr = d["levels_se"]["indonesia"]["science"]
    idx = next(i for i, v in enumerate(arr) if v is not None)
    arr[idx] = round(arr[idx] + 0.5, 2)
    return dump_json(d)


def mutate_non_canonical_bytes(_data: dict, raw: str) -> str:
    # Trailing space before newline preserves JSON semantics and stays under 220 KB, isolating check 10.
    return raw.rstrip("\n") + " \n"


def mutate_variation_domain_swap(data: dict, _raw: str) -> str:
    # This is exactly the reported bug: with per-domain keys present, a page could still print science
    # numbers under "matematika". Only the per-domain CSV recompute (check 2) can catch the swap.
    d = copy.deepcopy(data)
    var = d["variation"]["indonesia"]
    var["science"], var["mathematics"] = var["mathematics"], var["science"]
    return dump_json(d)


def run_gate(payload_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--payload",
            str(payload_path),
            "--csv-dir",
            str(CSV_DIR),
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    if not REAL_PAYLOAD.is_file():
        print(f"ERROR: missing payload {REAL_PAYLOAD}", file=sys.stderr)
        return 1

    raw_text = REAL_PAYLOAD.read_text(encoding="utf-8")
    base_data = json.loads(raw_text)

    mutations = [
        ("1. Indonesia science mean +1.5", 2, "recompute", mutate_idn_science_mean),
        ("2. delete a country from countries", 3, "completeness", mutate_delete_country),
        ("3. replace one null with 0", 4, "nulls", mutate_null_to_zero),
        ("4. add 3.0 to one country's level_3 (sum != 100)", 5, "bands", mutate_band_sum),
        ("5. remove one year from a share years array", 6, "series", mutate_remove_share_year),
        ("6. flip one meta.input_sha256 value", 7, "provenance", mutate_flip_sha256),
        ("7. set one value to float('nan') in JSON text", 8, "sanity", mutate_inject_nan),
        ("8. pad the file past 220 KB", 9, "budget", mutate_pad_past_budget),
        ("9. move oecd-average to the end of countries", 8, "sanity", mutate_move_oecd_average),
        ("10. insert a duplicate country id", 3, "completeness", mutate_duplicate_country_id),
        ("11. break one band set (science level_below_1c)", 5, "bands", mutate_break_band_set),
        ("12. rename a band key to below_1b", 1, "schema", mutate_rename_band_key),
        ("13. non-canonical trailing whitespace bytes", 10, "determinism", mutate_non_canonical_bytes),
        ("14. bump one band SE by 0.5", 2, "recompute", mutate_band_se),
        ("15. swap Indonesia science/mathematics variation arrays", 2, "recompute", mutate_variation_domain_swap),
    ]

    behaved = 0
    total = 1 + len(mutations)

    with tempfile.TemporaryDirectory(prefix="pisa_explorer_gate_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        baseline_file = tmp_path / "baseline.json"
        shutil.copyfile(REAL_PAYLOAD, baseline_file)

        base_proc = run_gate(baseline_file)
        base_ok = base_proc.returncode == 0 and "PASS 10 checks" in base_proc.stdout and base_proc.stderr == ""
        if base_ok:
            behaved += 1
        status = "OK" if base_ok else "MISMATCH"
        print(
            f"[{status}] 0. baseline unmutated payload | expected=PASS (exit 0) | "
            f"observed_exit={base_proc.returncode}"
        )

        for idx, (label, check_num, check_name, mutator) in enumerate(mutations, 1):
            mutant_file = tmp_path / f"mutant_{idx:02d}.json"
            mutant_text = mutator(base_data, raw_text)
            mutant_file.write_text(mutant_text, encoding="utf-8")

            proc = run_gate(mutant_file)
            first_err = proc.stderr.splitlines()[0] if proc.stderr.strip() else ""
            expected_prefix = f"FAIL {check_name}:"
            expected_tag = f"[check {check_num}]"
            ok = (
                proc.returncode != 0
                and first_err.startswith(expected_prefix)
                and expected_tag in first_err
            )
            if ok:
                behaved += 1
            status = "OK" if ok else "MISMATCH"
            print(
                f"[{status}] {label} | expected=check {check_num} ({check_name}) | "
                f"observed_exit={proc.returncode} | stderr={first_err}"
            )

    print(f"{behaved}/{total} scenarios behave")
    return 0 if behaved == total else 1


if __name__ == "__main__":
    sys.exit(main())
