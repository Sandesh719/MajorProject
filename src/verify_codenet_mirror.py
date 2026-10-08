"""Verify the CodeNet code mirror against IBM's official metadata, row by row.

The code comes from a third-party mirror (iNeil77/CodeNet), so before anything is built on
it we check every row, not a sample:
  - each mirror row exists in IBM metadata, and agrees on every metadata column;
  - which official C++ rows the mirror lacks;
  - the code's byte length equals IBM's code_size (catches re-encoding / newline changes).
Prints one JSON object; it is recorded in the snapshot's MANIFEST.json under "verification".

usage: python src/verify_codenet_mirror.py <codenet_code snapshot> <codenet_metadata snapshot>
"""
import json, sys
import duckdb

code_snap, meta_snap = sys.argv[1], sys.argv[2]
COLS = ("p_id", "u_id", "date", "original_language", "filename_ext", "status",
        "cpu_time", "memory", "code_size")
IBM = {"p_id": "problem_id", "u_id": "user_id"}  # mirror name -> IBM name, where they differ

c = duckdb.connect()
c.execute(f"create view m as select * from read_parquet('{code_snap}/C++/*.parquet')")
c.execute(f"""create table ibm as select * from read_csv('{meta_snap}/Project_CodeNet/metadata/p[0-9]*.csv',
              all_varchar=true, header=true) where language = 'C++'""")
c.execute("""create table j as select m.* exclude (code), octet_length(encode(m.code)) code_bytes,
             i.submission_id is not null in_ibm, """ +
          ", ".join(f"i.{IBM.get(k, k)} ibm_{k}" for k in COLS) +
          " from m left join ibm i on m.s_id = i.submission_id")

one = lambda q: c.execute(q).fetchone()[0]
R = {
    "mirror_rows": one("select count(*) from j"),
    "mirror_duplicate_ids": one("select count(*) - count(distinct s_id) from j"),
    "mirror_rows_not_in_ibm": one("select count(*) from j where not in_ibm"),
    "ibm_cpp_rows": one("select count(*) from ibm"),
    "ibm_cpp_rows_missing_from_mirror": one("select count(*) from ibm where submission_id not in (select s_id from m)"),
    "ibm_cpp_users": one("select count(distinct user_id) from ibm"),
    "ibm_cpp_users_missing_from_mirror": one("select count(*) from (select distinct user_id from ibm except select distinct u_id from m)"),
    "field_mismatches": {k: one(f"select count(*) from j where in_ibm and {k} is distinct from ibm_{k}") for k in COLS},
    "code_null": one("select count(*) from j where code_bytes is null"),
    "code_bytes_ne_code_size": one("select count(*) from j where in_ibm and code_bytes <> try_cast(ibm_code_size as int)"),
}
bad = c.execute("""select code_bytes - try_cast(ibm_code_size as int) d, count(*) n from j
                   where in_ibm and code_bytes <> try_cast(ibm_code_size as int) group by 1 order by n desc limit 5""").fetchall()
R["code_size_diff_top"] = [{"bytes_diff": d, "rows": n} for d, n in bad]
print(json.dumps(R, indent=1))
