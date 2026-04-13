import os
import sys
import json
import time

# Ensure we can import Django stuff
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

import django
django.setup()

from profiles.services.cv_parser import parse_cv
from profiles.services.llm_validator import validate_and_map_cv_data

def run_tests():
    data_dir = r"g:\New folder\test cvs2"
    all_files = [f for f in os.listdir(data_dir) if f.endswith('.pdf')]
    
    # Shuffle and pick 5 random to test
    import random
    random.shuffle(all_files)
    cv_files = all_files[:5]
    
    results = []
    
    print(f"Starting batch process for {len(cv_files)} CVs with API throttling...")
    
    for idx, filename in enumerate(cv_files):
        filepath = os.path.join(data_dir, filename)
        start_time = time.time()
        
        try:
            print(f"[{idx+1}/{len(cv_files)}] Processing {filename}...")
            parsed_data = parse_cv(filepath)
            raw_cv_text = parsed_data.get('raw_text', '')
            
            validated_data = validate_and_map_cv_data(parsed_data, raw_cv_text)
            
            elapsed = time.time() - start_time
            
            # Simple metrics collection
            metrics = {
                'filename': filename,
                'elapsed_time_sec': round(elapsed, 2),
                'status': 'success',
                'fields_populated': {
                    'full_name': bool(validated_data.get('full_name')),
                    'email': bool(validated_data.get('email')),
                    'phone': bool(validated_data.get('phone')),
                    'skills_count': len(validated_data.get('skills', [])),
                    'experiences_count': len(validated_data.get('experiences', [])),
                    'education_count': len(validated_data.get('education', [])),
                    'projects_count': len(validated_data.get('projects', [])),
                },
                'raw_validated_keys': list(validated_data.keys()),
                'data': validated_data  # save full data to analyze flaws
            }
            results.append(metrics)
            print(f"✓ Completed {filename} in {elapsed:.2f}s")
            
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"✗ Failed {filename} in {elapsed:.2f}s: {e}")
            results.append({
                'filename': filename,
                'elapsed_time_sec': round(elapsed, 2),
                'status': 'error',
                'error_msg': str(e)
            })
            
        if idx < len(cv_files) - 1:
            print("Sleeping 10s to respect API rate limits...")
            time.sleep(10)
            
    # Save the report
    with open('batch_results.json', 'w', encoding='utf-8') as f:
         json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nBatch analysis completed. Saved to batch_results.json")

if __name__ == '__main__':
    run_tests()
