from django.db import migrations

def migrate_data(apps, schema_editor):
    UserProfile = apps.get_model('profiles', 'UserProfile')
    for profile in UserProfile.objects.all():
        # Construct data_content from existing fields
        # Note: In a migration, we can't use the property setters we just defined on the model class
        # because the migration uses an older/historical version of the model.
        # We must access the data directly.
        # BUT, the historical model has the old fields (skills, experiences, etc) AND the new fields (data_content).
        
        data = profile.data_content or {}
        
        # We access the OLD fields
        if hasattr(profile, 'skills') and profile.skills:
            data['skills'] = profile.skills
        if hasattr(profile, 'experiences') and profile.experiences:
            data['experiences'] = profile.experiences
        if hasattr(profile, 'education') and profile.education:
            data['education'] = profile.education
        if hasattr(profile, 'projects') and profile.projects:
            data['projects'] = profile.projects
        if hasattr(profile, 'certifications') and profile.certifications:
            data['certifications'] = profile.certifications
            
        profile.data_content = data
        profile.save()

class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0005_remove_userprofile_certifications_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_data),
    ]
