"""Render-verify seed for the ATS breakdown panel (Slices 1-4).

Builds one demo résumé that triggers all FOUR card types so a single editor load
covers the whole feature for the eyeball:

  * actionable "Docker"     (must-have) — evidence-backed gap match, absent from
                                          content['skills'] → "add to skills" + delta.
  * actionable "Kubernetes" (nice-to-have) — same shape, nice tier.
  * advisory  "Python"      — repeated >4× in a bullet → keyword-density penalty.
  * quantify  (Category-2)  — an achievement-shaped, number-less bullet ("Led the
                              migration …") → asks the user for a REAL figure.

All rows are authored BY HAND — the command NEVER calls the generation pipeline.
It runs fully offline.

Auto-regen note: the editor GET redirects into the (v2) regenerate flow when
``profile.updated_at > resume.created_at`` (views.py:483). So we create the
profile FIRST (older than the résumé) — that keeps the editor from regenerating,
AND gives Category-2 a profile entry to write the user's figure into (the
quantify card needs a matching ``experiences[]`` entry).

Usage:
    python manage.py seed_ats_demo
then log in with the printed credentials and open the printed /resumes/edit/<id>/.
Re-running is idempotent (it replaces the demo user's profile + job + résumé).
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from jobs.models import Job
from analysis.models import GapAnalysis
from profiles.models import UserProfile
from resumes.models import GeneratedResume
from resumes.services.ats_cards import build_ats_cards, _match_profile_entry
from resumes.services.ats_breakdown import refresh_ats_score

DEMO_EMAIL = "ats-demo@example.com"
DEMO_PASSWORD = "atsdemo12345"
DEMO_JOB_TITLE = "ATS Demo — Backend Engineer"
EXP_TITLE = "Backend Engineer"
EXP_COMPANY = "PriorCo"


class Command(BaseCommand):
    help = ("Seed a demo résumé that triggers all four ATS card types "
            "(actionable ×2, advisory, quantify). No generation.")

    def handle(self, *args, **options):
        User = get_user_model()
        user, _ = User.objects.get_or_create(
            email=DEMO_EMAIL, defaults={"username": DEMO_EMAIL},
        )
        user.set_password(DEMO_PASSWORD)
        user.save()

        # Idempotent cleanup.
        Job.objects.filter(user=user, title=DEMO_JOB_TITLE).delete()
        UserProfile.objects.filter(user=user).delete()

        # Profile FIRST (older than the résumé → no editor auto-regen) and with an
        # experiences[] entry matching the résumé experience, so the Category-2
        # write has a confident target.
        profile = UserProfile.objects.create(
            user=user, full_name="Demo User",
            data_content={
                "full_name": "Demo User",
                "skills": [{"name": "Python"}, {"name": "PostgreSQL"}],
                "experiences": [{
                    "title": EXP_TITLE, "company": EXP_COMPANY,
                    "start_date": "2022", "end_date": "Present",
                    "description": ["Maintained backend services and the on-call rotation."],
                }],
            },
        )

        job = Job.objects.create(
            user=user,
            title=DEMO_JOB_TITLE,
            company="Demo Corp",
            description="Backend role needing Python, Docker, Kubernetes and PostgreSQL.",
            extracted_skills=["Python", "Docker", "Kubernetes", "PostgreSQL"],
            extracted_skills_tiers={
                "must_have": ["Python", "Docker"],
                "nice_to_have": ["Kubernetes", "PostgreSQL"],
            },
        )
        gap = GapAnalysis.objects.create(
            user=user, job=job, similarity_score=0.5,
            matched_must_have=[
                {"name": "Docker", "evidence_source": "projects",
                 "evidence_quote": "Built CI/CD pipelines with Docker"},
                {"name": "Python", "evidence_source": "experience",
                 "evidence_quote": "Three years building Python services"},
            ],
            matched_nice_to_have=[
                {"name": "Kubernetes", "evidence_source": "projects",
                 "evidence_quote": "Deployed microservices on Kubernetes"},
            ],
        )
        resume = GeneratedResume.objects.create(
            gap_analysis=gap, name="ATS Demo Résumé",
            content={
                "professional_title": "Backend Engineer",
                "professional_summary": "Backend engineer who builds reliable services.",
                # Docker / Kubernetes deliberately absent → actionable cards.
                "skills": ["Python", "PostgreSQL", "REST APIs", "Git"],
                "experience": [{
                    "title": EXP_TITLE, "company": EXP_COMPANY,
                    "duration": "2022 - Present",
                    "description": [
                        # No number, ≥50 chars, action-verb start → QUANTIFY card.
                        "Led the migration of the billing service to a new datastore.",
                        # Has "12" (so NOT a quantify card) and repeats Python >4×
                        # → keyword-density (stuffing) ADVISORY card.
                        "Shipped 12 services in Python. Python Python Python Python Python.",
                    ],
                }],
                "template_name": "ats_clean",
            },
        )

        refresh_ats_score(resume)

        # Self-check: fail loudly rather than print a URL to a dud.
        cards = build_ats_cards(resume)
        actionable = {c["skill"] for c in cards if c["kind"] == "actionable"}
        advisory = {c["skill"] for c in cards if c["kind"] == "advisory"}
        quantify = [c for c in cards if c["kind"] == "quantify"]
        missing = []
        if "Docker" not in actionable:
            missing.append("actionable Docker")
        if "Kubernetes" not in actionable:
            missing.append("actionable Kubernetes")
        if "Python" not in advisory:
            missing.append("advisory Python (stuffing)")
        if not quantify:
            missing.append("quantify card")
        else:
            # Confirm the quantify card actually wires to a profile entry, so its
            # Save-to-profile would have a target (else the demo's card is inert).
            qc = quantify[0]
            item = (resume.content.get(qc["section"]) or [])[qc["item_idx"]]
            if _match_profile_entry(profile.data_content, qc["section"], item) is None:
                missing.append("profile match for the quantify card")
        if missing:
            raise CommandError(
                "Seed produced a dud — missing: " + ", ".join(missing)
                + f". Got actionable={sorted(actionable)}, advisory={sorted(advisory)}, "
                f"quantify={len(quantify)}."
            )

        self.stdout.write(self.style.SUCCESS("Seeded ATS demo résumé (self-check passed)."))
        self.stdout.write(f"  Login:  {DEMO_EMAIL}  /  {DEMO_PASSWORD}")
        self.stdout.write(f"  Open:   /resumes/edit/{resume.id}/")
        self.stdout.write(
            f"  Cards:  actionable {sorted(actionable)} + advisory {sorted(advisory)} "
            f"+ {len(quantify)} quantify"
        )
