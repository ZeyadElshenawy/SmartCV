
import os
import sys
import django
import traceback
from pathlib import Path

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from profiles.services.cv_parser import CVExtractor

def debug_single_file_deep():
    try:
        cv_dir = Path(os.getcwd()) / 'media' / 'cvs'
        cv_files = list(cv_dir.glob('*.pdf'))
        if not cv_files:
            print("No PDF files found.")
            return

        target_file = cv_files[0]
        print(f"📄 Target: {target_file.name}")

        extractor = CVExtractor(use_spacy=True) # Ensure spacy is ON
        
        # 1. Check raw text extraction
        print("\n[Step 1] Raw Text Extraction:")
        raw_text = extractor.extract_text(str(target_file))
        print(f"Raw Text Length: {len(raw_text)}")
        print(f"Preview: {raw_text[:200]}...")
        
        # 2. Check complete parse
        print("\n[Step 2] Full Parse:")
        data = extractor.parse(str(target_file))
        print(f"Parsed Data Keys: {list(data.keys())}")
        print(f"Personal Info: {data.get('personal_info')}")
        print(f"Skills ({len(data.get('skills', []))}): {data.get('skills', [])}")

    except Exception as e:
        print(traceback.format_exc())

if __name__ == "__main__":
    debug_single_file_deep()
