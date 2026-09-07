from importlib import import_module

from django.db import connection
from django.test import TransactionTestCase

from bookmarks.tests.helpers import BookmarkFactoryMixin


class LegacyProfileMigrationTest(TransactionTestCase, BookmarkFactoryMixin):
    def test_removing_legacy_column_preserves_profiles_and_is_repeatable(self):
        profile = self.get_or_create_test_user().profile
        migration = import_module(
            "bookmarks.migrations.0073_userprofile_bookmark_actions_statuses"
        )
        with connection.schema_editor() as editor:
            editor.execute(
                "ALTER TABLE bookmarks_userprofile ADD COLUMN show_bookmark_actions boolean"
            )
            migration.drop_show_bookmark_actions_if_exists(None, editor)
            migration.drop_show_bookmark_actions_if_exists(None, editor)
        profile.refresh_from_db()
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(
                cursor, "bookmarks_userprofile"
            )
        self.assertNotIn("show_bookmark_actions", [column.name for column in columns])
