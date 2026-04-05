import uuid
from django.db import models
from django.conf import settings
from jobs.models import Job

class GapAnalysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='gap_analyses')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='gap_analyses', null=True)
    
    matched_skills = models.JSONField(default=list)
    missing_skills = models.JSONField(default=list)
    partial_skills = models.JSONField(default=list)
    
    similarity_score = models.FloatField(default=0.0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'gap_analyses'
        unique_together = ('job', 'user')

