from django.db import migrations


def migrate_tokens_forward(apps, schema_editor):
    token_model = apps.get_model("authtoken", "Token")
    api_token_model = apps.get_model("bookmarks", "ApiToken")

    for old_token in token_model.objects.all():
        api_token_model.objects.create(
            key=old_token.key,
            user=old_token.user,
            name="Default Token",
            created=old_token.created,
        )


def migrate_tokens_reverse(apps, schema_editor):
    api_token_model = apps.get_model("bookmarks", "ApiToken")
    api_token_model.objects.filter(name="Default Token").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("bookmarks", "0062_apitoken"),
        ("authtoken", "0004_alter_tokenproxy_options"),
    ]

    operations = [
        migrations.RunPython(migrate_tokens_forward, migrate_tokens_reverse),
    ]
