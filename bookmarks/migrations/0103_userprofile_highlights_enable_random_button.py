from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0102_userprofile_shortcut_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="highlights_enable_random_button",
            field=models.BooleanField(default=True),
        ),
    ]
