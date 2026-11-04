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

    def test_more_menu_lists_only_actions(self):
        soup = self.render_index()
        panel = soup.select_one(".header-more .header-more-panel")
        self.assertIsNotNone(panel)

        actions = panel.select(".header-menu-action")
        self.assertEqual(
            [action.get("data-menu-action") for action in actions],
            ["bulk-edit", "random-sort"],
        )
        # 显示偏好搬到左侧「布局」抽屉，菜单里不再有第二份
        self.assertIsNone(panel.select_one("[data-pref]"))

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

    def test_random_controls_hidden_when_button_disabled(self):
        self.user.profile.enable_random_button = False
        self.user.profile.save()

        soup = self.render_index()
        # 两个控件都在 DOM 里，只靠 hidden 表达关闭，抽屉里才能即时切换
        self.assertTrue(
            soup.select_one("form[data-random-control]").has_attr("hidden")
        )
        self.assertTrue(soup.select_one("[ld-random-single]").has_attr("hidden"))

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

    def test_more_menu_hidden_when_no_action_left(self):
        self.user.profile.enable_random_button = False
        self.user.profile.save()

        # 共享页没有批量编辑；随机也关掉后菜单整个不渲染，不留空按钮
        soup = self.make_soup(
            self.client.get(reverse("linkding:bookmarks.shared")).content.decode()
        )
        self.assertIsNone(soup.select_one(".header-more"))
