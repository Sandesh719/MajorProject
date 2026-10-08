"""Fetch Codeforces user.status (a user's full submission history) for a hash sample of rated users.

Handles come from the projected user.ratedList snapshot; a handle is fetched iff the shared
sample rule (src/sample_rule.py) puts it in the sample at --frac. Each response body is saved
gzipped exactly as received, one provenance line per request in _provenance.jsonl. Resumable:
handles already saved are skipped, so a larger --frac later only fetches the new users.

usage: python src/fetch_cf_user_status.py --snapshot acq-2026-10-07 --frac 0.01
"""
import argparse, gzip, hashlib, json, shutil, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.sample_rule import in_sample  # noqa: E402

RATED = ROOT / "data/raw/codeforces_api_rated_list/acq-2026-10-07/user.ratedList.projected.json.gz"
RAW = ROOT / "data/raw/codeforces_api_user_status"
GAP_S, MIN_FREE_GB = 2.5, 2.0


def get(url):
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            # a real API answer is JSON {"status":"FAILED",...} (e.g. handle renamed) and is kept as received;
            # rate limits and gateway/server errors (5xx, HTML bodies) are retried
            if e.code < 500 and b"limit" not in body.lower() and body.lstrip().startswith(b"{"):
                return e.code, body
            print(f"  HTTP {e.code}, retry {attempt + 1}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr, flush=True)
        time.sleep(GAP_S * (attempt + 2))
    raise RuntimeError(f"giving up on {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--frac", type=float, required=True)
    a = ap.parse_args()
    out = RAW / a.snapshot
    out.mkdir(parents=True, exist_ok=True)
    users = json.loads(gzip.decompress(RATED.read_bytes()))["result"]
    handles = sorted(u["handle"] for u in users if in_sample(u["handle"], a.frac))
    todo = [h for h in handles if not (out / f"user_status_{h}.json.gz").exists()]
    print(f"{len(handles)} handles in the {a.frac:.0%} sample, {len(todo)} to fetch", flush=True)
    for i, h in enumerate(todo, 1):
        if shutil.disk_usage(out).free / 1e9 < MIN_FREE_GB:
            sys.exit(f"stopping: under {MIN_FREE_GB} GB free (resume later, nothing is lost)")
        url = "https://codeforces.com/api/user.status?handle=" + urllib.parse.quote(h)
        status, body = get(url)
        dest = out / f"user_status_{h}.json.gz"
        dest.write_bytes(gzip.compress(body, mtime=0))
        with open(out / "_provenance.jsonl", "a") as log:
            log.write(json.dumps({"file": dest.name, "url": url, "http_status": status,
                                  "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                  "sha256_raw": hashlib.sha256(body).hexdigest(), "bytes": len(body)}) + "\n")
        if i % 250 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(GAP_S)
    print("done", flush=True)


if __name__ == "__main__":
    main()
