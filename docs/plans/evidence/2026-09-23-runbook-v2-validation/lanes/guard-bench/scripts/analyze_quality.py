#!/usr/bin/env python3
"""Exp E analysis, recomputed from raw logits.

Inputs per model dir: ref_scores.json (PyTorch fp32, unpadded, one sample per forward = the pristine reference),
scores_<backend>.json (gpu_scores.py: corpus padded to 512 + multi-window benign requests), optional
cpu parity from export_report.json.
Outputs:
  parity   : per backend vs reference: decision agreement % at 0.5 and at t_1%, max/mean |d score|, max |d logit|
  fpr      : per-window FPR at 0.5 and at t_1% (t_1% = 99th pct of the reference's benign sample scores and,
             separately, of the 510-token benign windows) for W = 1, 2, 4, 7; measured request-level FPR
             (OR over windows) vs independence prediction 1-(1-p)^W with p the per-window FPR of that W's windows
  recall   : per attack family at 0.5 and t_1% (reference and every backend)
usage: analyze_quality.py <dir-with-ref-and-scores> <families.json> <out.json>
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402


def p1(lg):
    return C.softmax_p1(np.asarray(lg, dtype=np.float64))


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    r = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (round((c - r) / d, 5), round((c + r) / d, 5))


def main():
    d, fam_path, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    ref = json.load(open(d / "ref_scores.json"))
    fams = json.load(open(fam_path))
    ids = sorted(ref)
    ref_p = np.array([ref[i]["p1"] for i in ids])
    labels = np.array([ref[i]["label"] for i in ids])
    family = np.array([ref[i]["family"] for i in ids])
    ben = labels == "benign"
    t1_ref = float(np.quantile(ref_p[ben], 0.99, method="higher"))  # >= 99% of benign samples strictly below-or-equal
    res = {"n_benign": int(ben.sum()), "n_malicious": int((~ben).sum()),
           "t1pct_on_reference_benign_samples": t1_ref, "backends": {}}

    def recall_table(p, thr):
        tab = {}
        for f in fams["attack"]:
            m = family == f
            if m.any():
                k = int((p[m] >= thr).sum())
                tab[f] = {"n": int(m.sum()), "detected": k, "recall": round(k / m.sum(), 4)}
        m = ~ben
        k = int((p[m] >= thr).sum())
        tab["ALL"] = {"n": int(m.sum()), "detected": k, "recall": round(k / m.sum(), 4)}
        return tab

    def fpr_samples(p, thr):
        k = int((p[ben] >= thr).sum())
        return {"fp": k, "n": int(ben.sum()), "fpr": round(k / ben.sum(), 5), "ci95": wilson(k, int(ben.sum()))}

    res["reference"] = {"recall@0.5": recall_table(ref_p, 0.5), f"recall@t1": recall_table(ref_p, t1_ref),
                        "sample_fpr@0.5": fpr_samples(ref_p, 0.5), "sample_fpr@t1": fpr_samples(ref_p, t1_ref)}
    for f in sorted(d.glob("scores_*.json")):
        s = json.load(open(f))
        b = s["backend"]
        lg = np.array([s["corpus"][i] for i in ids])
        p = p1(lg)
        finite = np.isfinite(p)
        ref_lg = np.array([ref[i]["logits"] for i in ids])
        par = {"n": len(ids), "nonfinite": int((~finite).sum()),
               "agree@0.5_pct": round(100 * float(((p >= 0.5) == (ref_p >= 0.5)).mean()), 3),
               "disagree@0.5": int(((p >= 0.5) != (ref_p >= 0.5)).sum()),
               "agree@t1_pct": round(100 * float(((p >= t1_ref) == (ref_p >= t1_ref)).mean()), 3),
               "max_abs_dscore": float(np.nanmax(np.abs(p - ref_p))), "mean_abs_dscore": float(np.nanmean(np.abs(p - ref_p))),
               "max_abs_dlogit": float(np.nanmax(np.abs(lg - ref_lg)))}
        # multi-window benign requests
        mw = {}
        allwin = np.concatenate([p1(np.array(v)).reshape(-1) for v in s["multiwindow"].values()])
        t1_win = float(np.quantile(allwin, 0.99, method="higher"))
        for W, arr in sorted(s["multiwindow"].items(), key=lambda x: int(x[0])):
            W = int(W)
            P = p1(np.array(arr))  # [n_req, W]
            row = {"n_requests": int(P.shape[0]), "windows_per_request": W}
            for name, thr in (("0.5", 0.5), ("t1_samples", t1_ref), ("t1_windows", t1_win)):
                pw = float((P >= thr).mean())
                kreq = int((P.max(axis=1) >= thr).sum())
                row[f"thr_{name}"] = {"threshold": thr, "per_window_fpr": round(pw, 5),
                                      "request_fpr_measured": round(kreq / P.shape[0], 5),
                                      "request_fpr_ci95": wilson(kreq, int(P.shape[0])),
                                      "request_fpr_independence_pred": round(1 - (1 - pw) ** W, 5)}
            mw[W] = row
        res["backends"][b] = {"parity_vs_torch_fp32": par, "recall@0.5": recall_table(p, 0.5),
                              "recall@t1": recall_table(p, t1_ref), "sample_fpr@0.5": fpr_samples(p, 0.5),
                              "t1pct_on_510tok_benign_windows": t1_win, "multiwindow_fpr": mw}
    Path(out).write_text(json.dumps(res, indent=1))
    print(json.dumps({b: {"parity": v["parity_vs_torch_fp32"], "mw": {W: {k: v2 for k, v2 in r.items() if k.startswith("thr_0.5")}
                                                                          for W, r in v["multiwindow_fpr"].items()}}
                      for b, v in res["backends"].items()}, indent=1))


if __name__ == "__main__":
    main()
