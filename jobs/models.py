import hashlib
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
    url = models.URLField(max_length=2000, null=True, blank=True)
    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField()
    raw_html = models.TextField(null=True, blank=True)
    extracted_skills = models.JSONField(default=list)
    # Skill-extractor v2 (2026-05-14): tiered skills + domain. The flat
    # extracted_skills field above is preserved as the union (must + nice)
    # for back-compat with the gap analyzer, resume generator, and benchmarks.
    # Shape: {"must_have": [str, ...], "nice_to_have": [str, ...]}
    extracted_skills_tiers = models.JSONField(default=dict, blank=True)
    # Canonical industry domain inferred from the JD body ("Financial Services",
    # "Healthcare", "Gaming", ...). Free-text fallback when unmapped. "" when
    # no signal.
    domain = models.CharField(max_length=64, blank=True, default='')
    embedding = VectorField(dimensions=384, null=True, blank=True)
    application_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='saved')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'jobs'
        ordering = ['-created_at']

class RecommendedJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recommendations')
    url = models.URLField(max_length=2000)
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


class ScrapeJob(models.Model):
    """A single run of the job-board scraper for a user. Owns N JobListing rows."""
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_ERROR, "Error"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='scrape_jobs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    params_json = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    progress_pct = models.PositiveSmallIntegerField(default=0)
    total_steps = models.PositiveIntegerField(default=0)
    completed_steps = models.PositiveIntegerField(default=0)
    current_step = models.CharField(max_length=255, blank=True, default="")
    message = models.CharField(max_length=255, blank=True, default="")
    error = models.TextField(blank=True, default="")
    cancel_requested = models.BooleanField(default=False)

    class Meta:
        db_table = 'scrape_jobs'
        ordering = ['-created_at']

    def __str__(self):
        return f"ScrapeJob {self.pk} [{self.status}] {self.progress_pct}%"

    @property
    def is_terminal(self) -> bool:
        return self.status in {self.STATUS_DONE, self.STATUS_ERROR, self.STATUS_CANCELLED}


class JobListing(models.Model):
    """One raw scraped job row. Lives only as long as it takes the scoring step
    to convert the top-K into RecommendedJob rows; can be GC'd later."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scrape_job = models.ForeignKey(ScrapeJob, on_delete=models.CASCADE, related_name='listings')
    source = models.CharField(max_length=32)
    title = models.CharField(max_length=512, blank=True, default="")
    company = models.CharField(max_length=512, blank=True, default="")
    company_url = models.URLField(max_length=2000, blank=True, default="")
    location = models.CharField(max_length=512, blank=True, default="")
    country = models.CharField(max_length=128, blank=True, default="")
    posted = models.CharField(max_length=128, blank=True, default="")
    salary = models.CharField(max_length=255, blank=True, default="")
    url = models.URLField(max_length=2000, blank=True, default="")
    description = models.TextField(blank=True, default="")
    raw_text = models.TextField(blank=True, default="")
    scraped_at = models.DateTimeField(auto_now_add=True)
    unique_hash = models.CharField(max_length=64)

    class Meta:
        db_table = 'job_listings'
        unique_together = ("scrape_job", "unique_hash")
        indexes = [models.Index(fields=["scrape_job", "source"])]
        ordering = ['-scraped_at']

    def __str__(self):
        return f"[{self.source}] {self.title} @ {self.company}"

    @staticmethod
    def make_hash(source, url, title, company, location):
        key = url.strip() if url else f"{source}|{title}|{company}|{location}"
        return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()
