from io import StringIO

from django.core.management import call_command
from django.test import TransactionTestCase


class MigrationTestCase(TransactionTestCase):
    """Verify that all migrations can be applied and the latest can be reversed/re-applicable."""

    def test_all_migrations_apply(self):
        """All migrations should apply without error."""
        call_command("migrate", verbosity=0)

    def test_showmigrations_all_applied(self):
        """After migrate, showmigrations should show all applied."""
        call_command("migrate", verbosity=0)

        out = StringIO()
        call_command("showmigrations", "bookmarks", stdout=out, verbosity=1)
        output = out.getvalue()

        for line in output.strip().splitlines():
            line = line.strip()
            if line.startswith("["):
                self.assertIn("[X]", f"Migration not applied: {line}")

    def test_latest_bookmarks_migration_is_reversible(self):
        """The latest bookmarks migration should be reversible and re-applicable."""
        call_command("migrate", verbosity=0)

        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(connection=None)
        graph = loader.graph
        leaf_nodes = graph.leaf_nodes()
        bookmarks_leaves = [n for n in leaf_nodes if n[0] == "bookmarks"]
        self.assertTrue(bookmarks_leaves, "No bookmarks migrations found")
        latest = bookmarks_leaves[0]

        # Reverse to the migration before the latest
        app_label, migration_name = latest
        plan = graph.backwards_plan(latest)
        if len(plan) > 1:
            target = plan[1]
            call_command("migrate", app_label, target[1], verbosity=0)
        else:
            call_command("migrate", app_label, "zero", verbosity=0)

        # Re-apply all migrations
        call_command("migrate", app_label, verbosity=0)
