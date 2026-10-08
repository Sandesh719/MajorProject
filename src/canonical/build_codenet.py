"""Build the canonical tables for CodeNet from its two raw snapshots.

Canonical = the raw facts in the shared schema. Every metadata row is kept (all 55 languages, every
verdict, every user); raw values are kept next to normalised ones; nothing is filtered, deduplicated
or inferred. Code is not copied: a submission carries code_sha256, and code_blobs says which raw
file and row holds those bytes. Code exists only for the 20% user sample of the C++ snapshot;
code_status says why any other row has none.

Output: data/canonical/<table>/corpus=codenet/*.parquet for problems, users, submissions (16 parts),
code_blobs, plus data/canonical/_builds/codenet.json. Everything is written to a staging directory,
checked there, and only then swapped in — a failed or interrupted build leaves the previous one intact.

usage: python src/canonical/build_codenet.py
"""
import hashlib, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.sample_rule import sql as sample_sql  # noqa: E402

META = "codenet_metadata/acq-2026-10-02_gz"
CODE = "codenet_code_cpp/1e28cfa_u20"
RAW = ROOT / "data/raw"
OUT = ROOT / "data/canonical"
STAGE = OUT / "_staging" / "codenet"
MAPS = Path(__file__).parent / "maps"
CORPUS = "codenet"
TABLES = ("problems", "users", "submissions", "code_blobs")

code_manifest = json.loads((RAW / CODE / "MANIFEST.json").read_text())
CODE_FRAC = code_manifest["scope"]["frac"]

shutil.rmtree(STAGE, ignore_errors=True)
STAGE.mkdir(parents=True)
c = duckdb.connect()
c.execute(f"set memory_limit='3GB'; set temp_directory='{STAGE / '_tmp'}'; set preserve_insertion_order=false")

meta = RAW / META / "Project_CodeNet"
c.execute(f"""create view ibm as select * from read_csv('{meta}/metadata/p[0-9]*.csv.gz',
              all_varchar=true, header=true)""")
c.execute(f"""create view plist as select * from read_csv('{meta}/metadata/problem_list.csv.gz',
              all_varchar=true, header=true)""")
c.execute(f"create table vmap as select * from read_csv('{MAPS}/verdict_map.csv', header=true) where corpus='{CORPUS}'")

# ---------------------------------------------------------------- code rows -> blobs
c.execute(f"""create table code_rows as select s_id, sha256(code) code_sha256, octet_length(encode(code)) byte_len,
              replace(filename, '{RAW}/', '') ref_file, file_row_number ref_row
              from read_parquet('{RAW / CODE}/C++/*.parquet', filename=true, file_row_number=true)""")
c.execute(f"""create table code_blobs as select code_sha256, any_value(byte_len) byte_len,
              split_part(min(ref_file || '#' || lpad(ref_row::varchar, 12, '0')), '#', 1) ref_file,  -- first location
              split_part(min(ref_file || '#' || lpad(ref_row::varchar, 12, '0')), '#', 2)::bigint ref_row,
              count(*) n_submissions, 'raw/{CODE}' source_id
              from code_rows group by 1""")

# ---------------------------------------------------------------- problems
c.execute(f"""create table statements as select replace(parse_filename(filename), '.html', '') pid, content
              from read_text('{meta}/problem_descriptions/*.html')""")
c.execute(f"""create table problems as select
    '{CORPUS}:' || p.id problem_uid, '{CORPUS}' corpus, p.dataset judge, p.id native_problem_id,
    p.name as title, s.content as statement, case when s.content is null then null else 'html' end as statement_format,
    try_cast(p.time_limit as int) time_limit_ms, try_cast(p.memory_limit as int) memory_limit_kb,
    null::double difficulty, null::varchar difficulty_scale, null::varchar[] tags,
    p.rating rating_raw, p.tags tags_raw, p.complexity complexity_raw,
    'raw/{META}' source_id
  from plist p left join statements s on s.pid = p.id""")

# ---------------------------------------------------------------- submissions (a view, streamed out in 16 parts)
c.execute(f"""create view submissions_v as select
    '{CORPUS}:' || i.submission_id submission_uid, '{CORPUS}' corpus, p.judge, i.submission_id native_submission_id,
    '{CORPUS}:' || i.problem_id problem_uid, '{CORPUS}:' || i.user_id user_uid,
    null::varchar contest_uid, null::varchar participant_type,
    i.date::bigint ts_unix, make_timestamp(i.date::bigint * 1000000) ts_utc,
    i.original_language lang_raw, i.language lang_family, i.filename_ext,
    i.status verdict_raw, v.verdict, v.accepted,
    try_cast(split_part(i.accuracy, '/', 1) as int) passed_tests,
    try_cast(split_part(i.accuracy, '/', 2) as int) total_tests, i.accuracy accuracy_raw,
    null::double score,
    try_cast(nullif(i.cpu_time, '') as int) runtime_ms, try_cast(nullif(i.memory, '') as int) memory_kb,
    try_cast(i.code_size as int) code_size_bytes,
    cr.code_sha256,
    case when cr.code_sha256 is not null then 'present'
         when i.language <> 'C++' then 'not_acquired'
         when not ({sample_sql('i.user_id', CODE_FRAC)}) then 'not_sampled'
         else 'missing_in_mirror' end code_status,
    'raw/{META}' source_id,
    left(sha256('{CORPUS}:' || i.user_id), 1) _part
  from ibm i
  left join vmap v on v.verdict_raw = i.status
  left join problems p on p.native_problem_id = i.problem_id
  left join code_rows cr on cr.s_id = i.submission_id""")
for t in TABLES:
    (STAGE / t).mkdir()
for k, part in enumerate("0123456789abcdef"):
    c.execute(f"""copy (select * exclude (_part) from submissions_v where _part = '{part}'
                  order by user_uid, ts_unix, submission_uid)
                  to '{STAGE}/submissions/part-{part}.parquet' (format parquet, compression zstd)""")
    print(f"  submissions part {k + 1}/16", flush=True)
c.execute(f"create view submissions as select * from read_parquet('{STAGE}/submissions/*.parquet')")

c.execute(f"""create table users as select user_uid, '{CORPUS}' corpus, 'ibm_anonymised' id_scheme,
    null::int rating, null::int max_rating, null::varchar rank, null::timestamp rating_asof,
    min(ts_utc) first_seen, max(ts_utc) last_seen, 'raw/{META}' source_id
  from submissions group by user_uid""")

# ---------------------------------------------------------------- checks (on what was written)
one = lambda q: c.execute(q).fetchone()[0]
checks = {
    "submissions == raw metadata rows": one("select count(*) from submissions") == one("select count(*) from ibm"),
    "submission_uid unique": one("select count(*) - count(distinct submission_uid) from submissions") == 0,
    "every verdict mapped": one("select count(*) from submissions where verdict is null") == 0,
    "every submission's problem exists": one("select count(*) from submissions where judge is null") == 0,
    "problems == problem_list rows": one("select count(*) from problems") == one("select count(*) from plist"),
    "every code row joined to a submission": one("select count(*) from submissions where code_status='present'")
        == one("select count(*) from code_rows"),
    "every code hash has a blob": one("""select count(*) from submissions s anti join code_blobs b using (code_sha256)
                                         where s.code_sha256 is not null""") == 0,
    "code byte length == code_size": one("""select count(*) from submissions s join code_blobs b using (code_sha256)
                                            where b.byte_len <> s.code_size_bytes""") == 0,
    "no sampled user lacks code it could have": one("""select count(*) from submissions where code_status='not_sampled'
                                            and user_uid in (select user_uid from submissions where code_status='present')""") == 0,
    "timestamps parse": one("select count(*) from submissions where ts_utc is null") == 0,
}
counts = {t: one(f"select count(*) from {t}") for t in TABLES}
counts["submissions_by_code_status"] = dict(c.execute("select code_status, count(*) from submissions group by 1 order by 2 desc").fetchall())
counts["users_with_code"] = one("select count(distinct user_uid) from submissions where code_status='present'")
counts["problems_with_statement"] = one("select count(statement) from problems")
print(json.dumps({"checks": checks, "counts": counts}, indent=1))
if not all(checks.values()):
    shutil.rmtree(STAGE)
    sys.exit("checks failed — staging discarded, previous canonical untouched")

for t, order in (("problems", "problem_uid"), ("users", "user_uid"), ("code_blobs", "code_sha256")):
    c.execute(f"copy (select * from {t} order by {order}) to '{STAGE}/{t}/data.parquet' (format parquet, compression zstd)")
c.close()
shutil.rmtree(STAGE / "_tmp", ignore_errors=True)

# ---------------------------------------------------------------- swap in
for t in TABLES:
    final = OUT / t / f"corpus={CORPUS}"
    shutil.rmtree(final, ignore_errors=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    (STAGE / t).rename(final)
shutil.rmtree(STAGE)

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
build = {
    "corpus": CORPUS, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "inputs": [f"raw/{s}" for s in (META, CODE)],
    "input_manifest_sha256": {s: sha(RAW / s / "MANIFEST.json") for s in (META, CODE)},
    "script": str(Path(__file__).relative_to(ROOT)), "script_sha256": sha(__file__),
    "maps": {"verdict_map.csv": sha(MAPS / "verdict_map.csv")},
    "duckdb": duckdb.__version__, "checks": checks, "counts": counts,
}
(OUT / "_builds").mkdir(exist_ok=True)
(OUT / "_builds" / f"{CORPUS}.json").write_text(json.dumps(build, indent=1) + "\n")
print("written")
