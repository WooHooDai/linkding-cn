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

        # 分隔线之后才是显示偏好
        self.assertIsNotNone(panel.select_one(".header-menu-divider"))
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
