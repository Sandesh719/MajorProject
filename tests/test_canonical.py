"""Invariants of the canonical layer. Run: .venv/bin/pytest tests/test_canonical.py -q

Every test runs over all built corpora, so a newly built corpus is covered without edits.
"""
import hashlib, json
from pathlib import Path
import pytest
from src.canonical.db import connect, CANON, RAW

VERDICTS = {"AC", "WA", "PE", "RE", "TLE", "MLE", "OLE", "CE", "PARTIAL", "OTHER", "JUDGE_ERROR", "PENDING"}
CODE_STATUS = {"present", "not_sampled", "not_acquired", "missing_in_mirror", "not_in_openr1"}


@pytest.fixture(scope="module")
def c():
    return connect()


def one(c, q):
    return c.execute(q).fetchone()[0]


def corpora(c):
    return [r[0] for r in c.execute("select distinct corpus from submissions").fetchall()]


@pytest.mark.parametrize("table,key", [("submissions", "submission_uid"), ("problems", "problem_uid"),
                                       ("users", "user_uid"), ("code_blobs", "(corpus, code_sha256)")])
def test_primary_keys_unique_and_not_null(c, table, key):
    for k in key.strip("()").split(", "):
        assert one(c, f"select count(*) from {table} where {k} is null") == 0
    assert one(c, f"select count(*) - count(distinct {key}) from {table}") == 0


def test_ids_carry_their_corpus_prefix(c):
    for t, k in (("submissions", "submission_uid"), ("problems", "problem_uid"), ("users", "user_uid")):
        assert one(c, f"select count(*) from {t} where not starts_with({k}, corpus || ':')") == 0


def test_every_submission_has_its_user(c):
    assert one(c, "select count(*) from submissions s anti join users u using (user_uid) where s.user_uid is not null") == 0


def test_every_submission_has_its_problem_except_known_atcoder_gap(c):
    # AtCoder: the dump predates/postdates the problems resource for 1,728 rows (reported in the build record)
    missing = dict(c.execute("""select corpus, count(*) from submissions s anti join problems p using (problem_uid)
                                group by 1""").fetchall())
    assert set(missing) <= {"atcoder"}
    build = json.loads((CANON / "_builds/atcoder.json").read_text())
    assert missing.get("atcoder", 0) == build["counts"]["submissions_whose_problem_not_in_problems"]


def test_null_user_only_for_team_submissions(c):
    cols = {r[0] for r in c.execute("describe submissions").fetchall()}
    if "team_size" in cols:
        assert one(c, "select count(*) from submissions where user_uid is null and coalesce(team_size, 1) = 1") == 0
    else:
        assert one(c, "select count(*) from submissions where user_uid is null") == 0


def test_verdicts_are_normalised(c):
    got = {r[0] for r in c.execute("select distinct verdict from submissions").fetchall()}
    assert None not in got and got <= VERDICTS
    assert one(c, "select count(*) from submissions where accepted <> (verdict = 'AC')") == 0


def test_code_status_consistent_with_hash(c):
    got = {r[0] for r in c.execute("select distinct code_status from submissions").fetchall()}
    assert got <= CODE_STATUS
    assert one(c, "select count(*) from submissions where (code_status = 'present') <> (code_sha256 is not null)") == 0


def test_every_code_hash_has_a_blob(c):
    assert one(c, "select count(*) from submissions s anti join code_blobs b using (code_sha256) where s.code_sha256 is not null") == 0


def test_code_pointers_return_the_hashed_bytes(c):
    rows = c.execute("""select b.code_sha256, k.code from (select code_sha256, corpus from code_blobs
                        order by hash(code_sha256) limit 300) b join code k using (code_sha256)""").fetchall()
    assert len(rows) == 300
    assert all(hashlib.sha256(code.encode()).hexdigest() == h for h, code in rows)


def test_timestamps_present_and_plausible(c):
    assert one(c, "select count(*) from submissions where ts_utc is null") == 0
    assert one(c, "select count(*) from submissions where ts_utc < '2005-01-01' or ts_utc > now()") == 0


def test_difficulty_scale_never_mixed(c):
    assert one(c, "select count(*) from problems where (difficulty is null) <> (difficulty_scale is null)") == 0
    assert one(c, "select count(distinct difficulty_scale) from problems where corpus = 'codenet'") == 0


def test_no_handle_in_canonical(c):
    # hashed ids are exactly 16 hex chars after the prefix; nothing handle-shaped survives
    assert one(c, """select count(*) from users where id_scheme = 'salted_sha256'
                     and not regexp_full_match(user_uid, '[a-z]+:[0-9a-f]{16}')""") == 0


@pytest.mark.parametrize("corpus", ["codenet", "atcoder", "codeforces"])
def test_build_record_matches_current_inputs(corpus):
    rec = CANON / "_builds" / f"{corpus}.json"
    if not rec.exists():
        pytest.skip(f"{corpus} not built yet")
    b = json.loads(rec.read_text())
    assert all(b["checks"].values())
    for snap, h in b["input_manifest_sha256"].items():
        assert h == hashlib.sha256((RAW / snap / "MANIFEST.json").read_bytes()).hexdigest(), f"{snap} changed since build"
    maps = Path(__file__).resolve().parents[1] / "src/canonical/maps"
    for m, h in b["maps"].items():
        assert h == hashlib.sha256((maps / m).read_bytes()).hexdigest(), f"{m} changed since the {corpus} build"
    salt = Path(__file__).resolve().parents[1] / ".secrets/user_salt"
    if "user_salt_sha256" in b:
        assert b["user_salt_sha256"] == hashlib.sha256(salt.read_text().strip().encode()).hexdigest()
