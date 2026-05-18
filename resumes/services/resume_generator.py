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


def generate_resume_content(profile, job, gap_analysis, *, metadata: dict | None = None):
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
    standards_section, classification_obj, retrieval_metadata = _build_standards_section(profile, job)

    # v2 grounding: pull per-skill candidate-evidence chunks + build the
    # inclusion plan. Both are no-ops + return empty blocks if anything
    # downstream blows up — the v1 path still runs.
    v2_block, inclusion_plan = _build_v2_grounding(profile, job, gap_analysis)

    logger.info(
        "Resume generation: domain='%s' for job '%s'; evidence_block_len=%d "
        "standards_block_len=%d v2_block_len=%d",
        domain, job.title, len(evidence_context), len(standards_section),
        len(v2_block),
    )

    prompt = f"""You are an EXPERT resume optimization strategist. Create a PROFESSIONAL, ATS-optimized resume tailored for this specific job using EVERY source provided.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description:
{jd_body}

COMPLETE CV DATA (the candidate's authoritative resume):
{json.dumps(slim_cv, indent=2)}

{evidence_context}

{v2_block}

=== FIELD MAPPING (CRITICAL — the CV data uses different field names than the output schema) ===
- CV `experiences[].highlights` array → output `experience[].description` array (rewrite each bullet)
- CV `experiences[].start_date` / `end_date` → output `experience[].duration` (combine as "Aug 2025 - Present"). Also pass through start_date and end_date verbatim into the output `experience[].start_date` / `experience[].end_date`.
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
- Structure: [Strong action verb] + [What you did] + [Measurable outcome or tool used].
- QUANTIFY whenever the source CV has any number (%, $, users, hours, records, teams, etc). Do NOT invent numbers. If the source bullet is "Built a data pipeline" keep it qualitative — do not fabricate "40% faster".
- Start every bullet with a DIFFERENT action verb. Never repeat the same verb in the same role.
- Preferred action verbs by intent: Built / Designed / Implemented / Shipped / Launched (creation); Reduced / Improved / Accelerated / Cut (optimization); Led / Owned / Coordinated / Mentored (leadership); Analyzed / Investigated / Diagnosed (analysis).

=== LENGTH & DENSITY ===
- Professional summary: 2-3 sentences, 40-60 words max. No fluff.
- Skills list: 8-15 items. Prioritize job-required skills that the candidate actually has (matched_skills first, then supporting technical skills).
- Total resume should fit one page for candidates with <5 years experience, maximum two pages otherwise.

=== REWRITE & STRUCTURING ===
1. PROFESSIONAL SUMMARY:
   - REQUIRED — never leave this field empty. The summary is the recruiter's 5-second hook; an empty summary signals "this candidate didn't bother". If the voice constraints below feel restrictive, write a SHORT, FACTUAL summary (one sentence is fine) rather than nothing.
   - Use NEUTRAL, DIRECT voice. No "I" / "my" pronouns. NO third-person references to the candidate by name (NEVER write "Zeyad has built..." or "Sara is a...") — referring to oneself by name in one's own resume reads as ghost-written and unprofessional.
   - Lead with the role and what the candidate does, not how long they've done it.
   - YoE / TENURE CLAIMS: Never invent or estimate years of experience. Only state YoE when the source CV's experience entries support it via real start_date / end_date dates that span at least 12 months total. If the candidate's only experience is an internship or a recent role under a year, do NOT use phrases like "X+ years experience", "early-career", "less than N years experience", or any framing that implies a duration. Just describe what they do and one concrete proof point. Example for a fresh-out-of-school candidate with one short role: "AI & Tooling Engineer focused on data pipelines in Microsoft Fabric, with hands-on PySpark and Python work across automation and ERP integration." NOT "early-career engineer with less than 2 years of experience".
   - 2-3 sentences, 40-60 words max. No fluff.
   - Reflect ONLY experience already present in the resume + corroborated signals.
2. SKILLS SECTION: Remove ALL soft skills. Keep ONLY hard/technical skills explicitly listed.
3. EXPERIENCE BULLETS: Start each bullet with a strong action verb. Use STAR structure where possible (Situation/Task → Action → Result).
4. MOST RECENT EXPERIENCE FIRST: Within each section, order entries newest first.

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
        resume_content = _apply_bullet_validator(resume_content)
        resume_content = normalize_resume(
            resume_content, plan=inclusion_plan, job=job, profile_data=sanitized_cv,
        )
        resume_content = _apply_v2_grounding_check(
            resume_content, inclusion_plan, profile, job, gap_analysis,
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
        if _is_token_limit_error(e) and (v2_block or standards_section):
            slim_prompt = prompt
            if v2_block:
                slim_prompt = slim_prompt.replace(v2_block, '')
            if standards_section:
                slim_prompt = slim_prompt.replace(standards_section, '')
            logger.warning(
                "Resume gen: token-limit hit (full=%d chars). Retrying with "
                "v2_block + standards trimmed (slim=%d chars, saved=%d).",
                len(prompt), len(slim_prompt), len(prompt) - len(slim_prompt),
            )
            try:
                slim_llm = get_structured_llm(
                    ResumeContentResult, temperature=0.7,
                    max_tokens=8192, task="resume_gen",
                )
                slim_result = slim_llm.invoke(slim_prompt)
                resume_content = _post_process(slim_result.model_dump())
                logger.info(
                    "✓ Resume gen recovered via slim-prompt retry; sections=%s",
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
            resume_content = recovered.model_dump()
            resume_content = _ensure_profile_data_preserved(resume_content, sanitized_cv)
            resume_content = _apply_bullet_validator(resume_content)
            # Same Pass-B safety net on the recovery path so a tool_use_failed
            # round-trip doesn't bypass normalization.
            resume_content = normalize_resume(resume_content, plan=inclusion_plan, job=job, profile_data=sanitized_cv)
            logger.info(
                "Resume recovered from failed_generation; sections=%s",
                list(resume_content.keys()),
            )
            return resume_content
        logger.exception(f"Resume generation error: {e}")
        # Offline fallback uses the sanitized CV so a hard failure renders
        # cleaned data, not the parser artefacts.
        return _build_offline_fallback(profile, job, sanitized_cv)


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
            "Rewrite ONLY the experience section's bullets. Keep titles, "
            "companies, durations exactly as in the CV. 3-5 bullets per role, "
            "each starting with a different action verb. Apply the evidence-"
            "grounded enrichment rule: quantify only when a source supports it."
        ),
        'projects': (
            "Rewrite ONLY the projects section's bullets. Keep names and URLs "
            "exactly as in the CV. 2-3 bullets per project. Apply the evidence-"
            "grounded enrichment rule: quantify only when a source supports it."
        ),
    }

    prompt = f"""You are an EXPERT resume strategist. Regenerate ONE section of a resume the user is actively editing — keep all other content unchanged.

JOB DETAILS:
- Title: {job.title}
- Company: {job.company}
- Required Skills: {', '.join(job.extracted_skills or [])}
- Job Description:
{jd_body}

CURRENT RESUME CONTENT (for reference; do NOT modify any section other than the target):
{json.dumps({k: v for k, v in (current_content or {}).items() if k in ('professional_title', 'professional_summary', 'skills', 'experience', 'education', 'projects')}, indent=2, default=str)}

CV DATA (authoritative source):
{json.dumps(slim_cv, indent=2, default=str)}

{evidence_context}

=== TARGET SECTION ===
{instruction_for[section]}

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

    # Experience — verbatim from profile, with bullets sourced from
    # highlights/achievements/description (in that order). Pass through the
    # full set of master-profile fields so the editor can surface them.
    experience = []
    for exp in (raw_cv_data.get('experiences') or []):
        if not isinstance(exp, dict):
            continue
        start = (exp.get('start_date') or '').strip()
        end = (exp.get('end_date') or '').strip()
        duration = f"{start} - {end}".strip(' -') if (start or end) else ''
        bullets = exp.get('highlights') or exp.get('achievements') or exp.get('description') or []
        if isinstance(bullets, str):
            bullets = [line.strip() for line in bullets.split('\n') if line.strip()]
        experience.append({
            'title': (exp.get('title') or '').strip(),
            'company': (exp.get('company') or '').strip(),
            'duration': duration,
            'start_date': start,
            'end_date': end,
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

    # Projects — verbatim. PR 3a: description is the single canonical
    # bullets field; highlights on master is folded in here so the LLM
    # sees a unified list and the resume-output schema's extra="forbid"
    # doesn't reject the dict downstream.
    projects = []
    for proj in (raw_cv_data.get('projects') or []):
        if not isinstance(proj, dict):
            continue
        bullets = proj.get('description') or proj.get('highlights') or []
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

    logger.info("Resume gen: offline fallback used for job '%s' (LLM unavailable)", getattr(job, 'title', '?'))
    return {
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
        resume_content['experience'] = []
        for exp in profile_data['experiences']:
            start = exp.get('start_date') or ''
            end = exp.get('end_date') or ''
            duration = f"{start} - {end}".strip(' -') if (start or end) else ''
            description = exp.get('highlights') or exp.get('achievements') or exp.get('description') or []
            if isinstance(description, str):
                description = [line.strip() for line in description.split('\n') if line.strip()]
            resume_content['experience'].append({
                'title': exp.get('title', ''),
                'company': exp.get('company', ''),
                'duration': duration,
                'start_date': start,
                'end_date': end,
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
            description = proj.get('description') or proj.get('highlights') or []
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
            # PR 3a: if the LLM returned no bullets, fall back to the
            # master-profile's bullets (description OR highlights).
            # Highlights on master is legitimate input (CV parser still
            # emits both fields); we fold here, not on the output.
            if not proj.get('description') and src.get('highlights'):
                h = src['highlights']
                if isinstance(h, str):
                    h = [line.strip() for line in h.split('\n') if line.strip()]
                proj['description'] = h

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


def calculate_ats_score(resume_content, job_skills):
    """Backwards-compat shim — delegates to resumes.services.scoring.

    The new implementation penalizes keyword stuffing (>4 occurrences), rewards
    keywords that appear in experience descriptions (not just the skills list),
    and exposes a structured breakdown via compute_ats_breakdown(). Callers
    that just need the float (tasks.py, views.py) keep calling this; UI code
    that wants transparency should import compute_ats_breakdown() directly.
    """
    from .scoring import calculate_ats_score as _calc
    return _calc(resume_content, job_skills)
