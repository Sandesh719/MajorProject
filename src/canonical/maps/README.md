# Normalisation rules

Rules we chose, versioned with the code that applies them. Each canonical build records the
sha256 of every map it used.

- `verdict_map.csv` — exact `verdict_raw` → `verdict` per corpus. Codes: AC WA PE RE TLE MLE OLE CE
  PARTIAL OTHER JUDGE_ERROR PENDING. Two rules are applied in code, not listed here:
  AtCoder progress strings like `2/3 TLE` or `53/63 ` (captured mid-judging) → PENDING.
  JUDGE_ERROR = the judge failed (says nothing about the candidate); PENDING = not final.
  Codeforces: a submission with no verdict (still testing) → PENDING. CHALLENGED (hacked after
  passing pretests) and SKIPPED (not system-tested because a later submission counted) → OTHER:
  not accepted, but not a plain wrong answer either; verdict_raw keeps the distinction.
- `language_family.csv` — for corpora without a curated family (AtCoder, Codeforces), the first
  matching pattern by priority wins (RE2 syntax, searched anywhere in `lang_raw`); otherwise the
  family is the first token of `lang_raw`.
  CodeNet uses IBM's own `language` column as its family.
