from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0006_migrate_data'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='userprofile',
            name='certifications',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='education',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='experiences',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='projects',
        ),
        migrations.RemoveField(
            model_name='userprofile',
            name='skills',
        ),
    ]
