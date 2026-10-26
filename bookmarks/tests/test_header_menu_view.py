from django.test import TestCase
from django.urls import reverse

from bookmarks.models import UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class BookmarkHeaderMenuViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def render_index(self):
        response = self.client.get(reverse("linkding:bookmarks.index"))
        self.assertEqual(response.status_code, 200)
        return self.make_soup(response.content.decode())

    def test_more_menu_lists_actions_before_preferences(self):
        soup = self.render_index()
        panel = soup.select_one(".header-more .header-more-panel")
        self.assertIsNotNone(panel)

        actions = panel.select(".header-menu-action")
        self.assertEqual(
            [action.get("data-menu-action") for action in actions],
            ["bulk-edit", "random-sort"],
        )

        # 分隔线之后才是显示偏好（只取面板直属的那一条，别撞上偏好组内部的分隔线）
        self.assertIsNotNone(panel.select_one(":scope > .header-menu-divider"))
        self.assertIsNotNone(panel.select_one('select[name="theme"]'))
        self.assertIsNotNone(panel.select_one('select[name="bookmark_view_mode"]'))
        self.assertIsNotNone(panel.select_one('input[name="items_per_page"]'))
        self.assertIsNotNone(
            panel.select_one('input[name="sticky_header_controls"]')
        )
        self.assertIsNotNone(panel.select_one('input[name="sticky_pagination"]'))

    def test_more_menu_view_option_matches_profile(self):
        self.user.profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_COMPACT
        self.user.profile.save()

        soup = self.render_index()
        panel = soup.select_one(".header-more .header-more-panel")
        option = panel.select_one(
            'select[name="bookmark_view_mode"] option[value="compact"]'
        )
        self.assertIsNotNone(option)
        self.assertTrue(option.has_attr("selected"))

    def test_wide_layout_popover_matches_profile(self):
        self.user.profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_COMPACT
        self.user.profile.save()

        soup = self.render_index()
        popover = soup.select_one(".header-layout .header-more-panel")
        self.assertIsNotNone(popover)
        option = popover.select_one(
            'select[name="bookmark_view_mode"] option[value="compact"]'
        )
        self.assertIsNotNone(option)
        self.assertTrue(option.has_attr("selected"))
        self.assertIsNotNone(popover.select_one('input[name="items_per_page"]'))
        self.assertIsNotNone(
            popover.select_one('input[name="sticky_header_controls"]')
        )
        self.assertIsNotNone(
            popover.select_one('input[name="sticky_pagination"]')
        )

    def test_random_rows_follow_random_button_and_mode(self):
        soup = self.render_index()
        panel = soup.select_one(".header-layout .header-more-panel")
        self.assertIsNotNone(panel.select_one('select[name="random_mode"]'))
        # 列表模式下随机目标无意义，行用 hidden 收起（切换模式时由 JS 直接改，不重载）
        self.assertTrue(
            panel.select_one('[data-pref-row="random_target"]').has_attr("hidden")
        )

        self.user.profile.random_mode = UserProfile.RANDOM_MODE_SINGLE
        self.user.profile.save()
        panel = self.render_index().select_one(".header-layout .header-more-panel")
        self.assertFalse(
            panel.select_one('[data-pref-row="random_target"]').has_attr("hidden")
        )

        self.user.profile.enable_random_button = False
        self.user.profile.save()
        panel = self.render_index().select_one(".header-layout .header-more-panel")
        self.assertIsNone(panel.select_one('select[name="random_mode"]'))
        self.assertIsNone(panel.select_one('select[name="random_target"]'))

    def test_random_control_renders_both_modes_for_instant_switch(self):
        soup = self.render_index()
        self.assertIsNotNone(soup.select_one("form[data-random-control]:not([hidden])"))
        self.assertTrue(soup.select_one("[ld-random-single]").has_attr("hidden"))

        self.user.profile.random_mode = UserProfile.RANDOM_MODE_SINGLE
        self.user.profile.save()
        soup = self.render_index()
        self.assertIsNotNone(soup.select_one("[ld-random-single]:not([hidden])"))
        self.assertTrue(
            soup.select_one("form[data-random-control]").has_attr("hidden")
        )

    def test_collapse_action_only_when_grouped(self):
        soup = self.render_index()
        panel = soup.select_one(".header-layout .header-more-panel")
        self.assertIsNotNone(panel.select_one(".group-toggle-all"))

        self.user.profile.group_by_date = False
        self.user.profile.save()
        panel = self.render_index().select_one(".header-layout .header-more-panel")
        self.assertIsNone(panel.select_one(".group-toggle-all"))

    def test_items_per_page_input_keeps_custom_value(self):
        self.user.profile.items_per_page = 40
        self.user.profile.save()

        soup = self.render_index()
        field = soup.select_one(
            '.header-layout .header-more-panel input[name="items_per_page"]'
        )
        self.assertIsNotNone(field)
        self.assertEqual(field.get("value"), "40")
        # 非法值走字段错误容器，原地提示
        self.assertIsNotNone(
            soup.select_one(
                '.header-layout [data-field-error-for="items_per_page"]'
            )
        )

    def test_preview_and_toolbar_rows_hidden_in_compact_view(self):
        soup = self.render_index()
        panel = soup.select_one(".header-layout .header-more-panel")
        self.assertIsNotNone(panel.select_one('input[name="enable_preview_images"]'))
        self.assertIsNotNone(
            panel.select_one('input[name="bookmark_toolbar_auto_hide"]')
        )

        self.user.profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_COMPACT
        self.user.profile.save()
        panel = self.render_index().select_one(".header-layout .header-more-panel")
        self.assertIsNone(panel.select_one('input[name="enable_preview_images"]'))
        self.assertIsNone(panel.select_one('input[name="bookmark_toolbar_auto_hide"]'))

    def test_more_menu_hides_random_action_when_disabled(self):
        self.user.profile.enable_random_button = False
        self.user.profile.save()

        soup = self.render_index()
        actions = [
            action.get("data-menu-action")
            for action in soup.select(".header-more .header-menu-action")
        ]
        self.assertEqual(actions, ["bulk-edit"])
        # 偏好里的随机按钮开关仍在，只是子项收起
        panel = soup.select_one(".header-more .header-more-panel")
        self.assertIsNotNone(panel.select_one('input[name="enable_random_button"]'))
        self.assertIsNone(panel.select_one('select[name="random_mode"]'))

        # 共享页没有批量编辑，随机也关掉后不能留下孤立的分隔线
        soup = self.make_soup(
            self.client.get(reverse("linkding:bookmarks.shared")).content.decode()
        )
        panel = soup.select_one(".header-more .header-more-panel")
        self.assertIsNone(panel.select_one(".header-menu-action"))
        # 只剩偏好组之间的两条分隔线
        self.assertEqual(len(panel.select(".header-menu-divider")), 2)

    def test_switches_reflect_profile(self):
        self.user.profile.show_sidebar = False
        self.user.profile.enable_preview_images = True
        self.user.profile.bookmark_toolbar_auto_hide = True
        self.user.profile.theme = UserProfile.THEME_DARK
        self.user.profile.save()

        soup = self.render_index()
        panel = soup.select_one(".header-layout .header-more-panel")
        self.assertFalse(panel.select_one('input[name="show_sidebar"]').has_attr("checked"))
        self.assertTrue(
            panel.select_one('input[name="enable_preview_images"]').has_attr("checked")
        )
        self.assertTrue(
            panel.select_one('input[name="bookmark_toolbar_auto_hide"]').has_attr(
                "checked"
            )
        )
        theme = panel.select_one('select[name="theme"] option[selected]')
        self.assertEqual(theme.get("value"), "dark")

    def test_save_view_mode_through_quick_endpoint(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            {
                "form_id": "profile_quick",
                "form_fields": "bookmark_view_mode",
                "bookmark_view_mode": "compact",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.bookmark_view_mode,
            UserProfile.BOOKMARK_VIEW_MODE_COMPACT,
        )

    def test_save_sticky_prefs_through_quick_endpoint(self):
        response = self.client.post(
            reverse("linkding:settings.save"),
            {
                "form_id": "profile_quick",
                "form_fields": "sticky_header_controls,sticky_pagination",
                "sticky_header_controls": "on",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.sticky_header_controls)
        self.assertFalse(self.user.profile.sticky_pagination)

    def test_all_list_pages_render_header_menu(self):
        for url_name in (
            "bookmarks.index",
            "bookmarks.archived",
            "bookmarks.shared",
            "bookmarks.trashed",
        ):
            response = self.client.get(reverse(f"linkding:{url_name}"))
            self.assertEqual(response.status_code, 200, url_name)
            soup = self.make_soup(response.content.decode())
            self.assertIsNotNone(
                soup.select_one(".header-more .header-more-panel"), url_name
            )

    def test_shared_menu_has_random_but_no_bulk_action(self):
        soup = self.make_soup(
            self.client.get(reverse("linkding:bookmarks.shared")).content.decode()
        )
        actions = [a.get("data-menu-action") for a in soup.select(".header-menu-action")]
        self.assertIn("random-sort", actions)
        self.assertNotIn("bulk-edit", actions)
