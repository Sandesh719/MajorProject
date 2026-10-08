# data/

Three layers. Data flows one way: `raw → canonical → derived`.

```
raw/<source>/<snapshot>/     ACQUIRED. Exactly as received from outside. Read-only.
canonical/                   SINGLE SOURCE OF TRUTH. Unified schema, built only from raw/.
derived/<dataset>/           MADE BY US. Everything built from canonical/, each with recipe.json.
```

## Rules

1. **`raw/` holds only what came from outside, byte for byte.** No renaming of fields, no
   filtering, no merging. Upstream file paths are preserved inside a snapshot.
2. **Every raw snapshot has a `MANIFEST.json`** written by `src/raw_manifest.py`: source, class,
   upstream url + revision + license, method, retrieval date, and a sha256 per file. It is
   written once; only a snapshot marked `"complete": false` may later be appended to.
3. **Snapshot names:** the upstream revision when one exists (`b7dc6d8`), otherwise the
   acquisition date (`acq-2026-10-06`). A re-fetch is a new snapshot, never an overwrite.
4. **`canonical/` is a pure function of `raw/`.** It can be deleted and rebuilt at any time.
   Every row carries the `source_id` of the snapshot it came from. Nothing synthetic, sampled
   or filtered-for-a-purpose goes in. User handles are stored only as salted hashes.
5. **`derived/` is everything else**: samples, cohorts, splits, features, lane inputs, baseline
   inputs, AI-generated positives. Each dataset has a `recipe.json` naming the canonical build it
   came from, the script and its version, parameters and seed, and whether rows are synthetic.
6. **`raw/` stays read-only** (`chmod -R a-w data/raw`). To add a snapshot:
   `chmod u+w data/raw/<source>`, add it, write its manifest, lock again.

## Current raw snapshots

Large upstreams are kept as **verified subsets** (`as_received_subset`): rows unchanged, selected by a
stated rule, with the parent's manifest sha256. User samples use one neutral, nested rule —
`src/sample_rule.py`: keep every row of user u iff `sha256(u)[:8] < frac·16⁸`. Full parents were
deleted only after verification; their manifests (upstream file hashes, for bit-identical
re-acquisition) are in `raw/_retired_manifests/`. Trimming tool: `src/trim_raw.py`.

| Snapshot | What | Class | Size |
|---|---|---|---|
| `codenet_metadata/acq-2026-10-02_gz` | IBM CodeNet metadata (all 13.9M rows) + statements; CSVs gzipped, content sha256 kept | as received | 0.30 GB |
| `codenet_code_cpp/1e28cfa_u20` | CodeNet C++ code, **20% of users** (1.65M submissions, 20,685 users) | subset | 0.33 GB |
| `atcoder_kenkoooo_dump/2025-11-21_u05` | AtCoder submissions metadata, **5% of users** (3.28M rows, 30,913 users), lines byte-identical | subset | 0.08 GB |
| `atcoder_kenkoooo_resources/acq-2026-10-07` | problems, IRT models, contests, contest-problem | as received | 7 MB |
| `codeforces_api_catalog/acq-2026-10-07` | problemset.problems, contest.list | as received | 3 MB |
| `codeforces_api_rated_list/acq-2026-10-07` | 980,102 rated users; personal fields dropped | projection | 23 MB |
| `codeforces_api_user_status/acq-2026-10-07` | full histories of the **1% user sample** (9,842 users, 2.57M submissions) | as received | 0.12 GB |
| `openr1_cf_submissions/07e7bf3_cfu` | open-r1 Codeforces code of the sampled users (126,021 rows) | subset | 0.03 GB |
| `aicd_bench/b7dc6d8_trainval` | AICD T1 train + validation | subset | 0.25 GB |
| `aigcodeset/b4d4e69` | AIGCodeSet | as received | 4 MB |

## Canonical layer

`data/canonical/<table>/corpus=<corpus>/data.parquet` for `problems`, `users`, `submissions`,
`code_blobs`, `contests`, `contest_problems`; one build record per corpus in `data/canonical/_builds/<corpus>.json` (input
manifests' sha256, script sha256, map sha256, row counts, checks). Read it only through
`src/canonical/db.py` (`connect()` → views over every corpus, plus a `code` view).

- **ids** are namespaced by corpus (`codenet:u…`); the online judge is the `judge` column on
  `problems` and on every `submissions` row.
- **raw values are kept beside normalised ones** (`verdict_raw`/`verdict`, `lang_raw`/`lang_family`,
  `accuracy_raw`/`passed_tests`+`total_tests`). Mapping rules live in `src/canonical/maps/`.
- **code is never copied**: `submissions.code_sha256` → `code_blobs` (raw file + row) → the bytes
  in `data/raw/`. `code_status` says why code is absent: `not_sampled` (user outside the code sample),
  `not_acquired` (language not acquired), `missing_in_mirror` (sampled, but upstream lacks it).
- **builds are staged**: written to `data/canonical/_staging/`, checked, then swapped in, so an
  interrupted build never leaves a half-written corpus.

| Corpus | Built by | Inputs |
|---|---|---|
| codenet | `src/canonical/build_codenet.py` | `codenet_metadata/acq-2026-10-02_gz`, `codenet_code_cpp/1e28cfa_u20` |
| atcoder | `src/canonical/build_atcoder.py` | `atcoder_kenkoooo_dump/2025-11-21_u05`, `atcoder_kenkoooo_resources/acq-2026-10-07` |
| codeforces | `src/canonical/build_codeforces.py` | `codeforces_api_catalog/acq-2026-10-07`, `codeforces_api_rated_list/acq-2026-10-07`, `codeforces_api_user_status/acq-2026-10-07`, `openr1_cf_submissions/07e7bf3_cfu` |

**User ids from real handles** (AtCoder, Codeforces) are `left(sha256(salt + ':' + handle), 16)`.
The salt is `.secrets/user_salt` — outside `data/`, never published. **Back it up**: a rebuild
with a different salt gives every user a new id and breaks every derived dataset keyed on users.
Each build record stores the salt's own sha256 so a mismatch is detectable.

## Derived layer

`data/derived/<name>/v<N>/` (`corpus=<corpus>/data.parquet` + `recipe.json`), built by
`src/derived/<name>.py` through `src/derived/common.py`, which enforces: read canonical or derived only,
stage → check → swap, and a recipe with the sha256 of every canonical build record and derived recipe read,
the script's sha256, all parameters, and `is_synthetic`. Changing a choice means a new version, never an
overwrite with different parameters. Read with `src.derived.common.register(con, name)`.

| # | Dataset | Key | What it is · why we need it | Inputs |
|---|---|---|---|---|
| D1 | `splits` | user | fixed 70/15/15 user-disjoint split by `sha256('split-v1:'+user_uid)` · nobody is in train and test | canonical |
| D2 | `attempts` | submission | place in the (user, problem) attempt chain, event type, line diff to the previous attempt · lane B, AI-fix positives | canonical |
| D3 | `code_views` **v2** | code_sha256 | normalised, `cpp -P`-expanded, token-type stream, parse stats · CP C++ is unparseable before macro expansion | canonical |
| D4 | `code_features` | code_sha256 | 52 layout / lexical / structure features · lane A fingerprint, rp2 baseline | D3 + canonical code |
| D5 | `problem_difficulty` **v2** | problem | Rasch difficulty from first attempts, train users only, validated vs AtCoder IRT · lane B | D1, D2, D6 |
| D6 | `problem_links` | CodeNet problem | CodeNet AtCoder problems → real AtCoder ids · validates D5 | canonical |
| D7 | `profiles` | eligible submission | as-of-t style + skill aggregates (strictly earlier seconds) · the per-person model | D2, D4, D5 |
| D8 | `cohorts` | (problem, solver) | who solved what and when + `cohort_percentile()` helper · cohort normalisation | D1, D2 |
| D9 | `dup_groups` | accepted code submission | exact and MinHash near-duplicate clusters per problem · lane S labels | D3 |
| D10 | `positives_p1` **v2** | pair | ghostwriter swaps, **synthetic** · labelled positives before any LLM | D1, D2, D8 |
| D11 | `eval_frame` | row to score | the frozen exam: D10 pairs + natural submissions (≤20/user) with split and difficulty band · every method is scored on exactly these rows by `src/eval/harness.py` | D1, D5, D7, D10 |

Build everything in dependency order: `src/derived/build_all.sh` (rehearse with `SMOKE=1`, which writes
`<name>_smoke` from a small sample). Long builds cache finished parts outside staging, so a rerun resumes.

Version history (each earlier version removed before any lane used it):
D3 v2 — a file is C++ if any submission of it is C++ (v1 dropped 43 C++ submissions); key = bytes across corpora.
D5 v2 — fixed row order and tol 1e-8 (v1's default tol gave up to 1.2-logit differences between row orders).
D10 v2 — C++ only for targets, history and donors (v1 could swap in another language: detectable by language alone).

Tests: `tests/test_canonical.py`, `tests/test_cohort_percentile.py`, `tests/test_profiles_leakage.py`, `tests/test_eval.py`
(`.venv/bin/pytest tests -q`).

History of how this layout came about: `docs/DATA_AUDIT.md`.
