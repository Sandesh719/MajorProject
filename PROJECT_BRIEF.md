# Project brief — paste this to start a new session

I am building my major project. Four research papers are attached as reference. Please read
this brief fully before suggesting anything.

---

## 1. The problem

With AI coding assistants (ChatGPT, Copilot, etc.), competitive programming platforms and
coding assessment systems cannot tell whether a submission reflects the candidate's own
work and understanding.

Traditional plagiarism tools (MOSS, JPlag) rely on **code similarity**, which fails here in
two opposite ways:

1. Different candidates naturally produce near-identical code, because they solve the same
   problem with the same standard algorithm and the same boilerplate templates.
2. AI-generated code is novel — it matches nothing in the corpus — so similarity detection
   is structurally blind to it.

The hardest case is **AI-assisted editing**: the candidate writes most of the solution
themselves and uses an LLM only to fix a bug or finish it. Most of the work is genuinely
theirs.

## 2. The reframing (this is the contribution)

The question is not "are these two files similar?" or "is this file AI-generated?" It is:

> Given candidate `c` submitting solution `s` to problem `p` at time `t`, how surprising is
> this submission under a model of **this specific person**, built only from what they had
> already submitted before `t`?

The system produces an **interpretable, calibrated integrity risk score** per submission,
not a binary plagiarised/not verdict. High-risk submissions go to a human evaluator with an
explanation of what drove the score. The score decides who gets looked at; a human decides
everything else.

## 3. The four signal lanes

| Lane | Question it asks | Inputs |
|---|---|---|
| **A — candidate-relative stylometry** | Does this look like *your* code? | this submission + your past code |
| **B — skill / solution consistency** | Could *you* have solved this, this fast, this way? | verdicts, timestamps, difficulty (no code needed) |
| **C — AI-generation likelihood** | Does this read as machine-written? | this submission only |
| **S — similarity / copy detection** | Does this match someone else? | this submission + a corpus |

Two design points that matter:

- **Cohort normalisation.** No raw score is used directly. Each becomes a *percentile
  against the other people who solved the same problem*, so "everyone's code looks alike"
  cannot by itself cause a flag. This is the main defence against false positives.
- **Strict as-of-`t` discipline.** Any profile used to judge a submission must be built from
  strictly earlier submissions by that user. Violating this leaks the answer into the
  features and invalidates every metric.

Lanes feed a fusion model → calibrated risk score → explanation → reviewer.

## 4. The reference papers (attached)

- **rp1 — AIGCodeSet**: a dataset of AI-generated vs human code, built with CodeLlama,
  Codestral and Gemini across three realistic scenarios (generate from scratch, fix a
  runtime error, fix a wrong answer). Their best detector was a Bayesian classifier.
  Source of the prompt templates for generating labelled positives.
- **rp2 — SemEval-2026 Task 13 stylometry**: char-level TF-IDF + structural features,
  K-means gating into clusters, logistic regression + Naive Bayes ensemble. Macro-F1 0.6125
  out of distribution.
- **rp3 — AICD Bench**: 2M examples, 77 generators, 9 languages. Reports that detector
  performance "remains far below practical usability", worst on hybrid (AI-edited) and
  adversarial code. Defines the human/machine/hybrid/adversarial taxonomy.
- **rp4 — Dickey, "The Failure of Plagiarism Detection in Competitive Programming"**:
  why similarity checking fails in CP courses; finds oral authorship verification is what
  actually works but is too labour-intensive to run on everyone. Our system is the triage
  layer that makes that affordable.

---

## 5. What is DONE: data collection only

All data lives under `data/` at the project root. **`data/raw/` is filesystem read-only**
(`chmod -R u+w data/raw` to re-open it). Integrity is hash-verified: 4,113 files, 3.09 GB.

### Collected sources

| Source | Volume | Carries source code? |
|---|---|---|
| Codeforces API | 3,422,644 submissions · 3,784 users · 33,414 problems | **No** — metadata only |
| AtCoder (kenkoooo API) | 23,242,826 submissions · 7,587 users · 9,645 problems | **No** — metadata only |
| Project CodeNet (C++ cohort) | 2,000,042 submissions · 9,998 users · 3,833 problems | **Yes** — 2.59 GB of C++ |
| Codeforces problem catalogue | 11,425 problems, 11,152 with official `rating` + `tags` | — |
| AtCoder problem catalogue | 9,625 problems, 4,841 with fitted IRT difficulty | — |
| CodeNet metadata + statements | 13.9M rows, 4,053 problems + HTML statements | — |
| AICD Bench T1 | 1.7M labelled human/machine samples (864 MB) | Yes |
| AIGCodeSet | 2,828 AI / 4,755 human Python | Yes |

Total **28.7M submissions**. Disk: `data/raw` 2.6 GB, `data/warehouse` 1.4 GB.

### Warehouse

`data/warehouse/integrity.duckdb` (DuckDB, queryable without a server) plus
`data/warehouse/parquet/codenet_cpp.parquet` (409 MB, the 2M code rows, exposed as a view).

Each source keeps its own table but shares a **column vocabulary** so the same code runs on
any of them: `user_id, submission_id, problem_id, ts, lang, lang_family, verdict, accepted`.

```
cf_submissions   … + problem_rating, problem_tags, participant_type, time_ms, memory_bytes, passed_tests
at_submissions   … + contest_id, code_length, time_ms, point
codenet_cpp      … + original_language, cpu_time, memory, code_size, code   ← the only one with source
cf_problems      problem_id, contest_id, index, name, rating, tags, points, solved_count
at_problems      problem_id, contest_id, index, name, difficulty, discrimination, irt_users, is_experimental
```

They are deliberately **not** unioned: user_id namespaces do not collide safely across
platforms, difficulty scales are not comparable, and only CodeNet has `code`.

### Cohort gates (all passed)

Criteria: ≥20 submissions, ≥12 accepted, spanning ≥30 days, in one language.

| Source | Qualifying profiles | Problems with ≥50 cohort solvers |
|---|---|---|
| CodeNet C++ (all qualified) | 35,960 | 2,784 |
| CodeNet C++ (10k sample built) | 9,998 | 1,876 |
| Codeforces C++ | 2,570 | 6,575 |
| AtCoder C++ | 5,843 | — |

---

## 6. Hard constraints found while collecting (do not re-litigate these)

1. **Source code cannot be scraped from live judges.** Codeforces, AtCoder, CodeChef and
   LeetCode all disallow automated collection of submission source in `robots.txt`;
   Codeforces additionally serves submission pages behind a bot challenge. LeetCode does not
   expose other users' code at all (`submissionDetails` returns null). **CodeNet is the
   licensed corpus — that is precisely why it exists.** CodeChef and LeetCode yielded
   nothing collectable.
2. **CodeNet has no usable difficulty or tags.** `problem_list.csv` has `rating`, `tags` and
   `complexity` columns, but they are empty in all 4,053 rows. Difficulty must be derived;
   Codeforces ratings and AtCoder IRT values exist to validate the derivation against.
3. **CodeNet `user_id` is pseudonymous** (`u434765267`), not an AtCoder handle, so CodeNet
   cannot be joined to the live AtCoder metadata.
4. **Solve time is a proxy.** CodeNet gives first-attempt → accepted; the moment the
   candidate started reading the problem is unknown. State as a limitation.
5. **Zero-diff resubmissions.** 2,068 submissions flip a failing verdict to Accepted with
   *byte-identical code* — the user simply picked a different compiler (98.5% of these show
   a changed `original_language`). Only 0.67% of transitions, but they have a diff of exactly
   zero, so any "smallest diff that fixed it" feature ranks them at the very top. They must
   be classified as their own event type.
6. **C++ needs the preprocessor before tokenising.** 42.3% of the cohort uses function-like
   macros (`#define rep(i,n) ...`). Tools that do not run `cpp -P` barely tokenise those
   files and return similarity 0.000 — a *silent false negative*, not an error.
7. **Outcome definition matters.** "Ever solved" is ~94% true (people resubmit until they
   pass) and carries almost no variance. "Solved on the first submission" is 60–67% and is
   also the event the system cares about.

---

## 7. What is NOT done

**Nothing beyond data collection is part of the current state.** In particular:

- No preprocessing layer. No AST parsing (`tree_sitter` not installed), no persisted
  as-of-`t` per-user profile store, no frozen train/validation/test splits, no
  near-duplicate or length filtering. This layer is the prerequisite for lane A and should
  be built first.
- No lanes built. None of A, B, C or S exist in the current project.
- No baselines built. None of the methods from the four papers are implemented.
- No labelled positives generated. AI-assisted positives (modify a candidate's own failing
  submission with an LLM, per rp1's scenarios) require an LLM; this machine currently has
  no API key, no Ollama and no torch/transformers.

An earlier exploratory pass exists under `backup/2026-10-06-v1/` (pullers, some baseline
experiments, a first lane B attempt, notes). **It is archived, not current.** Treat it as
reference only; I restarted because the work was built in the wrong order — baselines and a
lane before the preprocessing layer they both needed.

## 8. Environment

- `.venv/` at project root, Python 3.11. Installed: duckdb, numpy, pandas, scikit-learn,
  pyarrow, scipy. Not installed: tree_sitter, torch, transformers, xgboost, lifelines, ruptures.
- Activate with `source .venv/bin/activate`. Homebrew's system Python is PEP-668 managed and
  refuses installs, so use the venv.
- Java 21 available. DuckDB CLI installed (`duckdb -readonly -ui data/warehouse/integrity.duckdb`
  opens a browser UI; without `-readonly` it takes an exclusive lock that blocks every script).

## 9. What I want from this session

Start from the collected data and build in the right order. Before writing code, tell me the
plan and check it with me. I want to understand each step rather than accumulate scripts.
