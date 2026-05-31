---
id: bullet_patterns_009_fine_tuning_pattern
type: bullet_pattern
title: The Fine-Tuning Bullet Pattern — Six Mandatory Components
roles: [ml_engineer]
seniority: [all]
industries: [all]
region: global
weight: high
last_updated: 2026-05-16
---

# The Fine-Tuning Bullet Pattern — Six Mandatory Components

Parameter-efficient fine-tuning (PEFT) and full fine-tuning are the second-most-common LLM-engineering bullet shapes on 2024-2026 CVs, after RAG. The methods themselves are well-documented — LoRA (Hu et al. 2021, arXiv:2106.09685), QLoRA (Dettmers et al. 2023, arXiv:2305.14314), and DPO (Rafailov et al. 2023, arXiv:2305.18290) all have canonical references — but the typical fine-tuning bullet on a CV strips the engineering content out: "fine-tuned a model" or "used LoRA" gives the recruiter no way to evaluate the candidate's craft.

**The six-component pattern:**

`<action verb> <method> <base model> on <dataset description> for <task>, achieving <metric> on <eval>.`

1. **Action verb** — Fine-tuned, Adapted, Aligned, Distilled. **Reject bare "Trained"** — see `action_verbs/008_ml_production_deployment_verbs` for the notebook-vs-shipped distinction; bare "Trained" is on the reject list because it doesn't distinguish from-scratch training from continued pretraining from fine-tuning.
2. **Method named explicitly** — full FT, LoRA at rank R (LoRA r=16, alpha=32), QLoRA (4-bit NF4), prefix tuning, DPO, RLHF (PPO step), continued pretraining. The method choice signals trade-offs (compute, data efficiency, alignment regime).
3. **Base model with parameter count** — Llama-3-8B-Instruct, Mistral-7B-Instruct, Phi-3.5-mini-instruct, gemma-2-9b-it, Qwen2.5-14B. The parameter count signals compute scale; the variant suffix signals whether the candidate started from a base or instruction-tuned checkpoint.
4. **Dataset description** — size + domain + label source. "12K legal-clause classification examples, hand-labeled by 3 paralegals (Cohen's κ=0.78)" is engineering. "domain-specific data" is not.
5. **Task** — what the fine-tuned model now does that the base could not. Classification on N labels, instruction-following on domain Y, retrieval-augmented generation grounding on corpus Z, code completion on language stack W.
6. **Measurable outcome on a named eval** — see `industry_norms/013_ml_metric_reporting`. The eval set should be held-out from training; the metric should be specific.

**BAD vs. GOOD examples:**

- BAD: "Fine-tuned a large language model on customer data."
- GOOD: "Fine-tuned Llama-3-8B-Instruct via QLoRA (4-bit NF4, rank 16) on 12K hand-labeled support-ticket triage examples; achieved 0.91 macro-F1 on the held-out 800-example test set, vs. 0.74 for the zero-shot Llama-3-8B baseline and 0.93 for GPT-4-turbo as upper bound."

- BAD: "Used LoRA for parameter-efficient training."
- GOOD: "Applied LoRA (rank 32, alpha 64, target modules: q_proj, v_proj) to adapt Mistral-7B-Instruct for clinical-note summarization; trained on 8K MIMIC-derived (synthetic-PHI) example pairs; ROUGE-L 0.41 -> 0.58 vs. zero-shot Mistral baseline on the 1K-example held-out set."

- BAD: "Trained a model on company data for chatbot use."
- GOOD: "Aligned Phi-3.5-mini-instruct via DPO on 5K preference pairs hand-collected from senior support agents over 6 weeks; pairwise-preference win rate against the base instruction-tuned model rose from 50% (parity) to 71% on a 200-question audit set, scored by 3 product managers."

- BAD: "Adapted a foundation model to our domain."
- GOOD: "Continued-pretrained gemma-2-9b on 240M tokens of internal documentation (8 epochs, AdamW, cosine schedule, peak LR 5e-5); perplexity on a held-out 8M-token doc sample dropped from 18.4 to 9.1; downstream Ragas faithfulness on the doc-QA task rose from 0.71 to 0.84 vs. the base 9B."

**Method-choice signals.** The method named in the bullet implicitly tells the recruiter:

- **Full FT on a 7B+ model** — the candidate had access to multi-GPU compute (A100/H100 cluster) AND made a deliberate choice against PEFT, usually because the base model couldn't approach the task even with adapter weights.
- **LoRA / QLoRA** — production-aware: single-GPU or modest cluster, deployable as a small adapter on top of a frozen base.
- **DPO / RLHF** — alignment work, usually after an initial SFT pass; signals the candidate understands preference data is distinct from supervised data.
- **Continued pretraining** — domain-adaptation at scale; signals access to a large domain corpus.

**Anti-patterns to reject:**

- Naming the framework without the method: "Fine-tuned with Hugging Face Trainer" — Trainer is scaffolding; the engineering content is the PEFT method, rank, target modules, learning rate.
- Hyperparameter dumps without outcomes: "trained for 3 epochs at lr=2e-5, batch=8, on 4xA100 for 14 hours". This tells the recruiter the candidate executed the recipe; it does NOT tell them the model got better. Pair every hyperparameter cluster with a measurable result.
- Naming the LLM but not the base: "Built a domain LLM" — was it Llama-3-8B? Mistral-7B? A 70B? The parameter count is the cost / quality signal.
- Conflating eval sets: training-set metrics ("achieved 0.99 F1") are not results; held-out test metrics are.
- Claiming "improved performance" without naming the base-model baseline: a fine-tuned 8B model beating a smaller base 8B is expected; beating GPT-4-turbo on the same task is a result.

**Choosing among FT methods (for body context, not the rule).** PEFT method choice has become a common interview question. Candidates should be able to justify LoRA vs. QLoRA vs. full FT vs. continued pretraining in 30 seconds. The bullet should make the choice defensible without requiring the question.

## Concrete rule for SmartCV

Generate fine-tuning bullets with six mandatory components: (1) action verb (Fine-tuned, Adapted, Aligned, Distilled — never bare "Trained"), (2) method named explicitly (full FT, LoRA at rank R, QLoRA, prefix tuning, DPO, RLHF, continued pretraining), (3) base model with parameter count (Llama-3-8B-Instruct, Mistral-7B-Instruct, Phi-3.5-mini, gemma-2-9b, Qwen2.5-14B), (4) dataset description (size + domain + label source), (5) task, and (6) measurable outcome on a named held-out eval. Reject vague "fine-tuned a model" or "used LoRA" bullets that name neither base model nor task — they signal tutorial-grade work.

---
sources:
  - https://arxiv.org/abs/2106.09685  (Hu et al., "LoRA: Low-Rank Adaptation of Large Language Models", 2021, accessed 2026-05-16)
  - https://arxiv.org/abs/2305.14314  (Dettmers et al., "QLoRA: Efficient Finetuning of Quantized LLMs", 2023, accessed 2026-05-16)
  - https://arxiv.org/abs/2305.18290  (Rafailov et al., "Direct Preference Optimization", 2023, accessed 2026-05-16)
  - https://huggingface.co/docs/peft  (Hugging Face PEFT library docs, accessed 2026-05-16)
