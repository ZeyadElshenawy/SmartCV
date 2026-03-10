import os
import sys
import django

# Setup Django environment to allow importing app modules
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from jobs.services.skill_extractor import extract_skills

SAMPLE_DESCRIPTION = """
We are looking for a Senior Python Developer to join our team.
You will be working with Django and FastApi to build scalable APIs.
Experience with PostgreSQL and Redis is required.
Knowledge of React or TypeScript is a plus.
You should be familiar with Docker and Kubernetes for deployment.
We use AWS for cloud infrastructure.
Strong problem solving skills and communication are essential.
"""

print("--- Testing Skill Extraction ---")
print("Input Text:", SAMPLE_DESCRIPTION.strip())
print("-" * 30)

try:
    skills = extract_skills(SAMPLE_DESCRIPTION)
    print(f"Extracted {len(skills)} skills:")
    for skill in skills:
        print(f"- {skill}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
