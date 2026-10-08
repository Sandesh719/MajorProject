"""The one user-sampling rule used when a raw snapshot is trimmed to a subset of users.

A user is in the sample at fraction f when the first 8 hex digits of sha256(user_id) are below
f * 16^8. The rule is neutral (no modelling choice), stable across machines and library
versions, and nested: the 5% sample is contained in the 20% sample. Not salted — it is public.
"""
import hashlib


def threshold(frac):
    return f"{int(frac * 16 ** 8):08x}"


def in_sample(user_id, frac):
    return hashlib.sha256(user_id.encode()).hexdigest()[:8] < threshold(frac)


def sql(col, frac):
    """The same rule as a DuckDB predicate (duckdb's sha256 of a VARCHAR hashes its UTF-8 bytes)."""
    return f"left(sha256({col}), 8) < '{threshold(frac)}'"


RULE_TEXT = "keep every row of user u iff sha256(u)[:8] (hex) < frac * 16^8"
