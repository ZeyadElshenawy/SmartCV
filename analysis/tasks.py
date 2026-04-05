import logging
from profiles.models import UserProfile
from jobs.models import Job
from profiles.services.embeddings import get_embedding, generate_vector_input

logger = logging.getLogger(__name__)

def generate_profile_embeddings(profile_id):
    """
    Background task to generate all 384-dimensional vector embeddings for a UserProfile.
    """
    try:
        profile = UserProfile.objects.get(id=profile_id)
        
        # Core overall monolithic embedding
        summary = generate_vector_input(profile.data_content)
        profile.embedding = get_embedding(summary)
        
        # Future multi-vector chunking
        skills = profile.skills
        if skills:
            skill_text = ", ".join([str(s.get('name') if isinstance(s, dict) else s) for s in skills])
            profile.embedding_skills = get_embedding(f"Skills: {skill_text}")
            
        experiences = profile.experiences
        if experiences:
            exp_text = " ".join([f"{e.get('title')} at {e.get('company')} - {e.get('description', '')}" for e in experiences])
            profile.embedding_experience = get_embedding(exp_text)
            
        education = profile.education
        if education:
            edu_text = " ".join([f"{e.get('degree')} in {e.get('field')} from {e.get('institution')}" for e in education])
            profile.embedding_education = get_embedding(edu_text)
            
        # Update specific fields to avoid overwriting other parallel saves
        profile.save(update_fields=['embedding', 'embedding_skills', 'embedding_experience', 'embedding_education'])
        logger.info(f"Successfully generated embeddings for profile {profile_id}")
        
    except UserProfile.DoesNotExist:
        logger.error(f"Cannot generate embeddings: Profile {profile_id} does not exist.")
    except Exception as e:
        logger.error(f"Failed to generate embeddings for profile {profile_id}: {e}")

def generate_job_embeddings(job_id):
    """
    Background task to generate all 384-dimensional vector embeddings for a Job.
    """
    try:
        job = Job.objects.get(id=job_id)
        
        job_text = f"{job.title} at {job.company or ''}. {job.description}"
        job.embedding = get_embedding(job_text)
        
        # We also clear phase-1 sub-vectors in Job bust, so we don't necessarily generate them here unless needed,
        # but just saving the monolithic one is what the system requires right now.
        
        job.save(update_fields=['embedding'])
        logger.info(f"Successfully generated embedding for job {job_id}")
        
    except Job.DoesNotExist:
        logger.error(f"Cannot generate embeddings: Job {job_id} does not exist.")
    except Exception as e:
        logger.error(f"Failed to generate embeddings for job {job_id}: {e}")
