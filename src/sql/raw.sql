-- RAW layer: what we acquired, exactly as received (read-only views; nothing is copied or changed).
--   cd /Users/vikas/Desktop/MajorProject && duckdb -ui -init src/sql/raw.sql
-- Column names and value formats are each source's own — that is the point of raw. For the unified
-- vocabulary use canonical.sql. Views prefixed raw_<source>.

-- every snapshot: where it came from, how, when, license, size (one row per MANIFEST.json)
create or replace view raw_manifests as
  select snapshot, source, class, upstream.name as upstream_name, upstream.revision as revision,
         upstream.license as license, method, retrieved_at, n_files, round(total_bytes / 1e6, 1) as mb, complete
  from read_json('data/raw/*/*/MANIFEST.json', union_by_name=true, maximum_object_size=67108864);

-- CodeNet (IBM): metadata for every submission (all languages), one CSV per problem; statements are HTML files
create or replace view raw_codenet_metadata as
  select * from read_csv('data/raw/codenet_metadata/acq-2026-10-02_gz/Project_CodeNet/metadata/p[0-9]*.csv.gz', header=true, all_varchar=true);
create or replace view raw_codenet_problem_list as
  select * from read_csv('data/raw/codenet_metadata/acq-2026-10-02_gz/Project_CodeNet/metadata/problem_list.csv.gz', header=true, all_varchar=true);
create or replace view raw_codenet_statements as
  select replace(parse_filename(filename), '.html', '') as problem_id, content as html
  from read_text('data/raw/codenet_metadata/acq-2026-10-02_gz/Project_CodeNet/problem_descriptions/*.html');
-- CodeNet C++ source code (20% user sample; iNeil77 mirror columns: s_id, p_id, u_id, …, code)
create or replace view raw_codenet_code as
  select * from read_parquet('data/raw/codenet_code_cpp/1e28cfa_u20/C++/*.parquet');

-- AtCoder (kenkoooo): submissions dump (5% user sample, no code) + resource files
create or replace view raw_atcoder_submissions as
  select * from read_csv('data/raw/atcoder_kenkoooo_dump/2025-11-21_u05/submissions.csv.gz', header=true, all_varchar=true);
create or replace view raw_atcoder_problems as
  select * from read_json('data/raw/atcoder_kenkoooo_resources/acq-2026-10-07/problems.json');
create or replace view raw_atcoder_contests as
  select * from read_json('data/raw/atcoder_kenkoooo_resources/acq-2026-10-07/contests.json');
create or replace view raw_atcoder_contest_problem as
  select * from read_json('data/raw/atcoder_kenkoooo_resources/acq-2026-10-07/contest-problem.json');
create or replace view raw_atcoder_problem_models as  -- IRT difficulty; one JSON object keyed by problem id
  select unnest(map_keys(m)) as problem_id, unnest(map_values(m)) as model
  from (select json_transform(content, '"MAP(VARCHAR, JSON)"') as m
        from read_text('data/raw/atcoder_kenkoooo_resources/acq-2026-10-07/problem-models.json'));

-- Codeforces: API catalog, rated users (personal fields removed), open-r1 code of the sampled users
create or replace view raw_cf_problems as
  select unnest(result.problems, recursive := true)
  from read_json('data/raw/codeforces_api_catalog/acq-2026-10-07/problemset.problems.json', maximum_object_size=67108864);
create or replace view raw_cf_contests as
  select unnest(result, recursive := true) from read_json('data/raw/codeforces_api_catalog/acq-2026-10-07/contest.list.json');
create or replace view raw_cf_rated_users as
  select unnest(result, recursive := true)
  from read_json('data/raw/codeforces_api_rated_list/acq-2026-10-07/user.ratedList.projected.json.gz', maximum_object_size=268435456);
create or replace view raw_cf_openr1_code as
  select * from read_parquet('data/raw/openr1_cf_submissions/07e7bf3_cfu/data/*.parquet');
-- Codeforces user.status histories: one gzipped JSON response per user (9,842 files). Heavy to read all at once;
-- query one user's file, e.g.:
--   select unnest(result, recursive := true) from read_json('data/raw/codeforces_api_user_status/acq-2026-10-07/user_status_<handle>.json.gz');
create or replace view raw_cf_user_status_files as
  select parse_filename(file) as file from glob('data/raw/codeforces_api_user_status/acq-2026-10-07/user_status_*.json.gz');

-- AI-vs-human labelled code (label 1 = machine, 0 = human)
create or replace view raw_aicd_t1 as
  select parse_filename(filename) as file, code, label
  from read_parquet('data/raw/aicd_bench/b7dc6d8_trainval/T1/*.parquet', filename=true);
create or replace view raw_aigcodeset_human as
  select * from read_csv('data/raw/aigcodeset/b4d4e69/data/human_selected_dataset.csv', header=true, all_varchar=true);
create or replace view raw_aigcodeset_ai as
  select * from read_csv('data/raw/aigcodeset/b4d4e69/data/created_dataset_with_llms.csv', header=true, all_varchar=true);
