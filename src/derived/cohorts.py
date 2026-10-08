"""D8 cohorts — who solved each problem, and when; plus the cohort-percentile helper every lane uses.

Table (one row per problem x solver): problem_uid, user_uid, corpus, first_ac_submission_uid, first_ac_ts,
first_ac_has_code, attempts_to_ac, split.

Helper — the main defence against "everyone's code looks alike" false flags:
    cohort_percentile(scores, queries, min_size=30, as_of=True)
  scores:  DataFrame [problem_uid, user_uid, ts, score]   — the reference population's scores (e.g. each
           solver's score on their first accepted submission)
  queries: DataFrame [problem_uid, user_uid, ts, score]   — the submissions to normalise
  returns queries + [cohort_n, pct]: pct = share of the cohort scoring <= the query's score, where the
  cohort is every OTHER user in `scores` for that problem with ts < query ts (as_of=True), or every other
  user (as_of=False). pct is NULL when cohort_n < min_size.
  Per problem, scores are inserted in time order into a sorted list, so it is O(n log n), not pairwise.

usage: python src/derived/cohorts.py       (builds the table)
"""
import bisect, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PARAMS = {"member": "a user with an accepted submission on the problem (team submissions excluded)",
          "member_time": "ts of their first accepted submission", "default_min_size": 30, "default_as_of": True}


def cohort_percentile(scores, queries, min_size=30, as_of=True):
    out_n = np.full(len(queries), -1, dtype=np.int64)
    out_p = np.full(len(queries), np.nan)
    q = queries.reset_index(drop=True)
    for pid, qg in q.groupby("problem_uid", sort=False):
        ref = scores[scores.problem_uid == pid]
        if ref.empty:
            out_n[qg.index] = 0
            continue
        r_ts, r_score, r_user = ref.ts.to_numpy(), ref.score.to_numpy(), ref.user_uid.to_numpy()
        if not as_of:
            allv = np.sort(r_score)
            for i, row in qg.iterrows():
                mine = r_score[r_user == row.user_uid]  # exclude the query user's own reference scores
                n = len(allv) - len(mine)
                le = np.searchsorted(allv, row.score, side="right") - (mine <= row.score).sum()
                out_n[i] = n
                out_p[i] = le / n if n >= min_size else np.nan
            continue
        order = np.argsort(r_ts, kind="stable")
        r_ts, r_score, r_user = r_ts[order], r_score[order], r_user[order]
        sorted_scores, per_user, j = [], {}, 0
        for i in qg.sort_values("ts", kind="stable").index:
            t, x, u = q.at[i, "ts"], q.at[i, "score"], q.at[i, "user_uid"]
            while j < len(r_ts) and r_ts[j] < t:  # strictly earlier only
                bisect.insort(sorted_scores, r_score[j])
                per_user.setdefault(r_user[j], []).append(r_score[j])
                j += 1
            mine = per_user.get(u, [])
            n = len(sorted_scores) - len(mine)
            le = bisect.bisect_right(sorted_scores, x) - sum(1 for v in mine if v <= x)
            out_n[i] = n
            out_p[i] = le / n if n >= min_size else np.nan
    return q.assign(cohort_n=out_n, pct=out_p)


if __name__ == "__main__":
    from src.derived.common import Build
    with Build("cohorts", 1, PARAMS, __file__, derived_inputs=["attempts", "splits"]) as b:
        c = b.c
        c.execute("""create table cohorts as select a.problem_uid, a.user_uid, a.corpus,
                       arg_min(a.submission_uid, a.ts_unix) first_ac_submission_uid, min(a.ts_unix) first_ac_ts,
                       arg_min(s.code_sha256 is not null, a.ts_unix) first_ac_has_code,
                       min(a.attempt_no) attempts_to_ac, any_value(sp.split) split
                     from attempts a join submissions s on s.submission_uid = a.submission_uid
                     join splits sp on sp.user_uid = a.user_uid
                     where a.event_type in ('first_try_ac', 'ac_after_fail')
                     group by 1, 2, 3""")
        b.write("cohorts", order="problem_uid, first_ac_ts")
        one = b.one
        b.check("one row per (problem, solver)", one("select count(*) - count(distinct (problem_uid, user_uid)) from cohorts") == 0)
        b.check("every first AC is an accepted submission", one("""select count(*) from cohorts c join submissions s
                 on s.submission_uid = c.first_ac_submission_uid where not s.accepted""") == 0)
        b.check("first AC is the earliest AC", one("""select count(*) from cohorts c join attempts a using (problem_uid, user_uid)
                 where a.event_type in ('first_try_ac', 'ac_after_fail') and a.ts_unix < c.first_ac_ts""") == 0)
        sizes = c.execute("""select corpus, count(*) problems, median(n) median_solvers,
                               count(*) filter (where n_code >= 30) problems_30plus_solvers_with_code,
                               count(*) filter (where n_code >= 50) problems_50plus_solvers_with_code
                             from (select corpus, problem_uid, count(*) n, count(*) filter (where first_ac_has_code) n_code
                                   from cohorts group by 1, 2) group by 1 order by 1""").fetchall()
        b.count("per_corpus", {r[0]: dict(zip(["problems", "median_solvers", "problems_30plus_with_code", "problems_50plus_with_code"], r[1:])) for r in sizes})
