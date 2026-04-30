from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from bookmarks.models import UserProfile
from bookmarks.tests_e2e.helpers import LinkdingE2ETestCase


class SettingsGeneralE2ETestCase(LinkdingE2ETestCase):
    def wait_for_settings_page_behavior(self, page):
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            """
            () => {
              const element = document.querySelector("[ld-settings-page]");
              return Boolean(element && element.__behaviors && element.__behaviors.length);
            }
            """
        )

    def test_should_toggle_default_sharing_visibility_from_sharing_mode(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:settings.general"))
            self.wait_for_settings_page_behavior(page)

            sharing_form = page.locator("[data-sharing-settings-form]")
            default_sharing_row = page.locator(
                "[data-setting-row='default_mark_shared']"
            )
            default_sharing = default_sharing_row.locator(
                "input[name='default_mark_shared']"
            )

            expect(default_sharing_row).to_be_hidden()
            expect(default_sharing).not_to_be_checked()

            sharing_form.get_by_label("Private sharing").check()
            expect(default_sharing_row).to_be_visible()
            expect(default_sharing).to_be_enabled()

            default_sharing.check()
            expect(default_sharing).to_be_checked()

            sharing_form.get_by_label("Disabled").check()
            expect(default_sharing_row).to_be_hidden()
            expect(default_sharing).not_to_be_checked()

    def test_should_toggle_description_max_lines_from_description_style(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_description_display = (
            UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_INLINE
        )
        profile.save()

        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:settings.general"))
            self.wait_for_settings_page_behavior(page)

            bookmarks_form = page.locator(
                "section#settings-bookmarks form[data-settings-save-mode='instant']"
            )
            max_lines_row = page.locator(
                "[data-setting-row='bookmark_description_max_lines']"
            )

            expect(max_lines_row).to_be_hidden()

            bookmarks_form.get_by_label("Separate").check()
            expect(max_lines_row).to_be_visible()

            bookmarks_form.get_by_label("Inline").check()
            expect(max_lines_row).to_be_hidden()

    def test_should_toggle_refresh_favicons_row_from_favicon_switch(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:settings.general"))
            self.wait_for_settings_page_behavior(page)

            refresh_row = page.locator("[data-setting-row='refresh_favicons']")
            expect(refresh_row).to_be_hidden()

            page.get_by_label("Display Favicons").check()
            expect(refresh_row).to_be_visible()

            page.get_by_label("Display Favicons").uncheck()
            expect(refresh_row).to_be_hidden()

    def test_should_restore_long_text_draft_after_theme_reload(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:settings.general"))
            page.evaluate(
                """
                () => {
                  try {
                    for (const key of Object.keys(window.localStorage)) {
                      if (
                        key.startsWith("ld:settings-panel:") ||
                        key.startsWith("ld:settings-draft:")
                      ) {
                        window.localStorage.removeItem(key);
                      }
                    }
                  } catch (_error) {
                    // Ignore storage access failures in test bootstrap.
                  }
                }
                """
            )
            page.reload(wait_until="networkidle")
            self.wait_for_settings_page_behavior(page)

            theme_form = page.locator(
                "section#settings-interface form[data-settings-save-mode='instant']"
            )
            custom_css_form = page.locator(
                "section#settings-interface form[data-settings-save-mode='explicit']"
            )
            custom_css_form.get_by_role("button", name="Expand").click()
            custom_css = custom_css_form.locator("textarea[name='custom_css']")
            custom_css.fill("body { color: hotpink; }")

            with page.expect_navigation(wait_until="load", timeout=3000):
                theme_form.get_by_label("Dark").check()

            self.wait_for_settings_page_behavior(page)
            custom_css_form = page.locator(
                "section#settings-interface form[data-settings-draft-form]"
            ).first
            custom_css = custom_css_form.locator("textarea[name='custom_css']")
            expect(custom_css).to_have_value("")
            restore_button = custom_css_form.get_by_role("button", name="Restore draft")
            expect(restore_button).to_be_visible()

            restore_button.click()
            expect(custom_css).to_have_value("body { color: hotpink; }")
            expect(restore_button).to_be_hidden()

    def test_should_restore_legacy_plain_string_draft(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:settings.general"))
            self.wait_for_settings_page_behavior(page)

            page.evaluate(
                """
                () => {
                  try {
                    window.localStorage.setItem(
                      "ld:settings-draft:profile_custom_css",
                      "body { color: deepskyblue; }"
                    );
                  } catch (_error) {
                    // Ignore storage access failures in test bootstrap.
                  }
                }
                """
            )
            page.reload(wait_until="networkidle")
            self.wait_for_settings_page_behavior(page)

            custom_css_form = page.locator(
                "section#settings-interface form[data-settings-draft-form]"
            ).first
            custom_css_form.get_by_role("button", name="Expand").click()
            restore_button = custom_css_form.get_by_role("button", name="Restore draft")
            custom_css = custom_css_form.locator("textarea[name='custom_css']")

            expect(restore_button).to_be_visible()
            restore_button.click()
            expect(custom_css).to_have_value("body { color: deepskyblue; }")

    def test_should_release_directory_lock_on_keyboard_scroll_intent(self):
        with sync_playwright() as p:
            browser = self.setup_browser(p)
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("linkding:settings.general"))
            self.wait_for_settings_page_behavior(page)

            user_link = page.locator(
                "[data-settings-directory] [data-settings-section-target='settings-user']"
            )
            user_link.click()
            expect(user_link).to_have_attribute("aria-current", "true")

            active_targets = page.evaluate(
                """
                () => new Promise((resolve) => {
                  document.dispatchEvent(
                    new KeyboardEvent("keydown", { key: "End", bubbles: true })
                  );
                  const scroller = document.querySelector(".body-container");
                  scroller.scrollTop = scroller.scrollHeight;
                  scroller.dispatchEvent(new Event("scroll"));
                  window.setTimeout(() => {
                    resolve(
                      Array.from(
                        document.querySelectorAll(
                          "[data-settings-directory] [data-settings-section-target]"
                        )
                      )
                        .filter((element) => element.getAttribute("aria-current") === "true")
                        .map((element) => element.dataset.settingsSectionTarget)
                    );
                  }, 150);
                })
                """
            )

            self.assertEqual(active_targets, ["settings-about"])
