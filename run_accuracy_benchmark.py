
import os
import sys
import django
import glob
import pandas as pd
import time
from pathlib import Path

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from profiles.services.cv_parser import CVExtractor

def run_benchmark():
    # cv_dir = Path('media/cvs')
    # Using absolute path to be safe based on previous context, but relative should work if CWD is project root
    cv_dir = Path(os.getcwd()) / 'media' / 'cvs'
    
    cv_files = list(cv_dir.glob('*.*'))
    # Filter for supported extensions
    cv_files = [f for f in cv_files if f.suffix.lower() in ['.pdf', '.docx', '.txt']]
    
    print(f"🚀 Starting Benchmark on {len(cv_files)} CVs...")
    print(f"📂 Directory: {cv_dir}\n")
    
    extractor = CVExtractor()
    results = []
    
    start_time_all = time.time()
    
    for i, cv_path in enumerate(cv_files, 1):
        print(f"[{i}/{len(cv_files)}] Parsing: {cv_path.name}...")
        
        start_time = time.time()
        try:
            # Extract
            data = extractor.parse(str(cv_path))
            duration = time.time() - start_time
            
            # Check fields
            file_name = cv_path.name
            
            # Personal Info
            personal = data.get('personal_information', {})
            has_email = bool(personal.get('email'))
            has_phone = bool(personal.get('phone'))
            has_name = bool(personal.get('name'))
            has_linkedin = bool(personal.get('linkedin'))
            
            # Skills
            skills_dict = data.get('skills', {})
            # Sum up all lists in the skills dictionary
            try:
                skill_count = sum(len(v) for v in skills_dict.values() if isinstance(v, list)) 
            except:
                skill_count = 0
            
            # Experience
            experience = data.get('work_experience', [])
            exp_count = len(experience)
            
            # Education
            education = data.get('education', [])
            edu_count = len(education)
            
            results.append({
                'Filename': file_name,
                'Status': 'Success',
                'Time_Sec': round(duration, 2),
                'Has_Name': has_name,
                'Has_Email': has_email,
                'Has_Phone': has_phone,
                'Has_LinkedIn': has_linkedin,
                'Skill_Count': skill_count,
                'Experience_Count': exp_count,
                'Education_Count': edu_count
            })
            
        except Exception as e:
            duration = time.time() - start_time
            print(f"❌ Error parsing {cv_path.name}: {e}")
            results.append({
                'Filename': cv_path.name,
                'Status': 'Failed',
                'Time_Sec': round(duration, 2),
                'Has_Name': False,
                'Has_Email': False,
                'Has_Phone': False,
                'Has_LinkedIn': False,
                'Skill_Count': 0,
                'Experience_Count': 0,
                'Education_Count': 0
            })

    total_time = time.time() - start_time_all
    
    # Analyze Results
    df = pd.DataFrame(results)
    
    # Save CSV
    output_file = 'benchmark_results.csv'
    df.to_csv(output_file, index=False)
    
    # Calculate Metrics
    total_files = len(df)
    success_files = len(df[df['Status'] == 'Success'])
    
    if total_files > 0:
        email_success_rate = (df['Has_Email'].sum() / total_files) * 100
        phone_success_rate = (df['Has_Phone'].sum() / total_files) * 100
        linkedin_success_rate = (df['Has_LinkedIn'].sum() / total_files) * 100
        avg_skills = df['Skill_Count'].mean()
        avg_time = df['Time_Sec'].mean()
    else:
        email_success_rate = 0
        phone_success_rate = 0
        linkedin_success_rate = 0
        avg_skills = 0
        avg_time = 0

    print("\n" + "="*50)
    print("📊 BENCHMARK RESULTS SUMMARY")
    print("="*50)
    print(f"Total CVs Processed: {total_files}")
    print(f"Successful Parses:   {success_files} ({success_files/total_files*100:.1f}%)")
    print(f"Average Parse Time:  {avg_time:.2f} seconds")
    print("-" * 30)
    print(f"📧 Email Extraction Rate:    {email_success_rate:.1f}%")
    print(f"📱 Phone Extraction Rate:    {phone_success_rate:.1f}%")
    print(f"🔗 LinkedIn Extraction Rate: {linkedin_success_rate:.1f}%")
    print("-" * 30)
    print(f"🧠 Average Skills Found:     {avg_skills:.1f}")
    print("="*50)
    print(f"\nDetailed results saved to: {output_file}")

if __name__ == '__main__':
    run_benchmark()
