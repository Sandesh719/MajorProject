"""Metrics for the evaluation harness. One definition, used by every baseline, lane and fusion model.

Conventions (fixed; changing one is a new harness version):
  - a score is a float; HIGHER = MORE SUSPICIOUS. NaN = the method could not score the row: it is never flagged,
    and `coverage` reports how many rows were scored.
  - review budget b: the threshold tau_b is the (1 - b) quantile of scores on NATURAL rows — "flag the top b of
    ordinary submissions", the load a human reviewer can actually carry. A row is flagged iff score > tau_b
    (strict: with ties at tau_b, flagging every tied row would exceed the budget — at most b of natural rows is
    flagged; ties can only make it fewer).
  - recall@b      share of p1_pos rows flagged                        (headline: b = 5%)
    genuine_flag@b share of p1_neg rows flagged — the user's own real code; should sit near b
  - auroc         p1_pos vs natural rows (the realistic population)
    paired_win    share of pairs where the swapped code scores above the user's real code (ties = 0.5) —
                  controls for user, problem and time, so it isolates authorship
  - CIs: user-cluster bootstrap (users resampled with replacement), percentile 95%.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

BUDGETS = (0.01, 0.05)
HEADLINE = 0.05


def _wquantile(x, w, q):
    o = np.argsort(x, kind="stable")
    x, w = x[o], w[o]
    cw = np.cumsum(w)
    return x[np.searchsorted(cw, q * cw[-1], side="left").clip(0, len(x) - 1)]


def _point(df, w):
    """Metrics for one weighted sample. df has kind, score (NaN already mapped below the minimum), pair_id; w = row weights."""
    out = {}
    nat, pos, neg = (df.kind == "natural").to_numpy(), (df.kind == "p1_pos").to_numpy(), (df.kind == "p1_neg").to_numpy()
    s = df.score.to_numpy()
    for b in BUDGETS:
        if w[nat].sum() == 0:
            out[f"recall@{b:.0%}"] = out[f"genuine_flag@{b:.0%}"] = np.nan
            continue
        tau = _wquantile(s[nat], w[nat], 1 - b)
        flagged = s > tau  # strict: tied rows at the threshold are not flagged, so the budget is never exceeded
        out[f"recall@{b:.0%}"] = np.average(flagged[pos], weights=w[pos]) if w[pos].sum() else np.nan
        out[f"genuine_flag@{b:.0%}"] = np.average(flagged[neg], weights=w[neg]) if w[neg].sum() else np.nan
    m = pos | nat
    y = pos[m]
    out["auroc"] = roc_auc_score(y, s[m], sample_weight=w[m]) if 0 < y.sum() < len(y) and w[m].sum() else np.nan
    pairs = df[pos | neg].assign(w=w[pos | neg])
    if len(pairs):
        pv = pairs.pivot_table(index="pair_id", columns="kind", values=["score", "w"], aggfunc="first").dropna()
        if len(pv):
            sp, sn, pw = pv[("score", "p1_pos")].to_numpy(), pv[("score", "p1_neg")].to_numpy(), pv[("w", "p1_pos")].to_numpy()
            out["paired_win"] = np.average((sp > sn) + 0.5 * (sp == sn), weights=pw) if pw.sum() else np.nan
        else:
            out["paired_win"] = np.nan
    else:
        out["paired_win"] = np.nan
    return out


def evaluate(frame, scores, n_boot=500, seed=0):
    """frame: eval_frame rows of ONE split; scores: Series indexed by row_id. Returns {scope: {metric: [point, lo, hi]}}."""
    df = frame.set_index("row_id").join(scores.rename("score"), how="left").reset_index()
    df["scored"] = df.score.notna()
    finite = df.score.astype(float)
    floor = finite.min() - 1.0 if finite.notna().any() else 0.0  # unscorable rows sit below every real score
    df["score"] = finite.fillna(floor)
    scopes = {"all": df}
    for col in ("corpus", "difficulty_band"):
        for v, g in df.groupby(col):
            scopes[f"{col}={v}"] = g
    rng = np.random.default_rng(seed)
    result = {}
    for name, g in scopes.items():
        g = g.reset_index(drop=True)
        users, inv = np.unique(g.user_uid.to_numpy(), return_inverse=True)
        point = _point(g, np.ones(len(g)))
        boots = []
        for _ in range(n_boot):
            uw = rng.multinomial(len(users), np.full(len(users), 1 / len(users)))
            boots.append(_point(g, uw[inv].astype(float)))
        bdf = pd.DataFrame(boots)
        result[name] = {k: [round(float(point[k]), 4), round(float(bdf[k].quantile(0.025)), 4), round(float(bdf[k].quantile(0.975)), 4)]
                        if not np.isnan(point[k]) else [None, None, None] for k in point}
        result[name]["n_rows"] = int(len(g))
        result[name]["n_users"] = int(len(users))
        result[name]["n_pos"] = int((g.kind == "p1_pos").sum())
        result[name]["coverage"] = round(float(g.scored.mean()), 4)
    return result
