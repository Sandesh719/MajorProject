# Canonical schema — v1 (frozen 2026-10-07)

The contract every derived dataset, baseline and lane codes against. Read it through
`src/canonical/db.py` → `connect()`, which exposes each table as one view over all corpora.
Changing a column's meaning means schema v2, not an edit here.

**Fill:** which corpora populate a column — **C** CodeNet, **A** AtCoder, **F** Codeforces.
A blank means NULL for that corpus because the source does not provide it — never a default.
`*_raw` columns hold the source value untouched; the column next to it is our normalisation.

## Identifiers

`<corpus>:<native id>` for submissions, problems and contests (`codenet:s694813024`,
`codeforces:1036B`). Users: `codenet:u…` (IBM's pseudonym, as given); `atcoder:` / `codeforces:` +
first 16 hex of `sha256(salt + ':' + handle)` — the salt is `.secrets/user_salt`. Ids from different
corpora never refer to the same entity, even when the judge is the same (CodeNet's AtCoder users
cannot be linked to real AtCoder accounts).

## submissions — one row per judged submission

| Column | Type | Fill | Meaning |
|---|---|---|---|
| `submission_uid` | varchar | CAF | primary key |
| `corpus` | varchar | CAF | `codenet` / `atcoder` / `codeforces` — which dataset |
| `judge` | varchar | CAF | which online judge: `AtCoder`, `AIZU`, `Codeforces` |
| `native_submission_id` | varchar | CAF | id as the source gives it |
| `problem_uid` | varchar | CAF | → `problems` |
| `user_uid` | varchar | CAF | → `users`. **NULL for Codeforces team submissions** |
| `team_size` | int | F | members of the submitting party (1 = individual) |
| `team_member_uids` | varchar[] | F | members, only when `team_size > 1` |
| `contest_uid` | varchar | AF | contest the submission was made in |
| `participant_type` | varchar | F | `CONTESTANT`, `PRACTICE`, `VIRTUAL`, `OUT_OF_COMPETITION`, … |
| `ts_unix`, `ts_utc` | bigint, timestamp | CAF | submission time (UTC) |
| `relative_time_s` | bigint | F | seconds since the participant's contest start; NULL outside the contest window |
| `participant_start_unix` | bigint | F | when this participant's contest (or virtual run) started |
| `lang_raw` | varchar | CAF | language string as given (`C++14 (GCC 5.4.1)`) |
| `lang_family` | varchar | CAF | `C++`, `Python`, `Java`, … (C: IBM's family; A, F: `maps/language_family.csv`) |
| `filename_ext` | varchar | C | IBM's file extension |
| `verdict_raw` | varchar | CAF | verdict as given |
| `verdict` | varchar | CAF | `AC WA PE RE TLE MLE OLE CE PARTIAL OTHER JUDGE_ERROR PENDING` (`maps/verdict_map.csv`) |
| `accepted` | boolean | CAF | `verdict = 'AC'` |
| `testset` | varchar | F | `TESTS`, `PRETESTS`, … |
| `passed_tests` | int | C (AOJ rows), F | tests passed |
| `total_tests` | int | C (AOJ rows) | tests in the set |
| `accuracy_raw` | varchar | C (AOJ rows) | IBM's `3/4` |
| `score` | double | AF | points awarded |
| `runtime_ms` | int | CAF | judge CPU time; NULL when the code never ran (CE) |
| `memory_kb` | int | CF | judge memory |
| `code_size_bytes` | int | CA | source length in bytes as recorded by the judge |
| `code_sha256` | varchar | CF | → `code_blobs`; NULL when no code is held |
| `code_status` | varchar | CAF | `present`; else why not: `not_sampled` (user outside the code sample), `not_acquired` (language/source not acquired), `missing_in_mirror`, `not_in_openr1` |
| `source_id` | varchar | CAF | the raw snapshot this row came from |

## problems — one row per problem

| Column | Type | Fill | Meaning |
|---|---|---|---|
| `problem_uid` | varchar | CAF | primary key |
| `corpus`, `judge`, `native_problem_id` | varchar | CAF | as for submissions |
| `title` | varchar | CAF | problem name |
| `title_raw` | varchar | A | `A. Name` as given |
| `contest_uid`, `problem_index` | varchar | AF | the problem's home contest and letter |
| `statement`, `statement_format` | varchar | C | HTML statement (3,999 of 4,053 CodeNet problems) |
| `time_limit_ms`, `memory_limit_kb` | int | C | limits |
| `difficulty` | double | AF | **as published**: AtCoder IRT (raw scale) or Codeforces rating. CodeNet has none — see derived `problem_difficulty` |
| `difficulty_scale` | varchar | AF | `atcoder_irt_raw` / `cf_rating` — never compare across scales |
| `tags` | varchar[] | F | Codeforces tags |
| `irt_discrimination`, `irt_users`, `irt_is_experimental` | | A | kenkoooo IRT model fields |
| `problem_type`, `points`, `solved_count` | | F | Codeforces catalog fields |
| `rating_raw`, `tags_raw`, `complexity_raw` | varchar | C | IBM's columns — empty upstream in every row |
| `source_id` | varchar | CAF | |

## users — one row per user

| Column | Type | Fill | Meaning |
|---|---|---|---|
| `user_uid` | varchar | CAF | primary key |
| `corpus` | varchar | CAF | |
| `id_scheme` | varchar | CAF | `ibm_anonymised` / `salted_sha256` |
| `rating`, `max_rating`, `rank`, `max_rank` | | F | Codeforces rating **as of `rating_asof`** (a snapshot, not historical — never use as a feature at time t) |
| `rating_asof` | timestamp | F | when the rated list was fetched |
| `registered_utc`, `last_online_utc` | timestamp | F | |
| `first_seen`, `last_seen` | timestamp | CAF | first/last submission in this corpus (computed over all time — not as-of-t safe) |
| `in_history_sample` | boolean | F | the user's full history was fetched (1% sample) |
| `source_id` | varchar | CAF | |

Codeforces `users` holds all 980,102 rated users; only `in_history_sample` users have submissions.

## code_blobs — one row per distinct code file

| Column | Type | Meaning |
|---|---|---|
| `code_sha256` | varchar | sha256 of the exact source bytes. **Primary key is (`corpus`, `code_sha256`)**: each corpus points at its own raw copy, and identical bytes can occur in two corpora (one 11-byte file does) |
| `byte_len` | bigint | length in bytes |
| `ref_file`, `ref_row` | varchar, bigint | where the bytes live in `data/raw/` (first occurrence) |
| `n_submissions` | bigint | submissions sharing these exact bytes |
| `source_id`, `corpus` | varchar | |

The bytes themselves: the `code` view in `connect()` (`code_sha256`, `code`).

## contests, contest_problems

| `contests` column | Fill | Meaning |
|---|---|---|
| `contest_uid`, `corpus`, `judge`, `native_contest_id`, `name` | AF | |
| `start_unix`, `start_utc`, `duration_s` | AF | scheduled window |
| `rate_change_raw` | A | which ratings the contest affects |
| `contest_type`, `phase` | F | `CF`/`ICPC`/`IOI`; `FINISHED`/… |

`contest_problems(contest_uid, problem_uid, problem_index)` — a problem can sit in several contests.
CodeNet has no contests (IBM's anonymisation removed the link).

## Known limits a lane must respect

- **As-of-t:** `users.first_seen/last_seen`, `users.rating*`, `code_blobs.n_submissions` use all time. Never feed them to a model scoring a submission at time t.
- **Difficulty** is per-corpus and per-scale; CodeNet's comes from derived `problem_difficulty`.
- **Code** exists for CodeNet C++ (20% of users) and Codeforces (accepted contest-time submissions of the 1% user sample, via open-r1). Everything else is metadata only.
