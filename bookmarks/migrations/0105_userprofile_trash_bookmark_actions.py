from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0104_userprofile_highlights_author_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="trash_bookmark_actions",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
