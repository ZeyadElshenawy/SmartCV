import re
import logging
from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import SkillListResult

logger = logging.getLogger(__name__)

# Hard denylist for the post-LLM filter. Each entry was an actual hallucination
# in benchmarks/results/2026-04-25 — the LLM adds them on most senior-ish JDs
# regardless of whether they appear in the text. We let the prompt try first,
# then strip these unconditionally if they slipped through.
_GENERIC_SOFT_SKILL_DENYLIST = {
    "technical leadership",
    "problem solving",
    "problem-solving",
    "communication",
    "teamwork",
    "collaboration",
    "code review",
    "pair programming",
    "pairing sessions",
    "mentorship",
    "leadership",
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
}

def _is_jd_anchored(skill: str, jd_lower: str) -> bool:
    """True if the extracted skill name plausibly appears in the JD text.

    Permissive — we'd rather keep a real canonical extraction than reject it
    on a tokenization mismatch. Three independent passes:

    1. Full skill name appears as a substring of the JD (case-insensitive).
       Catches the easy 90% case ("AWS", "PostgreSQL", "TypeScript").
    2. After stripping common boilerplate suffixes (" pipelines", " API",
       " workflows", " testing"), the trimmed name appears as a substring.
       Lets "REST API" match a JD that just says "REST".
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
    trimmed = re.sub(r"\s+(pipelines?|apis?|workflows?|testing|clients?|sessions?)$", "", s)
    if trimmed and trimmed != s and trimmed in jd_lower:
        return True
    words = [w for w in re.findall(r"[a-z]+", s) if len(w) > 2]
    if words and all(w in jd_lower for w in words):
        return True
    return False


# --------------------------------------------------
# Full extraction pipeline (Using LangChain + Groq)
# --------------------------------------------------
def extract_skills(text):
    if not text:
        return []

    prompt = f"""You are an expert AI recruiter system.
Extract key professional skills, tools, frameworks, programming languages, and technologies from the following job description text.

Guidelines:
1. Extract ONLY technical skills, tools, frameworks, languages, platforms, and named technologies.
2. Try to map extracted skills to canonical names if appropriate (e.g. "aws" -> "AWS", "gen ai" -> "Generative AI", "k8s" -> "Kubernetes").
3. Return unique skill names.

=== STRICT ANTI-HALLUCINATION RULES (CRITICAL) ===
- Only list skills explicitly mentioned in the job description text. Do not invent skills.
- A skill is "explicitly mentioned" only if its name (or a well-known alias) appears verbatim in the text.
- DO NOT include generic soft skills. The following are BANNED unless the exact phrase appears verbatim in the JD:
  Technical Leadership, Problem Solving, Communication, Teamwork, Collaboration,
  Code Review, Pair Programming, Pairing Sessions, Mentorship, Leadership.
- DO NOT infer skills from job seniority, company type, or industry. If it isn't in the text, do not list it.

Job Description Text to analyze:
{text}"""

    try:
        structured_llm = get_structured_llm(SkillListResult, temperature=0.0, max_tokens=512, task="skill_extractor")
        result = structured_llm.invoke(prompt)

        if not result or not result.skills:
            return []

        # Defense in depth: even with the prompt rules above, the LLM occasionally
        # adds the banned soft skills on senior JDs. Strip them unconditionally
        # unless they appear verbatim in the JD text. Then drop anything that
        # fails JD-substring anchoring.
        text_lower = text.lower()
        out: list[str] = []
        for s in result.skills:
            if not s:
                continue
            sl = s.lower().strip()
            if sl in _GENERIC_SOFT_SKILL_DENYLIST and sl not in text_lower:
                logger.debug("skill_extractor: dropped denylisted '%s' (not in JD text)", s)
                continue
            if not _is_jd_anchored(s, text_lower):
                logger.debug("skill_extractor: dropped unanchored '%s' (no substring or word match in JD)", s)
                continue
            out.append(s)
        return out

    except Exception as e:
        logger.error(f"Failed to extract skills: {e}")
        return []
