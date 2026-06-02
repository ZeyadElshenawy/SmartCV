"""Deterministic skill → category grouping for resume display (rule 012).

Static lookup table; no LLM, no fuzzy matching. An unknown skill falls
through to 'Other' so a skill is NEVER dropped and NEVER misfiled into
a wrong category. Presentation only — introduces no new skills.

Lookup is case-insensitive against a canonical lowercased form. The
template re-renders the user's original casing.
"""
from __future__ import annotations


CATEGORIES_ORDER = (
    "Languages",
    "Frameworks & Libraries",
    "Databases",
    "Cloud & DevOps",
    "ML & Data",
    "Tools & Platforms",
    "Other",
)


# Canonical (lowercased) skill string → category. Bias is toward the
# software / ML / data vocabulary SmartCV typically sees; broader roles
# fall through to "Other" rather than risk misfiling.
_SKILL_TO_CATEGORY = {
    # --- Languages -------------------------------------------------------
    "python": "Languages", "java": "Languages", "javascript": "Languages",
    "typescript": "Languages", "go": "Languages", "golang": "Languages",
    "rust": "Languages", "c": "Languages", "c++": "Languages", "c#": "Languages",
    "php": "Languages", "ruby": "Languages", "swift": "Languages",
    "kotlin": "Languages", "scala": "Languages", "r": "Languages",
    "matlab": "Languages", "bash": "Languages", "shell": "Languages",
    "powershell": "Languages",
    "html": "Languages", "html5": "Languages", "css": "Languages", "css3": "Languages",
    "sass": "Languages", "scss": "Languages", "less": "Languages",
    "sql": "Languages", "dart": "Languages", "perl": "Languages",
    "lua": "Languages", "haskell": "Languages", "elixir": "Languages",

    # --- Frameworks & Libraries ------------------------------------------
    "django": "Frameworks & Libraries", "flask": "Frameworks & Libraries",
    "fastapi": "Frameworks & Libraries", "react": "Frameworks & Libraries",
    "react.js": "Frameworks & Libraries", "reactjs": "Frameworks & Libraries",
    "nextjs": "Frameworks & Libraries", "next.js": "Frameworks & Libraries",
    "vue": "Frameworks & Libraries", "vue.js": "Frameworks & Libraries",
    "vuejs": "Frameworks & Libraries", "nuxt.js": "Frameworks & Libraries",
    "angular": "Frameworks & Libraries", "angularjs": "Frameworks & Libraries",
    "svelte": "Frameworks & Libraries", "sveltekit": "Frameworks & Libraries",
    "express": "Frameworks & Libraries", "express.js": "Frameworks & Libraries",
    "node.js": "Frameworks & Libraries", "nodejs": "Frameworks & Libraries",
    "node": "Frameworks & Libraries",
    "spring": "Frameworks & Libraries", "spring boot": "Frameworks & Libraries",
    "rails": "Frameworks & Libraries", "ruby on rails": "Frameworks & Libraries",
    "laravel": "Frameworks & Libraries", ".net": "Frameworks & Libraries",
    "asp.net": "Frameworks & Libraries", "dotnet": "Frameworks & Libraries",
    "tailwind": "Frameworks & Libraries", "tailwindcss": "Frameworks & Libraries",
    "bootstrap": "Frameworks & Libraries", "jquery": "Frameworks & Libraries",
    "flutter": "Frameworks & Libraries", "react native": "Frameworks & Libraries",

    # --- Databases -------------------------------------------------------
    "postgresql": "Databases", "postgres": "Databases", "mysql": "Databases",
    "mariadb": "Databases", "mongodb": "Databases", "redis": "Databases",
    "elasticsearch": "Databases", "opensearch": "Databases", "sqlite": "Databases",
    "oracle": "Databases", "ms sql server": "Databases", "sql server": "Databases",
    "mssql": "Databases", "dynamodb": "Databases", "cassandra": "Databases",
    "snowflake": "Databases", "bigquery": "Databases", "supabase": "Databases",
    "firebase": "Databases", "firestore": "Databases", "pgvector": "Databases",
    "neo4j": "Databases", "clickhouse": "Databases",

    # --- Cloud & DevOps --------------------------------------------------
    "aws": "Cloud & DevOps", "azure": "Cloud & DevOps", "gcp": "Cloud & DevOps",
    "google cloud": "Cloud & DevOps", "docker": "Cloud & DevOps",
    "kubernetes": "Cloud & DevOps", "k8s": "Cloud & DevOps",
    "terraform": "Cloud & DevOps", "ansible": "Cloud & DevOps",
    "jenkins": "Cloud & DevOps", "github actions": "Cloud & DevOps",
    "gitlab ci": "Cloud & DevOps", "gitlab ci/cd": "Cloud & DevOps",
    "circleci": "Cloud & DevOps", "nginx": "Cloud & DevOps",
    "apache": "Cloud & DevOps", "linux": "Cloud & DevOps", "ubuntu": "Cloud & DevOps",
    "ci/cd": "Cloud & DevOps", "lambda": "Cloud & DevOps", "ec2": "Cloud & DevOps",
    "s3": "Cloud & DevOps", "cloudformation": "Cloud & DevOps",
    "heroku": "Cloud & DevOps", "vercel": "Cloud & DevOps", "netlify": "Cloud & DevOps",
    "render": "Cloud & DevOps", "fly.io": "Cloud & DevOps",

    # --- ML & Data -------------------------------------------------------
    "pandas": "ML & Data", "numpy": "ML & Data", "scipy": "ML & Data",
    "scikit-learn": "ML & Data", "sklearn": "ML & Data",
    "tensorflow": "ML & Data", "pytorch": "ML & Data", "keras": "ML & Data",
    "xgboost": "ML & Data", "lightgbm": "ML & Data", "catboost": "ML & Data",
    "matplotlib": "ML & Data", "seaborn": "ML & Data", "plotly": "ML & Data",
    "jupyter": "ML & Data", "tableau": "ML & Data", "power bi": "ML & Data",
    "spark": "ML & Data", "pyspark": "ML & Data", "hadoop": "ML & Data",
    "airflow": "ML & Data", "dbt": "ML & Data", "kafka": "ML & Data",
    "huggingface": "ML & Data", "hugging face": "ML & Data",
    "transformers": "ML & Data", "langchain": "ML & Data", "llamaindex": "ML & Data",
    "openai": "ML & Data", "llm": "ML & Data", "llms": "ML & Data",
    "large language models": "ML & Data", "generative ai": "ML & Data",
    "genai": "ML & Data", "gen ai": "ML & Data",
    "ai model development": "ML & Data", "ai feature implementation": "ML & Data",
    "ai tools deployment": "ML & Data", "internal ai tool deployment": "ML & Data",
    "model optimization": "ML & Data", "model evaluation": "ML & Data",
    "model deployment": "ML & Data", "fine-tuning": "ML & Data", "fine tuning": "ML & Data",
    "prompt engineering": "ML & Data", "rag": "ML & Data",
    "supervised learning": "ML & Data", "unsupervised learning": "ML & Data",
    "supervised & unsupervised learning": "ML & Data",
    "transfer learning": "ML & Data",
    "nlp": "ML & Data", "natural language processing": "ML & Data",
    "computer vision": "ML & Data", "opencv": "ML & Data",
    "deep learning": "ML & Data", "machine learning": "ML & Data",
    "statistical modeling": "ML & Data", "statistics": "ML & Data",
    "data analysis": "ML & Data", "data visualization": "ML & Data",
    "feature engineering": "ML & Data", "regression": "ML & Data",
    "classification": "ML & Data", "clustering": "ML & Data",
    "time series": "ML & Data", "reinforcement learning": "ML & Data",
    "etl": "ML & Data", "elt": "ML & Data",

    # --- Tools & Platforms ----------------------------------------------
    "git": "Tools & Platforms", "github": "Tools & Platforms",
    "gitlab": "Tools & Platforms", "bitbucket": "Tools & Platforms",
    "jira": "Tools & Platforms", "confluence": "Tools & Platforms",
    "trello": "Tools & Platforms", "notion": "Tools & Platforms",
    "slack": "Tools & Platforms", "figma": "Tools & Platforms",
    "vscode": "Tools & Platforms", "vs code": "Tools & Platforms",
    "intellij": "Tools & Platforms", "pycharm": "Tools & Platforms",
    "postman": "Tools & Platforms", "insomnia": "Tools & Platforms",
    "swagger": "Tools & Platforms", "openapi": "Tools & Platforms",
    "graphql": "Tools & Platforms", "rest": "Tools & Platforms",
    "rest api": "Tools & Platforms", "rest apis": "Tools & Platforms",
    "websocket": "Tools & Platforms", "websockets": "Tools & Platforms",
    "grpc": "Tools & Platforms", "agile": "Tools & Platforms",
    "scrum": "Tools & Platforms", "kanban": "Tools & Platforms",
    "technical documentation": "Tools & Platforms",
}


# When grouped display becomes lopsided (most skills land in one bucket,
# typically "Other"), the resume reads worse than a clean flat list.
# Show the categorized layout only when it's BALANCED:
#   - at least MIN_CATEGORIES populated (so it actually looks categorical), and
#   - no single category holds more than MAX_DOMINANCE of the total skills.
# Otherwise the template falls back to the flat comma-joined list. This
# guard is general — it protects all users, not just dev/ML profiles.
MIN_CATEGORIES = 3
MAX_DOMINANCE = 0.60


def should_show_grouped(groups, total_skills):
    """Return True iff the categorised grouping reads better than a flat
    list.

    ``groups`` is the output of ``group_skills_for_display`` (a list of
    ``{category, skills}`` dicts, empties already dropped). ``total_skills``
    is the count of distinct skills that went in. The rule is documented
    above as ``MIN_CATEGORIES`` / ``MAX_DOMINANCE``.
    """
    if not groups or total_skills <= 0:
        return False
    if len(groups) < MIN_CATEGORIES:
        return False
    largest = max(len(g["skills"]) for g in groups)
    if largest / total_skills > MAX_DOMINANCE:
        return False
    return True


def categorize_skill(skill):
    """Return the category for a skill string. Unknown → 'Other'."""
    if not isinstance(skill, str):
        return "Other"
    return _SKILL_TO_CATEGORY.get(skill.strip().lower(), "Other")


def group_skills_for_display(skills):
    """Group a flat skills list into ordered categories for the resume template.

    Returns ``[{'category': str, 'skills': list[str]}, ...]`` in
    ``CATEGORIES_ORDER``, omitting empty categories. Preserves the user's
    original skill ordering and casing within each category, deduped
    case-insensitively. Unknown skills end up under 'Other' — never
    dropped, never misfiled.
    """
    if not skills:
        return []
    buckets = {cat: [] for cat in CATEGORIES_ORDER}
    seen = set()
    for raw in skills:
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        buckets[categorize_skill(name)].append(name)
    return [
        {"category": cat, "skills": buckets[cat]}
        for cat in CATEGORIES_ORDER
        if buckets[cat]
    ]
