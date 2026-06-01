"""PDF rendering for tailored resumes via WeasyPrint.

The same rendered HTML string the previous xhtml2pdf path produced is
fed to WeasyPrint, which writes the PDF to ``output_path``. The
templates and their section-order resolution are unchanged.
"""


def generate_pdf(resume_obj, output_path, template_name='standard'):
    """Generate PDF from resume object using WeasyPrint.

    Honors the user's saved section_order from resume.content if present,
    falling back to the default order otherwise. Templates iterate over
    `section_order` in their body so the rendered PDF matches the live
    preview's stacking on the edit page.
    """
    from django.template.loader import render_to_string
    from weasyprint import HTML
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

    HTML(string=html_string).write_pdf(output_path)

    return output_path
