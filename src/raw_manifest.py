"""Write MANIFEST.json for one raw snapshot: where it came from, how, and a sha256 per file.

A raw snapshot is data/raw/<source>/<snapshot>/, where <snapshot> is the upstream revision
when one exists, otherwise acq-<YYYY-MM-DD> (the date it was acquired). The manifest is the
only file in a snapshot that we write; everything else is exactly as received.

usage:
  python src/raw_manifest.py data/raw/<source>/<snapshot> --spec spec.json
spec.json holds the descriptive fields (source, class, upstream, method, retrieved_at, notes);
this tool adds the file inventory and refuses to overwrite an existing manifest.
"""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = ("source", "class", "upstream", "method", "retrieved_at")
CLASSES = ("as_received", "as_received_subset", "as_received_projection", "reshaped_legacy")


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def inventory(root):
    files = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.name not in ("MANIFEST.json", ".DS_Store"))
    return [{"path": str(p.relative_to(root)), "bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in files]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--spec", type=Path, required=True)
    a = ap.parse_args()

    spec = json.loads(a.spec.read_text())
    missing = [k for k in REQUIRED if k not in spec]
    if missing or spec["class"] not in CLASSES:
        sys.exit(f"spec invalid: missing={missing} class={spec.get('class')!r}")
    out = a.snapshot / "MANIFEST.json"
    if out.exists():
        # The one exception: a snapshot declared incomplete may grow, but files already
        # recorded must be byte-identical — a snapshot is only ever appended to.
        old = json.loads(out.read_text())
        if old.get("complete", True):
            sys.exit(f"{out} exists; manifests are written once")
        now = {f["path"]: f["sha256"] for f in inventory(a.snapshot)}
        changed = [f["path"] for f in old["files"] if now.get(f["path"]) != f["sha256"]]
        if changed:
            sys.exit(f"recorded files changed or vanished: {changed[:5]}")
        out.unlink()

    files = inventory(a.snapshot)
    manifest = {
        "snapshot": f"{a.snapshot.parent.name}/{a.snapshot.name}",
        **spec,
        "manifest_written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_files": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
    }
    out.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    print(f"{out}: {len(files)} files, {manifest['total_bytes'] / 1e9:.3f} GB")


if __name__ == "__main__":
    main()
