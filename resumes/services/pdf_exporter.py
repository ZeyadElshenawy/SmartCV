from django.template.loader import render_to_string
from xhtml2pdf import pisa
import os

def generate_pdf(resume_content, output_path):
    """
    Generate PDF from resume content using xhtml2pdf
    """
    html_string = render_to_string('resumes/pdf_template.html', {
        'resume': resume_content
    })
    
    with open(output_path, "w+b") as result_file:
        pisa_status = pisa.CreatePDF(html_string, dest=result_file)
        
    if pisa_status.err:
        raise Exception(f"Failed to generate PDF with xhtml2pdf: {pisa_status.err}")
        
    return output_path
