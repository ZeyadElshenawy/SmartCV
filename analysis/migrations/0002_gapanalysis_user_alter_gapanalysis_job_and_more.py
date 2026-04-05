# Generated manually to bypass Supabase PgBouncer Makemigrations hangs

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('jobs', '0001_initial'),
        ('analysis', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='gapanalysis',
            name='user',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='gap_analyses', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='gapanalysis',
            name='job',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='gap_analyses', to='jobs.job'),
        ),
        migrations.AlterUniqueTogether(
            name='gapanalysis',
            unique_together={('job', 'user')},
        ),
    ]
