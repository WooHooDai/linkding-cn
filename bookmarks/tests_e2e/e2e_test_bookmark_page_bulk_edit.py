from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.models import Bookmark
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class BookmarkPagePartialUpdatesE2ETestCase(LinkdingE2ETestCase):
    def setup_test_data(self):
        self.setup_numbered_bookmarks(50)
        self.setup_numbered_bookmarks(50, archived=True)
        self.setup_numbered_bookmarks(50, prefix="foo")
        self.setup_numbered_bookmarks(50, archived=True, prefix="foo")

        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=False, title__startswith="Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=True, title__startswith="Archived Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(is_archived=False, title__startswith="foo").count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(is_archived=True, title__startswith="foo").count(),
        )

    def test_active_bookmarks_bulk_select_across(self):
        self.setup_test_data()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)

            bookmark_list = self.locate_bookmark_list()
            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            self.select_bulk_action("Move to trash")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            # Wait until bookmark list is updated (old reference becomes invisible)
            expect(bookmark_list).not_to_be_visible()

        self.assertEqual(
            0,
            Bookmark.objects.filter(
                is_archived=False, is_deleted=False, title__startswith="Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=True, title__startswith="Archived Bookmark"
            ).count(),
        )
        self.assertEqual(
            0,
            Bookmark.objects.filter(is_archived=False, is_deleted=False, title__startswith="foo").count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(is_archived=True, title__startswith="foo").count(),
        )

    def test_archived_bookmarks_bulk_select_across(self):
        self.setup_test_data()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.archived"), p)

            bookmark_list = self.locate_bookmark_list()
            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            self.select_bulk_action("Delete permanently")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            # Wait until bookmark list is updated (old reference becomes invisible)
            expect(bookmark_list).not_to_be_visible()

        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=False, title__startswith="Bookmark"
            ).count(),
        )
        self.assertEqual(
            0,
            Bookmark.objects.filter(
                is_archived=True, title__startswith="Archived Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(is_archived=False, title__startswith="foo").count(),
        )
        self.assertEqual(
            0,
            Bookmark.objects.filter(is_archived=True, title__startswith="foo").count(),
        )

    def test_active_bookmarks_bulk_select_across_respects_query(self):
        self.setup_test_data()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index") + "?q=foo", p)

            bookmark_list = self.locate_bookmark_list()
            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            self.select_bulk_action("Move to trash")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            # Wait until bookmark list is updated (old reference becomes invisible)
            expect(bookmark_list).not_to_be_visible()

        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=False, is_deleted=False, title__startswith="Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=True, title__startswith="Archived Bookmark"
            ).count(),
        )
        self.assertEqual(
            0,
            Bookmark.objects.filter(is_archived=False, is_deleted=False, title__startswith="foo").count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(is_archived=True, title__startswith="foo").count(),
        )

    def test_archived_bookmarks_bulk_select_across_respects_query(self):
        self.setup_test_data()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.archived") + "?q=foo", p)

            bookmark_list = self.locate_bookmark_list()
            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            self.select_bulk_action("Delete permanently")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            # Wait until bookmark list is updated (old reference becomes invisible)
            expect(bookmark_list).not_to_be_visible()

        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=False, title__startswith="Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(
                is_archived=True, title__startswith="Archived Bookmark"
            ).count(),
        )
        self.assertEqual(
            50,
            Bookmark.objects.filter(is_archived=False, title__startswith="foo").count(),
        )
        self.assertEqual(
            0,
            Bookmark.objects.filter(is_archived=True, title__startswith="foo").count(),
        )

    def test_select_all_toggles_all_checkboxes(self):
        self.setup_numbered_bookmarks(5)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()

            checkboxes = page.locator("label.bulk-edit-checkbox input")
            self.assertEqual(6, checkboxes.count())
            for i in range(checkboxes.count()):
                expect(checkboxes.nth(i)).not_to_be_checked()

            self.locate_bulk_edit_select_all().click()

            for i in range(checkboxes.count()):
                expect(checkboxes.nth(i)).to_be_checked()

            self.locate_bulk_edit_select_all().click()

            for i in range(checkboxes.count()):
                expect(checkboxes.nth(i)).not_to_be_checked()

    def test_select_all_shows_select_across(self):
        # The "All pages" checkbox is always visible while bulk editing
        self.setup_numbered_bookmarks(5)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            self.open(url, p)

            self.locate_bulk_edit_toggle().click()

            expect(self.locate_bulk_edit_select_across()).to_be_visible()
            expect(self.locate_bulk_edit_select_across()).not_to_be_checked()

            # Toggling the header only affects the current page, not select-across
            self.locate_bulk_edit_select_all().click()
            expect(self.locate_bulk_edit_select_across()).not_to_be_checked()

    def test_select_all_excluded_page_keeps_select_across_checked(self):
        # In select-all mode, unchecking the header excludes the current page.
        # Select-across stays checked because we are still selecting every
        # bookmark except that page.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            self.open(url, p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()

            # Uncheck the header: current page (30) is excluded
            self.locate_bulk_edit_select_all().click()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(70)")

            # Re-check the header: the page is selected again
            self.locate_bulk_edit_select_all().click()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(100)")

    def test_select_all_excluded_bookmark_keeps_select_across_checked(self):
        # In select-all mode, unchecking a single bookmark records it as an
        # exclusion; select-across stays checked and the count drops by one.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            self.open(url, p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()

            # Uncheck one bookmark
            self.locate_bookmark("Bookmark 1").locator(
                "label.bulk-edit-checkbox"
            ).click()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(99)")

            # Re-check it
            self.locate_bookmark("Bookmark 1").locator(
                "label.bulk-edit-checkbox"
            ).click()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(100)")

    def test_execute_resets_all_checkboxes(self):
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            # Select all bookmarks, enable select across
            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            # Execute bulk action
            self.select_bulk_action("Mark as unread")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()

            # Wait for turbo stream to update the page (checkboxes get reset)
            page.wait_for_timeout(1000)

            # Verify bulk edit checkboxes are reset
            checkboxes = page.locator("label.bulk-edit-checkbox input")
            self.assertEqual(31, checkboxes.count())
            for i in range(checkboxes.count()):
                expect(checkboxes.nth(i)).not_to_be_checked()

            # Toggle select all and verify select across is reset
            self.locate_bulk_edit_select_all().click()
            expect(self.locate_bulk_edit_select_across()).not_to_be_checked()

    def test_update_select_across_bookmark_count(self):
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()

            expect(
                self.locate_bulk_edit_bar().get_by_text("All pages (100 bookmarks)")
            ).to_be_visible()

            self.select_bulk_action("Move to trash")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            # Wait for turbo stream to update the page
            page.wait_for_timeout(1000)

            expect(self.locate_bulk_edit_select_all()).not_to_be_checked()
            self.locate_bulk_edit_select_all().click()

            expect(
                self.locate_bulk_edit_bar().get_by_text("All pages (70 bookmarks)")
            ).to_be_visible()

    def test_header_checkbox_selects_only_current_page(self):
        # The header checkbox only selects/deselects bookmarks on the current
        # page, and the selected count reflects the current page, not the
        # total number of bookmarks.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()

            bookmark_checkboxes = page.locator(
                "ul.bookmark-list input[name='bookmark_id']"
            )
            page_size = bookmark_checkboxes.count()
            self.assertEqual(30, page_size)

            self.locate_bulk_edit_select_all().click()

            for i in range(page_size):
                expect(bookmark_checkboxes.nth(i)).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text(f"Selected({page_size})")
            expect(
                self.locate_bulk_edit_select_across()
            ).not_to_be_checked()

            self.locate_bulk_edit_select_all().click()

            for i in range(page_size):
                expect(bookmark_checkboxes.nth(i)).not_to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).not_to_be_visible()

    def test_header_checkbox_selects_only_current_page_after_navigation(self):
        # Checking the header checkbox on one page must not select bookmarks
        # on other pages. Navigating to another page keeps earlier selections
        # while the header checkbox still acts on the page currently shown.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()

            # Select all bookmarks on page 1
            self.locate_bulk_edit_select_all().click()

            # Navigate to page 2: page 1 selection persists, page 2 is not
            # selected and the count still shows the page 1 selection only
            page.locator(".pagination-list li.page-item a", has_text="2").click()
            page.wait_for_timeout(1000)

            page2_checkboxes = page.locator(
                "ul.bookmark-list input[name='bookmark_id']"
            )
            self.assertEqual(30, page2_checkboxes.count())
            for i in range(page2_checkboxes.count()):
                expect(page2_checkboxes.nth(i)).not_to_be_checked()
            expect(self.locate_bulk_edit_select_all()).not_to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(30)")

            # Select all bookmarks on page 2 as well
            self.locate_bulk_edit_select_all().click()

            for i in range(page2_checkboxes.count()):
                expect(page2_checkboxes.nth(i)).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(60)")

            # Deselect page 2 again, page 1 selection remains
            self.locate_bulk_edit_select_all().click()

            for i in range(page2_checkboxes.count()):
                expect(page2_checkboxes.nth(i)).not_to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(30)")

    def test_select_across_selects_all_and_deselects_all(self):
        # The "All pages" checkbox selects all bookmarks when any bookmark is
        # not selected yet, and deselects all bookmarks when all are selected.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()

            # Select the current page, then extend the selection to all pages
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(100)")

            # Deselect all bookmarks again
            self.locate_bulk_edit_select_across().click()

            expect(self.locate_bulk_edit_select_across()).not_to_be_checked()
            page_checkboxes = page.locator(
                "ul.bookmark-list input[name='bookmark_id']"
            )
            for i in range(page_checkboxes.count()):
                expect(page_checkboxes.nth(i)).not_to_be_checked()
            expect(self.locate_bulk_edit_select_all()).not_to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).not_to_be_visible()


    def test_select_all_with_excluded_bookmark_does_not_act_on_it(self):
        # Select all, then uncheck one bookmark. The bulk action must apply
        # to every bookmark except the excluded one.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            # Exclude Bookmark 1
            self.locate_bookmark("Bookmark 1").locator(
                "label.bulk-edit-checkbox"
            ).click()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(99)")

            # Move all selected bookmarks to trash
            self.select_bulk_action("Move to trash")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            page.wait_for_timeout(1000)

            # Bookmark 1 must still be present
            self.assertTrue(self.locate_bookmark("Bookmark 1").is_visible())
            # The other 99 bookmarks are gone from the active list
            bookmark_list = page.locator("ul.bookmark-list")
            expect(bookmark_list).not_to_have_count(0)
            self.assertFalse(
                self.locate_bookmark("Bookmark 2").is_visible()
            )
