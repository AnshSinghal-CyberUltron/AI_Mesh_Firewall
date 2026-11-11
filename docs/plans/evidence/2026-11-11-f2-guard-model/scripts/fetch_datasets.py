"""Download the public evaluation datasets at PINNED revisions into $F2_OUT_DIR/datasets.

  .f2-scratch/venv/bin/python -I fetch_datasets.py

Downloaded files are untrusted data: they are only read as parquet/csv/json by eval_datasets.py
(run with ``python -I``), never executed or imported. The dataset bytes are NOT committed; the
repository id, revision, licence, file path and sha256 of every file are written to
results/datasets.json so the evaluation is reproducible.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from f2common import EVIDENCE_DIR, OUT_DIR, sha256_file
from huggingface_hub import hf_hub_download

#: (repo id, pinned revision, licence as declared on the dataset card, files)
DATASETS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("deepset/prompt-injections", "4f61ecb038e9c3fb77e21034b22511b523772cdd", "apache-2.0",
     ("data/train-00000-of-00001-9564e8b05b4757ab.parquet",
      "data/test-00000-of-00001-701d16158af87368.parquet")),
    ("jackhhao/jailbreak-classification", "2f2ceeb39658696fd3f462403562b6eea5306287",
     "apache-2.0", ("default/jailbreak_dataset_full.csv",)),
    ("Lakera/gandalf_ignore_instructions", "04737b65e90a6794ec227012e4a255a7def6344b", "mit",
     ("data/train-00000-of-00001-ded53be747ff55cd.parquet",
      "data/validation-00000-of-00001-94481a2a09ff2fff.parquet",
      "data/test-00000-of-00001-bc92128b9288a6d1.parquet")),
    ("TrustAIRLab/in-the-wild-jailbreak-prompts", "a10aab8eff1c73165a442d4464dce192bd28b9c5",
     "mit", ("jailbreak_2023_12_25/train-00000-of-00001.parquet",
             "regular_2023_12_25/train-00000-of-00001.parquet")),
    ("databricks/databricks-dolly-15k", "bdd27f4d94b9c1f951818a7da7fd7aeea5dbff1a",
     "cc-by-sa-3.0", ("databricks-dolly-15k.jsonl",)),
    ("OpenAssistant/oasst1", "fdf72ae0827c1cda404aff25b6603abec9e3399b", "apache-2.0",
     ("data/validation-00000-of-00001-134b8fd0c89408b6.parquet",)),
    ("yanismiraoui/prompt_injections", "bd55359f2f332afc35f277ac3dd08f7111b024c9", "apache-2.0",
     ("prompt_injections.csv",)),
    ("microsoft/llmail-inject-challenge", "1063bdf01ec8762b812d5e06ee768a06faa5a6f7", "mit",
     ("data/emails_for_fp_tests.json",)),  # the labelled submissions carry labels, not texts
)


def main() -> int:
    root = OUT_DIR / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    record = []
    for repo, rev, lic, files in DATASETS:
        for name in files:
            try:
                path = Path(hf_hub_download(repo, name, repo_type="dataset", revision=rev,
                                            local_dir=str(root / repo.replace("/", "__"))))
                record.append({"repo": repo, "revision": rev, "license": lic, "file": name,
                               "bytes": path.stat().st_size, "sha256": sha256_file(path)})
                print("ok", repo, name, path.stat().st_size)
            except Exception as exc:  # noqa: BLE001 — record a refused download, do not work around
                record.append({"repo": repo, "revision": rev, "license": lic, "file": name,
                               "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                print("FAILED", repo, name, type(exc).__name__)
    out = EVIDENCE_DIR / "results" / "datasets.json"
    out.write_text(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
