# Data audit — what in `data/` is acquired, and what was made from it

Audited 2026-10-07 against the pullers in `backup/2026-10-06-v1/pullers/` and, where possible,
against the upstream files themselves. Purpose: sort every item into the new layout

```
data/raw/<source>/<snapshot>/   acquired, exactly as received, read-only, MANIFEST.json per snapshot
data/canonical/                 the single source of truth, rebuilt only from raw/
data/derived/<dataset>/         everything made from canonical, each with recipe.json
```

## Classes used below

| Class | Meaning | Allowed in `raw/`? |
|---|---|---|
| **AS-RECEIVED** | bytes exactly as the upstream served them | yes |
| **RESHAPED** | acquired, but the puller dropped/renamed/merged fields before writing | no — the original response is gone |
| **SELECTION** | a list chosen by us (sample of users, cohort) | no — it is derived |
| **DERIVED** | computed from other files | no |

## Item by item

| Path | Size | Class | Origin | Pinned? | Verdict |
|---|---|---|---|---|---|
| `raw/codeforces/submissions/part-*.jsonl.gz` | 81 MB, 3.42M rows | **RESHAPED + SELECTION** | `api/user.status` for 3,784 handles | no | `flatten()` kept 10 fields, renamed `author.participantType`→`participant_type`, built `problem_id`, copied `problem.rating/tags` in, replaced `members` with `team_size`. Handles were a stratified sample (600 per rating band). Not raw. |
| `raw/codeforces/problems.jsonl.gz` | 0.3 MB, 11,425 | **RESHAPED** | `api/problemset.problems` (problems + problemStatistics merged) | no | Merge of two arrays into one row. One API call to re-fetch as-received. |
| `raw/codeforces/handles.json`, `handles_report.json` | 54 KB | **SELECTION** | stratified sample of rated users | — | Input choice to a pull → `derived/`. |
| `raw/atcoder/submissions/part-*.jsonl.gz` | ~430 MB, 23.2M rows | **AS-RECEIVED rows, SELECTION of users** | kenkoooo `v3/user/submissions`, 7,586 users, heaviest first | no | Each row is the API object written verbatim. But which users were pulled is our choice, and the pull spanned many days (no single snapshot date). |
| `raw/atcoder/problems.jsonl.gz` | 9,625 | **RESHAPED** | kenkoooo `problems.json` + `problem-models.json` merged | no | Two responses merged into one row. Two calls to re-fetch. |
| `raw/atcoder/users.json` | — | **SELECTION** | user list for the pull | — | → `derived/`. |
| `raw/codenet/Project_CodeNet/metadata/` | 4,054 CSVs | **AS-RECEIVED** | IBM S3 `Project_CodeNet_metadata.tar.gz`, extracted | no (archive deleted, no hash) | Official IBM release, complete (4,053 problems + `problem_list.csv`). Keep. |
| `raw/codenet/Project_CodeNet/problem_descriptions/` | 3,999 HTML | **AS-RECEIVED** | same archive | no | 54 problems have no statement upstream ("for most problems" — IBM README). Keep. |
| `raw/codenet/Project_CodeNet/README.md` | — | AS-RECEIVED | same archive | — | Keep. |
| *(missing)* CodeNet source code | — | — | — | — | **Not in `raw/` at all.** See next row. |
| `warehouse/parquet/codenet_cpp.parquet` | 390 MB, 2.0M rows | **DERIVED** | `iNeil77/CodeNet` HF mirror → C++ only → 9,998-user sample (seed 20260101) → joined to IBM metadata | no (`resolve/main`; mirror now at `1e28cfa`) | The only copy of CodeNet code, and it is a filtered derivative of a *third-party* mirror whose shards were deleted after the build. Mirror was spot-checked on 200 rows only; it declares no license (upstream CodeNet is CDLA-Permissive-2.0). |
| `raw/aicd_bench/T1/*.parquet` | 864 MB, 6 files | **AS-RECEIVED** | HF `AICD-bench/AICD-Bench` | **verified**: all 6 sha256 equal LFS oids at rev `b7dc6d82257de6e0146d1e7b0831abd7088633af` | Keep; pin to that revision. |
| `raw/aigcodeset/*.csv` | 3.9 MB, 2 files | **AS-RECEIVED** | HF `basakdemirok/AIGCodeSet` | **verified**: git blob hashes equal rev `b4d4e69fdbcba2608c99808c6afbf53a3bfd108c` | Keep; pin. (Third upstream file — 265 MB of ada embeddings — not needed.) |
| `warehouse/integrity.duckdb` | 1.0 GB | **DERIVED** | `warehouse.py build/cohort` | — | All 11 tables (`*_submissions`, `*_problems`, `*_profiles`, `*_cohort_problems`) are derived. Rebuild from canonical; do not migrate. |
| `MANIFEST.json` | 4,113 entries | integrity only | `tools/manifest.py` | — | Path → 16-hex truncated hash + bytes. No URL, revision, method, date or license. Replace with per-snapshot manifests. |
| `pilots/p1_openr1/inputs/` | 0.36 GB | **AS-RECEIVED** | HF open-r1 rev `07e7bf3`; CF `contest.status` ×21 with per-page provenance | yes | Already meets the raw-layer standard; becomes the first new snapshots. |

## What this means

1. **`raw/` today is a mix of three of the four classes.** Only CodeNet metadata, AICD and
   AIGCodeSet are genuinely raw. Both live-platform pulls were reshaped on write, so the
   original API responses no longer exist anywhere.
2. **The most important data — CodeNet source code — has no raw copy.** It exists only as a
   sampled derivative. Everything downstream depends on re-acquiring it.
3. **Two upstream datasets can be pinned retroactively** (AICD, AIGCodeSet) because their bytes
   still match a published revision. The others cannot; they must be re-acquired to be pinned.
4. **Handles/user ids in clear** appear in the Codeforces and AtCoder pulls. That is fine in
   `raw/` (public data, as received); `canonical/` stores salted hashes only.

## Proposed migration

| New snapshot | Action | Cost |
|---|---|---|
| `raw/codenet_ibm_metadata/<tar-date>/` | move existing extracted metadata + descriptions; record that the archive hash is unknown | none |
| `raw/codenet_code/<mirror rev>/` | re-download `iNeil77/CodeNet` pinned at `1e28cfa`, unfiltered; verify **every** row against IBM metadata, not 200 | 3.6 GB (C++) — minutes |
| `raw/aicd_bench/b7dc6d8/`, `raw/aigcodeset/b4d4e69/` | move existing files, write manifests | none |
| `raw/openr1_cf_submissions/07e7bf3/` | full 36 shards | 5.6 GB |
| `raw/codeforces_api/<date>/` | `problemset.problems` as received; `contest.status` per contest (the background join) | 1 call; 8–16 h |
| `raw/atcoder_kenkoooo/<dump date>/` | the official weekly `submissions.csv.gz` dump + `problems.json` + `problem-models.json` as received — complete and one snapshot, replacing the per-user pull | 1.6 GB |
| `raw/_legacy_2026-10/` | the reshaped CF/AtCoder pulls, read-only, tagged RESHAPED; canonical never reads them | none |
| delete | `warehouse/` and `MANIFEST.json` once canonical is rebuilt | — |

## Migration status (2026-10-07)

Done:
- Moved CodeNet metadata, AICD (`b7dc6d8`), AIGCodeSet (`b4d4e69`) and the pilot-1 inputs into
  pinned snapshots; old pulls into `raw/_legacy_2026-10/`; old warehouse into
  `derived/_legacy_warehouse_2026-10/`; old `MANIFEST.json` into the legacy snapshot.
- Re-acquired CodeNet C++ code unfiltered at mirror rev `1e28cfa` (24/24 shards hash-checked).
  Verified **every** row against IBM metadata: 7,982,624 rows, 0 not in IBM, 0 duplicate ids,
  0 disagreeing values, code byte length = IBM `code_size` on all rows. Mirror lacks 25,903
  official C++ rows (0.32%) and 237 users.
- Manifests written for all 7 snapshots; `raw/` locked read-only.

Pending — blocked on disk space (5.6 GB free after the above):
- open-r1 remaining 35 shards (5.4 GB), Codeforces `contest.status` for 1,882 contests (3–6 GB
  gzipped), kenkoooo AtCoder dump (1.6 GB; freshness to confirm), as-received
  `problemset.problems` and kenkoooo `problems.json` + `problem-models.json` (small).
- 2026-10-07: `derived/_legacy_warehouse_2026-10/` deleted by the user (1.4 GB).

## Trim (2026-10-07) — the project does not need every published row

Decided with the user: keep enough data for every lane, not everything. Raw went from ~13 GB to
~1.1 GB (+ open-r1 until its subset is cut). Each full snapshot was replaced by a verified subset
and only then deleted; manifests of deleted snapshots are in `raw/_retired_manifests/`.

| Parent | Now | Verification |
|---|---|---|
| `codenet_code_cpp/1e28cfa` (3.7 GB) | `_u20`: 20% users, 1,652,872 rows, 0.33 GB | EXCEPT ALL both ways = 0 |
| `codenet_metadata/acq-2026-10-02` (1.2 GB) | `_gz`: same files gzipped, 0.30 GB | every decompressed sha256 = original |
| `atcoder_kenkoooo_dump/2025-11-21` (1.6 GB) | `_u05`: 5% users, 3,275,403 lines, 0.08 GB | lines copied verbatim; count + EXCEPT ALL = 0 |
| `aicd_bench/b7dc6d8` (0.9 GB) | `_trainval`, 0.25 GB | sha256 of kept files unchanged |
| `_legacy_2026-10`, both `codeforces_api_contest_status` snapshots, pilot metadata scan | deleted (user decision) | manifests retired |

Codeforces strategy changed: instead of `contest.status` for every contest (~93% practice traffic),
`user.ratedList` (personal fields dropped) → 1% hash sample → `user.status` per user → keep only
the open-r1 rows of those users.

Incident: a CodeNet rebuild spilled ~6 GB of DuckDB temp files while the contest.status join was
writing; the join's disk guard stopped it safely, but I misattributed the spill and deleted the temp
directory under a running build, orphaning it and leaving canonical half-rewritten. Fixed by
rebuilding; builders now stage, check, then swap, and stream submissions in 16 parts.
