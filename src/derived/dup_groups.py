"""D9 dup_groups — labels for identical and near-identical code across users. Labels only: nothing is dropped.

  exact   code_sha256 submitted by >= 2 different users (byte-identical files).
  near    per problem, MinHash (64 permutations) over 5-gram shingles of the D3 token-type stream — identifiers
          are already abstracted to ID, so renamed copies still match. LSH with 8 bands x 8 rows proposes pairs
          (S-curve midpoint ~0.77); a pair is kept if its exact shingle Jaccard >= 0.8. Clusters = connected
          components over kept pairs, per problem.
Most near clusters on easy problems are convergent solutions, not copying — that is the false-positive case
lane S must discount with cohort normalisation; D9 only says which files look alike.

Rows (one per code-bearing accepted submission): submission_uid, user_uid, problem_uid, code_sha256,
exact_n_users, near_cluster_id, near_cluster_n_users, near_cluster_n_files.
usage: python src/derived/dup_groups.py
"""
import sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

K, PERMS, BANDS, ROWS, MIN_J = 5, 64, 8, 8, 0.8
PARAMS = {"shingle": f"{K}-grams of D3 token types", "minhash_perms": PERMS, "lsh": f"{BANDS} bands x {ROWS} rows",
          "min_jaccard": MIN_J, "scope": "within a problem, accepted submissions with code", "seed": 20261008,
          "shingle_hash": "Python hash() with PYTHONHASHSEED=0", "chunks": 32}
_rng = np.random.default_rng(PARAMS["seed"])
_P = np.uint64((1 << 61) - 1)
_A = _rng.integers(1, (1 << 61) - 1, PERMS, dtype=np.uint64)
_B = _rng.integers(0, (1 << 61) - 1, PERMS, dtype=np.uint64)


def shingles(types):
    t = types.split()
    return {hash(" ".join(t[i:i + K])) & 0x1FFFFFFFFFFFFFFF for i in range(max(1, len(t) - K + 1))}


def minhash(item):
    sha, types = item
    s = np.fromiter(shingles(types), dtype=np.uint64)
    # (a*h + b) wraps modulo 2^64, then is reduced mod p: not a strict universal family, adequate for MinHash
    sig = ((np.outer(_A, s) + _B[:, None]) % _P).min(axis=1)
    return sha, sig


def jaccard(a, b):
    sa, sb = shingles(a), shingles(b)
    return len(sa & sb) / len(sa | sb) if sa or sb else 1.0


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


if __name__ == "__main__":
    import os
    os.environ["PYTHONHASHSEED"] = "0"  # shingle hashing must be stable across worker processes
    if sys.flags.hash_randomization:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    with Build("dup_groups", 1, PARAMS, __file__, derived_inputs=["code_views"]) as b:
        c = b.c
        c.execute("""create table subs as select s.submission_uid, s.user_uid, s.problem_uid, s.code_sha256, s.corpus
                     from submissions s semi join code_views v using (code_sha256)
                     where s.accepted and s.user_uid is not null""")
        c.execute("""create table exact as select code_sha256, count(distinct user_uid) exact_n_users from subs group by 1""")
        per_problem = {}
        for pid, sha in c.execute("select distinct problem_uid, code_sha256 from subs order by 1, 2").fetchall():
            per_problem.setdefault(pid, []).append(sha)
        pids = sorted(per_problem)
        dsu, n_cand, n_kept = DSU(), 0, 0
        with Pool(7) as pool:
            for ci in range(PARAMS["chunks"]):  # one chunk of problems in memory at a time
                chunk = pids[ci::PARAMS["chunks"]]
                c.execute("create or replace temp table want as select unnest(?::varchar[]) code_sha256",
                          [sorted({h for pid in chunk for h in per_problem[pid]})])
                types = dict(c.execute("select v.code_sha256, v.types from code_views v semi join want using (code_sha256)").fetchall())
                sigs = dict(pool.map(minhash, types.items(), chunksize=500))
                for pid in chunk:
                    shas = per_problem[pid]
                    if len(shas) < 2:
                        continue
                    buckets = {}
                    for h in shas:
                        sig = sigs[h]
                        for band in range(BANDS):
                            buckets.setdefault((band, sig[band * ROWS:(band + 1) * ROWS].tobytes()), []).append(h)
                    seen = set()
                    for members in buckets.values():
                        for i in range(len(members)):
                            for j in range(i + 1, len(members)):
                                pair = (members[i], members[j]) if members[i] < members[j] else (members[j], members[i])
                                if pair in seen:
                                    continue
                                seen.add(pair)
                                n_cand += 1
                                if jaccard(types[pair[0]], types[pair[1]]) >= MIN_J:
                                    n_kept += 1
                                    dsu.union((pid, pair[0]), (pid, pair[1]))
                del types, sigs
                print(f"  chunk {ci + 1}/{PARAMS['chunks']}: candidates {n_cand}, kept {n_kept}", flush=True)
        rows = [(pid, h, dsu.find((pid, h))[1]) for pid, shas in per_problem.items() for h in shas]
        t = pa.table(list(zip(*rows)), names=["problem_uid", "code_sha256", "root"])
        c.register("t_arrow", t)
        c.execute("create table near as select problem_uid, code_sha256, problem_uid || '#' || root near_cluster_id from t_arrow")
        c.execute("""create table dup_groups as select s.submission_uid, s.user_uid, s.problem_uid, s.code_sha256, s.corpus,
                       e.exact_n_users, n.near_cluster_id,
                       count(distinct s.user_uid) over (partition by n.near_cluster_id) near_cluster_n_users,
                       count(distinct s.code_sha256) over (partition by n.near_cluster_id) near_cluster_n_files
                     from subs s join exact e using (code_sha256) join near n using (problem_uid, code_sha256)""")
        b.write("dup_groups", order="problem_uid, near_cluster_id, submission_uid")
        one = b.one
        b.check("one row per accepted code submission", one("select count(*) from dup_groups") == one("select count(*) from subs"))
        b.check("byte-identical files share a near cluster", one("""select count(*) from (select problem_uid, code_sha256
                 from dup_groups group by 1, 2 having count(distinct near_cluster_id) > 1)""") == 0)
        b.count("candidate_pairs", n_cand)
        b.count("kept_pairs_jaccard_ge_0.8", n_kept)
        b.count("submissions_in_exact_group_with_another_user", one("select count(*) from dup_groups where exact_n_users > 1"))
        b.count("submissions_in_near_cluster_with_another_user", one("select count(*) from dup_groups where near_cluster_n_users > 1"))
        b.count("largest_near_cluster_users", one("select max(near_cluster_n_users) from dup_groups"))
