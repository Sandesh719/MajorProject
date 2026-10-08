"""The harness's metrics and guardrails, on synthetic frames with known answers."""
import numpy as np
import pandas as pd
import pytest
from src.eval.metrics import evaluate


def frame(n_users=200, nat_per=10, pairs_per=3, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(n_users):
        for i in range(nat_per):
            rows.append({"row_id": f"n{u}_{i}", "kind": "natural", "label": 0, "pair_id": None, "user_uid": f"u{u}",
                         "corpus": "c", "difficulty_band": rng.choice(["easy", "hard"])})
        for i in range(pairs_per):
            for k, lab in (("p1_neg", 0), ("p1_pos", 1)):
                rows.append({"row_id": f"p{u}_{i}:{k}", "kind": k, "label": lab, "pair_id": f"p{u}_{i}", "user_uid": f"u{u}",
                             "corpus": "c", "difficulty_band": "easy"})
    return pd.DataFrame(rows)


def test_oracle_is_perfect():
    f = frame()
    m = evaluate(f, pd.Series(f.label.astype(float).values, index=f.row_id), n_boot=20)["all"]
    assert m["recall@5%"][0] == 1.0 and m["auroc"][0] == 1.0 and m["paired_win"][0] == 1.0


def test_random_sits_at_the_budget():
    f = frame(n_users=400)
    rng = np.random.default_rng(1)
    m = evaluate(f, pd.Series(rng.random(len(f)), index=f.row_id), n_boot=20)["all"]
    assert abs(m["recall@5%"][0] - 0.05) < 0.02
    assert abs(m["genuine_flag@5%"][0] - 0.05) < 0.02
    assert abs(m["auroc"][0] - 0.5) < 0.05 and abs(m["paired_win"][0] - 0.5) < 0.05


def test_constant_scores_tie_to_half_and_flag_nothing():
    f = frame()
    m = evaluate(f, pd.Series(1.0, index=f.row_id), n_boot=10)["all"]
    assert m["paired_win"][0] == 0.5 and m["auroc"][0] == 0.5
    assert m["recall@5%"][0] == 0.0 and m["genuine_flag@5%"][0] == 0.0  # ties at the threshold are never flagged


def test_oracle_never_flags_genuine_code():
    # every natural and p1_neg row ties at 0: flagging ties would flag all genuine code (the bug this test pins)
    f = frame()
    m = evaluate(f, pd.Series(f.label.astype(float).values, index=f.row_id), n_boot=10)["all"]
    assert m["genuine_flag@5%"][0] == 0.0 and m["genuine_flag@1%"][0] == 0.0


def test_flagging_never_exceeds_the_budget_on_natural_rows():
    f = frame(n_users=300)
    rng = np.random.default_rng(3)
    s = pd.Series(rng.integers(0, 5, len(f)).astype(float), index=f.row_id)  # heavy ties
    from src.eval.metrics import _wquantile
    nat = s[f.kind.eq("natural").values].to_numpy()
    tau = _wquantile(nat, np.ones(len(nat)), 0.95)
    assert (nat > tau).mean() <= 0.05


def test_nan_is_never_flagged_and_lowers_coverage():
    f = frame()
    s = pd.Series(f.label.astype(float).values, index=f.row_id)
    s[f.kind.eq("p1_pos").values] = np.nan
    m = evaluate(f, s, n_boot=10)["all"]
    assert m["recall@5%"][0] == 0.0
    assert m["coverage"] < 1.0


def test_interval_contains_point_and_scopes_exist():
    f = frame()
    rng = np.random.default_rng(2)
    r = evaluate(f, pd.Series(rng.random(len(f)) + f.label.values * 0.5, index=f.row_id), n_boot=50)
    p, lo, hi = r["all"]["auroc"]
    assert lo <= p <= hi
    assert {"corpus=c", "difficulty_band=easy", "difficulty_band=hard"} <= set(r)


def test_guardrails(tmp_path, monkeypatch):
    import src.eval.harness as h
    f = frame()
    f["split"] = "val"
    monkeypatch.setattr(h, "load_frame", lambda split=None: f if split in (None, "val") else f.assign(split="train").iloc[:0])
    monkeypatch.setattr(h, "RUNS", tmp_path)
    monkeypatch.setattr(h, "train_users", lambda: {"u0"})
    s = pd.Series(0.0, index=f.row_id)
    with pytest.raises(ValueError, match="not in the val frame"):
        h.record_run("m", pd.concat([s, pd.Series([1.0], index=["bogus"])]), "val", __file__)
    with pytest.raises(ValueError, match="have no score"):
        h.record_run("m", s.iloc[1:], "val", __file__)
    with pytest.raises(ValueError, match="not train-split users"):
        h.record_run("m", s, "val", __file__, fit_users={"u0", "u5"})
    with pytest.raises(ValueError, match="final=True"):
        h.record_run("m", s, "test", __file__)
    with pytest.raises(ValueError, match="'train' rows are for fitting"):
        h.record_run("m", s, "train", __file__)
