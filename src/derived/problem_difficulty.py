"""D5 problem_difficulty — a difficulty for every problem, fitted from first-attempt outcomes.

Model (1PL Rasch): P(first attempt accepted) = sigmoid(theta_user - b_problem), fitted as an L2-regularised
logistic regression on one-hot user and problem indicators. Fitted per corpus, on TRAIN-split users only
(D1), so a test user's outcomes never shape the difficulty used to judge them. Judge-noise attempts are
excluded. Problems with fewer than MIN_ATTEMPTERS train users get no difficulty.

Fitted for every corpus, not only CodeNet (which has none), so the method is validated three ways:
  atcoder   b vs kenkoooo's published IRT difficulty on the same problems     (is the method sound?)
  codenet   b vs AtCoder IRT via D6 problem_links                              (are the values we use sound?)
  codeforces b vs the official problem rating                                  (when that corpus is built)
Known bias, stated: users choose which problems to attempt, so b is relative to who tried.

Output per problem: b (logit; higher = harder), b_pct (percentile within corpus), n_attempters, first_try_rate.
v2 (2026-10-08): rows in a fixed order and tol 1e-8. v1 used sklearn's default tol 1e-4, which stopped early: the
same data in two row orders gave per-problem differences up to 1.2 logits (rho 0.990 between orderings); at 1e-8
the two agree to 0.0003. v1 removed.

usage: python src/derived/problem_difficulty.py
"""
import sys
from pathlib import Path
import numpy as np
from scipy import sparse
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

PARAMS = {"model": "1PL: logit P(first-try AC) = theta_u - b_p", "fit_on": "train split users", "C": 1.0,
          "solver": "lbfgs", "tol": 1e-8, "max_iter": 5000, "row_order": "user_uid, problem_uid",
          "min_attempters": 20, "exclude": "judge_noise first attempts"}

with Build("problem_difficulty", 2, PARAMS, __file__, derived_inputs=["attempts", "splits", "problem_links"]) as b:
    c = b.c
    rows_out, validation = [], {}
    corpora = [r[0] for r in c.execute("select distinct corpus from attempts order by 1").fetchall()]
    for corpus in corpora:
        df = c.execute(f"""select a.user_uid, a.problem_uid, (a.event_type = 'first_try_ac')::int y
                           from attempts a join splits s using (user_uid)
                           where a.corpus = '{corpus}' and a.attempt_no = 1 and not a.judge_noise and s.split = 'train'
                           order by a.user_uid, a.problem_uid""").df()
        users, ui = np.unique(df.user_uid, return_inverse=True)
        probs, pi = np.unique(df.problem_uid, return_inverse=True)
        n, nu = len(df), len(users)
        X = sparse.csr_matrix((np.r_[np.ones(n), -np.ones(n)], (np.r_[np.arange(n), np.arange(n)], np.r_[ui, nu + pi])),
                              shape=(n, nu + len(probs)))
        m = LogisticRegression(C=PARAMS["C"], solver=PARAMS["solver"], tol=PARAMS["tol"], max_iter=PARAMS["max_iter"], fit_intercept=True)
        m.fit(X, df.y.values)
        bvals = m.coef_[0][nu:]
        stats = df.groupby("problem_uid").agg(n_attempters=("y", "size"), first_try_rate=("y", "mean"))
        stats["b"] = bvals - np.median(bvals)  # centre each corpus at its median problem
        stats.loc[stats.n_attempters < PARAMS["min_attempters"], "b"] = np.nan
        stats["b_pct"] = stats.b.rank(pct=True)
        stats = stats.reset_index()
        stats["corpus"] = corpus
        rows_out.append(stats)
        validation[corpus] = {"first_attempts": n, "train_users": nu, "problems_seen": len(probs),
                              "problems_with_b": int(stats.b.notna().sum()), "n_iter": int(m.n_iter_[0]),
                              "converged": bool(m.n_iter_[0] < PARAMS["max_iter"])}
        print(f"  {corpus}: {n} first attempts, {nu} users, {len(probs)} problems", flush=True)
    import pandas as pd
    out = pa.Table.from_pandas(pd.concat(rows_out, ignore_index=True)[["problem_uid", "corpus", "b", "b_pct", "n_attempters", "first_try_rate"]])
    c.register("o_arrow", out)
    c.execute("create table problem_difficulty as select * from o_arrow")
    b.write("problem_difficulty", order="problem_uid")

    def rho(sql):
        x = c.execute(sql).fetchall()
        return (round(float(spearmanr([a for a, _ in x], [z for _, z in x]).statistic), 3), len(x)) if len(x) > 10 else (None, len(x))

    validation["atcoder_vs_kenkoooo_irt"] = rho("""select d.b, p.difficulty from problem_difficulty d join problems p using (problem_uid)
        where d.corpus='atcoder' and d.b is not null and p.difficulty is not null and not coalesce(p.irt_is_experimental, false)""")
    validation["codenet_vs_atcoder_irt_via_links"] = rho("""select d.b, p.difficulty from problem_difficulty d
        join problem_links l on l.codenet_problem_uid = d.problem_uid join problems p on p.problem_uid = l.atcoder_problem_uid
        where d.b is not null and p.difficulty is not null and not coalesce(p.irt_is_experimental, false)""")
    validation["codeforces_vs_rating"] = rho("""select d.b, p.difficulty from problem_difficulty d join problems p using (problem_uid)
        where d.corpus='codeforces' and d.b is not null and p.difficulty is not null""")
    validation["codenet_first_try_rate_vs_b"] = rho("select b, -first_try_rate from problem_difficulty where corpus='codenet' and b is not null")
    b.count("validation_spearman_(rho, n)", validation)
    b.check("every corpus fit converged", all(v["converged"] for k, v in validation.items() if k in corpora))
    b.check("one row per problem", b.one("select count(*) - count(distinct problem_uid) from problem_difficulty") == 0)
    for k in ("atcoder_vs_kenkoooo_irt", "codenet_vs_atcoder_irt_via_links"):
        b.check(f"{k} rho >= 0.6", validation[k][0] is not None and validation[k][0] >= 0.6)
