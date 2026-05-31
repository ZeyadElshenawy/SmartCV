---
id: action_verbs_009_ml_evaluation_verbs
type: action_verb
title: ML Evaluation Verbs — Anchor Every Claim to Metric + Eval Set + Baseline
roles: [ml_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# ML Evaluation Verbs — Anchor Every Claim to Metric + Eval Set + Baseline

Evaluation discipline is the single fastest credibility signal on an ML engineering resume — and the fastest red flag when missing. Sculley et al.'s "Hidden Technical Debt in Machine Learning Systems" (NeurIPS 2015) names eval-related anti-patterns ("undeclared consumers", "data dependency debt", "feedback loops") as the dominant production failure modes. Hiring managers read evaluation verbs as a proxy for whether the candidate knows their model didn't break in production — or just hopes.

**Evaluation verbs (use these — but only with anchors):**
Measured, Validated, Benchmarked, Ablated, Audited, A/B tested, Shadow-tested, Calibrated, Cross-validated, Stress-tested, Probed (for OOD or adversarial behavior), Compared (against named baselines), Monitored (post-deployment), Reproduced (a prior result).

**Three anchors every evaluation bullet needs:**

1. **The metric, named specifically.** Not "accuracy" alone — Accuracy/F1/AUC/NDCG@K/BLEU/ROUGE/RMSE/MAE/Ragas-faithfulness/exact-match/recall@K. Different metrics imply different evaluation regimes; recruiters interpret the choice.
2. **The eval set.** Held-out test set, dev set, OOD probe set, shadow-traffic sample, A/B holdout, golden-question set. A bare metric without a named eval set is unfalsifiable.
3. **A baseline.** Prior production model, no-model control, random baseline, human inter-annotator agreement, a named external model (GPT-4-turbo, BERT-base). Without a baseline, a number isn't a result — it's a number.

**Strong evaluation bullets:**

- "Benchmarked 5 candidate ranking models on the held-out 50K-query test set; gradient-boosted variant won at +0.04 NDCG@10 over the prior production LambdaMART baseline (statistical significance p<0.01, 1K-query bootstrap)."
- "Validated the LLM classifier on a 1,200-example golden set hand-labeled by 3 product managers (Cohen's κ=0.82); achieved 91% exact-match vs. 64% for the rule-based baseline it replaced."
- "Ablated retrieval-vs-no-retrieval on the QA system across 800 prod-traffic-sampled questions; RAG improved answer-grounding (Ragas faithfulness 0.74 → 0.91) but cost +180ms p95."
- "A/B-tested the new ranker on 8% of search traffic over 14 days (n=2.1M queries); CTR +3.2pp (95% CI [2.8, 3.6]), no statistically significant change in dwell time."
- "Shadow-tested the v2 model on full prod traffic for 7 days before cutover; per-segment recall held within 1.5pp of the v1 baseline on every monitored slice (region × device × user-tenure)."

**Anti-patterns to reject:**

- "Evaluated the model" — no metric, no eval set, no baseline. The bullet should be deleted or rewritten.
- "Achieved 92% accuracy" — accuracy on what? against what? a 92% accuracy classifier with a 90%-majority-class baseline is doing almost nothing. Recruiters with ML backgrounds will catch this in interviews.
- "Improved model performance significantly" — neither metric nor baseline. The word "significantly" without a p-value or effect size is a tell.
- "Achieved state-of-the-art results" — meaningless on a resume; SOTA-on-what isn't named, and SOTA claims age in months.
- "Validated the model" without specifying validation regime — k-fold? hold-out? walk-forward time-series? Different choices imply different rigor.

**Calibration vs. accuracy.** A growing 2024-2026 expectation, especially for LLM-backed systems: candidates should know the difference between a model that's accurate (right answers) and a model that's calibrated (confident-when-right, uncertain-when-wrong). Bullets that mention calibration error (ECE, Brier score) or selective prediction (coverage at fixed precision) signal craft.

## Concrete rule for SmartCV

For ml_engineer bullets describing evaluation work, every evaluation verb (Measured, Validated, Benchmarked, Ablated, Audited, A/B-tested, Shadow-tested, Calibrated) must be anchored to three components: (1) the named metric (F1, NDCG@K, BLEU, Ragas, exact-match — never bare "accuracy"), (2) the eval set (held-out test set, OOD probe, prod-traffic shadow, golden-question set), and (3) a baseline (prior production model, no-model control, random baseline, named external model). Reject bare claims like "evaluated the model", "achieved 92% accuracy", or "improved performance significantly" — these are unfalsifiable and signal weak rigor.

---
sources:
  - https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html  (Sculley et al., "Hidden Technical Debt in Machine Learning Systems", NeurIPS 2015, accessed 2026-05-16)
  - https://en.wikipedia.org/wiki/Cross-validation_(statistics)  (accessed 2026-05-16)
  - https://capd.mit.edu/resources/resume-action-verbs/  (accessed 2026-05-16)
