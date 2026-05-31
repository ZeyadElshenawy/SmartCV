# Generated for fix #1 — content stickiness (audit §6.5, 2026-05-30).
# Adds the previous_best JSONField on GeneratedResume. Empty default so
# every existing row is automatically "no prior export yet" — no data
# migration needed.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('resumes', '0003_add_validation_report'),
    ]

    operations = [
        migrations.AddField(
            model_name='generatedresume',
            name='previous_best',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
