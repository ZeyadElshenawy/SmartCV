---
id: bullet_patterns_008_rag_pattern
type: bullet_pattern
title: The RAG Bullet Pattern — Five Mandatory Components
roles: [ml_engineer, data_scientist]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# The RAG Bullet Pattern — Five Mandatory Components

Retrieval-Augmented Generation, formalized in Lewis et al. 2020 ("Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", arXiv:2005.11401), became the dominant LLM-application architecture in 2023-2026. Almost every AI / ML engineering CV submitted in 2026 includes a RAG bullet, which means generic "built a RAG system" bullets are now indistinguishable from each other — they fail to differentiate strong candidates from those who followed a LangChain tutorial.

**The five-component pattern:**

`<action verb> RAG over <corpus scope> using <embedding model> with <retrieval characteristic>, achieving <metric> on <named eval>.`

1. **Action verb** — Built, Shipped, Productionized, Architected (only if senior).
2. **Corpus scope** — N documents / N chunks / domain (legal contracts, support tickets, internal wiki).
3. **Embedding model named explicitly** — text-embedding-3-large, Voyage v3, BGE-M3, all-MiniLM-L6-v2, Cohere v3, nomic-embed-text. The choice signals tradeoffs (dimensions, cost, multilingual support, domain fit).
4. **Retrieval characteristic** — top-K, similarity threshold, hybrid (BM25 + dense), reranker model (cross-encoder, Cohere Rerank), chunk size + overlap.
5. **Measurable outcome on a named eval** — Ragas faithfulness, hit-rate@K, end-to-end answer accuracy on a golden set, support-ticket auto-resolution rate.

**BAD vs. GOOD examples:**

- BAD: "Built a RAG system using LangChain."
- GOOD: "Built RAG over 18K support-KB articles using Voyage v3 (1024-dim) + Cohere Rerank, top-10 -> reranked-to-3; lifted ticket auto-resolution from 31% to 78% on a 400-question golden set."

- BAD: "Implemented retrieval-augmented generation for the chatbot."
- GOOD: "Implemented hybrid retrieval (BM25 + BGE-M3 dense) over 240K legal clauses, chunk size 512 with 64 overlap; Ragas faithfulness 0.74 -> 0.91 vs. dense-only baseline."

- BAD: "Used vector search to improve answers."
- GOOD: "Shipped pgvector-backed retrieval over 50K internal docs, all-MiniLM-L6-v2 384-dim, top-5 above cosine 0.65 threshold; reduced hallucination rate (LLM-as-judge) from 18% to 4% on a 200-question audit set."

**Why each component matters individually.**

- *Skipping the corpus scope* makes the bullet unverifiable in an interview ("how big was your index?" — "uh, a lot of documents").
- *Skipping the embedding model* is the strongest tell that the candidate followed a tutorial without thinking about embedding choice — the embedding model is the single largest determinant of retrieval quality, and a candidate who can't name theirs probably can't defend the choice.
- *Skipping the retrieval characteristic* (top-K, threshold, reranker) signals the candidate hit "vectorStore.similaritySearch" and stopped.
- *Skipping a measurable outcome on a named eval* makes the bullet "I built a thing" — no claim of working.

**Anti-patterns specific to RAG bullets:**

- Naming the framework instead of the components: "Built a RAG with LangChain" tells the recruiter the candidate followed a quickstart. LangChain/LlamaIndex are scaffolding — the engineering content is the embedding choice, the chunking strategy, the retrieval eval.
- Using vague retrieval metrics: "improved retrieval accuracy" — what's retrieval accuracy? Recall@K? MRR? Hit-rate? Be specific.
- Mixing up retrieval eval and generation eval: "Built RAG with 92% accuracy" — accuracy of *what*? Retrieval hit-rate? End-to-end answer correctness? Faithfulness? They measure different failure modes.
- Listing the LLM but not the embedding model: "Built RAG with GPT-4" — the LLM is the easy part; the embedding choice is the engineering decision.

**Variation across consecutive bullets.** If the candidate has multiple RAG bullets across roles or projects, vary the dimension emphasized: one bullet leads with retrieval evaluation, another with chunking strategy, another with reranking, another with cost/latency. Three near-identical RAG bullets is worse than one strong one and two non-RAG bullets.

## Concrete rule for SmartCV

Generate RAG-system bullets with five mandatory components: (1) action verb (Built, Shipped, Productionized), (2) corpus scope (N documents / N chunks / domain), (3) embedding model named explicitly (text-embedding-3-large, Voyage v3, BGE-M3, all-MiniLM-L6-v2, Cohere v3, nomic-embed-text), (4) retrieval characteristic (top-K, similarity threshold, hybrid BM25+dense, reranker), and (5) a measurable outcome on a named eval (Ragas faithfulness, hit-rate@K, end-to-end answer accuracy, auto-resolution rate). Reject vague "built a RAG system" or "implemented retrieval-augmented generation" bullets that name neither the embedding model nor the retrieval evaluation — those bullets are tutorial-grade and undifferentiate the candidate.

---
sources:
  - https://arxiv.org/abs/2005.11401  (Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", 2020, accessed 2026-05-16)
  - https://en.wikipedia.org/wiki/Retrieval-augmented_generation  (accessed 2026-05-16)
  - https://www.indeed.com/career-advice/resumes-cover-letters/star-method-resume  (accessed 2026-05-16 — for the general bullet-pattern voice this RAG-specialization extends)
