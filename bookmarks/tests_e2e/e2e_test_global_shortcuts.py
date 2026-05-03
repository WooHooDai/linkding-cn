from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class GlobalShortcutsE2ETestCase(LinkdingE2ETestCase):
    def test_focus_search(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:bookmarks.index"))
            page.wait_for_load_state("networkidle")

            page.press("body", "s")

            search_input = page.get_by_placeholder("Search for words or #tags")
            search_input.wait_for(state="visible")
            expect(search_input).to_be_focused()

            browser.close()

    def test_add_bookmark(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:bookmarks.index"))
            page.wait_for_load_state("networkidle")

            page.press("body", "n")

            expect(page).to_have_url(
                self.live_server_url + reverse("linkding:bookmarks.new")
            )

            browser.close()
