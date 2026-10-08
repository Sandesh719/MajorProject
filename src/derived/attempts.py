"""D2 attempts — each submission's place in its (user, problem) attempt chain, and what kind of event it is.

Chain order: (ts_unix, submission_uid) within (user_uid, problem_uid). Team submissions (no user) are
excluded. For every submission:
  attempt_no, prev_* (previous attempt in the chain), gap_s, chain_start_ts, solved_before,
  n_prior_fails, lang_changed, same_code_as_prev, seen_code_before (exact bytes submitted earlier by
  the same user on any problem), judge_noise (JUDGE_ERROR / PENDING verdicts), and
  event_type, first match wins:
    post_ac             the user had already solved this problem
    zero_diff_resubmit  byte-identical to the previous attempt (constraint 5: usually a compiler switch)
    first_try_ac        solved on the first attempt
    ac_after_fail       solved after one or more failed attempts
    fail_first          first attempt, not accepted
    fail_retry          later attempt, not accepted
When this and the previous attempt both have code: a line diff (lines with trailing whitespace stripped,
blank lines ignored) — lines_added, lines_removed, prev_n_lines, n_lines, diff_ratio.

usage: python src/derived/attempts.py
"""
import difflib, sys
from multiprocessing import Pool
from pathlib import Path
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402
from src.canonical.db import fetch_code  # noqa: E402

PARAMS = {"chain_order": ["ts_unix", "submission_uid"], "diff": "difflib line opcodes; rstrip lines; drop blank lines",
          "event_priority": ["post_ac", "zero_diff_resubmit", "first_try_ac", "ac_after_fail", "fail_first", "fail_retry"]}


def lines(code):
    return [ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]


def diff(pair):
    uid, a, b = pair
    la, lb = lines(a), lines(b)
    add = rem = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            rem += i2 - i1
        if op in ("replace", "insert"):
            add += j2 - j1
    return uid, add, rem, len(la), len(lb)


if __name__ == "__main__":
    with Build("attempts", 1, PARAMS, __file__, memory="3GB") as b:
        c = b.c
        c.execute("""create table chain as select
            submission_uid, corpus, user_uid, problem_uid, ts_unix, verdict, accepted, lang_raw, code_sha256,
            row_number() over w attempt_no,
            lag(submission_uid) over w prev_submission_uid, lag(verdict) over w prev_verdict,
            lag(ts_unix) over w prev_ts, lag(lang_raw) over w prev_lang, lag(code_sha256) over w prev_code_sha256,
            min(ts_unix) over w0 chain_start_ts,
            coalesce(bool_or(accepted) over wp, false) solved_before,
            coalesce(count(*) filter (where not accepted) over wp, 0) n_prior_fails
          from submissions where user_uid is not null
          window w as (partition by user_uid, problem_uid order by ts_unix, submission_uid),
                 w0 as (partition by user_uid, problem_uid),
                 wp as (partition by user_uid, problem_uid order by ts_unix, submission_uid
                        rows between unbounded preceding and 1 preceding)""")
        c.execute("""create table seen as select submission_uid,
            row_number() over (partition by user_uid, code_sha256 order by ts_unix, submission_uid) > 1 seen_code_before
          from submissions where user_uid is not null and code_sha256 is not null""")

        # ---- line diffs, 16 user buckets so only one bucket's code is in memory at a time
        diffs = []
        for part in "0123456789abcdef":
            pairs = c.execute(f"""select submission_uid, prev_code_sha256, code_sha256 from chain
                where code_sha256 is not null and prev_code_sha256 is not null
                  and left(sha256(user_uid), 1) = '{part}'""").fetchall()
            c.execute(f"""create or replace temp table need as select distinct code_sha256 from (
                select prev_code_sha256 code_sha256 from chain where code_sha256 is not null and prev_code_sha256 is not null
                  and left(sha256(user_uid), 1) = '{part}'
                union all select code_sha256 from chain where code_sha256 is not null and prev_code_sha256 is not null
                  and left(sha256(user_uid), 1) = '{part}')""")
            code = fetch_code(c, "need")
            with Pool(8) as pool:
                diffs.extend(pool.map(diff, [(u, code[a], code[z]) for u, a, z in pairs], chunksize=500))
            del code
            print(f"  diffs part {part}: {len(pairs)} pairs", flush=True)
        d = pa.table(list(zip(*diffs)) if diffs else [[]] * 5,
                     names=["submission_uid", "lines_added", "lines_removed", "prev_n_lines", "n_lines"])
        c.register("d_arrow", d)
        c.execute("create table diffs as select * from d_arrow")
        c.unregister("d_arrow")

        c.execute("""create table attempts as select ch.submission_uid, ch.corpus, ch.user_uid, ch.problem_uid, ch.ts_unix,
            ch.attempt_no, ch.prev_submission_uid, ch.prev_verdict, ch.ts_unix - ch.prev_ts gap_s,
            ch.chain_start_ts, ch.ts_unix - ch.chain_start_ts since_chain_start_s,
            ch.solved_before, ch.n_prior_fails::int n_prior_fails,
            (ch.prev_lang is not null and ch.lang_raw <> ch.prev_lang) lang_changed,
            (ch.code_sha256 is not null and ch.code_sha256 = ch.prev_code_sha256) same_code_as_prev,
            coalesce(s.seen_code_before, false) seen_code_before,
            ch.verdict in ('JUDGE_ERROR', 'PENDING') judge_noise,
            case when ch.solved_before then 'post_ac'
                 when ch.code_sha256 is not null and ch.code_sha256 = ch.prev_code_sha256 then 'zero_diff_resubmit'
                 when ch.attempt_no = 1 and ch.accepted then 'first_try_ac'
                 when ch.accepted then 'ac_after_fail'
                 when ch.attempt_no = 1 then 'fail_first'
                 else 'fail_retry' end event_type,
            d.lines_added, d.lines_removed, d.prev_n_lines, d.n_lines,
            (d.lines_added + d.lines_removed) / nullif(d.prev_n_lines + d.n_lines, 0) diff_ratio
          from chain ch left join seen s using (submission_uid) left join diffs d using (submission_uid)""")
        b.write("attempts", order="user_uid, problem_uid, attempt_no")

        one = b.one
        b.check("one row per individual submission", one("select count(*) from attempts")
                == one("select count(*) from submissions where user_uid is not null"))
        b.check("submission_uid unique", one("select count(*) - count(distinct submission_uid) from attempts") == 0)
        b.check("attempt_no is 1..n without gaps", one("""select count(*) from (select user_uid, problem_uid, max(attempt_no) m, count(*) n
                                                         from attempts group by 1, 2) where m <> n""") == 0)
        b.check("every code-to-code transition got a diff", one("""select count(*) from chain where code_sha256 is not null
                     and prev_code_sha256 is not null""") == one("select count(*) from diffs"))
        b.check("zero-diff transitions have an empty diff", one("""select count(*) from attempts where same_code_as_prev
                     and (lines_added + lines_removed) <> 0""") == 0)
        b.check("first attempts have no previous", one("select count(*) from attempts where attempt_no = 1 and prev_submission_uid is not null") == 0)
        b.count("event_types", {f"{c_}/{e}": n for c_, e, n in c.execute(
            "select corpus, event_type, count(*) from attempts group by 1, 2 order by 1, 3 desc").fetchall()})
        b.count("diffs", one("select count(*) from diffs"))
        b.count("zero_diff_resubmits_with_lang_change", one("select count(*) from attempts where event_type='zero_diff_resubmit' and lang_changed"))
        b.count("zero_diff_resubmits", one("select count(*) from attempts where event_type='zero_diff_resubmit'"))
        b.count("median_diff_ratio_ac_after_fail", one("select median(diff_ratio) from attempts where event_type='ac_after_fail'"))
