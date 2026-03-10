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
