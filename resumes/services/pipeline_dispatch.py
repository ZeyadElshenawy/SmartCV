"""Single dispatch point for production resume generation.

Both production trigger sites — ``resumes.tasks.generate_resume_task``
and ``resumes.views.trigger_resume_regeneration_api`` — call
``generate_resume_content_dispatched`` instead of going straight to v1.
The dispatcher honours ``settings.RESUME_GENERATOR_PIPELINE``:

  - ``'v1'`` (default): byte-for-byte identical to the prior call —
    delegates to ``generate_resume_content_supervised`` with the same
    arguments and returns its output unchanged.
  - ``'v2'``: runs the v2 evidence-first pipeline
    (extract -> classify -> KB -> plan -> generate -> review/regen),
    flattens through ``resume_v2_to_template_dict`` into the v1 dict
    shape callers already consume, and attaches a v1-shaped
    ``validation_report`` via the existing
    ``resume_reviewer_v2.build_v2_validation_report`` shim so the
    editor's findings panel keeps working unchanged.

Because the default is ``'v1'`` and both trigger sites already passed
through ``generate_resume_content_supervised``, introducing this
dispatcher is a no-op until the env var flips.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings


def generate_resume_content_dispatched(profile, job, gap_analysis, *,
                                       previous_best=None,
                                       pipeline: str | None = None) -> dict:
    """Dispatch resume generation to v1 or v2 based on the configured pipeline.

    Parameters mirror ``generate_resume_content_supervised`` so the two
    production call sites change only at the import + call line.

    ``pipeline`` overrides ``settings.RESUME_GENERATOR_PIPELINE``; intended
    for tests. Unknown values fall back to v1 (defensive — env-flip typos
    must not break production).
    """
    flag = pipeline or getattr(settings, "RESUME_GENERATOR_PIPELINE", "v1") or "v1"
    if flag == "v2":
        return _generate_via_v2(profile, job, gap_analysis)
    # 'v1' and any unrecognised value -> the legacy path, byte-for-byte.
    from .resume_generator import generate_resume_content_supervised
    return generate_resume_content_supervised(
        profile, job, gap_analysis, previous_best=previous_best,
    )


def _generate_via_v2(profile, job, gap_analysis) -> dict:
    """Run the full v2 pipeline and return a v1-shaped content dict.

    Output keys match v1's ``ResumeContentResult`` shape (professional_summary,
    skills, experience[], projects[], education[], certifications[], languages,
    plus ``validation_report``) so downstream consumers — calculate_ats_score,
    the PDF templates, the editor's findings panel — work unchanged.
    """
    from profiles.services.role_classifier import classify_for_jd
    from .fact_extractor import FactStore, extract_into_store
    from .kb_integration import (
        format_writing_rules_block,
        prefetch_kb_for_pipeline,
        split_kb_chunks,
    )
    from .resume_generator_v2 import (
        _synthesize_summary_from_sections,
        generate_resume_v2,
    )
    from .resume_planner_v2 import build_plan
    from .resume_reviewer_v2 import build_v2_validation_report, review_and_regenerate
    from .resume_v2_adapter import resume_v2_to_template_dict

    data_content: dict[str, Any] = dict(profile.data_content or {})
    jd_text = (getattr(job, "description", "") or "")

    store = FactStore()
    extract_into_store(store, "structured_profile", data_content=data_content)

    classification = classify_for_jd(data_content, jd_text)
    kb_chunks = prefetch_kb_for_pipeline(jd_text, classification)
    _calibration, phrasing = split_kb_chunks(kb_chunks)
    writing_rules_block = format_writing_rules_block(phrasing)

    tiers = getattr(job, "extracted_skills_tiers", None) or {}
    must_have = list(tiers.get("must_have") or getattr(job, "extracted_skills", None) or [])
    nice_to_have = list(tiers.get("nice_to_have") or [])

    # Fix A: self-reported skills the user moved into "matched" on the gap page
    # WITHOUT profile evidence (fix C's user_asserted marker on the persisted
    # GapAnalysis row). These surface in the skills LINE only — never a fact,
    # never a bullet, never the summary's grounded pool. Evidenced matches are
    # not flagged and keep their normal fact-driven behaviour.
    user_asserted_skills = [
        c["name"] for c in
        ((gap_analysis.matched_must_have or []) + (gap_analysis.matched_nice_to_have or []))
        if isinstance(c, dict) and c.get("user_asserted") and c.get("name")
    ] if gap_analysis else []

    plan = build_plan(
        store,
        job_must_have_skills=must_have,
        job_nice_to_have_skills=nice_to_have,
        job_description=jd_text,
        classification=classification,
        kb_chunks=kb_chunks,
    )

    resume_v2 = generate_resume_v2(
        store,
        plan,
        job_title=getattr(job, "title", "") or "",
        job_company=getattr(job, "company", "") or "",
        kb_chunks=kb_chunks,
        user_asserted_skills=user_asserted_skills,
    )

    revised, _report = review_and_regenerate(
        resume_v2,
        store=store,
        plan=plan,
        job_title=getattr(job, "title", "") or "",
        writing_rules_block=writing_rules_block,
    )

    # Layer 5 Full — synthesise the professional summary AFTER the
    # reviewer settles, fed from the post-reviewer sections (only
    # facts that survived into a rendered bullet). Empty harvested
    # pool → summary stays empty → adapter warns and omits. The helper
    # applies the same number-lock + a one-shot banned-openings
    # re-check that mirrors the reviewer's contract, so the gap from
    # synthesising after the reviewer's pass is closed.
    revised = _synthesize_summary_from_sections(
        revised,
        store=store,
        job_title=getattr(job, "title", "") or "",
        job_company=getattr(job, "company", "") or "",
        writing_rules_block=writing_rules_block,
        jd_must=must_have,
        jd_nice=nice_to_have,
    )

    # The adapter's source-merge looks for ``experience`` / ``projects``
    # keys; profile.data_content stores experience under the plural
    # ``experiences``. Alias before adapting so the metadata-passthrough
    # (company, location, dates, is_current, …) flows through for the
    # title+company match chain.
    source = dict(data_content)
    if "experience" not in source and source.get("experiences"):
        source["experience"] = source["experiences"]

    content = resume_v2_to_template_dict(revised, source=source)

    # validation_report — emit the v1-shaped keys the existing
    # findings_classifier + findings_presenter already consume. v2's
    # shim was built for exactly this; no UI change required.
    content["validation_report"] = build_v2_validation_report(revised)

    return content
