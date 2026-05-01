from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class SettingsIntegrationsE2ETestCase(LinkdingE2ETestCase):
    def test_create_api_token_modal_is_interactive(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:settings.integrations"), p)

            page.get_by_role("link", name="Create API token").click()

            modal = page.locator("turbo-frame#api-modal ld-modal")
            expect(modal).to_be_visible()

            token_name = modal.locator("input[name='name']")
            token_name.fill("Playwright Token")
            expect(token_name).to_have_value("Playwright Token")

            modal.get_by_role("link", name="Cancel").click()
            expect(modal).to_be_hidden()

    def test_create_api_token_closes_modal_and_shows_new_token(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:settings.integrations"), p)

            page.get_by_role("link", name="Create API token").click()

            modal = page.locator("turbo-frame#api-modal ld-modal")
            expect(modal).to_be_visible()

            modal.locator("input[name='name']").fill("Playwright Token")
            modal.get_by_role("button", name="Create token").click()

            expect(modal).to_be_hidden()
            expect(page.locator("#new-token-key")).to_be_visible()

    def test_should_toggle_bookmarklet_variants(self):
        with sync_playwright() as p:
            page = self.open(reverse("linkding:settings.integrations"), p)

            server_bookmarklet = page.locator("#bookmarklet-server")
            client_bookmarklet = page.locator("#bookmarklet-client")

            expect(server_bookmarklet).to_be_visible()
            expect(client_bookmarklet).to_be_hidden()

            page.get_by_label("Detect title and description in the browser").check()

            expect(server_bookmarklet).to_be_hidden()
            expect(client_bookmarklet).to_be_visible()
