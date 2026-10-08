# Pilot 1 — open-r1 Codeforces coverage (2026-10-06/07)

Question: can `open-r1/codeforces-submissions` supply Codeforces source code with per-user
histories, once joined to the official API for authors?

## Inputs (moved on 2026-10-07 into `data/raw/`, never modified)

| Raw snapshot | Source | Pin |
|---|---|---|
| `openr1_cf_submissions/07e7bf3/data/train-00000-of-00036.parquet` | HF `open-r1/codeforces-submissions`, shard 0 of 36 | rev `07e7bf31f284b0e90e02dfc7ab675d5821e1c068` |
| `openr1_cf_submissions/07e7bf3/selected_accepted/` | same repo, `selected_accepted` config | same rev |
| `codeforces_api_contest_status/acq-2026-10-06/` | `codeforces.com/api/contest.status`, 21 contests, 62 pages | per-page sha256 in its `_provenance.jsonl` |

`out/openr1_meta_all.parquet` holds metadata + `md5(source)` for all 36 shards, read remotely
(code not stored). sha256 of every input: the snapshots' `MANIFEST.json`.

Contests were picked by `hash(contestId)` within (median year 2013–2023) × (failure share
above/below 15%) — `out/pilot_contests.txt`.

## Findings

1. **open-r1 rows are genuine Codeforces submissions.** 161,642 / 161,642 rows in the pilot
   contests found in the API; timestamp, verdict, language, runtime agree 100%.
2. **Main-split code is unmodified.** The "potentially modified" warning applies to the curated
   subsets only: for all 1,017 rows that `selected_accepted` rewrote, the main split's
   `md5(source)` equals `og_source` (100%) and never the rewritten version (0%).
3. **Coverage is structured, not random:**

   | participant type | API rows | in open-r1 | accepted | failed |
   |---|---|---|---|---|
   | CONTESTANT | 245,653 | 40.8% | **99.9%** | 3.7% |
   | PRACTICE | 4,903,603 | 1.2% | 1.6% | 0.9% |
   | VIRTUAL | 94,909 | 3.1% | 4.6% | 2.1% |

   Overall 3.1% of the API population (5.8% of accepted, 1.0% of failed).
4. **Attempt chains are missing.** Of 28,159 contest (user, problem) pairs solved after ≥1
   failure, only 3.6% have any failed attempt's code. Across all pilot rows: 6,098 fail→OK
   pairs in open-r1 vs 850,297 in the API (0.7%).
5. **Global profile (36 shards):** 12,591,518 rows, 1,882 contests, 9,906 problems, no duplicate
   ids, 120,557 rows whose code is byte-identical to another row. 91% `OK`. Failure share
   swings by year (0% 2010–12, 34% 2018, 49% 2023) — mixed upstream sources.
6. **Join cost.** 21 contests = 5.25M API rows, 62 pages, 17 min, 2.85 GB raw (111 MB gzipped).

## Conclusion

open-r1 ≈ *every accepted contest-time submission, with code*, plus scattered extras.
Good for stylometry on accepted code (lane A, lane S). Not usable for fail→fix chains with
code (lane B's code features, AI-fix positives) — that stays CodeNet's job. Codeforces
metadata for lane B comes from the API, which is complete.
