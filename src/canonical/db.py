"""The one way to read the canonical layer.

    from src.canonical.db import connect
    c = connect()                       # views: problems, users, submissions, code_blobs, code
    c.sql("select corpus, judge, count(*) from submissions group by all")
    c.sql("select s.submission_uid, k.code from submissions s join code k using (code_sha256) limit 5")

Each table is the union of data/canonical/<table>/corpus=*/*.parquet, so a new corpus
appears in every query without code changes. `code` resolves hashes to bytes by reading the raw
snapshot row that code_blobs points at — code lives only in data/raw/, never in canonical/.
"""
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
CANON = ROOT / "data/canonical"
RAW = ROOT / "data/raw"
TABLES = ("problems", "users", "submissions", "code_blobs", "contests", "contest_problems")

# where the code column lives in each code-bearing raw snapshot
CODE_SOURCES = {"raw/codenet_code_cpp/1e28cfa_u20": ("C++/*.parquet", "code"),
                "raw/openr1_cf_submissions/07e7bf3_cfu": ("data/*.parquet", "source")}


def connect(read_only_db=":memory:"):
    c = duckdb.connect(read_only_db)
    for t in TABLES:
        if any((CANON / t).glob("corpus=*/*.parquet")):
            c.execute(f"""create or replace view {t} as select * from
                          read_parquet('{CANON / t}/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true)""")
    parts = []
    for src, (glob, col) in CODE_SOURCES.items():
        if not any((ROOT / "data" / src).glob(glob)):
            continue
        parts.append(f"""select b.code_sha256, r.{col} as code from code_blobs b
            join read_parquet('{ROOT / 'data' / src}/{glob}', filename=true, file_row_number=true) r
              on b.source_id = '{src}' and r.filename = '{RAW}/' || b.ref_file and r.file_row_number = b.ref_row""")
    if parts:
        c.execute("create or replace view code as " + " union all ".join(parts))
    return c


def fetch_code(c, sha_table):
    """{code_sha256: code} for the hashes in table `sha_table` (one column, code_sha256).

    Use this instead of joining the `code` view when you need many files: the small set of wanted
    blobs is the hash-join build side and each raw snapshot is streamed once, so memory scales with
    what is asked for, not with the corpus.
    """
    out = {}
    for src, (glob, col) in CODE_SOURCES.items():
        if not any((ROOT / "data" / src).glob(glob)):
            continue
        rows = c.execute(f"""select b.code_sha256, r.{col} from
            (select code_sha256, '{RAW}/' || ref_file f, ref_row from code_blobs
              where source_id = '{src}' and code_sha256 in (select code_sha256 from {sha_table})) b
            join read_parquet('{ROOT / 'data' / src}/{glob}', filename=true, file_row_number=true) r
              on r.filename = b.f and r.file_row_number = b.ref_row""").fetchall()
        out.update(rows)
    return out
