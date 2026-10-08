"""The cohort-percentile helper: as-of-t, self-exclusion, min size, and agreement with brute force."""
import numpy as np
import pandas as pd
from src.derived.cohorts import cohort_percentile


def brute(scores, queries, min_size, as_of):
    out = []
    for q in queries.itertuples():
        ref = scores[(scores.problem_uid == q.problem_uid) & (scores.user_uid != q.user_uid)]
        if as_of:
            ref = ref[ref.ts < q.ts]
        n = len(ref)
        out.append((n, (ref.score <= q.score).mean() if n >= min_size else np.nan))
    return out


def test_small_hand_example():
    s = pd.DataFrame({"problem_uid": ["p"] * 4, "user_uid": ["a", "b", "c", "q"], "ts": [1, 2, 3, 0], "score": [0.1, 0.5, 0.9, 0.0]})
    q = pd.DataFrame({"problem_uid": ["p"], "user_uid": ["q"], "ts": [3], "score": [0.6]})
    r = cohort_percentile(s, q, min_size=1)
    assert r.cohort_n[0] == 2              # a, b are earlier; c is at ts=3 (not strictly earlier); q itself excluded
    assert r.pct[0] == 1.0                 # 0.6 >= 0.1 and 0.5
    r2 = cohort_percentile(s, q, min_size=1, as_of=False)
    assert r2.cohort_n[0] == 3 and abs(r2.pct[0] - 2 / 3) < 1e-12


def test_min_size_gives_null():
    s = pd.DataFrame({"problem_uid": ["p"] * 3, "user_uid": ["a", "b", "c"], "ts": [1, 2, 3], "score": [1.0, 2.0, 3.0]})
    q = pd.DataFrame({"problem_uid": ["p"], "user_uid": ["z"], "ts": [10], "score": [2.0]})
    assert np.isnan(cohort_percentile(s, q, min_size=5).pct[0])


def test_unknown_problem_has_empty_cohort():
    s = pd.DataFrame({"problem_uid": ["p"], "user_uid": ["a"], "ts": [1], "score": [1.0]})
    q = pd.DataFrame({"problem_uid": ["other"], "user_uid": ["z"], "ts": [10], "score": [2.0]})
    r = cohort_percentile(s, q, min_size=1)
    assert r.cohort_n[0] == 0 and np.isnan(r.pct[0])


def test_matches_brute_force_on_random_data():
    rng = np.random.default_rng(0)
    n = 600
    s = pd.DataFrame({"problem_uid": rng.choice(["p1", "p2", "p3"], n), "user_uid": rng.choice([f"u{i}" for i in range(80)], n),
                      "ts": rng.integers(0, 1000, n), "score": rng.normal(size=n).round(2)})  # ties included on purpose
    q = s.sample(150, random_state=1).assign(score=lambda d: d.score + rng.normal(0, 0.3, len(d)).round(2))
    for as_of in (True, False):
        got = cohort_percentile(s, q, min_size=5, as_of=as_of)
        want = brute(s, q.reset_index(drop=True), 5, as_of)
        assert list(got.cohort_n) == [w[0] for w in want]
        assert np.allclose(got.pct.to_numpy(), np.array([w[1] for w in want]), equal_nan=True)
