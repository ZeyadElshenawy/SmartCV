---
id: industry_norms_013_ml_metric_reporting
type: industry_norm
title: ML Metric Reporting — Four Anchors Every Model Number Needs
roles: [ml_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# ML Metric Reporting — Four Anchors Every Model Number Needs

Engineering bullets get away with bare percentages — "reduced latency by 40%" reads fine when both the metric and baseline are obvious from context. ML bullets cannot. A model metric without anchoring is unverifiable, often misleading, and a hiring red flag specific to ML hiring managers who have seen enough inflated CVs to discount bare numbers. The Sklearn metrics documentation alone lists 30+ scoring functions; "92% accuracy" is ambiguous without naming which.

**The four anchors:**

1. **Metric name, specifically.** Not "accuracy" alone unless the dataset trivially implies it.
   - Classification: F1 (and which F1 — macro / micro / weighted), AUC-ROC, AUC-PR, precision@K, recall@K, exact-match.
   - Ranking: NDCG@K, MRR, MAP, hit-rate@K.
   - Regression: RMSE, MAE, MAPE, R².
   - Sequence / generation: BLEU, ROUGE-L, METEOR, exact-match, perplexity.
   - LLM-era: Ragas (faithfulness, answer-relevancy, context-precision), DeepEval scores, LLM-as-judge agreement, calibration error (ECE), refusal rate.
2. **Value.** A number, expressed to a meaningful number of significant digits (model F1 of 0.847 — not 0.84725, not 85%).
3. **Eval set or split.** Held-out test set, dev set, OOD probe set, prod-traffic shadow sample, golden hand-labeled set, A/B holdout. If the eval set is named ("the held-out 50K-query test set"), the bullet's credibility doubles.
4. **A baseline or comparison.** Prior production model, no-model rule baseline, random baseline, human inter-annotator agreement, named external model (GPT-4-turbo, BERT-base, the original paper's reported number). A metric without a baseline is a number; with one it's a result.

**BAD -> GOOD transformations:**

- BAD: "Achieved 92% accuracy on the classifier."
- GOOD: "Achieved 0.92 macro-F1 on the 2,400-example held-out test set, vs. 0.71 for the keyword-rule baseline it replaced."

- BAD: "Improved model performance by 15%."
- GOOD: "Lifted NDCG@10 from 0.41 to 0.47 (+0.06 absolute, +14% relative) on the production-traffic-sampled 80K-query benchmark; statistical significance p<0.001 with 1K-query bootstrap resampling."

- BAD: "Built an LLM classifier with high accuracy."
- GOOD: "Built an LLM classifier (Claude 3.5 Sonnet with Pydantic schema-enforced output); 0.91 exact-match on a 1,200-example golden set, vs. 0.64 for the prior keyword classifier and 0.93 for a 3-PM human-labeling triple."

- BAD: "92% precision and 89% recall."
- GOOD: "0.92 precision / 0.89 recall on the OOD probe set (200 manually constructed adversarial examples); precision held above 0.88 across all 4 user-segment slices."

**Common honest tradeoffs to surface:**

- Precision/recall tradeoffs are real; bullets that report only the favorable side are read with skepticism. Reporting both reads as honest.
- Latency-vs-accuracy tradeoffs are mandatory context for any production deployment bullet ("0.91 F1, but only at 320ms p95 — for the 80ms-budget endpoint we deployed the distilled 0.84-F1 variant instead").
- Sample-size context for small evals: "94% accuracy on a 23-example pilot set" is honest, "94% accuracy" alone implies the eval was robust.

**Anti-patterns to reject:**

- Round numbers. Real model metrics rarely land on 90%, 95%, 99%. A round number is either rounded down from the truth (lying with rounding) or rounded up (lying outright).
- "Significantly improved" / "dramatically reduced" without a p-value, effect size, or before/after pair.
- Bare lift numbers without absolute values: "+25%" — from what to what? A lift from 4% to 5% (relative +25%, absolute +1pp) is rounding error; from 60% to 75% (relative +25%, absolute +15pp) is a result.
- Stacking metrics that measure the same thing: "92% accuracy, 94% precision, 91% recall, 0.93 F1" — these are not four independent claims; they're four views of the same underlying performance. Pick one or two with the most decision-relevant view.

**Calibration claims are increasingly expected.** For ml_engineer roles touching LLM systems, expect the bullet to acknowledge calibration: a 0.85-F1 model that's overconfident on its errors is worse than a 0.78-F1 model that knows when to abstain. Mentions of ECE (Expected Calibration Error), Brier score, refusal/abstention rate, or selective-prediction coverage signal craft awareness.

## Concrete rule for SmartCV

For ml_engineer bullets, every numerical model metric must include four anchors: (1) the metric named specifically (F1 macro / micro, AUC-ROC, NDCG@K, perplexity, Ragas faithfulness, exact-match — never bare "accuracy" unless the dataset trivially implies it), (2) the value at meaningful precision, (3) the eval set or split (held-out test set, dev, OOD probe, golden hand-labeled set, prod-traffic shadow), and (4) a baseline or comparison (prior production model, no-model baseline, named external model, human inter-annotator agreement). Reject bare claims like "92% accuracy" or "improved performance significantly" — these are unverifiable and frequently fail interview verification. Report values at meaningful precision (0.847 not 0.85, 92.3% not 92%); real model metrics rarely land on suspiciously round numbers, and reporting at honest precision is itself a credibility signal.

---
sources:
  - https://scikit-learn.org/stable/api/sklearn.metrics.html  (accessed 2026-05-16)
  - https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html  (Sculley et al., "Hidden Technical Debt in Machine Learning Systems", NeurIPS 2015, accessed 2026-05-16)
  - https://capd.mit.edu/resources/resumes/  (accessed 2026-05-16)
