import uuid
from django.db import models
from analysis.models import GapAnalysis

class GeneratedResume(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gap_analysis = models.ForeignKey(GapAnalysis, on_delete=models.CASCADE, related_name='resumes')
    name = models.CharField(max_length=200, default='Tailored Resume')
    
    content = models.JSONField(default=dict)  # Structured resume sections
    html_content = models.TextField(blank=True)
    ats_score = models.FloatField(default=0.0)
    version = models.IntegerField(default=1)
    # Deterministic bullet-validator findings + stats, written when the
    # resume_generator post-LLM hook runs (§4 of the RAG plan). Empty dict
    # on legacy rows / when the validator is disabled. Shape:
    # {"passed": bool, "findings": [...], "stats": {...}}
    validation_report = models.JSONField(default=dict, blank=True)
    # Fix #1 — content stickiness (audit §6.5, 2026-05-30).
    # Snapshot of the resume content as it was at the user's most recent
    # export (PDF or DOCX). On the next regeneration for the same JD, the
    # supervised loop injects this as a "preserve OR improve, do not
    # regress" reference and deterministically enforces no-metric-loss /
    # no-bullet-count-drop against it. Empty dict means "no prior export
    # yet" (the resume regenerates without a stickiness reference, same
    # as today). Shape when populated:
    #   {
    #     "content": <deepcopy of resume.content at export time>,
    #     "exported_at": "<iso timestamp>",
    #     "ats_score_at_export": <float>,
    #     "jd_identity_hash": "<sha256 of normalised job identity>",
    #   }
    # The snapshot lives on the RESUME row — never on the master profile.
    previous_best = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'generated_resumes'
        ordering = ['-created_at']

class CoverLetter(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey('jobs.Job', on_delete=models.CASCADE, related_name='cover_letters')
    profile = models.ForeignKey('profiles.UserProfile', on_delete=models.CASCADE, related_name='cover_letters')
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'cover_letters'
        ordering = ['-created_at']
