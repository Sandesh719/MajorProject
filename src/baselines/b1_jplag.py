"""B1 — rp4's similarity baseline: JPlag (v6.2.0), as a course would run it, scored on the eval frame.

Score of a row = the highest JPlag similarity (averageSimilarity) between the row's code and any reference file:
every OTHER user's accepted C++ code for the same problem. Two variants, one JPlag run:
  b1_jplag_in_corpus   references include the donor's original     — "the source is in the corpus" (what MOSS/JPlag assume)
  b1_jplag_no_source   every reference byte-identical to the scored code is removed, for every row alike (for a
                       swap: the donor's original and any exact copy of it) — "the source is not in the corpus"
                       (ghostwriter, private repo, AI): the case the project brief says similarity cannot see
References a row's own user submitted are never counted (self-matches). Input is D3 `expanded` code (macros
expanded — constraint 6). JPlag drops files under its 12-token minimum match: those rows score NaN (coverage).
References per problem: capped at REF_CAP by hash, with every donor original kept. Files over MAX_BYTES (expanded) are
excluded: 335 of 1.7M files (0.02%; median 602 bytes, max 9.4 MB) — JPlag's cost grows quadratically with length and a
single 4.7 MB file held one run for 39 min. Rows over the limit score NaN; such references are skipped. Applied at
assembly too, so pairs cached before the limit follow the same rule (each pair's similarity depends only on its two files). Small problems are packed into
one JPlag run (JVM start-up dominates them); only same-problem pairs are kept, so results equal per-problem runs.
--cluster-skip: JPlag's optional spectral clustering of results can fail to converge on large runs, and then the JVM
hangs on its progress spinner without exiting; clusters are not used here, only pairwise similarities.

Work is cached per problem in runs/_work/b1_jplag/<split>/ so an interrupted run resumes.
usage: python src/baselines/b1_jplag.py val      |   python src/baselines/b1_jplag.py test --final
"""
import argparse, csv, json, os, shutil, subprocess, sys, tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.canonical.db import connect  # noqa: E402
from src.derived.common import register  # noqa: E402
from src.eval.harness import load_frame, record_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "backup/2026-10-06-v1/tools/jplag.jar"
REF_CAP, WORKERS, BATCH_FILES, MAX_BYTES = 300, 4, 1500, 100_000
PARAMS = {"tool": "JPlag 6.2.0", "args": "-l cpp -M RUN --normalize --cluster-skip --csv-export -n -1 -old <refs>", "similarity": "averageSimilarity",
          "score": "max over references", "references": "other users' accepted C++ code on the same problem",
          "ref_cap": REF_CAP, "ref_choice": "every donor original + lowest sha256 up to the cap", "input": "D3 expanded",
          "max_file_bytes": MAX_BYTES}


def safe(row_id):
    return row_id.replace(":", "_").replace("/", "_")


def run_batch(task):
    """Several problems in one JPlag run (JVM start-up dominates tiny problems). Directory names carry the problem
    index, and only same-problem (new, old) pairs are kept — each file is parsed once, exactly as in a per-problem run."""
    probs, out_dir = task  # probs: [(pid, rows[(row_id, code)], refs[(sha, code)])]
    work = Path(tempfile.mkdtemp(prefix="b1_"))
    try:
        (work / "new").mkdir()
        (work / "old").mkdir()
        for i, (pid, rows, refs) in enumerate(probs):
            for rid, code in rows:
                d = work / "new" / f"{i}__{safe(rid)}"
                d.mkdir()
                (d / "main.cpp").write_text(code)
            for sha, code in refs:
                d = work / "old" / f"{i}__{sha[:16]}"
                d.mkdir()
                (d / "main.cpp").write_text(code)
        r = subprocess.run(["java", "-Xmx1g", "-XX:ActiveProcessorCount=2", "-jar", str(JAR), "-l", "cpp", "-M", "RUN",
                            "--normalize", "--cluster-skip", "--csv-export", "-n", "-1", "-old", str(work / "old"), "-r", str(work / "out"),
                            str(work / "new")], capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
        res = work / "out" / "results.csv"
        pairs = {i: [] for i in range(len(probs))}
        if res.exists():
            with open(res) as fh:
                for a_, b_, avg, mx in csv.reader(fh):
                    if a_.startswith("old/") and b_.startswith("new/"):
                        a_, b_ = b_, a_
                    if not (a_.startswith("new/") and b_.startswith("old/")):
                        continue
                    ia, ra = a_[4:].split("__", 1)
                    ib, rb = b_[4:].split("__", 1)
                    if ia == ib:
                        pairs[int(ia)].append((ra, rb, float(avg)))
        if r.returncode != 0:  # never cache a failed run: its problems would silently become "no similarity"
            return [p for p, _, _ in probs], r.returncode, (r.stderr or r.stdout)[-300:]
        for i, (pid, _, _) in enumerate(probs):
            tmp = out_dir / f"{safe(pid)}.parquet.tmp"
            pd.DataFrame(pairs[i], columns=["row", "ref16", "sim"]).to_parquet(tmp)
            tmp.rename(out_dir / f"{safe(pid)}.parquet")
        return [], 0, ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("split", choices=["val", "test"])
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--limit-problems", type=int, default=0, help="smoke run on the first N problems (writes no run)")
    ap.add_argument("--assemble-only", action="store_true",
                    help="record from the cached problems only; rows of unprocessed problems score NaN and are listed")
    a = ap.parse_args()
    frame = load_frame(a.split)
    c = connect()
    register(c, "code_views")
    work = ROOT / "runs/_work/b1_jplag" / a.split
    work.mkdir(parents=True, exist_ok=True)
    c.register("frame_rows", frame[["row_id", "user_uid", "problem_uid", "code_sha256", "kind"]])
    # references: other users' accepted C++ code per problem, with who submitted each distinct file
    c.execute(f"""create table small as select code_sha256 from code_views where octet_length(encode(expanded)) <= {MAX_BYTES}""")
    c.execute("""create table refs as select s.problem_uid, s.code_sha256, list(distinct s.user_uid) users
                 from submissions s semi join small using (code_sha256)
                 where s.accepted and s.lang_family = 'C++' and s.user_uid is not null
                   and s.problem_uid in (select distinct problem_uid from frame_rows) group by 1, 2""")
    c.execute("""create table donors as select distinct problem_uid, code_sha256 from frame_rows where kind = 'p1_pos'""")
    c.execute(f"""create table refs_capped as select * from (
                    select r.*, row_number() over (partition by r.problem_uid order by (d.code_sha256 is null), r.code_sha256) rk
                    from refs r left join donors d using (problem_uid, code_sha256)) where rk <= {REF_CAP}""")
    problems = sorted(frame.problem_uid.unique())
    if a.limit_problems:
        problems = problems[:a.limit_problems]
    todo = [p for p in problems if not (work / f"{safe(p)}.parquet").exists()]
    unprocessed = sorted(todo) if a.assemble_only else []
    if a.assemble_only:
        todo = []
    print(f"{a.split}: {len(problems)} problems, {len(todo)} to run", flush=True)
    views = lambda shas: dict(c.execute("select code_sha256, expanded from code_views where code_sha256 in (select unnest(?::varchar[]))",
                                        [list(shas)]).fetchall())
    small_set = {h for (h,) in c.execute("select code_sha256 from small").fetchall()}
    failures = json.loads((work / "failures.json").read_text()) if (work / "failures.json").exists() else {}
    with ProcessPoolExecutor(WORKERS) as pool:
        done = 0
        for i in range(0, len(todo), 400):  # fetch code for 400 problems at a time
            chunk = todo[i:i + 400]
            fr = frame[frame.problem_uid.isin(chunk)]
            fr = fr[fr.code_sha256.isin(small_set)]
            rf = c.execute("select problem_uid, code_sha256 from refs_capped where problem_uid in (select unnest(?::varchar[]))", [chunk]).fetchall()
            code = views(set(fr.code_sha256) | {h for _, h in rf})
            refs_by = {}
            for p, h in rf:
                refs_by.setdefault(p, []).append((h, code[h]))
            probs = [(p, [(r.row_id, code[r.code_sha256]) for r in g.itertuples()], refs_by.get(p, []))
                     for p, g in fr.groupby("problem_uid")]
            batches, cur, size = [], [], 0  # pack problems into JVM runs of ~BATCH_FILES files
            for pr in sorted(probs, key=lambda x: len(x[2])):
                cur.append(pr)
                size += len(pr[1]) + len(pr[2])
                if size >= BATCH_FILES:
                    batches.append(cur)
                    cur, size = [], 0
            if cur:
                batches.append(cur)
            failed = []
            for f in as_completed([pool.submit(run_batch, (b, work)) for b in batches]):
                bad, rc, err = f.result()
                if bad:
                    print(f"  JPlag rc={rc} on a batch of {len(bad)} — retrying one problem at a time: {err!r}", flush=True)
                    failed += bad
            by_pid = {pr[0]: pr for pr in probs}
            for f in as_completed([pool.submit(run_batch, ([by_pid[p]], work)) for p in failed]):
                bad, rc, err = f.result()
                for p in bad:
                    failures[p] = f"rc={rc}: {err}"
                    print(f"  JPlag still fails on {p} alone (rc={rc})", flush=True)
            done += len(chunk)
            print(f"  {done}/{len(todo)} problems", flush=True)
    (work / "failures.json").write_text(json.dumps(failures, indent=1) + "\n")
    if a.limit_problems:
        print("smoke run: nothing recorded")
        return
    # assemble: per row, best similarity to an allowed reference
    pairs = pd.concat([pd.read_parquet(p) for p in work.glob("*.parquet")], ignore_index=True)
    refs = c.execute("select problem_uid, left(code_sha256, 16) ref16, code_sha256, users from refs").df()
    fr = frame.assign(row=frame.row_id.map(safe))
    m = pairs.merge(fr[["row", "row_id", "user_uid", "problem_uid", "code_sha256"]], on="row").merge(
        refs, on=["problem_uid", "ref16"], suffixes=("", "_ref"))
    m = m[m.code_sha256.isin(small_set) & m.code_sha256_ref.isin(small_set)]  # the size limit, also for cached pairs
    m = m[[u not in set(us) or len(us) > 1 for u, us in zip(m.user_uid, m.users)]]  # drop self-matches
    best_in = m.groupby("row_id").sim.max()
    best_no = m[m.code_sha256 != m.code_sha256_ref].groupby("row_id").sim.max()
    for name, best, note in (("b1_jplag_in_corpus", best_in, "source of a copy is among the references"),
                             ("b1_jplag_no_source", best_no, "every reference byte-identical to the scored code removed")):
        record_run(name, best.reindex(frame.row_id), a.split, __file__, params={**PARAMS, "variant": note},
                   final=a.final, notes=f"rp4 baseline; {len(failures)} problems JPlag could not process: {sorted(failures)[:20]}; "
                         f"{len(unprocessed)} problems not run (stopped early; their rows score NaN): {unprocessed[:20]}")


if __name__ == "__main__":
    main()
