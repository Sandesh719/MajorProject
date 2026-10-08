"""The evaluation harness: one frozen exam (derived eval_frame), one metric definition, one run registry.

    from src.eval.harness import load_frame, record_run
    frame = load_frame("val")                         # rows to score: row_id, user_uid, problem_uid, ts_unix, code_sha256, ...
    scores = my_method(frame)                         # pd.Series indexed by row_id; higher = more suspicious
    record_run("lane_a_v0", scores, split="val", fit_users=train_users_used, params={...}, script=__file__)

Guardrails — a run is refused when:
  - it scores rows outside the frame's split, or misses some (NaN is allowed: "could not score");
  - anything it fitted on (fit_users) is not a train-split user — val/test users must never shape a method;
  - split is 'train' (training rows are for fitting, not for reporting).
Test-split runs need final=True: tune on val, report test once. Every test run is counted in run.json, so
repeated test evaluation is visible.

Each run writes runs/<method>/<timestamp>-<split>/ : scores.parquet, metrics.json, run.json (frame version and
recipe sha256, script sha256, params, fit-user count). docs/RESULTS.md is generated from these files only.
"""
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.derived.common import latest  # noqa: E402
from src.eval.metrics import evaluate, HEADLINE  # noqa: E402

RUNS = ROOT / "runs"


def frame_dir():
    return latest("eval_frame")


def load_frame(split=None):
    d = frame_dir()
    q = f"select * from read_parquet('{d}/**/*.parquet', hive_partitioning=true)"
    if split:
        q += f" where split = '{split}'"
    return duckdb.sql(q).df()


def train_users():
    return set(load_frame("train").user_uid)


def record_run(method, scores, split, script, params=None, fit_users=(), final=False, notes="", n_boot=500):
    if split not in ("val", "test"):
        raise ValueError("report on 'val' or 'test'; 'train' rows are for fitting")
    if split == "test" and not final:
        raise ValueError("test-split runs need final=True: tune on val, report test once")
    frame = load_frame(split)
    scores = pd.Series(scores, dtype=float)
    extra = set(scores.index) - set(frame.row_id)
    missing = set(frame.row_id) - set(scores.index)
    if extra:
        raise ValueError(f"{len(extra)} scored rows are not in the {split} frame (e.g. {sorted(extra)[:3]})")
    if missing:
        raise ValueError(f"{len(missing)} {split} rows have no score — return NaN for rows the method cannot score")
    fit = set(fit_users)
    leak = fit - train_users() if fit else set()
    if leak:
        raise ValueError(f"{len(leak)} fit users are not train-split users — refusing (val/test must not shape a method)")
    metrics = evaluate(frame, scores, n_boot=n_boot)
    d = frame_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RUNS / method / f"{stamp}-{split}"
    out.mkdir(parents=True)
    scores.rename("score").rename_axis("row_id").reset_index().to_parquet(out / "scores.parquet", index=False)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n")
    prior_test = sum(1 for p in (RUNS / method).glob("*-test")) - (split == "test")
    run = {"method": method, "split": split, "final": final, "at": stamp,
           "frame": str(d.relative_to(ROOT)), "frame_recipe_sha256": hashlib.sha256((d / "recipe.json").read_bytes()).hexdigest(),
           "script": str(Path(script).resolve().relative_to(ROOT)), "script_sha256": hashlib.sha256(Path(script).read_bytes()).hexdigest(),
           "params": params or {}, "n_fit_users": len(fit), "previous_test_runs_of_this_method": prior_test, "notes": notes}
    (out / "run.json").write_text(json.dumps(run, indent=1, default=str) + "\n")
    a = metrics["all"]
    print(f"{method} [{split}] recall@{HEADLINE:.0%}={a[f'recall@{HEADLINE:.0%}'][0]} "
          f"auroc={a['auroc'][0]} paired_win={a['paired_win'][0]} coverage={a['coverage']} -> {out.relative_to(ROOT)}")
    return out
