"""D10 positives_p1 — synthetic "someone else wrote this" cases (ghostwriter swaps). is_synthetic = true.

For a target submission s (user u, problem p, time t), a positive replaces s's code with another user's
accepted code for the same p; the paired negative is s's own code. Same user, same problem, same time —
only the authorship differs, so a lane that separates them is detecting authorship, not problem or era.

Language: C++ only, for targets, their history and donors — a donor in another language would be detectable
by language alone, and D3/D4/D7 are C++-only. (v1 counted any language; replaced by v2 before any use.)

Target eligibility: s is accepted, has C++ code, and u had >= MIN_PRIOR earlier accepted submissions with code
(strictly before t) — the same history rule D7 profiles use. At most PER_USER targets per user, chosen by
hash. Donor: a different user's first accepted code on p, chosen by hash; donor_solved_before_t says whether
the donor's solution existed at time t (an as-of-realistic ghostwriter) — the default positive uses only such
donors when one exists.

Rows: pair_id, label (1 = swapped, 0 = original), split, target_submission_uid, user_uid, problem_uid, ts_unix,
code_sha256 (the code to score), donor_user_uid, donor_submission_uid, donor_solved_before_t.
usage: python src/derived/positives_p1.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

PARAMS = {"min_prior_ac_with_code": 10, "per_user": 5, "corpora": ["codenet", "codeforces"], "language": "C++",
          "target_choice": "lowest sha256('p1-target:' || submission_uid) among eligible, per user",
          "donor_choice": "lowest sha256('p1-donor:' || target || donor_submission) among other solvers with code; "
                          "donors who solved before t preferred"}

with Build("positives_p1", 2, PARAMS, __file__, derived_inputs=["attempts", "splits", "cohorts"], is_synthetic=True) as b:
    c = b.c
    k, per_user = PARAMS["min_prior_ac_with_code"], PARAMS["per_user"]
    corpora = ", ".join(f"'{x}'" for x in PARAMS["corpora"])
    c.execute(f"""create table acs as select s.submission_uid, s.corpus, s.user_uid, s.problem_uid, s.ts_unix, s.code_sha256,
                    count(*) over (partition by s.user_uid order by s.ts_unix, s.submission_uid
                                   rows between unbounded preceding and 1 preceding) prior_ac_with_code
                  from submissions s where s.accepted and s.code_sha256 is not null and s.user_uid is not null
                    and s.corpus in ({corpora}) and s.lang_family = 'C++'""")
    # strictly earlier: prior_ac_with_code counts rows before this one in (ts, uid) order, so subtract the
    # same-second rows that precede it — a submission never counts one made in the same second
    c.execute(f"""create table eligible as select * from (
                    select *, prior_ac_with_code - (row_number() over (partition by user_uid, ts_unix order by submission_uid) - 1) strict_prior
                    from acs) where strict_prior >= {k}""")
    c.execute(f"""create table targets as select e.*, sp.split from (
                    select *, row_number() over (partition by user_uid order by sha256('p1-target:' || submission_uid)) rn
                    from eligible) e join splits sp using (user_uid) where rn <= {per_user}""")
    c.execute("""create table donors as select t.submission_uid as target_uid, co.user_uid donor_user_uid,
                   co.first_ac_submission_uid donor_submission_uid, s.code_sha256 donor_code, co.first_ac_ts < t.ts_unix before_t,
                   row_number() over (partition by t.submission_uid
                     order by (co.first_ac_ts < t.ts_unix) desc, sha256('p1-donor:' || t.submission_uid || co.first_ac_submission_uid)) rn
                 from targets t join cohorts co on co.problem_uid = t.problem_uid and co.user_uid <> t.user_uid and co.first_ac_has_code
                 join submissions s on s.submission_uid = co.first_ac_submission_uid
                 where s.code_sha256 <> t.code_sha256 and s.lang_family = 'C++'""")
    c.execute("""create table positives_p1 as
                 select 'p1:' || t.submission_uid as pair_id, 0 as label, t.split, t.submission_uid as target_submission_uid, t.user_uid,
                        t.problem_uid, t.ts_unix, t.code_sha256, null::varchar as donor_user_uid, null::varchar as donor_submission_uid,
                        null::boolean as donor_solved_before_t, t.corpus
                 from targets t semi join donors d on d.target_uid = t.submission_uid and d.rn = 1
                 union all
                 select 'p1:' || t.submission_uid, 1, t.split, t.submission_uid, t.user_uid, t.problem_uid, t.ts_unix,
                        d.donor_code, d.donor_user_uid, d.donor_submission_uid, d.before_t, t.corpus
                 from targets t join donors d on d.target_uid = t.submission_uid and d.rn = 1""")
    b.write("positives_p1", order="pair_id, label")

    one = b.one
    b.check("every pair has exactly one positive and one negative", one("""select count(*) from (select pair_id from positives_p1
             group by 1 having count(*) <> 2 or sum(label) <> 1)""") == 0)
    b.check("donor is never the target user", one("select count(*) from positives_p1 where label = 1 and donor_user_uid = user_uid") == 0)
    b.check("swapped code differs from the original", one("""select count(*) from positives_p1 p join positives_p1 n
             using (pair_id) where p.label = 1 and n.label = 0 and p.code_sha256 = n.code_sha256""") == 0)
    b.check(f"every target has >= {k} strictly earlier ACs with code", one(f"""select count(*) from positives_p1 p
             where p.label = 0 and (select count(*) from submissions s where s.user_uid = p.user_uid and s.accepted
                 and s.code_sha256 is not null and s.ts_unix < p.ts_unix) < {k}""") == 0)
    b.check(f"at most {per_user} targets per user", one(f"""select count(*) from (select user_uid from positives_p1 where label = 0
             group by 1 having count(*) > {per_user})""") == 0)
    b.count("pairs_by_split", dict(c.execute("select corpus || '/' || split, count(*) from positives_p1 where label = 0 group by 1 order by 1").fetchall()))
    b.count("users_by_split", dict(c.execute("select corpus || '/' || split, count(distinct user_uid) from positives_p1 group by 1 order by 1").fetchall()))
    b.count("donor_solved_before_t_rate", one("select round(avg(donor_solved_before_t::int), 4) from positives_p1 where label = 1"))
    b.count("eligible_submissions", one("select count(*) from eligible"))
    b.count("targets_without_any_donor", one("select count(*) from targets anti join donors on donors.target_uid = targets.submission_uid"))
