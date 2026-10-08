#!/usr/bin/env bash
# Runs after the user.status fetch of 2026-10-07 finishes. Each step stops the chain on failure;
# retire deletes the full open-r1 only if the subset verified (trim_raw.py enforces that).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
US=data/raw/codeforces_api_user_status/acq-2026-10-07
log() { echo "[$(date '+%F %H:%M:%S')] $*"; }

grep -q '^done$' logs/cf_user_status_2026_10_07.log || { log "fetch did not finish cleanly — stopping"; exit 1; }
n_files=$(ls $US/user_status_*.json.gz | wc -l | tr -d ' ')
log "0/5 every history file must be a JSON API answer"
$PY - "$US" <<'PYEOF'
import gzip, json, sys
from pathlib import Path
bad = []
for f in Path(sys.argv[1]).glob("user_status_*.json.gz"):
    try:
        assert "status" in json.loads(gzip.decompress(f.read_bytes()))
    except Exception:
        bad.append(f.name)
sys.exit(f"not JSON API answers: {bad}" if bad else 0)
PYEOF
log "1/5 manifest for $US ($n_files histories)"
SPEC=$(mktemp)
$PY - "$SPEC" "$n_files" <<'EOF'
import json, sys
json.dump({"source": "codeforces_api_user_status", "class": "as_received", "complete": True,
  "upstream": {"name": "Codeforces API user.status", "url": "https://codeforces.com/api/user.status?handle=<handle>",
               "revision": "live API — per-file retrieved_at in _provenance.jsonl", "license": "Codeforces terms; API use permitted"},
  "method": "src/fetch_cf_user_status.py: one call per handle, 2.5 s apart; bodies gzipped unmodified (mtime 0)",
  "retrieved_at": "2026-10-07 onward, see _provenance.jsonl",
  "scope": {"handles_from": "raw/codeforces_api_rated_list/acq-2026-10-07",
            "rule": "keep every row of user u iff sha256(u)[:8] (hex) < frac * 16^8", "frac": 0.01, "n_handles": int(sys.argv[2])},
  "notes": ["Full histories: every verdict, practice/virtual/contest/gym, team submissions included.",
            "File names and bodies contain Codeforces handles in clear (as served). Canonical stores salted hashes only.",
            "2 handles first returned HTTP 503/504 gateway pages, which the fetcher then kept; those files were deleted and re-fetched on 2026-10-08 before this manifest was written. _provenance.jsonl keeps both attempts."]},
  open(sys.argv[1], "w"))
EOF
chmod -R u+w data/raw/codeforces_api_user_status
$PY src/raw_manifest.py $US --spec "$SPEC"
chmod -R a-w data/raw/codeforces_api_user_status

log "2/5 open-r1 subset for the sampled users"
$PY src/trim_raw.py openr1 acq-2026-10-07
log "3/5 retire the full open-r1"
$PY src/trim_raw.py retire openr1_cf_submissions/07e7bf3
log "4/5 canonical codeforces"
$PY src/canonical/build_codeforces.py 2>&1 | grep -v "▕"
log "5/5 canonical codenet (refresh build record against the final maps)"
$PY src/canonical/build_codenet.py 2>&1 | grep -v "▕\|submissions part" | tail -3
rm -rf .duckdb_tmp
log "ALL DONE — free: $(df -g . | awk 'NR==2{print $4}') GB"
