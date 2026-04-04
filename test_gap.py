import os
import sys
import time

sys.path.append(os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
import django
django.setup()

from profiles.models import UserProfile
from jobs.models import Job
from analysis.services.gap_analyzer import compute_gap_analysis

print("Fetching latest profile and job...")
profile = UserProfile.objects.first()
job = Job.objects.first()

if not profile or not job:
    print("No profile or job in DB.")
    sys.exit()

print(f"Testing Gap Analysis for {profile.full_name} and {job.title}...")
start = time.time()
res = compute_gap_analysis(profile, job)
print(f"Finished in {time.time() - start:.2f}s")
print("Similarity Score:", res.get('similarity_score'))
