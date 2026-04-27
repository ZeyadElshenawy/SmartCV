from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    outreach_token = models.UUIDField(null=True, blank=True, unique=True, db_index=True)
    # When the current outreach_token was issued. Surfaced in the pairing
    # UI so the user can decide whether the token is overdue for rotation —
    # the token has no expiration TTL by design (a long-running paired
    # extension shouldn't break overnight), so age is the only signal a
    # leaked / stale token might be in use.
    outreach_token_rotated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'

    def rotate_outreach_token(self) -> uuid.UUID:
        from django.utils import timezone
        self.outreach_token = uuid.uuid4()
        self.outreach_token_rotated_at = timezone.now()
        self.save(update_fields=['outreach_token', 'outreach_token_rotated_at', 'updated_at'])
        return self.outreach_token
