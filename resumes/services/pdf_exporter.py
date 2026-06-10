"""PDF rendering for tailored resumes via WeasyPrint.

The two surviving themes are built ATS-clean by construction:
``ats_clean`` (black-only) and ``ats_clean_accent`` (one restrained
accent color). Both extend ``templates/resumes/pdf_base.html`` so the
ATS guarantees live in one place.

Existing resumes that pinned a removed theme name in
``content['template_name']`` (standard, executive, minimalist, compact,
danette, zeyad) migrate through ``THEME_MIGRATION`` to a surviving
theme rather than 500-ing on a missing template file.
"""


LIVE_THEMES = frozenset({
    "ats_clean", "ats_clean_accent",      # original pair (B&W / one-accent)
    "ats_dense", "ats_spacious", "ats_strict",  # Stage 2 — each a pdf_template_{name}.html
})

# Old theme name → surviving theme. The two old "color" themes
# (danette, zeyad) map to the accent variant so the user's prior intent
# is preserved; B&W themes map to ats_clean. Unknown / empty values are
# handled by the resolver's fall-through to ats_clean (never raises).
THEME_MIGRATION = {
    "standard":   "ats_clean",
    "executive":  "ats_clean",
    "minimalist": "ats_clean",
    "compact":    "ats_clean",
    "danette":    "ats_clean_accent",
    "zeyad":      "ats_clean_accent",
}

DEFAULT_THEME = "ats_clean"


def resolve_template(template_name):
    """Map a user-saved ``template_name`` to ``(theme_key, template_file)``.

    The resolver NEVER raises on an unknown / empty value — anything not
    in ``LIVE_THEMES`` or ``THEME_MIGRATION`` falls through to
    ``DEFAULT_THEME`` so an old or hand-edited ``template_name`` doesn't
    500 the export.
    """
    name = (template_name or "").strip()
    if name in LIVE_THEMES:
        theme = name
    elif name in THEME_MIGRATION:
        theme = THEME_MIGRATION[name]
    else:
        theme = DEFAULT_THEME
    return theme, f"resumes/pdf_template_{theme}.html"


def generate_pdf(resume_obj, output_path, template_name=DEFAULT_THEME):
    """Generate PDF from a ``GeneratedResume`` using WeasyPrint.

    Honors the user's saved section_order from resume.content if present,
    falling back to the default order otherwise. Templates iterate over
    `section_order` in their body so the rendered PDF matches the live
    preview's stacking on the edit page.
    """
    from django.template.loader import render_to_string
    from weasyprint import HTML
    from resumes.views import RESUME_SECTION_KEYS, DEFAULT_SECTION_ORDER
    from .skill_categorizer import group_skills_for_display, should_show_grouped
    from .resume_normalizer import (
        heal_experience_durations, sort_experience_reverse_chronological,
    )

    theme, template_file = resolve_template(template_name)

    user = resume_obj.gap_analysis.job.user
    profile = user.profile
    content = resume_obj.content or {}
    # Defensive heal: stored "X - Present" durations on records without
    # is_current=True (legacy LLM fabrications) are recomputed to the
    # honest representation, then re-sorted so a stale role no longer
    # outranks a genuinely newer one on the strength of a fabricated
    # end date. Non-current records that were already honest pass
    # through unchanged.
    if content.get("experience"):
        content = {**content, "experience": heal_experience_durations(content["experience"])}
        content = sort_experience_reverse_chronological(content)

    saved = content.get("section_order") or []
    valid_saved = [s for s in saved if s in RESUME_SECTION_KEYS]
    section_order = valid_saved + [s for s in DEFAULT_SECTION_ORDER if s not in valid_saved]

    skills_list = content.get("skills") or []
    skill_groups = group_skills_for_display(skills_list)
    show_grouped_skills = should_show_grouped(skill_groups, len(skills_list))

    html_string = render_to_string(template_file, {
        "resume": content,
        "user": user,
        "profile": profile,
        "section_order": section_order,
        "skill_groups": skill_groups,
        "show_grouped_skills": show_grouped_skills,
        "theme": theme,
    })

    HTML(string=html_string).write_pdf(output_path)

    return output_path
