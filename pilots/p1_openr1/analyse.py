"""Pilot 1 — how much of Codeforces does open-r1 cover, and does it carry user histories?

Inputs (read-only):  data/raw/codeforces_api_contest_status/acq-2026-10-06/   raw contest.status pages
                     out/openr1_meta_all.parquet     open-r1 metadata (no code), all 36 shards
Output:              out/report.json  + printed tables
Handles are replaced by a salted hash on read; no handle is written anywhere.
"""
import glob, gzip, hashlib, json, os, secrets
from pathlib import Path
import duckdb, pandas as pd

HERE = Path(__file__).parent
SALT = os.environ.get("PILOT_SALT") or secrets.token_hex(16)  # ephemeral: pilot needs no stable ids


def uid(handle):
    return hashlib.sha256((SALT + handle.lower()).encode()).hexdigest()[:16]


rows = []
for f in sorted(glob.glob(str(HERE / "../../data/raw/codeforces_api_contest_status/acq-2026-10-06/*.json.gz"))):
    for s in json.loads(gzip.decompress(Path(f).read_bytes()))["result"]:
        a = s["author"]
        rows.append({
            "submission_id": str(s["id"]), "contestId": str(s["contestId"]),
            "problem_index": s["problem"]["index"],
            "user": uid(a["members"][0]["handle"]) if len(a["members"]) == 1 else None,  # teams: no single author
            "team": len(a["members"]) != 1, "ptype": a["participantType"],
            "ts": s["creationTimeSeconds"], "lang": s["programmingLanguage"],
            "verdict": s.get("verdict"), "passed": s["passedTestCount"], "time_ms": s["timeConsumedMillis"],
        })
api = pd.DataFrame(rows).drop_duplicates("submission_id")

c = duckdb.connect()
c.register("api", api)
c.execute(f"create view o as select * from '{HERE}/out/openr1_meta_all.parquet' "
          f"where contestId in (select distinct contestId from api)")
c.execute("""create table j as select a.*, o.submission_id is not null in_o,
             o.creationTimeSeconds o_ts, o.verdict o_verdict, o.programmingLanguage o_lang, o.timeConsumedMillis o_time
             from api a left join o using (submission_id)""")

q = lambda s: c.execute(s).df()
R = {}

# 1. are open-r1 rows real CF submissions, and do their fields agree with the API?
R["openr1_rows_in_contests"] = c.execute("select count(*) from o").fetchone()[0]
R["openr1_rows_found_in_api"] = c.execute("select count(*) from j where in_o").fetchone()[0]
R["field_agreement"] = q("""select avg((o_ts=ts)::int) ts, avg((o_verdict=verdict)::int) verdict,
                          avg((o_lang=lang)::int) lang, avg((o_time=time_ms)::int) time_ms from j where in_o""").iloc[0].round(4).to_dict()

# 2. coverage of the API population
per_contest = q("""select contestId, count(*) api_n, sum(in_o::int) o_n, round(avg(in_o::int),3) cov,
                   round(avg(in_o::int) filter (where verdict='OK'),3) cov_ok,
                   round(avg(in_o::int) filter (where verdict<>'OK'),3) cov_fail,
                   round(avg(in_o::int) filter (where ptype='CONTESTANT'),3) cov_contestant
                   from j group by 1 order by try_cast(contestId as int)""")
R["coverage_overall"] = q("""select avg(in_o::int) all_, avg(in_o::int) filter (where verdict='OK') ok,
                           avg(in_o::int) filter (where verdict<>'OK') fail from j""").iloc[0].round(4).to_dict()

# 3. per-user: is a covered user's history complete or a sample?
users = q("""select user, count(*) api_n, sum(in_o::int) o_n, sum((in_o and verdict<>'OK')::int) o_fail
             from j where user is not null group by 1""")
covered = users[users.o_n > 0]
R["users_in_api"] = len(users)
R["users_with_any_openr1_row"] = len(covered)
R["per_user_coverage_of_covered_users"] = covered.eval("o_n/api_n").describe(percentiles=[.1, .25, .5, .75, .9]).round(3).to_dict()
R["covered_users_with_any_failed_row"] = int((covered.o_fail > 0).sum())

# 4. attempt chains inside open-r1: user x problem with >=2 rows, and fail -> OK sequences
R["chains"] = q("""with x as (select user, contestId, problem_index, count(*) n,
                     bool_or(verdict<>'OK') any_fail, bool_or(verdict='OK') any_ok
                     from j where in_o and user is not null group by 1,2,3)
                  select count(*) user_problem_pairs, sum((n>=2)::int) multi_attempt,
                         sum((any_fail and any_ok)::int) fail_then_ok_pairs from x""").iloc[0].to_dict()

# 5. same chains in the API, for comparison: what open-r1 would need to look like
R["chains_api"] = q("""with x as (select user, contestId, problem_index, count(*) n,
                     bool_or(verdict<>'OK') any_fail, bool_or(verdict='OK') any_ok
                     from j where user is not null group by 1,2,3)
                  select count(*) user_problem_pairs, sum((n>=2)::int) multi_attempt,
                         sum((any_fail and any_ok)::int) fail_then_ok_pairs from x""").iloc[0].to_dict()

pd.set_option("display.width", 200)
print(per_contest.to_string(index=False))
print(json.dumps(R, indent=2, default=int))
per_contest.to_csv(HERE / "out/coverage_per_contest.csv", index=False)
(HERE / "out/report.json").write_text(json.dumps(R, indent=2, default=int))
