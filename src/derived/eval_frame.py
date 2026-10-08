"""D11 eval_frame — the frozen exam every baseline, lane and fusion model is scored on.

One row per thing to score. A method receives (user_uid, problem_uid, ts_unix, code_sha256) and returns a
score; it must score the code named HERE, not the code of target_submission_uid (for a swap they differ).

  kind = p1_neg    the user's real accepted code (label 0)                     — from D10, paired with p1_pos
  kind = p1_pos    another user's code swapped into the same slot (label 1)    — from D10 (synthetic)
  kind = natural   an unmodified accepted submission with an as-of-t profile   — presumed honest (label 0);
                   up to NATURAL_PER_USER per user, not a D10 target. Sets the review threshold: "flag the
                   top b% of ordinary submissions" is the budget a human reviewer can actually afford.

Every row carries split (D1), corpus, and difficulty_band (tertile of D5 b_pct within corpus; 'unknown' when the
problem has no b). A swap changes only the code, so metadata-only signals (lane B) cannot separate p1_pos from
p1_neg by construction — expected, not a bug. P2/P3 (LLM-generated) kinds will be added as a new version.

usage: python src/derived/eval_frame.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

NATURAL_PER_USER = 20
PARAMS = {"kinds": ["p1_neg", "p1_pos", "natural"], "natural_per_user": NATURAL_PER_USER,
          "natural_from": "D7 profiles (eligible accepted C++ submissions), excluding D10 targets",
          "natural_choice": "lowest sha256('natural:' || submission_uid) per user",
          "difficulty_band": "tertiles of D5 b_pct within corpus: easy < 1/3 <= medium < 2/3 <= hard; else unknown"}

with Build("eval_frame", 1, PARAMS, __file__,
           derived_inputs=["positives_p1", "profiles", "splits", "problem_difficulty"], is_synthetic=True) as b:
    c = b.c
    c.execute(f"""create table nat as select p.submission_uid, p.user_uid, s.problem_uid, p.ts_unix, s.code_sha256, p.corpus
                  from (select *, row_number() over (partition by user_uid order by sha256('natural:' || submission_uid)) rn
                        from profiles where submission_uid not in (select target_submission_uid from positives_p1)) p
                  join submissions s using (submission_uid) where p.rn <= {NATURAL_PER_USER}""")
    c.execute("""create table rows as
                 select pair_id || case when label = 1 then ':pos' else ':neg' end as row_id,
                        case when label = 1 then 'p1_pos' else 'p1_neg' end as kind, label, pair_id,
                        corpus, user_uid, problem_uid, ts_unix, code_sha256, target_submission_uid,
                        donor_user_uid, donor_submission_uid
                 from positives_p1
                 union all
                 select 'natural:' || submission_uid, 'natural', 0, null, corpus, user_uid, problem_uid, ts_unix,
                        code_sha256, submission_uid, null, null
                 from nat""")
    c.execute("""create table eval_frame as select r.*, sp.split,
                   case when d.b_pct is null then 'unknown' when d.b_pct < 1/3 then 'easy'
                        when d.b_pct < 2/3 then 'medium' else 'hard' end as difficulty_band
                 from rows r join splits sp using (user_uid) left join problem_difficulty d using (problem_uid)""")
    b.write("eval_frame", order="split, kind, row_id")

    one = b.one
    b.check("row_id unique", one("select count(*) - count(distinct row_id) from eval_frame") == 0)
    b.check("every row has a split", one("select count(*) from eval_frame where split is null") == 0)
    b.check("labels match kinds", one("""select count(*) from eval_frame where (kind = 'p1_pos') <> (label = 1)""") == 0)
    b.check("natural rows are never D10 targets", one("""select count(*) from eval_frame n where n.kind = 'natural'
             and n.target_submission_uid in (select target_submission_uid from positives_p1)""") == 0)
    b.check("every pair is in one split", one("""select count(*) from (select pair_id from eval_frame where pair_id is not null
             group by 1 having count(distinct split) > 1)""") == 0)
    b.check("every row has code", one("select count(*) from eval_frame where code_sha256 is null") == 0)
    b.count("rows_by_split_kind", {f"{s}/{k}": n for s, k, n in c.execute(
        "select split, kind, count(*) from eval_frame group by 1, 2 order by 1, 2").fetchall()})
    b.count("users_by_split", dict(c.execute("select split, count(distinct user_uid) from eval_frame group by 1 order by 1").fetchall()))
    b.count("difficulty_band", dict(c.execute("select difficulty_band, count(*) from eval_frame group by 1 order by 1").fetchall()))
