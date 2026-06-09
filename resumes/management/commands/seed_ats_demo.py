"""Render-verify seed for the ATS breakdown panel (Slices 1-3).

Builds one demo résumé that triggers all three cards so a single eyeball pays
down the Slice-2 render debt and confirms Slice-3 apply:

  * actionable "Docker"     (must-have) — evidence-backed gap match, absent from
                                          content['skills'] → "add to skills" + delta.
  * actionable "Kubernetes" (nice-to-have) — same shape, nice tier.
  * advisory  "Python"      — repeated >4× in a bullet → keyword-density penalty.

All rows are authored BY HAND. The command NEVER calls the generation pipeline
(generate_resume_content_dispatched / build_plan / the fact store / any v2
function): a render fixture needs finished, deterministic data, not LLM output.
It runs fully offline.

It also deletes any UserProfile for the demo user. `resume_edit_view` GET
auto-redirects to the (v2) regenerate flow when ``profile.updated_at >
resume.created_at`` (views.py:483); with no profile that whole block is skipped
(UserProfile.DoesNotExist), so the editor renders the seeded content directly
instead of trying to regenerate it against an empty fact store.

Usage:
    python manage.py seed_ats_demo
then log in with the printed credentials and open the printed /resumes/edit/<id>/.
Re-running is idempotent (it replaces the demo user's job + résumé + profile).
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from jobs.models import Job
from analysis.models import GapAnalysis
from profiles.models import UserProfile
from resumes.models import GeneratedResume
from resumes.services.ats_cards import build_ats_cards
from resumes.services.ats_breakdown import refresh_ats_score

DEMO_EMAIL = "ats-demo@example.com"
DEMO_PASSWORD = "atsdemo12345"
DEMO_JOB_TITLE = "ATS Demo — Backend Engineer"


class Command(BaseCommand):
    help = ("Seed a demo résumé that triggers both an actionable and an advisory "
            "ATS card (render-verify for the breakdown panel). No generation.")

    def handle(self, *args, **options):
        User = get_user_model()
        user, _ = User.objects.get_or_create(
            email=DEMO_EMAIL, defaults={"username": DEMO_EMAIL},
        )
        user.set_password(DEMO_PASSWORD)
        user.save()

        # Idempotent cleanup. Deleting the demo job cascades its gap + résumé.
        # Deleting the profile is what keeps the editor from auto-redirecting
        # into the v2 regenerate flow (which would crash on an empty fact store).
        Job.objects.filter(user=user, title=DEMO_JOB_TITLE).delete()
        UserProfile.objects.filter(user=user).delete()

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
                # Docker / Kubernetes deliberately absent → scorer marks them
                # missing → actionable cards (the gap proves the candidate HAS them).
                "skills": ["Python", "PostgreSQL", "REST APIs", "Git"],
                "experience": [{
                    "title": "Backend Engineer", "company": "PriorCo",
                    "duration": "2022 - Present",
                    # IMPORTANT: the résumé text must NOT contain "Docker" or
                    # "Kubernetes" anywhere. The scorer scans the whole content
                    # JSON (bullets included), so naming them here would count
                    # them as PRESENT (matched) and the actionable cards would not
                    # fire. The cards' grounded evidence quotes live on the
                    # GapAnalysis (the master-profile evidence build_ats_cards
                    # reads), NOT the résumé — that's exactly the gap these cards
                    # close ("you have it; it isn't in your résumé text").
                    "description": [
                        "Designed and shipped reliable backend services end to end.",
                        # A skill repeated >4× → keyword-density (stuffing) advisory card.
                        "Built services in Python. Python Python Python Python Python everywhere.",
                    ],
                }],
                "template_name": "ats_clean",
            },
        )

        # Store the honest recomputed score (the panel recomputes live anyway).
        refresh_ats_score(resume)

        # Self-check: fail loudly rather than print a URL to a dud. Guarantees the
        # eyeball actually shows the three cards.
        cards = build_ats_cards(resume)
        actionable = {c["skill"] for c in cards if c["kind"] == "actionable"}
        advisory = {c["skill"] for c in cards if c["kind"] == "advisory"}
        missing = []
        if "Docker" not in actionable:
            missing.append("actionable Docker")
        if "Kubernetes" not in actionable:
            missing.append("actionable Kubernetes")
        if "Python" not in advisory:
            missing.append("advisory Python (stuffing)")
        if missing:
            raise CommandError(
                "Seed produced a dud — expected cards missing: "
                + ", ".join(missing)
                + f". Got actionable={sorted(actionable)}, advisory={sorted(advisory)}."
            )

        self.stdout.write(self.style.SUCCESS("Seeded ATS demo résumé (self-check passed)."))
        self.stdout.write(f"  Login:  {DEMO_EMAIL}  /  {DEMO_PASSWORD}")
        self.stdout.write(f"  Open:   /resumes/edit/{resume.id}/")
        self.stdout.write(
            f"  Cards:  actionable {sorted(actionable)} + advisory {sorted(advisory)}"
        )
