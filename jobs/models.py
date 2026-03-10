import uuid
from django.db import models
from django.conf import settings
from pgvector.django import VectorField

class Job(models.Model):
    STATUS_CHOICES = [
        ('saved', 'Saved'),
        ('applied', 'Applied'),
        ('interviewing', 'Interviewing'),
        ('offer', 'Offer'),
        ('rejected', 'Rejected'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='jobs')
    url = models.URLField(max_length=500, null=True, blank=True)
    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField()
    raw_html = models.TextField(null=True, blank=True)
    extracted_skills = models.JSONField(default=list)
    embedding = VectorField(dimensions=768, null=True, blank=True)
    application_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='saved')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'jobs'
        ordering = ['-created_at']

class RecommendedJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recommendations')
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField()
    match_score = models.IntegerField(help_text="0-100 score from gap analysis")
    status = models.CharField(max_length=20, choices=[
        ('new', 'New'),
        ('saved', 'Saved to Board'),
        ('dismissed', 'Dismissed')
    ], default='new')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'recommended_jobs'
        ordering = ['-match_score', '-created_at']
