---
id: industry_norms_009_ml_engineering
type: industry_norm
title: ML Engineering — Resume Conventions
roles: [ml_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-12
---

# ML Engineering — Resume Conventions

ML engineering sits between data science (research/training) and software engineering (production). Wikipedia's MLOps article (2026) defines the discipline as deploying and maintaining ML models reliably and efficiently, spanning data collection, feature engineering, training, deployment, and monitoring. Wikipedia notes 88% of ML initiatives struggle past testing — ML engineers bridge that gap.

**Tech-stack categories:**

- **Languages:** Python primary; Go / Rust / C++ for inference services.
- **ML training:** PyTorch (dominant), TensorFlow, JAX, Hugging Face, scikit-learn, XGBoost / LightGBM.
- **MLOps / tracking:** MLflow, Weights & Biases, Kubeflow, SageMaker, Vertex AI, Comet.
- **Feature stores:** Feast, Tecton, SageMaker Feature Store, Databricks.
- **Model serving:** TF Serving, TorchServe, Triton, BentoML, Ray Serve, KServe, vLLM, TGI.
- **LLM-specific:** OpenAI / Anthropic / Groq APIs, LangChain, LlamaIndex, fine-tuning (LoRA, QLoRA), eval (Ragas, DeepEval), vector DBs (pgvector, Pinecone, Weaviate, Qdrant, Chroma).
- **Orchestration:** Airflow, Prefect, Kubeflow Pipelines, Argo Workflows.
- **Observability:** Arize, WhyLabs, Fiddler, Evidently.

**Metrics:**
- **Model:** offline (accuracy, AUC, F1, BLEU, ROUGE, RAGAS, NDCG) + online (CTR, conversion).
- **Inference:** p50/p95/p99 latency, QPS, GPU %, batch size, $/M inferences.
- **Training:** wall-clock, GPU-hours, dataset size, checkpoint size.
- **Pipeline:** failure rate, drift-alert rate, rollout time.

**Strong bullet examples for ML engineering:**

- "Productionized the recommendation model (PyTorch two-tower, 18M users × 4M items) on Triton GPU servers; 8K QPS at p95=28ms; 41% cost reduction vs. CPU SageMaker."
- "Shipped LLM-powered support-ticket triage: Claude with Pydantic outputs, RAG over 18K KB articles via pgvector (1024-dim Voyage); auto-classified 78% of tickets at 94% precision; triage time fell from 22 min to 2 min."
- "Migrated training infra from ad-hoc EC2 to Kubeflow on EKS; new-model time-to-prod fell from 3 weeks to 4 days; MLflow catches regressions before deploy."
- "Built the monitoring stack (Evidently + Prometheus + PagerDuty alerts on PSI > 0.2); detected post-launch drift within 48h of an upstream schema change."
- "Fine-tuned a 7B LLaMA variant with QLoRA on 84K examples; eval perplexity dropped from 8.4 to 4.1; served via vLLM at 200ms p95, $0.04/1K tokens vs. $1.20 with GPT-4-turbo."

**Anti-patterns:**

- Listing every architecture you've heard of. Pick the ones you shipped.
- Claiming RAG / LLM expertise without naming embedding model, vector DB, and retrieval evaluation.
- Notebook prototyping ≠ production. Without API, observability, cost figures it's research.
- Listing every cloud-ML service. Pick 1–2 platforms.

**LLM-era 2024–2026 trends:** RAG (embedding, chunking, retriever eval, vector DB, reranking); structured outputs (Pydantic, JSON Schema); eval harnesses (Ragas, DeepEval, LLM-as-judge); cost-aware serving (vLLM, quantization, tiered routing); guardrails (input/output validation, jailbreak detection).

## Concrete rule for SmartCV

For ML engineering roles, surface the stack across Languages, ML frameworks, MLOps, Serving, LLM-specific, and Observability sub-groups. Every model bullet must include both an offline metric AND a production / inference characteristic (latency, QPS, cost, or business outcome). For LLM / RAG bullets, name the embedding model, vector DB, retrieval evaluation method, and serving framework. Distinguish between research / prototype work and shipped-to-production work.

---
sources:
  - https://en.wikipedia.org/wiki/MLOps  (accessed 2026-05-12)
  - https://en.wikipedia.org/wiki/Data_science  (accessed 2026-05-12)
