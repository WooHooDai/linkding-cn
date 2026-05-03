import json
import random
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup
from django.test import TestCase, override_settings
from django.urls import reverse
from requests import RequestException

from bookmarks.models import GlobalSettings, UserProfile
from bookmarks.services import tasks
from bookmarks.tests.helpers import BookmarkFactoryMixin
from bookmarks.views.settings import app_version, get_version_info


class SettingsGeneralViewTestCase(TestCase, BookmarkFactoryMixin):
    quick_boolean_fields = (
        "display_url",
        "permanent_notes",
        "display_view_bookmark_action",
        "display_edit_bookmark_action",
        "display_archive_bookmark_action",
        "display_remove_bookmark_action",
        "default_mark_unread",
        "default_mark_shared",
        "enable_favicons",
        "enable_preview_images",
        "enable_automatic_html_snapshots",
        "enable_web_archive",
        "sticky_header_controls",
        "sticky_pagination",
        "show_sidebar",
        "sticky_side_panel",
        "legacy_search",
    )

    def setUp(self) -> None:
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def make_soup(self, html):
        return BeautifulSoup(html, "html.parser")

    def create_sidebar_modules(self, modules=None):
        if modules is None:
            modules = [
                {"key": "summary", "enabled": True},
                {"key": "bundles", "enabled": True},
                {"key": "domains", "enabled": True},
                {"key": "tags", "enabled": True},
            ]
        return json.dumps(modules)

    def create_quick_profile_form_data(self, overrides=None):
        overrides = overrides or {}
        values = {
            "form_id": "profile_quick",
            "theme": UserProfile.THEME_AUTO,
            "bookmark_date_display": UserProfile.BOOKMARK_DATE_DISPLAY_RELATIVE,
            "bookmark_description_display": UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_INLINE,
            "bookmark_description_max_lines": "1",
            "bookmark_link_target": UserProfile.BOOKMARK_LINK_TARGET_BLANK,
            "enable_web_archive": False,
            "tag_search": UserProfile.TAG_SEARCH_STRICT,
            "tag_grouping": UserProfile.TAG_GROUPING_ALPHABETICAL,
            "legacy_search": False,
            "items_per_page": "30",
            "sharing_mode": "disabled",
            "sidebar_modules": self.create_sidebar_modules(),
            "display_url": False,
            "permanent_notes": False,
            "display_view_bookmark_action": True,
            "display_edit_bookmark_action": True,
            "display_archive_bookmark_action": True,
            "display_remove_bookmark_action": True,
            "default_mark_unread": False,
            "default_mark_shared": False,
            "enable_favicons": False,
            "enable_preview_images": False,
            "enable_automatic_html_snapshots": True,
            "sticky_header_controls": False,
            "sticky_pagination": False,
            "show_sidebar": True,
            "sticky_side_panel": False,
        }
        values.update(overrides)
        post_data = {
            k: v
            for k, v in values.items()
            if k not in self.quick_boolean_fields and k != "form_fields"
        }
        post_data["form_fields"] = ",".join(
            key for key in values if key not in {"form_id", "form_fields"}
        )
        for field in self.quick_boolean_fields:
            if values.get(field):
                post_data[field] = "on"
        return post_data

    def create_global_quick_form_data(self, overrides=None):
        overrides = overrides or {}
        values = {
            "form_id": "global_quick",
            "landing_page": GlobalSettings.LANDING_PAGE_LOGIN,
            "guest_profile_user": "",
            "enable_link_prefetch": False,
        }
        values.update(overrides)
        post_data = {
            "form_id": values["form_id"],
            "landing_page": values["landing_page"],
            "guest_profile_user": values["guest_profile_user"],
        }
        if values["enable_link_prefetch"]:
            post_data["enable_link_prefetch"] = "on"
        return post_data

    def create_long_text_form_data(self, form_id, field_name, value):
        return {
            "form_id": form_id,
            field_name: value,
        }

    def assertSuccessMessage(self, html, message: str, count=1):
        self.assertInHTML(
            f'<div class="toast toast-success mb-4">{message}</div>',
            html,
            count=count,
        )

    def test_should_render_grouped_sections_and_toc(self):
        superuser = self.setup_superuser()
        self.client.force_login(superuser)
        response = self.client.get(reverse("linkding:settings.general"))
        self.assertEqual(response.status_code, 200)

        soup = self.make_soup(response.content.decode())
        section_ids = [
            "settings-interface",
            "settings-sidebar",
            "settings-search",
            "settings-bookmarks",
            "settings-sharing",
            "settings-user",
            "settings-import-export",
            "settings-about",
        ]
        for section_id in section_ids:
            self.assertIsNotNone(soup.select_one(f"section#{section_id}"))

        nav_targets = [
            link.get("data-settings-section-target")
            for link in soup.select(
                ".settings-directory [data-settings-section-target]"
            )
        ]
        self.assertEqual(
            nav_targets,
            [
                "settings-interface",
                "settings-sidebar",
                "settings-search",
                "settings-bookmarks",
                "settings-sharing",
                "settings-user",
                "settings-import-export",
                "settings-about",
            ],
        )
        self.assertIsNone(soup.select_one("section#settings-bookmark-list"))

        username_value = soup.select_one("[data-setting-username]")
        self.assertIsNotNone(username_value)
        self.assertEqual(username_value.get_text(strip=True), superuser.username)
        self.assertIsNotNone(soup.find("a", href=reverse("change_password")))

    def test_should_render_requested_segment_option_order_and_labels(self):
        superuser = self.setup_superuser()
        self.client.force_login(superuser)
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        language_labels = [
            option.get_text(strip=True)
            for option in soup.select(
                '[aria-labelledby="settings-language-label"] .settings-segmented-option span'
            )
        ]
        self.assertEqual(language_labels, ["简体中文", "English", "..."])

        other_language_select = soup.select_one(
            "[data-settings-language-other] [data-settings-language-select]"
        )
        self.assertIsNotNone(other_language_select)
        other_language_options = [
            option.get_text(strip=True)
            for option in other_language_select.select("option")
        ]
        self.assertEqual(other_language_options, ["No other languages"])
        self.assertTrue(other_language_select.has_attr("disabled"))

        date_format_labels = [
            option.get_text(strip=True)
            for option in soup.select(
                '[aria-labelledby="settings-bookmark-date-label"] .settings-segmented-option span'
            )
        ]
        self.assertEqual(date_format_labels, ["Hidden", "Relative", "Absolute"])

        landing_page_labels = [
            option.get_text(strip=True)
            for option in soup.select(
                '[aria-labelledby="settings-landing-page-label"] .settings-segmented-option span'
            )
        ]
        self.assertEqual(landing_page_labels, ["Login page", "Shared page"])

    def test_search_and_tag_cards_should_render_in_expected_sections(self):
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        search_form_fields = [
            hidden_field.get("value")
            for hidden_field in soup.select(
                "section#settings-search input[name='form_fields']"
            )
        ]
        self.assertEqual(search_form_fields, ["legacy_search,tag_search"])

        search_section = soup.select_one("section#settings-search")
        self.assertIsNotNone(search_section)
        self.assertIn("Compatibility mode", search_section.get_text())
        self.assertIn("Tag search mode", search_section.get_text())

        sidebar_section = soup.select_one("section#settings-sidebar")
        self.assertIsNotNone(sidebar_section)
        self.assertIsNotNone(
            sidebar_section.select_one(
                "input[name='form_fields'][value='show_sidebar,sticky_side_panel,sidebar_modules']"
            )
        )

        bookmarks_section = soup.select_one("section#settings-bookmarks")
        self.assertIsNotNone(bookmarks_section)
        self.assertIsNotNone(
            bookmarks_section.select_one(
                "input[name='form_id'][value='profile_auto_tagging_rules']"
            )
        )
        self.assertIsNotNone(
            bookmarks_section.select_one(
                "input[name='form_id'][value='profile_custom_domain_root']"
            )
        )

    def test_prefetch_internal_links_should_render_in_interface_section(self):
        superuser = self.setup_superuser()
        self.client.force_login(superuser)

        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        interface_section = soup.select_one("section#settings-interface")
        self.assertIsNotNone(interface_section)
        self.assertIsNotNone(
            interface_section.select_one(
                "input[type='checkbox'][name='enable_link_prefetch']"
            )
        )

        user_section = soup.select_one("section#settings-user")
        self.assertIsNotNone(user_section)
        self.assertIsNone(
            user_section.select_one(
                "input[type='checkbox'][name='enable_link_prefetch']"
            )
        )

    def test_show_sidebar_should_render_in_sidebar_section(self):
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        interface_section = soup.select_one("section#settings-interface")
        self.assertIsNotNone(interface_section)
        self.assertIsNone(interface_section.select_one("input[name='show_sidebar']"))

        sidebar_section = soup.select_one("section#settings-sidebar")
        self.assertIsNotNone(sidebar_section)
        self.assertIsNotNone(sidebar_section.select_one("input[name='show_sidebar']"))

    @patch(
        "bookmarks.views.settings._get_other_language_choices",
        return_value=[("fr", "francais")],
    )
    def test_update_language_persists_other_language_preference(
        self, _mock_other_languages
    ):
        response = self.client.post(
            reverse("language-update"),
            {"language": "fr", "next": reverse("linkding:settings.general")},
            follow=True,
        )

        self.user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.profile.language, "fr")
        self.user.profile.clean_fields(
            exclude=["search_preferences", "trash_search_preferences"]
        )

    def test_should_hide_default_sharing_when_sharing_is_disabled(self):
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        default_sharing_row = soup.select_one(
            "[data-setting-row='default_mark_shared']"
        )
        self.assertIsNotNone(default_sharing_row)
        self.assertIn("is-hidden", default_sharing_row.get("class", []))

    def test_should_show_default_sharing_when_sharing_is_enabled(self):
        profile = self.user.profile
        profile.enable_sharing = True
        profile.default_mark_shared = True
        profile.save()

        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        default_sharing_row = soup.select_one(
            "[data-setting-row='default_mark_shared']"
        )
        self.assertIsNotNone(default_sharing_row)
        self.assertIn("Default sharing", default_sharing_row.get_text())

    def test_global_settings_only_visible_for_superuser(self):
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())
        user_section = soup.select_one("section#settings-user")
        self.assertIsNotNone(user_section)
        self.assertNotIn("Default page for visitors", user_section.get_text())
        self.assertNotIn("Anonymous visitor profile", user_section.get_text())

        superuser = self.setup_superuser()
        self.client.force_login(superuser)
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        section = soup.select_one("section#settings-user")
        self.assertIsNotNone(section)
        self.assertIn("User & Visitors", section.get_text())
        self.assertIn("Default page for visitors", section.get_text())
        self.assertIn("Anonymous visitor profile", section.get_text())

    def test_update_language_persists_language_preference(self):
        response = self.client.post(
            reverse("language-update"),
            {"language": "zh-hans", "next": reverse("linkding:settings.general")},
            follow=True,
        )

        self.user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.profile.language, "zh-hans")

    def test_async_profile_quick_save_updates_profile_fields(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data(
                {
                    "theme": UserProfile.THEME_DARK,
                    "bookmark_date_display": UserProfile.BOOKMARK_DATE_DISPLAY_HIDDEN,
                    "bookmark_description_display": UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE,
                    "bookmark_description_max_lines": "3",
                    "bookmark_link_target": UserProfile.BOOKMARK_LINK_TARGET_SELF,
                    "enable_web_archive": True,
                    "tag_search": UserProfile.TAG_SEARCH_LAX,
                    "tag_grouping": UserProfile.TAG_GROUPING_DISABLED,
                    "legacy_search": True,
                    "items_per_page": "40",
                    "display_url": True,
                    "permanent_notes": True,
                    "display_view_bookmark_action": False,
                    "display_edit_bookmark_action": True,
                    "display_archive_bookmark_action": False,
                    "display_remove_bookmark_action": True,
                    "default_mark_unread": True,
                    "enable_favicons": True,
                    "enable_preview_images": True,
                    "enable_automatic_html_snapshots": False,
                    "sticky_header_controls": True,
                    "sticky_pagination": True,
                    "show_sidebar": False,
                    "sticky_side_panel": False,
                    "sharing_mode": "public",
                    "default_mark_shared": True,
                    "sidebar_modules": self.create_sidebar_modules(
                        [
                            {"key": "domains", "enabled": True},
                            {"key": "summary", "enabled": True},
                            {"key": "bundles", "enabled": False},
                            {"key": "tags", "enabled": True},
                        ]
                    ),
                }
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, UserProfile.THEME_DARK)
        self.assertEqual(
            self.user.profile.bookmark_date_display,
            UserProfile.BOOKMARK_DATE_DISPLAY_HIDDEN,
        )
        self.assertEqual(
            self.user.profile.bookmark_description_display,
            UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE,
        )
        self.assertEqual(self.user.profile.bookmark_description_max_lines, 3)
        self.assertEqual(
            self.user.profile.bookmark_link_target,
            UserProfile.BOOKMARK_LINK_TARGET_SELF,
        )
        self.assertEqual(
            self.user.profile.web_archive_integration,
            UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED,
        )
        self.assertEqual(self.user.profile.tag_search, UserProfile.TAG_SEARCH_LAX)
        self.assertEqual(
            self.user.profile.tag_grouping, UserProfile.TAG_GROUPING_DISABLED
        )
        self.assertTrue(self.user.profile.legacy_search)
        self.assertEqual(self.user.profile.items_per_page, 40)
        self.assertTrue(self.user.profile.display_url)
        self.assertTrue(self.user.profile.permanent_notes)
        self.assertFalse(self.user.profile.display_view_bookmark_action)
        self.assertFalse(self.user.profile.display_archive_bookmark_action)
        self.assertTrue(self.user.profile.default_mark_unread)
        self.assertTrue(self.user.profile.enable_favicons)
        self.assertTrue(self.user.profile.enable_preview_images)
        self.assertFalse(self.user.profile.enable_automatic_html_snapshots)
        self.assertTrue(self.user.profile.sticky_header_controls)
        self.assertTrue(self.user.profile.sticky_pagination)
        self.assertTrue(self.user.profile.collapse_side_panel)
        self.assertTrue(self.user.profile.enable_sharing)
        self.assertTrue(self.user.profile.enable_public_sharing)
        self.assertTrue(self.user.profile.default_mark_shared)
        self.assertEqual(
            self.user.profile.sidebar_modules,
            [
                {"key": "domains", "enabled": True},
                {"key": "summary", "enabled": True},
                {"key": "bundles", "enabled": False},
                {"key": "tags", "enabled": True},
            ],
        )

    def test_disabling_sidebar_should_preserve_sticky_sidebar_preference(self):
        profile = self.user.profile
        profile.sticky_side_panel = True
        profile.save(update_fields=["sticky_side_panel"])

        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data(
                {
                    "show_sidebar": False,
                    "sticky_side_panel": True,
                }
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)

        profile.refresh_from_db()
        self.assertTrue(profile.collapse_side_panel)
        self.assertTrue(profile.sticky_side_panel)

    def test_html_profile_quick_save_redirects_on_success(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data({"theme": UserProfile.THEME_DARK}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.request["PATH_INFO"], reverse("linkding:settings.general")
        )

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, UserProfile.THEME_DARK)
        self.assertSuccessMessage(response.content.decode(), "Profile updated")

    def test_async_profile_quick_save_rejects_invalid_items_per_page(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data({"items_per_page": "-1"}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("items_per_page", response.json()["errors"])

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.items_per_page, 30)

    def test_html_profile_quick_save_renders_form_errors(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data({"items_per_page": "-1"}),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("items_per_page", response.context["profile_quick_form"].errors)
        self.assertContains(
            response,
            "Ensure this value is greater than or equal to 10.",
            status_code=422,
        )

    def test_html_custom_css_save_redirects_on_success(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_long_text_form_data(
                "profile_custom_css", "custom_css", "body { color: green; }"
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.request["PATH_INFO"], reverse("linkding:settings.general")
        )

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.custom_css, "body { color: green; }")
        self.assertSuccessMessage(response.content.decode(), "Profile updated")

    def test_html_global_quick_save_redirects_on_success(self):
        superuser = self.setup_superuser()
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_global_quick_form_data(
                {"landing_page": GlobalSettings.LANDING_PAGE_SHARED_BOOKMARKS}
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.request["PATH_INFO"], reverse("linkding:settings.general")
        )
        self.assertSuccessMessage(response.content.decode(), "Global settings updated")
        self.assertEqual(
            GlobalSettings.get().landing_page,
            GlobalSettings.LANDING_PAGE_SHARED_BOOKMARKS,
        )

    def test_html_global_quick_save_renders_form_errors(self):
        superuser = self.setup_superuser()
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_global_quick_form_data({"landing_page": "invalid"}),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("landing_page", response.context["global_settings_form"].errors)
        self.assertContains(response, "Select a valid choice.", status_code=422)

    def test_async_profile_quick_save_only_updates_submitted_section_fields(self):
        profile = self.user.profile
        profile.theme = UserProfile.THEME_DARK
        profile.items_per_page = 30
        profile.sticky_header_controls = True
        profile.sticky_pagination = True
        profile.save()

        response = self.client.post(
            reverse("linkding:settings.save"),
            {
                "form_id": "profile_quick",
                "form_fields": "items_per_page,sticky_header_controls,sticky_pagination",
                "items_per_page": "55",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)

        profile.refresh_from_db()
        self.assertEqual(profile.theme, UserProfile.THEME_DARK)
        self.assertEqual(profile.items_per_page, 55)
        self.assertFalse(profile.sticky_header_controls)
        self.assertFalse(profile.sticky_pagination)

    def test_private_sharing_mode_enables_private_sharing_without_public_access(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data(
                {
                    "sharing_mode": "private",
                    "default_mark_shared": True,
                }
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)

        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.enable_sharing)
        self.assertFalse(self.user.profile.enable_public_sharing)
        self.assertTrue(self.user.profile.default_mark_shared)

    def test_disabling_sharing_resets_public_and_default_sharing(self):
        profile = self.user.profile
        profile.enable_sharing = True
        profile.enable_public_sharing = True
        profile.default_mark_shared = True
        profile.save()

        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_quick_profile_form_data(
                {
                    "sharing_mode": "disabled",
                    "default_mark_shared": True,
                }
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)

        profile.refresh_from_db()
        self.assertFalse(profile.enable_sharing)
        self.assertFalse(profile.enable_public_sharing)
        self.assertFalse(profile.default_mark_shared)

    def test_long_text_save_updates_only_the_target_field(self):
        profile = self.user.profile
        profile.custom_css = "body { color: red; }"
        profile.auto_tagging_rules = "example.com news"
        profile.custom_domain_root = "example.com"
        profile.save()

        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_long_text_form_data(
                "profile_custom_css", "custom_css", "body { color: blue; }"
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.custom_css, "body { color: blue; }")
        self.assertEqual(profile.auto_tagging_rules, "example.com news")
        self.assertEqual(profile.custom_domain_root, "example.com")

    def test_long_text_panels_should_render_collapsed_by_default(self):
        profile = self.user.profile
        profile.custom_css = "body { color: red; }"
        profile.auto_tagging_rules = "example.com news"
        profile.custom_domain_root = "example.com"
        profile.save()

        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        for panel_id in (
            "settings-custom-css-panel",
            "settings-sidebar-modules-panel",
            "settings-auto-tagging-panel",
            "settings-custom-domain-panel",
        ):
            panel = soup.select_one(f"#{panel_id}")
            self.assertIsNotNone(panel)
            self.assertTrue(panel.has_attr("hidden"))

        for control_id in (
            "settings-custom-css-panel",
            "settings-sidebar-modules-panel",
            "settings-auto-tagging-panel",
            "settings-custom-domain-panel",
        ):
            toggle = soup.select_one(
                f"[data-settings-panel-toggle][aria-controls='{control_id}']"
            )
            self.assertIsNotNone(toggle)
            self.assertEqual(toggle.get("aria-expanded"), "false")

    def test_import_file_controls_should_use_input_group_markup(self):
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        input_group = soup.select_one(
            ".settings-file-controls .input-group.settings-file-input-group"
        )
        self.assertIsNotNone(input_group)

        file_shell = input_group.select_one("[data-settings-file-shell]")
        self.assertIsNotNone(file_shell)
        self.assertIn("form-input", file_shell.get("class", []))
        self.assertIn("settings-file-input-shell", file_shell.get("class", []))

        file_input = input_group.select_one("input[type='file'][name='import_file']")
        self.assertIsNotNone(file_input)
        self.assertIn("settings-native-file-input", file_input.get("class", []))

        upload_button = input_group.select_one("input[type='submit']")
        self.assertIsNotNone(upload_button)
        self.assertIn("input-group-btn", upload_button.get("class", []))

    def test_async_global_save_updates_global_settings(self):
        superuser = self.setup_superuser()
        selectable_user = self.setup_user()
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_global_quick_form_data(
                {
                    "landing_page": GlobalSettings.LANDING_PAGE_SHARED_BOOKMARKS,
                    "guest_profile_user": selectable_user.id,
                    "enable_link_prefetch": True,
                }
            ),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        global_settings = GlobalSettings.get()
        self.assertEqual(
            global_settings.landing_page, GlobalSettings.LANDING_PAGE_SHARED_BOOKMARKS
        )
        self.assertEqual(global_settings.guest_profile_user, selectable_user)
        self.assertTrue(global_settings.enable_link_prefetch)

    def test_async_global_save_checks_for_superuser(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            self.create_global_quick_form_data(),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 403)

    def test_refresh_favicons_action(self):
        with patch.object(tasks, "schedule_refresh_favicons") as mock_schedule:
            response = self.client.post(
                reverse("linkding:settings.update"),
                {"refresh_favicons": ""},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_schedule.assert_called_once_with(self.user)
        self.assertSuccessMessage(
            response.content.decode(),
            "Scheduled favicon update. This may take a while...",
        )

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_missing_html_snapshots_action(self):
        with patch.object(
            tasks, "create_missing_html_snapshots", return_value=5
        ) as mock_create:
            response = self.client.post(
                reverse("linkding:settings.update"),
                {"create_missing_html_snapshots": ""},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        mock_create.assert_called_once_with(self.user)
        self.assertSuccessMessage(
            response.content.decode(),
            "Queued 5 missing snapshots. This may take a while...",
        )

    def test_about_shows_version_info(self):
        with patch(
            "bookmarks.views.settings.get_version_info", return_value=app_version
        ):
            response = self.client.get(reverse("linkding:settings.general"))

        self.assertInHTML(
            f"""
            <tr>
                <td>Version</td>
                <td>{app_version}</td>
            </tr>
            """,
            response.content.decode(),
        )

    def test_about_shows_based_on_linkding_reference(self):
        response = self.client.get(reverse("linkding:settings.general"))
        soup = self.make_soup(response.content.decode())

        rows = soup.select("section#settings-about .settings-about-table tr")
        based_on_row = rows[-1]
        cells = based_on_row.select("td")

        self.assertEqual(cells[0].get_text(strip=True), "Based on")
        self.assertEqual(cells[1].get_text(" ", strip=True), "sissbruecker's linkding")

        link = cells[1].select_one("a")
        self.assertIsNotNone(link)
        self.assertEqual(link.get("href"), "https://github.com/sissbruecker/linkding")
        self.assertEqual(link.get_text(strip=True), "linkding")

    def test_get_version_info_just_displays_latest_when_versions_are_equal(self):
        latest_version_response_mock = Mock(
            status_code=200, json=lambda: {"name": f"v{app_version}"}
        )
        with patch.object(requests, "get", return_value=latest_version_response_mock):
            version_info = get_version_info(random.random())
            self.assertEqual(version_info, f"{app_version} (latest)")

    def test_get_version_info_shows_latest_version_when_versions_are_not_equal(self):
        latest_version_response_mock = Mock(
            status_code=200, json=lambda: {"name": "v123.0.1"}
        )
        with patch.object(requests, "get", return_value=latest_version_response_mock):
            version_info = get_version_info(random.random())
            self.assertEqual(version_info, f"{app_version} (latest: 123.0.1)")

    def test_get_version_info_silently_ignores_request_errors(self):
        with patch.object(requests, "get", side_effect=RequestException()):
            version_info = get_version_info(random.random())
            self.assertEqual(version_info, app_version)

    def test_get_version_info_handles_invalid_response(self):
        latest_version_response_mock = Mock(status_code=403, json=lambda: {})
        with patch.object(requests, "get", return_value=latest_version_response_mock):
            version_info = get_version_info(random.random())
            self.assertEqual(version_info, app_version)

        latest_version_response_mock = Mock(status_code=200, json=lambda: {})
        with patch.object(requests, "get", return_value=latest_version_response_mock):
            version_info = get_version_info(random.random())
            self.assertEqual(version_info, app_version)
