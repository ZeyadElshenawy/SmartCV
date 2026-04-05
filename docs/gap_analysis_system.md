# SmartCV Gap Analysis System
## Technical Documentation & Architecture Reference

---

## 1. System Overview
The Gap Analysis System is the analytical core ("The Hook") of the SmartCV platform. It sits directly between the user's uploaded Profile and a targeted Job Description. Its primary purpose is to quickly assess a candidate's fitness for a role, explicitly identifying what they already know and what they are missing. It solves the "black box" problem of job applications by giving users deterministic, actionable feedback (skills to learn or a chatbot to interview them to fill gaps) based on mathematical similarity and structured LLM extraction.

## 2. Data Models
The system operates primarily across four database models, utilizing PostgreSQL with the `pgvector` extension for semantic search capabilities.

### `profiles.models.UserProfile`
Stores the candidate's core data.
- `id`: UUID (Primary Key)
- `data_content`: JSONField (Contains core validated CV structures like `skills`, `experiences`, `education`, `projects`).
- `embedding`: VectorField (384 dimensions) — Represents the monolithic semantic meaning of the profile.
- `embedding_skills`, `embedding_experience`, `embedding_education`: VectorField (384 dims) — *Phase 1 infrastructure for future multi-vector chunking strategy.*

### `jobs.models.Job`
Stores the target position details.
- `title`, `company`, `description`: Text fields detailing the role.
- `extracted_skills`: JSON Field containing a list of target skills required for the job.
- `embedding`: VectorField (384 dimensions) — The semantic vector of the job description.

### 8. Database Architecture: `GapAnalysis`

The `GapAnalysis` model is defined in `analysis/models.py`.

*   **Primary Key**: UUID
*   **Foreign Keys**:
    *   `job`: A ForeignKey linking to `jobs.models.Job` with cascading deletion.
    *   `user`: A ForeignKey linking to the `User` model to map analysis to specific candidates.
*   **Uniqueness Constraint**: `unique_together = ('job', 'user')` ensures that each account has exactly one targeted gap analysis payload per external job.
*   **JSON Fields**: `matched_skills`, `missing_skills`, and `partial_skills` store the exact arrays yielded by the LLM Engine.
*   **Score Field**: Stores the overall cosine `similarity_score`.: FloatField normalising the semantic score (0.0 to 1.0).

### `profiles.services.schemas.GapAnalysisResult`
The Pydantic schema strictly binding the LLM's JSON output:
- `critical_missing`: List of strings (Hard technical skills lacking).
- `soft_skill_gaps`: List of strings (Leadership, communication, etc.).
- `matched_skills`: List of strings (Skills the user fulfills).

## 3. Pipeline Architecture
The analysis is triggered asynchronously via `POST /analysis/api/compute/<job_id>/`. The pipeline follows a strict multi-phase architecture:

1. **Vector embedding Check (Lazy Loading)**
   If `profile.embedding` or `job.embedding` is null, the system intercepts to generate them locally using `sentence-transformers`.
2. **Vector Similarity Scoring**
   The PostgreSQL database computes the cosine distance between the profile and job vectors to derive a baseline `similarity_score` percentage.
3. **Context Construction**
   The candidate's raw skills are enriched with proficiency/years, and their top experiences/projects are formatted into a single string to provide "proof of applied work."
4. **LLM Extraction**
   The LLM is invoked using the schema and context. It uses Strict Directional Matching to categorize skills into the Pydantic schema buckets.
5. **Fallback Execution (Safety Net)**
   If the LLM fails or timeouts, the system falls back to a deterministic, zero-dependency fuzzy string matcher using Python's `difflib`.

## 4. Embedding Strategy
- **Model Model:** `sentence-transformers/all-MiniLM-L6-v2` (Running completely locally to avoid HuggingFace rate limits).
- **Dimensions:** 384 floats.
- **Weighted Generation Strategy:** Instead of just embedding a raw wall of text, `generate_vector_input` artificially weights the input by constructing a string combining the Top 15 skills, current job title, and summaries of the top 3 projects/experiences.
- **Caching & Invalidation:** Vectors are stored directly on the `UserProfile` and `Job` models permanently. They are ONLY invalidated (set to `None`) via the `_bust_profile_embeddings` helper whenever the user successfully saves new data via CV Upload, Manual Form, or Chatbot completion. This ensures the 5-10s computation penalty is only paid when data actually changes.

## 5. Vector Distance and Filtering

PGVector calculates a `CosineDistance` which returns a strict distance score between `0.0` (identical) and `2.0` (opposite).
To translate this into a human-readable and standard machine similarity index, the formula used is:
```python
similarity_score = 1.0 - (distance / 2.0)
similarity_score = max(0.0, min(1.0, similarity_score))
```

The system requires `similarity_score > 0.80` (an 80% cosine match) to conclude that the user's vector embeddings loosely qualify against the job embeddings on a broad structural level. If the score is lower, the gap analysis inherently knows the core competencies simply do not align.

## 6. LLM Integration
The LLM phase is responsible for cleanly categorizing the required skills. It utilizes Groq (`ChatGroq`) with Llama-4 for ultra-low latency. It forces the output into the `GapAnalysisResult` Pydantic class via Langchain's `with_structured_output`.

**The Strict Directional Matching Prompt:**
The prompt enforces an explicit logic boundary to solve "Bottom-to-Top" semantic validation:
> *"Allow specific tools to satisfy broader category requirements. If the job requires a broad category ('SQL'), specific tools natively belonging to that category in the candidate's profile ('MySQL') firmly count as a MATCH. However, if the job requires a specific tool ('React'), a broad category in the candidate profile ('Frontend') DOES NOT MATCH."*

This is augmented by the **Applied Context Pipeline**, which feeds the LLM brief highlights from the user's configured `experiences` and `projects`. If a user doesn't explicitly list "Data Analysis" in their `skills` array, but their project says "Built an HR Data Analytics Dashboard", the LLM is explicitly instructed to honor that as a matched skill.

## 7. Fallback Mechanism
If the Groq API fails or rate-limits, a fallback executes so the user is never left hanging.
- **Engine:** Python's built-in `difflib.get_close_matches`.
- **Cutoff:** `0.85` threshold matching `n=1`.
- **Limitation:** It is purely string-distance based. It can easily correlate "Node.js" to "node", but it will completely fail to map "Power BI" to "Data Visualization".

## 8. Observability
To ensure developers can monitor the health of the pipeline in production, the returned dictionary explicitly sets `analysis_method` to either `llm` or `fallback`. This reveals immediately if the application is silently failing over to `difflib` due to ongoing LLM issues.

## 9. UI & Frontend

### The Loading Screen (`gap_analysis.html`)
Because the backend embedding model can take ~10-20 seconds on a cold run, the frontend uses an Alpine.JS state (`x-data="gapCalculator()"`) to artificially update a progress string for UX retention:
- *0s*: "Waking up the AI engine..."
- *4s*: "Computing match score..."
- *8s*: "Identifying missing skills..."
On success, it reloads the page to trigger the Django cached-view state.

### The Results Screen
- **The Score Gauge:** Uses a hardcoded SVG circle with a dynamic `stroke-dasharray` transition to animate the score loading. Colors adapt based on score (Green > 70%, Yellow > 50%, Red < 50%).
- **Dynamic Routing:** 
  - **> 80%:** The primary action shifts directly to "Generate Resume" (Green).
  - **> 50%:** The primary action shifts to the "Chat with AI to Fill Gaps" (Blue), aiming to dig missing details out of the user's brain.
  - **< 50%:** The primary action shifts to "Build Your Learning Path" (Purple), suggesting they need to study rather than apply.
- **Expandable Job Description:** A toggleable accordion to let users verify what text the system actually parsed.

## 10. Performance Profile
- **Local Embedding Vector Generation:** **~5 to 10 seconds.** (Primary bottleneck). This heavy computation runs locally via `sentence-transformers` on the host CPU.
- **LLM Structured Extraction:** **~2 to 7 seconds.** Handled via Groq's high-speed API, dependent on token length.
- **Cache Hits:** **~50ms**. 

## 11. Known Limitations & Future Improvements
- **Monolithic Embeddings:** The current vector search combines skills, education, and experience into a single `embedding`. The DB migrations for a multi-vector architecture (`embedding_skills`, `embedding_experience`, etc.) have been merged, but `gap_analyzer.py` has not yet been rewritten to perform multi-vector weighted distance searches. 
- **Embedding Generation Speed**: Calling `get_embedding(text)` executes the `sentence-transformers/all-MiniLM-L6-v2` multi-layer transformer network which outputs `384` dimension arrays. Synchronous CPU inference takes anywhere from 5 to 20 seconds. This blocks synchronous HTTP workers and risks Nginx `504 Gateway Timeout` triggers in production. (See Section 12 for the implemented Async worker deployment).

## 12. Background Task Architecture (`django-q2`)

To eliminate severe HTTP timeouts when generating `384`-dimension embedding tensors (since Nginx usually drops connections over 10 seconds), SmartCV integrates [Django-Q2](https://django-q2.readthedocs.io/en/master/).

### Async Workflows Added
When a profile is created or updated, or a job description is organically edited, the system invalidates the cached vectors via `_bust_profile_embeddings` and `_bust_job_embedding`.
It then schedules an asynchronous broker operation:
```python
from django_q.tasks import async_task

async_task('analysis.tasks.generate_profile_embeddings', profile.id)
async_task('analysis.tasks.generate_job_embeddings', job.id)
```

### Async Architecture
*   **Broker**: Uses the native Django Database ORM (the `django_q_ormq` tables) avoiding external Redis reliance.
*   **Workers**: Scales via `python manage.py qcluster`.
*   **Concurrency limits**: Defined in `settings.py` via the `Q_CLUSTER` definition (e.g., `workers: 2`, `timeout: 90`).
