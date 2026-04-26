from django.contrib.staticfiles.testing import LiveServerTestCase
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests.helpers import BookmarkFactoryMixin


class LoginFormE2ETestCase(LiveServerTestCase, BookmarkFactoryMixin):
    def test_successful_login_navigates_to_bookmark_index(self):
        user = self.setup_user(name="login-user")
        user.set_password("password123")
        user.save()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("login"))

            page.get_by_label("Username").fill("login-user")
            page.get_by_label("Password").fill("password123")
            page.locator("input[type='submit']").click()

            expect(page).to_have_url(
                self.live_server_url + reverse("linkding:bookmarks.index")
            )

            browser.close()
