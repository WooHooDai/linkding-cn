from pathlib import Path

from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FrontendModuleBoundaryTestCase(SimpleTestCase):
    def read_source(self, relative_path: str) -> str:
        return (PROJECT_ROOT / relative_path).read_text()

    def test_bookmark_page_imports_summary_preferences_not_summary_behavior(self):
        content = self.read_source("bookmarks/frontend/behaviors/bookmark-page.js")

        self.assertNotIn('from "./sidebar-user-summary"', content)
        self.assertRegex(
            content,
            r'from "\.\./state/(summary-preferences|page-preferences)"',
        )

    def test_sidebar_summary_behavior_does_not_import_domain_preferences(self):
        content = self.read_source(
            "bookmarks/frontend/behaviors/sidebar-user-summary.js"
        )

        self.assertNotIn('from "../state/domain-preferences"', content)

    def test_frontend_entry_imports_display_preference_registry(self):
        content = self.read_source("bookmarks/frontend/index.js")

        self.assertIn('import "./state/registry";', content)

    def test_preference_modules_do_not_self_register_document_listeners(self):
        summary_content = self.read_source(
            "bookmarks/frontend/state/summary-preferences.js"
        )
        domain_content = self.read_source(
            "bookmarks/frontend/state/domain-preferences.js"
        )

        self.assertIn(
            "export function registerSummaryDisplayPreferences()", summary_content
        )
        self.assertIn(
            "export function registerDomainDisplayPreferences()", domain_content
        )
        self.assertNotIn(
            "registerSummaryDisplayPreferences();",
            summary_content,
        )
        self.assertNotIn(
            "registerDomainDisplayPreferences();",
            domain_content,
        )

    def test_filter_drawer_behavior_imports_drawer_state_from_state_directory(self):
        content = self.read_source("bookmarks/frontend/behaviors/filter-drawer.js")

        self.assertIn('from "../state/filter-drawer-state"', content)
