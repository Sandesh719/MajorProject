"""Trim large raw snapshots to the subsets the project needs, without changing a single row.

Each subcommand writes a NEW snapshot next to its parent, verifies it against the parent, and
writes its MANIFEST.json (class as_received_subset / as_received_projection, with the rule, the
parent's manifest sha256, and the verification result). `retire` then deletes a parent — only if a
verified child names it — after copying the parent's manifest to raw/_retired_manifests/, which
keeps the upstream file hashes needed to re-acquire it bit-identically.

  python src/trim_raw.py codenet_code     # CodeNet C++ code, 20% of users          (parquet rows, values unchanged)
  python src/trim_raw.py codenet_meta     # CodeNet metadata CSVs, gzipped          (bytes unchanged inside gzip)
  python src/trim_raw.py atcoder_dump     # kenkoooo dump, 5% of users              (CSV lines byte-identical)
  python src/trim_raw.py aicd             # AICD T1 train + validation only         (files moved, untouched)
  python src/trim_raw.py cf_rated_list FILE RETRIEVED_AT   # user.ratedList without personal fields
  python src/trim_raw.py retire <source>/<snapshot>
"""
import csv, gzip, hashlib, io, json, shutil, subprocess, sys
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.sample_rule import RULE_TEXT, in_sample, sql as sample_sql, threshold  # noqa: E402
from src.raw_manifest import inventory, sha256  # noqa: E402

RAW = ROOT / "data/raw"
PY = str(ROOT / ".venv/bin/python")


def unlock(*paths):
    for p in paths:
        subprocess.run(["chmod", "-R", "u+w", str(p)], check=True)


def lock(*paths):
    for p in paths:
        subprocess.run(["chmod", "-R", "a-w", str(p)], check=True)


def write_manifest(snap, spec):
    files = inventory(snap)
    m = {"snapshot": f"{snap.parent.name}/{snap.name}", **spec, "n_files": len(files),
         "total_bytes": sum(f["bytes"] for f in files), "files": files}
    (snap / "MANIFEST.json").write_text(json.dumps(m, indent=1, ensure_ascii=False, default=str) + "\n")
    print(f"{snap}/MANIFEST.json: {len(files)} files, {m['total_bytes'] / 1e9:.3f} GB")


def parent_info(parent):
    pm = json.loads((parent / "MANIFEST.json").read_text())
    return pm, {"snapshot": pm["snapshot"], "manifest_sha256": sha256(parent / "MANIFEST.json")}


def new_snapshot(parent, suffix):
    child = parent.parent / f"{parent.name}{suffix}"
    unlock(RAW, parent.parent)
    if child.exists():
        shutil.rmtree(child)
    child.mkdir()
    return child


# ---------------------------------------------------------------------------------------- CodeNet code
def codenet_code(frac=0.20):
    parent = RAW / "codenet_code_cpp/1e28cfa"
    pm, pinfo = parent_info(parent)
    child = new_snapshot(parent, f"_u{int(frac * 100):02d}")
    (child / "C++").mkdir()
    c = duckdb.connect()
    c.execute("set memory_limit='3GB'; set preserve_insertion_order=false")
    src = f"read_parquet('{parent}/C++/*.parquet')"
    c.execute(f"""copy (select * from {src} where {sample_sql('u_id', frac)}) to '{child}/C++/'
                  (format parquet, compression zstd, per_thread_output true, filename_pattern 'part-{{i}}')""")
    one = lambda q: c.execute(q).fetchone()[0]
    sub = f"read_parquet('{child}/C++/*.parquet')"
    want = f"(select * from {src} where {sample_sql('u_id', frac)})"
    ver = {
        "rows_expected": one(f"select count(*) from {want}"),
        "rows_written": one(f"select count(*) from {sub}"),
        "rows_in_subset_not_in_parent": one(f"select count(*) from (select * from {sub} except all select * from {want})"),
        "rows_in_parent_sample_not_in_subset": one(f"select count(*) from (select * from {want} except all select * from {sub})"),
        "users": one(f"select count(distinct u_id) from {sub}"),
    }
    ok = ver["rows_expected"] == ver["rows_written"] and ver["rows_in_subset_not_in_parent"] == 0 \
        and ver["rows_in_parent_sample_not_in_subset"] == 0
    ver["ok"] = ok
    print(json.dumps(ver, indent=1))
    if not ok:
        sys.exit("verification failed; parent untouched")
    write_manifest(child, {
        "source": "codenet_code_cpp", "class": "as_received_subset", "complete": True,
        "upstream": pm["upstream"], "retrieved_at": pm["retrieved_at"],
        "method": "rows of the parent snapshot selected by the user rule and rewritten as parquet; "
                  "every column and value unchanged (all VARCHAR, as upstream)",
        "subset_of": pinfo, "scope": {"rule": RULE_TEXT, "column": "u_id", "frac": frac, "threshold_hex": threshold(frac)},
        "notes": pm.get("notes", []), "parent_verification": pm.get("verification"), "verification": ver,
    })
    lock(child)


# ---------------------------------------------------------------------------------------- CodeNet metadata
def codenet_meta():
    parent = RAW / "codenet_metadata/acq-2026-10-02"
    pm, pinfo = parent_info(parent)
    child = new_snapshot(parent, "_gz")
    orig = {f["path"]: f["sha256"] for f in pm["files"]}
    bad = []
    for rel, h in orig.items():
        src, dst = parent / rel, child / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".csv"):
            data = src.read_bytes()
            with open(str(dst) + ".gz", "wb") as f:  # mtime=0 so the same input always gives the same .gz
                f.write(gzip.compress(data, compresslevel=9, mtime=0))
            if hashlib.sha256(gzip.decompress(Path(str(dst) + ".gz").read_bytes())).hexdigest() != h:
                bad.append(rel)
        else:
            shutil.copy2(src, dst)
            if sha256(dst) != h:
                bad.append(rel)
    ver = {"files": len(orig), "csv_gzipped": sum(r.endswith(".csv") for r in orig),
           "content_sha256_mismatches": bad, "ok": not bad}
    print(json.dumps({k: v for k, v in ver.items()}, indent=1))
    if bad:
        sys.exit("verification failed; parent untouched")
    write_manifest(child, {
        "source": "codenet_metadata", "class": "as_received", "complete": True,
        "upstream": pm["upstream"], "retrieved_at": pm["retrieved_at"],
        "method": pm["method"] + "; then each CSV gzip-compressed (level 9, mtime 0). Decompressed bytes are "
                  "exactly the extracted files — see content_sha256 (the parent manifest's per-file sha256)",
        "subset_of": pinfo,
        "content_sha256": orig,
        "notes": pm.get("notes", []), "verification": ver,
    })
    lock(child)


# ---------------------------------------------------------------------------------------- AtCoder dump
def atcoder_dump(frac=0.05):
    parent = RAW / "atcoder_kenkoooo_dump/2025-11-21"
    pm, pinfo = parent_info(parent)
    child = new_snapshot(parent, f"_u{int(frac * 100):02d}")
    kept = total = 0
    # Lines are copied byte for byte; csv is used only to read the user_id field of each line.
    with gzip.open(parent / "submissions.csv.gz", "rb") as fin, \
            open(child / "submissions.csv.gz", "wb") as raw_out, \
            gzip.GzipFile(fileobj=raw_out, mode="wb", compresslevel=6, mtime=0) as fout:
        header = fin.readline()
        fout.write(header)
        uid_col = header.decode().rstrip("\r\n").split(",").index("user_id")
        for line in fin:
            total += 1
            uid = next(csv.reader([line.decode()]))[uid_col]
            if in_sample(uid, frac):
                fout.write(line)
                kept += 1
    c = duckdb.connect()
    c.execute("set memory_limit='3GB'; set preserve_insertion_order=false")
    full = f"(select * from read_csv('{parent}/submissions.csv.gz', header=true, all_varchar=true) where {sample_sql('user_id', frac)})"
    sub = f"read_csv('{child}/submissions.csv.gz', header=true, all_varchar=true)"
    one = lambda q: c.execute(q).fetchone()[0]
    ver = {"parent_rows": total, "rows_kept": kept,
           "rows_expected_by_sql": one(f"select count(*) from {full}"),
           "rows_in_subset_not_in_parent": one(f"select count(*) from (select * from {sub} except all select * from {full})"),
           "users": one(f"select count(distinct user_id) from {sub}")}
    ver["ok"] = ver["rows_kept"] == ver["rows_expected_by_sql"] and ver["rows_in_subset_not_in_parent"] == 0
    print(json.dumps(ver, indent=1))
    if not ver["ok"]:
        sys.exit("verification failed; parent untouched")
    write_manifest(child, {
        "source": "atcoder_kenkoooo_dump", "class": "as_received_subset", "complete": True,
        "upstream": pm["upstream"], "retrieved_at": pm["retrieved_at"],
        "method": "parent CSV streamed; the header and every line of a sampled user copied byte for byte; re-gzipped (mtime 0)",
        "subset_of": pinfo, "scope": {"rule": RULE_TEXT, "column": "user_id", "frac": frac, "threshold_hex": threshold(frac)},
        "notes": pm.get("notes", []), "verification": ver,
    })
    lock(child)


# ---------------------------------------------------------------------------------------- AICD
def aicd():
    parent = RAW / "aicd_bench/b7dc6d8"
    pm, pinfo = parent_info(parent)
    child = new_snapshot(parent, "_trainval")
    (child / "T1").mkdir()
    keep = [f for f in pm["files"] if f["path"].split("/")[-1].startswith(("train-", "validation-"))]
    for f in keep:
        shutil.copy2(parent / f["path"], child / f["path"])
    bad = [f["path"] for f in keep if sha256(child / f["path"]) != f["sha256"]]
    ver = {"files_kept": [f["path"] for f in keep], "sha256_mismatches": bad, "ok": not bad}
    print(json.dumps(ver, indent=1))
    if bad:
        sys.exit("verification failed; parent untouched")
    write_manifest(child, {
        "source": "aicd_bench", "class": "as_received_subset", "complete": True,
        "upstream": pm["upstream"], "retrieved_at": pm["retrieved_at"], "method": pm["method"],
        "subset_of": pinfo, "scope": {"rule": "T1 train and validation files only; test-0000{0..3} dropped (re-downloadable at the pinned revision)"},
        "notes": pm.get("notes", []), "verification": ver,
    })
    lock(child)


# ---------------------------------------------------------------------------------------- Codeforces rated list
KEEP_FIELDS = ("handle", "rating", "maxRating", "rank", "maxRank", "registrationTimeSeconds", "lastOnlineTimeSeconds")


def cf_rated_list(path, retrieved_at):
    body = Path(path).read_bytes()
    d = json.loads(body)
    assert d["status"] == "OK"
    snap = RAW / "codeforces_api_rated_list" / f"acq-{retrieved_at[:10]}"
    unlock(RAW)
    snap.mkdir(parents=True, exist_ok=False)
    all_fields = sorted({k for u in d["result"] for k in u})
    proj = {"status": d["status"], "result": [{k: u[k] for k in KEEP_FIELDS if k in u} for u in d["result"]]}
    (snap / "user.ratedList.projected.json.gz").write_bytes(
        gzip.compress(json.dumps(proj, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    write_manifest(snap, {
        "source": "codeforces_api_rated_list", "class": "as_received_projection", "complete": True,
        "upstream": {"name": "Codeforces API user.ratedList", "url": "https://codeforces.com/api/user.ratedList?activeOnly=false&includeRetired=true",
                     "revision": "live API", "license": "Codeforces terms; API use permitted"},
        "method": "one HTTP GET; response parsed and every user object reduced to the kept fields; values unchanged",
        "retrieved_at": retrieved_at,
        "projection": {"kept_fields": list(KEEP_FIELDS), "dropped_fields": [f for f in all_fields if f not in KEEP_FIELDS],
                       "reason": "personal data (names, city, country, organisation, photos) is not needed by any lane",
                       "original_response_sha256": hashlib.sha256(body).hexdigest(), "original_response_bytes": len(body),
                       "n_users": len(d["result"])},
        "notes": ["Contains Codeforces handles in clear (as served). Canonical stores salted hashes only."],
    })
    lock(snap)


# ---------------------------------------------------------------------------------------- open-r1
def openr1(user_status_snapshot="acq-2026-10-07"):
    """Keep the open-r1 main-split rows whose submission_id belongs to a sampled user."""
    parent = RAW / "openr1_cf_submissions/07e7bf3"
    us = RAW / "codeforces_api_user_status" / user_status_snapshot
    pm, pinfo = parent_info(parent)
    child = new_snapshot(parent, "_cfu")
    (child / "data").mkdir()
    c = duckdb.connect()
    c.execute("set memory_limit='3GB'; set preserve_insertion_order=false")
    # submission ids of every sampled user, straight from the raw user.status bodies
    ids = set()
    for f in sorted(us.glob("user_status_*.json.gz")):  # parsed with the stdlib: DuckDB's JSON reader needs huge buffers
        ids.update(str(x["id"]) for x in json.loads(gzip.decompress(f.read_bytes())).get("result", []))
    c.execute("create table ids as select unnest(?::varchar[]) submission_id", [sorted(ids)])
    src = f"read_parquet('{parent}/data/*.parquet')"
    want = f"(select * from {src} where submission_id in (select submission_id from ids))"
    c.execute(f"""copy {want} to '{child}/data/' (format parquet, compression zstd,
                  per_thread_output true, filename_pattern 'part-{{i}}')""")
    one = lambda q: c.execute(q).fetchone()[0]
    sub = f"read_parquet('{child}/data/*.parquet')"
    ver = {"sampled_user_submission_ids": one("select count(*) from ids"),
           "rows_expected": one(f"select count(*) from {want}"), "rows_written": one(f"select count(*) from {sub}"),
           "rows_in_subset_not_in_parent": one(f"select count(*) from (select * from {sub} except all select * from {want})"),
           "rows_in_parent_sample_not_in_subset": one(f"select count(*) from (select * from {want} except all select * from {sub})")}
    ver["ok"] = ver["rows_expected"] == ver["rows_written"] and ver["rows_in_subset_not_in_parent"] == 0 \
        and ver["rows_in_parent_sample_not_in_subset"] == 0
    print(json.dumps(ver, indent=1))
    if not ver["ok"]:
        sys.exit("verification failed; parent untouched")
    write_manifest(child, {
        "source": "openr1_cf_submissions", "class": "as_received_subset", "complete": True,
        "upstream": pm["upstream"], "retrieved_at": pm["retrieved_at"],
        "method": "rows of the parent's main split (data/*.parquet) selected by submission_id and rewritten as parquet; "
                  "every column and value unchanged. selected_accepted / selected_incorrect not kept (curated, code rewritten)",
        "subset_of": pinfo,
        "scope": {"rule": f"submission_id appears in a user.status body in raw/codeforces_api_user_status/{user_status_snapshot}",
                  "user_sample": RULE_TEXT},
        "notes": pm.get("notes", []), "verification": ver,
    })
    lock(child)


# ---------------------------------------------------------------------------------------- retire
def retire(rel):
    parent = RAW / rel
    pm, pinfo = parent_info(parent)
    children = [p for p in parent.parent.iterdir() if p != parent and (p / "MANIFEST.json").exists()]
    ok = [p for p in children
          if (m := json.loads((p / "MANIFEST.json").read_text())).get("subset_of", {}).get("manifest_sha256") == pinfo["manifest_sha256"]
          and m.get("verification", {}).get("ok")]
    if not ok:
        sys.exit(f"no verified child names {rel} as its parent — refusing to delete")
    dest = RAW / "_retired_manifests" / f"{rel.replace('/', '__')}.json"
    unlock(RAW, parent.parent)
    dest.write_text(json.dumps({**pm, "retired": {"replaced_by": [f"{p.parent.name}/{p.name}" for p in ok]}}, indent=1) + "\n")
    unlock(parent)
    shutil.rmtree(parent)
    lock(parent.parent)
    print(f"retired {rel} -> replaced by {[p.name for p in ok]}; manifest kept at {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"codenet_code": codenet_code, "codenet_meta": codenet_meta, "atcoder_dump": atcoder_dump, "aicd": aicd,
     "cf_rated_list": cf_rated_list, "openr1": openr1, "retire": retire}[cmd](*args)
    subprocess.run(["chmod", "-R", "a-w", str(RAW)])
