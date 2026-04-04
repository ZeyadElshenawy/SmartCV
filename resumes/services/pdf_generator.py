"""
PDF Generator Service
Generates tailored PDFs from profile data_content JSON
"""
import logging
from io import BytesIO

logger = logging.getLogger(__name__)


def generate_optimized_pdf(profile, job=None):
    """
    Generate a tailored PDF resume from profile data_content.
    
    Args:
        profile: UserProfile instance
        job: Optional Job instance to highlight matching skills
        
    Returns:
        BytesIO: PDF file buffer
    """
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    
    # Prepare context for template
    context = {
        'profile': profile,
        'full_name': profile.full_name or 'Your Name',
        'email': profile.email or '',
        'phone': profile.phone or '',
        'location': profile.location or '',
        'linkedin_url': profile.linkedin_url or '',
        'github_url': profile.github_url or '',
        'summary': profile.data_content.get('summary', ''),
        'skills': profile.skills or [],
        'experiences': profile.experiences or [],
        'education': profile.education or [],
        'projects': profile.projects or [],
        'certifications': profile.certifications or [],
    }
    
    # Add job-specific highlighting
    if job:
        job_skills = set(job.extracted_skills or [])
        context['job_skills'] = job_skills
        context['job_title'] = job.title
        context['job_company'] = job.company
    else:
        context['job_skills'] = set()
    
    # Add dynamic sections from data_content
    standard_keys = {'full_name', 'email', 'phone', 'location', 'linkedin_url', 'github_url', 
                     'summary', 'normalized_summary', 'skills', 'experiences', 'education', 
                     'projects', 'certifications'}
    
    extra_sections = {}
    for key, value in profile.data_content.items():
        if key not in standard_keys and value:
            # Convert snake_case to Title Case
            section_title = key.replace('_', ' ').title()
            extra_sections[section_title] = value
    
    context['extra_sections'] = extra_sections
    
    # Render HTML template
    html_string = render_to_string('resumes/resume_template.html', context)
    
    # Convert to PDF
    pdf_buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer)
    
    if pisa_status.err:
        logger.error(f"PDF generation error: {pisa_status.err}")
        raise Exception(f"Failed to generate PDF: {pisa_status.err}")
        
    pdf_buffer.seek(0)
    
    logger.info(f"Generated PDF for {profile.full_name}")
    return pdf_buffer
