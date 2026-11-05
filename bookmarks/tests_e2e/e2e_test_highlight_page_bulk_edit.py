from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.models import Annotation
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class HighlightBulkEditE2ETestCase(LinkdingE2ETestCase):
    def setup_test_data(self, count=25):
        bookmark = self.setup_bookmark()
        for index in range(count):
            Annotation.objects.create(
                bookmark=bookmark,
                selector={"type": "TextQuoteSelector", "exact": f"highlight {index}"},
                selected_text=f"highlight {index}",
                color=Annotation.COLOR_YELLOW,
            )

        self.user.profile.highlights_per_page = 10
        self.user.profile.save(update_fields=["highlights_per_page"])

    def test_bulk_delete_across_pages(self):
        self.setup_test_data()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.highlights"), p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()
            expect(self.locate_bulk_edit_bar().locator(".bulk-edit-count")).to_have_text(
                "Selected(25)"
            )

            self.select_bulk_action("bulk_delete")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            expect(self.page.locator("li.highlight-item")).to_have_count(0)

        self.assertEqual(0, Annotation.objects.count())

    def test_selection_survives_pagination(self):
        self.setup_test_data()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.highlights"), p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            self.page.goto(self.live_server_url + reverse("linkding:bookmarks.highlights") + "?page=2")

            # 下一页仍在批量模式，且沿用「全站」选区
            expect(self.page.locator(".highlights-page.active")).to_have_count(1)
            expect(self.locate_bulk_edit_bar().locator(".bulk-edit-count")).to_have_text(
                "Selected(25)"
            )
            expect(self.page.locator("li.highlight-item .bulk-edit-checkbox").first).to_be_visible()
