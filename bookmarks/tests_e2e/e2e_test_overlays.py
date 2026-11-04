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
            # 「布局」现在是左侧抽屉，弹层菜单改为验证搜索选项下拉
            page.locator(".search-options .dropdown-toggle").click()
            overlay = page.locator(".search-options .menu")
            expect(overlay).to_be_visible()

            self.assert_overlay_above_pagination(page, overlay)
            browser.close()

    def test_layout_drawer_opens_and_refreshes_list_without_reload(self):
        with sync_playwright() as p:
            browser, page = self.open_bookmarks_page(p)
            page.locator("[data-layout-drawer-toggle]").click()
            drawer = page.locator("#layout-drawer")
            expect(drawer).to_be_visible()

            # 切换视图走片段刷新：页面不重载，列表 DOM 换掉
            page.locator('.ld-segment-option:has-text("Compact")').click()
            expect(page.locator("#bookmark-list-container ul.view-compact")).to_be_visible()

            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            browser.close()
