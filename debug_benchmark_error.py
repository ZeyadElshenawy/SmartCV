
import os
import sys
import django
import traceback
from pathlib import Path

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from profiles.services.cv_parser import CVExtractor

def debug_single_file():
    try:
        cv_dir = Path(os.getcwd()) / 'media' / 'cvs'
        cv_files = list(cv_dir.glob('*.pdf'))
        if not cv_files:
            print("No PDF files found to test.")
            return

        target_file = cv_files[0]
        print(f"Debugging parsing for: {target_file}")

        extractor = CVExtractor()
        data = extractor.parse_cv(str(target_file))
        print("Success!")
        print(data)

    except Exception as e:
        print("Error occurred:")
        with open('debug_error.log', 'w') as f:
            f.write(traceback.format_exc())
        print(traceback.format_exc())

if __name__ == "__main__":
    debug_single_file()
