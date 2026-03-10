import os
import sys
import django
import time

# Setup env
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from jobs.services.linkedin_scraper import scrape_linkedin_job
from jobs.services.skill_extractor import extract_skills

def debug_flow():
    url = "https://www.linkedin.com/jobs/view/432361" # Truncated from screenshot, just testing if it connects at all or fails fast
    # Real URL likely: https://www.linkedin.com/jobs/view/4323610000 or similar.
    # I'll just use a generic valid one or the one from the screenshot if I can guess the ID.
    # Screenshot says: ...currentJobId=432361... 
    
    print("Testing Scraper...")
    start = time.time()
    try:
        # Just test connection to linkedin jobs generally
        test_url = "https://www.linkedin.com/jobs" 
        print(f"Connecting to {test_url}...")
        job_data = scrape_linkedin_job(test_url)
        print("Scraper finished.")
        
        from jobs.models import Job
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.first() # Grab first user
        
        if user:
            print("Saving Job to DB...")
            skills = extract_skills(job_data.get('description', ''))
            job = Job.objects.create(
                user=user,
                url=test_url,
                title=job_data.get('title', 'Test'),
                company=job_data.get('company', 'Test'),
                description=job_data.get('description', 'Test'),
                extracted_skills=skills
            )
            print(f"Job saved successfully: {job.id}")
            print(f"Embedding field: {job.embedding}") # Should be None or None-ish
        else:
            print("No user found to save job.")
            
    except Exception as e:
        print(f"Flow failed: {e}")
    
    print(f"Time taken: {time.time() - start:.2f}s")

if __name__ == "__main__":
    debug_flow()
