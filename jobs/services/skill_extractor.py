import re
import logging
from difflib import SequenceMatcher
from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import JobExtractionResult, SkillListResult

logger = logging.getLogger(__name__)

# Hard denylist for the post-LLM filter. Each entry was an actual hallucination
# in benchmarks/results/2026-04-25 — the LLM adds them on most senior-ish JDs
# regardless of whether they appear in the text. We let the prompt try first,
# then strip these unconditionally if they slipped through *and* aren't in the
# JD verbatim. Soft skills that ARE verbatim in the JD (e.g. "leadership
# skills" or "Excellent communication") are kept — see _filter_skills below.
_GENERIC_SOFT_SKILL_DENYLIST = {
    "technical leadership",
    "problem solving",
    "problem-solving",
    "teamwork",
    "code review",
    "pair programming",
    "pairing sessions",
}

# --------------------------------------------------
# Enhanced Skill Knowledge Base (kept for reference/fallback)
# --------------------------------------------------
SKILL_KB = {
    # --- Core AI & ML Concepts ---
    "Artificial Intelligence": ["ai", "artificial intelligence", "ai systems", "ai solutions", "ai innovations", "ai models"],
    "Machine Learning": ["machine learning", "ml", "statistical learning", "ml spectrum", "machine learning algorithms"],
    "Deep Learning": ["deep learning", "neural networks", "ann", "cnn", "rnn", "deep-learning"],
    "Large Language Models": ["llm", "llms", "large language models", "gpt", "bert", "llama", "transformer models", "language models", "large deep-learning model"],
    "Generative AI": ["generative ai", "genai", "stable diffusion", "midjourney", "dall-e", "generative models", "gen ai", "generative"],
    "Retrieval-Augmented Generation": ["rag", "retrieval augmented generation", "rag pipelines"],
    "AI Agents": ["ai agents", "autonomous agents", "multi-agent systems", "autogen", "langchain agents", "agentic systems"],
    "Multi-Agent Systems": ["multi-agent systems", "multi agent systems", "agent orchestration"],
    "Computer Vision": ["computer vision", "cv", "object detection", "image segmentation", "ocr", "yolo", "opencv", "vision systems"],
    "Multimodal AI": ["multimodal ai", "multimodal learning", "multimodal models", "cross-modal learning", "multi-modal", "multimodal"],
    "Natural Language Processing": ["nlp", "natural language processing", "text mining", "sentiment analysis", "named entity recognition", "ner", "spacy", "nltk"],
    "Reinforcement Learning": ["reinforcement learning", "rl", "q-learning", "proximal policy optimization", "ppo", "reinforcement"],
    "Time Series Analysis": ["time series", "time-series forecasting", "arima", "prophet", "lstm for time series", "time series modeling", "time-series modeling"],
    "Recommender Systems": ["recommender systems", "recommendation engines", "collaborative filtering", "content-based filtering"],
    "Causal Inference": ["causal inference", "causality", "ab testing", "uplift modeling"],
    "Optimization": ["optimization", "optimization techniques", "mathematical optimization", "convex optimization"],
    "Data Science": ["data science", "data scientist", "data scientists"],
    "Data Analysis": ["data analysis", "exploratory data analysis", "eda", "analyze data"],
    "Predictive Analytics": ["predictive analytics", "predictive modeling", "predictive analysis", "prediction"],
    "Statistical Modeling": ["statistical models", "statistical modeling", "statistical methods"],
    "Statistics": ["statistics", "statistical analysis", "hypothesis testing", "bayesian statistics", "probability"],
    "Data Visualization": ["data visualization", "visualizations", "tableau", "power bi", "looker", "qlik", "matplotlib", "seaborn", "plotly"],
    "Data Mining": ["data mining"],
    "Feature Engineering": ["feature engineering", "feature selection", "dimensionality reduction", "pca"],
    "Research": ["research", "scientific research", "research methods"],
    "Python": ["python", "py", "programming skills of python"],
    "R": ["r programming", "r language"],
    "SQL": ["sql", "mysql", "postgresql", "t-sql", "pl/sql"],
    "C++": ["c++", "cpp"],
    "Java": ["java"],
    "Scala": ["scala"],
    "Julia": ["julia"],
    "MATLAB": ["matlab"],
    "SAS": ["sas"],
    "JavaScript/TypeScript": ["javascript", "js", "typescript", "ts"],
    "Go": ["golang", "go language"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Scikit-learn": ["scikit-learn", "sklearn"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow", "tf"],
    "Keras": ["keras"],
    "LangChain": ["langchain"],
    "LlamaIndex": ["llamaindex", "llama-index"],
    "Hugging Face": ["hugging face", "huggingface", "transformers library"],
    "XGBoost": ["xgboost", "xgb"],
    "LightGBM": ["lightgbm", "lgbm"],
    "CatBoost": ["catboost"],
    "SciPy": ["scipy"],
    "Statsmodels": ["statsmodels"],
    "FastAPI": ["fastapi"],
    "Django": ["django"],
    "Flask": ["flask"],
    "Streamlit": ["streamlit"],
    "MLOps": ["mlops", "machine learning operations", "model monitoring", "model serving", "mlops practices", "mlops best practices", "ml ops"],
    "CI/CD": ["ci/cd", "jenkins", "gitlab ci", "github actions", "ci cd for ml", "continuous integration", "continuous deployment"],
    "Model Deployment": ["model deployment", "deployment", "deploying models"],
    "Data Engineering": ["data engineering", "etl", "elt", "data pipelines"],
    "Apache Spark": ["spark", "apache spark", "pyspark"],
    "Docker": ["docker", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Databricks": ["databricks"],
    "Snowflake": ["snowflake"],
    "MLflow": ["mlflow"],
    "AWS": ["aws", "amazon web services", "sagemaker", "ec2", "s3", "lambda", "redshift"],
    "Azure": ["azure", "azure machine learning", "azure ml", "azure synapse"],
    "GCP": ["gcp", "google cloud platform", "vertex ai", "bigquery"],
    "NoSQL": ["nosql", "mongodb", "cassandra", "dynamodb", "redis"],
    "Vector Databases": ["vector databases", "vector db", "pinecone", "weaviate", "milvus", "qdrant", "chromadb", "pgvector", "vector database"],
    "Prompt Engineering": ["prompt engineering"],
    "Fine-Tuning": ["fine-tuning", "peft", "lora", "qlora", "model fine tuning", "fine tuning"],
    "Collaboration": ["collaborate", "collaboration", "collaborative"],
    "Communication": ["communicate", "communication", "presentations", "reports"],
    "Problem Solving": ["solve problems", "problem solving", "problem-solving"],
    "Leadership": ["leadership", "lead a team", "leading teams", "team leadership"],
    "Mentorship": ["mentorship", "mentoring", "mentor"],
}


def _is_jd_anchored(skill: str, jd_lower: str) -> bool:
    """True if the extracted skill name plausibly appears in the JD text.

    Permissive — we'd rather keep a real canonical extraction than reject it
    on a tokenization mismatch. Three independent passes:

    1. Full skill name appears as a substring of the JD (case-insensitive).
       Catches the easy 90% case ("AWS", "PostgreSQL", "TypeScript").
    2. After stripping common boilerplate suffixes (" pipelines", " API",
       " workflows", " testing", " skills", " experience"), the trimmed
       name appears as a substring. Lets "Leadership skills" match
       "leadership experience".
    3. Every alphabetic word longer than 2 chars in the skill name appears
       in the JD. Catches multi-word canonicalizations like "Tailwind CSS"
       when the JD only says "Tailwind".

    Skills that fail all three are very likely hallucinations.
    """
    s = skill.lower().strip()
    if not s:
        return False
    if s in jd_lower:
        return True
    trimmed = re.sub(
        r"\s+(pipelines?|apis?|workflows?|testing|clients?|sessions?|skills?|experience)$",
        "",
        s,
    )
    if trimmed and trimmed != s and trimmed in jd_lower:
        return True
    words = [w for w in re.findall(r"[a-z]+", s) if len(w) > 2]
    if words and all(w in jd_lower for w in words):
        return True
    return False


# --------------------------------------------------
# Full extraction pipeline (Using LangChain + Groq)
# --------------------------------------------------
# Canonical-name collapse map for short common-prefix mismatches that fail the
# 0.85 fuzzy matcher (e.g., 'REST' vs 'REST API' = 0.667, below cutoff).
# SKILL_KB covers most aliases; this catches the residue.
_CANONICAL_COLLAPSE = {
    "rest": "REST API", "rest api": "REST API", "rest apis": "REST API",
    "restful": "REST API", "restful api": "REST API", "restful apis": "REST API",
    "vue": "Vue.js", "vue.js": "Vue.js", "vuejs": "Vue.js",
    "react": "React", "react.js": "React", "reactjs": "React",
    "node": "Node.js", "node.js": "Node.js", "nodejs": "Node.js",
    "agile": "Agile", "agile development": "Agile",
    "agile development methodologies": "Agile", "agile methodologies": "Agile",
    "ci/cd": "CI/CD", "ci/cd pipeline": "CI/CD", "ci/cd pipelines": "CI/CD",
    "html": "HTML5", "html5": "HTML5",
    "css": "CSS3", "css3": "CSS3",
    "typescript": "TypeScript", "ts": "TypeScript",
    "javascript": "JavaScript", "js": "JavaScript",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "ts/cd": "CI/CD",  # common typo
}


def _build_skill_canonical_map():
    """One-time alias-lower → canonical map: SKILL_KB ∪ _CANONICAL_COLLAPSE."""
    out: dict[str, str] = {}
    for canonical, aliases in SKILL_KB.items():
        out[canonical.lower()] = canonical
        for a in (aliases or []):
            out[a.strip().lower()] = canonical
    # _CANONICAL_COLLAPSE wins on conflict (more idiomatic forms).
    out.update(_CANONICAL_COLLAPSE)
    return out


_SKILL_CANONICAL_MAP = _build_skill_canonical_map()


# Generic trailing role-nouns that pad a JD skill phrase without changing the
# underlying skill ("REST API integration" == "REST API"; "mobile applications"
# == "mobile"). One such noun is stripped from the END before alias lookup so a
# padded JD form collapses onto the same canonical as the bare token that
# "REST APIs"/"RESTful APIs" already map to. Principled small set — NOT
# per-skill enumeration. Singular "application" is intentionally ABSENT so
# "mobile application" is not reduced to "mobile".
_GENERIC_TRAILING_NOUNS = {"integration", "development", "applications", "apps"}


def _strip_trailing_generic_noun(key: str) -> str:
    """Drop ONE trailing generic role-noun from a lowercased skill key.

    Returns the key UNCHANGED when stripping would empty it or leave no other
    token (so a bare "apps"/"integration" is preserved, never blanked).
    """
    parts = key.split()
    if len(parts) >= 2 and parts[-1] in _GENERIC_TRAILING_NOUNS:
        return " ".join(parts[:-1])
    return key


def _canonicalize_skill(s: str) -> str:
    """Return the canonical surface form for s if known, else s.strip().

    Lookup order: the verbatim (lowercased) form first; on a miss, the form
    with one trailing generic role-noun stripped — so JD padding like
    "REST API integration" collapses onto the "REST API" canonical that
    "REST APIs"/"RESTful APIs" already map to. The strip only fires when the
    verbatim form is NOT already a known alias, so it never overrides an
    existing _CANONICAL_COLLAPSE / SKILL_KB key.
    """
    if not s:
        return s
    key = s.strip().lower()
    if key in _SKILL_CANONICAL_MAP:
        return _SKILL_CANONICAL_MAP[key]
    stripped = _strip_trailing_generic_noun(key)
    if stripped != key:
        # Prefer the alias canonical of the stripped form; else return the
        # stripped form itself so two differently-padded variants reduce alike.
        return _SKILL_CANONICAL_MAP.get(stripped, stripped)
    return s.strip()


_SKILL_GROUP_SPLIT = re.compile(r"[/&,]| and ")


def _skill_atoms(s: str) -> list:
    """Split a grouped skill token into its member skill names (else [s])."""
    return [p.strip() for p in _SKILL_GROUP_SPLIT.split(s or "") if p.strip()]


def skills_match(a: str, b: str, *, cutoff: float = 0.85) -> bool:
    """True iff two skill names denote the same skill.

    Shared by the gap-analyzer grounding validator and the planner's
    JD-relevance so both sites treat variant spellings identically:
      1. exact equality of canonical forms (alias table + trailing-noun strip);
      2. difflib SequenceMatcher.ratio() >= cutoff on the canonical forms;
      3. grouped enumerations: "JavaScript/TypeScript" matches "JavaScript"
         (and vice versa) by matching any member atom under (1)/(2).

    Uses ratio(), NOT token-set/containment, for whole phrases: ratio() is
    substring-safe ("Firebase Messaging" vs "Firebase" = 0.615 < 0.85), so a
    phantom sharing one token is NOT admitted. The atom split (3) only fires on
    explicit enumeration delimiters (/ & , "and"), so "React Native" (no
    delimiter) still does NOT match "React".
    """
    if not a or not b:
        return False
    ca = _canonicalize_skill(a).strip().lower()
    cb = _canonicalize_skill(b).strip().lower()
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    if SequenceMatcher(None, ca, cb).ratio() >= cutoff:
        return True
    atoms_a, atoms_b = _skill_atoms(a), _skill_atoms(b)
    if len(atoms_a) > 1 or len(atoms_b) > 1:
        for pa in atoms_a:
            for pb in atoms_b:
                cpa = _canonicalize_skill(pa).strip().lower()
                cpb = _canonicalize_skill(pb).strip().lower()
                if not cpa or not cpb:
                    continue
                if cpa == cpb or SequenceMatcher(None, cpa, cpb).ratio() >= cutoff:
                    return True
    return False


# --------------------------------------------------
# Domain canonicalization
# --------------------------------------------------
# LLM emits free-text domain ("Banking", "FinTech", "financial sector"); we
# collapse common variants to one canonical surface form so downstream callers
# (RAG region facet, analytics) see "Financial Services" no matter what the JD
# wording was. Free-text passthrough (title-cased) for anything unmapped.
_DOMAIN_ALIAS_MAP = {
    "banking": "Financial Services",
    "bank": "Financial Services",
    "banks": "Financial Services",
    "finance": "Financial Services",
    "financial": "Financial Services",
    "financial services": "Financial Services",
    "financial sector": "Financial Services",
    "fintech": "Financial Services",
    "insurance": "Financial Services",
    "healthcare": "Healthcare",
    "health care": "Healthcare",
    "medical": "Healthcare",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    "biotech": "Healthcare",
    "ecommerce": "E-commerce",
    "e-commerce": "E-commerce",
    "retail": "E-commerce",
    "marketplace": "E-commerce",
    "saas": "SaaS",
    "b2b saas": "SaaS",
    "gaming": "Gaming",
    "games": "Gaming",
    "game development": "Gaming",
    "edtech": "Education",
    "education": "Education",
    "education technology": "Education",
    "government": "Government",
    "public sector": "Government",
    "telecom": "Telecommunications",
    "telecommunications": "Telecommunications",
    "manufacturing": "Manufacturing",
    "energy": "Energy",
    "oil and gas": "Energy",
    "media": "Media",
    "entertainment": "Media",
    "advertising": "Media",
    "logistics": "Logistics",
    "supply chain": "Logistics",
    "real estate": "Real Estate",
    "proptech": "Real Estate",
}


def _canonicalize_domain(raw: str) -> str:
    """Map LLM-emitted domain string to a canonical industry label.

    Lowercases + strips, looks up in _DOMAIN_ALIAS_MAP. Falls through to
    title-cased free text when no alias matches. Returns "" for empty input.
    """
    if not raw or not raw.strip():
        return ""
    key = raw.strip().lower()
    if key in _DOMAIN_ALIAS_MAP:
        return _DOMAIN_ALIAS_MAP[key]
    # Try single-token fallbacks: "Financial Services & Banking" or
    # "Banking and Insurance" → first matched token wins.
    for token in re.split(r"\s*(?:&|/|,|\band\b)\s*", key):
        token = token.strip()
        if token in _DOMAIN_ALIAS_MAP:
            return _DOMAIN_ALIAS_MAP[token]
    # Unknown — return title-cased free text so downstream displays cleanly.
    return raw.strip().title()


# --------------------------------------------------
# Filtering helpers (shared between both lists)
# --------------------------------------------------

def _filter_skills(raw: list[str] | None, jd_lower: str) -> list[str]:
    """Three-pass post-filter: denylist drop (unless verbatim) → JD anchoring
    (against raw OR canonical form) → canonicalize + dedupe. Used for both
    must_have and nice_to_have lists.

    Anchoring against the canonical form catches the alias-mismatch case
    where the LLM emits "k8s" but the JD says "Kubernetes" (the bare "k8s"
    string never appears verbatim — anchoring would reject it without this
    cross-check).

    Soft skills like 'Leadership' or 'Communication' are NOT denylisted at
    all — they survive as long as they JD-anchor. The denylist is reserved
    for patterns the LLM hallucinates with no anchor at all (e.g. "Technical
    Leadership" on a JD that never mentions leadership).
    """
    if not raw:
        return []
    canonical: list[str] = []
    seen: set[str] = set()
    for s in raw:
        if not s:
            continue
        sl = s.lower().strip()
        if sl in _GENERIC_SOFT_SKILL_DENYLIST and sl not in jd_lower:
            logger.debug("skill_extractor: dropped denylisted '%s' (not in JD text)", s)
            continue
        c = _canonicalize_skill(s)
        # Anchor against EITHER the raw form OR the canonical form. Catches
        # alias cases where the JD uses the canonical spelling but the LLM
        # emitted the alias (or vice versa).
        if not (_is_jd_anchored(s, jd_lower) or _is_jd_anchored(c, jd_lower)):
            logger.debug("skill_extractor: dropped unanchored '%s' (no substring or word match in JD)", s)
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        canonical.append(c)
    return canonical


_EXTRACTION_PROMPT = """You are an expert AI recruiter system.

Your job is to read a job description and produce THREE outputs:

  1. must_have_skills  - technical skills the JD lists as REQUIRED. Cues to
                         look for: "Required Skills", "Must-have", "You must
                         have", "Responsibilities", "What you'll do",
                         "Qualifications", or skills mentioned in bullet
                         points without a "nice-to-have" qualifier.
  2. nice_to_have_skills - technical skills the JD lists as DESIRABLE / OPTIONAL.
                         Cues: "Nice to have", "Desirable Skills", "Bonus",
                         "Plus", "Preferred", "Good to have", "is a plus".
  3. domain            - a short noun phrase (1-3 words) naming the INDUSTRY
                         this role serves, inferred from the company
                         description and responsibilities. Examples:
                         "Financial Services", "Healthcare", "E-commerce",
                         "Gaming", "Telecommunications". If the JD gives no
                         clear domain signal, return an empty string.

=== WHAT COUNTS AS A SKILL ===
- Technical skills, tools, frameworks, languages, platforms, named technologies.
- Soft skills that are EXPLICITLY MENTIONED in the JD with a clear phrase:
    "leadership experience", "leadership skills", "team leadership" -> Leadership
    "Excellent communication", "communication skills" -> Communication
    "mentorship", "mentoring junior engineers" -> Mentorship
    "collaboration with stakeholders" -> Collaboration
  Include them when the JD names them; OMIT them when the JD doesn't.

=== ANTI-HALLUCINATION RULES (CRITICAL) ===
- Every output skill must have its name (or a well-known alias) appear
  somewhere in the JD text. Aliases: "k8s" -> Kubernetes, "gen ai" ->
  Generative AI, "ml" -> Machine Learning, etc.
- DO NOT infer skills from job seniority, company size, or industry alone.
  If "Python" is not in the text, do not list Python.
- DO NOT invent generic soft skills the JD never mentions (no "Problem
  Solving" / "Teamwork" / "Pair Programming" unless verbatim).
- It is OK to return an empty list for either tier when the JD doesn't
  use that structure. An empty must_have_skills is unusual; an empty
  nice_to_have_skills is common for short JDs.

=== TIER ASSIGNMENT RULES ===
- A skill that appears in BOTH required and desirable sections goes in
  must_have_skills (the stronger signal wins).
- A skill mentioned only in the company blurb (e.g. "we leverage AI to
  serve customers") is must_have only if it's also in the responsibilities;
  otherwise it goes in nice_to_have.

=== DOMAIN INFERENCE ===
- Read the company description (often the first paragraph) and the
  responsibilities. What industry does this role serve?
- Examples of good answers: "Financial Services" (bank, fintech, insurance),
  "Healthcare" (hospital, medtech, pharma), "Gaming" (game studio),
  "E-commerce" (retail, marketplace), "Government" (public sector).
- One short noun phrase. NOT a sentence. NOT a list. Empty string when no
  signal.

Job Description Text to analyze:
{text}
"""


def extract_job_info(text: str) -> JobExtractionResult:
    """Single-call LLM extractor producing tiered skills + domain.

    Returns an empty JobExtractionResult on empty input or LLM failure -
    callers should treat that as "no signal" rather than an error.
    """
    if not text:
        return JobExtractionResult()

    prompt = _EXTRACTION_PROMPT.format(text=text)

    try:
        structured_llm = get_structured_llm(
            JobExtractionResult,
            temperature=0.0,
            max_tokens=768,
            task="skill_extractor",
        )
        result = structured_llm.invoke(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to extract job info: %s", exc)
        return JobExtractionResult()

    if not result:
        return JobExtractionResult()

    jd_lower = text.lower()
    must = _filter_skills(result.must_have_skills, jd_lower)
    nice = _filter_skills(result.nice_to_have_skills, jd_lower)

    # Cross-tier dedupe: if a skill landed in both lists somehow (the LLM
    # sometimes echoes both sides), keep it in must_have only.
    must_keys = {s.lower() for s in must}
    nice = [s for s in nice if s.lower() not in must_keys]

    domain = _canonicalize_domain(result.domain or "")
    return JobExtractionResult(
        must_have_skills=must,
        nice_to_have_skills=nice,
        domain=domain,
    )


def extract_skills(text: str) -> list[str]:
    """Backward-compat shim: returns the flat union of must_have + nice_to_have.

    Existing callers (benchmarks/skill_extractor_eval, gap analyzer, etc.)
    keep working without change. Order: must_have first, then nice_to_have,
    deduped while preserving first-seen order.
    """
    info = extract_job_info(text)
    seen: set[str] = set()
    flat: list[str] = []
    for s in info.must_have_skills + info.nice_to_have_skills:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        flat.append(s)
    return flat
