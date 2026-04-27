import json
import logging
import re
from typing import Dict, Any, Optional
from profiles.services.llm_engine import get_structured_llm, get_llm
from profiles.services.schemas import ResumeContentResult, ResumeExperience, ResumeProject
from profiles.services.prompt_guards import HUMAN_VOICE_RULE

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


def generate_resume_content(profile, job, gap_analysis):
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

    filtered_cv = raw_cv_data

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
    logger.info(
        "Resume generation: domain='%s' for job '%s'; evidence_block_len=%d",
        domain, job.title, len(evidence_context),
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
- CV `projects[].description` or `highlights` → output `projects[].description` array (rewrite as bullets)
- CV `projects[].url` → output `projects[].url` (PRESERVE all project URLs exactly)
- CV `projects[].technologies` → output `projects[].technologies` array (PRESERVE; ATS scanner picks these up as keywords)
- CV `projects[].highlights` → output `projects[].highlights` array (PRESERVE; structured outcomes that complement the bullet description)
- CV `objective` → output `objective` (the standalone objective field; this is OPTIONAL — only include if the candidate's CV explicitly has one and it's not redundant with professional_summary)
- Include ALL certifications from the CV data — do NOT truncate or omit any.

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

Make it PROFESSIONAL and ATS-OPTIMIZED.

{HUMAN_VOICE_RULE}"""

    try:
        structured_llm = get_structured_llm(ResumeContentResult, temperature=0.7, max_tokens=8192, task="resume_gen")
        result = structured_llm.invoke(prompt)

        resume_content = result.model_dump()
        # Guarantee data integrity — fill in anything the LLM left empty or
        # mis-mapped from the profile. The LLM is good at rewriting but often
        # drops sections or uses wrong field names (e.g. graduation_year vs year).
        resume_content = _ensure_profile_data_preserved(resume_content, raw_cv_data)
        logger.info(f"✓ Generated tailored resume with sections: {list(resume_content.keys())}")
        return resume_content

    except Exception as e:
        logger.exception(f"Resume generation error: {e}")
        return _build_offline_fallback(profile, job, raw_cv_data)


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

    # Projects — verbatim, preserving technologies and structured highlights.
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
        # `highlights` on master may double as the bullet source (above); only
        # pass it through here when it's structurally distinct from description.
        master_highlights = proj.get('highlights') or []
        if isinstance(master_highlights, str):
            master_highlights = [line.strip() for line in master_highlights.split('\n') if line.strip()]
        if master_highlights == bullets:
            master_highlights = []
        projects.append({
            'name': (proj.get('name') or '').strip(),
            'description': bullets,
            'url': (proj.get('url') or '').strip(),
            'technologies': techs,
            'highlights': master_highlights,
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
    if not resume_content.get('projects') and profile_data.get('projects'):
        resume_content['projects'] = []
        for proj in profile_data['projects']:
            description = proj.get('description') or proj.get('highlights') or []
            if isinstance(description, str):
                description = [line.strip() for line in description.split('\n') if line.strip()]
            techs = proj.get('technologies') or []
            if isinstance(techs, str):
                techs = [t.strip() for t in techs.split(',') if t.strip()]
            master_highlights = proj.get('highlights') or []
            if isinstance(master_highlights, str):
                master_highlights = [line.strip() for line in master_highlights.split('\n') if line.strip()]
            if master_highlights == description:
                master_highlights = []
            resume_content['projects'].append({
                'name': proj.get('name', ''),
                'description': description,
                'url': proj.get('url') or '',
                'technologies': techs,
                'highlights': master_highlights,
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
            # Only patch highlights if master has them AND they differ from description.
            if not proj.get('highlights') and src.get('highlights'):
                h = src['highlights']
                if isinstance(h, str):
                    h = [line.strip() for line in h.split('\n') if line.strip()]
                desc = proj.get('description') or []
                if h != desc:
                    proj['highlights'] = h

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
