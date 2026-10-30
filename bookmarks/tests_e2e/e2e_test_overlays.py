from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class OverlayLayeringE2ETestCase(LinkdingE2ETestCase):
    def setUp(self) -> None:
        super().setUp()
        profile = self.get_or_create_test_user().profile
        profile.items_per_page = 10
        profile.sticky_header_controls = True
        profile.sticky_pagination = True
        profile.save()
        self.setup_numbered_bookmarks(12)
        for index in range(5):
            self.setup_tag(name=f"alpha-{index}")

    def open_bookmarks_page(self, playwright):
        browser = self.setup_browser(playwright)
        page = browser.new_page()
        page.set_viewport_size({"width": 1000, "height": 300})
        page.add_init_script(
            """
            localStorage.setItem("ld:search:history", JSON.stringify({
              recent: ["#alpha-search-1", "#alpha-search-2", "#alpha-search-3",
                       "#alpha-search-4", "#alpha-search-5"]
            }));
            """
        )
        page.goto(self.live_server_url + reverse("linkding:bookmarks.index"))
        page.wait_for_load_state("networkidle")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return browser, page

    def assert_overlay_above_pagination(self, page, overlay):
        result = page.evaluate(
            """
            (overlay) => {
              const pagination = document.querySelector(
                ".bookmark-pagination[data-sticky-on='true']"
              );
              const overlayRect = overlay.getBoundingClientRect();
              const paginationRect = pagination.getBoundingClientRect();
              const left = Math.max(overlayRect.left, paginationRect.left);
              const right = Math.min(overlayRect.right, paginationRect.right);
              const top = Math.max(overlayRect.top, paginationRect.top);
              const bottom = Math.min(overlayRect.bottom, paginationRect.bottom);
              if (left >= right || top >= bottom) {
                return { overlaps: false };
              }
              const hit = document.elementFromPoint(
                (left + right) / 2,
                (top + bottom) / 2
              );
              return {
                overlaps: true,
                popoverOpen: overlay.matches(":popover-open"),
                hitsOverlay: overlay.contains(hit),
              };
            }
            """,
            overlay.element_handle(),
        )
        self.assertTrue(result["overlaps"])
        self.assertTrue(result["popoverOpen"])
        self.assertTrue(result["hitsOverlay"])

    def test_search_autocomplete_is_above_sticky_pagination(self):
        with sync_playwright() as p:
            browser, page = self.open_bookmarks_page(p)
            search = page.get_by_placeholder("Search for words or #tags")
            search.fill("#a")
            overlay = page.locator("ld-search-autocomplete .menu")
            expect(overlay.locator(".menu-item")).to_have_count(12)
            expect(overlay).to_be_visible()

            self.assert_overlay_above_pagination(page, overlay)
            browser.close()

    def test_dropdown_is_above_sticky_pagination(self):
        with sync_playwright() as p:
            browser, page = self.open_bookmarks_page(p)
            page.get_by_role("button", name="Layout").click()
            overlay = page.locator(".header-layout .menu")
            expect(overlay).to_be_visible()

            self.assert_overlay_above_pagination(page, overlay)
            browser.close()
