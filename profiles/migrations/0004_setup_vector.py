from django.db import migrations
from pgvector.django import VectorExtension

class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0003_userprofile_raw_cv_data'),
    ]

    operations = [
        VectorExtension(),
    ]
