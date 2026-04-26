from django.urls import reverse
from playwright.sync_api import sync_playwright, expect

from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class FilterDrawerE2ETestCase(LinkdingE2ETestCase):
    def test_show_modal_close_modal(self):
        self.setup_bookmark(tags=[self.setup_tag(name="cooking")])
        self.setup_bookmark(tags=[self.setup_tag(name="hiking")])

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.index"), p)

            # use smaller viewport to make filter button visible
            page.set_viewport_size({"width": 375, "height": 812})

            # open drawer
            drawer_trigger = page.locator(".main").get_by_role("button", name="筛选")
            drawer_trigger.click()

            # verify drawer is visible
            drawer = page.locator(".modal.drawer.filter-drawer")
            expect(drawer).to_be_visible()
            expect(drawer.locator("h2")).to_have_text("筛选")

            # close with close button
            drawer.locator("button.close").click()
            expect(drawer).to_be_hidden()

            # open drawer again
            drawer_trigger.click()

            # close with backdrop
            backdrop = drawer.locator(".modal-overlay")
            backdrop.click(position={"x": 0, "y": 0})
            expect(drawer).to_be_hidden()

    def test_select_tag(self):
        self.setup_bookmark(tags=[self.setup_tag(name="cooking")])
        self.setup_bookmark(tags=[self.setup_tag(name="hiking")])

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.index"), p)

            # use smaller viewport to make filter button visible
            page.set_viewport_size({"width": 375, "height": 812})

            # open tag cloud modal
            drawer_trigger = page.locator(".main").get_by_role("button", name="筛选")
            drawer_trigger.click()

            # verify tags are displayed
            drawer = page.locator(".modal.drawer.filter-drawer")
            unselected_tags = drawer.locator(".unselected-tags")
            expect(unselected_tags.get_by_text("cooking")).to_be_visible()
            expect(unselected_tags.get_by_text("hiking")).to_be_visible()

            # select tag
            unselected_tags.get_by_text("cooking").click()

            # open drawer again
            drawer_trigger.click()

            # verify tag is selected, other tag is not visible anymore
            selected_tags = drawer.locator(".selected-tags")
            expect(selected_tags.get_by_text("cooking")).to_be_visible()

            expect(unselected_tags.get_by_text("cooking")).not_to_be_visible()
            expect(unselected_tags.get_by_text("hiking")).not_to_be_visible()

    def test_domain_compact_toggle_keeps_drawer_open_and_url_clean(self):
        for index in range(12):
            self.setup_bookmark(url=f"https://domain-{index}.example.com")

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.index"), p)

            page.set_viewport_size({"width": 375, "height": 812})

            drawer_trigger = page.locator("button[ld-filter-drawer-trigger]")
            drawer_trigger.click()

            drawer = page.locator(".modal.drawer.filter-drawer")
            expect(drawer).to_be_visible()
            expect(page.locator(".domain-menu")).to_have_attribute(
                "data-domain-compact-mode", "true"
            )

            domain_section = drawer.locator("section[aria-labelledby='domains-heading']")
            domain_section.locator("button.dropdown-toggle").click()
            expect(domain_section.locator(".menu-link").nth(1)).to_be_visible()
            compact_mode_debug = page.evaluate(
                """
                () => {
                  const links = document.querySelectorAll(
                    ".filter-drawer section[aria-labelledby='domains-heading'] .menu-link"
                  );
                  const link = links[1];
                  window.ldHandleDomainPreferenceClick({
                    currentTarget: link,
                    button: 0,
                    metaKey: false,
                    ctrlKey: false,
                    shiftKey: false,
                    altKey: false,
                    preventDefault() {},
                  });
                  return {
                    stored: window.localStorage.getItem("ld:domain-compact-mode"),
                  };
                }
                """
            )

            self.assertEqual(compact_mode_debug["stored"], "0")
            expect(drawer).to_be_visible()
            expect(page.locator(".domain-menu")).to_have_attribute(
                "data-domain-compact-mode", "false"
            )
            expect(page.locator(".domain-menu")).to_have_attribute(
                "data-domain-view-mode", "full"
            )
            self.assertNotIn("domain_compact=", page.url)
            self.assertNotIn("domain_view=", page.url)

    def test_domain_menu_labels_refresh_after_toggles_without_page_reload(self):
        for index in range(12):
            self.setup_bookmark(url=f"https://domain-{index}.example.com")

        with sync_playwright() as p:
            page = self.open(reverse("linkding:bookmarks.index"), p)

            page.set_viewport_size({"width": 375, "height": 812})

            page.locator("button[ld-filter-drawer-trigger]").click()
            drawer = page.locator(".modal.drawer.filter-drawer")
            expect(drawer).to_be_visible()

            domain_section = drawer.locator("section[aria-labelledby='domains-heading']")
            domain_section.locator("button.dropdown-toggle").click()
            menu_links = domain_section.locator(".menu-link")
            expect(menu_links.nth(0)).to_have_text("Icon mode")
            expect(menu_links.nth(1)).to_have_text("All domains")

            menu_links.nth(1).click()
            expect(drawer).to_be_visible()
            expect(page.locator(".domain-menu")).to_have_attribute(
                "data-domain-compact-mode", "false"
            )

            domain_section.locator("button.dropdown-toggle").click()
            expect(menu_links.nth(0)).to_have_text("Icon mode")
            expect(menu_links.nth(1)).to_have_text("Only important domains")

            menu_links.nth(0).click()
            expect(drawer).to_be_visible()
            expect(page.locator(".domain-menu")).to_have_attribute(
                "data-domain-view-mode", "icon"
            )

            domain_section.locator("button.dropdown-toggle").click()
            expect(menu_links.nth(0)).to_have_text("Full mode")
            expect(menu_links.nth(1)).to_have_text("Only important domains")
            self.assertReloads(0)

    def test_selected_parent_domain_keeps_prefix_before_icon_and_padding(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn"
        profile.save()

        self.setup_bookmark(url="https://docs.feishu.cn/123", title="hello docs")
        self.setup_bookmark(url="https://feishu.cn/blog", title="hello root")

        with sync_playwright() as p:
            page = self.open(
                reverse("linkding:bookmarks.index")
                + "?q=domain%3A%28feishu.cn+%7C+.feishu.cn%29",
                p,
            )

            selected_item = page.locator('li[data-domain-host="feishu.cn"]')
            expect(selected_item).to_be_visible()

            metrics = page.evaluate(
                """
                () => {
                  const selectedMain = document.querySelector(
                    'li[data-domain-host="feishu.cn"] [data-domain-primary] .domain-link-main'
                  );
                  const selectedLink = document.querySelector(
                    'li[data-domain-host="feishu.cn"] [data-domain-primary]'
                  );
                  const childClasses = Array.from(selectedMain.children).map((element) =>
                    element.classList.length ? element.classList[0] : null
                  );
                  const styles = window.getComputedStyle(selectedLink);

                  return {
                    childClasses,
                    paddingLeft: parseFloat(styles.paddingLeft),
                    paddingRight: parseFloat(styles.paddingRight),
                  };
                }
                """
            )

            self.assertEqual(metrics["childClasses"][0], "domain-selection-prefix")
            self.assertGreater(metrics["paddingLeft"], 0)
            self.assertGreater(metrics["paddingRight"], 0)
