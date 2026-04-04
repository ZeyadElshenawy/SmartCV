import logging
import json
import numpy as np
from pgvector.django import CosineDistance
from profiles.services.llm_engine import get_structured_llm
from profiles.services.schemas import GapAnalysisResult
from profiles.services.embeddings import generate_vector_input, get_embedding
from jobs.models import Job

logger = logging.getLogger(__name__)

def compute_gap_analysis(profile, job):
    """
    Compare user profile with job requirements using semantic similarity (Embeddings)
    and list specific skill gaps via LangChain structured output.
    """
    # 1. Similarity Score using Embeddings (Cosine Similarity)
    similarity_score = 0.0
    
    # Ensure embeddings exist (Generate on the fly if missing - lazy)
    if profile.embedding is None:
        summary = generate_vector_input(profile.data_content)
        emb = get_embedding(summary)
        if emb:
            profile.embedding = emb
            profile.save()
            
    if job.embedding is None:
        job_text = f"{job.title} at {job.company or ''}. {job.description}"
        emb = get_embedding(job_text)
        if emb:
            job.embedding = emb
            job.save()

    # Calculate Distance/Similarity
    if profile.embedding is not None and job.embedding is not None:
        try:
            job_with_dist = Job.objects.annotate(
                distance=CosineDistance('embedding', profile.embedding)
            ).get(id=job.id)
            
            similarity_score = 1.0 - job_with_dist.distance
            similarity_score = max(0.0, min(1.0, similarity_score))
            
        except Exception as e:
            logger.error("Error calculating vector distance: %s", e)
            try:
                p_vec = np.array(profile.embedding)
                j_vec = np.array(job.embedding)
                norm_p = np.linalg.norm(p_vec)
                norm_j = np.linalg.norm(j_vec)
                if norm_p > 0 and norm_j > 0:
                    similarity_score = float(np.dot(p_vec, j_vec) / (norm_p * norm_j))
            except Exception as np_err:
                logger.error("Numpy fallback for similarity also failed: %s", np_err)
                similarity_score = 0.0

    # 2. Hybrid LLM Gap Analysis via LangChain structured output
    job_skills = job.extracted_skills
    user_skills = profile.skills
    
    try:
        prompt = f"""Compare the Candidate's Skills against the Job Requirements.
        
JOB REQUIREMENTS: {json.dumps(job_skills)}
CANDIDATE SKILLS: {json.dumps(user_skills, default=str)}

Task:
1. Identify CRITICAL MISSING SKILLS (Hard technical skills the user clearly lacks).
2. Identify SOFT SKILL GAPS (e.g. Leadership, Communication if required and missing).
3. Identify MATCHED SKILLS (Skills the user has that matches requirements).

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills not present in the provided lists.
- Only report on what actually exists in the provided lists."""

        structured_llm = get_structured_llm(GapAnalysisResult, temperature=0.1, max_tokens=512)
        result = structured_llm.invoke(prompt)
        
        return {
            'matched_skills': result.matched_skills,
            'missing_skills': result.critical_missing,
            'partial_skills': [],
            'soft_skill_gaps': result.soft_skill_gaps,
            'critical_missing_skills': result.critical_missing,
            'seniority_mismatch': None,
            'similarity_score': round(similarity_score, 2)
        }
            
    except Exception as e:
        logger.error(f"LLM Gap Analysis failed: {e}. Falling back to set difference.")

    # Fallback to Set Difference
    user_skills_set = set()
    for s in profile.skills:
        if isinstance(s, dict):
            name = s.get('name', '')
            if name: user_skills_set.add(name.lower().strip())
        elif isinstance(s, str):
            user_skills_set.add(s.lower().strip())
    
    matched_skills = []
    missing_skills = []
    
    for js in job.extracted_skills:
        js_clean = js.lower().strip()
        if js_clean in user_skills_set:
            matched_skills.append(js)
        else:
            missing_skills.append(js)
            
    return {
        'matched_skills': matched_skills,
        'missing_skills': missing_skills,
        'partial_skills': [],
        'soft_skill_gaps': [],
        'critical_missing_skills': missing_skills[:5],
        'seniority_mismatch': None,
        'similarity_score': round(similarity_score, 2)
    }
