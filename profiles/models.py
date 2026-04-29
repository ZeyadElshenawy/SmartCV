import uuid
from django.db import models
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from pgvector.django import VectorField

class UserProfile(models.Model):
    INPUT_METHOD_CHOICES = [
        ('upload', 'CV Upload'),
        ('form', 'Manual Form'),
        ('chatbot', 'Chatbot')
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    input_method = models.CharField(max_length=20, choices=INPUT_METHOD_CHOICES, null=True)
    
    # Contact Information
    full_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20, null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    linkedin_url = models.URLField(null=True, blank=True)
    github_url = models.URLField(null=True, blank=True)
    
    
    # Core Structured Data (fields moved to data_content)
    # Removing: skills, experiences, education, certifications, projects
    # These are now in data_content

    
    # NEW: Complete CV Data Storage (no data loss!)
    # Consolidated JSONB field for all resume content
    # Contains ALL sections from parsed CV including:
    # - Core: skills, experiences, education, projects, certifications
    # - Optional: publications, awards, languages, volunteer, patents, 
    #            speaking_engagements, hobbies, honors, etc.
    data_content = models.JSONField(default=dict)
    
    # Vector embedding for semantic search (all-MiniLM-L6-v2 uses 384 dimensions)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    
    # Multi-Vector Architecture (Phase 1)
    embedding_skills = VectorField(dimensions=384, null=True, blank=True)
    embedding_experience = VectorField(dimensions=384, null=True, blank=True)
    embedding_education = VectorField(dimensions=384, null=True, blank=True)
    
    # CV Upload
    uploaded_cv = models.FileField(upload_to='cvs/', null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # --- URL accessors surfaced in the rendered CV's contact line ---
    # The parser writes these into data_content; the PDF templates + live
    # preview read them via these helpers so contact info stays consistent
    # without digging into the JSONB shape in template code.
    @property
    def portfolio_url(self):
        return (self.data_content or {}).get('portfolio_url') or ''

    @property
    def kaggle_url(self):
        """Pulled from explicit kaggle_url, or built from kaggle_signals.username
        when only the handle is known."""
        data = self.data_content or {}
        explicit = data.get('kaggle_url') or ''
        if explicit:
            return explicit
        kg = data.get('kaggle_signals') or {}
        if isinstance(kg, dict):
            u = (kg.get('username') or '').strip()
            if u:
                return f"https://www.kaggle.com/{u}"
        return ''

    @property
    def scholar_url(self):
        data = self.data_content or {}
        explicit = data.get('scholar_url') or ''
        if explicit:
            return explicit
        sc = data.get('scholar_signals') or {}
        if isinstance(sc, dict):
            return (sc.get('profile_url') or sc.get('url') or '').strip()
        return ''

    @property
    def other_urls(self):
        """Catch-all list — any URL the parser captured that doesn't fit
        a named slot. Rendered as labeled chips at the end of the contact
        line so nothing gets dropped silently."""
        urls = (self.data_content or {}).get('other_urls') or []
        return [u for u in urls if isinstance(u, str) and u.strip()]

    @property
    def objective(self):
        """Objective statement from data_content. Empty string when absent
        — never None, so templates can use the value directly without a
        |default filter and the form binding stays honest."""
        return (self.data_content or {}).get('objective') or ''

    @property
    def normalized_summary(self):
        """Profile summary surfaced from data_content. Falls back to the
        raw `summary` field if the LLM normalizer didn't run, so a
        partially-populated profile still shows its summary text in the
        master review form."""
        data = self.data_content or {}
        return data.get('normalized_summary') or data.get('summary') or ''

    # Properties for backward compatibility
    @property
    def skills(self):
        return self.data_content.get('skills', [])
        
    @skills.setter
    def skills(self, value):
        self.data_content['skills'] = value

    @property
    def experiences(self):
        return self.data_content.get('experiences', [])
        
    @experiences.setter
    def experiences(self, value):
        self.data_content['experiences'] = value

    @property
    def education(self):
        return self.data_content.get('education', [])
        
    @education.setter
    def education(self, value):
        self.data_content['education'] = value
        
    @property
    def projects(self):
        return self.data_content.get('projects', [])
        
    @projects.setter
    def projects(self, value):
        self.data_content['projects'] = value
        
    @property
    def certifications(self):
        return self.data_content.get('certifications', [])
        
    @certifications.setter
    def certifications(self, value):
        self.data_content['certifications'] = value

    class Meta:
        db_table = 'user_profiles'
        indexes = [
            GinIndex(fields=['data_content'], name='profile_data_gin', opclasses=['jsonb_path_ops']),
        ]


class JobProfileSnapshot(models.Model):
    """Stores a per-job profile snapshot when user chooses to limit chatbot changes to a single application."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='job_snapshots')
    job = models.OneToOneField('jobs.Job', on_delete=models.CASCADE, related_name='profile_snapshot')

    # Snapshot of data_content at the moment the chatbot updated the profile for THIS job
    data_content = models.JSONField(default=dict)

    # The pre-chatbot state — used to revert the master profile when user chose "this job only"
    pre_chatbot_data = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'job_profile_snapshots'


class OutreachCampaign(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('paused', 'Paused'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='outreach_campaigns')
    job = models.ForeignKey('jobs.Job', on_delete=models.CASCADE, related_name='outreach_campaigns')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    daily_invite_cap = models.PositiveSmallIntegerField(default=15)

    # Cached per-status counts of OutreachAction children, refreshed on
    # every state transition. Lets the status panel render in O(1) instead
    # of doing a COUNT(*) GROUP BY status on every poll. Shape mirrors
    # OutreachAction.STATUS_CHOICES keys: queued/in_flight/sent/accepted/
    # failed/skipped/total. Optional `reason_finished` records why a
    # campaign settled (e.g. 'all_sent', 'all_failed', 'user_paused')
    # so the UI can explain the dot color without a click-through.
    summary_stats = models.JSONField(default=dict, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'outreach_campaigns'
        ordering = ['-created_at']


class DiscoveredTarget(models.Model):
    """A LinkedIn profile the paired Chrome extension scraped from a logged-in
    LinkedIn job page on the user's behalf. Survives until the user explicitly
    discards it or it ages out via cleanup. NOT the same as OutreachAction —
    these are *candidate* targets the user hasn't queued yet.
    """
    SOURCE_CHOICES = [
        ('hiring_team', 'Meet the hiring team'),
        ('people_you_know', 'People you can reach out to'),
        ('company_people', 'Company employees'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='discovered_targets')
    job = models.ForeignKey('jobs.Job', on_delete=models.CASCADE, related_name='discovered_targets')
    handle = models.CharField(max_length=128)
    name = models.CharField(max_length=128, blank=True)
    role = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'discovered_targets'
        ordering = ['-discovered_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'job', 'handle'],
                name='unique_discovered_target_per_user_job',
            ),
        ]


class OutreachAction(models.Model):
    KIND_CHOICES = [
        ('connect', 'Connect with note'),
        ('message', 'Direct message'),
    ]
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('in_flight', 'In flight'),
        ('sent', 'Sent'),
        ('accepted', 'Accepted'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(OutreachCampaign, on_delete=models.CASCADE, related_name='actions')
    target_handle = models.CharField(max_length=128)
    target_name = models.CharField(max_length=128, blank=True)
    target_role = models.CharField(max_length=128, blank=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    payload = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='queued')
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'outreach_actions'
        ordering = ['queued_at']
        indexes = [
            models.Index(fields=['campaign', 'status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['campaign', 'target_handle', 'kind'],
                name='unique_action_per_target_per_campaign',
            ),
        ]


class OutreachActionEvent(models.Model):
    """Audit-trail row for OutreachAction state transitions.

    Each row captures one transition (e.g. queued → in_flight, in_flight →
    failed). Append-only, never updated. Lets us answer questions like
    "why did this action fail at 14:32?" or "how often does this action
    bounce between queued and failed?" without losing history when the
    action is retried.

    Kept lightweight on purpose — no LLM payload, no per-attempt detail.
    Just the bare facts: when, what changed, what error (if any), what
    actor caused it (extension, server-side retry, stale-recovery sweep).
    """
    ACTOR_CHOICES = [
        ('extension', 'Extension'),
        ('user', 'User'),               # manual retry / pause / resume
        ('server_dispatch', 'Dispatcher'),  # claim / requeue
        ('server_recovery', 'Stale-recovery sweep'),
        ('server_finish', 'Campaign finish'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.ForeignKey(OutreachAction, on_delete=models.CASCADE, related_name='events')
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16)
    actor = models.CharField(max_length=24, choices=ACTOR_CHOICES, default='server_dispatch')
    reason = models.CharField(max_length=64, blank=True)
    detail = models.TextField(blank=True)
    attempts_after = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'outreach_action_events'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', 'created_at']),
        ]

