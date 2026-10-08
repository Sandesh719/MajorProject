"""D7 profiles — what each user looked like using ONLY their submissions strictly before time t.

The "model of this specific person" every lane compares against, so the as-of rule is enforced in one place:
every aggregate is a window `RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` over the integer timestamp,
partitioned by user — a row sees only rows from earlier seconds, never itself or anything in the same second.

One row per ELIGIBLE submission: accepted, has code, and the user had >= MIN_PRIOR strictly earlier accepted
submissions with code. Columns:
  style (lane A)   prior_n_code; for each D4 feature f: f_mean, f_std over the user's prior accepted code
  skill (lane B)   prior_n_attempted, prior_n_solved, prior_first_try_rate, prior_mean_b_solved,
                   prior_max_b_solved, prior_days_active, days_since_prev_ac
Template lines (as-of) are a lane-A step, not here: they need the code text.

`profile_sql(...)` is the single definition; tests/test_profiles_leakage.py runs it on altered data.
usage: python src/derived/profiles.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

MIN_PRIOR = 10
ASOF = "over (partition by user_uid order by ts_unix range between unbounded preceding and 1 preceding)"


def style_sql(features, code_acs="code_acs"):
    """code_acs: (submission_uid, user_uid, ts_unix, <features>) for accepted submissions with code."""
    aggs = ",\n  ".join(f"avg({f}) {ASOF} {f}_mean, stddev_samp({f}) {ASOF} {f}_std" for f in features)
    return f"""select submission_uid, user_uid, ts_unix, count(*) {ASOF} prior_n_code,
  {aggs}
from {code_acs}"""


def skill_sql(events="skill_events"):
    """events: (submission_uid, user_uid, ts_unix, attempted, solved, first_try, b_solved) — one row per attempt."""
    return f"""select submission_uid, user_uid, ts_unix,
  coalesce(sum(attempted) {ASOF}, 0) prior_n_attempted,
  coalesce(sum(solved) {ASOF}, 0) prior_n_solved,
  sum(first_try) {ASOF} / nullif(sum(attempted) {ASOF}, 0) prior_first_try_rate,
  avg(b_solved) {ASOF} prior_mean_b_solved,
  max(b_solved) {ASOF} prior_max_b_solved,
  (ts_unix - min(ts_unix) {ASOF}) / 86400.0 prior_days_active,
  (ts_unix - max(case when solved = 1 then ts_unix end) {ASOF}) / 86400.0 days_since_prev_ac
from {events}"""


PARAMS = {"min_prior_ac_with_code": MIN_PRIOR, "window": ASOF, "style": "mean and sample std of every D4 feature",
          "skill": ["prior_n_attempted", "prior_n_solved", "prior_first_try_rate", "prior_mean_b_solved",
                    "prior_max_b_solved", "prior_days_active", "days_since_prev_ac"],
          "skill_attempted": "first attempt on a problem", "skill_solved": "first accepted submission on a problem",
          "difficulty": "D5 b (NULL when the problem has no b)"}

if __name__ == "__main__":
    from src.derived.code_features import FEATURES
    with Build("profiles", 1, PARAMS, __file__, derived_inputs=["attempts", "code_features", "problem_difficulty", "positives_p1"], memory="3GB") as b:
        c = b.c
        feats = list(FEATURES)
        # D4 marks an inapplicable feature NaN; as NULL it simply drops out of that user's mean/std
        nan_to_null = ", ".join(f"case when isnan(f.{x}) then null else f.{x} end as {x}" for x in feats)
        c.execute(f"""create table code_acs as select s.submission_uid, s.user_uid, s.ts_unix, s.corpus, {nan_to_null}
                     from submissions s join code_features f using (code_sha256)
                     where s.accepted and s.user_uid is not null""")
        c.execute(f"create table style as {style_sql(feats)}")
        c.execute("""create table skill_events as select a.submission_uid, a.user_uid, a.ts_unix,
                       (a.attempt_no = 1)::int attempted,
                       (a.event_type in ('first_try_ac', 'ac_after_fail'))::int solved,
                       (a.event_type = 'first_try_ac')::int first_try,
                       case when a.event_type in ('first_try_ac', 'ac_after_fail') then d.b end b_solved
                     from attempts a left join problem_difficulty d using (problem_uid)
                     where a.user_uid in (select distinct user_uid from code_acs)""")
        c.execute(f"create table skill as {skill_sql()}")
        c.execute(f"""create table profiles as select st.*, sk.* exclude (submission_uid, user_uid, ts_unix), ca.corpus
                      from style st join skill sk using (submission_uid) join code_acs ca using (submission_uid)
                      where st.prior_n_code >= {MIN_PRIOR}""")
        b.write("profiles", order="user_uid, ts_unix, submission_uid")

        one = b.one
        b.check("one row per eligible submission", one("select count(*) - count(distinct submission_uid) from profiles") == 0)
        b.check(f"every profile has >= {MIN_PRIOR} strictly earlier accepted submissions with code", one(f"""
            select count(*) from (select * from profiles order by hash(submission_uid) limit 2000) p
            where (select count(*) from code_acs x where x.user_uid = p.user_uid and x.ts_unix < p.ts_unix) <> p.prior_n_code""") == 0)
        f0 = feats[0]
        b.check(f"{f0}_mean equals a recomputation from strictly earlier rows", one(f"""
            select count(*) from (select * from profiles order by hash(submission_uid) limit 2000) p
            where abs((select avg(x.{f0}) from code_acs x where x.user_uid = p.user_uid and x.ts_unix < p.ts_unix) - p.{f0}_mean) > 1e-9""") == 0)
        if not b.smoke:
            b.check("D10 targets all have a profile", one("""select count(*) from (select distinct target_submission_uid from
            positives_p1) t anti join profiles p on p.submission_uid = t.target_submission_uid""") == 0)
        b.count("profiles_by_corpus", dict(c.execute("select corpus, count(*) from profiles group by 1").fetchall()))
        b.count("users", one("select count(distinct user_uid) from profiles"))
        b.count("median_prior_n_code", one("select median(prior_n_code) from profiles"))
