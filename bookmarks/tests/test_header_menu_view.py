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

    def test_list_pages_share_the_same_toolbar(self):
        # 四个列表页同一个骨架：标题独立、工具栏两行、按钮全部直接陈列
        for url_name in (
            "bookmarks.index",
            "bookmarks.archived",
            "bookmarks.shared",
            "bookmarks.trashed",
        ):
            response = self.client.get(reverse(f"linkding:{url_name}"))
            self.assertEqual(response.status_code, 200, url_name)
            soup = self.make_soup(response.content.decode())
            self.assertIsNotNone(soup.select_one(".page-title"), url_name)
            self.assertIsNotNone(
                soup.select_one(".section-header.header-stack .header-actions"), url_name
            )
            self.assertIsNotNone(soup.select_one("[data-tools-toggle]"), url_name)
            self.assertIsNone(soup.select_one(".header-more"), url_name)

    def test_shared_toolbar_has_no_bulk_or_group_controls(self):
        # 分享页可能被匿名访问，批量与分组（写 profile）都不摆上来
        soup = self.make_soup(
            self.client.get(reverse("linkding:bookmarks.shared")).content.decode()
        )
        actions = soup.select_one(".section-header.header-stack .header-actions")
        self.assertIsNotNone(actions)
        self.assertIsNone(actions.select_one(".bulk-edit-active-toggle"))
        self.assertIsNone(soup.select_one("[data-toolbar-pref='group_by_date']"))
        self.assertIsNotNone(soup.select_one("[data-toolbar-nav='sort']"))

    def test_index_toolbar_splits_search_and_list_controls(self):
        soup = self.render_index()
        main = soup.select_one(".section-header")
        tools = soup.select_one(".header-row-tools")
        self.assertIsNotNone(tools)

        # 搜索 + 常驻操作在第一行
        self.assertIsNotNone(main.select_one("#bookmark-search-container"))
        self.assertIsNotNone(main.select_one(".header-actions .bulk-edit-active-toggle"))
        self.assertIsNotNone(main.select_one("[data-tools-toggle]"))

        # 排序 / 分组 / 全部展开折叠在第二行
        self.assertIsNotNone(tools.select_one("[data-toolbar-nav='sort']"))
        self.assertIsNotNone(tools.select_one("[data-toolbar-pref='group_by_date']"))
        self.assertIsNotNone(tools.select_one("[data-group-expand-all]"))
        self.assertIsNotNone(tools.select_one("[data-group-collapse-all]"))
        self.assertIsNone(tools.select_one(".group-toggle-all"))
        self.assertIsNone(tools.select_one(".header-actions"))

        # 第一行不再有可见的排序控件，但要留一个 hidden 把当前排序带过面板提交
        self.assertIsNone(main.select_one("select[name='sort']") or soup.select_one(".section-header select[name='sort']"))
        hidden_sort = main.select_one("input[name='sort']")
        self.assertIsNotNone(hidden_sort)
        self.assertEqual(hidden_sort.get("value"), "added_desc")
