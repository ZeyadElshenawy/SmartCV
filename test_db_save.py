import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from jobs.models import Job
from django.contrib.auth import get_user_model

def test_save():
    print("Getting User...")
    User = get_user_model()
    user = User.objects.first()
    if not user:
        print("No user found, creating one.")
        user = User.objects.create_user(username='test', email='test@example.com', password='password')
        
    print(f"User found: {user.email}")
    
    print("Creating Job...")
    try:
        job = Job.objects.create(
            user=user,
            title="Test Job",
            description="Test Description",
            extracted_skills=["Python"]
        )
        print(f"Job saved: {job.id}")
    except Exception as e:
        print(f"FAILED to save job: {e}")

if __name__ == "__main__":
    test_save()
