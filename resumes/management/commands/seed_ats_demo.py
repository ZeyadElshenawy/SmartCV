"""Render-verify seed for the ATS breakdown panel (Slices 1-3).

Creates one demo résumé that triggers BOTH card types at once so a single eyeball
pays down the Slice-2 render debt and confirms Slice-3 apply:

  * two ACTIONABLE cards — "Docker" (must-have) and "Kubernetes" (nice-to-have):
    each is an evidence-backed gap match (evidence_source='projects', real quote)
    that is absent from content['skills'], so the scorer counts it missing →
    "add to skills" card with a real coverage delta + a working Apply button.
  * one ADVISORY card — "Python" is repeated >4× in a bullet (prose stuffing).

This is a VERIFICATION ARTIFACT, not a code path — it only writes demo rows.

Usage:
    python manage.py seed_ats_demo
then log in with the printed credentials and open the printed /resumes/edit/<id>/.
Re-running is idempotent (it replaces the prior demo job for the demo user).
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from jobs.models import Job
from analysis.models import GapAnalysis
from resumes.models import GeneratedResume

DEMO_EMAIL = "ats-demo@example.com"
DEMO_PASSWORD = "atsdemo12345"
DEMO_JOB_TITLE = "ATS Demo — Backend Engineer"


class Command(BaseCommand):
    help = ("Seed a demo résumé that triggers both an actionable and an advisory "
            "ATS card (render-verify for the breakdown panel).")

    def handle(self, *args, **options):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=DEMO_EMAIL, defaults={"username": DEMO_EMAIL},
        )
        user.set_password(DEMO_PASSWORD)
        user.save()

        # Idempotent: drop any prior demo job (cascades gap + résumé).
        Job.objects.filter(user=user, title=DEMO_JOB_TITLE).delete()

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
                "skills": ["Python"],   # Docker / Kubernetes deliberately absent
                "experience": [{
                    "title": "Backend Engineer", "company": "PriorCo",
                    "duration": "2022 - Present",
                    # Python repeated >4× → stuffing advisory card.
                    "description": [
                        "Built services in Python. Python Python Python Python Python everywhere.",
                    ],
                }],
                "template_name": "ats_clean",
            },
        )

        self.stdout.write(self.style.SUCCESS("Seeded ATS demo résumé."))
        self.stdout.write(f"  Login:  {DEMO_EMAIL}  /  {DEMO_PASSWORD}")
        self.stdout.write(f"  Open:   /resumes/edit/{resume.id}/")
        self.stdout.write("  Expect: 2 actionable cards (Docker, Kubernetes) + 1 advisory (Python).")
