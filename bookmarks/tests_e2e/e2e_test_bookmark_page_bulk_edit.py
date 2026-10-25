from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
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

    def test_execute_keeps_selection_for_non_removing_action(self):
        # Non-removing actions keep the selection for the next operation.
        self.setup_numbered_bookmarks(100)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()
            self.locate_bulk_edit_select_across().click()

            self.select_bulk_action("Mark as unread")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            page.wait_for_timeout(1000)

            # Selection preserved after the action
            checkboxes = page.locator("label.bulk-edit-checkbox input")
            self.assertEqual(31, checkboxes.count())
            for i in range(checkboxes.count()):
                expect(checkboxes.nth(i)).to_be_checked()
            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(100)")
            expect(
                self.locate_bulk_edit_bar().locator(
                    'button[name="bulk_execute"]'
                )
            ).to_be_enabled()

            # A second action applies to the same batch
            self.select_bulk_action("Mark as read")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            page.wait_for_timeout(1000)

            expect(self.locate_bulk_edit_select_across()).to_be_checked()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(100)")

        self.assertEqual(0, Bookmark.objects.filter(unread=True).count())

    def test_execute_clears_selection_for_removing_action(self):
        # Removing actions clear the selection.
        self.setup_numbered_bookmarks(30)

        with sync_playwright() as p:
            url = reverse("linkding:bookmarks.index")
            page = self.open(url, p)

            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()

            self.select_bulk_action("Move to trash")
            self.locate_bulk_edit_bar().get_by_text("Execute").click()
            self.page.get_by_text("Confirm").click()
            page.wait_for_timeout(1000)

            checkboxes = page.locator("label.bulk-edit-checkbox input")
            for i in range(checkboxes.count()):
                expect(checkboxes.nth(i)).not_to_be_checked()
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


class BulkEditSelectionE2ETestCase(LinkdingE2ETestCase):
    def setup_grouped_bookmarks(self):
        now = timezone.now()
        for i in range(1, 4):
            self.setup_bookmark(
                title=f"Today Bookmark {i}", added=now - timedelta(minutes=i)
            )
        for i in range(1, 3):
            self.setup_bookmark(
                title=f"Older Bookmark {i}", added=now - timedelta(days=40, minutes=i)
            )

    def item_checkboxes(self):
        return self.page.locator(
            "ul.bookmark-list li[ld-bookmark-item] label.bulk-edit-checkbox"
        )

    def group_checkboxes(self):
        return self.page.locator("li.group-header label.bulk-group-checkbox")

    def checked_inputs(self):
        return self.page.locator("ul.bookmark-list input[name='bookmark_id']:checked")

    def checked_count(self):
        return self.checked_inputs().count()

    def test_group_checkbox_is_hidden_outside_bulk_mode(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)

            expect(self.group_checkboxes()).to_have_count(2)
            expect(self.group_checkboxes().first).not_to_be_visible()

            self.locate_bulk_edit_toggle().click()

            expect(self.group_checkboxes().first).to_be_visible()

    def test_group_checkbox_selects_only_its_group(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()

            # First group holds the three bookmarks added today
            self.group_checkboxes().first.click()
            self.assertEqual(3, self.checked_count())
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(3)")

            # Clearing the group leaves nothing selected
            self.group_checkboxes().first.click()
            self.assertEqual(0, self.checked_count())
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).not_to_be_visible()

    def test_group_checkbox_reflects_partial_selection(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()

            group_input = self.page.locator(
                "li.group-header label.bulk-group-checkbox input"
            ).first
            self.assertFalse(group_input.evaluate("el => el.indeterminate"))

            self.item_checkboxes().first.click()

            self.assertTrue(group_input.evaluate("el => el.indeterminate"))
            self.assertFalse(group_input.evaluate("el => el.checked"))

            # Selecting the remaining two bookmarks completes the group
            self.item_checkboxes().nth(1).click()
            self.item_checkboxes().nth(2).click()

            self.assertTrue(group_input.evaluate("el => el.checked"))
            self.assertFalse(group_input.evaluate("el => el.indeterminate"))

    def test_shift_click_selects_range_across_groups(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()

            checkboxes = self.item_checkboxes()
            expect(checkboxes).to_have_count(5)

            # Anchor on the first bookmark, then extend over the group boundary
            checkboxes.nth(0).click()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(1)")

            self.page.keyboard.down("Shift")
            checkboxes.nth(3).click()
            self.page.keyboard.up("Shift")

            self.assertEqual(4, self.checked_count())
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(4)")

    def test_shift_click_clears_range(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()

            checkboxes = self.item_checkboxes()
            self.locate_bulk_edit_select_all().click()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(5)")

            # Anchor on the third bookmark, shift-click the first one: the
            # range 1-3 is cleared, 4-5 stay selected
            checkboxes.nth(2).click()
            self.page.keyboard.down("Shift")
            checkboxes.nth(0).click()
            self.page.keyboard.up("Shift")

            self.assertEqual(2, self.checked_count())
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(2)")

    def test_long_press_arms_range_for_touch(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()

            checkboxes = self.item_checkboxes()
            hint = self.locate_bulk_edit_bar().locator(".selection-hint")
            expect(hint).not_to_be_visible()

            checkboxes.nth(0).dispatch_event(
                "pointerdown",
                {
                    "pointerType": "touch",
                    "clientX": 10,
                    "clientY": 10,
                    "bubbles": True,
                },
            )

            expect(hint).to_be_visible()
            expect(hint).to_contain_text("Anchor set")
            # The long press toggles the anchor itself, showing the direction
            expect(self.checked_inputs()).to_have_count(1)

            checkboxes.nth(3).click()

            expect(self.checked_inputs()).to_have_count(4)
            expect(hint).not_to_be_visible()
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(4)")

    def test_long_press_with_mouse_arms_range(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()

            checkboxes = self.item_checkboxes()
            hint = self.locate_bulk_edit_bar().locator(".selection-hint")

            # Hold the left button on the first bookmark instead of using shift
            box = checkboxes.nth(0).bounding_box()
            self.page.mouse.move(
                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
            self.page.mouse.down()

            expect(hint).to_be_visible()
            self.page.mouse.up()

            checkboxes.nth(3).click()

            expect(self.checked_inputs()).to_have_count(4)
            expect(hint).not_to_be_visible()

    def test_long_press_on_selected_bookmark_clears_range(self):
        self.setup_grouped_bookmarks()

        with sync_playwright() as p:
            self.open(reverse("linkding:bookmarks.index"), p)
            self.locate_bulk_edit_toggle().click()
            self.locate_bulk_edit_select_all().click()

            checkboxes = self.item_checkboxes()
            hint = self.locate_bulk_edit_bar().locator(".selection-hint")

            checkboxes.nth(3).dispatch_event(
                "pointerdown",
                {
                    "pointerType": "touch",
                    "clientX": 10,
                    "clientY": 10,
                    "bubbles": True,
                },
            )

            expect(hint).to_contain_text("clear the range")
            # The pressed anchor is cleared right away
            expect(self.checked_inputs()).to_have_count(4)

            checkboxes.nth(0).click()

            # Range 1-4 cleared, bookmark 5 keeps its selection
            expect(self.checked_inputs()).to_have_count(1)
            expect(
                self.locate_bulk_edit_bar().locator(".bulk-edit-count")
            ).to_have_text("Selected(1)")
