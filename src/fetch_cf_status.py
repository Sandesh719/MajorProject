"""Fetch Codeforces contest.status for a list of contests, raw and unmodified.

Each API page is saved gzipped exactly as received, and one provenance line per
request is appended to the snapshot's _provenance.jsonl (url, time, status, sha256).
Codeforces allows python-requests only under /api/, at most 1 call per 2 s.

usage: python fetch_cf_status.py <snapshot, e.g. acq-2026-10-08> 1036 1360 ...
"""
import gzip, hashlib, json, shutil, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = Path(__file__).parents[1] / "data/raw/codeforces_api_contest_status"
PAGE = 100_000
GAP_S = 2.5


def get(url):
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            # The API answers {"status":"FAILED","comment":...} with a 4xx. A rate-limit reply is
            # retried; any other FAILED (e.g. contest not found) is the real answer, kept as received.
            body = e.read()
            if b"limit" not in body.lower():
                return e.code, body
            print(f"  rate-limited, retry {attempt + 1}", file=sys.stderr)
        except Exception as e:  # network hiccup / timeout
            print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
        time.sleep(GAP_S * (attempt + 2))
    raise RuntimeError(f"giving up on {url}")


MIN_FREE_GB = 2.0


def fetch_contest(cid):
    page_from = 1
    while True:
        if shutil.disk_usage(OUT).free / 1e9 < MIN_FREE_GB:
            sys.exit(f"stopping: under {MIN_FREE_GB} GB free on disk (resume later, nothing is lost)")
        dest = OUT / f"contest_status_{cid}_from{page_from}.json.gz"
        if dest.exists():  # resumable: raw pages are never re-fetched or overwritten
            body = gzip.decompress(dest.read_bytes())
        else:
            url = f"https://codeforces.com/api/contest.status?contestId={cid}&from={page_from}&count={PAGE}"
            status, body = get(url)
            dest.write_bytes(gzip.compress(body))
            with open(OUT / "_provenance.jsonl", "a") as log:
                log.write(json.dumps({
                    "file": dest.name, "url": url, "http_status": status,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "sha256_raw": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                }) + "\n")
            time.sleep(GAP_S)
        n = len(json.loads(body).get("result", []))
        print(f"contest {cid} from {page_from}: {n} rows")
        if n < PAGE:
            return
        page_from += PAGE


if __name__ == "__main__":
    OUT = RAW / sys.argv[1]  # a new snapshot dir, e.g. acq-2026-10-08
    OUT.mkdir(parents=True, exist_ok=True)
    for cid in sys.argv[2:]:
        fetch_contest(cid)
