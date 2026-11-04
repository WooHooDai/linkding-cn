"""视图注册表的一致性约束。

新增视图时这些断言会失败，直到视图模板、布局菜单项、静态文件都补齐——
这是「新视图与已有视图保持一致」的唯一强制机制。
"""

import os
import re

from django.conf import settings
from django.http import HttpResponse
from django.template import RequestContext, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from bookmarks.middlewares import LinkdingMiddleware
from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin
from bookmarks.views import contexts


class BookmarkViewsRegistryTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()

    def render_list(self):
        rf = RequestFactory()
        request = rf.get("/bookmarks")
        request.user = self.user
        LinkdingMiddleware(lambda r: HttpResponse())(request)
        search = BookmarkSearch.from_request(request, request.GET)
        bookmark_list = contexts.ActiveBookmarkListContext(request, search)
        context = RequestContext(
            request,
            {
                "bookmark_list": bookmark_list,
                "sticky_pagination": self.user.profile.sticky_pagination,
                "show_notes": self.user.profile.permanent_notes,
            },
        )
        return Template("{% include 'bookmarks/bookmark_list.html' %}").render(context)

    def switch_view(self, mode):
        self.user.profile.bookmark_view_mode = mode
        self.user.profile.save()

    # --- 注册表本身 ---

    def test_registry_covers_every_choice(self):
        choices = {key for key, _ in UserProfile.BOOKMARK_VIEW_MODE_CHOICES}
        self.assertEqual(set(contexts.BOOKMARK_VIEWS), choices)

    def test_registry_templates_exist(self):
        for key, view in contexts.BOOKMARK_VIEWS.items():
            with self.subTest(view=key):
                path = os.path.join(
                    settings.BASE_DIR, "bookmarks", "templates", view["template"]
                )
                self.assertTrue(os.path.exists(path), view["template"])

    def test_registry_settings_are_real_preferences(self):
        # 白名单里的键必须能在布局面板里找到，否则视图会声明一个不存在的选项
        for key, view in contexts.BOOKMARK_VIEWS.items():
            with self.subTest(view=key):
                self.assertTrue(view["settings"])
                self.assertIn("bookmark_view_mode", view["settings"])

    # --- 每个视图渲染出的结构 ---

    def test_each_view_renders_single_data_list(self):
        self.setup_bookmark()
        for mode in contexts.BOOKMARK_VIEWS:
            with self.subTest(view=mode):
                self.switch_view(mode)
                soup = self.make_soup(self.render_list())
                lists = soup.select(
                    "ul.bookmark-list:not(.table-header-list):not(.table-footer-list)"
                )
                self.assertEqual(len(lists), 1)
                self.assertIn(f"view-{mode}", lists[0].get("class", []))

    def test_each_view_renders_one_row_per_bookmark(self):
        self.setup_bookmark(title="first")
        self.setup_bookmark(title="second")
        for mode in contexts.BOOKMARK_VIEWS:
            with self.subTest(view=mode):
                self.switch_view(mode)
                soup = self.make_soup(self.render_list())
                self.assertEqual(len(soup.select("li[ld-bookmark-item]")), 2)

    def test_row_attributes_are_identical_across_views(self):
        # 三个视图模板各自重复 <li> 包裹，这条断言锁住属性集合不漂移
        self.setup_bookmark()
        attribute_sets = {}
        for mode in contexts.BOOKMARK_VIEWS:
            self.switch_view(mode)
            soup = self.make_soup(self.render_list())
            li = soup.select_one("li[ld-bookmark-item]")
            attribute_sets[mode] = set(li.attrs)
        unique = {frozenset(attrs) for attrs in attribute_sets.values()}
        self.assertEqual(len(unique), 1, attribute_sets)

    # --- 布局抽屉 ---

    def test_layout_drawer_gates_view_specific_options_on_registry(self):
        # 注册表里的 settings 现在是「该视图下可调的项」：视图相关项必须严格跟随，
        # 与视图无关的项（模块顺序、动作列表等）任何视图都要在。
        self.client.force_login(self.user)
        view_specific = (
            "enable_preview_images",
            "bookmark_toolbar_auto_hide",
        )
        for mode, view in contexts.BOOKMARK_VIEWS.items():
            with self.subTest(view=mode):
                self.switch_view(mode)
                soup = self.make_soup(
                    self.client.get(
                        reverse("linkding:bookmarks.index")
                    ).content.decode()
                )
                drawer = soup.select_one("[data-layout-drawer]")
                rendered = {el["data-pref"] for el in drawer.select("[data-pref]")}
                self.assertIn("bookmark_view_mode", rendered)
                for field in view_specific:
                    # 行常驻 DOM，注册表决定它首帧是否收起
                    row = drawer.select_one(
                        f'[data-drawer-views] [data-pref="{field}"]'
                    ).find_parent(attrs={"data-drawer-views": True})
                    self.assertIsNotNone(row, field)
                    self.assertEqual(
                        row.has_attr("hidden"),
                        field not in view["settings"],
                        field,
                    )

    # --- 静态文件 ---

    def test_view_static_files_are_registered(self):
        theme = os.path.join(
            settings.BASE_DIR, "bookmarks", "styles", "theme-light.css"
        )
        index = os.path.join(settings.BASE_DIR, "bookmarks", "frontend", "index.js")
        with open(theme) as f:
            theme_css = f.read()
        with open(index) as f:
            index_js = f.read()
        for mode in contexts.BOOKMARK_VIEWS:
            for name, source in (
                (f"bookmark-{mode}.css", theme_css),
                (f"bookmark-{mode}.js", index_js),
            ):
                path = os.path.join(
                    settings.BASE_DIR,
                    "bookmarks",
                    "styles" if name.endswith(".css") else "frontend/components",
                    name,
                )
                if not os.path.exists(path):
                    continue
                with self.subTest(file=name):
                    self.assertIn(name, source)

    # --- 视图类不泄漏 ---

    def test_shared_style_sheet_has_no_view_specific_selectors(self):
        # 共享样式（页面骨架 + 行基座）不得出现任何视图类，视图差异留在各自文件
        for name in ("bookmark-page.css", "bookmark-row.css"):
            with open(os.path.join(settings.BASE_DIR, "bookmarks", "styles", name)) as f:
                source = f.read()
            for mode in contexts.BOOKMARK_VIEWS:
                with self.subTest(file=name, view=mode):
                    self.assertNotIn(f"view-{mode}", source)

    def test_shared_row_behavior_does_not_sniff_view_classes(self):
        path = os.path.join(
            settings.BASE_DIR, "bookmarks", "frontend", "components", "bookmark-row.js"
        )
        with open(path) as f:
            source = f.read()
        self.assertIsNone(re.search(r"view-(list|compact|table)", source))
