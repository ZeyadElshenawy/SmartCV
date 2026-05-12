"""Score scraped JobListing rows against a user's profile and seed RecommendedJob.

Pipeline:
1. Pre-filter with rapidfuzz (cheap) — drop bottom 60% by title-vs-skills overlap.
2. Run the existing analysis.services.gap_analyzer.compute_gap_analysis
   against each survivor — that's the same primitive the dashboard uses to
   score user-saved jobs.
3. Persist the top K as RecommendedJob rows (status=new), keyed on
   (user, normalized_url). When a row already exists with status saved or
   dismissed, we *don't* clobber the user's choice — we just refresh the
   match_score and metadata.

Returns the count of rows created/updated.
"""

import logging
from dataclasses import dataclass
from typing import List

from django.db import transaction

from analysis.services.gap_analyzer import compute_gap_analysis
from jobs.models import JobListing, RecommendedJob
from jobs.services.skill_extractor import extract_skills
from jobs.services.url_normalizer import normalize_url

logger = logging.getLogger(__name__)


# How many listings survive the cheap pre-filter and get the (expensive) LLM call.
PREFILTER_TOP_N = 20

# rapidfuzz partial_ratio threshold (0–100). Anything below is dropped.
PREFILTER_MIN_SCORE = 40.0

DEFAULT_TOP_K = 10


@dataclass
class _CandidateJob:
    """Adapter that quacks like jobs.models.Job for compute_gap_analysis.

    gap_analyzer reads `.title`, `.company`, `.extracted_skills`. Building
    a plain dataclass keeps this scoring pass purely in-memory until we
    decide which listings deserve a RecommendedJob row.
    """
    title: str
    company: str
    description: str
    extracted_skills: List[str]


def _profile_signal_text(profile) -> str:
    """Cheap haystack for the pre-filter: skills + recent role titles + summary."""
    data = profile.data_content or {}
    bits: List[str] = []

    skills = data.get("skills") or []
    for s in skills:
        if isinstance(s, dict):
            name = s.get("name") or s.get("skill") or ""
        else:
            name = str(s or "")
        if name:
            bits.append(name)

    for exp in (data.get("experiences") or [])[:5]:
        if isinstance(exp, dict):
            title = exp.get("title") or exp.get("role") or ""
            if title:
                bits.append(title)

    summary = data.get("normalized_summary") or data.get("summary") or ""
    if summary:
        bits.append(summary)

    return " · ".join(bits).lower()


def _prefilter(profile, listings: List[JobListing], top_n: int = PREFILTER_TOP_N) -> List[JobListing]:
    """Rank by rapidfuzz partial_ratio of (title + raw_text bigrams) vs profile signals.
    Keep the top N (or all surviving the floor, whichever is smaller)."""
    try:
        from rapidfuzz import fuzz
    except Exception:
        # If rapidfuzz isn't available we'd rather score everything than drop blindly.
        logger.warning("rapidfuzz not available — skipping pre-filter")
        return listings[:top_n]

    haystack = _profile_signal_text(profile)
    if not haystack:
        return listings[:top_n]

    scored = []
    for li in listings:
        candidate_text = f"{li.title} {li.raw_text or ''}".lower()[:2000]
        score = fuzz.partial_ratio(candidate_text, haystack)
        if score >= PREFILTER_MIN_SCORE:
            scored.append((score, li))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [li for _score, li in scored[:top_n]]


def _candidate_from_listing(li: JobListing) -> _CandidateJob:
    description = li.description or li.raw_text or ""
    try:
        skills = extract_skills(description) or []
    except Exception:
        logger.exception("Skill extraction failed for listing %s", li.pk)
        skills = []
    return _CandidateJob(
        title=li.title or "",
        company=li.company or "",
        description=description,
        extracted_skills=list(skills),
    )


def _upsert_recommendation(user_id, listing: JobListing, score: float) -> bool:
    """Upsert RecommendedJob, preserving any user-set status (saved/dismissed)."""
    url = normalize_url(listing.url) or listing.url
    existing = RecommendedJob.objects.filter(user_id=user_id, url=url).first()
    match_score = max(0, min(100, int(round(score * 100))))
    description = listing.description or listing.raw_text or ""

    if existing is None:
        RecommendedJob.objects.create(
            user_id=user_id,
            url=url,
            title=(listing.title or "")[:200],
            company=(listing.company or "")[:200],
            description=description,
            match_score=match_score,
            status="new",
        )
        return True

    # User already acted on this URL — don't reset their decision, just
    # refresh metadata so future re-scans show fresher copy/score.
    existing.title = (listing.title or "")[:200] or existing.title
    existing.company = (listing.company or "")[:200] or existing.company
    existing.description = description or existing.description
    existing.match_score = match_score
    if existing.status not in {"saved", "dismissed"}:
        existing.status = "new"
    existing.save(update_fields=["title", "company", "description", "match_score", "status"])
    return False


def score_listings_for_user(user_id, scrape_job_id, top_k: int = DEFAULT_TOP_K) -> int:
    """Convert top-K JobListing rows from a ScrapeJob into RecommendedJob rows.

    Returns the number of RecommendedJob rows created or updated.
    """
    from profiles.models import UserProfile

    profile = UserProfile.objects.filter(user_id=user_id).first()
    if profile is None:
        logger.warning("score_listings: no UserProfile for user %s — skipping", user_id)
        return 0

    listings = list(JobListing.objects.filter(scrape_job_id=scrape_job_id))
    if not listings:
        return 0

    survivors = _prefilter(profile, listings)
    logger.info(
        "score_listings: %d listings -> %d after prefilter (scrape_job=%s)",
        len(listings), len(survivors), scrape_job_id,
    )

    scored = []
    for li in survivors:
        candidate = _candidate_from_listing(li)
        try:
            result = compute_gap_analysis(profile, candidate)
        except Exception:
            logger.exception("compute_gap_analysis failed for listing %s", li.pk)
            continue
        score = float(result.get("similarity_score") or 0.0)
        scored.append((score, li))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    written = 0
    with transaction.atomic():
        for score, li in top:
            try:
                _upsert_recommendation(user_id, li, score)
                written += 1
            except Exception:
                logger.exception("Failed to upsert RecommendedJob for listing %s", li.pk)
    return written
