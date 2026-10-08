"""B2 — rp2's machine-generated-code detector (Santhanam et al., SemEval-2026 Task 13), reimplemented as published.

  features   char TF-IDF on RAW code, 3-5-grams, max 120,000 features, min_df 5; plus 6 structural features
             (length, line count, comment ratio, digit ratio, punctuation ratio, unique characters), standardised,
             concatenated (rp2 §4.1, §5.2)
  gating     MiniBatch K-means, K = 4, in TF-IDF space; each snippet routed to its nearest centroid (§4.2, §5.3)
  experts    per cluster: logistic regression (L2, max_iter 3000) + multinomial NB (alpha 0.5); probabilities averaged
  training   stratified 80,000-sample subset of the training split (§5.1)
  threshold  scanned over [0.70, 0.99] on validation, maximising macro-F1 (§5.4)
Gap filled here: multinomial NB needs non-negative input, so NB sees TF-IDF + min-max-scaled structural features,
while LR sees the standardised ones as specified.

Data: AICD T1 (rp2's EDA statistics match it: human 598 / AI 1,052 mean characters). AICD label 1 = machine,
0 = human (verified on the data: markdown fences 430x, "here is / this function" phrasing ~100x more in label 1).
Our AICD copy has train + validation (test was not kept), so validation is split in half by hash: one half
calibrates the threshold, the other is the held-out reproduction score — compare to rp2's validation 0.639.

On our exam the score is P(AI). Swaps are human code by another human, so ~chance is the expected result there;
the run also records the false-alarm rate at rp2's own threshold on genuine (natural) code, per corpus.
usage: python src/baselines/b2_rp2_stylometry.py            (fits once, scores val, and test with --final)
The fitted model is cached in runs/_work/b2_rp2/; later runs load it (use --refit to force a new fit).
"""
import argparse, hashlib, json, re, sys
import joblib
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.cluster import MiniBatchKMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.naive_bayes import MultinomialNB
from sklearn.preprocessing import MinMaxScaler, StandardScaler
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.canonical.db import connect, fetch_code  # noqa: E402
from src.eval.harness import load_frame, record_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
AICD = ROOT / "data/raw/aicd_bench/b7dc6d8_trainval/T1"
SEED, N_TRAIN, K = 42, 80_000, 4
PARAMS = {"paper": "rp2 Santhanam et al. 2026", "tfidf": {"analyzer": "char", "ngram_range": [3, 5], "max_features": 120000, "min_df": 5},
          "structural": ["length", "lines", "comment_ratio", "digit_ratio", "punct_ratio", "unique_chars"],
          "kmeans": {"type": "MiniBatchKMeans", "k": K}, "lr": {"penalty": "l2", "max_iter": 3000}, "nb": {"alpha": 0.5},
          "n_train": N_TRAIN, "seed": SEED, "threshold_scan": [0.70, 0.99],
          "data": "AICD T1 train (fit), validation halves (calibrate / held-out)", "nb_input": "TF-IDF + min-max structural"}
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/|#[^\n]*", re.S)
PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def structural(codes):
    rows = []
    for s in codes:
        n = max(len(s), 1)
        comm = sum(len(m) for m in COMMENT.findall(s))
        rows.append((len(s), s.count("\n") + 1, comm / n, sum(ch.isdigit() for ch in s) / n,
                     sum(ch in PUNCT for ch in s) / n, len(set(s))))
    return np.array(rows, dtype=float)


class RP2:
    def fit(self, codes, y):
        self.tfidf = TfidfVectorizer(analyzer="char", ngram_range=(3, 5), max_features=120000, min_df=5, dtype=np.float32)
        T = self.tfidf.fit_transform(codes)
        S = structural(codes)
        self.std, self.mm = StandardScaler().fit(S), MinMaxScaler().fit(S)
        self.km = MiniBatchKMeans(n_clusters=K, random_state=SEED, n_init=3, batch_size=4096).fit(T)
        cl = self.km.predict(T)
        X_lr = sparse.hstack([T, sparse.csr_matrix(self.std.transform(S))]).tocsr()
        X_nb = sparse.hstack([T, sparse.csr_matrix(self.mm.transform(S).clip(0, None))]).tocsr()
        self.experts = {}
        for k in range(K):
            m = cl == k
            if len(set(y[m])) < 2:  # a single-class cluster predicts its class
                self.experts[k] = float(y[m].mean()) if m.any() else 0.5
                continue
            lr = LogisticRegression(penalty="l2", max_iter=3000, random_state=SEED).fit(X_lr[m], y[m])
            nb = MultinomialNB(alpha=0.5).fit(X_nb[m], y[m])
            self.experts[k] = (lr, nb)
        return self

    def predict_proba(self, codes):
        T = self.tfidf.transform(codes)
        S = structural(codes)
        X_lr = sparse.hstack([T, sparse.csr_matrix(self.std.transform(S))]).tocsr()
        X_nb = sparse.hstack([T, sparse.csr_matrix(self.mm.transform(S).clip(0, None))]).tocsr()
        cl = self.km.predict(T)
        p = np.zeros(len(codes))
        for k, e in self.experts.items():
            m = cl == k
            if not m.any():
                continue
            p[m] = e if isinstance(e, float) else (e[0].predict_proba(X_lr[m])[:, 1] + e[1].predict_proba(X_nb[m])[:, 1]) / 2
        return p


def half(code):
    return int(hashlib.sha256(code.encode()).hexdigest()[:8], 16) % 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", action="store_true", help="also score the test split (once)")
    ap.add_argument("--smoke", action="store_true", help="4,000 training samples, 2,000 val rows, nothing recorded")
    ap.add_argument("--refit", action="store_true", help="ignore the cached fitted model")
    a = ap.parse_args()
    n_train = 4000 if a.smoke else N_TRAIN
    tr = duckdb.sql(f"select code, label from read_parquet('{AICD}/train-*.parquet') where length(trim(code)) > 0").df()
    tr = pd.concat([g.sample(n_train // 2, random_state=SEED) for _, g in tr.groupby("label")], ignore_index=True)
    va = duckdb.sql(f"select code, label from read_parquet('{AICD}/validation-*.parquet') where length(trim(code)) > 0").df()
    if a.smoke:
        va = va.sample(4000, random_state=SEED)
    va["half"] = va.code.map(half)
    # the fitted model is cached: re-scoring or re-evaluating later loads it instead of refitting (~20 min)
    cache = ROOT / "runs/_work/b2_rp2" / f"model_n{n_train}_seed{SEED}.joblib"
    if cache.exists() and not a.refit:
        model = joblib.load(cache)
        print(f"loaded cached model {cache.relative_to(ROOT)}", flush=True)
    else:
        print(f"fit on {len(tr)} AICD train samples", flush=True)
        model = RP2().fit(tr.code.tolist(), tr.label.to_numpy())
        cache.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, cache)
    cal, held = va[va.half == 0], va[va.half == 1]
    p_cal, p_held = model.predict_proba(cal.code.tolist()), model.predict_proba(held.code.tolist())
    grid = np.round(np.arange(0.70, 0.9901, 0.01), 2)
    f1s = [f1_score(cal.label, p_cal >= t, average="macro") for t in grid]
    thr = float(grid[int(np.argmax(f1s))])
    native = {"threshold": thr, "macro_f1_calibration_half": round(max(f1s), 4),
              "macro_f1_heldout_half": round(f1_score(held.label, p_held >= thr, average="macro"), 4),
              "macro_f1_heldout_at_0.5": round(f1_score(held.label, p_held >= 0.5, average="macro"), 4),
              "rp2_reported_validation": 0.639, "rp2_reported_test_ood": 0.6125, "n_cal": len(cal), "n_held": len(held)}
    print(json.dumps(native, indent=1), flush=True)
    c = connect()
    for split in ["val"] + (["test"] if a.final else []):
        f = load_frame(split)
        if a.smoke:
            f = f.sample(2000, random_state=SEED)
        c.register("need_df", f[["code_sha256"]].drop_duplicates())
        c.execute("create or replace temp table need as select code_sha256 from need_df")
        code = fetch_code(c, "need")
        p = pd.Series(model.predict_proba([code[h] for h in f.code_sha256]), index=f.row_id.values)
        fa = {k: round(float((p[f.row_id[(f.kind == "natural") & (f.corpus == k)]] >= thr).mean()), 4) for k in sorted(f.corpus.unique())}
        if a.smoke:
            print("smoke:", p.groupby(f.kind.values).mean().round(3).to_dict(), "false alarms", fa)
            continue
        out = record_run("b2_rp2_ai_detector", p, split, __file__, params=PARAMS, final=split == "test",
                         notes=f"rp2 reproduction; threshold {thr}; score = P(AI)")
        (out / "native_metrics.json").write_text(json.dumps({**native, "false_alarm_rate_on_natural_at_threshold": fa}, indent=1) + "\n")
        print(f"  false alarms on genuine code at threshold {thr}: {fa}", flush=True)


if __name__ == "__main__":
    main()
