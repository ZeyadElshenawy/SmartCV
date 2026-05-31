---
id: action_verbs_008_ml_production_deployment_verbs
type: action_verb
title: ML Production Deployment Verbs — Notebook vs. Shipped Distinction
roles: [ml_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# ML Production Deployment Verbs — Notebook vs. Shipped Distinction

The hardest signal to send on an ML engineering resume is the difference between research that lived in a notebook and a model that reached production users. Wikipedia's MLOps article cites that ~88% of ML initiatives never make it past the testing phase. Hiring managers screen aggressively for verbs that prove the candidate's work crossed that line — the verb choice is the first thing they read.

**Production-grade verbs (use these for shipped work):**
Served, Productionized, Containerized, Orchestrated, Instrumented, Exposed (as an endpoint), Packaged (as an artifact), Registered (in a model registry), Gated (behind a feature flag or canary), Rolled out, Promoted (from staging to prod), Scaled, Retrained (as a continuous-training pipeline), Versioned, Warmed (as a cache or pre-load), Sharded (across replicas), Quantized (for inference speedup).

**Notebook-grade tell-verbs (reject these for production-shipped claims):**
Worked with, Used, Tried, Explored, Played with, Looked at, Studied (alone), Implemented (when the implementation never left a notebook), Trained (alone, without runtime or scale context — "Fine-tuned a 7B LLaMA on a vLLM cluster" is fine; "Trained a sentiment classifier" alone is not).

**Why the distinction matters.** "Built a model in TensorFlow" tells the recruiter nothing about whether the model survived contact with real traffic. "Productionized the recommendation model on Triton at 8K QPS, p95=28ms" tells them the candidate has done the hard 20% of ML work that the 80% of notebook-only candidates haven't.

**Strong production-deployment bullets:**

- "Productionized the demand-forecast model (PyTorch, 14 SKUs × 800 stores) on SageMaker endpoints behind a canary gate; auto-rollback triggered twice in 4 months on drift > 0.18 PSI, saving an estimated 6 stockout incidents."
- "Containerized the embedding service (sentence-transformers/all-MiniLM-L6-v2) with Triton + ONNX; served 240 RPS at p99=42ms on a single g5.xlarge, replacing a Lambda-based prototype that cost 4× more."
- "Orchestrated nightly retraining via Airflow + MLflow: feature freshness 24h, training wall-clock 38min, automatic promotion to staging on +0.4 F1 improvement against the held-out test set."
- "Instrumented the ranker with Prometheus + Evidently; latency, QPS, feature-drift PSI, and per-segment recall surfaced on a Grafana dashboard that on-call now references as the first thing to check during a quality regression."
- "Exposed the LLM-classifier as a FastAPI endpoint with Pydantic schemas, deployed via vLLM on 2× A10G; throughput 18 RPS at p95=620ms, $0.04 per 1K tokens vs. $1.20 with the GPT-4-turbo baseline."

Note the shape: every bullet pairs a production-grade verb with a runtime (Triton, vLLM, SageMaker, FastAPI) AND a production characteristic (QPS, p95, drift threshold, cost). One without the other reads incomplete.

**Anti-patterns:**

- "Worked with TensorFlow and PyTorch" — both notebook-grade verb AND no artifact/scale signal. Six words that tell the recruiter nothing.
- "Used scikit-learn for classification" — what classification? on what dataset? for whom? did it ship?
- "Built a model in a notebook" — the phrase "in a notebook" actively flags the work as research-only. If it shipped, say it shipped.
- "Implemented a deep learning model" — generic implementation verb, no runtime, no scale, no outcome. A 20-line PyTorch script and a 4-month productionization project look identical in this sentence.

**When notebook-grade verbs ARE correct.** A junior ML candidate or a research-track data scientist may have legitimate notebook-only work — exploratory analysis, ablations, model-selection studies. For those bullets, use the analysis verbs from `action_verbs/003` ("Analyzed", "Benchmarked", "Ablated"). Don't fake production framing for research work; recruiters spot it instantly. The rule here applies when the candidate's source CV claims the model reached users — surface the right verbs to back the claim.

## Concrete rule for SmartCV

When generating ml_engineer bullets for work the source CV claims reached production, prefer production-grade verbs: Served, Productionized, Containerized, Orchestrated, Instrumented, Exposed, Packaged, Gated. Reject notebook-grade openers — "worked with TensorFlow", "used scikit-learn", "built a model in a notebook" — they read as research-only and undersell production work. Every production-deployment bullet must name the serving runtime (Triton, vLLM, TorchServe, BentoML, FastAPI, SageMaker, Vertex AI) AND a production characteristic (QPS, p95 latency, instance type, autoscaling threshold, drift alert, cost-per-1K).

---
sources:
  - https://en.wikipedia.org/wiki/MLOps  (accessed 2026-05-16)
  - https://papers.nips.cc/paper/2015/hash/86df7dcfd896fcaf2674f757a2463eba-Abstract.html  (Sculley et al., "Hidden Technical Debt in Machine Learning Systems", NeurIPS 2015, accessed 2026-05-16)
  - https://capd.mit.edu/resources/resume-action-verbs/  (accessed 2026-05-16)
