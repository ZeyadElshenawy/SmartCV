import spacy
import re
from rapidfuzz import fuzz
from collections import defaultdict

# --------------------------------------------------
# 1. Load NLP model
# --------------------------------------------------
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    from spacy.cli import download
    download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

# --------------------------------------------------
# 2. Enhanced Skill Knowledge Base
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

    # --- Data Science & Analytics ---
    "Data Science": ["data science", "data scientist", "data scientists"],
    "Data Analysis": ["data analysis", "exploratory data analysis", "eda", "analyze data"],
    "Predictive Analytics": ["predictive analytics", "predictive modeling", "predictive analysis", "prediction"],
    "Statistical Modeling": ["statistical models", "statistical modeling", "statistical methods"],
    "Statistics": ["statistics", "statistical analysis", "hypothesis testing", "bayesian statistics", "probability"],
    "Data Visualization": ["data visualization", "visualizations", "tableau", "power bi", "looker", "qlik", "matplotlib", "seaborn", "plotly"],
    "Data Mining": ["data mining"],
    "Feature Engineering": ["feature engineering", "feature selection", "dimensionality reduction", "pca"],
    "Research": ["research", "scientific research", "research methods"],
    
    # --- Programming Languages ---
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

    # --- Python Libraries & Frameworks ---
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

    # --- Data Engineering & MLOps ---
    "MLOps": ["mlops", "machine learning operations", "model monitoring", "model serving", "mlops practices", "mlops best practices", "ml ops"],
    "CI/CD": ["ci/cd", "jenkins", "gitlab ci", "github actions", "ci cd for ml", "continuous integration", "continuous deployment"],
    "Model Deployment": ["model deployment", "deployment", "deploying models"],
    "Model Monitoring": ["model monitoring", "model performance monitoring", "monitoring models"],
    "Model Retraining": ["model retraining", "retraining", "model updating"],
    "Data Engineering": ["data engineering", "etl", "elt", "data pipelines"],
    "Data Pipelines": ["data pipelines", "pipeline development", "etl pipelines"],
    "Apache Spark": ["spark", "apache spark", "pyspark"],
    "Hadoop": ["hadoop", "hdfs", "mapreduce"],
    "Kafka": ["kafka", "apache kafka"],
    "Airflow": ["airflow", "apache airflow"],
    "Docker": ["docker", "containerization"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Databricks": ["databricks"],
    "Snowflake": ["snowflake"],
    "dbt": ["dbt", "data build tool"],
    "MLflow": ["mlflow"],
    "Kubeflow": ["kubeflow"],
    "WandB": ["weights & biases", "wandb"],

    # --- Cloud Platforms ---
    "AWS": ["aws", "amazon web services", "sagemaker", "ec2", "s3", "lambda", "redshift"],
    "Azure": ["azure", "azure machine learning", "azure ml", "azure synapse"],
    "GCP": ["gcp", "google cloud platform", "vertex ai", "bigquery"],
    
    # --- Databases ---
    "Relational Databases": ["rdbms", "relational database"],
    "NoSQL": ["nosql", "mongodb", "cassandra", "dynamodb", "redis"],
    "Vector Databases": ["vector databases", "vector db", "pinecone", "weaviate", "milvus", "qdrant", "chromadb", "pgvector", "vector database"],
    "Graph Databases": ["graph databases", "neo4j", "amazon neptune"],

    # --- Specialized & Emerging ---
    "Edge AI": ["edge ai", "tinyml", "edge computing", "edge deployments", "edge", "real-time ai deployments"],
    "Real-Time AI": ["real-time ai", "real time ai", "real-time deployments", "real time deployments", "real-time systems"],
    "Geospatial AI": ["geospatial ai", "gis", "arcgis", "geopandas", "spatial analysis"],
    "Digital Twins": ["digital twins", "digital twin", "twin technology"],
    "IoT": ["iot", "internet of things", "iot data"],
    "OT Data": ["ot data", "operational technology"],
    "Smart Systems": ["smart systems", "intelligent systems"],
    "Bioinformatics": ["bioinformatics", "computational biology"],
    "Quantitative Finance": ["quantitative finance", "algorithmic trading"],
    "AI Ethics": ["ai ethics", "responsible ai", "fairness", "bias mitigation", "ethics"],
    "Explainability": ["explainable ai", "xai", "explainability", "model interpretability"],
    "Prompt Engineering": ["prompt engineering"],
    "Fine-Tuning": ["fine-tuning", "peft", "lora", "qlora", "model fine tuning", "fine tuning"],
    "Synthetic Data": ["synthetic data", "data augmentation"],
    
    # --- Leadership & Strategy ---
    "Technical Leadership": ["technical leadership", "team lead", "tech lead", "technical oversight"],
    "AI Strategy": ["ai strategy", "ai roadmap", "strategic planning"],
    "Team Building": ["team building", "team development", "hiring", "building teams"],
    "Mentorship": ["mentorship", "mentoring", "coaching", "mentor", "guide and mentor"],
    "AI Governance": ["ai governance", "governance", "model governance"],
    "Regulatory Compliance": ["regulatory compliance", "compliance"],
    
    # --- Architecture & Engineering ---
    "System Architecture": ["system architecture", "architecture", "solution architecture", "design architectures"],
    "Scalability": ["scalability", "scalable systems", "scalable solutions", "scalable"],
    "Knowledge Engineering": ["knowledge engineering", "knowledge graphs"],
    "Semantic Search": ["semantic search", "vector search", "similarity search"],
    
    # --- Data Processing ---
    "Data Processing": ["data processing", "process data", "data preparation"],
    "Data Cleaning": ["data cleaning", "clean data", "data cleansing"],
    "Data Organization": ["organize data", "data organization", "schematize data"],
    
    # --- Collaboration & Communication ---
    "Collaboration": ["collaborate", "collaboration", "collaborative"],
    "Communication": ["communicate", "communication", "presentations", "reports"],
    "Problem Solving": ["solve problems", "problem solving", "problem-solving"],
}

# Reverse index: alias → canonical
ALIAS_TO_SKILL = {}
for canonical, aliases in SKILL_KB.items():
    for alias in aliases:
        ALIAS_TO_SKILL[alias.lower()] = canonical

# --------------------------------------------------
# 3. Enhanced candidate phrase extraction
# --------------------------------------------------
def extract_candidate_phrases(text):
    doc = nlp(text)
    candidates = set()

    # Noun phrases
    for chunk in doc.noun_chunks:
        phrase = chunk.text.strip().lower()
        if len(phrase.split()) <= 6:
            candidates.add(phrase)
            
            # Add sub-phrases for compound terms
            words = phrase.split()
            if len(words) > 2:
                for i in range(len(words)):
                    for j in range(i+1, len(words)+1):
                        sub_phrase = " ".join(words[i:j])
                        if len(sub_phrase.split()) >= 1:
                            candidates.add(sub_phrase)

    # Extract individual tokens that might be acronyms
    for token in doc:
        if token.text.isupper() and len(token.text) >= 2:
            candidates.add(token.text.lower())

    # Pattern-based extraction (AI & ML, CI/CD, etc.)
    pattern = r"\b([A-Za-z]+(?:\s*[&/]\s*[A-Za-z]+)+)\b"
    for match in re.findall(pattern, text):
        candidates.add(match.lower())

    # Extract hyphenated terms
    hyphen_pattern = r"\b([a-z]+-[a-z]+(?:-[a-z]+)*)\b"
    for match in re.findall(hyphen_pattern, text.lower()):
        candidates.add(match)

    return candidates

# --------------------------------------------------
# 4. Enhanced skill matching
# --------------------------------------------------
def match_skills(candidates, text_lower, threshold=80):
    extracted = set()

    # Direct exact matching first
    for phrase in candidates:
        if phrase in ALIAS_TO_SKILL:
            extracted.add(ALIAS_TO_SKILL[phrase])

    # Fuzzy matching for near-matches
    for phrase in candidates:
        if len(phrase) < 3:
            continue
            
        best_match = None
        best_score = 0
        
        for alias, canonical in ALIAS_TO_SKILL.items():
            score = fuzz.token_set_ratio(phrase, alias)
            if score > best_score and score >= threshold:
                best_score = score
                best_match = canonical
        
        if best_match:
            extracted.add(best_match)

    # Additional context-based extraction
    for alias, canonical in ALIAS_TO_SKILL.items():
        pattern = r'\b' + re.escape(alias) + r'\b'
        if re.search(pattern, text_lower, re.IGNORECASE):
            extracted.add(canonical)

    return extracted

# --------------------------------------------------
# 5. Intelligent contextual filtering
# --------------------------------------------------
def context_filter(text, skills):
    """
    Filter skills based on context and relevance to avoid false positives
    """
    lower_text = text.lower()
    filtered = set()
    
    # Skills that need explicit mention (commonly over-extracted)
    requires_explicit = {
        "Scalability", "Data Visualization", "Communication", 
        "Collaboration", "Problem Solving"
    }
    
    for skill in skills:
        # For skills requiring explicit mention
        if skill in requires_explicit:
            skill_found = False
            for alias in SKILL_KB.get(skill, []):
                # More strict matching for these skills
                pattern = r'\b' + re.escape(alias) + r'\b'
                if re.search(pattern, lower_text):
                    skill_found = True
                    break
            if skill_found:
                filtered.add(skill)
        else:
            # Include all other skills that were matched
            filtered.add(skill)
    
    return filtered

# --------------------------------------------------
# 6. Full extraction pipeline
# --------------------------------------------------
def extract_skills(text):
    if not text:
        return []
    text_lower = text.lower()
    candidates = extract_candidate_phrases(text)
    matched = match_skills(candidates, text_lower, threshold=78)
    filtered = context_filter(text, matched)
    return list(filtered)

