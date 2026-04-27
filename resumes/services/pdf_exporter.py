def generate_pdf(resume_obj, output_path, template_name='standard'):
    """
    Generate PDF from resume object using xhtml2pdf.

    Honors the user's saved section_order from resume.content if present,
    falling back to the default order otherwise. Templates iterate over
    `section_order` in their body so the rendered PDF matches the live
    preview's stacking on the edit page.
    """
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from resumes.views import RESUME_SECTION_KEYS, DEFAULT_SECTION_ORDER

    if template_name and template_name != 'standard':
        template_file = f'resumes/pdf_template_{template_name}.html'
    else:
        template_file = 'resumes/pdf_template.html'

    user = resume_obj.gap_analysis.job.user
    profile = user.profile

    # Resolve the section order with the same defensive logic as the edit
    # view: validate saved keys against the whitelist, fill in any missing
    # ones at the end so a partial order still produces a complete PDF.
    saved = (resume_obj.content or {}).get('section_order') or []
    valid_saved = [s for s in saved if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    html_string = render_to_string(template_file, {
        'resume': resume_obj.content,
        'user': user,
        'profile': profile,
        'section_order': section_order,
    })

    with open(output_path, "w+b") as result_file:
        pisa_status = pisa.CreatePDF(html_string, dest=result_file)

    if pisa_status.err:
        raise Exception(f"Failed to generate PDF with xhtml2pdf: {pisa_status.err}")

    return output_path
