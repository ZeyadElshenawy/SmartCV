---
id: industry_norms_014_ml_reproducibility
type: industry_norm
title: ML Reproducibility — Anchoring Bullets in Verifiable Engineering
roles: [ml_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# ML Reproducibility — Anchoring Bullets in Verifiable Engineering

Reproducibility is the single largest gap between research-grade ML and production-grade ML. Joelle Pineau's 2018 NeurIPS Reproducibility Checklist (now adopted as a mandatory submission requirement at NeurIPS, ICML, and ICLR) catalogues the signals: code release, hyperparameter ranges, seed disclosure, dataset versioning, hardware specification. Sculley et al.'s "Hidden Technical Debt in ML Systems" (NeurIPS 2015) frames the same problem from the production side: undeclared consumers, data dependency debt, and feedback loops all stem from work that wasn't reproducibly anchored.

**Distinct from metric reporting.** `industry_norms/013_ml_metric_reporting` covers what the eval said — the four anchors (metric name, value, eval set, baseline). This chunk covers whether anyone else could verify the result. A bullet can pass metric-reporting (well-named metric, clear eval set, sensible baseline) and still fail reproducibility (no seed, no version pin, no run ID — the next engineer can't rebuild the experiment). Both rules apply independently.

**The six reproducibility signals (mandate at least 3 in any model-training or experiment bullet):**

1. **Version pins** — PyTorch 2.4.0 + CUDA 12.4 + Python 3.11. The specific stack version matters: kernel implementations, autograd semantics, and bf16 behavior have all shifted between minor PyTorch releases. A bullet that names the versions is engineering; a bullet that doesn't is folklore.
2. **Seed setting** — random, numpy, torch (torch.manual_seed, torch.cuda.manual_seed_all). For data shuffling, also the DataLoader worker seed. PyTorch's docs explicitly note that "fully reproducible results are not guaranteed across PyTorch releases" — but seed setting is the floor.
3. **Dataset hash or commit pin** — SHA-256 of the training manifest, a DVC version tag, a snapshot commit ID, or a hash of the (deterministic) data-loading pipeline output. A bullet that says "trained on customer data" is a black box; a bullet that says "trained on dataset v2.4 (manifest SHA d7f9c2…)" is anchored.
4. **Experiment tracking** — MLflow / W&B / Comet run ID surfaced (or "tracked in MLflow"). The run ID lets someone retrieve the exact hyperparameters, metrics, and artifacts. Bullets without tracking imply notebook-only work.
5. **Model artifact in a registry** — ONNX export tagged v1.2, safetensors with a model card, or an MLflow Model Registry entry with a version tag. The registry entry IS the reproducibility anchor for the served model.
6. **Git commit pin for the training script** — sha 7e3d9af of model-training repo. The script's commit, not just the model's; otherwise re-running with the "same script" might not actually be the same.

**BAD -> GOOD transformations:**

- BAD: "Trained a sentiment classifier achieving 92% accuracy."
- GOOD: "Trained a 3-class sentiment classifier (DistilBERT-base-uncased, PyTorch 2.4 + CUDA 12.4, seed=42 across random/numpy/torch); 0.91 macro-F1 on the held-out 2K-example test set; tracked as MLflow run mlflow-run-id mlf:7f3a, artifact onnx://sentiment-v1.2 in the model registry, training script at git sha 9a3c2f1."

- BAD: "Fine-tuned an LLM on internal documentation."
- GOOD: "Fine-tuned Llama-3-8B via QLoRA (rank=16, target_modules=[q_proj, v_proj], LR=2e-4 cosine) on 14K-example internal QA pairs (dataset manifest SHA: 4c9d…f2); W&B run wandb://team/llm-ft/run-0042; adapter weights pushed to MLflow Registry as llama3-internal-qa-v1.0; reproduced once by a teammate from the same commit (git sha bda7c1) without drift."

- BAD: "Improved model performance by retraining on new data."
- GOOD: "Retrained the ranker on the Q3 dataset extension (v3.1 manifest, +180K rows hashed); held seeds (random/numpy/torch=42) and library pins (lightgbm 4.3, scikit-learn 1.4); NDCG@10 lifted 0.41 -> 0.47 on the held-out 80K-query benchmark, with the v3.0 model preserved in the registry for rollback."

**Why each signal matters individually.**

- *No version pin* → 6 months later, "torch 2.x" produces different results because of a numerical-kernel change. The candidate's claim is no longer verifiable.
- *No seed* → the lift might be hyperparameter sensitivity, might be lucky initialization, might be a real result. Without seeds, you can't tell.
- *No dataset version* → "we retrained on the latest data" is the most common form of unfalsifiable claim. New data + new metric = correlation, not causation.
- *No experiment tracking* → the bullet is the only artifact. Six months later, the candidate can't reconstruct the hyperparameters they actually used.
- *No model registry* → which model is in production? "The latest one" isn't a registry; it's hope.
- *No git commit pin* → the training script "changed slightly between v1 and v2" is the single most common source of irreproducibility in real teams.

**Anti-patterns:**

- Reproducibility theater: naming MLflow without using it ("tracked experiments in MLflow" with no run ID) is worse than not naming it — it implies discipline that isn't there.
- Listing tools without showing they were exercised: "PyTorch, CUDA, W&B, MLflow, DVC, Git" as a skills row tells the recruiter nothing about whether the candidate actually anchors their work in these tools. Anchor in the bullet, not the skill list.
- Confusing reproducibility with replication: replicating a paper's result on a public dataset is research practice; reproducing your own production result so the next engineer can debug it is engineering practice. Resume bullets should signal the second.

**Calibration vs. seniority.** A junior bullet that names 1-2 signals (seed + MLflow run) is credible. A mid bullet should name 3-4 (seed + versions + tracking + registry). A senior bullet should imply 5-6 by virtue of having designed the team's reproducibility discipline (see `seniority_norms/007_ml_senior` for the architecture-decision framing). Don't grade the same way at every level — over-claiming a 6-signal bullet at the junior level reads as inflation.

## Concrete rule for SmartCV

For ml_engineer bullets describing model training or experiments, mandate at least 3 of these six reproducibility signals: (1) version pins (PyTorch + CUDA + Python), (2) seeds (random/numpy/torch), (3) dataset hash or commit pin, (4) experiment tracking with run ID (MLflow, W&B, Comet), (5) model artifact in a registry (ONNX, safetensors, MLflow Registry, with a version tag), (6) git commit pin for the training script. Bullets reporting a metric with no reproducibility anchor read as one-off lab results, not engineering deliverables; recruiters with ML production experience discount them. Distinct from metric reporting: that rule covers what the eval said; this rule covers whether someone else could verify it.

---
sources:
  - https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html  (Sculley et al., "Hidden Technical Debt in Machine Learning Systems", NeurIPS 2015, accessed 2026-05-16)
  - https://www.cs.mcgill.ca/~jpineau/ReproducibilityChecklist.pdf  (Pineau, "ML Reproducibility Checklist", NeurIPS 2018 / extended for ICML 2019 keynote, accessed 2026-05-16)
  - https://mlflow.org/docs/latest/index.html  (MLflow documentation, accessed 2026-05-16)
  - https://pytorch.org/docs/stable/notes/randomness.html  (PyTorch reproducibility documentation, accessed 2026-05-16)
