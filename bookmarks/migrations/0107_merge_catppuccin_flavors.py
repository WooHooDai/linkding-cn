from django.db import migrations

# 四个 flavor 曾各自独立成皮肤，现合并为 catppuccin（浅色 Latte / 深色 Mocha）
OLD_SKIN_IDS = (
    "catppuccin-latte",
    "catppuccin-frappe",
    "catppuccin-macchiato",
    "catppuccin-mocha",
)


def merge_flavors(apps, schema_editor):
    UserProfile = apps.get_model("bookmarks", "UserProfile")
    UserProfile.objects.filter(skin__in=OLD_SKIN_IDS).update(skin="catppuccin")


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0106_userprofile_skin"),
    ]

    operations = [
        migrations.RunPython(merge_flavors, migrations.RunPython.noop),
    ]
