"""Build the canonical tables for AtCoder from the kenkoooo raw snapshots.

Inputs: the full submissions dump (metadata only, no code) and the resource JSONs (problems,
IRT problem models, contests, contest-problem links). Every dump row is kept. Handles are
replaced by salted hashes (salt in .secrets/user_salt, never in data/); nothing else is
filtered or inferred.

Writes data/canonical/<table>/corpus=atcoder/*.parquet for problems, users, submissions,
contests, contest_problems, and data/canonical/_builds/atcoder.json.
Submissions are written as 16 parts by the first hex digit of sha256(user_uid), each sorted by
(user, time). Everything is staged, checked, then swapped in.
The dump snapshot is the 5% user sample (raw/atcoder_kenkoooo_dump/2025-11-21_u05).

usage: python src/canonical/build_atcoder.py
"""
import hashlib, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
DUMP = "atcoder_kenkoooo_dump/2025-11-21_u05"
RES = "atcoder_kenkoooo_resources/acq-2026-10-07"
RAW = ROOT / "data/raw"
OUT = ROOT / "data/canonical"
MAPS = Path(__file__).parent / "maps"
CORPUS = "atcoder"
STAGE = OUT / "_staging" / "atcoder"
TABLES = ("problems", "users", "submissions", "contests", "contest_problems")
SALT = (ROOT / ".secrets/user_salt").read_text().strip()

shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True)
for t in TABLES:
    (STAGE / t).mkdir()
c = duckdb.connect()
c.execute(f"set memory_limit='3GB'; set temp_directory='{STAGE / '_tmp'}'; set preserve_insertion_order=false")

csv = RAW / DUMP / "submissions.csv.gz"
res = RAW / RES
c.execute(f"""create view dump as select * from read_csv('{csv}', header=true, all_varchar=true)""")
c.execute(f"create table vmap as select * from read_csv('{MAPS}/verdict_map.csv', header=true) where corpus='{CORPUS}'")
c.execute(f"create table lmap as select * from read_csv('{MAPS}/language_family.csv', header=true)")
c.execute("create macro uhash(h) as left(sha256(getvariable('salt') || ':' || h), 16)")
c.execute("set variable salt = ?", [SALT])

# ---------------------------------------------------------------- contests, problems
c.execute(f"""create table contests as select '{CORPUS}:' || id contest_uid, '{CORPUS}' corpus, 'AtCoder' judge,
    id native_contest_id, title as name, start_epoch_second::bigint start_unix,
    make_timestamp(start_epoch_second::bigint * 1000000) start_utc, duration_second::bigint duration_s,
    rate_change rate_change_raw, 'raw/{RES}' source_id
  from read_json('{res}/contests.json')""")
c.execute(f"""create table contest_problems as select '{CORPUS}:' || contest_id contest_uid,
    '{CORPUS}:' || problem_id problem_uid, problem_index, 'raw/{RES}' source_id
  from read_json('{res}/contest-problem.json')""")
# problem-models.json is one object keyed by problem id -> rows (pid, model json)
models = json.loads((res / "problem-models.json").read_text())
c.execute("create table models (pid varchar, m json)")
c.executemany("insert into models values (?, ?)", [(k, json.dumps(v)) for k, v in models.items()])
c.execute(f"""create table problems as select '{CORPUS}:' || p.id problem_uid, '{CORPUS}' corpus, 'AtCoder' judge,
    p.id native_problem_id, p.name as title, p.title title_raw, '{CORPUS}:' || p.contest_id contest_uid, p.problem_index,
    null::varchar as statement, null::varchar statement_format, null::int time_limit_ms, null::int memory_limit_kb,
    (m.m->>'difficulty')::double difficulty, case when m.m->>'difficulty' is not null then 'atcoder_irt_raw' end difficulty_scale,
    null::varchar[] tags,
    (m.m->>'discrimination')::double irt_discrimination, (m.m->>'irt_users')::int irt_users,
    (m.m->>'is_experimental')::boolean irt_is_experimental,
    'raw/{RES}' source_id
  from read_json('{res}/problems.json') p left join models m on m.pid = p.id""")

# ---------------------------------------------------------------- language families (once per distinct string)
c.execute("""create table langs as select lang_raw, coalesce(
    (select l.lang_family from lmap l where regexp_matches(lang_raw, l.pattern) order by l.priority limit 1),
    regexp_extract(lang_raw, '^([^ (]+)', 1)) lang_family
  from (select distinct language lang_raw from dump)""")

# ---------------------------------------------------------------- submissions, 16 user buckets
sub_dir = STAGE / "submissions"
SELECT = f"""select '{CORPUS}:' || d.id submission_uid, '{CORPUS}' corpus, 'AtCoder' judge, d.id native_submission_id,
    '{CORPUS}:' || d.problem_id problem_uid, '{CORPUS}:' || uhash(d.user_id) user_uid,
    '{CORPUS}:' || d.contest_id contest_uid, null::varchar participant_type,
    d.epoch_second::bigint ts_unix, make_timestamp(d.epoch_second::bigint * 1000000) ts_utc,
    d.language lang_raw,
    lf.lang_family,
    null::varchar filename_ext,
    d.result verdict_raw,
    coalesce(v.verdict, case when regexp_matches(d.result, '^[0-9]+/[0-9]+') then 'PENDING' end) verdict,
    coalesce(v.accepted, false) accepted,
    null::int passed_tests, null::int total_tests, null::varchar accuracy_raw,
    try_cast(d.point as double) score, try_cast(d.execution_time as int) runtime_ms, null::int memory_kb,
    try_cast(d.length as int) code_size_bytes,
    null::varchar code_sha256, 'not_acquired' code_status,
    'raw/{DUMP}' source_id, left(sha256('{CORPUS}:' || uhash(d.user_id)), 1) _part
  from dump d left join vmap v on v.verdict_raw = d.result left join langs lf on lf.lang_raw = d.language"""
c.execute(f"create view submissions_v as {SELECT}")
for k, part in enumerate("0123456789abcdef"):
    c.execute(f"""copy (select * exclude (_part) from submissions_v where _part = '{part}' order by user_uid, ts_unix, submission_uid)
                  to '{sub_dir}/part-{part}.parquet' (format parquet, compression zstd)""")
    print(f"  submissions part {k + 1}/16", flush=True)
c.execute(f"create view submissions as select * from read_parquet('{sub_dir}/*.parquet')")

# ---------------------------------------------------------------- users (hashed; no handle stored)
c.execute(f"""create table users as select user_uid, '{CORPUS}' corpus, 'salted_sha256' id_scheme,
    null::int rating, null::int max_rating, null::varchar rank, null::timestamp rating_asof,
    min(ts_utc) first_seen, max(ts_utc) last_seen, 'raw/{DUMP}' source_id
  from submissions group by user_uid""")

# ---------------------------------------------------------------- checks
one = lambda q: c.execute(q).fetchone()[0]
n_dump = one("select count(*) from dump")
checks = {
    "submissions == dump rows": one("select count(*) from submissions") == n_dump,
    "submission_uid unique": one("select count(*) - count(distinct submission_uid) from submissions") == 0,
    "every verdict mapped": one("select count(*) from submissions where verdict is null") == 0,
    "users == distinct raw handles (no hash collision)": one("select count(*) from users")
        == one("select count(distinct user_id) from dump"),
    "timestamps parse": one("select count(*) from submissions where ts_utc is null") == 0,
    "problem_uid unique": one("select count(*) - count(distinct problem_uid) from problems") == 0,
    "contest_uid unique": one("select count(*) - count(distinct contest_uid) from contests") == 0,
}
counts = {
    "submissions": n_dump,
    **{t: one(f"select count(*) from {t}") for t in ("users", "problems", "contests", "contest_problems")},
    "problems_with_irt_difficulty": one("select count(difficulty) from problems"),
    # reported, not failed: references the resource files do not cover (they postdate the dump, or vice versa)
    "submissions_whose_problem_not_in_problems": one("select count(*) from submissions anti join problems using (problem_uid)"),
    "submissions_whose_contest_not_in_contests": one("select count(*) from submissions anti join contests using (contest_uid)"),
    "verdicts": dict(c.execute("select verdict, count(*) from submissions group by 1 order by 2 desc").fetchall()),
    "top_lang_families": dict(c.execute("select lang_family, count(*) from submissions group by 1 order by 2 desc limit 8").fetchall()),
}
print(json.dumps({"checks": checks, "counts": counts}, indent=1))
if not all(checks.values()):
    shutil.rmtree(STAGE)
    sys.exit("checks failed — staging discarded, previous canonical untouched")

for t, order in (("problems", "problem_uid"), ("users", "user_uid"), ("contests", "contest_uid"),
                 ("contest_problems", "contest_uid, problem_uid")):
    c.execute(f"copy (select * from {t} order by {order}) to '{STAGE}/{t}/data.parquet' (format parquet, compression zstd)")
c.close()
shutil.rmtree(STAGE / "_tmp", ignore_errors=True)
for t in TABLES:  # swap in
    final = OUT / t / f"corpus={CORPUS}"
    shutil.rmtree(final, ignore_errors=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    (STAGE / t).rename(final)
shutil.rmtree(STAGE)

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
build = {
    "corpus": CORPUS, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "inputs": [f"raw/{s}" for s in (DUMP, RES)],
    "input_manifest_sha256": {s: sha(RAW / s / "MANIFEST.json") for s in (DUMP, RES)},
    "script": str(Path(__file__).relative_to(ROOT)), "script_sha256": sha(__file__),
    "maps": {m: sha(MAPS / m) for m in ("verdict_map.csv", "language_family.csv")},
    "user_salt_sha256": hashlib.sha256(SALT.encode()).hexdigest(),  # identifies the salt without revealing it
    "duckdb": duckdb.__version__, "checks": checks, "counts": counts,
}
(OUT / "_builds" / f"{CORPUS}.json").write_text(json.dumps(build, indent=1, default=str) + "\n")
print("written")
