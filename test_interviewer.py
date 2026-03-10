import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from dotenv import load_dotenv
load_dotenv(override=True)

print("=== Testing Job-Aware Interviewer ===\n")

# Get sample data
from django.contrib.auth import get_user_model
from jobs.models import Job

User = get_user_model()
user = User.objects.first()
job = Job.objects.first()

if not user or not job:
    print("❌ Need at least one user and one job in database")
    sys.exit(1)

print(f"✓ User: {user.username}")
print(f"✓ Job: {job.title}")
print(f"✓ Required Skills: {job.extracted_skills}\n")

# Test get_job_aware_question
print("--- Test 1: Get Initial Question ---")
try:
    from profiles.services.interviewer import get_job_aware_question
    
    question, topic, complete = get_job_aware_question(
        user_id=user.id,
        job_id=str(job.id),
        conversation_history=[]
    )
    
    print(f"✓ Question: {question}")
    print(f"✓ Topic: {topic}")
    print(f"✓ Complete: {complete}\n")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test process_conversational_reply
print("--- Test 2: Process Reply ---")
try:
    from profiles.services.interviewer import process_conversational_reply
    
    result = process_conversational_reply(
        user_id=user.id,
        job_id=str(job.id),
        user_reply="I have 5 years of Python experience with Django and Flask"
    )
    
    print(f"✓ Skills Extracted: {result.get('extracted_skills')}")
    print(f"✓ Profile Updated: {result.get('profile_updated')}")
    print(f"✓ Insights: {result.get('insights')}\n")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("=== Test Complete ===")
