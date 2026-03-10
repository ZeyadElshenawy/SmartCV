import os
import sys
import django
import traceback

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smartcv.settings")
django.setup()

from resumes.models import GeneratedResume
from resumes.services.pdf_exporter import generate_pdf
import tempfile

try:
    resume = GeneratedResume.objects.get(id="82649a9c-ec9b-4231-a5db-f4eaedf2e386")
    print("Found resume! Parsing...")
    
    fd, output_path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd)
    
    generate_pdf(resume.content, output_path)
    print("Success! Saved to", output_path)
    
except Exception as e:
    print("Error:", str(e))
    traceback.print_exc()
