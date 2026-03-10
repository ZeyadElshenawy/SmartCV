import os
import django
import sys
import traceback
from django.conf import settings

# Setup minimal django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "smartcv.settings")
django.setup()

from django.template.loader import render_to_string
from xhtml2pdf import pisa
from io import BytesIO

try:
    html = render_to_string('resumes/pdf_template.html', {'resume': {
        'professional_title': 'Test Title',
        'professional_summary': 'Test Summary',
        'skills': ['Python', 'Django'],
        'experience': [{'title': 'Dev', 'company': 'TestCo', 'duration': '2020-2022', 'description': 'Did things.'}],
        'education': [{'degree': 'BS', 'institution': 'TestU', 'year': '2020'}],
    }})
    
    pdf = BytesIO()
    pisa.CreatePDF(html, dest=pdf)
    print("Success! Size:", len(pdf.getvalue()))
except Exception as e:
    with open('error.log', 'w') as f:
        traceback.print_exc(file=f)
    print("Error saved to error.log")
