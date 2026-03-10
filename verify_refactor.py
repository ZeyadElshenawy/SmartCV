import os
import sys
import django
import json

# Setup env
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

def verify_gap_analysis():
    print("\n--- Verifying Hybrid Gap Analysis ---")
    try:
        from analysis.services.gap_analyzer import compute_gap_analysis
        from profiles.models import UserProfile
        from jobs.models import Job
        
        user = UserProfile.objects.first()
        job = Job.objects.first()
        
        if user and job:
            result = compute_gap_analysis(user, job)
            print(f"Result keys: {result.keys()}")
            if 'soft_skill_gaps' in result:
                print("PASS: Hybrid Analysis Returned Soft Skills")
            else:
                print("FAIL: Soft Skills missing from Gap Report")
        else:
            print("SKIP: No data to test gap analysis")
            
    except Exception as e:
        print(f"FAIL: Gap Analysis Error: {e}")

def verify_embedding_input():
    print("\n--- Verifying Vector Input Generation ---")
    try:
        from profiles.services.embeddings import generate_vector_input
        data = {
            "normalized_summary": "10 years exp Java Developer",
            "skills": ["Java", "Spring"],
            "experiences": [{"title": "Lead Dev", "company": "Tech", "description": "Built things"}],
            "projects": [{"name": "AI App", "description": "Smart CV"}]
        }
        vector_input = generate_vector_input(data)
        print(f"Vector Input Length: {len(vector_input)}")
        if "Summary: 10 years" in vector_input and "Current Role: Lead Dev" in vector_input:
            print("PASS: Vector Input formatted correctly")
        else:
            print(f"FAIL: Vector Input format incorrect: {vector_input[:50]}...")
            
    except Exception as e:
        print(f"FAIL: Embedding Gen Error: {e}")

from profiles.models import UserProfile
from jobs.models import Job
from analysis.services.gap_analyzer import compute_gap_analysis

def verify_schema():
    print("\n--- Verifying Database Schema ---")
    
    # 1. Check UserProfile Schema
    profile = UserProfile()
    if hasattr(profile, 'data_content'):
        print("✓ UserProfile has data_content")
    else:
        print("✗ UserProfile MISSING data_content")
        
    if hasattr(profile, 'embedding'):
        print("✓ UserProfile has embedding field")
    else:
        print("✗ UserProfile MISSING embedding field")

    # 2. Check Job Schema
    job = Job()
    if hasattr(job, 'embedding'):
        print("✓ Job has embedding field")
    else:
        print("✗ Job MISSING embedding field")
        
    # 3. Check Backward Compatibility
    print("\nChecking Backwards Compatibility:")
    try:
        profile.skills = [{"name": "Python", "proficiency": "Expert"}]
        print(f"✓ Setter for skills works. data_content: {profile.data_content}")
        print(f"✓ Getter for skills: {profile.skills}")
    except Exception as e:
        print(f"✗ Skills property failed: {e}")
        
    try:
        profile.experiences = [{"title": "Dev"}]
        print(f"✓ Setter for experiences works.")
    except Exception as e:
        print(f"✗ Experiences property failed: {e}")
        
    # 4. Check Gap Analysis Import/Signature
    print("\nChecking Gap Analysis:")
    try:
        # Mock objects
        profile.data_content = {'skills': [{'name': 'Python'}]}
        job.extracted_skills = ['Python', 'Java']
        
        # We need to save to test embedding generation usually, but we can test if the function accepts the objects
        # We just want to ensure it doesn't crash on import or basic call
        # Mock embedding/save to avoid DB hit here or failure
        # Actually proper test needs DB.
        pass
    except Exception as e:
        print(f"✗ Gap Analysis check failed: {e}")

    print("\nVerification Script Completed.")

if __name__ == "__main__":
    verify_schema()
    verify_gap_analysis()
    verify_embedding_input()
