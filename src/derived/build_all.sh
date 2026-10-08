#!/usr/bin/env bash
# Every derived dataset in dependency order; each only after the previous succeeded; then all tests.
#   D1 splits, D2 attempts, D6 problem_links, D5 problem_difficulty, D8 cohorts, D10 positives_p1,
#   D3 code_views, D4 code_features, D7 profiles (checks D10 targets), D9 dup_groups
# Rehearse with SMOKE=1. Logs: logs/derived_<name>.log
# FROM=<step> resumes the chain at that step (e.g. FROM=code_views).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
STEPS="splits attempts problem_links problem_difficulty cohorts positives_p1 code_views code_features profiles dup_groups eval_frame"
[ -n "${FROM:-}" ] && STEPS="${STEPS#*"$FROM"}" && STEPS="$FROM$STEPS"
for step in $STEPS; do
  echo "[$(date '+%F %H:%M:%S')] start $step"
  $PY src/derived/$step.py > logs/derived_${step}.log 2>&1 || { echo "FAILED $step — see logs/derived_${step}.log"; exit 1; }
  grep -v "▕" logs/derived_${step}.log | grep -E "written|failed" || true
done
echo "[$(date '+%F %H:%M:%S')] tests"
$PY -m pytest tests -q -p no:cacheprovider 2>&1 | tail -3
