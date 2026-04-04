def generate_pdf(resume_obj, output_path, template_name='standard'):
    """
    Generate PDF from resume object using xhtml2pdf
    """
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    
    if template_name and template_name != 'standard':
        template_file = f'resumes/pdf_template_{template_name}.html'
    else:
        template_file = 'resumes/pdf_template.html'
        
    user = resume_obj.gap_analysis.job.user
    profile = user.profile
        
    html_string = render_to_string(template_file, {
        'resume': resume_obj.content,
        'user': user,
        'profile': profile
    })
    
    with open(output_path, "w+b") as result_file:
        pisa_status = pisa.CreatePDF(html_string, dest=result_file)
        
    if pisa_status.err:
        raise Exception(f"Failed to generate PDF with xhtml2pdf: {pisa_status.err}")
        
    return output_path
