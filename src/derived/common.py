"""Shared machinery for derived datasets: data/derived/<name>/v<N>/ with a recipe.json.

    with Build("splits", 1, params={...}, inputs=["canonical"], script=__file__) as b:
        b.c.execute("create table out as ...")
        b.write("out", order="user_uid", partition_by_corpus=True)
        b.check("each user in one split", <bool>)
        b.count("users", <int>)

Rules enforced here:
  - a derived dataset reads canonical (via src.canonical.db) or other derived datasets — never raw;
  - output is staged in data/derived/_staging/, and swapped in only if every check passed;
  - recipe.json records the sha256 of each canonical build record and derived recipe it read, the
    script's sha256, every parameter, and is_synthetic. A version directory, once written, is never
    rewritten with different parameters — change a choice, bump the version.
"""
import hashlib, json, os, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.canonical.db import connect, CANON  # noqa: E402

DERIVED = ROOT / "data/derived"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def latest(name):
    vs = sorted((DERIVED / name).glob("v*/recipe.json"), key=lambda p: int(p.parent.name[1:]))
    if not vs:
        raise FileNotFoundError(f"derived dataset {name!r} not built")
    return vs[-1].parent


def register(c, name, version=None, view=None):
    """Expose a derived dataset as a view (default: latest version); returns its directory."""
    d = DERIVED / name / f"v{version}" if version else latest(name)
    files = list(d.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(d)
    hive = any("corpus=" in str(f) for f in files)
    c.execute(f"""create or replace view {view or name} as select * from
                  read_parquet('{d}/**/*.parquet', hive_partitioning={str(hive).lower()}, union_by_name=true)""")
    return d


class Build:
    def __init__(self, name, version, params, script, derived_inputs=(), is_synthetic=False, memory="3GB"):
        # SMOKE=1: build <name>_smoke from the _smoke versions of its inputs where they exist — a fast end-to-end
        # rehearsal of the whole pipeline, including the final write and checks, on a small sample.
        self.smoke = bool(os.environ.get("SMOKE"))
        if self.smoke and not name.endswith("_smoke"):
            name += "_smoke"
            derived_inputs = [d + "_smoke" if (DERIVED / f"{d}_smoke").exists() else d for d in derived_inputs]
        self.name, self.version, self.params = name, version, params
        self.script, self.derived_inputs, self.is_synthetic = Path(script), list(derived_inputs), is_synthetic
        self.final = DERIVED / name / f"v{version}"
        self.stage = DERIVED / "_staging" / f"{name}_v{version}"
        self.checks, self.counts = {}, {}
        self.memory = memory
        self.on_success = []  # callables run after the swap, e.g. removing a resumable cache

    def __enter__(self):
        if self.final.exists():
            old = json.loads((self.final / "recipe.json").read_text())
            if old["params"] != self.params:
                sys.exit(f"{self.final} exists with different params — bump the version instead")
        shutil.rmtree(self.stage, ignore_errors=True)
        self.stage.mkdir(parents=True)
        self.c = connect()
        self.c.execute(f"set memory_limit='{self.memory}'; set temp_directory='{self.stage / '_tmp'}'; "
                       "set preserve_insertion_order=false")
        self.inputs = {"canonical_builds": {p.stem: sha(p) for p in sorted((CANON / "_builds").glob("*.json"))}}
        self.inputs["derived"] = {}
        for dep in self.derived_inputs:
            d = register(self.c, dep, view=dep.removesuffix("_smoke"))
            self.inputs["derived"][f"{dep}/{d.name}"] = sha(d / "recipe.json")
        return self

    def write(self, table, order, partition_by_corpus=True):
        if partition_by_corpus:
            for (corpus,) in self.c.execute(f"select distinct corpus from {table} order by 1").fetchall():
                d = self.stage / f"corpus={corpus}"
                d.mkdir(exist_ok=True)
                self.c.execute(f"""copy (select * exclude (corpus) from {table} where corpus = '{corpus}' order by {order})
                                   to '{d}/data.parquet' (format parquet, compression zstd)""")
        else:
            self.c.execute(f"copy (select * from {table} order by {order}) to '{self.stage}/{table}.parquet' (format parquet, compression zstd)")

    def check(self, label, ok):
        self.checks[label] = bool(ok)

    def count(self, label, value):
        self.counts[label] = value

    def one(self, q):
        return self.c.execute(q).fetchone()[0]

    def __exit__(self, exc_type, exc, tb):
        self.c.close()
        shutil.rmtree(self.stage / "_tmp", ignore_errors=True)
        report = json.dumps({"checks": self.checks, "counts": self.counts}, indent=1, default=str)
        print(report)
        if exc_type or not all(self.checks.values()):
            shutil.rmtree(self.stage, ignore_errors=True)
            if exc_type:
                return False
            sys.exit(f"{self.name} v{self.version}: checks failed — staging discarded")
        recipe = {
            "name": self.name, "version": self.version, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": str(self.script.resolve().relative_to(ROOT)), "script_sha256": sha(self.script),
            "params": self.params, "is_synthetic": self.is_synthetic, "inputs": self.inputs,
            "duckdb": duckdb.__version__, "checks": self.checks, "counts": self.counts,
        }
        (self.stage / "recipe.json").write_text(json.dumps(recipe, indent=1, default=str) + "\n")
        shutil.rmtree(self.final, ignore_errors=True)
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.stage.rename(self.final)
        print(f"written {self.final.relative_to(ROOT)}")
        for fn in self.on_success:
            fn()
        return False
