"""Derive PISA-plate figures from the student public-use microdata (PUF).

Freezes reproducible PISA science and ESCS figures into small committed CSVs.
Memory-bounded two-pass streaming over SPSS microdata using pyreadstat.
Deterministic output: no wall clock, no randomness, no environment lookups in emitted files.
"""

import csv
import hashlib
import json
import os
import resource
import sys
import time
import numpy as np
import pyreadstat

INPUT_FILE = "/root/data/pisa2025/extract/CY09_MS_STU_PUF.sav"
OUTPUT_DIR = "/root/projects/dashboard/data/pisa"

CHUNK_SIZE = 100000

# Columns explicitly required by name
COLS_CHEAP = ["CNT", "OECD", "ESCS", "W_FSTUWT"]
WEIGHT_COLS = ["W_FSTUWT"] + [f"W_FSTURWT{r}" for r in range(1, 81)]
PV_COLS = [f"PV{m}SCIE" for m in range(1, 6)]
COLUMNS_USED = ["CNT", "CNTSCHID", "OECD", "ESCS"] + WEIGHT_COLS + PV_COLS

POPS = ["idn", "oecd"]

SE_FORMULA_STR = (
    "SE = sqrt(U + B) where B = var(theta_m, ddof=1) over m=1..5, "
    "U = 4 * mean_m(var_r(theta_mr, ddof=1)) over r=1..80. B = 0 for stats without PVs."
)


def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1048576):
            h.update(chunk)
    return h.hexdigest()


def compute_quartile_cuts(escs: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Compute weighted quartile cut points (0.25, 0.50, 0.75) via cumulative weights."""
    idx = np.argsort(escs)
    sorted_escs = escs[idx]
    sorted_w = weights[idx]
    cum_w = np.cumsum(sorted_w)
    total_w = cum_w[-1]
    targets = [0.25 * total_w, 0.50 * total_w, 0.75 * total_w]
    return np.interp(targets, cum_w, sorted_escs)


def compute_pv_summary(matrix_81_5: np.ndarray):
    """Compute mean across 5 PVs under rep 0, between variance B, sampling variance U, and SE."""
    theta_m = matrix_81_5[0, :]
    theta_mr = matrix_81_5[1:, :]
    mean_val = float(np.mean(theta_m))
    b_val = float(np.var(theta_m, ddof=1))
    var_r = np.var(theta_mr, axis=0, ddof=1)
    u_val = float(4.0 * np.mean(var_r))
    se_val = float(np.sqrt(u_val + b_val))
    return mean_val, b_val, u_val, se_val


def compute_no_pv_summary(vector_81: np.ndarray):
    """Compute estimate under rep 0 and sampling SE for statistics without plausible values."""
    theta_0 = float(vector_81[0])
    var_r = float(np.var(vector_81[1:], ddof=1))
    u_val = float(4.0 * var_r)
    se_val = float(np.sqrt(u_val))
    return theta_0, 0.0, u_val, se_val


def main():
    t_start = time.perf_counter()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 0. Metadata inspection & SHA256 calculation
    input_sha256 = compute_sha256(INPUT_FILE)
    _, meta = pyreadstat.read_sav(INPUT_FILE, metadataonly=True)
    meta_row_count = meta.number_rows
    meta_col_count = meta.number_columns

    # =========================================================================
    # PASS A: Collect ESCS & W_FSTUWT per population for quartile cut points
    # =========================================================================
    offset = 0
    idn_escs_chunks = []
    idn_w_chunks = []
    oecd_escs_chunks = []
    oecd_w_chunks = []

    while True:
        # Avoid position-indexing trap: pyreadstat returns file column order
        df_chunk, _ = pyreadstat.read_sav(
            INPUT_FILE,
            row_offset=offset,
            row_limit=CHUNK_SIZE,
            usecols=COLS_CHEAP,
        )
        df_chunk = df_chunk[COLS_CHEAP]
        n_chunk = len(df_chunk)
        offset += n_chunk

        # IDN population
        sub_idn = df_chunk[df_chunk["CNT"] == "IDN"]
        if len(sub_idn) > 0:
            valid_idn = sub_idn.dropna(subset=["ESCS"])
            if len(valid_idn) > 0:
                idn_escs_chunks.append(valid_idn["ESCS"].to_numpy(dtype=np.float64))
                idn_w_chunks.append(valid_idn["W_FSTUWT"].to_numpy(dtype=np.float64))

        # OECD population
        sub_oecd = df_chunk[df_chunk["OECD"] == 1.0]
        if len(sub_oecd) > 0:
            valid_oecd = sub_oecd.dropna(subset=["ESCS"])
            if len(valid_oecd) > 0:
                oecd_escs_chunks.append(valid_oecd["ESCS"].to_numpy(dtype=np.float64))
                oecd_w_chunks.append(valid_oecd["W_FSTUWT"].to_numpy(dtype=np.float64))

        if n_chunk < CHUNK_SIZE:
            break

    idn_cuts = compute_quartile_cuts(
        np.concatenate(idn_escs_chunks),
        np.concatenate(idn_w_chunks),
    )
    oecd_cuts = compute_quartile_cuts(
        np.concatenate(oecd_escs_chunks),
        np.concatenate(oecd_w_chunks),
    )
    cuts = {"idn": idn_cuts, "oecd": oecd_cuts}

    # Free Pass A temporary chunk lists
    del idn_escs_chunks, idn_w_chunks, oecd_escs_chunks, oecd_w_chunks

    # =========================================================================
    # PASS B: Accumulate running sums for 5 PVs x 81 weights and quartiles
    # =========================================================================
    sum_scie = {p: np.zeros((81, 5), dtype=np.float64) for p in POPS}
    sum_w_scie = {p: np.zeros(81, dtype=np.float64) for p in POPS}

    sum_scie_q = {p: {q: np.zeros((81, 5), dtype=np.float64) for q in (1, 2, 3, 4)} for p in POPS}
    sum_w_scie_q = {p: {q: np.zeros(81, dtype=np.float64) for q in (1, 2, 3, 4)} for p in POPS}

    sum_escs = {p: np.zeros(81, dtype=np.float64) for p in POPS}
    sum_w_escs = {p: np.zeros(81, dtype=np.float64) for p in POPS}

    counts = {
        p: {
            "students": 0,
            "schools": set(),
            "missing_escs": 0,
            "no_science_pv": 0,
            "level_6_any_pv": 0,
            "level_5_plus_any_pv": 0,
        }
        for p in POPS
    }
    total_counts = {
        "students": 0,
        "schools": set(),
        "missing_escs": 0,
        "no_science_pv": 0,
        "level_6_any_pv": 0,
        "level_5_plus_any_pv": 0,
    }

    offset = 0
    total_rows = 0

    while True:
        df_chunk, _ = pyreadstat.read_sav(
            INPUT_FILE,
            row_offset=offset,
            row_limit=CHUNK_SIZE,
            usecols=COLUMNS_USED,
        )
        df_chunk = df_chunk[COLUMNS_USED]
        n_chunk = len(df_chunk)
        offset += n_chunk
        total_rows += n_chunk

        # Track total dataset counts
        total_counts["students"] += n_chunk
        total_counts["schools"].update(df_chunk["CNTSCHID"].dropna().tolist())
        total_counts["missing_escs"] += int(df_chunk["ESCS"].isna().sum())
        total_counts["no_science_pv"] += int(df_chunk["PV1SCIE"].isna().sum())

        pv_all = df_chunk[PV_COLS].to_numpy(dtype=np.float64)
        total_counts["level_6_any_pv"] += int(np.any(pv_all >= 707.93, axis=1).sum())
        total_counts["level_5_plus_any_pv"] += int(np.any(pv_all >= 633.33, axis=1).sum())

        for pop in POPS:
            cond = (df_chunk["CNT"] == "IDN") if pop == "idn" else (df_chunk["OECD"] == 1.0)
            sub = df_chunk[cond]
            n_sub = len(sub)
            if n_sub == 0:
                continue

            counts[pop]["students"] += n_sub
            counts[pop]["schools"].update(sub["CNTSCHID"].dropna().tolist())
            counts[pop]["missing_escs"] += int(sub["ESCS"].isna().sum())
            counts[pop]["no_science_pv"] += int(sub["PV1SCIE"].isna().sum())

            pv_sub = sub[PV_COLS].to_numpy(dtype=np.float64)
            counts[pop]["level_6_any_pv"] += int(np.any(pv_sub >= 707.93, axis=1).sum())
            counts[pop]["level_5_plus_any_pv"] += int(np.any(pv_sub >= 633.33, axis=1).sum())

            # Accumulate overall science weighted sums
            valid_scie_mask = sub["PV1SCIE"].notna()
            if valid_scie_mask.any():
                sub_scie = sub[valid_scie_mask]
                w_scie = sub_scie[WEIGHT_COLS].to_numpy(dtype=np.float64)
                pv_vals = sub_scie[PV_COLS].to_numpy(dtype=np.float64)
                sum_scie[pop] += w_scie.T @ pv_vals
                sum_w_scie[pop] += np.sum(w_scie, axis=0)

            # Accumulate ESCS weighted sums
            valid_escs_mask = sub["ESCS"].notna()
            if valid_escs_mask.any():
                sub_escs = sub[valid_escs_mask]
                w_escs = sub_escs[WEIGHT_COLS].to_numpy(dtype=np.float64)
                escs_vals = sub_escs["ESCS"].to_numpy(dtype=np.float64)
                sum_escs[pop] += w_escs.T @ escs_vals
                sum_w_escs[pop] += np.sum(w_escs, axis=0)

            # Accumulate science by ESCS quartile (complete cases for ESCS and PVs)
            valid_q_mask = valid_escs_mask & valid_scie_mask
            if valid_q_mask.any():
                sub_q = sub[valid_q_mask]
                escs_q_vals = sub_q["ESCS"].to_numpy(dtype=np.float64)
                w_q_vals = sub_q[WEIGHT_COLS].to_numpy(dtype=np.float64)
                pv_q_vals = sub_q[PV_COLS].to_numpy(dtype=np.float64)

                c1, c2, c3 = cuts[pop]
                m1 = escs_q_vals <= c1
                m2 = (escs_q_vals > c1) & (escs_q_vals <= c2)
                m3 = (escs_q_vals > c2) & (escs_q_vals <= c3)
                m4 = escs_q_vals > c3

                for q_idx, m_q in [(1, m1), (2, m2), (3, m3), (4, m4)]:
                    if np.any(m_q):
                        w_sub_q = w_q_vals[m_q]
                        pv_sub_q = pv_q_vals[m_q]
                        sum_scie_q[pop][q_idx] += w_sub_q.T @ pv_sub_q
                        sum_w_scie_q[pop][q_idx] += np.sum(w_sub_q, axis=0)

        if n_chunk < CHUNK_SIZE:
            break

    # =========================================================================
    # Compute final statistics
    # =========================================================================
    estimates = {}
    for pop in POPS:
        # scie_mean: matrix of shape (81, 5)
        estimates[(pop, "scie_mean")] = sum_scie[pop] / sum_w_scie[pop][:, None]

        # scie_q1..4_mean: matrices of shape (81, 5)
        for q in (1, 2, 3, 4):
            estimates[(pop, f"scie_q{q}_mean")] = sum_scie_q[pop][q] / sum_w_scie_q[pop][q][:, None]

        # scie_q4_minus_q1: matrix of shape (81, 5)
        estimates[(pop, "scie_q4_minus_q1")] = (
            estimates[(pop, "scie_q4_mean")] - estimates[(pop, "scie_q1_mean")]
        )

        # escs_mean: vector of shape (81,)
        estimates[(pop, "escs_mean")] = sum_escs[pop] / sum_w_escs[pop]

    # =========================================================================
    # Emit CSV and JSON outputs
    # =========================================================================
    # 1. puf_estimates.csv
    out_estimates_path = os.path.join(OUTPUT_DIR, "puf_estimates.csv")
    stat_tokens = [
        "scie_mean",
        "scie_q1_mean",
        "scie_q2_mean",
        "scie_q3_mean",
        "scie_q4_mean",
        "scie_q4_minus_q1",
        "escs_mean",
    ]
    with open(out_estimates_path, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["stat", "pop", "pv", "rep", "value"])
        for stat in stat_tokens:
            for pop in POPS:
                if stat == "escs_mean":
                    vals = estimates[(pop, stat)]
                    for r in range(81):
                        writer.writerow([stat, pop, 0, r, repr(float(vals[r]))])
                else:
                    vals = estimates[(pop, stat)]
                    for m in range(1, 6):
                        for r in range(81):
                            writer.writerow([stat, pop, m, r, repr(float(vals[r, m - 1]))])

    # 2. puf_quartile_cuts.csv
    out_cuts_path = os.path.join(OUTPUT_DIR, "puf_quartile_cuts.csv")
    with open(out_cuts_path, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["pop", "quartile", "escs_cut"])
        for pop in POPS:
            for q_label, cut_val in [
                ("q1", cuts[pop][0]),
                ("q2", cuts[pop][1]),
                ("q3", cuts[pop][2]),
            ]:
                writer.writerow([pop, q_label, repr(float(cut_val))])

    # 3. puf_counts.csv
    out_counts_path = os.path.join(OUTPUT_DIR, "puf_counts.csv")
    count_rows = [
        ("students_idn", counts["idn"]["students"]),
        ("students_oecd", counts["oecd"]["students"]),
        ("schools_idn", len(counts["idn"]["schools"])),
        ("schools_oecd", len(counts["oecd"]["schools"])),
        ("missing_escs_idn", counts["idn"]["missing_escs"]),
        ("missing_escs_oecd", counts["oecd"]["missing_escs"]),
        ("no_science_pv_idn", counts["idn"]["no_science_pv"]),
        ("no_science_pv_oecd", counts["oecd"]["no_science_pv"]),
        ("level_6_any_pv_idn", counts["idn"]["level_6_any_pv"]),
        ("level_6_any_pv_oecd", counts["oecd"]["level_6_any_pv"]),
        ("level_5_plus_any_pv_idn", counts["idn"]["level_5_plus_any_pv"]),
        ("level_5_plus_any_pv_oecd", counts["oecd"]["level_5_plus_any_pv"]),
        ("idn_students", counts["idn"]["students"]),
        ("idn_schools", len(counts["idn"]["schools"])),
        ("idn_missing_escs", counts["idn"]["missing_escs"]),
        ("idn_no_science_pv", counts["idn"]["no_science_pv"]),
        ("idn_level_6_any_pv", counts["idn"]["level_6_any_pv"]),
        ("idn_level_5_plus_any_pv", counts["idn"]["level_5_plus_any_pv"]),
        ("idn_level_5_or_above_any_pv", counts["idn"]["level_5_plus_any_pv"]),
        ("oecd_students", counts["oecd"]["students"]),
        ("oecd_schools", len(counts["oecd"]["schools"])),
        ("oecd_missing_escs", counts["oecd"]["missing_escs"]),
        ("oecd_no_science_pv", counts["oecd"]["no_science_pv"]),
        ("oecd_level_6_any_pv", counts["oecd"]["level_6_any_pv"]),
        ("oecd_level_5_plus_any_pv", counts["oecd"]["level_5_plus_any_pv"]),
        ("oecd_level_5_or_above_any_pv", counts["oecd"]["level_5_plus_any_pv"]),
        ("total_students", total_counts["students"]),
        ("total_schools", len(total_counts["schools"])),
        ("total_missing_escs", total_counts["missing_escs"]),
        ("total_no_science_pv", total_counts["no_science_pv"]),
        ("total_level_6_any_pv", total_counts["level_6_any_pv"]),
        ("total_level_5_plus_any_pv", total_counts["level_5_plus_any_pv"]),
        ("total_level_5_or_above_any_pv", total_counts["level_5_plus_any_pv"]),
    ]
    with open(out_counts_path, "w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["key", "value"])
        for k, v in count_rows:
            writer.writerow([k, v])

    # 4. puf_report.json
    out_report_path = os.path.join(OUTPUT_DIR, "puf_report.json")
    counts_dict = {k: v for k, v in count_rows}
    report_data = {
        "input_sha256": input_sha256,
        "row_count": total_rows,
        "column_count": meta_col_count,
        "chunk_size": CHUNK_SIZE,
        "column_list_used": COLUMNS_USED,
        "se_formula": SE_FORMULA_STR,
        "counts": counts_dict,
        **counts_dict,
    }
    with open(out_report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    # =========================================================================
    # Telemetry and target value checks to stdout ONLY (never in frozen files)
    # =========================================================================
    t_end = time.perf_counter()
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    idn_scie_mean, idn_scie_b, idn_scie_u, idn_scie_se = compute_pv_summary(
        estimates[("idn", "scie_mean")]
    )
    idn_escs_mean, _, _, idn_escs_se = compute_no_pv_summary(
        estimates[("idn", "escs_mean")]
    )
    oecd_escs_mean, _, _, oecd_escs_se = compute_no_pv_summary(
        estimates[("oecd", "escs_mean")]
    )
    idn_gap_mean, idn_gap_b, idn_gap_u, idn_gap_se = compute_pv_summary(
        estimates[("idn", "scie_q4_minus_q1")]
    )

    print("=" * 60)
    print("PISA PUF Microdata Extraction Complete")
    print(f"Elapsed time: {t_end - t_start:.2f} s")
    print(f"Peak memory: {peak_rss_mb:.1f} MB (RSS)")
    print(f"Chunk size: {CHUNK_SIZE}")
    print(f"Input rows: {total_rows}, Columns in file: {meta_col_count}")
    print("-" * 60)
    print(f"IDN science mean: {idn_scie_mean:.5f} (S.E. = {idn_scie_se:.5f})")
    print(f"IDN ESCS mean:    {idn_escs_mean:.4f} (S.E. = {idn_escs_se:.5f})")
    print(f"OECD ESCS mean:   {oecd_escs_mean:.4f} (S.E. = {oecd_escs_se:.5f})")
    print(
        f"IDN quartile cuts: "
        f"{idn_cuts[0]:.3f} / {idn_cuts[1]:.3f} / {idn_cuts[2]:.3f}"
    )
    print(f"IDN quartile gap (Q4 - Q1): {idn_gap_mean:.2f} pts (S.E. = {idn_gap_se:.5f})")
    print("=" * 60)


if __name__ == "__main__":
    main()
