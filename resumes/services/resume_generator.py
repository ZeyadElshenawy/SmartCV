import json
import logging
import re
from typing import Dict, Any, Optional
from profiles.services.llm_engine import get_structured_llm, get_llm
from profiles.services.schemas import ResumeContentResult, ResumeExperience, ResumeProject
from profiles.services.prompt_guards import HUMAN_VOICE_RULE
from profiles.services.profile_sanitizer import sanitize_profile_data
from resumes.services.inclusion_planner import (
    InclusionPlan, build_inclusion_plan,
)
from resumes.services.resume_normalizer import normalize_resume

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bullet quality + metric-fabrication safety. Shared by:
#   - the main generation prompt (generate_resume_content)
#   - the per-section regen prompt (regenerate_section)
# Both prompts MUST embed this constant verbatim so the per-section
# "↻ Regenerate bullets" button (which used to ship without the safety
# rules) can't open a fabrication hole the main path closed.
# ---------------------------------------------------------------------------
BULLET_QUALITY_AND_SAFETY_RULES = """ACHIEVEMENT SHAPE (this is the difference between a strong resume and a job-description rewrite):
Every bullet must read as a RESULT, not a duty. The shape is:
  [Strong action verb] + [What you did, briefly] + [Concrete outcome — a result, a deliverable, a metric, a scope marker]
- "What you did" is the SHORT middle. "Outcome" is the load-bearing tail and must reference something real: a metric (only when it belongs to THIS item — see SAFETY rules below), a shipped artifact, a downstream effect, a scope number (rows / users / models / regions).
- When the source has no number for this item, the outcome stays qualitative but stays concrete: name the artifact, the tool integrated, the audience reached, the problem the work solved. "Honest qualitative bullet beats fake quantitative bullet" — never reach for a number that isn't there.

WEAK SHAPES TO AVOID (these are duty descriptions, not achievements):
- "Contributed to <project> by <doing X>" — drop "contributed to"; lead with the verb that captures the actual work, and add the result.
- "Applied <technique> to <data>" — name what the application achieved, not just that it was applied.
- "Worked on / Helped with / Assisted in / Participated in / Took part in / Engaged in / Involved in <X>" — same fix.
- "Responsible for / Tasked with / In charge of / Duties included <X>" — pure duty framing; rewrite as a verb + outcome.
- "Developed and evaluated <models>" / "Built and tested <X>" — compound verbs that hide the outcome. Pick the load-bearing verb and add the result.
- "Demonstrating proficiency in / showcasing experience with <tool>" — the bullet's tail must be a real outcome, not a meta-claim about skill.

SAFETY RULES FOR METRICS (NON-NEGOTIABLE — fabrication is the worst failure for a resume tool):
- Use ONLY metrics that already exist in the source data for THIS specific item. Look in the
  V2 GROUNDING block (per-skill evidence chunks pulled from the candidate's own profile) and in
  the experience/project fields. Numbers in the JD or in other items are OFF-LIMITS for this item.
- NEVER move a metric from one item to another. A silhouette score that belongs to project A
  must not appear on project B, experience C, or anywhere else. Cross-attaching = fabrication.
- NEVER invent a number, percentage, count, scale, duration, team size, or any other quantitative
  claim that is not literally present in the source for this item. "Approximate" or "round" fabrication
  is still fabrication.
- If an item has NO real metric, write the bullet with a stronger verb and a clearer qualitative
  outcome — but add NO number. The grounding validator will flag any invented metric and the
  finding will surface to the user; don't gamble on the validator missing one.

- Start every bullet within ONE role/project with a DIFFERENT action verb (and don't reuse a verb
  across adjacent roles in the same resume when you can help it).
- Preferred action verbs by intent: Built / Designed / Engineered / Shipped / Launched / Delivered
  (creation); Reduced / Improved / Accelerated / Cut / Automated (optimization); Led / Owned /
  Coordinated / Mentored / Drove (leadership); Analyzed / Investigated / Diagnosed / Profiled
  (analysis). Use one with a real OBJECT, not one with a vague generalization."""


# --- Domain detection ---------------------------------------------------------
# Keyword-based classifier. Cheap, deterministic, no LLM call. If nothing
# matches we fall back to 'general' and the prompt stays neutral.

_DOMAIN_KEYWORDS = {
    'software_engineering': [
        'software engineer', 'software developer', 'backend', 'back-end',
        'frontend', 'front-end', 'fullstack', 'full-stack', 'full stack',
        'web developer', 'mobile developer', 'ios developer', 'android developer',
        'devops', 'sre', 'site reliability', 'platform engineer',
        'systems engineer', 'embedded', 'game developer',
    ],
    'data': [
        'data scientist', 'data engineer', 'data analyst', 'machine learning',
        'ml engineer', 'ai engineer', 'ai/ml', 'analytics engineer',
        'business intelligence', 'bi analyst', 'bi developer',
        'research scientist', 'quantitative', 'statistician',
    ],
    'design': [
        'ux designer', 'ui designer', 'ux/ui', 'product designer',
        'graphic designer', 'visual designer', 'interaction designer',
        'motion designer', 'brand designer', 'web designer',
    ],
    'product': [
        'product manager', 'product owner', 'program manager',
        'technical program manager', 'tpm', 'chief of staff',
    ],
    'marketing': [
        'marketing manager', 'growth marketer', 'content marketing',
        'seo specialist', 'seo manager', 'digital marketing', 'marketing analyst',
        'brand manager', 'social media manager', 'performance marketing',
    ],
    'sales': [
        'account executive', 'sales development', 'sdr', 'bdr',
        'account manager', 'sales manager', 'customer success',
        'solutions consultant', 'sales engineer',
    ],
    'finance': [
        'financial analyst', 'investment banking', 'controller', 'cfo',
        'accountant', 'auditor', 'treasurer', 'financial planner',
        'equity research', 'portfolio manager',
    ],
}

_DOMAIN_PROMPTS = {
    'software_engineering': (
        "=== DOMAIN EMPHASIS: SOFTWARE ENGINEERING ===\n"
        "- Lead bullets with shipped systems, scale, and tech stack.\n"
        "- Highlight: languages/frameworks used, scale metrics (QPS, users, rows, uptime),\n"
        "  latency/perf improvements, system design decisions, test/deploy pipelines.\n"
        "- Prefer concrete verbs: Built, Implemented, Shipped, Deployed, Refactored, Optimized, Debugged.\n"
        "- Skills section: name the exact tools (Python 3, PostgreSQL, Kubernetes, Redis, AWS Lambda)."
    ),
    'data': (
        "=== DOMAIN EMPHASIS: DATA / ML ===\n"
        "- Lead bullets with business impact first, method second.\n"
        "  Example: 'Cut churn 12% by building a retention model in PyTorch trained on 2M events.'\n"
        "- Name models, libraries, and datasets explicitly (XGBoost, scikit-learn, TensorFlow, pandas, Snowflake, dbt).\n"
        "- Preferred verbs: Modelled, Predicted, Forecasted, Validated, Deployed, Instrumented, Analyzed.\n"
        "- Keep statistical rigor: 'AUC 0.87 on held-out set' beats 'accurate model'.\n"
        "- If the role is analyst-track, emphasize dashboards, SQL, stakeholder storytelling."
    ),
    'design': (
        "=== DOMAIN EMPHASIS: DESIGN ===\n"
        "- Lead bullets with user outcomes, not deliverables.\n"
        "  Example: 'Redesigned onboarding; activation rose 24% across 3 release cycles.'\n"
        "- Mention process: research method (user interviews, A/B tests, usability studies), design artifacts (wireframes, prototypes, design systems).\n"
        "- Name tools (Figma, Sketch, Adobe XD, Framer, Principle) and collaboration context (worked with PM + 4 engineers).\n"
        "- Preferred verbs: Designed, Prototyped, Researched, Iterated, Shipped, Partnered, Defined.\n"
        "- Consider adding a 'Portfolio' link in the header if the candidate has one."
    ),
    'product': (
        "=== DOMAIN EMPHASIS: PRODUCT MANAGEMENT ===\n"
        "- Lead bullets with metrics moved and strategic scope.\n"
        "  Example: 'Owned checkout rewrite; conversion +8%, cart abandonment -15% in 2 quarters.'\n"
        "- Every bullet should answer: what did you ship, who benefited, what was the measurable outcome.\n"
        "- Preferred verbs: Led, Owned, Launched, Prioritized, Aligned, Discovered, Defined.\n"
        "- Signal cross-functional leadership (partnered with engineering/design/sales) without buzzwords.\n"
        "- Skills section: frameworks (JTBD, OKRs, RICE), tools (Amplitude, Mixpanel, Figma, Jira), domain depth."
    ),
    'marketing': (
        "=== DOMAIN EMPHASIS: MARKETING ===\n"
        "- Lead with the channel, the outcome, and the budget or reach.\n"
        "  Example: 'Ran paid search on $200K monthly spend; CPA dropped 30%, CAC < $42 for Q3.'\n"
        "- Quantify: impressions, conversions, CAC/LTV, channel mix, campaign ROI.\n"
        "- Name platforms (Google Ads, LinkedIn Ads, HubSpot, Marketo, Ahrefs, GA4).\n"
        "- Preferred verbs: Launched, Ran, Grew, Scaled, Tested, Converted, Attributed."
    ),
    'sales': (
        "=== DOMAIN EMPHASIS: SALES ===\n"
        "- Lead every bullet with a number: quota attainment, deal size, cycle length, pipeline coverage.\n"
        "  Example: 'Closed $1.4M ARR in year 1, 132% of quota, average deal size $85K.'\n"
        "- Quantify: quota %, ACV/ARR, win rate, ramp time, accounts managed.\n"
        "- Name tools (Salesforce, Outreach, Gong, LinkedIn Sales Navigator) and methodology (MEDDPICC, Sandler, Challenger).\n"
        "- Preferred verbs: Closed, Opened, Prospected, Negotiated, Expanded, Retained."
    ),
    'finance': (
        "=== DOMAIN EMPHASIS: FINANCE ===\n"
        "- Specify the models, deal sizes, and frameworks.\n"
        "  Example: 'Built 3-statement model for $120M acquisition; identified $8M in synergies.'\n"
        "- Quantify: AUM, deal size, P&L owned, forecast accuracy, audit scope.\n"
        "- Name tools (Excel with advanced formulas/VBA, SAP, NetSuite, Bloomberg, Capital IQ, Tableau) and frameworks (DCF, LBO, IFRS, GAAP).\n"
        "- Preferred verbs: Modelled, Forecasted, Analyzed, Audited, Reconciled, Advised, Valued."
    ),
}


def _detect_job_domain(job) -> str:
    """Return a domain key based on job title + description. 'general' if no clear match."""
    haystack = (f"{getattr(job, 'title', '')} {getattr(job, 'description', '')[:500]}").lower()
    if not haystack.strip():
        return 'general'
    # Score each domain by keyword hits and pick the winner
    best, best_score = 'general', 0
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in haystack)
        if score > best_score:
            best, best_score = domain, score
    return best


def _domain_prompt_section(domain: str) -> str:
    """Return the domain-specific prompt addendum, or empty string for 'general'."""
    return _DOMAIN_PROMPTS.get(domain, '')


def _build_evidence_context(profile, job, gap_analysis) -> str:
    """Compose the rich, source-labeled context block the LLM uses to write
    a genuinely tailored resume.

    Mirrors `analysis.services.gap_analyzer._build_full_candidate_context`
    in spirit, but labels every fact by source so the prompt can ask the
    LLM to corroborate enrichment claims against a specific source. The
    gap analyzer needs this for matching; the resume generator needs it
    so it can write things like "scaled to 12 production repos" only
    when GitHub actually shows that.

    Returns a multi-section text block. Empty when no signals/gap data
    is present (the prompt still works — falls back to CV-only mode).
    """
    sections: list[str] = []
    data = getattr(profile, 'data_content', None) or {}

    # --- Gap analysis breakdown (matched / missing / soft) ---
    matched = list(getattr(gap_analysis, 'matched_skills', None) or [])
    missing = list(getattr(gap_analysis, 'critical_missing_skills', None) or
                   getattr(gap_analysis, 'missing_skills', None) or [])
    soft = list(getattr(gap_analysis, 'soft_skill_gaps', None) or [])
    if matched or missing or soft:
        gap_lines = ["=== GAP ANALYSIS (lead with what's matched, never over-claim what's missing) ==="]
        if matched:
            gap_lines.append(f"MATCHED skills (emphasize these): {', '.join(matched)}")
        if missing:
            gap_lines.append(f"MISSING skills (the candidate does NOT have these — do NOT claim them): {', '.join(missing)}")
        if soft:
            gap_lines.append(f"SOFT GAPS (e.g., seniority, career-switch — keep summary calibrated, no overreach): {'; '.join(soft)}")
        sections.append("\n".join(gap_lines))

    # --- GitHub signals: corroborate technical claims with public evidence ---
    gh = data.get('github_signals') if isinstance(data, dict) else None
    if isinstance(gh, dict) and not gh.get('error'):
        lines = ["=== GITHUB ACTIVITY (use to quantify scale; never claim repos that aren't here) ==="]
        username = gh.get('username') or 'unknown'
        public = gh.get('public_repos') or 0
        stars = gh.get('total_stars') or 0
        recent = gh.get('recent_commit_count') or 0
        lines.append(f"@{username} — {public} public repos, {stars} stars, {recent} commits in last 90 days")
        langs = gh.get('language_breakdown') or []
        if langs:
            formatted = []
            for entry in langs[:8]:
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    formatted.append(f"{entry[0]} ({entry[1]} repos)")
                elif isinstance(entry, dict) and 'name' in entry:
                    formatted.append(f"{entry.get('name')} ({entry.get('count', '?')} repos)")
            if formatted:
                lines.append(f"Languages: {', '.join(formatted)}")
        for repo in (gh.get('top_repos') or [])[:5]:
            if not isinstance(repo, dict):
                continue
            n = repo.get('name') or repo.get('full_name', '?')
            lang = repo.get('language') or ''
            rstars = repo.get('stars') or 0
            desc = (repo.get('description') or '').strip()[:140]
            line = f"- {n}"
            if lang:
                line += f" [{lang}]"
            if rstars:
                line += f" — {rstars}★"
            if desc:
                line += f": {desc}"
            lines.append(line)
        sections.append("\n".join(lines))

    # --- Scholar signals: publication-backed claims for academic CVs ---
    sc = data.get('scholar_signals') if isinstance(data, dict) else None
    if isinstance(sc, dict) and not sc.get('error'):
        lines = ["=== GOOGLE SCHOLAR (publication evidence; cite specifics only when claiming research depth) ==="]
        cites = sc.get('total_citations') or 0
        h = sc.get('h_index') or 0
        i10 = sc.get('i10_index') or 0
        if cites or h or i10:
            lines.append(f"Citations: {cites} total · h-index: {h} · i10: {i10}")
        for pub in (sc.get('top_publications') or [])[:5]:
            if not isinstance(pub, dict):
                continue
            title = (pub.get('title') or '').strip()
            if not title:
                continue
            year = pub.get('year') or ''
            venue = pub.get('venue') or ''
            pcites = pub.get('citations') or 0
            bits = [title]
            if venue: bits.append(venue)
            if year: bits.append(str(year))
            tail = f" — {pcites} citations" if pcites else ''
            lines.append(f"- {' · '.join(bits)}{tail}")
        if len(lines) > 1:
            sections.append("\n".join(lines))

    # --- Kaggle signals: medal/competition evidence for data/ML candidates ---
    kg = data.get('kaggle_signals') if isinstance(data, dict) else None
    if isinstance(kg, dict) and not kg.get('error'):
        lines = ["=== KAGGLE (competition/notebook evidence; quantify ML claims from this) ==="]
        u = kg.get('username') or kg.get('display_name') or 'unknown'
        tier = kg.get('overall_tier') or 'Novice'
        lines.append(f"@{u} — overall tier: {tier}")
        for label, key in (('Competitions', 'competitions'), ('Notebooks', 'notebooks'),
                          ('Datasets', 'datasets'), ('Discussion', 'discussion')):
            cat = kg.get(key)
            if not isinstance(cat, dict):
                continue
            count = cat.get('count') or 0
            if not count:
                continue
            m = cat.get('medals') or {}
            g, s, b = m.get('gold', 0), m.get('silver', 0), m.get('bronze', 0)
            ct = cat.get('tier') or ''
            medal_str = f" · medals 🥇{g} 🥈{s} 🥉{b}" if (g or s or b) else ''
            lines.append(f"- {label}: {count}{(' · ' + ct) if ct else ''}{medal_str}")
        if len(lines) > 1:
            sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _apply_bullet_validator(resume_content: dict) -> dict:
    """Run the deterministic bullet validator over a freshly-generated resume,
    optionally auto-fix safe issues in-place, and stash the report on the
    result dict under `validation_report` so the views layer can persist it
    onto `GeneratedResume.validation_report`.

    Mode is governed by `settings.BULLET_AUTOFIX` ("report_only" | "safe_autofix").
    Any exception in the validator path is swallowed — resume generation must
    not fail because of a validator bug.
    """
    from django.conf import settings as dj_settings
    mode = getattr(dj_settings, "BULLET_AUTOFIX", "report_only")
    strict = bool(getattr(dj_settings, "BULLET_VALIDATOR_STRICT", False))
    if mode not in ("report_only", "safe_autofix"):
        mode = "report_only"

    try:
        from resumes.services.bullet_validator import validate_resume
        if mode == "safe_autofix":
            resume_content, report = validate_resume(
                resume_content, seniority="mid", mode="safe_autofix", strict=strict,
            )
        else:
            report = validate_resume(
                resume_content, seniority="mid", mode="report_only", strict=strict,
            )
        resume_content["validation_report"] = report.model_dump()
        logger.info(
            "Bullet validator: mode=%s passed=%s errors=%d warns=%d total_bullets=%d",
            mode, report.passed, report.stats.get("errors", 0),
            report.stats.get("warns", 0), report.stats.get("total_bullets", 0),
        )
    except Exception as exc:  # noqa: BLE001 — validator must not break gen
        logger.warning("Bullet validator failed (%s); skipping report.", exc)
    return resume_content


def _build_standards_section(profile, job):
    """Return ``(standards_block_str, classification_or_None,
    retrieval_metadata_dict)``.

    PR 4 (integration tests) — extended return shape so callers can
    surface classification and retrieval breadth as metadata. The
    string remains the primary value (it's spliced into the LLM prompt
    verbatim); the second and third values are diagnostic.

    Classification runs independently of retrieval, so a DB-less
    environment (or a retrieval failure mid-query) still produces the
    classification metadata. Resume generation continues either way.
    """
    from django.conf import settings as dj_settings

    profile_dict = (getattr(profile, "data_content", None) or {})
    jd_text = (getattr(job, "description", "") or "")

    classification = None
    try:
        from profiles.services.role_classifier import classify_for_jd
        classification = classify_for_jd(profile_dict, jd_text)
    except Exception as exc:  # noqa: BLE001 — classifier failure must not break resume gen
        logger.warning("Role classification failed (%s); continuing without classification.", exc)

    if not getattr(dj_settings, "RAG_ENABLED", False):
        return "", classification, {}

    try:
        from profiles.services.knowledge_retriever import (
            retrieve_chunks, format_standards_block,
        )
        # Use a fail-safe classification stub when classifier failed
        # earlier — retrieval needs the role tag.
        cls_for_retrieval = classification
        if cls_for_retrieval is None:
            from profiles.services.role_classifier import RoleClassification
            cls_for_retrieval = RoleClassification(
                primary_role='Software Engineer', seniority='mid',
                tech_stack_signals=[], region='global',
            )
        chunks = retrieve_chunks(
            jd_text,
            cls_for_retrieval,
            k=int(getattr(dj_settings, "RAG_TOP_K", 6)),
            universal_share=int(getattr(dj_settings, "RAG_UNIVERSAL_SHARE", 3)),
        )
        retrieval_meta = {
            'chunk_ids': [getattr(c, 'kb_id', '') for c in chunks],
            'chunk_types': [getattr(c, 'type', '') for c in chunks],
            'chunk_roles': [list(getattr(c, 'roles', []) or []) for c in chunks],
        }
        return format_standards_block(chunks), classification, retrieval_meta
    except Exception as exc:  # noqa: BLE001 — retrieval failure must not break resume gen
        logger.warning("RAG retrieval failed (%s); falling back to no-standards prompt.", exc)
        return "", classification, {}


def _apply_v2_grounding_check(
    resume_content: dict, plan, profile, job, gap_analysis,
) -> dict:
    """Run the Stage 7 grounding validator and merge its findings into
    ``resume_content['validation_report']``. Strips any trailing
    `[chunk_id]` citations the LLM emitted before persistence (the
    prompt's GROUNDING RULE asks for them as self-grounding markers —
    they should never reach the user)."""
    if plan is None:
        return resume_content
    try:
        from resumes.services.resume_validator import (
            run_grounding_check, strip_citations_from_resume, findings_to_report,
        )
        from profiles.services.candidate_evidence_retriever import retrieve_for_skills
        # Re-retrieve so the validator sees the same evidence pool the
        # prompt saw. Cheap — retrieve_for_skills only hits the embedding
        # service for skill names not already cached this request.
        tiers = (job.extracted_skills_tiers or {}) if job else {}
        skills_of_interest = list((tiers.get('must_have') or [])) + list((tiers.get('nice_to_have') or []))
        if not skills_of_interest and job:
            skills_of_interest = list(job.extracted_skills or [])
        per_skill_ev = retrieve_for_skills(profile, skills_of_interest, k_per_skill=3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("v2 grounding-check: skipped (%s).", exc)
        return resume_content

    resume_content = strip_citations_from_resume(resume_content)
    findings = run_grounding_check(resume_content, plan, per_skill_ev)
    existing_report = resume_content.get('validation_report') or {}
    if not isinstance(existing_report, dict):
        existing_report = {}
    existing_report['grounding_findings'] = findings_to_report(findings)
    resume_content['validation_report'] = existing_report
    return resume_content


def _build_v2_grounding(profile, job, gap_analysis) -> tuple[str, Optional[InclusionPlan]]:
    """Pull per-skill candidate evidence + build the inclusion plan, then
    render them as a single prompt block.

    Returns ``("", None)`` on any failure — the v1 path still runs with
    the rest of the prompt, so a missing index or retriever bug never
    breaks resume generation. The inclusion plan is also returned so the
    grounding validator (Stage 7) can re-use the same skills_to_list,
    bridge_bullet_skills, and drop_skills decisions without recomputing.
    """
    try:
        from profiles.services.candidate_evidence_retriever import retrieve_for_skills
    except Exception as exc:  # noqa: BLE001 — keep resume gen alive even if the new modules fail to import
        logger.warning("v2 grounding: imports failed (%s) — skipping.", exc)
        return "", None

    try:
        tiers = (job.extracted_skills_tiers or {}) if job else {}
        must = list(tiers.get('must_have') or [])
        nice = list(tiers.get('nice_to_have') or [])
        if not must and not nice and job:
            must = list(job.extracted_skills or [])
        skills_of_interest = [s for s in (must + nice) if s]

        per_skill_ev = retrieve_for_skills(profile, skills_of_interest, k_per_skill=3)
        plan = build_inclusion_plan(profile, job, gap_analysis, per_skill_ev)
    except Exception as exc:  # noqa: BLE001
        logger.warning("v2 grounding: build failed (%s) — falling back to v1.", exc)
        return "", None

    block_parts: list[str] = ["=== V2 GROUNDING BLOCK ==="]

    # --- Gap analysis v2 fields ---
    block_parts.append("")
    block_parts.append("GAP ANALYSIS (tier-aware — must-haves move hiring decisions the most):")
    block_parts.append(
        f"- MATCHED must-have ({len(plan.matched_must_have)}): {', '.join(plan.matched_must_have) or '(none)'}"
    )
    block_parts.append(
        f"- MATCHED nice-to-have ({len(plan.matched_nice_to_have)}): {', '.join(plan.matched_nice_to_have) or '(none)'}"
    )
    if plan.bridge_bullet_skills:
        block_parts.append(
            "- BRIDGE OPPORTUNITIES (high-proximity missing — write ONE bullet "
            "each that honestly connects existing evidence to the skill, do NOT fabricate experience):"
        )
        for entry in plan.bridge_bullet_skills:
            hint = entry.get('bridge_hint') or ''
            block_parts.append(
                f"  - {entry['name']} (proximity {entry['proximity']:.2f}): {hint}"
            )
    if plan.drop_skills:
        block_parts.append(
            f"- DO-NOT-CLAIM ({len(plan.drop_skills)}): {', '.join(plan.drop_skills)} "
            "— these are missing must-haves with NO bridging evidence. NEVER include them "
            "in the Skills section, summary, or any bullet."
        )

    # --- Inclusion plan (authoritative spec) ---
    block_parts.append("")
    block_parts.append("INCLUSION PLAN (treat this as authoritative — write to it, don't second-guess it):")
    block_parts.append(
        f"- Skills section list ({len(plan.skills_to_list)} items, in this order): "
        f"{', '.join(plan.skills_to_list) or '(none)'}"
    )
    if plan.projects:
        kept = [
            f"#{p.profile_index} {p.name or '(unnamed)'} (relevance={p.relevance_score})"
            for p in plan.projects
        ]
        block_parts.append(
            f"- Projects to include ({len(plan.projects)}, ranked by JD relevance): {', '.join(kept)}"
        )
    if plan.certifications:
        block_parts.append(
            f"- Certifications to include ({len(plan.certifications)}): "
            f"{', '.join(plan.certifications)}. DROP all other certifications."
        )
    block_parts.append(
        f"- Volunteer section: {'INCLUDE' if plan.include_volunteer else 'OMIT'}; "
        f"Publications: {'INCLUDE' if plan.include_publications else 'OMIT'}; "
        f"Awards: {'INCLUDE' if plan.include_awards else 'OMIT'}."
    )
    if plan.summary_hints:
        block_parts.append("- Summary draft hints (pull phrasing from these retrieved chunks):")
        for hint in plan.summary_hints:
            block_parts.append(f"  - {hint}")

    # --- Per-skill evidence map (the LLM's grounding source) ---
    if per_skill_ev:
        block_parts.append("")
        block_parts.append(
            "PER-SKILL EVIDENCE (cite a chunk_id in [brackets] when you write a bullet that "
            "uses one of these; the validator strips citations before persistence):"
        )
        for skill, chunks in per_skill_ev.items():
            if not chunks:
                continue
            block_parts.append(f"  • {skill}:")
            for c in chunks:
                snippet = ' '.join(c.text.split())[:220]
                block_parts.append(f"    [{c.chunk_id}] {snippet}")

    block_parts.append("")
    block_parts.append(
        "GROUNDING RULE: every concrete claim in your output must trace to either "
        "(a) a chunk_id in the PER-SKILL EVIDENCE block, (b) the CV / signal data in the "
        "blocks above, or (c) a BRIDGE OPPORTUNITY (one bridge bullet per listed skill). "
        "When in doubt, keep the bullet qualitative — DO NOT invent numbers, dates, teams, "
        "tools, or outcomes."
    )

    return "\n".join(block_parts), plan


# --------------------------------------------------------------------------
# Fix #1 — content stickiness (audit §6.5, 2026-05-30).
#
# When the user EXPORTS a resume (PDF or DOCX), the export view captures the
# current resume.content as a "previous_best" snapshot on the GeneratedResume
# row. On a later regeneration for the SAME JD (verified via content hash —
# Job has no updated_at to rely on), the supervised loop:
#   1. Injects a "preserve OR improve, do NOT regress" prompt block listing
#      the previous-best content per section (per-item where the join key
#      is stable; whole-section where it isn't).
#   2. After generation, runs a deterministic regression check that flags
#      metric_loss + bullet_count_drop as BLOCKING findings — the
#      supervised loop then uses the same revision-round budget as the
#      supervisor to drive a regen that restores the lost content.
#   3. On cap exhaustion (SUPERVISOR_MAX_REVISION_ROUNDS reached with
#      regression findings still open), ships the best draft observed
#      (matching the supervisor's existing cap-hit behaviour) and DEMOTES
#      any remaining regression findings to 'warning' so the fix-#2
#      banner surfaces them rather than the user receiving an error.
#
# Master profile is never written to. Snapshot is resume-row-scoped.
# --------------------------------------------------------------------------


def _jd_identity_hash(job) -> str:
    """SHA256 of normalised job identity. Used to detect "same JD" between
    a previous export and a later regeneration. We can't rely on
    job.updated_at because the Job model doesn't track it; instead we
    hash the four fields that determine the tailoring outcome.

    Returns the hex digest, or '' for a falsy job.
    """
    import hashlib
    if job is None:
        return ''
    payload = {
        'title': (getattr(job, 'title', '') or '').strip().lower(),
        'company': (getattr(job, 'company', '') or '').strip().lower(),
        'description': (getattr(job, 'description', '') or '').strip(),
        'tiers': getattr(job, 'extracted_skills_tiers', None) or {},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _canon_pb(text) -> str:
    """Local canonical form for previous-best join keys: lowercase +
    alphanumeric only. Mirrors inclusion_planner._canonical but kept
    local to avoid coupling resume_generator → inclusion_planner for a
    pure-string helper."""
    if not text:
        return ''
    return ''.join(c.lower() for c in str(text) if c.isalnum())


def _build_previous_best_block(snapshot: dict | None, current_job_hash: str) -> str:
    """Emit the prompt block that anchors regeneration to the user's last
    exported version. Returns '' when:
      • the snapshot is missing / empty (no prior export);
      • the JD identity hash doesn't match (the user edited the job —
        previous-best is evidence against a now-irrelevant target).

    When it returns content, the LLM sees a structured per-section
    "preserve OR improve" reference. Per join keys agreed in scoping B5:
      experience    → (canon company, canon title)
      education     → (canon institution, canon degree)
      projects      → url (when non-empty) else canon name
      certifications → (canon name, canon issuer)
      summary / objective / title / skills / languages / awards → whole
    """
    if not snapshot or not isinstance(snapshot, dict):
        return ''
    snap_hash = snapshot.get('jd_identity_hash') or ''
    if not snap_hash or not current_job_hash or snap_hash != current_job_hash:
        return ''
    content = snapshot.get('content') or {}
    if not isinstance(content, dict):
        return ''
    lines: list[str] = []
    lines.append(
        "=== PREVIOUS BEST (from your last export — preserve OR improve, "
        "do NOT regress) ==="
    )
    lines.append(
        "A previous export of this resume contained content the user already "
        "approved. For each item below, MATCH or IMPROVE — do not drop "
        "metrics (%/numbers/named tools), do not cut bullets, and do not "
        "weaken phrasing. If you can genuinely improve a bullet (sharper "
        "verb, tighter claim) do so; otherwise keep it. NEVER fabricate to "
        "preserve — if a metric was real before, it's still real now."
    )
    # Single-block / flat-list sections.
    summ = (content.get('professional_summary') or '').strip()
    if summ:
        lines.append("")
        lines.append("[PREVIOUS SUMMARY] (preserve or improve)")
        lines.append(summ)
    obj = (content.get('objective') or '').strip()
    if obj:
        lines.append("")
        lines.append("[PREVIOUS OBJECTIVE]")
        lines.append(obj)
    title = (content.get('professional_title') or '').strip()
    if title:
        lines.append("")
        lines.append(f"[PREVIOUS TITLE] {title}")
    skills = content.get('skills') or []
    if isinstance(skills, list) and skills:
        lines.append("")
        lines.append(
            "[PREVIOUS SKILLS] (preserve these; you may add JD-aligned ones)"
        )
        lines.append(", ".join(str(s) for s in skills if s))
    # Experience — per-role anchored on (company, title).
    exps = content.get('experience') or []
    if isinstance(exps, list) and exps:
        lines.append("")
        lines.append(
            "[PREVIOUS EXPERIENCE BULLETS] (per role — preserve metrics, "
            "same bullet count or more)"
        )
        for e in exps:
            if not isinstance(e, dict):
                continue
            t = (e.get('title') or '').strip()
            c = (e.get('company') or '').strip()
            desc = e.get('description') or []
            if isinstance(desc, str):
                desc = [desc]
            bullets = [b for b in desc if isinstance(b, str) and b.strip()]
            if not (t or c) or not bullets:
                continue
            lines.append(f"- Role: {t} @ {c}")
            for b in bullets:
                lines.append(f"    * {b}")
    # Projects — per-project anchored on url (preferred) or canon name.
    projs = content.get('projects') or []
    if isinstance(projs, list) and projs:
        lines.append("")
        lines.append(
            "[PREVIOUS PROJECT BULLETS] (per project — preserve metrics, "
            "same bullet count or more)"
        )
        for p in projs:
            if not isinstance(p, dict):
                continue
            n = (p.get('name') or '').strip()
            u = (p.get('url') or '').strip()
            desc = p.get('description') or []
            if isinstance(desc, str):
                desc = [desc]
            bullets = [b for b in desc if isinstance(b, str) and b.strip()]
            if not n or not bullets:
                continue
            anchor = n if not u else f"{n} ({u})"
            lines.append(f"- Project: {anchor}")
            for b in bullets:
                lines.append(f"    * {b}")
    # Certifications — names only (the LLM's job is to keep them on the page,
    # not rewrite issuers/dates).
    certs = content.get('certifications') or []
    if isinstance(certs, list) and certs:
        cert_names = [
            (c.get('name') or '').strip() if isinstance(c, dict) else str(c).strip()
            for c in certs
        ]
        cert_names = [n for n in cert_names if n]
        if cert_names:
            lines.append("")
            lines.append("[PREVIOUS CERTIFICATIONS] (preserve)")
            lines.append("; ".join(cert_names))
    langs = content.get('languages') or []
    if isinstance(langs, list) and langs:
        lines.append("")
        lines.append("[PREVIOUS LANGUAGES] (preserve)")
        lines.append(", ".join(str(l) for l in langs if l))
    awards = content.get('awards') or []
    if isinstance(awards, list) and awards:
        lines.append("")
        lines.append("[PREVIOUS AWARDS] (preserve)")
        lines.append("; ".join(str(a) for a in awards if a))
    if len(lines) <= 2:
        # Snapshot existed but had no actual content — don't emit a useless block.
        return ''
    return "\n".join(lines)


# Regex for "this bullet contained a numeric metric" — reused inside the
# regression check. The resume_validator module owns the canonical regex;
# importing it here is cleaner than redefining the pattern.
def _bullet_numeric_claims(bullet: str) -> set[str]:
    """Return the set of numeric tokens in a bullet (e.g. '92%', '5M').
    Thin wrapper that delegates to the existing resume_validator helper."""
    from resumes.services.resume_validator import _extract_numeric_claims
    if not isinstance(bullet, str) or not bullet.strip():
        return set()
    return set(_extract_numeric_claims(bullet))


def _apply_regression_check(
    resume_content: dict,
    previous_best: dict | None,
    current_job_hash: str | None = None,
) -> dict:
    """Compare a freshly-generated resume against its prior export. Emits
    deterministic findings into ``validation_report['regression_findings']``:

      • metric_loss      (severity='blocking') — a numeric claim that was
                         in the matched previous-best bullet is missing
                         from the new one. Per (section, item, bullet).
      • bullet_count_drop (severity='blocking') — a matched experience/
                         project item has fewer bullets than before.
      • skill_loss       (severity='warning') — a canonical skill that
                         was on the previous version isn't on the new
                         one. Advisory: skills legitimately shift with
                         tailoring, but the user should see the change.

    Mutates ``resume_content`` in place (sets the findings list) and
    returns it. Empty list when:
      • no previous_best snapshot, OR
      • snapshot's jd_identity_hash doesn't match ``current_job_hash``
        (JD edited — previous-best is no longer a valid comparator;
        mirrors _build_previous_best_block's gate so injection and
        enforcement stay in sync), OR
      • no regression.
    """
    if not isinstance(resume_content, dict):
        return resume_content
    findings: list[dict] = []
    snap = previous_best or {}
    if not isinstance(snap, dict):
        snap = {}
    # JD-identity gate: if caller supplied current hash AND snapshot has a
    # hash AND they don't match, the snapshot is stale (user edited the
    # JD). Skip the diff entirely; write empty findings list.
    snap_hash = snap.get('jd_identity_hash') or ''
    if current_job_hash is not None and snap_hash and snap_hash != current_job_hash:
        vr = resume_content.setdefault('validation_report', {})
        if isinstance(vr, dict):
            vr['regression_findings'] = []
        return resume_content
    prev_content = snap.get('content') or {}
    if not isinstance(prev_content, dict):
        prev_content = {}

    # --- Skills (whole-section, canonical-set comparison). ---
    if prev_content.get('skills'):
        new_skills = resume_content.get('skills') or []
        if not isinstance(new_skills, list):
            new_skills = []
        new_canon = {_canon_pb(s) for s in new_skills if s}
        for s in (prev_content.get('skills') or []):
            c = _canon_pb(s)
            if c and c not in new_canon:
                findings.append({
                    'kind': 'skill_loss', 'severity': 'warning',
                    'where': 'skills',
                    'prev': str(s),
                    'now': '',
                    'detail': (
                        f"Skill {str(s)!r} was on your last exported version "
                        "but is missing from this draft. (Advisory — skills "
                        "can shift with tailoring.)"
                    ),
                })

    # --- Experience — match by (canon company, canon title). ---
    def _index_by(items, keyfn):
        idx: dict = {}
        for i, it in enumerate(items or []):
            if not isinstance(it, dict):
                continue
            k = keyfn(it)
            if k and k not in idx:
                idx[k] = it
        return idx

    def _exp_key(e):
        return (_canon_pb(e.get('company')), _canon_pb(e.get('title')))

    new_exps = resume_content.get('experience') or []
    prev_exps = prev_content.get('experience') or []
    new_exp_idx = _index_by(new_exps, _exp_key)
    for prev in prev_exps:
        if not isinstance(prev, dict):
            continue
        k = _exp_key(prev)
        if not any(k):
            continue
        new = new_exp_idx.get(k)
        if not new:
            # Whole-role gone — covered by bullet_count_drop check below
            # via len(new.description)=0 vs len(prev.description)=N.
            new = {'description': []}
        prev_desc = [b for b in (prev.get('description') or []) if isinstance(b, str)]
        new_desc = [b for b in (new.get('description') or []) if isinstance(b, str)]
        where = f"experience[{prev.get('title') or ''} @ {prev.get('company') or ''}]"
        if len(new_desc) < len(prev_desc):
            findings.append({
                'kind': 'bullet_count_drop', 'severity': 'blocking',
                'where': where,
                'prev': str(len(prev_desc)),
                'now': str(len(new_desc)),
                'detail': (
                    f"Role had {len(prev_desc)} bullets in your last export; "
                    f"this draft has {len(new_desc)}. Restore the dropped "
                    "bullet(s) — keep their concrete content."
                ),
            })
        # metric_loss: for each prev bullet, find the closest new bullet
        # (greedy first-fit by shared metrics) and assert prev metrics ⊆ new.
        # Greedy is fine: if a metric truly survives anywhere in the role's
        # new bullets, the union check catches it.
        new_metric_union: set[str] = set()
        for nb in new_desc:
            new_metric_union |= _bullet_numeric_claims(nb)
        for pb in prev_desc:
            pm = _bullet_numeric_claims(pb)
            missing = pm - new_metric_union
            if missing:
                findings.append({
                    'kind': 'metric_loss', 'severity': 'blocking',
                    'where': where,
                    'prev': sorted(missing),
                    'now': '',
                    'detail': (
                        f"Bullet metrics {sorted(missing)} from your last "
                        "export aren't in this draft's version of the role. "
                        "Restore the specific number(s)."
                    ),
                })

    # --- Projects — match by url (preferred) or canon name. ---
    def _proj_key(p):
        u = (p.get('url') or '').strip()
        if u:
            return ('url', u)
        return ('name', _canon_pb(p.get('name')))

    new_projs = resume_content.get('projects') or []
    prev_projs = prev_content.get('projects') or []
    new_proj_idx = _index_by(new_projs, _proj_key)
    for prev in prev_projs:
        if not isinstance(prev, dict):
            continue
        k = _proj_key(prev)
        if not k[1]:
            continue
        new = new_proj_idx.get(k)
        if not new:
            new = {'description': []}
        prev_desc = [b for b in (prev.get('description') or []) if isinstance(b, str)]
        new_desc = [b for b in (new.get('description') or []) if isinstance(b, str)]
        where = f"projects[{prev.get('name') or ''}]"
        if len(new_desc) < len(prev_desc):
            findings.append({
                'kind': 'bullet_count_drop', 'severity': 'blocking',
                'where': where,
                'prev': str(len(prev_desc)),
                'now': str(len(new_desc)),
                'detail': (
                    f"Project had {len(prev_desc)} bullets in your last "
                    f"export; this draft has {len(new_desc)}."
                ),
            })
        new_metric_union: set[str] = set()
        for nb in new_desc:
            new_metric_union |= _bullet_numeric_claims(nb)
        for pb in prev_desc:
            pm = _bullet_numeric_claims(pb)
            missing = pm - new_metric_union
            if missing:
                findings.append({
                    'kind': 'metric_loss', 'severity': 'blocking',
                    'where': where,
                    'prev': sorted(missing),
                    'now': '',
                    'detail': (
                        f"Project metrics {sorted(missing)} from your last "
                        "export aren't in this draft. Restore the number(s)."
                    ),
                })

    # Persist findings (always — empty list is meaningful: "we checked").
    vr = resume_content.setdefault('validation_report', {})
    if isinstance(vr, dict):
        vr['regression_findings'] = findings
    return resume_content


# Verbose per-item fields stripped from slim_cv before prompt embedding.
# The actual bullet TEXT for selected items lives in v2_block (per-skill
# JD-aligned evidence) — duplicating it here is what blew the prompt to
# ~39k chars on real profiles. The remaining fields (title, company,
# dates, location, industry, technologies, url) carry the metadata the
# LLM needs to scaffold a tailored resume; bullet content is sourced
# from v2_block.
_EXP_BULLET_KEYS = (
    'description', 'highlights', 'responsibilities',
    'achievements', 'accomplishments', 'tasks', 'bullets',
    'duties', 'summary',
)
_PROJ_BULLET_KEYS = (
    'description', 'highlights', 'features',
    'outcomes', 'deliverables', 'summary',
)


def _strip_bullet_fields(item: dict, bullet_keys) -> dict:
    """Return a copy of `item` with the bullet-carrying fields dropped.
    Falls through unchanged when `item` isn't a dict."""
    if not isinstance(item, dict):
        return item
    return {k: v for k, v in item.items() if k not in bullet_keys}


# ---- Constructive CV-block builder (Fix D, third pass) ----
# Earlier passes tried to FILTER the full sanitized profile down — a
# subtractive approach. On real users this still produced 34k-39k char
# cv_blocks because the user's "kept" content was inherently large
# (3 experiences with 12 bullets each, 4 projects, 15 certs, plus a
# pile of keys the master profile happens to carry: github_signals,
# linkedin_snapshot, raw_text, normalized_summary, etc.). Filter-and-
# subtract drifts back toward the full profile whenever the master
# schema gains a new key.
#
# The constructive builder below starts EMPTY and only adds:
#   - identity / contact (small allowlist)
#   - planner-selected skills (names only)
#   - planner-selected experiences with metadata only (bullets live in
#     v2_block as per-skill JD-aligned evidence)
#   - planner-selected projects with metadata only (same reason)
#   - planner-selected certifications (full structure — small)
#   - education / languages (small, structured)
# Nothing else can leak in.


_CV_IDENTITY_KEYS = (
    'name', 'full_name',
    'email', 'phone', 'location',
    'linkedin', 'website', 'github', 'portfolio',
    'headline', 'professional_summary',
)
_EXP_META_KEYS = (
    'title', 'company', 'location', 'industry',
    'duration', 'start_date', 'end_date',
)
_PROJ_META_KEYS = (
    'name', 'url', 'technologies',
    'start_date', 'end_date',
)


def _exp_metadata(exp):
    if not isinstance(exp, dict):
        return None
    return {k: exp[k] for k in _EXP_META_KEYS if exp.get(k)}


def _proj_metadata(proj):
    if not isinstance(proj, dict):
        return None
    return {k: proj[k] for k in _PROJ_META_KEYS if proj.get(k)}


def _build_planner_aligned_cv(sanitized_cv: dict, plan) -> dict:
    """Construct the prompt's CV block from the inclusion plan + a tiny
    allowlist of identity / structured fields. Anything not explicitly
    allowed CANNOT leak in — the function is constructive, not
    subtractive, so future master-profile schema changes can't silently
    re-inflate the prompt.

    What this includes:
      * identity / contact: small allowlist (name, email, phone, …)
      * skills: ``plan.skills_to_list`` (planner names, not master skills)
      * experiences: only at ``plan.experiences[i].profile_index``, and
                     ONLY metadata (no bullets — v2_block has them)
      * projects: only at ``plan.projects[i].profile_index``, metadata only
      * certifications: only those in ``plan.certifications``, full struct
      * education / languages: full (small, structured)

    What this DROPS:
      * unselected experiences / projects / certs
      * every bullet-bearing field on kept items (description, highlights,
        responsibilities, achievements, …)
      * github_signals / scholar_signals / kaggle_signals / linkedin_snapshot
      * raw_text / extracted_text / cv_text
      * normalized_summary / any other catch-all key

    When ``plan`` is None (v2-grounding path declined), falls back to a
    sane minimum: metadata-only experiences + master skills/certs/edu.
    """
    src = sanitized_cv or {}
    out: dict = {}

    # Identity / contact (small, fixed allowlist).
    for k in _CV_IDENTITY_KEYS:
        v = src.get(k)
        if v:
            out[k] = v

    # Skills — planner names if available, else master skills.
    plan_skills = list(getattr(plan, 'skills_to_list', None) or []) if plan else []
    if plan_skills:
        out['skills'] = plan_skills
    elif src.get('skills'):
        out['skills'] = src['skills']

    # Experiences — planner-selected indices, metadata only.
    src_exps = src.get('experiences') or []
    plan_exps = list(getattr(plan, 'experiences', None) or []) if plan else []
    if plan_exps and isinstance(src_exps, list) and src_exps:
        idxs = [
            getattr(ep, 'profile_index', None) for ep in plan_exps
        ]
        idxs = [i for i in idxs if isinstance(i, int) and 0 <= i < len(src_exps)]
        kept_exps = [_exp_metadata(src_exps[i]) for i in idxs]
    elif isinstance(src_exps, list) and src_exps:
        # No plan — keep all but metadata-only.
        kept_exps = [_exp_metadata(e) for e in src_exps]
    else:
        kept_exps = []
    kept_exps = [e for e in kept_exps if e]
    if kept_exps:
        out['experiences'] = kept_exps

    # Projects — same shape.
    src_projs = src.get('projects') or []
    plan_projs = list(getattr(plan, 'projects', None) or []) if plan else []
    if plan_projs and isinstance(src_projs, list) and src_projs:
        idxs = [
            getattr(pp, 'profile_index', None) for pp in plan_projs
        ]
        idxs = [i for i in idxs if isinstance(i, int) and 0 <= i < len(src_projs)]
        kept_projs = [_proj_metadata(src_projs[i]) for i in idxs]
    elif isinstance(src_projs, list) and src_projs:
        kept_projs = [_proj_metadata(p) for p in src_projs]
    else:
        kept_projs = []
    kept_projs = [p for p in kept_projs if p]
    if kept_projs:
        out['projects'] = kept_projs

    # Certifications — planner-selected names; full structure (each
    # entry is small: name + issuer + date + url + duration).
    src_certs = src.get('certifications') or []
    plan_certs = list(getattr(plan, 'certifications', None) or []) if plan else []
    if plan_certs and isinstance(src_certs, list) and src_certs:
        wanted = {(c or '').strip().lower() for c in plan_certs if c}
        kept_certs = [
            c for c in src_certs
            if isinstance(c, dict) and (c.get('name') or '').strip().lower() in wanted
        ]
        # Fallback when name matching misses (case / whitespace drift):
        # ship the planner's bare name list so at least the names appear.
        if not kept_certs:
            kept_certs = [{'name': n} for n in plan_certs if n]
        if kept_certs:
            out['certifications'] = kept_certs
    elif isinstance(src_certs, list) and src_certs:
        out['certifications'] = src_certs

    # Education — full (small, structured).
    edu = src.get('education')
    if edu:
        out['education'] = edu

    # Languages — small list.
    langs = src.get('languages')
    if langs:
        out['languages'] = langs

    return out


def _apply_plan_filter_to_slim_cv(slim_cv: dict, plan) -> dict:
    """[DEPRECATED — kept for legacy tests.] Subtractive filter that
    starts from a sanitized master profile and tries to drop fields.
    On real users this still produced 34k-39k char outputs because
    the kept content (bullets, signal blobs, catch-all keys) was
    inherently large. The supersedeer is ``_build_planner_aligned_cv``,
    which builds the block constructively from a tiny allowlist.

    Why this exists: the LLM prompt previously shipped the FULL master
    profile (all 50+ skills, 25+ certs, every experience and project
    WITH every bullet) AND the planner's filtered selection in `v2_block`
    separately. Real user profiles produced ~39k-char CV dumps even
    after the index-only filter, because each kept experience carried
    8-15 bullets at 200-1000 chars each. The bullets were already in
    `v2_block` — redundantly burning ~20-30k tokens. The planner is the
    single source of truth for "what belongs on THIS resume for THIS
    job"; v2_block is the single source for "what the bullets say."
    Honor both.

    The filter:
      - skills: replaced with `plan.skills_to_list` (ordered by the planner)
      - experiences: kept only those at `plan.experiences[i].profile_index`,
                     and per-experience BULLET fields stripped (sourced
                     from v2_block at write time)
      - projects: kept only those at `plan.projects[i].profile_index`,
                  bullet/description fields stripped (same reason)
      - certifications: kept only those matching `plan.certifications` by name
      - everything else (contact, education, languages, summary): untouched

    When `plan` is None (the v2-grounding path declined to produce one),
    or `slim_cv` isn't a dict, returns `slim_cv` unchanged.
    """
    if plan is None or not isinstance(slim_cv, dict):
        return slim_cv

    filtered = dict(slim_cv)  # shallow copy — don't mutate caller's dict

    # Skills — replace with the planner's ordered selection.
    skills_to_list = getattr(plan, 'skills_to_list', None) or []
    if skills_to_list:
        filtered['skills'] = list(skills_to_list)

    # Experiences — keep only the indices the planner picked, in plan
    # order, AND strip per-experience bullet fields (bullets ship via
    # v2_block; duplicating them was the bulk of the prompt).
    src_exps = filtered.get('experiences') or []
    plan_exps = getattr(plan, 'experiences', None) or []
    if plan_exps and isinstance(src_exps, list) and src_exps:
        idxs = [
            getattr(ep, 'profile_index', None)
            for ep in plan_exps
        ]
        idxs = [i for i in idxs if isinstance(i, int) and 0 <= i < len(src_exps)]
        if idxs:
            filtered['experiences'] = [
                _strip_bullet_fields(src_exps[i], _EXP_BULLET_KEYS) for i in idxs
            ]
    elif isinstance(src_exps, list) and src_exps:
        # No plan_exps but we still want to drop bullets — they're in v2_block.
        filtered['experiences'] = [
            _strip_bullet_fields(e, _EXP_BULLET_KEYS) for e in src_exps
        ]

    # Projects — same; metadata kept, bullets dropped.
    src_projs = filtered.get('projects') or []
    plan_projs = getattr(plan, 'projects', None) or []
    if plan_projs and isinstance(src_projs, list) and src_projs:
        idxs = [
            getattr(pp, 'profile_index', None)
            for pp in plan_projs
        ]
        idxs = [i for i in idxs if isinstance(i, int) and 0 <= i < len(src_projs)]
        if idxs:
            filtered['projects'] = [
                _strip_bullet_fields(src_projs[i], _PROJ_BULLET_KEYS) for i in idxs
            ]
    elif isinstance(src_projs, list) and src_projs:
        filtered['projects'] = [
            _strip_bullet_fields(p, _PROJ_BULLET_KEYS) for p in src_projs
        ]

    # Certifications — keep only those whose name matches the plan's list.
    src_certs = filtered.get('certifications') or []
    plan_certs = getattr(plan, 'certifications', None) or []
    if plan_certs and isinstance(src_certs, list) and src_certs:
        wanted = {(c or '').strip().lower() for c in plan_certs if c}
        kept: list = []
        for c in src_certs:
            if isinstance(c, dict):
                name = (c.get('name') or '').strip().lower()
            else:
                name = str(c or '').strip().lower()
            if name and name in wanted:
                kept.append(c)
        if kept:
            filtered['certifications'] = kept

    # Drop raw_text and any other catch-all blobs that survived earlier
    # filtering (defensive — these are 5-20k chars of duplicated profile
    # text on some users).
    for blob_key in ('raw_text', 'extracted_text', 'cv_text', 'linkedin_snapshot'):
        filtered.pop(blob_key, None)

    return filtered


def generate_resume_content(profile, job, gap_analysis, *, metadata: dict | None = None,
                            supervisor_feedback: str = "",
                            standards_section_override: str | None = None,
                            previous_best: dict | None = None):
    """
    Generate a PROFESSIONAL, ATS-optimized tailored resume using LangChain
    structured output, grounded in every signal source we have for the
    candidate (CV + GitHub + Scholar + Kaggle + gap-analysis breakdown +
    full JD body).

    The prompt's enrichment rule lets the LLM quantify claims using
    corroborating evidence from these signals — turning "Built a model"
    into "Modelled churn across 12 production repos" when GitHub backs
    that up. Without a corroborating source, claims stay qualitative.
    """
    raw_cv_data = profile.data_content or {}

    if not raw_cv_data:
        logger.warning("raw_cv_data not available, using core fields")
        raw_cv_data = {
            'skills': profile.skills or [],
            'experiences': profile.experiences or [],
            'education': profile.education or [],
            'projects': profile.projects or [],
            'certifications': profile.certifications or []
        }

    # Pass A: deterministic upstream cleanup. The CV parser + LinkedIn merger
    # land plenty of garbage in data_content (label-leaked skills, ALL-CAPS
    # titles, kebab-case GitHub project names, first-person voice). Sanitizer
    # returns a deep-copied, cleaned dict — the original profile is never
    # mutated. The LLM prompt + every downstream preserve/fallback step use
    # this cleaned view so the dirty source can't re-enter the resume.
    sanitized_cv = sanitize_profile_data(raw_cv_data)
    filtered_cv = sanitized_cv

    # Build a slim version of CV data to save tokens — drop raw_text, empty
    # fields, the cached signal blobs (we surface those separately, source-
    # labeled, in the evidence context), and the redundant summary/objective.
    _SIGNAL_KEYS = {'github_signals', 'scholar_signals', 'kaggle_signals', 'linkedin_snapshot'}
    slim_cv = {k: v for k, v in filtered_cv.items()
               if k != 'raw_text'
               and k not in _SIGNAL_KEYS
               and v
               and k not in ('normalized_summary', 'objective')}

    # Cap JD body to 4000 chars — full text is better than 1000 (the previous
    # cap was wasting context that mattered) but unbounded would let a 50KB
    # JD blow the prompt budget.
    jd_body = (job.description or '')[:4000]

    domain = _detect_job_domain(job)
    domain_section = _domain_prompt_section(domain)
    evidence_context = _build_evidence_context(profile, job, gap_analysis)
    # RAG: retrieve KB chunks + format as STANDARDS block (empty when disabled
    # or when retrieval errors out — failure must not break resume generation).
    # PR 4 — tuple return surfaces classification + retrieval metadata for
    # the integration-test harness; both are None/empty when RAG is off.
    if standards_section_override is not None:
        # Supervised loop already retrieved the KB block once; reuse it instead
        # of re-running retrieval every regen round. Still classify (cheap, 400
        # tokens) so the metadata['_classification'] contract holds.
        standards_section = standards_section_override
        retrieval_metadata = {}
        classification_obj = None
        try:
            from profiles.services.role_classifier import classify_for_jd
            classification_obj = classify_for_jd(
                (getattr(profile, "data_content", None) or {}),
                (getattr(job, "description", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — classifier failure must not break gen
            logger.warning("Override path: classification failed (%s); continuing.", exc)
    else:
        standards_section, classification_obj, retrieval_metadata = _build_standards_section(profile, job)

    # v2 grounding: pull per-skill candidate-evidence chunks + build the
    # inclusion plan. Both are no-ops + return empty blocks if anything
    # downstream blows up — the v1 path still runs.
    v2_block, inclusion_plan = _build_v2_grounding(profile, job, gap_analysis)

    # Fix-D (2026-05-31, third pass): build the prompt's CV block
    # CONSTRUCTIVELY from the inclusion plan + a tiny identity allowlist.
    # The two prior passes filtered the full sanitized profile and still
    # shipped 34-39k chars because (a) the kept items each carried 8-15
    # bullets and (b) master profiles contain catch-all keys that survive
    # any reasonable subtractive filter. Switching to constructive — start
    # empty, add only what's explicitly allowed — guarantees the block
    # cannot drift back toward the full profile when the schema grows.
    # Bullet text for kept items lives in v2_block (per-skill JD-aligned
    # evidence); slim_cv carries metadata only.
    slim_cv_for_prompt = _build_planner_aligned_cv(sanitized_cv, inclusion_plan)

    logger.info(
        "Resume generation: domain='%s' for job '%s'; evidence_block_len=%d "
        "standards_block_len=%d v2_block_len=%d cv_block_len=%d (raw=%d)",
        domain, job.title, len(evidence_context), len(standards_section),
        len(v2_block),
        len(json.dumps(slim_cv_for_prompt, default=str)),
        len(json.dumps(slim_cv, default=str)),
    )

    # Fix #1 — previous-best block. Built only when (a) caller passed a
    # snapshot and (b) the snapshot's jd_identity_hash matches THIS job's
    # current hash (JD unchanged since the last export). Placed alongside
    # the supervisor block — same "never stripped by 413 slim-retry"
    # exemption applies.
    previous_best_block = _build_previous_best_block(
        previous_best, _jd_identity_hash(job),
    )

    # Supervisor feedback (set by the supervised regen loop). High-salience,
    # placed right after JOB DETAILS, and — unlike v2_block/standards_section —
    # NEVER stripped by the 413 pre-slim/retry, so blocking fixes always survive.
    supervisor_block = ""
    if supervisor_feedback:
        supervisor_block = (
            "\n=== SUPERVISOR FEEDBACK (a senior HR/CV reviewer found these BLOCKING "
            "issues in the previous draft) ===\n"
            "Fix EVERY issue below WITHOUT making anything else worse:\n"
            "- Do NOT fabricate: never invent a metric, employer, tool, certification, or "
            "claim not already supported by the CV data and evidence. If an issue cannot be "
            "fixed truthfully, improve the phrasing instead.\n"
            "- Do NOT ADD bullets to fix a 'thin' or 'lacks metrics' bullet - REWRITE the "
            "existing bullet to be sharper. Keep each role's bullet count appropriate to its "
            "tenure (a short internship: 2-3 bullets maximum).\n"
            "- Each bullet in a role must describe a DISTINCT accomplishment - never produce "
            "two bullets that say the same thing in different words.\n"
            "- PRESERVE every factual field exactly: do NOT drop or alter start_date, "
            "end_date, titles, employers, or locations when rewriting a section.\n"
            f"{supervisor_feedback}\n"
        )

    prompt = f"""You are an EXPERT resume optimization strategist. Create a PROFESSIONAL, ATS-optimized resume tailored for this specific job using EVERY source provided.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description:
{jd_body}
{previous_best_block}
{supervisor_block}
COMPLETE CV DATA (the candidate's authoritative resume — already narrowed by the inclusion planner to the items relevant for THIS job):
{json.dumps(slim_cv_for_prompt, indent=2)}

{evidence_context}

{v2_block}

=== FIELD MAPPING (CRITICAL — the CV data uses different field names than the output schema) ===
- CV `experiences[].highlights` array → output `experience[].description` array (rewrite each bullet)
- CV `experiences[].start_date` / `end_date` → output `experience[].duration` (combine as a CLOSED range, e.g. "Aug 2025 - Mar 2026"). Also pass through start_date and end_date verbatim into the output `experience[].start_date` / `experience[].end_date` — NEVER replace a null/empty end_date with a guess.
- CV `experiences[].is_current` → output `experience[].is_current` (pass through; null when the CV did not state it).
- CRITICAL — DO NOT FABRICATE "Present": render "X - Present" in `duration` AND emit `end_date="Present"` ONLY when `is_current=true` is set on the source experience. If the source `end_date` is null/empty AND `is_current` is not true, render `duration` as the start date alone (e.g. "Jul 2024"). NEVER invent an end_date or write "- Present" for a role whose end is simply unknown.
- CV `experiences[].title` → output `experience[].title`
- CV `experiences[].location` → output `experience[].location` (PRESERVE)
- CV `experiences[].industry` → output `experience[].industry` (PRESERVE)
- CV `education[].graduation_year` → output `education[].year`
- CV `education[].degree` → output `education[].degree`
- CV `education[].field` → output `education[].field` (separate field, do NOT pre-combine into degree)
- CV `education[].gpa` → output `education[].gpa` (PRESERVE; the renderer decides whether to display)
- CV `education[].location` → output `education[].location` (PRESERVE)
- CV `education[].honors` → output `education[].honors` (PRESERVE all)
- CV `certifications[].url` → output `certifications[].url` (PRESERVE all certification URLs exactly)
- CV `certifications[].duration` → output `certifications[].duration` (PRESERVE)
- CV `projects[].description` or `highlights` → output `projects[].description` array (rewrite as bullets — single canonical field)
- CV `projects[].url` → output `projects[].url` (PRESERVE all project URLs exactly)
- CV `projects[].technologies` → output `projects[].technologies` array (PRESERVE; ATS scanner picks these up as keywords)
- CV `projects[].source` (one of "github" / "scholar" / "kaggle") → SIGNAL ONLY (not output as a resume field). If a project has this field, it was enriched from an external signal source the candidate themselves has connected (their own GitHub, Scholar, or Kaggle account), and the user has explicitly confirmed it via the project review UI. Treat its existence, `name`, `url`, and `technologies` as ground truth — they are NOT fabrications. You may still rewrite the bullet phrasing to match the JD, but never drop a confirmed enriched project on the assumption that it's not "really" the candidate's. The bullets in such projects' `description` are typically derived from the source repo / paper / competition, so they are also pre-vetted; rewrite them for tone but don't strip evidence (star counts, citation counts, medal counts).
- CV `objective` → output `objective` (the standalone objective field; this is OPTIONAL — only include if the candidate's CV explicitly has one and it's not redundant with professional_summary)
- Include ALL certifications from the CV data — do NOT truncate or omit any.

=== DO NOT INVENT FIELDS (CRITICAL — the output schema is strict and rejects unknown fields) ===
For each `experience[]` and `projects[]` entry, the ONLY bullet-bearing field is `description` (a flat list of strings). Producing ANY of these will cause the entry to be rejected:
  • experience[].highlights         → use `description` instead
  • experience[].achievements       → use `description`
  • experience[].responsibilities   → use `description`
  • experience[].accomplishments    → use `description`
  • experience[].bullets / tasks    → use `description`
  • projects[].highlights           → use `description` (no separate "structured outcomes" field — bullets and outcomes go in description together)
  • projects[].features / outcomes  → use `description`
  • projects[].deliverables         → use `description`
  • ANY nested wrapper inside description — e.g., `description: [{{"text": [...]}}]` is WRONG; produce `description: ["bullet 1", "bullet 2"]` directly.

The `description` field is a flat list of strings. Each string is one bullet point on the resume. Never split bullets across multiple field names.

=== PRIMITIVE SHAPES (CRITICAL — Groq rejects the tool call if any of these are wrong) ===
  • `skills` is a list of PLAIN STRINGS. Correct: `["Python", "PySpark", "SQL"]`. WRONG: `[{{"name": "Python", "years": null, "proficiency": null}}, ...]` — Groq will reject the entire generation with HTTP 400 because the schema declares `List[str]`. Do not wrap each skill in an object.
  • `languages` is a list of plain strings, SPOKEN (human) languages ONLY — "Arabic (Native)", "English (Fluent)". NEVER programming languages, libraries, frameworks, or tech ("Python", "Pandas", "SQL" all belong in `skills`, not `languages`). The downstream sanitizer drops any non-spoken-language entry, so misrouting wastes the slot.
  • `experience[]` has NO `employment_type` field. Don't emit `"employment_type": "Internship"` / `"Full-time"` / `"Contract"`. Employment type is already in "REMOVE FROM RESUMES" above.
  • `projects[]` has NO `source` / `source_id` / `source_url` / `role` / `duration` field — those are signal-only inputs documented in FIELD MAPPING and must not appear in the output.

=== EVIDENCE-GROUNDED ENRICHMENT RULE (CRITICAL — read this twice) ===
Every concrete claim (a number, a tool name, a scale, a duration, a team size, a metric) must be supported by AT LEAST ONE source you've been given:
  (a) the CV's own bullets / skills / education / projects;
  (b) the GITHUB ACTIVITY block (use to corroborate language fluency, scale of work, recent activity);
  (c) the GOOGLE SCHOLAR block (use to corroborate research depth, publications, methods);
  (d) the KAGGLE block (use to corroborate competition wins, ML/data depth, medal counts);
  (e) the GAP ANALYSIS block (use to know which JD-required skills the candidate genuinely has — never claim a MISSING skill).

When the JD emphasizes a skill the candidate genuinely has, you MAY enrich the bullet using corroborating evidence — e.g., promote "Built a model" to "Modelled churn across 2M events" if the CV mentioned "2M users" elsewhere, or "across 12 production repos" if GitHub language_breakdown shows that. Mentally tag each enrichment with its source ("from GitHub: Python in 8 repos") before writing.

If NO source supports a specific claim — keep it qualitative. Never fabricate a number, a team size, a company name, an outcome, or a tool you can't see. The candidate's MISSING skills are explicitly listed in the GAP ANALYSIS block; never claim those.

This is the difference between a tailored resume and a hallucinated one. Restructuring is not enough on its own; enrichment from corroborated sources is what makes the resume actually feel job-specific.

=== REMOVE FROM RESUMES ===
- Street/home address (city and country are fine)
- Objective statements
- Graduation year if the degree is more than 10 years old
- Work experience older than 15 years (20 years max for executive roles)
- High school experience
- GPA or university grades
- Headshot or photo references
- Employment type labels (contract, part-time, etc.)
- Salary expectations
- First-person "I" statements

=== LANGUAGE & STYLE ===
- See the HUMAN VOICE block at the end of this prompt for the full banned-word list and sentence-structure rules.
- Replace these words: Spearheaded -> Led, Leveraged -> Used/Applied, Utilized -> Used, Synergized -> Collaborated, Streamlined -> Simplified/Improved, Robust -> Strong, Demonstrated -> Showed/Proved, Facilitated -> Helped/Enabled.

=== CV CLEANUP RULES (CRITICAL — the source CV has parser / LinkedIn artefacts) ===
1. TITLE CASING: convert every ALL-CAPS title to standard Title Case. "DIGITAL TRANSFORMATION INTERN" -> "Digital Transformation Intern". "INFROMATION TECHNOLOGY INTERN" -> "Information Technology Intern" (fix the parser typo "INFROMATION" -> "Information"). Preserve well-known acronyms uppercase (AI, ML, IT, HR, SAP, ERP, SQL, AWS, GCP, NLP, MLOps, DevOps, API, UI, UX, iOS).
2. HARD-SKILL ENFORCEMENT: the Skills section MUST contain ONLY hard / technical skills (programming languages, libraries, frameworks, tools, methods, platforms). NEVER include "Communication", "Communications Planning", "Presentation skills", "Leadership", "Teamwork", "Team Management", "People Development", "Project Management", "Problem-solving", "Critical Thinking", "Adaptability", "Time Management", "Collaboration", or any other soft skill. Soft skills can appear in bullet text contextually as a side effect — never in the Skills array.
3. COURSEWORK CONSOLIDATION: if an experience description contains a coursework / topic list (lines that start with `•`, `-`, or `*` and name short noun phrases like "Prompt Engineering", "Data Science Methodology", "Tools for Data Science"), CONSOLIDATE them into ONE sentence inside ONE bullet: "Coursework included: Prompt Engineering, Data Science Methodology, Tools for Data Science." Never preserve the bullet-per-course shape — it renders as visual noise and ATS scanners read it as filler.
4. PROJECT NAME CANONICALIZATION: project names use the readable display form, not the GitHub slug or the verbose CV name. "healthcare-prediction-depi" -> "Healthcare Prediction (DEPI)". "customer-segmentation-rfmt" -> "Customer Segmentation (RFMT)". "BRAIN TUMOR CLASSIFICATION APP" -> "Brain Tumor Classification App". Preserve well-known mixed-case names ("SmartCV", "BookShop") as-is.
5. CERTIFICATION SCOPE: include ONLY certifications listed in the INCLUSION PLAN above (when one is provided) — drop everything else. Without a plan, include only certifications whose name or issuer is JD-relevant.
6. NO SOFT-SKILL BULLETS: never write a bullet whose entire content is about soft skills (e.g. "Developed soft skills including cross-team communication, problem-solving, and adaptability in a corporate environment"). Those read as filler. If you must mention a soft skill, weave it into a bullet that ALSO names a hard outcome — "Partnered with the SAP team to ship a procurement dashboard" beats "Built strong communication and teamwork skills with the SAP team".

=== BULLET POINT STANDARDS (CRITICAL for resume quality) ===
- Each experience role: 3-5 bullets. Never more, never fewer if data exists.
- Each project: 2-3 bullets max.
- Bullet length: 1-2 lines each, roughly 15-25 words. No walls of text, no one-word bullets.

{BULLET_QUALITY_AND_SAFETY_RULES}

=== LENGTH & DENSITY ===
- Professional summary: 2-3 sentences, 40-60 words max. No fluff.
- Skills list: 8-15 items. Prioritize job-required skills that the candidate actually has (matched_skills first, then supporting technical skills).
- Total resume should fit one page for candidates with <5 years experience, maximum two pages otherwise.

=== REWRITE & STRUCTURING ===
1. PROFESSIONAL SUMMARY:
   - REQUIRED — never leave this field empty. The summary is the recruiter's 5-second hook; an empty summary signals "this candidate didn't bother". If the voice constraints below feel restrictive, write a SHORT, FACTUAL summary (one sentence is fine) rather than nothing.
   - Use NEUTRAL, DIRECT voice. No "I" / "my" pronouns. NO third-person references to the candidate by name (NEVER write "Zeyad has built..." or "Sara is a...") — referring to oneself by name in one's own resume reads as ghost-written and unprofessional.
   - Lead with the role and what the candidate does, not how long they've done it.
   - ONE PRIMARY ROLE — never lead with a pipe-separated multi-title header like "Data Scientist | AI Engineer | Data Analyst". That pattern reads as "I'm applying to anything" and dilutes the JD match. Pick the SINGLE role the job is hiring for (use the JD title from the header above as the anchor) and write a focused first sentence about THAT role. If adjacent skills are worth surfacing, weave them into the body sentence ("...with applied experience in NLP and dashboarding"), never as a co-equal title.
   - NO HEADLINE-STUFFING PATTERN — also avoid the LinkedIn-headline structure "{{Role}} with a focus on X, Y, and Z. Proficient in A, B, C. Experienced in deploying D and working with E." That reads as ATS keyword bait, not positioning. Use narrative prose instead.
   - NO UNSUPPORTED CAPABILITY CLAIMS — phrases like "experienced in deploying AI models" or "working with large datasets" must be backed by an experience or project bullet that actually shows deployment / scale. If no bullet supports it, drop the claim. The summary cannot promise something the experience section doesn't deliver.
   - YoE / TENURE CLAIMS: Never invent or estimate years of experience. Only state YoE when the source CV's experience entries support it via real start_date / end_date dates that span at least 12 months total. If the candidate's only experience is an internship or a recent role under a year, do NOT use phrases like "X+ years experience", "early-career", "less than N years experience", or any framing that implies a duration. Just describe what they do and one concrete proof point. Example for a fresh-out-of-school candidate with one short role: "AI & Tooling Engineer focused on data pipelines in Microsoft Fabric, with hands-on PySpark and Python work across automation and ERP integration." NOT "early-career engineer with less than 2 years of experience".
   - 2-3 sentences, 40-60 words max. No fluff.
   - Reflect ONLY experience already present in the resume + corroborated signals.
2. SKILLS SECTION: Remove ALL soft skills. Keep ONLY hard/technical skills explicitly listed.
3. EXPERIENCE BULLETS: Start each bullet with a strong action verb. Use STAR structure where possible (Situation/Task → Action → Result).
4. MOST RECENT EXPERIENCE FIRST: Within EXPERIENCE, order entries newest first.
5. PROJECT ORDERING (DIFFERENT FROM EXPERIENCE): order projects by demonstrated DEPTH for the target role, not by recency or by raw JD-keyword overlap. The first project in the list is the one the recruiter reads first, so it must be the one that most directly signals "I can do this job":
   - Production-deployed system > toy notebook
   - End-to-end pipeline (parse → analyze → serve) > isolated EDA notebook
   - Real evaluation metrics in the bullets (silhouette score, F1, accuracy, k-clusters, dataset size) > qualitative descriptions only
   - Diverse / production-grade tech stack (e.g., Django + PostgreSQL + LLM + vector DB) > narrow stack (single notebook + pandas)
   - Custom system the candidate designed > standard tutorial implementation
   The INCLUSION PLAN below provides a JD-overlap ranking, but it under-weights tech-stack diversity and production signals; you may override its order when a less-overlapping project clearly demonstrates more depth. NEVER auto-default to "the one with most matched JD tokens leads".

=== ATS OPTIMIZATION ===
- Use standard section names: "Professional Summary", "Skills", "Experience", "Education", "Projects", "Certifications".
- Spell out acronyms on first use where non-obvious (e.g., "SEO (Search Engine Optimization)"). Keep industry-standard acronyms as-is (SQL, AWS, API).
- Keep job titles in the output identical to how they appear in the source CV. Only fix clear typos.
- Ensure every skill from Required Skills that the candidate genuinely has appears at least once in the resume (skills section or bullets).

=== THEME MIRRORING ===
1. Identify 3 key themes from the job posting.
2. Mirror those themes in the title, summary, and bullet point headings.
3. CRITICAL: ONLY mirror themes genuinely supported by existing experience.

{domain_section}

{standards_section}

Make it PROFESSIONAL and ATS-OPTIMIZED.

{HUMAN_VOICE_RULE}"""

    def _post_process(resume_content: dict) -> dict:
        """Shared post-LLM pipeline. Extracted so the slim-prompt retry
        path applies the exact same cleanup as the happy path."""
        resume_content = _strip_schema_envelope_leaks(resume_content)
        resume_content = _ensure_profile_data_preserved(resume_content, sanitized_cv)
        # Fix-2 (2026-06-01) — main-gen role-identity guard. The LLM can
        # return a fabricated role (the trace caught "Banque Misr"
        # through the regen path; the same risk exists here). Drop any
        # returned experience/project entry whose identity doesn't
        # match a real entry in the master profile. Log every drop so
        # we see when the model fabricates.
        from resumes.services.role_identity_guard import (
            filter_experiences_to_known,
            filter_projects_to_known,
            log_dropped,
        )
        master_exps = (sanitized_cv or {}).get('experiences') or []
        returned_exps = resume_content.get('experience') or []
        if returned_exps and master_exps:
            kept_exps, dropped_exps = filter_experiences_to_known(
                returned_exps, master_exps,
            )
            if dropped_exps:
                log_dropped(dropped_exps, kind='experience', surface='main-gen')
                resume_content['experience'] = kept_exps
        master_projs = (sanitized_cv or {}).get('projects') or []
        returned_projs = resume_content.get('projects') or []
        if returned_projs and master_projs:
            kept_projs, dropped_projs = filter_projects_to_known(
                returned_projs, master_projs,
            )
            if dropped_projs:
                log_dropped(dropped_projs, kind='projects', surface='main-gen')
                resume_content['projects'] = kept_projs
        resume_content = _apply_bullet_validator(resume_content)
        resume_content = normalize_resume(
            resume_content, plan=inclusion_plan, job=job, profile_data=sanitized_cv,
        )
        resume_content = _apply_v2_grounding_check(
            resume_content, inclusion_plan, profile, job, gap_analysis,
        )
        # Fix #1 — regression check against the user's last exported
        # version (when one exists AND the JD hash matches). Findings
        # land on validation_report['regression_findings'] for the
        # supervised loop to read.
        resume_content = _apply_regression_check(
            resume_content, previous_best, current_job_hash=_jd_identity_hash(job),
        )
        # PR 4 — surface classification, plan, and retrieval metadata to
        # any caller that passes ``metadata={}``. Test harness reads these
        # for assertions; production callers (resumes/tasks.py) pass nothing
        # and the dict goes unused. Keys are underscore-prefixed so they
        # don't collide with schema fields the renderer reads.
        if metadata is not None:
            if classification_obj is not None:
                metadata['_classification'] = {
                    'primary_role': getattr(classification_obj, 'primary_role', ''),
                    'seniority': getattr(classification_obj, 'seniority', ''),
                    'region': getattr(classification_obj, 'region', ''),
                    'profile_role': getattr(classification_obj, 'profile_role', ''),
                }
            else:
                metadata['_classification'] = {}
            metadata['_retrieval_metadata'] = retrieval_metadata
            if inclusion_plan is not None:
                metadata['_plan_metadata'] = {
                    'project_count_in_plan': len(getattr(inclusion_plan, 'projects', []) or []),
                    'cert_count_in_plan': len(getattr(inclusion_plan, 'certifications', []) or []),
                    'skill_count_in_plan': len(getattr(inclusion_plan, 'skills_to_list', []) or []),
                    'project_names_in_plan': [
                        getattr(p, 'name', '') for p in (getattr(inclusion_plan, 'projects', []) or [])
                    ],
                    'cert_names_in_plan': list(getattr(inclusion_plan, 'certifications', []) or []),
                }
            else:
                metadata['_plan_metadata'] = {}
        return resume_content

    # Issue 8 (2026-05-22): skip the doomed full-prompt call when we can
    # predict the 413. The full prompt 413s above Groq's per-request
    # ceiling; rather than burn a fast-failing round-trip, pre-slim when
    # the prompt exceeds RESUME_PROMPT_CHAR_BUDGET (drops the same v2 +
    # standards blocks the 413 retry would). The retry below stays as the
    # safety net for sizes we under-estimate. Observed: full 87.7k chars
    # 413s; slim 78.9k succeeds — default budget 85k sits between.
    from django.conf import settings as _dj_settings
    _char_budget = int(getattr(_dj_settings, 'RESUME_PROMPT_CHAR_BUDGET', 85000))
    # Fix-B (2026-05-31): keep the un-slimmed prompt aside. The pre-slim
    # path mutates `prompt`; if we then 413 and try to slim AGAIN in the
    # except branch using the same `prompt` variable, .replace() runs on
    # an already-trimmed string → saved=0 (the bug observed in dev logs).
    # `_original_prompt` is the safety net the retry path uses.
    _original_prompt = prompt
    _pre_slimmed = False
    if len(prompt) > _char_budget and (v2_block or standards_section):
        _slimmed = prompt
        if v2_block:
            _slimmed = _slimmed.replace(v2_block, '')
        if standards_section:
            _slimmed = _slimmed.replace(standards_section, '')
        logger.info(
            "Resume gen: prompt %d chars > budget %d; pre-slimming to %d "
            "(skipping the full call that would 413).",
            len(prompt), _char_budget, len(_slimmed),
        )
        prompt = _slimmed
        _pre_slimmed = True

    try:
        structured_llm = get_structured_llm(ResumeContentResult, temperature=0.7, max_tokens=8192, task="resume_gen")
        result = structured_llm.invoke(prompt)

        resume_content = _post_process(result.model_dump())
        logger.info(f"✓ Generated tailored resume with sections: {list(resume_content.keys())}")
        return resume_content

    except Exception as e:
        # ── Token-limit retry ─────────────────────────────────────────
        # If Groq rejected the call because the prompt exceeded the
        # tokens-per-minute ceiling (413 / rate_limit_exceeded with
        # `type: tokens`), retry ONCE with the biggest two dynamic
        # blocks stripped: the v2 grounding evidence (10-15k chars on
        # a rich profile) and the RAG standards block (~3k chars).
        # The inclusion plan is still enforced post-LLM via
        # normalize_resume, so dropping the v2 block from the prompt
        # doesn't lose the selection rules — the LLM just rewrites
        # bullets without the per-skill evidence snippets to lean on.
        if (_is_token_limit_error(e) and (v2_block or standards_section)
                and not _pre_slimmed):
            # Retry slims from the ORIGINAL prompt — `prompt` is identical
            # to `_original_prompt` here since pre-slim didn't run (the
            # `not _pre_slimmed` guard above). When pre-slim DID run, the
            # retry has nothing left to trim (it'd produce saved=0); fall
            # through to the recovery / offline-fallback path instead.
            slim_prompt = _original_prompt
            if v2_block:
                slim_prompt = slim_prompt.replace(v2_block, '')
            if standards_section:
                slim_prompt = slim_prompt.replace(standards_section, '')
            logger.warning(
                "Resume gen: token-limit hit (full=%d chars). Retrying with "
                "v2_block + standards trimmed (slim=%d chars, saved=%d).",
                len(_original_prompt), len(slim_prompt),
                len(_original_prompt) - len(slim_prompt),
            )
            try:
                slim_llm = get_structured_llm(
                    ResumeContentResult, temperature=0.7,
                    max_tokens=8192, task="resume_gen",
                )
                slim_result = slim_llm.invoke(slim_prompt)
                resume_content = _post_process(slim_result.model_dump())
                logger.info(
                    "✓ Resume gen succeeded via slim-prompt retry (full prompt "
                    "hit token limit); sections=%s",
                    list(resume_content.keys()),
                )
                return resume_content
            except Exception as slim_exc:
                # Slim retry also failed — flow into the regular
                # failed_generation salvage / offline-fallback path with
                # the slim exception (its failed_generation, if any, is
                # what we'd want to recover).
                logger.warning(
                    "Resume gen: slim-prompt retry also failed (%s) — "
                    "falling through to recovery.", slim_exc,
                )
                e = slim_exc
        # Try to salvage from Groq's tool_use_failed before giving up. The
        # model often produces well-formed content but fails the strict
        # tool-call validator (null in string field, list-of-objects where
        # list-of-strings was expected, etc.). Schema's before-validators
        # coerce both shapes to the canonical form. Same recovery pattern
        # as profiles.services.outreach_generator and
        # analysis.services.learning_path_generator.
        recovered = _recover_resume_from_failed_generation(e)
        if recovered is not None:
            # Fix-3 (2026-06-01) — route the salvaged content through the
            # SAME post-process pipeline as the happy path: identity
            # guard, bullet validator, normalizer, grounding check,
            # regression check. The prior code only ran 3 of those 6,
            # so a recovered resume could ship with un-flagged fabricated
            # metrics OR a phantom role. Reuse _post_process to inherit
            # every safety net (including the FIX-2 identity guard added
            # above).
            resume_content = _post_process(recovered.model_dump())
            logger.info(
                "Resume recovered from failed_generation; sections=%s",
                list(resume_content.keys()),
            )
            return resume_content
        logger.exception(f"Resume generation error: {e}")
        # Offline fallback uses the sanitized CV so a hard failure renders
        # cleaned data, not the parser artefacts.
        return _build_offline_fallback(profile, job, sanitized_cv)


def _format_supervisor_feedback(findings) -> str:
    """Numbered, content-blocking-only feedback for the regen prompt (cap 8)."""
    lines = []
    for i, f in enumerate(findings[:8], 1):
        loc = (f.location or "").strip()
        loc_str = f" [{loc}]" if loc else ""
        lines.append(f"{i}. ({f.category}){loc_str} {f.issue} -> FIX: {f.fix}")
    return "\n".join(lines)


def _format_regression_feedback(findings) -> str:
    """Build the regression-loss revision instruction. Capped at 8 items
    so a pathological diff can't blow the prompt budget; aligns with
    _format_supervisor_feedback's cap."""
    if not findings:
        return ""
    lines = ["", "REGRESSION (vs your last exported version — MUST restore):"]
    for i, f in enumerate(findings[:8], 1):
        where = f.get('where') or ''
        detail = f.get('detail') or ''
        lines.append(f"{i}. [{where}] {detail}")
    return "\n".join(lines)


def generate_resume_content_supervised(profile, job, gap_analysis, *,
                                        metadata: dict | None = None,
                                        previous_best: dict | None = None):
    """generate_resume_content + an HR/CV supervisor review loop.

    Generate -> review (KB-grounded, render-aware) -> if blocking CONTENT issues
    remain and the round cap isn't hit, regenerate with the feedback injected ->
    re-review -> ship. Render/layout findings are surfaced but never drive the
    loop (regeneration can't fix them). The review fails open, so the supervisor
    can never block a resume from shipping.

    Fix #1 — the supervised loop ALSO consumes regression findings written by
    _apply_regression_check (metric_loss, bullet_count_drop). They share the
    SAME revision-round budget (SUPERVISOR_MAX_REVISION_ROUNDS) — there is no
    second independent loop. When both supervisor and regression findings are
    open in the same round, both go into the same feedback string. The shared
    cap guarantees termination (at most cap+1 rounds; identical to today's
    behaviour). On cap exhaustion, any remaining BLOCKING regression findings
    are DEMOTED to severity='warning' on the shipped draft so the fix-#2
    banner surfaces them rather than the user seeing an error.

    When SUPERVISOR_ENABLED is off this is a transparent pass-through to
    generate_resume_content (which still runs the regression check inline;
    findings just don't drive a retry).
    """
    from django.conf import settings as _dj
    if not getattr(_dj, 'SUPERVISOR_ENABLED', False):
        return generate_resume_content(
            profile, job, gap_analysis,
            metadata=metadata, previous_best=previous_best,
        )

    # Lazy import — resume_supervisor imports helpers from this module, so a
    # top-level import here would be circular.
    from resumes.services.resume_supervisor import review_resume

    cap = int(getattr(_dj, 'SUPERVISOR_MAX_REVISION_ROUNDS', 1))
    # Retrieve the KB block once and reuse it across rounds (the override path
    # in generate_resume_content skips its own retrieval).
    standards_block, _, _ = _build_standards_section(profile, job)

    feedback = ""
    resume_content: dict | None = None
    review = None
    rounds_run = 0
    # Best-draft elitism (Fix #3): keep the draft with the FEWEST blocking
    # findings across rounds. Score now includes regression findings so a
    # regen that loses a metric is treated as a regression in the same
    # bucket as a supervisor blocker.
    best_content: dict | None = None
    best_review = None
    best_round = -1
    best_score: tuple[int, int] | None = None
    last_regression_findings: list[dict] = []
    for round_i in range(cap + 1):
        rounds_run = round_i + 1
        resume_content = generate_resume_content(
            profile, job, gap_analysis,
            metadata=metadata,
            supervisor_feedback=feedback,
            standards_section_override=standards_block,
            previous_best=previous_best,
        )
        # Fix-C (2026-05-31): if the main generation 413'd / errored and
        # fell to _build_offline_fallback, the result is profile-derived
        # boilerplate — not an LLM-tailored draft. Reviewing it with the
        # supervisor would burn 2+ more Groq calls against the same TPM
        # window that just rate-limited us, all to grade a non-LLM
        # placeholder. Skip the loop entirely; ship the fallback as-is
        # with the marker preserved so the UI can show a degraded-mode
        # banner.
        if isinstance(resume_content, dict) and resume_content.get('_is_fallback'):
            logger.warning(
                "Supervised+regression: round %d returned the offline "
                "fallback (LLM unavailable) — skipping supervisor review "
                "and shipping the fallback. User is in DEGRADED MODE.",
                round_i,
            )
            best_content = resume_content
            best_review = None
            best_round = round_i
            review = None
            break
        # Defensive re-run of the regression check on the supervised loop's
        # view of the result. _post_process inside generate_resume_content
        # already ran the check on the happy path; calling it again here is
        # idempotent (deterministic diff + JD-hash gate = same outputs for
        # same inputs). This makes the loop robust to a generate_resume_content
        # that returns a dict without a populated validation_report — e.g.
        # the offline fallback path, OR a test harness that mocks
        # generate_resume_content.
        if isinstance(resume_content, dict) and previous_best:
            resume_content = _apply_regression_check(
                resume_content, previous_best,
                current_job_hash=_jd_identity_hash(job),
            )
        try:
            review = review_resume(
                resume_content, profile, job, gap_analysis, standards_block=standards_block,
            )
        except Exception as exc:  # noqa: BLE001 — review must never block shipping
            logger.warning("Supervisor review raised (%s); shipping current draft.", exc)
            review = None
            break
        # Findings classification policy: only AUTO_FIXABLE blockers
        # drive a regen round. NEEDS_USER_INPUT findings (unsupported
        # metric, metric_loss, missing field, …) bypass the loop —
        # regenerating them would fabricate or delete the user's real
        # content. They surface as "Confirm or complete" instead.
        from resumes.services.findings_classifier import (
            classify_finding, BUCKET_AUTO_FIX,
        )
        all_blocking = review.blocking_content_findings()
        blocking = [
            f for f in all_blocking
            if classify_finding('supervisor', {
                'category': getattr(f, 'category', '') or '',
                'severity': getattr(f, 'severity', '') or '',
                'layer': getattr(f, 'layer', '') or '',
            }) == BUCKET_AUTO_FIX
        ]
        # Pull regression findings written by _apply_regression_check inside
        # _post_process. Severity is set deterministically:
        #   metric_loss / bullet_count_drop → 'blocking' (must be restored)
        #   skill_loss → 'warning'           (advisory, doesn't drive regen)
        vr = resume_content.get('validation_report') if isinstance(resume_content, dict) else {}
        regression_findings = list((vr or {}).get('regression_findings') or [])
        regression_blocking = [
            f for f in regression_findings
            if (f.get('severity') or '').lower() == 'blocking'
            and classify_finding('regression', f) == BUCKET_AUTO_FIX
        ]
        # User-input blockers that BYPASSED the loop — kept in the
        # validation_report so the UI can surface them under "Confirm
        # or complete" without inflating the regen round count.
        regression_user_input = [
            f for f in regression_findings
            if (f.get('severity') or '').lower() == 'blocking'
            and classify_finding('regression', f) != BUCKET_AUTO_FIX
        ]
        supervisor_user_input_blockers = [
            f for f in all_blocking
            if classify_finding('supervisor', {
                'category': getattr(f, 'category', '') or '',
                'severity': getattr(f, 'severity', '') or '',
                'layer': getattr(f, 'layer', '') or '',
            }) != BUCKET_AUTO_FIX
        ]
        if regression_user_input or supervisor_user_input_blockers:
            logger.info(
                "Supervisor round %d: %d user-input blocker(s) bypassed the "
                "loop (regression=%d, supervisor=%d) — they will surface to "
                "the user as 'Confirm or complete'.",
                round_i, len(regression_user_input) + len(supervisor_user_input_blockers),
                len(regression_user_input), len(supervisor_user_input_blockers),
            )
        last_regression_findings = regression_findings
        render_count = len([f for f in review.findings if f.layer == 'render'])
        logger.info(
            "Supervisor round %d: %d sup-findings (%d content-blocking, %d render) "
            "+ %d regression-findings (%d blocking) verdict=%s",
            round_i, len(review.findings), len(blocking), render_count,
            len(regression_findings), len(regression_blocking), review.verdict,
        )
        # Score on COMBINED blocking count. Both finding types are
        # high-precision (supervisor's are deal-breakers; regression's are
        # deterministic deltas) so they get equal weight.
        combined_blocking_count = len(blocking) + len(regression_blocking)
        combined_total = len(review.findings) + len(regression_findings)
        score = (combined_blocking_count, combined_total)
        if best_score is None or score < best_score:
            best_content = resume_content
            best_review = review
            best_round = round_i
            best_score = score
        if combined_blocking_count == 0:
            break
        if round_i >= cap:
            if best_round != round_i:
                logger.info(
                    "Supervised+regression: round cap (%d) reached; round %d "
                    "regressed (blocking=%d total=%d) vs round %d (blocking=%d "
                    "total=%d) — shipping the earlier draft.",
                    cap, round_i, score[0], score[1],
                    best_round, best_score[0], best_score[1],
                )
            else:
                logger.info(
                    "Supervised+regression: round cap (%d) reached with %d "
                    "blocking issues (sup=%d, regression=%d); shipping.",
                    cap, combined_blocking_count, len(blocking), len(regression_blocking),
                )
            break
        # Combined feedback: supervisor findings + regression findings share
        # ONE revision instruction. They usually align (restoring a metric
        # IS a content fix), so emitting both together is the natural shape.
        feedback = (
            _format_supervisor_feedback(blocking)
            + _format_regression_feedback(regression_blocking)
        )

    # Ship the best draft observed, not necessarily the last one.
    if best_content is not None:
        resume_content = best_content
        review = best_review

    if isinstance(resume_content, dict):
        # Cap-exhaustion fallback: if blocking regression findings still
        # stand on the SHIPPED draft (the best one), demote them to
        # 'warning' so the fix-#2 banner surfaces them instead of the
        # user seeing an error. Mirrors the supervisor's existing
        # ship-with-revise-verdict behaviour on cap-hit.
        vr_final = resume_content.setdefault('validation_report', {})
        if isinstance(vr_final, dict):
            shipped_regression = list((vr_final.get('regression_findings') or []))
            demoted = 0
            # Cap-exhaustion: only demote AUTO_FIXABLE blockers. USER_INPUT
            # regression findings (metric_loss, bullet_count_drop) stay at
            # blocking severity and surface to the user under "Confirm or
            # complete" — the loop never owned them.
            from resumes.services.findings_classifier import (
                classify_finding as _classify, BUCKET_AUTO_FIX as _AUTOFIX,
            )
            for f in shipped_regression:
                if (f.get('severity') or '').lower() == 'blocking' \
                        and _classify('regression', f) == _AUTOFIX:
                    f['severity'] = 'warning'
                    demoted += 1
            if demoted:
                logger.info(
                    "Supervised+regression: %d blocking regression finding(s) "
                    "demoted to 'warning' on shipped draft (cap exhausted).",
                    demoted,
                )
                vr_final['regression_findings'] = shipped_regression

    if review is not None and isinstance(resume_content, dict):
        all_findings = [
            {'layer': f.layer, 'severity': f.severity, 'category': f.category,
             'location': f.location, 'issue': f.issue, 'fix': f.fix}
            for f in review.findings
        ]
        # Cap-exhaustion fallback (supervisor side). Mirrors the regression
        # demote above: if the loop ran the full cap and shipped a draft
        # with AUTO_FIXABLE supervisor blockers still on it, demote them
        # to 'warning'. The loop tried and failed — the user shouldn't
        # see "to fix" alarm for issues the system owned. USER_INPUT
        # supervisor blockers (category='grounding', unknown categories
        # via fail-safe) keep their 'blocking' severity and surface
        # under "Confirm or complete" instead.
        from resumes.services.findings_classifier import (
            classify_supervisor as _classify_sup, BUCKET_AUTO_FIX as _AUTOFIX_SUP,
        )
        sup_demoted = 0
        for f in all_findings:
            if (f.get('severity') or '').lower() == 'blocking' \
                    and (f.get('layer') or 'content').lower() == 'content' \
                    and _classify_sup(
                        f.get('category', ''),
                        f.get('severity', ''),
                        f.get('layer', ''),
                    ) == _AUTOFIX_SUP:
                f['severity'] = 'warning'
                sup_demoted += 1
        if sup_demoted:
            logger.info(
                "Supervised+regression: %d AUTO_FIX supervisor blocker(s) "
                "demoted to 'warning' on shipped draft (cap exhausted).",
                sup_demoted,
            )
        resume_content['supervisor_review'] = {
            'verdict': review.verdict,
            'summary': review.summary,
            'rounds': rounds_run,
            'findings': all_findings,
        }
        vr = resume_content.setdefault('validation_report', {})
        if isinstance(vr, dict):
            vr['supervisor_findings'] = all_findings
    return resume_content


def load_previous_best_for(gap_analysis) -> dict | None:
    """Find the most recent populated previous_best snapshot across all
    GeneratedResume rows for this gap_analysis. Path A's generate_resume_task
    creates a NEW row each call, so the snapshot from the prior row carries
    forward.

    Returns the JSONField dict (with 'content', 'exported_at',
    'ats_score_at_export', 'jd_identity_hash' keys) or None when no prior
    export exists for this (profile, job)."""
    from resumes.models import GeneratedResume
    try:
        row = (
            GeneratedResume.objects
            .filter(gap_analysis=gap_analysis)
            .exclude(previous_best={})
            .exclude(previous_best=None)
            .order_by('-created_at')
            .first()
        )
    except Exception as exc:  # noqa: BLE001 — never let lookup failure break generation
        logger.warning("load_previous_best_for: lookup failed (%s); proceeding without snapshot.", exc)
        return None
    if row is None:
        return None
    snap = row.previous_best
    if isinstance(snap, dict) and snap.get('content'):
        return snap
    return None


_SCHEMA_ENVELOPE_KEYS = frozenset(('additionalProperties', 'properties', 'type', 'items'))


def _strip_schema_envelope_leaks(resume_content: dict) -> dict:
    """Strip schema-envelope leftover keys from a happy-path generation.

    The structured-output LLM sometimes returns

        {"additionalProperties": true,
         "properties": {<actual fields>},
         "type": "object"}

    on a successful 200, instead of the flat instance shape the
    Pydantic schema declares. ``model_config = {"extra": "allow"}`` on
    ResumeContentResult lets the envelope keys through as undeclared
    extras, which then leak into the section ordering and downstream
    writers. The recovery path already handles this for failed
    generations; this helper applies the same unwrap to a happy-path
    dict.

    Logic:
      1. If the dict has BOTH ``properties`` (dict) AND
         ``additionalProperties``, replace the dict with its
         ``properties`` payload (the actual instance lives there).
      2. Strip any remaining envelope keys at the top level.
      3. For any value that still looks like a ``{type, value}``
         wrapper, replace it with the inner ``value``.
    """
    if not isinstance(resume_content, dict):
        return resume_content
    # Step 1: full envelope shape (top level is ONLY envelope keys) —
    # step into `properties`. Detection: every top-level key is in the
    # envelope set. In the mixed shape (real fields + leaked envelope
    # keys side by side, which is the production case), don't step in —
    # just strip the envelope keys in Step 2 and keep the real fields.
    has_full_envelope = (
        isinstance(resume_content.get('properties'), dict)
        and 'additionalProperties' in resume_content
        and all(k in _SCHEMA_ENVELOPE_KEYS for k in resume_content.keys())
    )
    if has_full_envelope:
        logger.info(
            "Happy-path schema-envelope detected; stepping into `properties`."
        )
        resume_content = dict(resume_content['properties'])
    # Step 2: leftover envelope keys at the top level (sometimes the
    # envelope wraps the real fields AND adds extras alongside them).
    leaked = [k for k in _SCHEMA_ENVELOPE_KEYS if k in resume_content]
    if leaked:
        logger.info(
            "Stripping leftover schema-envelope keys: %s", leaked,
        )
        for k in leaked:
            resume_content.pop(k, None)
    # Step 3: per-field `{type, value}` wrappers.
    unwrapped: dict = {}
    for key, val in resume_content.items():
        if (
            isinstance(val, dict)
            and 'value' in val
            and 'type' in val
            and set(val.keys()) <= {'type', 'value', 'description', 'items'}
        ):
            unwrapped[key] = val['value']
        else:
            unwrapped[key] = val
    return unwrapped


def _is_token_limit_error(exc) -> bool:
    """Return True iff the exception is Groq's 413/rate_limit token
    ceiling rejection (prompt-too-large), not a generic 4xx/5xx.

    Distinguishing the token-limit case from other failures matters
    because the only useful recovery is to retry with a smaller prompt
    — other failures benefit from the failed_generation salvage path
    or just need to fall through to the offline renderer.
    """
    # exc.body dict (newer groq SDK) is the cleanest discriminator.
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        err = body.get('error') or {}
        err_type = (err.get('type') or '').lower()
        err_code = (err.get('code') or '').lower()
        if err_type == 'tokens' or err_code == 'rate_limit_exceeded':
            return True
    # str(exc) fallback — match the message Groq formats.
    s = str(exc).lower()
    return (
        ('request too large' in s or 'rate_limit_exceeded' in s)
        and ('token' in s or 'tpm' in s)
    )


def _extract_failed_generation(exc) -> Optional[str]:
    """Pull the ``failed_generation`` payload from a Groq exception.

    The groq SDK has stored the response body in different attributes
    across versions:

      - newer: ``exc.body`` is the parsed JSON dict
      - sometimes: ``exc.body`` is None but ``exc.response`` carries
        the raw httpx Response with ``.json()`` / ``.text``
      - fallback: the dict is rendered into ``str(exc)`` as a Python
        repr (single-quoted), parseable with ``ast.literal_eval``
        (the safe literal-only parser — rejects function calls and
        arbitrary code, only handles dict/list/str/int/float/bool/
        None literals).

    Without the last fallback the recovery silently returns None when
    ``exc.body`` is None, which is what was masking the schema-envelope
    payload on the regen attempt at 18:03.
    """
    # Path 1: exc.body is already a dict.
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        raw = (body.get('error') or {}).get('failed_generation')
        if isinstance(raw, str) and raw:
            return raw
    # Path 2: exc.response with .json() — try parsing.
    response = getattr(exc, 'response', None)
    if response is not None:
        for attempt in ('json', 'text'):
            try:
                if attempt == 'json':
                    data = response.json()
                else:
                    data = json.loads(getattr(response, 'text', '') or '')
            except Exception:
                continue
            if isinstance(data, dict):
                raw = (data.get('error') or {}).get('failed_generation')
                if isinstance(raw, str) and raw:
                    return raw
    # Path 3: parse the Python repr embedded in str(exc).
    # Groq formats the message as "Error code: 400 - {'error': {...}}",
    # and the dict portion is a valid Python literal. ast.literal_eval
    # (the safe-by-design literal-only parser, not the dangerous
    # built-in evaluator) handles single-quoted strings and embedded
    # escapes that json.loads would reject. It refuses to run anything
    # that isn't a literal data structure, so there's no code-exec
    # risk even with attacker-controlled exception messages.
    s = str(exc)
    marker = s.find("{'error':")
    if marker == -1:
        marker = s.find('{"error":')
    if marker != -1:
        candidate = s[marker:]
        try:
            from ast import literal_eval as _safe_literal
            data = _safe_literal(candidate)
            if isinstance(data, dict):
                raw = (data.get('error') or {}).get('failed_generation')
                if isinstance(raw, str) and raw:
                    return raw
        except Exception:
            pass
    return None


def _tolerant_json_parse(raw: str):
    """Try strict ``json.loads``; fall back to tolerant repair on parse error.

    Groq's tool-call validator sometimes serialises malformed JSON when
    the LLM produces a structure that doesn't match the tool schema —
    we observed a "Expecting ',' delimiter" at line 432 col 3 (char 15483)
    in a 16 kB blob even though the boundaries at start and end looked
    correct. The strict parser bails on the first bad character; this
    fallback tries two recovery strategies before giving up.

    Strategies attempted, in order:
      1. Strict ``json.loads`` (happy path; no overhead when it works).
      2. Trailing-comma strip — ``,(\\s*[}\\]])`` → ``\\1``. Catches the
         common ``[a, b, c,]`` shape.
      3. Brace-truncation — walk the string tracking brace depth outside
         strings; truncate to the last position where depth returned to 0.
         Recovers the well-formed prefix of a partially-malformed payload.

    Returns the parsed dict/list on success. Raises ``json.JSONDecodeError``
    with context if even tolerant parse fails — caller treats as
    unrecoverable.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(
            "resume_generator: strict JSON parse failed (%s) — attempting tolerant repair",
            e,
        )

    # Repair pass 1: strip trailing commas before } or ].
    repaired = re.sub(r',(\s*[}\]])', r'\1', raw)
    try:
        parsed = json.loads(repaired)
        logger.warning(
            "resume_generator: recovered via tolerant JSON parse (strategy: trailing-comma-strip)"
        )
        return parsed
    except json.JSONDecodeError:
        pass

    # Repair pass 2: brace-repair. Walk once and (a) auto-insert missing
    # close chars when a `]` is seen with `{` on top of stack (or vice
    # versa) — the LLM forgot a closer somewhere in the middle, and the
    # closer it DID emit was intended for a deeper opener — and (b)
    # append missing closers for anything still unclosed at end. Single
    # pass, handles both mismatch-mid-string and unclosed-at-end shapes.
    #
    # Observed Zeyad failure (2026-05-18): ``[{"name": "X",
    # "parameters": {...}]`` — the `]` was meant to close `[`, but the
    # outer `{` is unclosed. Repair: see `]` with `{` on top, auto-insert
    # `}` (closing the orphan `{`), then match `]` against `[`.
    last_valid = -1
    repaired2_chars: list[str] = []
    stack: list[str] = []  # 'O' for {, 'A' for [
    in_string = False
    escape_next = False
    for ch in repaired:
        if escape_next:
            repaired2_chars.append(ch)
            escape_next = False
            continue
        if ch == '\\' and in_string:
            repaired2_chars.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            repaired2_chars.append(ch)
            continue
        if in_string:
            repaired2_chars.append(ch)
            continue
        if ch == '{':
            stack.append('O')
            repaired2_chars.append(ch)
        elif ch == '[':
            stack.append('A')
            repaired2_chars.append(ch)
        elif ch == '}':
            # If `[` is on top, auto-close it first (LLM forgot the `]`).
            while stack and stack[-1] == 'A':
                stack.pop()
                repaired2_chars.append(']')
            if stack and stack[-1] == 'O':
                stack.pop()
                repaired2_chars.append(ch)
                if not stack:
                    last_valid = len(repaired2_chars) - 1
            # else: orphan } — drop it (no matching opener anywhere)
        elif ch == ']':
            # If `{` is on top, auto-close it first (LLM forgot the `}`).
            while stack and stack[-1] == 'O':
                stack.pop()
                repaired2_chars.append('}')
            if stack and stack[-1] == 'A':
                stack.pop()
                repaired2_chars.append(ch)
                if not stack:
                    last_valid = len(repaired2_chars) - 1
            # else: orphan ] — drop it
        else:
            repaired2_chars.append(ch)
    # Append closers for anything still open at end.
    appended_closers: list[str] = []
    while stack:
        op = stack.pop()
        closer = '}' if op == 'O' else ']'
        repaired2_chars.append(closer)
        appended_closers.append(closer)
    brace_repaired = ''.join(repaired2_chars)

    if brace_repaired != repaired:
        try:
            parsed = json.loads(brace_repaired)
            logger.warning(
                "resume_generator: recovered via tolerant JSON parse "
                "(strategy: brace-repair, appended=%r, len %d -> %d)",
                ''.join(appended_closers), len(raw), len(brace_repaired),
            )
            return parsed
        except json.JSONDecodeError:
            pass

    # Repair pass 3: truncate to last balanced brace outside strings.
    # Catches the inverse shape: trailing garbage AFTER a complete object,
    # e.g. ``{...complete...}{incomplete``. ``last_valid`` was set during
    # the repair walk above to the position where the brace stack
    # genuinely returned to 0 (no auto-insertion needed at that point).
    if last_valid > 0:
        truncated = brace_repaired[:last_valid + 1]
        try:
            parsed = json.loads(truncated)
            logger.warning(
                "resume_generator: recovered via tolerant JSON parse "
                "(strategy: brace-truncation at char %d, original len=%d)",
                last_valid + 1, len(raw),
            )
            return parsed
        except json.JSONDecodeError:
            pass

    # Unrecoverable — re-raise with context for the caller's log line.
    raise json.JSONDecodeError(
        f"Tolerant parse failed; original len={len(raw)}, last balanced "
        f"brace at char {last_valid}, appended_closers={appended_closers!r}",
        raw,
        last_valid if last_valid > 0 else 0,
    )


def _flatten_achievements_wrapper(parsed: dict) -> dict:
    """Flatten the LLM-invented ``achievements`` wrapper into ``description``.

    The Groq tool-call validator occasionally produces this shape:

        experience[*].achievements: [{description: [bullet, bullet, ...]}]

    Under PR 3a's schema, ``ResumeExperience`` accepts only ``description``
    as the canonical bullet field; ``extra="forbid"`` rejects the
    ``achievements`` key directly. The schema validator's
    ``_fold_into_description`` would also handle this wrapper at validation
    time, but doing it here in the recovery path keeps the flattener's
    output self-consistent with the canonical shape and surfaces a clear
    "recovered N bullets" log line for diagnostics.

    Idempotent — calling it on an already-flat dict is a no-op. The
    recovery path calls it twice (before and after schema-envelope
    unwrap) to catch the wrapper regardless of nesting depth.

    Operates in place AND returns the dict for chaining.
    """
    experiences = parsed.get('experience') or parsed.get('experiences')
    if not isinstance(experiences, list):
        return parsed

    flattened_count = 0
    total_bullets_recovered = 0

    for exp in experiences:
        if not isinstance(exp, dict):
            continue
        achievements = exp.pop('achievements', None)
        if achievements is None:
            continue

        bullets: list[str] = []
        # Three observed shapes:
        #   A) list of dicts each with description: list[str]
        #   B) list of dicts each with description: str
        #   C) list of strings directly
        if isinstance(achievements, list):
            for item in achievements:
                if isinstance(item, dict):
                    desc = item.get('description')
                    if isinstance(desc, list):
                        bullets.extend(
                            d for d in desc if isinstance(d, str) and d.strip()
                        )
                    elif isinstance(desc, str) and desc.strip():
                        bullets.append(desc)
                elif isinstance(item, str) and item.strip():
                    bullets.append(item)

        if not bullets:
            continue

        # Append into existing description, preferring existing-first.
        # Existing description may be: list[str] (post-PR-3a canonical),
        # str (legacy single paragraph), or missing.
        existing = exp.get('description')
        if isinstance(existing, list):
            exp['description'] = existing + bullets
        elif isinstance(existing, str) and existing.strip():
            exp['description'] = [existing] + bullets
        else:
            exp['description'] = bullets

        flattened_count += 1
        total_bullets_recovered += len(bullets)

    if flattened_count:
        logger.info(
            "resume_generator: recovered via achievements-wrapper flattening "
            "(%d experiences flattened, %d bullets recovered)",
            flattened_count, total_bullets_recovered,
        )

    return parsed


def _recover_resume_from_failed_generation(exc):
    """Recover a ResumeContentResult from Groq's tool_use_failed body.

    Groq emits tool calls in several shapes when its validator rejects:

      A) [{"name": "ResumeContentResult", "parameters": {<flat fields>}}]
      B) {<flat fields already in ResumeContentResult shape>}
      C) [{"name": "...", "parameters": {
             "additionalProperties": true,
             "properties": {
                 "<field>": {"type": "array", "value": [...]},
                 "<other_field>": "<direct value>",
                 ...
             }
         }}]

    Shape C is the model serializing the SCHEMA INSTEAD OF an instance —
    it wraps each list field in `{"type": "array", "value": <actual list>}`
    and nests everything under `properties`. We need to peel both layers
    and unwrap any type-envelope wrappers per field before handing the
    dict to Pydantic.

    The Pydantic schema's `before` validators handle the field-level
    coercions (null → "", `[{description: ...}]` → `["..."]`), so a
    correctly-unwrapped dict reaches the same final shape the happy
    path produces.
    """
    raw = _extract_failed_generation(exc)
    if not raw:
        logger.warning(
            "Resume recovery: could not locate failed_generation payload on "
            "%s exception (body_type=%s, has_response=%s) — falling back.",
            type(exc).__name__,
            type(getattr(exc, 'body', None)).__name__,
            hasattr(exc, 'response'),
        )
        return None
    try:
        parsed = _tolerant_json_parse(raw)
    except Exception as je:
        logger.warning(
            "Resume recovery: failed_generation JSON parse failed (%s) — "
            "first 200 chars: %r", je, raw[:200],
        )
        return None
    # Tool-call wrapper: list of {name, parameters} entries.
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict) and isinstance(first.get('parameters'), dict):
            parsed = first['parameters']
        elif isinstance(first, dict):
            parsed = first
    if not isinstance(parsed, dict):
        return None
    # PR 3f — Flatten invented `achievements` wrapper BEFORE schema-envelope
    # unwrap. Idempotent — calling again after the unwrap is harmless and
    # catches the wrapper at either nesting depth.
    parsed = _flatten_achievements_wrapper(parsed)
    # Schema-envelope unwrap: the model produced
    #   {"additionalProperties": true, "properties": {<field>: ...}}
    # instead of a flat instance. The `properties` payload IS the instance,
    # so step into it.
    if (
        isinstance(parsed.get('properties'), dict)
        and 'additionalProperties' in parsed
    ):
        logger.info(
            "Recovered resume: schema-envelope detected, stepping into "
            "`properties`."
        )
        parsed = parsed['properties']
    # PR 3f — Second flattener call in case the wrapper lived inside the
    # schema envelope rather than at the top level.
    parsed = _flatten_achievements_wrapper(parsed)
    # Per-field type-envelope unwrap: lists arrive as
    #   {"type": "array", "value": [...]}
    # instead of bare arrays. Same for "object"/"string" envelopes the
    # model sometimes emits. Strip the wrapper, keep the value.
    unwrapped: dict[str, Any] = {}
    for key, val in parsed.items():
        if (
            isinstance(val, dict)
            and 'value' in val
            and 'type' in val
            and set(val.keys()) <= {'type', 'value', 'description', 'items'}
        ):
            unwrapped[key] = val['value']
        else:
            unwrapped[key] = val
    parsed = unwrapped
    try:
        return ResumeContentResult(**parsed)
    except Exception:
        logger.exception("Recovered resume failed_generation didn't validate")
        return None


def regenerate_section(profile, job, gap_analysis, current_content: dict, section: str) -> dict | list | str:
    """Rewrite ONE section of the resume in-place using the same enriched
    context as full generation, but a focused prompt. Returns just the
    new value for that section so the caller can update content[section]
    and save.

    Allowed sections: 'professional_summary', 'skills', 'experience',
    'projects'. Other sections (education, certifications, languages)
    are sourced verbatim from the profile and aren't worth regenerating.

    Uses the same evidence-grounded enrichment rule as the full prompt
    so a regenerated bullet won't suddenly fabricate while neighbouring
    bullets stay grounded.
    """
    if section not in {'professional_summary', 'skills', 'experience', 'projects'}:
        raise ValueError(f"unsupported section {section!r}")

    raw_cv_data = profile.data_content or {}
    _SIGNAL_KEYS = {'github_signals', 'scholar_signals', 'kaggle_signals', 'linkedin_snapshot'}
    slim_cv = {k: v for k, v in raw_cv_data.items()
               if k != 'raw_text'
               and k not in _SIGNAL_KEYS
               and v
               and k not in ('normalized_summary', 'objective')}
    # Fix-1a (2026-06-01) — phantom-role guard, INPUT side. When the
    # regen target is experience or projects, the LLM must NOT see the
    # full master list for that section. Showing both `current_content`
    # AND the master is what let "Banque Misr" sneak through: the LLM
    # picked a role from the master that wasn't in the in-flight resume.
    # Strip the master block for the target section so the only roles
    # the LLM can rewrite are the ones already on the resume.
    if section == 'experience':
        slim_cv.pop('experiences', None)
    elif section == 'projects':
        slim_cv.pop('projects', None)
    jd_body = (job.description or '')[:4000]
    evidence_context = _build_evidence_context(profile, job, gap_analysis)

    # Single-section schemas — keeps the LLM focused and the response
    # tiny. Avoids the "regenerate one bullet but the LLM rewrites the
    # whole resume and drops sections" failure mode.
    from pydantic import BaseModel, Field
    from typing import List

    class SummaryOnly(BaseModel):
        professional_summary: str = Field(..., description="2-3 sentences, 40-60 words. Third person. No banned phrases.")

    class SkillsOnly(BaseModel):
        skills: List[str] = Field(..., description="8-15 hard/technical skills, JD-relevant ones first.")

    class ExperienceOnly(BaseModel):
        experience: List[ResumeExperience] = Field(..., description="All experience entries; bullets rewritten.")

    class ProjectsOnly(BaseModel):
        projects: List[ResumeProject] = Field(..., description="All project entries; bullets rewritten.")

    schema_for = {
        'professional_summary': SummaryOnly,
        'skills': SkillsOnly,
        'experience': ExperienceOnly,
        'projects': ProjectsOnly,
    }
    target_schema = schema_for[section]

    instruction_for = {
        'professional_summary': (
            "Rewrite ONLY the professional summary. 2-3 sentences, 40-60 words. "
            "NEUTRAL voice (no 'I' / 'my'). NEVER refer to the candidate by their "
            "first or last name (writing 'Zeyad has built...' in your own resume "
            "reads as ghost-written). NEVER invent or estimate years of experience: "
            "only state YoE if the CV's experience entries actually span 12+ months "
            "via real start_date / end_date values. If the candidate has only an "
            "internship or a sub-1-year role, lead with the role and one concrete "
            "proof point — do NOT use phrases like 'X+ years experience', "
            "'early-career', or 'less than N years experience'. Pull the proof "
            "point from the CV or one of the evidence blocks (GitHub, Scholar, "
            "Kaggle). No banned phrases, no inside-out openers."
        ),
        'skills': (
            "Rewrite ONLY the skills list. 8-15 items, hard/technical only, "
            "JD-required skills the candidate genuinely has FIRST, then the rest. "
            "Use exact skill names from the JD where possible."
        ),
        'experience': (
            "Rewrite ONLY the experience section's bullets. The set of "
            "roles is FIXED — exactly the entries in CURRENT RESUME "
            "CONTENT above. Do NOT add a role; do NOT remove a role; "
            "do NOT rename a role's company; do NOT invent a new company "
            "(an output role with a company not in CURRENT RESUME CONTENT "
            "is a SHIPPING DEFECT). Return the SAME entries in the SAME "
            "ORDER with bullets rewritten.\n"
            "3-5 bullets per role, each starting with a DIFFERENT strong "
            "action verb.\n"
            "EVERY bullet must follow the ACHIEVEMENT SHAPE below: lead "
            "with the verb, state what was done briefly, end on the "
            "concrete outcome.\n"
            "REPLACE every weak / duty opener you see in the current "
            "bullets — \"Contributed to …\", \"Applied … to …\", "
            "\"Worked on …\", \"Responsible for …\", \"Developed and "
            "evaluated …\", \"Demonstrating proficiency in …\" — with "
            "achievement-shaped rewrites.\n"
            "METRICS: only surface a number when it already exists in "
            "the source for THAT SAME role. NEVER invent; NEVER move a "
            "metric from another role to this one. If a role has no "
            "real metric, keep the bullets qualitative."
        ),
        'projects': (
            "Rewrite ONLY the projects section's bullets. The set of "
            "projects is FIXED — exactly the entries in CURRENT RESUME "
            "CONTENT above. Do NOT add a project; do NOT remove a "
            "project; do NOT rename a project's URL; do NOT invent a "
            "new one. Return the SAME entries in the SAME ORDER with "
            "bullets rewritten.\n"
            "2-3 bullets per project, each starting with a DIFFERENT "
            "strong action verb.\n"
            "EVERY bullet must follow the ACHIEVEMENT SHAPE below.\n"
            "REPLACE every weak / duty opener.\n"
            "METRICS: only surface a number when it already exists in "
            "the source for THAT SAME project. NEVER invent. NEVER "
            "move a metric from one project to another — a silhouette "
            "score on project A must NOT appear on project B."
        ),
    }

    prompt = f"""You are an EXPERT resume strategist. Regenerate ONE section of a resume the user is actively editing — keep all other content unchanged.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description:
{jd_body}

CURRENT RESUME CONTENT (the single source of truth for the target section's role/project IDENTITIES — titles, companies, names, URLs come from HERE, never from CV DATA):
{json.dumps({k: v for k, v in (current_content or {}).items() if k in ('professional_title', 'professional_summary', 'skills', 'experience', 'education', 'projects')}, indent=2, default=str)}

CV DATA (the candidate's master profile — supplemental context only; for the target section, the role/project SET is fixed by CURRENT RESUME CONTENT above):
{json.dumps(slim_cv, indent=2, default=str)}

{evidence_context}

=== TARGET SECTION ===
{instruction_for[section]}

=== BULLET QUALITY + METRIC SAFETY (applies to experience and projects sections) ===
{BULLET_QUALITY_AND_SAFETY_RULES}

=== EVIDENCE-GROUNDED ENRICHMENT RULE ===
Every concrete claim must trace to a source you've been given (CV, GitHub, Scholar, Kaggle, gap analysis). Never claim a skill the gap analysis lists as MISSING. When no source supports a number, keep it qualitative.

{HUMAN_VOICE_RULE}"""

    structured_llm = get_structured_llm(target_schema, temperature=0.6, max_tokens=2048, task="resume_gen")
    result = structured_llm.invoke(prompt)
    out = result.model_dump()
    return out[section]


def _build_offline_fallback(profile, job, raw_cv_data: dict) -> dict:
    """Compose a usable resume directly from profile data when the LLM call
    fails (rate limit, validation error, timeout). Pulls verbatim from the
    source CV — no fabrication, no banned AI-tell openers, no stub text
    like "Experienced professional seeking X position at Y" (which the
    HUMAN_VOICE_RULE explicitly bans).

    The output is deterministic given (profile, job) and produces a resume
    that a real recruiter could read without flagging it as obvious LLM
    fallback content. Applies basic JD-relevance filtering on skills so
    the recruiter doesn't see Java/Python/C++ on a junior frontend role.
    """
    # Build a JD-keyword set for relevance filtering. We keep skills that
    # match one of these (substring or word-level) and demote the rest
    # to the bottom of the list. We never DROP skills — keeping the source
    # CV's full skill set preserves grounding — we just reorder.
    job_skills_lower = {s.lower().strip() for s in (getattr(job, 'extracted_skills', None) or [])}
    job_words = set()
    for s in job_skills_lower:
        for w in re.findall(r"[a-z]+", s):
            if len(w) > 2:
                job_words.add(w)

    def _is_relevant_to_jd(name: str) -> bool:
        n = name.lower().strip()
        if not n:
            return False
        if n in job_skills_lower:
            return True
        # word-level overlap (e.g., "react" in "React Router")
        return any(w in n for w in job_words)

    # Title — most-recent experience > job title
    title = ''
    for exp in (raw_cv_data.get('experiences') or []):
        if isinstance(exp, dict):
            t = (exp.get('title') or '').strip()
            if t:
                title = t
                break
    if not title:
        title = getattr(job, 'title', '') or ''

    # Skills — split into JD-relevant first, then the rest. Preserves grounding
    # (no skills dropped) while making the top of the list look tailored.
    relevant_skills: list[str] = []
    other_skills: list[str] = []
    for s in (raw_cv_data.get('skills') or []):
        if isinstance(s, dict):
            name = (s.get('name') or '').strip()
        else:
            name = str(s).strip()
        if not name:
            continue
        if _is_relevant_to_jd(name):
            relevant_skills.append(name)
        else:
            other_skills.append(name)
    skills = relevant_skills + other_skills

    # Summary — never start with "Experienced professional seeking..."
    # Compose from normalized_summary if present, else from role + the most
    # JD-relevant skill (so even the fallback summary looks tailored).
    summary = (raw_cv_data.get('normalized_summary') or
               raw_cv_data.get('summary') or '').strip()
    if not summary:
        top_skills = relevant_skills[:2] if relevant_skills else (skills[:2] or [])
        bits = []
        if title:
            bits.append(title)
        if top_skills:
            bits.append(f"working with {' and '.join(top_skills)}")
        # Plain English, no banned phrases. If we have neither, leave empty.
        summary = (', '.join(bits) + '.') if bits else ''

    # Experience — verbatim from profile. PR 3b: description is the
    # single canonical bullets bucket on the profile-side Experience
    # schema; highlights/achievements are folded in at validation time
    # and the migration brought legacy rows into the same shape.
    from resumes.services.resume_normalizer import assemble_duration_honest
    experience = []
    for exp in (raw_cv_data.get('experiences') or []):
        if not isinstance(exp, dict):
            continue
        start = (exp.get('start_date') or '').strip()
        end = (exp.get('end_date') or '').strip()
        is_current = exp.get('is_current') is True
        duration = assemble_duration_honest(start, end, is_current)
        bullets = exp.get('description') or []
        if isinstance(bullets, str):
            bullets = [line.strip() for line in bullets.split('\n') if line.strip()]
        experience.append({
            'title': (exp.get('title') or '').strip(),
            'company': (exp.get('company') or '').strip(),
            'duration': duration,
            'start_date': start,
            'end_date': end,
            'is_current': is_current,
            'location': (exp.get('location') or '').strip(),
            'industry': (exp.get('industry') or '').strip(),
            'description': bullets,
        })

    # Education — pass through every field; the editor / template decides
    # which to render. `degree` stays separate from `field` so the renderer
    # can join them in its own house style.
    education = []
    for edu in (raw_cv_data.get('education') or []):
        if not isinstance(edu, dict):
            continue
        honors = edu.get('honors') or []
        if isinstance(honors, str):
            honors = [line.strip() for line in honors.split('\n') if line.strip()]
        education.append({
            'degree': (edu.get('degree') or '').strip(),
            'field': (edu.get('field') or '').strip(),
            'institution': (edu.get('institution') or '').strip(),
            'year': (edu.get('graduation_year') or edu.get('year') or '').strip(),
            'gpa': (edu.get('gpa') or '').strip(),
            'location': (edu.get('location') or '').strip(),
            'honors': honors,
        })

    # Projects — verbatim. PR 3a+3b: description is the single
    # canonical bullets field across both resume-output AND profile-
    # input schemas. Legacy highlights data was folded in by the PR 3b
    # migration and is folded at validation time for fresh data.
    projects = []
    for proj in (raw_cv_data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        bullets = proj.get('description') or []
        if isinstance(bullets, str):
            bullets = [line.strip() for line in bullets.split('\n') if line.strip()]
        techs = proj.get('technologies') or []
        if isinstance(techs, str):
            techs = [t.strip() for t in techs.split(',') if t.strip()]
        projects.append({
            'name': (proj.get('name') or '').strip(),
            'description': bullets,
            'url': (proj.get('url') or '').strip(),
            'technologies': techs,
        })

    # Certifications — verbatim, including duration.
    certifications = []
    for cert in (raw_cv_data.get('certifications') or []):
        if not isinstance(cert, dict):
            continue
        certifications.append({
            'name': (cert.get('name') or '').strip(),
            'issuer': (cert.get('issuer') or '').strip(),
            'date': (cert.get('date') or '').strip(),
            'duration': (cert.get('duration') or '').strip(),
            'url': (cert.get('url') or '').strip(),
        })

    # Languages — accept either list of str or list of dicts
    languages = []
    for lang in (raw_cv_data.get('languages') or []):
        if isinstance(lang, str):
            n = lang.strip()
        elif isinstance(lang, dict):
            n = (lang.get('name') or '').strip()
        else:
            n = ''
        if n:
            languages.append(n)

    logger.warning(
        "Resume gen: OFFLINE FALLBACK used for job '%s' — the LLM call failed "
        "(rate-limit / 413 / timeout) and the user is getting deterministic "
        "profile-derived content, NOT a tailored resume. The supervised loop "
        "will skip this draft (no point reviewing a non-LLM placeholder). "
        "Investigate the upstream Groq error in the previous log lines.",
        getattr(job, 'title', '?'),
    )
    return {
        # Marker so generate_resume_content_supervised can detect this is
        # not a real LLM-tailored result and skip the supervisor review.
        # Stored on resume_content (which becomes GeneratedResume.content)
        # so downstream consumers can also surface a degraded-mode banner.
        '_is_fallback': True,
        'professional_title': title,
        'professional_summary': summary,
        'objective': (raw_cv_data.get('objective') or '').strip(),
        'skills': skills,
        'experience': experience,
        'education': education,
        'projects': projects,
        'certifications': certifications,
        'languages': languages,
    }


def _ensure_profile_data_preserved(resume_content: dict, profile_data: dict) -> dict:
    """
    Map profile fields to resume schema as a guaranteed fallback.

    The LLM is supposed to restructure profile data into ResumeContentResult,
    but it sometimes returns empty sections or keeps profile field names
    (e.g. `graduation_year` instead of `year`, `highlights` instead of
    `description`). This function fills the gaps so the edit page always
    renders populated fields.
    """
    if not profile_data:
        return resume_content

    # --- Experience ---
    if not resume_content.get('experience') and profile_data.get('experiences'):
        from resumes.services.resume_normalizer import assemble_duration_honest
        resume_content['experience'] = []
        for exp in profile_data['experiences']:
            start = exp.get('start_date') or ''
            end = exp.get('end_date') or ''
            is_current = exp.get('is_current') is True
            duration = assemble_duration_honest(start, end, is_current)
            # PR 3b: description canonical on profile-side too.
            description = exp.get('description') or []
            if isinstance(description, str):
                description = [line.strip() for line in description.split('\n') if line.strip()]
            resume_content['experience'].append({
                'title': exp.get('title', ''),
                'company': exp.get('company', ''),
                'duration': duration,
                'start_date': start,
                'end_date': end,
                'is_current': is_current,
                'location': exp.get('location') or '',
                'industry': exp.get('industry') or '',
                'description': description,
            })
    elif resume_content.get('experience') and profile_data.get('experiences'):
        # LLM returned experience but may have dropped supplemental fields the
        # master profile carries (location/industry/start_date/end_date). Patch
        # them in by positional index when blank, so the editor doesn't lose
        # data the user typed on /profiles/setup/review/.
        for i, exp in enumerate(resume_content['experience']):
            if i >= len(profile_data['experiences']):
                break
            src = profile_data['experiences'][i]
            for key in ('location', 'industry', 'start_date', 'end_date'):
                if not exp.get(key) and src.get(key):
                    exp[key] = src.get(key)

    # --- Education ---
    if profile_data.get('education'):
        existing_edu = resume_content.get('education') or []
        # Patch missing fields from master by positional index. degree/field
        # stay separate so the renderer (PDF/DOCX) joins them in its own style.
        for i, edu in enumerate(existing_edu):
            if i >= len(profile_data['education']):
                break
            src = profile_data['education'][i]
            if not edu.get('year'):
                edu['year'] = src.get('graduation_year') or src.get('year') or ''
            for key in ('field', 'gpa', 'location'):
                if not edu.get(key) and src.get(key):
                    edu[key] = src.get(key)
            if not edu.get('honors') and src.get('honors'):
                h = src['honors']
                if isinstance(h, str):
                    h = [line.strip() for line in h.split('\n') if line.strip()]
                edu['honors'] = h
        # If LLM returned nothing, rebuild from profile
        if not existing_edu:
            existing_edu = []
            for edu in profile_data['education']:
                honors = edu.get('honors') or []
                if isinstance(honors, str):
                    honors = [line.strip() for line in honors.split('\n') if line.strip()]
                existing_edu.append({
                    'degree': edu.get('degree', ''),
                    'field': edu.get('field', ''),
                    'institution': edu.get('institution', ''),
                    'year': edu.get('graduation_year') or edu.get('year') or '',
                    'gpa': edu.get('gpa') or '',
                    'location': edu.get('location') or '',
                    'honors': honors,
                })
        resume_content['education'] = existing_edu

    # --- Projects ---
    # PR 3a: description is the single canonical bullets field. Master-side
    # `highlights` is folded into description here for the no-LLM fallback;
    # the LLM-output path is governed by ResumeProject's coerce_to_canonical
    # validator.
    if not resume_content.get('projects') and profile_data.get('projects'):
        resume_content['projects'] = []
        for proj in profile_data['projects']:
            # PR 3b: description canonical on profile-side.
            description = proj.get('description') or []
            if isinstance(description, str):
                description = [line.strip() for line in description.split('\n') if line.strip()]
            techs = proj.get('technologies') or []
            if isinstance(techs, str):
                techs = [t.strip() for t in techs.split(',') if t.strip()]
            resume_content['projects'].append({
                'name': proj.get('name', ''),
                'description': description,
                'url': proj.get('url') or '',
                'technologies': techs,
            })
    elif resume_content.get('projects') and profile_data.get('projects'):
        for i, proj in enumerate(resume_content['projects']):
            if i >= len(profile_data['projects']):
                break
            src = profile_data['projects'][i]
            if not proj.get('technologies') and src.get('technologies'):
                t = src['technologies']
                if isinstance(t, str):
                    t = [x.strip() for x in t.split(',') if x.strip()]
                proj['technologies'] = t
            # PR 3b: if the LLM returned no bullets, fall back to the
            # master-profile's description. Pre-PR-3b this also
            # checked highlights; now description is canonical on
            # both layers.
            if not proj.get('description') and src.get('description'):
                d = src['description']
                if isinstance(d, str):
                    d = [line.strip() for line in d.split('\n') if line.strip()]
                proj['description'] = d

    # --- Certifications ---
    if not resume_content.get('certifications') and profile_data.get('certifications'):
        resume_content['certifications'] = []
        for cert in profile_data['certifications']:
            resume_content['certifications'].append({
                'name': cert.get('name', ''),
                'issuer': cert.get('issuer') or '',
                'date': cert.get('date') or '',
                'duration': cert.get('duration') or '',
                'url': cert.get('url') or '',
            })
    elif resume_content.get('certifications') and profile_data.get('certifications'):
        for i, cert in enumerate(resume_content['certifications']):
            if i >= len(profile_data['certifications']):
                break
            src = profile_data['certifications'][i]
            if not cert.get('duration') and src.get('duration'):
                cert['duration'] = src['duration']

    # --- Objective (passthrough; LLM strips by ATS rules, sync_from_master restores) ---
    if not resume_content.get('objective') and profile_data.get('objective'):
        resume_content['objective'] = (profile_data.get('objective') or '').strip()

    # --- Languages (spoken only) ---
    if not resume_content.get('languages') and profile_data.get('languages'):
        langs = profile_data['languages']
        if isinstance(langs, list):
            resume_content['languages'] = [l if isinstance(l, str) else l.get('name', '') for l in langs]

    # --- Awards / Honors (ICPC, scholarships, hackathon placements) ---
    if not resume_content.get('awards'):
        src_awards = (
            profile_data.get('awards')
            or profile_data.get('honors')
            or profile_data.get('achievements')
            or []
        )
        if isinstance(src_awards, list) and src_awards:
            normalised: list[str] = []
            for a in src_awards:
                if isinstance(a, str) and a.strip():
                    normalised.append(a.strip())
                elif isinstance(a, dict):
                    # Awards can come in as {name, issuer, date, description}
                    bits = [
                        a.get('name') or a.get('title') or '',
                        a.get('issuer') or '',
                        a.get('date') or '',
                    ]
                    label = ' — '.join(b for b in bits if b)
                    if label:
                        normalised.append(label)
            if normalised:
                resume_content['awards'] = normalised

    # --- Skills ---
    if not resume_content.get('skills') and profile_data.get('skills'):
        resume_content['skills'] = [
            s.get('name', '') if isinstance(s, dict) else str(s)
            for s in profile_data['skills']
        ]

    return resume_content


def calculate_ats_score(resume_content, job_skills, tiers=None):
    """Backwards-compat shim — delegates to resumes.services.scoring.

    The new implementation penalizes keyword stuffing (>4 occurrences), rewards
    keywords that appear in experience descriptions (not just the skills list),
    tier-weights the score when ``tiers`` is supplied, and exposes a structured
    breakdown via compute_ats_breakdown(). Callers that just need the float
    (tasks.py, views.py) keep calling this; UI code that wants transparency
    should import compute_ats_breakdown() directly.
    """
    from .scoring import calculate_ats_score as _calc
    return _calc(resume_content, job_skills, tiers)
