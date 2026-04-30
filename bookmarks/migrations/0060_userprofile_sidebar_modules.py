from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0059_userprofile_language"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="sidebar_modules",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
