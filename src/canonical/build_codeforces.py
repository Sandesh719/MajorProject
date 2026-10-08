"""Build the canonical tables for Codeforces from its raw snapshots.

Inputs: the API catalog (problemset.problems, contest.list), the projected rated-user list, the
user.status histories of the 1% user sample, and the open-r1 rows (code) of those users.
Every submission in every fetched history is kept (all verdicts, practice/virtual/contest, gym).
Handles become salted hashes; nothing else is filtered or inferred.

Team submissions have no single author: user_uid is NULL and team_member_uids lists the members.
A team submission appears in each sampled member's history; it is kept once.
Problems outside problemset.problems (gym, acm.sgu) are taken from the problem objects embedded in
the submissions — still raw facts.

Output: data/canonical/<table>/corpus=codeforces/*.parquet, staged, checked, swapped in.
usage: python src/canonical/build_codeforces.py
       DRY_RUN=1 CF_OPENR1_SNAPSHOT=openr1_cf_submissions/07e7bf3 python ...   (test on partial data, writes nothing)
"""
import gzip, hashlib, json, os, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.sample_rule import sql as sample_sql  # noqa: E402

CAT = "codeforces_api_catalog/acq-2026-10-07"
RATED = "codeforces_api_rated_list/acq-2026-10-07"
US = "codeforces_api_user_status/acq-2026-10-07"
OR1 = os.environ.get("CF_OPENR1_SNAPSHOT", "openr1_cf_submissions/07e7bf3_cfu")  # override only for dry runs
RAW = ROOT / "data/raw"
OUT = ROOT / "data/canonical"
STAGE = OUT / "_staging" / "codeforces"
MAPS = Path(__file__).parent / "maps"
CORPUS = "codeforces"
TABLES = ("problems", "users", "submissions", "code_blobs", "contests", "contest_problems")
SALT = (ROOT / ".secrets/user_salt").read_text().strip()
US_FRAC = 0.01
NOT_IN_CONTEST = 2147483647  # Codeforces' relativeTimeSeconds for submissions outside the contest window

shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True)
for t in TABLES:
    (STAGE / t).mkdir()
c = duckdb.connect()
c.execute(f"set memory_limit='3GB'; set temp_directory='{STAGE / '_tmp'}'; set preserve_insertion_order=false")
c.execute("set variable salt = ?", [SALT])
c.execute(f"create macro uid(h) as '{CORPUS}:' || left(sha256(getvariable('salt') || ':' || h), 16)")
c.execute(f"""create macro puid(cid, idx, pset) as '{CORPUS}:' ||
              case when cid is null then coalesce(pset, 'problemset') || ':' || idx else cid::varchar || idx end""")
c.execute(f"create table vmap as select * from read_csv('{MAPS}/verdict_map.csv', header=true) where corpus='{CORPUS}'")
c.execute(f"create table lmap as select * from read_csv('{MAPS}/language_family.csv', header=true)")
rated_at = json.loads((RAW / RATED / "MANIFEST.json").read_text())["retrieved_at"]

# ---------------------------------------------------------------- raw submissions, flattened in Python
# DuckDB's JSON reader needs huge buffers for big single objects, so the bodies are parsed with the
# stdlib and flattened into Arrow columns. Every field is copied as is; nothing is computed here.
FIELDS = ("id", "contest_id", "ts", "rel", "lang", "verdict", "testset", "passed", "points", "time_ms", "mem_bytes",
          "p_cid", "p_idx", "p_set", "p_name", "p_type", "p_points", "p_rating", "p_tags", "ptype", "start_ts", "handles", "file")
cols = {f: [] for f in FIELDS}
n_raw_rows, n_files = 0, 0
for f in sorted((RAW / US).glob("user_status_*.json.gz")):
    body = json.loads(gzip.decompress(f.read_bytes()))
    n_files += 1
    for x in body.get("result", []):
        n_raw_rows += 1
        p, a = x.get("problem", {}), x.get("author", {})
        for k, v in (("id", str(x["id"])), ("contest_id", x.get("contestId")), ("ts", x.get("creationTimeSeconds")),
                     ("rel", x.get("relativeTimeSeconds")), ("lang", x.get("programmingLanguage")), ("verdict", x.get("verdict")),
                     ("testset", x.get("testset")), ("passed", x.get("passedTestCount")), ("points", x.get("points")),
                     ("time_ms", x.get("timeConsumedMillis")), ("mem_bytes", x.get("memoryConsumedBytes")),
                     ("p_cid", p.get("contestId")), ("p_idx", p.get("index")), ("p_set", p.get("problemsetName")),
                     ("p_name", p.get("name")), ("p_type", p.get("type")), ("p_points", p.get("points")),
                     ("p_rating", p.get("rating")), ("p_tags", p.get("tags")), ("ptype", a.get("participantType")),
                     ("start_ts", a.get("startTimeSeconds")), ("handles", [m["handle"] for m in a.get("members", [])]),
                     ("file", f.name)):
            cols[k].append(v)
schema = pa.schema([("id", pa.string()), ("contest_id", pa.int64()), ("ts", pa.int64()), ("rel", pa.int64()),
                    ("lang", pa.string()), ("verdict", pa.string()), ("testset", pa.string()), ("passed", pa.int64()),
                    ("points", pa.float64()), ("time_ms", pa.int64()), ("mem_bytes", pa.int64()), ("p_cid", pa.int64()),
                    ("p_idx", pa.string()), ("p_set", pa.string()), ("p_name", pa.string()), ("p_type", pa.string()),
                    ("p_points", pa.float64()), ("p_rating", pa.int64()), ("p_tags", pa.list_(pa.string())),
                    ("ptype", pa.string()), ("start_ts", pa.int64()), ("handles", pa.list_(pa.string())), ("file", pa.string())])
s_arrow = pa.table(cols, schema=schema)
del cols
c.register("s_arrow", s_arrow)
c.execute("""create table s1 as select * exclude (rn) from (
    select *, row_number() over (partition by id order by file) rn from s_arrow) where rn = 1""")
c.unregister("s_arrow")
del s_arrow
c.execute("""create table langs as select lang_raw, coalesce(
    (select l.lang_family from lmap l where regexp_matches(lang_raw, l.pattern) order by l.priority limit 1),
    regexp_extract(lang_raw, '^([^ (]+)', 1)) lang_family
  from (select distinct lang lang_raw from s1)""")

# ---------------------------------------------------------------- code (open-r1 rows of the sampled users)
c.execute(f"""create table code_rows as select submission_id, sha256(source) code_sha256, octet_length(encode(source)) byte_len,
              creationTimeSeconds::bigint o_ts, verdict o_verdict,
              replace(filename, '{RAW}/', '') ref_file, file_row_number ref_row
              from read_parquet('{RAW / OR1}/data/*.parquet', filename=true, file_row_number=true)
              where submission_id in (select id from s1)""")
c.execute(f"""create table code_blobs as select code_sha256, any_value(byte_len) byte_len,
              split_part(min(ref_file || '#' || lpad(ref_row::varchar, 12, '0')), '#', 1) ref_file,
              split_part(min(ref_file || '#' || lpad(ref_row::varchar, 12, '0')), '#', 2)::bigint ref_row,
              count(*) n_submissions, 'raw/{OR1}' source_id from code_rows group by 1""")

# ---------------------------------------------------------------- submissions
c.execute(f"""create table submissions as select
    '{CORPUS}:' || id submission_uid, '{CORPUS}' corpus, 'Codeforces' judge, id native_submission_id,
    puid(p_cid, p_idx, p_set) problem_uid,
    case when len(handles) = 1 then uid(handles[1]) end user_uid,
    len(handles)::int team_size,
    case when len(handles) > 1 then list_transform(handles, h -> uid(h)) end team_member_uids,
    case when contest_id is not null then '{CORPUS}:' || contest_id end contest_uid,
    ptype participant_type,
    ts ts_unix, make_timestamp(ts * 1000000) ts_utc,
    nullif(rel, {NOT_IN_CONTEST}) relative_time_s, start_ts participant_start_unix,
    lang lang_raw, lf.lang_family,
    coalesce(s1.verdict, 'TESTING') verdict_raw, v.verdict, coalesce(v.accepted, false) accepted,
    s1.testset, passed passed_tests, null::int total_tests, s1.points score,
    time_ms runtime_ms, (mem_bytes // 1024)::int memory_kb, null::int code_size_bytes,
    cr.code_sha256, case when cr.code_sha256 is not null then 'present' else 'not_in_openr1' end code_status,
    'raw/{US}' source_id
  from s1
  left join vmap v on v.verdict_raw = coalesce(s1.verdict, 'TESTING')
  left join langs lf on lf.lang_raw = s1.lang
  left join code_rows cr on cr.submission_id = s1.id""")

# ---------------------------------------------------------------- problems, contests
cat = RAW / CAT
c.execute(f"""create table ps as select unnest(result.problems) p from read_json('{cat}/problemset.problems.json', maximum_object_size=268435456)""")
c.execute(f"""create table pstat as select unnest(result.problemStatistics) q from read_json('{cat}/problemset.problems.json', maximum_object_size=268435456)""")
c.execute(f"""create table problems as
  with catalog as (
    select puid(p.contestId, p.index, null) problem_uid, p.contestId cid, p.index idx, p.name title, p.type ptype,
           p.points points, p.rating rating, p.tags tags, q.solvedCount solved_count, 'raw/{CAT}' source_id
    from ps left join pstat on q.contestId = p.contestId and q.index = p.index),
  embedded as (
    select puid(p_cid, p_idx, p_set) problem_uid, any_value(p_cid) cid, any_value(p_idx) idx,
           any_value(p_name) title, any_value(p_type) ptype, any_value(p_points) points, any_value(p_rating) rating,
           any_value(p_tags) tags, null::int solved_count, 'raw/{US}' source_id
    from s1 group by 1)
  select problem_uid, '{CORPUS}' corpus, 'Codeforces' judge, regexp_replace(problem_uid, '^{CORPUS}:', '') native_problem_id,
         title, null::varchar as statement, null::varchar statement_format, null::int time_limit_ms, null::int memory_limit_kb,
         rating::double difficulty, case when rating is not null then 'cf_rating' end difficulty_scale, tags,
         case when cid is not null then '{CORPUS}:' || cid end contest_uid, idx problem_index, ptype problem_type,
         points, solved_count, source_id
  from (select * from catalog union all select * from embedded where problem_uid not in (select problem_uid from catalog))""")
c.execute(f"""create table contests as select '{CORPUS}:' || r.id contest_uid, '{CORPUS}' corpus, 'Codeforces' judge,
    r.id::varchar native_contest_id, r.name, r.type contest_type, r.phase, r.startTimeSeconds start_unix,
    make_timestamp(r.startTimeSeconds * 1000000) start_utc, r.durationSeconds duration_s, 'raw/{CAT}' source_id
  from (select unnest(result) r from read_json('{cat}/contest.list.json', maximum_object_size=268435456))""")
c.execute(f"""create table contest_problems as select '{CORPUS}:' || p.contestId contest_uid,
    puid(p.contestId, p.index, null) problem_uid, p.index problem_index, 'raw/{CAT}' source_id from ps""")

# ---------------------------------------------------------------- users: every rated user + any unrated author
rated_rows = json.loads(gzip.decompress((RAW / RATED / "user.ratedList.projected.json.gz").read_bytes()))["result"]
rated_arrow = pa.Table.from_pylist(rated_rows)
del rated_rows
c.execute("create table rated as select struct_pack(handle, rating, maxRating, rank, maxRank, registrationTimeSeconds, lastOnlineTimeSeconds) u from rated_arrow")
del rated_arrow
c.execute(f"""create table users as
  with r as (select uid(u.handle) user_uid, u.handle h, u.rating, u.maxRating max_rating, u.rank, u.maxRank max_rank,
                    make_timestamp(u.registrationTimeSeconds * 1000000) registered_utc,
                    make_timestamp(u.lastOnlineTimeSeconds * 1000000) last_online_utc from rated),
       seen as (select user_uid, min(ts_utc) first_seen, max(ts_utc) last_seen from submissions
                where user_uid is not null group by 1)
  select coalesce(r.user_uid, seen.user_uid) user_uid, '{CORPUS}' corpus, 'salted_sha256' id_scheme,
         r.rating, r.max_rating, r.rank, r.max_rank,
         case when r.user_uid is not null then '{rated_at}'::timestamp end rating_asof,
         r.registered_utc, r.last_online_utc, seen.first_seen, seen.last_seen,
         coalesce({sample_sql('r.h', US_FRAC)}, false) in_history_sample,
         case when r.user_uid is not null then 'raw/{RATED}' else 'raw/{US}' end source_id
  from r full join seen using (user_uid)""")

# ---------------------------------------------------------------- checks
one = lambda q: c.execute(q).fetchone()[0]
checks = {
    "submission_uid unique": one("select count(*) - count(distinct submission_uid) from submissions") == 0,
    "submissions == distinct ids in the histories": one("select count(*) from submissions") == one("select count(distinct id) from s1"),
    "every verdict mapped": one("select count(*) from submissions where verdict is null") == 0,
    "every submission's problem exists": one("select count(*) from submissions anti join problems using (problem_uid)") == 0,
    "every code row joined to a submission": one("select count(*) from submissions where code_status='present'") == one("select count(*) from code_rows"),
    "open-r1 timestamp == API timestamp": one("""select count(*) from submissions s join code_rows cr on cr.submission_id = s.native_submission_id
                                                 where cr.o_ts <> s.ts_unix""") == 0,
    "user_uid unique": one("select count(*) - count(distinct user_uid) from users") == 0,
    "no handle hash collision": one("select count(distinct user_uid) from users where source_id = 'raw/" + RATED + "'")
        == one("select count(distinct u.handle) from rated"),
    "single-author submissions all have a user": one("select count(*) from submissions where team_size = 1 and user_uid is null") == 0,
    "timestamps parse": one("select count(*) from submissions where ts_utc is null") == 0,
}
counts = {t: one(f"select count(*) from {t}") for t in TABLES}
counts.update({
    "history_files": n_files,
    "history_rows": n_raw_rows,
    "duplicate_rows_across_histories (team submissions)": n_raw_rows - one("select count(*) from submissions"),
    "submissions_by_code_status": dict(c.execute("select code_status, count(*) from submissions group by 1").fetchall()),
    "verdicts": dict(c.execute("select verdict, count(*) from submissions group by 1 order by 2 desc").fetchall()),
    "participant_types": dict(c.execute("select participant_type, count(*) from submissions group by 1 order by 2 desc").fetchall()),
    "team_submissions": one("select count(*) from submissions where team_size > 1"),
    "users_in_history_sample": one("select count(*) from users where in_history_sample"),
    "users_with_code": one("select count(distinct user_uid) from submissions where code_status='present'"),
    "problems_not_in_catalog (gym/acmsguru)": one(f"select count(*) from problems where source_id = 'raw/{US}'"),
    "submissions_whose_contest_not_in_contest_list (gym)": one("select count(*) from submissions where contest_uid is not null and contest_uid not in (select contest_uid from contests)"),
})
print(json.dumps({"checks": checks, "counts": counts}, indent=1, default=str))
if not all(checks.values()):
    shutil.rmtree(STAGE)
    sys.exit("checks failed — staging discarded, previous canonical untouched")
if os.environ.get("DRY_RUN"):
    shutil.rmtree(STAGE)
    sys.exit("dry run — nothing written")

c.execute("alter table submissions add column _part varchar")
c.execute("update submissions set _part = left(sha256(coalesce(user_uid, submission_uid)), 1)")
for part in "0123456789abcdef":
    c.execute(f"""copy (select * exclude (_part) from submissions where _part = '{part}' order by user_uid, ts_unix, submission_uid)
                  to '{STAGE}/submissions/part-{part}.parquet' (format parquet, compression zstd)""")
for t, order in (("problems", "problem_uid"), ("users", "user_uid"), ("code_blobs", "code_sha256"),
                 ("contests", "contest_uid"), ("contest_problems", "contest_uid, problem_uid")):
    c.execute(f"copy (select * from {t} order by {order}) to '{STAGE}/{t}/data.parquet' (format parquet, compression zstd)")
c.close()
shutil.rmtree(STAGE / "_tmp", ignore_errors=True)
for t in TABLES:
    final = OUT / t / f"corpus={CORPUS}"
    shutil.rmtree(final, ignore_errors=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    (STAGE / t).rename(final)
shutil.rmtree(STAGE)

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
inputs = (CAT, RATED, US, OR1)
build = {
    "corpus": CORPUS, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "inputs": [f"raw/{s}" for s in inputs],
    "input_manifest_sha256": {s: sha(RAW / s / "MANIFEST.json") if (RAW / s / "MANIFEST.json").exists() else None for s in inputs},
    "script": str(Path(__file__).relative_to(ROOT)), "script_sha256": sha(__file__),
    "maps": {m: sha(MAPS / m) for m in ("verdict_map.csv", "language_family.csv")},
    "user_salt_sha256": hashlib.sha256(SALT.encode()).hexdigest(),
    "duckdb": duckdb.__version__, "checks": checks, "counts": counts,
}
(OUT / "_builds" / f"{CORPUS}.json").write_text(json.dumps(build, indent=1, default=str) + "\n")
print("written")
