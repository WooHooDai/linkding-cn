from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0060_userprofile_sidebar_modules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userprofile",
            name="language",
            field=models.CharField(default="en", max_length=20),
        ),
    ]
