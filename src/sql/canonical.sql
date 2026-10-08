-- CANONICAL layer: the single source of truth — every corpus in one vocabulary (docs/SCHEMA.md).
--   cd /Users/vikas/Desktop/MajorProject && duckdb -ui -init src/sql/canonical.sql
-- Read-only views over data/canonical/; ids are <corpus>:<id>; raw values kept beside normalised ones.

-- canonical: one view per table, all corpora (corpus column from the folder name)
create or replace view submissions      as select * from read_parquet('data/canonical/submissions/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true);
create or replace view problems         as select * from read_parquet('data/canonical/problems/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true);
create or replace view users            as select * from read_parquet('data/canonical/users/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true);
create or replace view code_blobs       as select * from read_parquet('data/canonical/code_blobs/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true);
create or replace view contests         as select * from read_parquet('data/canonical/contests/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true);
create or replace view contest_problems as select * from read_parquet('data/canonical/contest_problems/corpus=*/*.parquet', hive_partitioning=true, union_by_name=true);

-- source code by hash (bytes live only in data/raw/); slow on large scans — filter by code_sha256 first
create or replace view code as
  select b.code_sha256, r.code from code_blobs b
    join read_parquet('data/raw/codenet_code_cpp/1e28cfa_u20/C++/*.parquet', filename=true, file_row_number=true) r
      on b.source_id = 'raw/codenet_code_cpp/1e28cfa_u20' and r.filename = 'data/raw/' || b.ref_file and r.file_row_number = b.ref_row
  union all
  select b.code_sha256, r.source from code_blobs b
    join read_parquet('data/raw/openr1_cf_submissions/07e7bf3_cfu/data/*.parquet', filename=true, file_row_number=true) r
      on b.source_id = 'raw/openr1_cf_submissions/07e7bf3_cfu' and r.filename = 'data/raw/' || b.ref_file and r.file_row_number = b.ref_row;
