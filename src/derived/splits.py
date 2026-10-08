"""D1 splits — a fixed train/val/test assignment per user.

A user's split is a pure function of their user_uid: u = sha256("split-v1:" + user_uid)[:8] / 16^8,
train if u < 0.70, val if u < 0.85, else test. User-disjoint by construction, so nobody's work is
seen in both training and testing; stable across rebuilds, so adding a corpus never moves anyone.
The "split-v1:" label keeps it independent of the raw sampling rule.

Team submissions (Codeforces, no single user) belong to no split.
usage: python src/derived/splits.py
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

PARAMS = {"rule": "u = int(sha256('split-v1:' + user_uid)[:8], 16) / 16**8", "train": 0.70, "val": 0.15, "test": 0.15}

with Build("splits", 1, PARAMS, __file__) as b:
    b.c.execute("""create table u as select user_uid, corpus,
                     ('0x' || left(sha256('split-v1:' || user_uid), 8))::ubigint / 4294967296.0 as u_hash,
                     count(*) n_submissions, sum(accepted::int) n_accepted,
                     count(*) filter (where code_status = 'present') n_with_code
                   from submissions where user_uid is not null group by 1, 2""")
    b.c.execute(f"""create table splits as select user_uid, corpus,
                      case when u_hash < {PARAMS['train']} then 'train'
                           when u_hash < {PARAMS['train'] + PARAMS['val']} then 'val' else 'test' end split,
                      u_hash, n_submissions, n_accepted, n_with_code from u""")
    b.write("splits", order="user_uid")

    b.check("one row per user", b.one("select count(*) - count(distinct user_uid) from splits") == 0)
    b.check("every submitting user has a split", b.one("""select count(*) from (select distinct user_uid from submissions
                                                           where user_uid is not null) anti join splits using (user_uid)""") == 0)
    shares = b.c.execute("select corpus, split, n, round(n / sum(n) over (partition by corpus), 4) as frac from (select corpus, split, count(*) n "
                         "from splits group by all) order by 1, 2").fetchall()
    for corpus, split, n, share in shares:
        b.check(f"{corpus} {split} share within 2 points", abs(share - PARAMS[split]) < 0.02)
    b.count("users_by_split", {f"{c}/{s}": n for c, s, n, _ in shares})
    b.count("users_with_code_by_split", dict((f"{c}/{s}", n) for c, s, n in b.c.execute(
        "select corpus, split, count(*) from splits where n_with_code > 0 group by all order by 1, 2").fetchall()))
