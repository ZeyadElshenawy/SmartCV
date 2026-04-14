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

Each stage carries:
  - label / detail / tone — for the hero copy
  - primary_label + primary_href (+ primary_route) — one clear CTA
  - secondary_actions — 2-3 quieter next moves, rendered as a strip below
"""
from __future__ import annotations

from typing import TypedDict, Optional


# Job statuses match jobs.models.Job.STATUS_CHOICES values (lowercase).
STATUS_OFFER = 'offer'
STATUS_INTERVIEWING = 'interviewing'
STATUS_APPLIED = 'applied'
STATUS_SAVED = 'saved'
STATUS_REJECTED = 'rejected'


class StageAction(TypedDict):
    label: str
    href: str


class CareerStage(TypedDict):
    key: str
    label: str
    detail: str
    primary_label: str
    primary_href: str
    primary_route: Optional[str]
    tone: str                         # brand | accent | success | warning | neutral
    secondary_actions: list[StageAction]


# ---- Helpers --------------------------------------------------

def _latest(jobs: list) -> Optional[object]:
    """Pick the most recently created job from a list, or None."""
    if not jobs:
        return None
    try:
        return sorted(jobs, key=lambda j: getattr(j, 'created_at', None) or '', reverse=True)[0]
    except Exception:
        return jobs[0]


def _gap_url(job_id) -> str:
    return f'/analysis/gap/{job_id}/'


def _chat_url(job_id) -> str:
    return f'/profiles/chatbot/{job_id}/'


def _salary_url(job_id) -> str:
    return f'/analysis/salary/{job_id}/'


def _cover_letter_url(job_id) -> str:
    return f'/resumes/cover-letter/{job_id}/'


def _outreach_url(job_id) -> str:
    return f'/profiles/outreach/{job_id}/'


def _resume_url(job_id) -> str:
    return f'/resumes/generate/{job_id}/'


def _agent_url(job_id) -> str:
    return f'/agent/?job={job_id}'


# ---- Core detector --------------------------------------------

def detect_career_stage(*,
                        has_profile: bool,
                        status_counts: dict[str, int],
                        jobs_by_status: Optional[dict[str, list]] = None) -> CareerStage:
    """Pure function: pick the right stage and primary + secondary CTAs.

    `status_counts` → {'saved': N, 'applied': N, ...} (case-insensitive).
    `jobs_by_status` → {status: [Job, ...]} when available, so primary
    actions can deep-link to the specific job that needs attention. Falls
    back to generic hub URLs if not supplied.
    """
    counts = {(k or '').lower(): int(v or 0) for k, v in (status_counts or {}).items()}
    jobs_by_status = {
        (k or '').lower(): list(v or [])
        for k, v in (jobs_by_status or {}).items()
    }

    if not has_profile:
        return CareerStage(
            key='getting_started',
            label='Just starting',
            detail="Let's build your master profile — one upload powers everything else.",
            primary_label='Upload your CV',
            primary_href='/profiles/setup/upload/',
            primary_route='upload_master_profile',
            tone='brand',
            secondary_actions=[
                StageAction(label='Build by form', href='/profiles/setup/review/'),
                StageAction(label='Skip intro',    href='/profiles/dashboard/'),
            ],
        )

    # OFFER IN HAND — deep-link to the specific offer's negotiator when we can.
    if counts.get(STATUS_OFFER, 0) > 0:
        offer_job = _latest(jobs_by_status.get(STATUS_OFFER) or [])
        primary_href = _salary_url(offer_job.id) if offer_job else '/profiles/applications/'
        secondary: list[StageAction] = []
        if offer_job:
            secondary.append(StageAction(label=f'Open {offer_job.company or "offer"} negotiator', href=_salary_url(offer_job.id)))
            secondary.append(StageAction(label='Write a thank-you', href=_cover_letter_url(offer_job.id)))
        secondary.append(StageAction(label='Review other offers', href='/applications/'))
        return CareerStage(
            key='offer_in_hand',
            label='Offer in hand',
            detail="Time to negotiate. Your agent has a script ready, anchored in your strengths.",
            primary_label=f'Negotiate {offer_job.company}' if offer_job and offer_job.company else 'Open the negotiator',
            primary_href=primary_href,
            primary_route=None,
            tone='success',
            secondary_actions=secondary[:3],
        )

    # INTERVIEWING — deep-link to the specific job's chatbot for mock interview prep.
    if counts.get(STATUS_INTERVIEWING, 0) > 0:
        iv_job = _latest(jobs_by_status.get(STATUS_INTERVIEWING) or [])
        primary_href = _chat_url(iv_job.id) if iv_job else '/applications/'
        secondary = []
        if iv_job:
            secondary.append(StageAction(label='Review the gap analysis', href=_gap_url(iv_job.id)))
            secondary.append(StageAction(label='Ask agent about this role', href=_agent_url(iv_job.id)))
        secondary.append(StageAction(label='See pipeline', href='/applications/'))
        return CareerStage(
            key='interviewing',
            label='In interviews',
            detail="Prep with the chatbot — it knows the role and your evidence, and will mock-interview for both.",
            primary_label=f'Prep for {iv_job.company}' if iv_job and iv_job.company else 'Prep an interview',
            primary_href=primary_href,
            primary_route=None,
            tone='accent',
            secondary_actions=secondary[:3],
        )

    # ACTIVELY APPLYING — keep "add job" as primary, suggest artifact work on the last applied/saved.
    if counts.get(STATUS_APPLIED, 0) > 0 or counts.get(STATUS_SAVED, 0) > 0:
        recent = _latest((jobs_by_status.get(STATUS_APPLIED) or []) + (jobs_by_status.get(STATUS_SAVED) or []))
        secondary = [StageAction(label='See pipeline', href='/applications/')]
        if recent:
            secondary.insert(0, StageAction(label=f'Cover letter · {recent.company or "last job"}', href=_cover_letter_url(recent.id)))
            secondary.insert(1, StageAction(label=f'Outreach · {recent.company or "last job"}',     href=_outreach_url(recent.id)))
        return CareerStage(
            key='actively_applying',
            label='Actively applying',
            detail="Tailor a résumé per posting. Cover letters and cold emails follow from the same evidence.",
            primary_label='Add a new job',
            primary_href='/jobs/input/',
            primary_route='job_input_view',
            tone='brand',
            secondary_actions=secondary[:3],
        )

    # REFLECTING — only rejected, nothing else active.
    if counts.get(STATUS_REJECTED, 0) > 0:
        rej = _latest(jobs_by_status.get(STATUS_REJECTED) or [])
        secondary = [
            StageAction(label='Add a fresh job', href='/jobs/input/'),
            StageAction(label='Connect GitHub for stronger evidence', href='/insights/'),
        ]
        if rej:
            secondary.insert(0, StageAction(label=f'Review the gap for {rej.company or "last job"}', href=_gap_url(rej.id)))
        return CareerStage(
            key='reflecting',
            label='Regrouping',
            detail="A no isn't a verdict on you — it's data. Run a learning path on the gaps and try again.",
            primary_label='Build a learning path',
            primary_href='/analysis/learning-path/',
            primary_route='learning_path_global',
            tone='neutral',
            secondary_actions=secondary[:3],
        )

    # READY TO LOOK — profile complete, no jobs yet. Push external signals + exploration.
    return CareerStage(
        key='ready_to_look',
        label='Ready to look',
        detail="Profile's set. Drop in a job posting and your agent will tell you exactly where you stand.",
        primary_label='Show your agent a job',
        primary_href='/jobs/input/',
        primary_route='job_input_view',
        tone='brand',
        secondary_actions=[
            StageAction(label='Strengthen your evidence', href='/insights/'),
            StageAction(label='Edit your profile',        href='/profiles/setup/review/'),
        ],
    )


def detect_stage_for_dashboard(profile, kanban_boards: dict) -> CareerStage:
    """Build the input for detect_career_stage from the existing dashboard context."""
    has_profile = bool(profile and getattr(profile, 'full_name', None))
    status_counts = {}
    jobs_by_status = {}
    for status, jobs in (kanban_boards or {}).items():
        key = (status or '').lower()
        jobs_list = list(jobs or [])
        status_counts[key] = len(jobs_list)
        jobs_by_status[key] = jobs_list
    return detect_career_stage(
        has_profile=has_profile,
        status_counts=status_counts,
        jobs_by_status=jobs_by_status,
    )
