"""Leakage tests for the as-of-t profile definitions (src/derived/profiles.py).

The as-of rule is the property every metric depends on. These tests run the exact production SQL on small
synthetic histories and check that nothing at or after t can reach a profile computed for time t.
"""
import duckdb
import numpy as np
import pandas as pd
import pytest
from src.derived.profiles import style_sql, skill_sql


def history(seed=0, users=6, n=60):
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(users):
        ts = np.sort(rng.integers(0, 10_000, n))
        ts[5] = ts[4]  # a same-second pair on purpose
        for i, t in enumerate(ts):
            rows.append({"submission_uid": f"u{u}s{i}", "user_uid": f"u{u}", "ts_unix": int(t),
                         "x": float(rng.normal()), "attempted": int(rng.random() < .7), "solved": int(rng.random() < .5),
                         "first_try": int(rng.random() < .3), "b_solved": float(rng.normal())})
    return pd.DataFrame(rows)


def run(df, sql):
    c = duckdb.connect()
    c.register("code_acs", df)
    c.register("skill_events", df)
    return c.execute(sql).df().set_index("submission_uid").sort_index()


@pytest.mark.parametrize("which", ["style", "skill"])
def test_deleting_the_future_changes_nothing_in_the_past(which):
    df = history()
    sql = style_sql(["x"]) if which == "style" else skill_sql()
    full = run(df, sql)
    t0 = int(df.ts_unix.median())
    past_only = run(df[df.ts_unix < t0], sql)
    pd.testing.assert_frame_equal(full.loc[past_only.index], past_only, check_dtype=False)


@pytest.mark.parametrize("which", ["style", "skill"])
def test_altering_the_future_changes_nothing_in_the_past(which):
    df = history(1)
    sql = style_sql(["x"]) if which == "style" else skill_sql()
    t0 = int(df.ts_unix.median())
    altered = df.copy()
    future = altered.ts_unix >= t0
    altered.loc[future, ["x", "b_solved"]] += 1000.0
    altered.loc[future, ["attempted", "solved", "first_try"]] = 1
    a, b = run(df, sql), run(altered, sql)
    past = df.loc[~future, "submission_uid"]
    pd.testing.assert_frame_equal(a.loc[past], b.loc[past], check_dtype=False)


def test_a_row_never_sees_itself_or_its_same_second_twin():
    df = history(2)
    p = run(df, style_sql(["x"]))
    for r in df.itertuples():
        earlier = df[(df.user_uid == r.user_uid) & (df.ts_unix < r.ts_unix)]
        assert p.at[r.submission_uid, "prior_n_code"] == len(earlier)
        want = earlier.x.mean() if len(earlier) else np.nan
        got = p.at[r.submission_uid, "x_mean"]
        assert (np.isnan(want) and pd.isna(got)) or abs(got - want) < 1e-9


def test_users_do_not_leak_into_each_other():
    df = history(3)
    sql = style_sql(["x"])
    base = run(df, sql)
    other = df.copy()
    other.loc[other.user_uid != "u0", "x"] = 1e9
    changed = run(other, sql)
    u0 = df.loc[df.user_uid == "u0", "submission_uid"]
    pd.testing.assert_frame_equal(base.loc[u0], changed.loc[u0], check_dtype=False)
