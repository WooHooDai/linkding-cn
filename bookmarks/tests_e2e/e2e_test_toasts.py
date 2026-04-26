from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.models import Toast
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class ToastsE2ETestCase(LinkdingE2ETestCase):
    def test_acknowledge_toast_removes_it_from_page(self):
        Toast.objects.create(
            owner=self.get_or_create_test_user(),
            key="test",
            message="Toast visible in header",
        )

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.index"), p)

            toast = page.locator(".message-list .toast").filter(
                has_text="Toast visible in header"
            )
            expect(toast).to_have_count(1)

            toast.locator("button[type='submit']").click()

            expect(page.locator(".message-list .toast")).to_have_count(0)
