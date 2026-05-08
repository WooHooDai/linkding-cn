from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

RENAMES = [
    ("0049_userprofile_legacy_search", "0062_userprofile_legacy_search"),
    ("0050_new_search_toast", "0063_new_search_toast"),
    ("0062_apitoken", "0064_apitoken"),
    ("0063_migrate_api_tokens", "0065_migrate_api_tokens"),
    ("0064_deduplicate_bookmarks", "0066_deduplicate_bookmarks"),
    ("0065_userprofile_summary_domain_prefs", "0067_userprofile_summary_domain_prefs"),
]


class Command(BaseCommand):
    help = "Rename v1.0.4 migration entries to match renumbered files for v1.0.5+ upgrade."

    def handle(self, **options):
        with connection.cursor() as cursor:
            try:
                for old, new in RENAMES:
                    cursor.execute(
                        "UPDATE django_migrations SET name = %s WHERE app = %s AND name = %s",
                        [new, "bookmarks", old],
                    )
                    if cursor.rowcount > 0:
                        self.stdout.write(f"Renamed migration: {old} → {new}")
            except DatabaseError:
                self.stdout.write("django_migrations table not found, skipping rename.")
