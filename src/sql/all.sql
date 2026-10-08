-- Every layer at once: raw_* views, canonical tables, derived d_* views, run_scores.
--   cd /Users/vikas/Desktop/MajorProject && duckdb -ui -init src/sql/all.sql
.read src/sql/raw.sql
.read src/sql/canonical.sql
.read src/sql/derived.sql
