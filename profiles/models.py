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

