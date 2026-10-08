"""Reference scorers that bound every results table — they prove the harness, not a method.

  random  uniform scores, seed 0     -> recall@b ~ b, auroc ~ 0.5, paired_win ~ 0.5  (the floor)
  oracle  the label itself           -> recall 1, auroc 1, paired_win 1               (the ceiling; uses labels!)
Neither fits on anything, so they are reported on val and, once, on test.
usage: python src/eval/reference.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.eval.harness import load_frame, record_run  # noqa: E402

if __name__ == "__main__":
    for split in ("val", "test"):
        f = load_frame(split)
        rng = np.random.default_rng(0)
        record_run("ref_random", pd.Series(rng.random(len(f)), index=f.row_id), split, __file__,
                   params={"seed": 0}, final=split == "test", notes="floor: uniform random scores")
        record_run("ref_oracle", pd.Series(f.label.astype(float).values, index=f.row_id), split, __file__,
                   final=split == "test", notes="ceiling: the label itself — a harness check, not a method")
