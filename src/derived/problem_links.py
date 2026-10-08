"""D6 problem_links — CodeNet's AtCoder problems mapped to their real AtCoder problem ids.

CodeNet anonymised problem ids, but kept titles of the form "<contest name> - <problem name>". Matching
those to AtCoder's own contest and problem names lets D5's derived difficulty be validated against
AtCoder's published IRT difficulty on the same problems. Tiers, first match wins, recorded as `method`:
  exact         contest name and problem name both equal (normalised: lowercase, single spaces)
  title_unique  problem name equals exactly one AtCoder problem's name
  fuzzy         within the exactly-matched contest, the most similar problem name, if similarity >= 0.85
  ambiguous_title   two CodeNet problems share the same full title (e.g. AGC028 F and F2) — left unlinked
A CodeNet title is split at every " - " so a contest or problem name may itself contain " - ".
One AtCoder task can be linked from several CodeNet problems: AtCoder reuses a task across parallel
contests (ABC + ARC), and CodeNet lists it once per contest — those CodeNet problems are the same problem.

usage: python src/derived/problem_links.py
"""
import difflib, re, sys
from pathlib import Path
import pyarrow as pa
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.derived.common import Build  # noqa: E402

PARAMS = {"normalise": "lowercase, collapse whitespace, strip", "fuzzy_min_ratio": 0.85,
          "tiers": ["exact", "title_unique", "fuzzy"], "ambiguous_title": "unlinked"}


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


with Build("problem_links", 1, PARAMS, __file__) as b:
    c = b.c
    cn = c.execute("select problem_uid, title from problems where corpus='codenet' and judge='AtCoder'").fetchall()
    at = c.execute("""select p.problem_uid, p.title, k.name from problems p
                      join contest_problems cp using (problem_uid) join contests k on k.contest_uid = cp.contest_uid
                      where p.corpus = 'atcoder'""").fetchall()
    by_pair, by_contest, by_title = {}, {}, {}
    for uid, title, contest in at:
        by_pair.setdefault((norm(contest), norm(title)), set()).add(uid)
        by_contest.setdefault(norm(contest), {})[norm(title)] = uid
    for uid, title in c.execute("select problem_uid, title from problems where corpus='atcoder'").fetchall():
        by_title.setdefault(norm(title), set()).add(uid)

    title_count = {}
    for _, title in cn:
        title_count[norm(title)] = title_count.get(norm(title), 0) + 1
    links = []
    for cuid, title in cn:
        if title_count[norm(title)] > 1:  # two CodeNet problems with the same full title cannot be told apart
            links.append((cuid, title, None, "ambiguous_title", None, None))
            continue
        parts = (title or "").split(" - ")
        splits = [(" - ".join(parts[:i]), " - ".join(parts[i:])) for i in range(1, len(parts))]
        hit = None
        for con, prob in splits:  # exact
            u = by_pair.get((norm(con), norm(prob)))
            if u and len(u) == 1:
                hit = (next(iter(u)), "exact", 1.0, con)
                break
        if not hit:
            for con, prob in splits:  # title_unique
                u = by_title.get(norm(prob))
                if u and len(u) == 1:
                    hit = (next(iter(u)), "title_unique", 1.0, con)
                    break
        if not hit:
            for con, prob in splits:  # fuzzy within the contest
                cands = by_contest.get(norm(con))
                if cands:
                    best = max(cands, key=lambda t: difflib.SequenceMatcher(None, norm(prob), t).ratio())
                    r = difflib.SequenceMatcher(None, norm(prob), best).ratio()
                    if r >= PARAMS["fuzzy_min_ratio"]:
                        hit = (cands[best], "fuzzy", round(r, 3), con)
                        break
        links.append((cuid, title, *(hit or (None, "unmatched", None, None))))
    t = pa.table(list(zip(*links)), names=["codenet_problem_uid", "codenet_title", "atcoder_problem_uid", "method",
                                           "similarity", "matched_contest_name"])
    c.register("t_arrow", t)
    c.execute("create table problem_links as select *, 'codenet' corpus from t_arrow")
    b.write("problem_links", order="codenet_problem_uid")

    one = b.one
    b.check("one row per CodeNet AtCoder problem", one("select count(*) from problem_links") == len(cn))
    # AtCoder shares one task between parallel contests (an ABC and an ARC held together); CodeNet lists it once per
    # contest. So one AtCoder problem may be claimed by several CodeNet ids — but only from different contests.
    b.check("an AtCoder problem is shared only across different contests", one("""select count(*) from (
             select atcoder_problem_uid from problem_links where atcoder_problem_uid is not null
             group by 1 having count(*) > count(distinct lower(matched_contest_name)))""") == 0)
    b.count("by_method", dict(c.execute("select method, count(*) from problem_links group by 1 order by 2 desc").fetchall()))
    b.count("linked_with_irt_difficulty", one("""select count(*) from problem_links l join problems p
             on p.problem_uid = l.atcoder_problem_uid where p.difficulty is not null"""))
    b.count("atcoder_problems_claimed_twice_any_method", one("""select count(*) from (select atcoder_problem_uid from problem_links
             where atcoder_problem_uid is not null group by 1 having count(*) > 1)"""))
