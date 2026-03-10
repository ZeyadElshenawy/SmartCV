import logging
import json
import os
import numpy as np
from pgvector.django import CosineDistance
from profiles.services.llm_engine import get_llm_client, LLM_MODEL
from profiles.services.embeddings import generate_vector_input, get_embedding
from jobs.models import Job

logger = logging.getLogger(__name__)

def compute_gap_analysis(profile, job):
    """
    Compare user profile with job requirements using semantic similarity (Embeddings)
    and list specific skill gaps.
    
    Args:
        profile: UserProfile instance
        job: Job instance
    
    Returns:
        dict with matched_skills, missing_skills, partial_skills, similarity_score
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
        # Create summary for job
        job_text = f"{job.title} at {job.company or ''}. {job.description}"
        emb = get_embedding(job_text)
        if emb:
            job.embedding = emb
            job.save()

    # Calculate Distance/Similarity
    if profile.embedding is not None and job.embedding is not None:
        # DB-side calculation using pgvector operator <=> (Cosine Distance)
        # We need to query to get the distance.
        try:
            # We can use the Job model to calculate distance to the profile embedding
            # annotate distance
            job_with_dist = Job.objects.annotate(
                distance=CosineDistance('embedding', profile.embedding)
            ).get(id=job.id)
            
            # Cosine Distance = 1 - Cosine Similarity
            # So Similarity = 1 - Distance
            # Note: pgvector CosineDistance returns 1 - cosine_similarity
            similarity_score = 1.0 - job_with_dist.distance
            
            # Clamp to 0-1 just in case
            similarity_score = max(0.0, min(1.0, similarity_score))
            
        except Exception as e:
            logger.error("Error calculating vector distance: %s", e)
            # Fallback to numpy if DB fails (e.g. SQLite local without pgvector)
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

    # 2. Hybrid LLM Gap Analysis
    # Use Gemini to intelligently compare lists and identify standardized gaps
    
    # Prepare data for LLM
    job_skills = job.extracted_skills
    user_skills = profile.skills
    
    try:
        client = get_llm_client()
        if not client:
            raise ValueError("LLM client unavailable")

        prompt = f"""
        Compare the Candidate's Skills against the Job Requirements.
        
        JOB REQUIREMENTS: {json.dumps(job_skills)}
        CANDIDATE SKILLS: {json.dumps(user_skills, default=str)}
        
        Task:
        1. Identify CRITICAL MISSING SKILLS (Hard technical skills the user clearly lacks).
        2. Identify SOFT SKILL GAPS (e.g. Leadership, Communication if required and missing).
        3. Identify MATCHED SKILLS (Skills the user has that matches requirements).
        
        Return valid JSON only:
        {{
            "critical_missing": ["skill1", "skill2"],
            "soft_skill_gaps": ["gap1"],
            "matched_skills": ["list of matched skills"]
        }}
        """
        
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert HR AI. Output ONLY a valid JSON object with no markdown fences, no explanation."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=512
        )
        
        content = response.choices[0].message.content
        gap_data = json.loads(content)
        
        return {
            'matched_skills': gap_data.get('matched_skills', []),
            'missing_skills': gap_data.get('critical_missing', []),
            'partial_skills': [],
            'soft_skill_gaps': gap_data.get('soft_skill_gaps', []),
            'critical_missing_skills': gap_data.get('critical_missing', []),
            'seniority_mismatch': gap_data.get('seniority_mismatch'),
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
        'partial_skills': [],  # Not calculated in fallback
        'soft_skill_gaps': [],
        'critical_missing_skills': missing_skills[:5],  # Top 5 as critical
        'seniority_mismatch': None,
        'similarity_score': round(similarity_score, 2)
    }
