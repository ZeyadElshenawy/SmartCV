
import os
import sys
import django
import pandas as pd
import time

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from jobs.services.skill_extractor import extract_skills

TEST_CASES = [
    {
        "role": "Python Developer",
        "text": """
        We are looking for a Senior Python Developer.
        Must have experience with Django and Flask frameworks.
        Knowledge of PostgreSQL and Redis is required.
        Familiarity with Docker and AWS (EC2, S3) is a plus.
        """,
        "expected": {"Python", "Django", "Flask", "PostgreSQL", "Redis", "Docker", "AWS", "EC2", "S3"}
    },
    {
        "role": "Data Scientist",
        "text": """
        Join our AI team.
        Proficiency in Python, Pandas, NumPy, and Scikit-learn.
        Experience with Deep Learning (TensorFlow or PyTorch).
        Strong background in Statistics and Mathematics.
        """,
        "expected": {"Python", "Pandas", "NumPy", "Scikit-learn", "Deep Learning", "TensorFlow", "PyTorch", "Statistics", "Mathematics"}
    },
    {
        "role": "Frontend Developer",
        "text": """
        Seeking a React Developer.
        Expertise in JavaScript (ES6+), HTML5, and CSS3.
        Experience with Redux and React Native is beneficial.
        Knowledge of Webpack and Babel.
        """,
        "expected": {"React", "JavaScript", "HTML5", "CSS3", "Redux", "React Native", "Webpack", "Babel"}
    },
    {
        "role": "DevOps Engineer",
        "text": """
        DevOps Engineer needed.
        Hands-on experience with Kubernetes and CI/CD pipelines (Jenkins, GitLab CI).
        Scripting skills in Bash or Python.
        Experience with Terraform for IaC.
        """,
        "expected": {"DevOps", "Kubernetes", "CI/CD", "Jenkins", "GitLab CI", "Bash", "Python", "Terraform"}
    },
    {
        "role": "Project Manager",
        "text": """
        Looking for a Project Manager.
        Must check Agile and Scrum methodologies.
        Experience with JIRA and Confluence.
        Strong Communication and Leadership skills.
        PMP certification is a plus.
        """,
        "expected": {"Project Manager", "Agile", "Scrum", "JIRA", "Confluence", "Communication", "Leadership", "PMP"}
    }
]

def run_benchmark():
    results = []
    print("🚀 Starting Skill Extraction Benchmark...")
    print(f"Testing {len(TEST_CASES)} scenarios...\n")
    
    start_time_all = time.time()
    
    for case in TEST_CASES:
        print(f"Testing: {case['role']}...")
        start_time = time.time()
        
        # Run Extraction
        extracted = set(extract_skills(case['text']))
        duration = time.time() - start_time
        
        expected = case['expected']
        
        # Calculate Metrics
        # Note: Extraction might normalize case, but let's assume loose matching for now or normalized comparison?
        # Let's normalize to lower case for fair comparison
        extracted_lower = {s.lower() for s in extracted}
        expected_lower = {s.lower() for s in expected}
        
        matches = extracted_lower.intersection(expected_lower)
        
        true_positives = len(matches)
        false_positives = len(extracted_lower - expected_lower)
        false_negatives = len(expected_lower - extracted_lower)
        
        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        
        results.append({
            'Role': case['role'],
            'Time_ms': round(duration * 1000, 2),
            'Expected_Count': len(expected),
            'Extracted_Count': len(extracted),
            'Matches': true_positives,
            'Precision': round(precision * 100, 1),
            'Recall': round(recall * 100, 1),
            'F1_Score': round(f1 * 100, 1)
        })
        
        # Debug Mismatches
        if false_negatives > 0:
            print(f"  ❌ Missed: {expected_lower - extracted_lower}")
        if false_positives > 0:
            print(f"  ⚠️ Extra: {extracted_lower - expected_lower}")

    print("\n" + "="*60)
    
    df = pd.DataFrame(results)
    print(df.to_string(index=False))
    
    avg_precision = df['Precision'].mean()
    avg_recall = df['Recall'].mean()
    avg_f1 = df['F1_Score'].mean()
    avg_time = df['Time_ms'].mean()
    
    print("\n" + "="*30)
    print("📊 SKILL BENCHMARK SUMMARY")
    print("="*30)
    print(f"🎯 Average Precision: {avg_precision:.1f}% (Quality of matches)")
    print(f"🔍 Average Recall:    {avg_recall:.1f}% (Completeness)")
    print(f"⚖️ Average F1 Score:  {avg_f1:.1f}%")
    print(f"⚡ Average Latency:   {avg_time:.1f} ms")
    print("="*30)
    
    # Save for reference
    df.to_csv('benchmark_skills_results.csv', index=False)

if __name__ == "__main__":
    run_benchmark()
