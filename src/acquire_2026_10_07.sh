#!/usr/bin/env bash
# Acquisition run of 2026-10-07. Every raw snapshot it creates is as received; this file is the
# record of exactly how. Phases are resumable: rerun the script and finished files are skipped.
#
#   1. open-r1/codeforces-submissions — complete snapshot 07e7bf3 (35 more shards + selected_incorrect)
#   2. kenkoooo AtCoder: full submissions dump + current resource JSONs
#   3. Codeforces catalog: problemset.problems, contest.list
#   4. Codeforces contest.status for every contest in open-r1 (the author join), hours
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
RAW=data/raw
SPECS=$(mktemp -d)

free_gb() { df -g . | awk 'NR==2 {print $4}'; }
need_gb() { [ "$(free_gb)" -ge "$1" ] || { echo "need $1 GB free, have $(free_gb) GB — stopping"; exit 1; }; }
unlock() { chmod u+w "$RAW"; [ -d "$(dirname "$1")" ] && chmod u+w "$(dirname "$1")"; mkdir -p "$1"; chmod -R u+w "$1"; }
lock() { chmod -R a-w "$(dirname "$1")"; chmod a-w "$RAW"; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ---------------------------------------------------------------- 1. open-r1
OR1=$RAW/openr1_cf_submissions/07e7bf3
REV=07e7bf31f284b0e90e02dfc7ab675d5821e1c068
need_gb 7
unlock $OR1
curl -s "https://huggingface.co/api/datasets/open-r1/codeforces-submissions/tree/$REV?recursive=true" \
  | $PY -c "import json,sys
for f in json.load(sys.stdin):
    if f['type']=='file' and f['path'].endswith('.parquet'): print(f['path'], f['lfs']['oid'])" \
  | while read -r p oid; do
      out=$OR1/$p; mkdir -p "$(dirname "$out")"
      if [ -f "$out" ] && [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" = "$oid" ]; then continue; fi
      curl -sfL --retry 5 -o "$out" "https://huggingface.co/datasets/open-r1/codeforces-submissions/resolve/$REV/$p"
      [ "$(shasum -a 256 "$out" | cut -d' ' -f1)" = "$oid" ] && log "open-r1 ok $p" || { log "HASH MISMATCH $p"; exit 1; }
    done
cat > $SPECS/openr1.json <<'EOF'
{"source":"openr1_cf_submissions","class":"as_received","complete":true,
 "upstream":{"name":"open-r1/codeforces-submissions","url":"https://huggingface.co/datasets/open-r1/codeforces-submissions","revision":"07e7bf31f284b0e90e02dfc7ab675d5821e1c068","license":"ODC-By 4.0 per card text; card YAML says cc-by-4.0"},
 "method":"HTTP download of every .parquet from resolve/<revision>/, upstream paths preserved; each sha256 checked against its LFS oid",
 "retrieved_at":"2026-10-06 (shard 0, selected_accepted) / 2026-10-07 (rest)",
 "notes":["Main-split `source` is the original code (pilot 1: md5 equals og_source for all 1,017 rows the curated subset rewrote). selected_* configs are curated and may be rewritten.","No author field upstream; authors come from codeforces_api_contest_status.","Pilot 1 coverage: 99.9% of accepted in-contest submissions, 3.7% of failed in-contest, ~1% of practice."]}
EOF
$PY src/raw_manifest.py $OR1 --spec $SPECS/openr1.json
lock $OR1

# ---------------------------------------------------------------- 2. kenkoooo AtCoder
DUMP=$RAW/atcoder_kenkoooo_dump/2025-11-21          # named by the dump's Last-Modified date
DUMP_URL=https://s3-ap-northeast-1.amazonaws.com/kenkoooo/submissions.csv.gz
need_gb 3
unlock $DUMP
if [ ! -f $DUMP/submissions.csv.gz ] || [ "$(stat -f%z $DUMP/submissions.csv.gz)" != 1603172550 ]; then
  log "kenkoooo dump: downloading 1.6 GB"
  curl -sfL --retry 5 -C - -o $DUMP/submissions.csv.gz $DUMP_URL
fi
lm=$(curl -sI $DUMP_URL | awk -F': ' 'tolower($1)=="last-modified" {print $2}' | tr -d '\r')
[ "$lm" = "Fri, 21 Nov 2025 11:01:10 GMT" ] || { log "dump changed upstream ($lm) — new snapshot needed"; exit 1; }
gzip -t $DUMP/submissions.csv.gz && log "kenkoooo dump: gzip integrity ok"
cat > $SPECS/dump.json <<'EOF'
{"source":"atcoder_kenkoooo_dump","class":"as_received","complete":true,
 "upstream":{"name":"AtCoder Problems (kenkoooo) full submissions dump","url":"https://s3-ap-northeast-1.amazonaws.com/kenkoooo/submissions.csv.gz","revision":"Last-Modified Fri, 21 Nov 2025 11:01:10 GMT; ETag a475cd1985e0d85ab48b16b9f9f7df85-192; 1,603,172,550 bytes","license":"not stated; AtCoder Problems API docs ask for >1 s between accesses"},
 "method":"single HTTP download (curl, resumable); size equals Content-Length, Last-Modified rechecked after download, gzip -t passed",
 "retrieved_at":"2026-10-07",
 "notes":["Metadata only — no source code. Every AtCoder user, not a sample.","Upstream had not been refreshed for ~10.5 months at retrieval; nothing after 2025-11-21. For later activity of 7,586 users see _legacy_2026-10/atcoder (reshaped-free rows, selected users)."]}
EOF
$PY src/raw_manifest.py $DUMP --spec $SPECS/dump.json
lock $DUMP

RES=$RAW/atcoder_kenkoooo_resources/acq-2026-10-07
unlock $RES
for r in problems problem-models contests contest-problem; do
  [ -f $RES/$r.json ] || { curl -sfL --retry 5 -o $RES/$r.json https://kenkoooo.com/atcoder/resources/$r.json; sleep 1.5; }
  $PY -c "import json;json.load(open('$RES/$r.json'))" && log "kenkoooo $r.json ok"
done
cat > $SPECS/res.json <<'EOF'
{"source":"atcoder_kenkoooo_resources","class":"as_received","complete":true,
 "upstream":{"name":"AtCoder Problems resources","url":"https://kenkoooo.com/atcoder/resources/{problems,problem-models,contests,contest-problem}.json","revision":"live, see retrieved_at","license":"not stated"},
 "method":"HTTP GET of each file, 1.5 s apart; each parsed as JSON to confirm completeness",
 "retrieved_at":"2026-10-07",
 "notes":["problem-models.json holds the fitted IRT difficulty/discrimination per problem."]}
EOF
$PY src/raw_manifest.py $RES --spec $SPECS/res.json
lock $RES

# ---------------------------------------------------------------- 3. Codeforces catalog
CAT=$RAW/codeforces_api_catalog/acq-2026-10-07
unlock $CAT
for m in problemset.problems contest.list; do
  [ -f $CAT/$m.json ] || { curl -sfL --retry 5 -o $CAT/$m.json "https://codeforces.com/api/$m"; sleep 2.5; }
  $PY -c "import json;assert json.load(open('$CAT/$m.json'))['status']=='OK'" && log "codeforces $m ok"
done
cat > $SPECS/cat.json <<'EOF'
{"source":"codeforces_api_catalog","class":"as_received","complete":true,
 "upstream":{"name":"Codeforces API problemset.problems + contest.list","url":"https://codeforces.com/api/{problemset.problems,contest.list}","revision":"live, see retrieved_at","license":"Codeforces terms; API use permitted"},
 "method":"HTTP GET, 2.5 s apart; status == OK checked",
 "retrieved_at":"2026-10-07",
 "notes":["problemset.problems holds both `problems` (rating, tags) and `problemStatistics` (solvedCount), unmerged.","No statements or limits: those come from open-r1/codeforces if needed."]}
EOF
$PY src/raw_manifest.py $CAT --spec $SPECS/cat.json
lock $CAT

# ---------------------------------------------------------------- 4. Codeforces author join
JOIN=$RAW/codeforces_api_contest_status/acq-2026-10-07
need_gb 4
unlock $JOIN
$PY -c "import duckdb
ids = duckdb.sql(\"select distinct contestId from read_parquet('$OR1/data/*.parquet')\").fetchall()
print(' '.join(sorted((i for (i,) in ids if i), key=int)))" > $SPECS/contests.txt
log "author join: $(wc -w < $SPECS/contests.txt) contests"
$PY src/fetch_cf_status.py acq-2026-10-07 $(cat $SPECS/contests.txt)
$PY - "$SPECS/contests.txt" > $SPECS/join.json <<'EOF'
import json, sys
ids = open(sys.argv[1]).read().split()
print(json.dumps({"source": "codeforces_api_contest_status", "class": "as_received", "complete": True,
  "upstream": {"name": "Codeforces API contest.status", "url": "https://codeforces.com/api/contest.status",
               "revision": "live API — per-page retrieved_at in _provenance.jsonl",
               "license": "Codeforces terms; API use permitted (robots.txt allows /api/ for python-requests)"},
  "method": "src/fetch_cf_status.py: 100,000 rows per page, 2.5 s between calls; bodies gzipped unmodified",
  "retrieved_at": "2026-10-07 onward, see _provenance.jsonl",
  "selection": {"rule": "every contestId present in openr1_cf_submissions/07e7bf3", "n_contests": len(ids), "contest_ids": ids},
  "notes": ["All participant types and verdicts, all submissions of each contest.",
            "Contains Codeforces handles in clear (as served). Canonical stores salted hashes only."]}))
EOF
$PY src/raw_manifest.py $JOIN --spec $SPECS/join.json
lock $JOIN
log "ALL DONE. free: $(free_gb) GB"
