import logging
from celery import shared_task
from django.contrib.auth import get_user_model
from jobs.models import RecommendedJob
from analysis.services.gap_analyzer import compute_gap_analysis
from profiles.models import UserProfile
# from jobs.services.linkedin_scraper import scrape_linkedin_jobs  # Assuming this exists or will be implemented

logger = logging.getLogger(__name__)
User = get_user_model()

@shared_task
def run_daily_job_matcher():
    """
    Background task to find jobs for all users and score them.
    In a real app, this would use a service like SerpAPI or a LinkedIn scraper.
    """
    users = User.objects.all()
    
    for user in users:
        try:
            profile = UserProfile.objects.get(user=user)
            if not profile.skills:
                continue
                
            # MOCK IMPLEMENTATION:
            # 1. Fetch jobs based on user's top skills & location
            # search_query = f"{' '.join(profile.skills[:3])} {profile.location}"
            # raw_jobs = scrape_linkedin_jobs(search_query, limit=5)
            
            raw_jobs = [
                {
                    "url": "https://linkedin.com/jobs/view/mock1",
                    "title": "Senior " + profile.skills[0] if profile.skills else "Software Engineer",
                    "company": "Tech Innovators Inc",
                    "description": f"Looking for someone with {', '.join(profile.skills[:3])}. We are a fast paced startup..."
                }
            ] # Mock data
            
            # 2. Score jobs against profile
            for job_data in raw_jobs:
                # Check if already processed
                if RecommendedJob.objects.filter(user=user, url=job_data['url']).exists():
                    continue
                    
                # Mock a Job object for the analyzer
                from types import SimpleNamespace
                mock_job = SimpleNamespace(
                    title=job_data['title'],
                    description=job_data['description'],
                    extracted_skills=[] 
                )
                
                analysis = compute_gap_analysis(profile, mock_job)
                score = int(analysis['similarity_score'] * 100)
                
                # Only save good matches (e.g., > 60%)
                if score >= 60:
                    RecommendedJob.objects.create(
                        user=user,
                        url=job_data['url'],
                        title=job_data['title'],
                        company=job_data['company'],
                        description=job_data['description'],
                        match_score=score
                    )
                    logger.info(f"Found match for {user.email}: {job_data['title']} ({score}%)")
                    
        except Exception as e:
            logger.error(f"Error matching jobs for user {user.id}: {e}")
            continue
