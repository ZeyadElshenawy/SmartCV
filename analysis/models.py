import uuid
from django.db import models
from django.conf import settings
from jobs.models import Job

class GapAnalysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='gap_analyses')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='gap_analyses', null=True)

    # Legacy flat-list storage (kept populated for back-compat with older
    # consumers — gap_analysis_view falls back to these when the tiered
    # fields below are empty for a row, and the drag-drop endpoint mirrors
    # the union back here so any external poller sees consistent data).
    matched_skills = models.JSONField(default=list)
    missing_skills = models.JSONField(default=list)
    partial_skills = models.JSONField(default=list)

    # ----- Tier-aware proximity-enriched fields (v2, 2026-05-14) -----
    # Each entry shape:
    #   matched_*:   {"name": str, "evidence_source": str, "evidence_quote": str,
    #                  "user_asserted": bool}
    #       user_asserted=True marks a match the user moved here via the
    #       reorganize UI that has NO profile evidence (evidence_source='user').
    #       It still counts toward similarity_score, but the UI flags it
    #       "self-reported" and the resume generator won't surface it without
    #       grounded evidence. Set by analysis.views.update_gap_skills.
    #   missing_*:   {"name": str, "source_quote": str, "proximity": float,
    #                  "proximity_reason": str, "bridge_hint": str | None}
    matched_must_have = models.JSONField(default=list, blank=True)
    matched_nice_to_have = models.JSONField(default=list, blank=True)
    missing_must_have = models.JSONField(default=list, blank=True)
    missing_nice_to_have = models.JSONField(default=list, blank=True)

    similarity_score = models.FloatField(default=0.0)
    # Bucket label derived from similarity_score via match_band():
    # strong (>=0.85), solid (>=0.70), partial (>=0.55), weak (<0.55).
    match_band = models.CharField(max_length=16, blank=True, default='')
    # Mean proximity across both missing tiers (None when zero missing).
    # Surfaced as "You're X% of the way to closing your gaps" when >0.5
    # and missing count >= 3.
    avg_proximity = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'gap_analyses'
        unique_together = ('job', 'user')

