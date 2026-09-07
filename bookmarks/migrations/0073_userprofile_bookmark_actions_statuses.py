from django.db import migrations, models


def populate_bookmark_actions(apps, schema_editor):
    UserProfile = apps.get_model("bookmarks", "UserProfile")
    ACTION_KEYS = ["read", "view", "edit", "archive", "remove", "unread", "share"]
    FIELD_MAP = {
        "read": "display_read_bookmark_action",
        "view": "display_view_bookmark_action",
        "edit": "display_edit_bookmark_action",
        "archive": "display_archive_bookmark_action",
        "remove": "display_remove_bookmark_action",
    }
    for profile in UserProfile.objects.all():
        profile.bookmark_actions = [
            {"key": key, "enabled": getattr(profile, FIELD_MAP.get(key, ""), True)}
            for key in ACTION_KEYS
        ]
        profile.save(update_fields=["bookmark_actions"])


def drop_show_bookmark_actions_if_exists(apps, schema_editor):
    """Drop the show_bookmark_actions column if it exists (may not exist on fresh installs)."""
    with schema_editor.connection.cursor() as cursor:
        columns = [
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, "bookmarks_userprofile"
            )
        ]
        if "show_bookmark_actions" in columns:
            cursor.execute("ALTER TABLE bookmarks_userprofile DROP COLUMN show_bookmark_actions")


class Migration(migrations.Migration):

    dependencies = [
        ("bookmarks", "0072_userprofile_display_read_bookmark_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="bookmark_actions",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="bookmark_action_display_mode",
            field=models.CharField(
                choices=[("text", "Text"), ("icon", "Icon")],
                default="text",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="bookmark_statuses",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(populate_bookmark_actions, migrations.RunPython.noop),
        migrations.RunPython(drop_show_bookmark_actions_if_exists, migrations.RunPython.noop),
    ]
