"""Career stage detection.

Reframes the dashboard from "what artifact do you need?" (résumé, cover
letter, etc.) to "where in your career are you right now?" — and surfaces
the right tool for that moment.

Stages, in priority order (highest stage wins):

  - getting_started → no master profile
  - ready_to_look   → profile complete, no jobs yet
  - actively_applying → at least one job in Saved or Applied
  - interviewing     → at least one job in Interviewing (overrides applying)
  - offer_in_hand    → at least one job in Offer (highest priority)
  - reflecting       → only Rejected jobs (and no other active ones)

Each stage maps to: a label, a one-sentence detail, and a recommended
primary action (route name + label). Used by the dashboard hero.
"""
from __future__ import annotations

from typing import TypedDict, Optional


# Job statuses match jobs.models.Job.STATUS_CHOICES values (lowercase).
STATUS_OFFER = 'offer'
STATUS_INTERVIEWING = 'interviewing'
STATUS_APPLIED = 'applied'
STATUS_SAVED = 'saved'
STATUS_REJECTED = 'rejected'


class CareerStage(TypedDict):
    key: str             # machine id ('getting_started', 'offer_in_hand', ...)
    label: str           # short label for the eyebrow ("Just starting" / "Got an offer")
    detail: str          # one-sentence prose for the hero
    primary_label: str   # CTA button label
    primary_href: str    # CTA link (relative or absolute)
    primary_route: Optional[str]  # URL route name when applicable, else None
    tone: str            # 'brand' | 'accent' | 'success' | 'warning' | 'neutral'


def detect_career_stage(*,
                        has_profile: bool,
                        status_counts: dict[str, int]) -> CareerStage:
    """Pure function: pick the right stage given profile + per-status job counts.

    `status_counts` is a dict like {'saved': 2, 'applied': 5, ...}. Keys are
    case-insensitive and missing keys are treated as 0.
    """
    counts = {(k or '').lower(): int(v or 0) for k, v in (status_counts or {}).items()}

    if not has_profile:
        return CareerStage(
            key='getting_started',
            label='Just starting',
            detail="Let's build your master profile — one upload powers everything else.",
            primary_label='Upload your CV',
            primary_href='/profiles/setup/upload/',
            primary_route='upload_master_profile',
            tone='brand',
        )

    if counts.get(STATUS_OFFER, 0) > 0:
        return CareerStage(
            key='offer_in_hand',
            label='Offer in hand',
            detail="Time to negotiate. Your agent has a script ready, anchored in your strengths.",
            primary_label='Open the negotiator',
            primary_href='/profiles/dashboard/#applications',
            primary_route=None,
            tone='success',
        )

    if counts.get(STATUS_INTERVIEWING, 0) > 0:
        return CareerStage(
            key='interviewing',
            label='In interviews',
            detail="Prep with the chatbot — it knows the role and your evidence, and will mock-interview for both.",
            primary_label='Prep an interview',
            primary_href='/profiles/dashboard/#applications',
            primary_route=None,
            tone='accent',
        )

    if counts.get(STATUS_APPLIED, 0) > 0 or counts.get(STATUS_SAVED, 0) > 0:
        return CareerStage(
            key='actively_applying',
            label='Actively applying',
            detail="Tailor a résumé per posting. Cover letters and cold emails follow from the same evidence.",
            primary_label='Add a new job',
            primary_href='/jobs/input/',
            primary_route='job_input_view',
            tone='brand',
        )

    if counts.get(STATUS_REJECTED, 0) > 0:
        return CareerStage(
            key='reflecting',
            label='Regrouping',
            detail="A no isn't a verdict on you — it's data. Run a learning path on the gaps and try again.",
            primary_label='Build a learning path',
            primary_href='/analysis/learning-path/',
            primary_route='learning_path_global',
            tone='neutral',
        )

    return CareerStage(
        key='ready_to_look',
        label='Ready to look',
        detail="Profile's set. Drop in a job posting and your agent will tell you exactly where you stand.",
        primary_label='Show your agent a job',
        primary_href='/jobs/input/',
        primary_route='job_input_view',
        tone='brand',
    )


def detect_stage_for_dashboard(profile, kanban_boards: dict) -> CareerStage:
    """Convenience: build the input dict from the existing dashboard context."""
    has_profile = bool(profile and getattr(profile, 'full_name', None))
    status_counts = {
        (status or '').lower(): len(jobs or [])
        for status, jobs in (kanban_boards or {}).items()
    }
    return detect_career_stage(has_profile=has_profile, status_counts=status_counts)
