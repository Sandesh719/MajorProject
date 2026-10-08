-- DERIVED layer: our datasets D1–D11 (data/README.md), plus every recorded run's scores.
--   cd /Users/vikas/Desktop/MajorProject && duckdb -ui -init src/sql/derived.sql
-- Each view points at the dataset's current version; the recipe.json beside the data says how it was made.

-- derived: the current version of each dataset (D1..D11)
create or replace view d_splits             as select * from read_parquet('data/derived/splits/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_attempts           as select * from read_parquet('data/derived/attempts/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_code_views         as select * from read_parquet('data/derived/code_views/v2/**/*.parquet', hive_partitioning=true);
create or replace view d_code_features      as select * from read_parquet('data/derived/code_features/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_problem_difficulty as select * from read_parquet('data/derived/problem_difficulty/v2/**/*.parquet', hive_partitioning=true);
create or replace view d_problem_links      as select * from read_parquet('data/derived/problem_links/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_profiles           as select * from read_parquet('data/derived/profiles/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_cohorts            as select * from read_parquet('data/derived/cohorts/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_dup_groups         as select * from read_parquet('data/derived/dup_groups/v1/**/*.parquet', hive_partitioning=true);
create or replace view d_positives_p1       as select * from read_parquet('data/derived/positives_p1/v2/**/*.parquet', hive_partitioning=true);
create or replace view d_eval_frame         as select * from read_parquet('data/derived/eval_frame/v1/**/*.parquet', hive_partitioning=true);

-- results: every recorded run's scores, with the method and split taken from the folder name
create or replace view run_scores as
  select split_part(filename, '/', 2) as method, regexp_extract(filename, '-(val|test)/', 1) as split, row_id, score
  from read_parquet('runs/*/*/scores.parquet', filename=true);
