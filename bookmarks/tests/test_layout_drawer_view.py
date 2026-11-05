from django.test import TestCase
from django.urls import reverse

from bookmarks.models import UserProfile, UserProfileQuickSettingsForm
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin

LIST_URL_NAMES = (
    "bookmarks.index",
    "bookmarks.archived",
    "bookmarks.shared",
    "bookmarks.trashed",
)


class LayoutDrawerViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def render(self, url_name="bookmarks.index"):
        response = self.client.get(reverse(f"linkding:{url_name}"))
        self.assertEqual(response.status_code, 200)
        return self.make_soup(response.content.decode())

    def drawer(self, soup=None):
        soup = soup or self.render()
        drawer = soup.select_one("[data-layout-drawer]")
        self.assertIsNotNone(drawer)
        return drawer

    def panel(self, name, soup=None):
        return self.drawer(soup).select_one(f'[data-layout-panel="{name}"]')

    # --- 结构 ---

    def test_drawer_renders_closed_on_every_list_page(self):
        for url_name in LIST_URL_NAMES:
            with self.subTest(page=url_name):
                soup = self.render(url_name)
                drawer = self.drawer(soup)
                self.assertEqual(drawer.get("aria-hidden"), "true")
                self.assertTrue(drawer.has_attr("inert"))
                self.assertFalse(drawer.has_attr("is-open"))
                button = soup.select_one("[data-layout-drawer-toggle]")
                self.assertIsNotNone(button)
                self.assertEqual(button.get("aria-controls"), "layout-drawer")
                # 按钮挂在工具栏下（窄屏由 .header-controls 的 has-random 决定显隐）
                self.assertIsNotNone(button.find_parent(class_="header-controls"))

    def test_drawer_has_page_and_bookmark_tabs(self):
        drawer = self.drawer()
        tabs = [tab.get("data-layout-tab") for tab in drawer.select("[data-layout-tab]")]
        self.assertEqual(tabs, ["page", "bookmark"])
        panels = [
            panel.get("data-layout-panel") for panel in drawer.select("[data-layout-panel]")
        ]
        self.assertEqual(panels, ["page", "bookmark"])
        self.assertTrue(
            drawer.select_one('[data-layout-panel="bookmark"]').has_attr("hidden")
        )

    def test_page_tab_groups(self):
        panel = self.panel("page")
        groups = [
            label.get_text(strip=True)
            for label in panel.select(".layout-drawer-group-label")
        ]
        self.assertEqual(groups, ["Interface", "View", "Toolbar", "Sidebar"])

    def test_bookmark_tab_groups(self):
        panel = self.panel("bookmark")
        groups = [
            label.get_text(strip=True)
            for label in panel.select(".layout-drawer-group-label")
        ]
        self.assertEqual(groups, ["Content", "Behavior", "Toolbar"])

    def test_groups_are_collapsible_with_header_actions(self):
        # 每个分组都是可折叠的 details，默认展开；批量开关在标题栏关闭按钮左侧
        drawer = self.drawer()
        for panel_name in ("page", "bookmark"):
            panel = self.panel(panel_name)
            groups = panel.select("details[data-drawer-group]")
            self.assertGreater(len(groups), 1, panel_name)
            for group in groups:
                with self.subTest(panel=panel_name, group=group.get("data-drawer-group")):
                    self.assertTrue(group.has_attr("open"))
                    summary = group.select_one("summary.layout-drawer-group-label")
                    self.assertIsNotNone(summary)
                    self.assertIsNotNone(summary.select_one(".ld-chevron"))
            self.assertIsNone(panel.select_one("[data-layout-groups-expand]"))

        actions = drawer.select_one(".layout-drawer-header-actions")
        self.assertIsNotNone(actions)
        self.assertIsNotNone(actions.select_one("[data-layout-groups-expand]"))
        self.assertIsNotNone(actions.select_one("[data-layout-groups-collapse]"))
        self.assertIsNotNone(actions.select_one("[data-layout-drawer-close]"))

        # 分组键跨页签不重名，折叠状态才不会互相覆盖
        keys = [
            group.get("data-drawer-group")
            for group in drawer.select("details[data-drawer-group]")
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_other_settings_row_links_to_settings_page(self):
        # 只是一行入口：不再套可折叠分组，免得为了点「前往」先展开一次
        panel = self.panel("page")
        row = panel.select_one('a[href="/settings/general"]').find_parent(class_="ld-row")
        self.assertIsNone(row.find_parent("details"))
        self.assertTrue(
            row.select_one(".ld-row-label").get_text(" ", strip=True).startswith(
                "Other settings"
            )
        )
        self.assertIsNotNone(row.select_one(".ld-help .ld-help-button"))

        link = row.select_one("a")
        self.assertEqual(link.get_text(strip=True), "Go to")
        # 抽屉里的入口按钮一律浅色，不抢主题色
        self.assertNotIn("btn-primary", link.get("class"))
        self.assertIn(
            "Configure bookmark automation, sharing scope, health check",
            panel.get_text(" ", strip=True),
        )

    def test_every_row_is_label_plus_control(self):
        # 统一版式：每行都是「标签列 + 控件列」，说明文字压在标签下方
        drawer = self.drawer()
        rows = drawer.select(".ld-row")
        self.assertGreater(len(rows), 20)
        for row in rows:
            self.assertIsNotNone(
                row.select_one(".ld-row-text"), row.get_text(strip=True)
            )
        helps = drawer.select(".ld-help")
        # 只保留指定的几项说明，其余不收
        self.assertEqual(len(helps), 12)
        for help_el in helps:
            self.assertTrue(help_el.select_one(".ld-help-button"))
            text = help_el.select_one(".ld-help-text")
            self.assertIsNotNone(text)
            self.assertTrue(text.get_text(strip=True))
        # 说明不再直接铺在行里
        self.assertEqual(len(drawer.select(".ld-row-hint")), 0)

    def test_help_icons_only_on_selected_rows(self):
        # 预取内部链接是全局设置，只有超级管理员看得到这一行
        user = self.setup_superuser()
        self.client.force_login(user)

        # 有说明的行（标签文案 → 关键片段）
        expected = [
            "Group by date",
            "Tag search mode",
            "Random button",
            "Display Favicons",
            "Expand notes",
            "Display preview images",
            "Display preview image placeholders",
            "Prefetch internal links",
            "Auto-hide",
            "Module settings",
            "Quick edit",
            "Quick tags",
            "Other settings",
        ]
        drawer = self.drawer()
        rows_with_help = [
            row.select_one(".ld-row-label").get_text(" ", strip=True)
            for row in drawer.select(".ld-row")
            if row.select_one(".ld-help")
        ]
        for needle in expected:
            with self.subTest(label=needle):
                self.assertTrue(
                    any(needle in label for label in rows_with_help),
                    f"{needle} 应该有说明图标",
                )

        # 其它行不该再带说明（比如语言、主题、每页数量）
        for needle in ("Language", "Theme", "Bookmarks per page", "Show URL"):
            with self.subTest(label=needle):
                self.assertFalse(
                    any(label.startswith(needle) for label in rows_with_help),
                    f"{needle} 不该有说明图标",
                )

        # 关键长文案仍在
        page = self.panel("page").get_text(" ", strip=True)
        self.assertIn("Group bookmarks by when they were added", page)
        self.assertIn("It shuffles the bookmark list, or opens a random bookmark.", page)

    def test_more_menu_no_longer_carries_preferences(self):
        soup = self.render()
        panel = soup.select_one(".header-more .header-more-panel")
        self.assertIsNotNone(panel)
        self.assertIsNone(panel.select_one("[data-pref]"))
        self.assertIsNone(panel.select_one(".header-menu-divider"))

    # --- 页面页 ---

    def test_language_and_theme_rows(self):
        panel = self.panel("page")
        language = panel.select("input[name='language']")
        self.assertGreaterEqual(len(language), 2)
        checked = [el.get("value") for el in language if el.has_attr("checked")]
        self.assertEqual(checked, [self.user.profile.language])
        # 与设置页一致：语言用分段控件
        self.assertIsNotNone(panel.select_one(".ld-segment input[name='language']"))

        for value in ("auto", "light", "dark"):
            self.assertIsNotNone(
                panel.select_one(f'input[name="theme"][value="{value}"]')
            )

    def test_view_mode_radio_matches_profile(self):
        self.user.profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_COMPACT
        self.user.profile.save()

        panel = self.panel("page")
        self.assertTrue(
            panel.select_one(
                'input[name="bookmark_view_mode"][value="compact"]'
            ).has_attr("checked")
        )
        self.assertFalse(
            panel.select_one(
                'input[name="bookmark_view_mode"][value="list"]'
            ).has_attr("checked")
        )

    def test_items_per_page_input_keeps_custom_value(self):
        self.user.profile.items_per_page = 40
        self.user.profile.save()

        panel = self.panel("page")
        field = panel.select_one('input[name="items_per_page"]')
        self.assertEqual(field.get("value"), "40")
        self.assertIsNotNone(panel.select_one('[data-field-error-for="items_per_page"]'))

    def test_collapse_row_only_when_grouped(self):
        panel = self.panel("page")
        self.assertFalse(
            panel.select_one('[data-drawer-row="collapse_groups"]').has_attr("hidden")
        )

        self.user.profile.group_by_date = False
        self.user.profile.save()
        panel = self.panel("page")
        self.assertTrue(
            panel.select_one('[data-drawer-row="collapse_groups"]').has_attr("hidden")
        )

    def test_random_rows_follow_button_and_mode(self):
        panel = self.panel("page")
        self.assertFalse(
            panel.select_one('[data-drawer-row="random_mode"]').has_attr("hidden")
        )
        self.assertTrue(
            panel.select_one('[data-drawer-row="random_target"]').has_attr("hidden")
        )

        self.user.profile.random_mode = UserProfile.RANDOM_MODE_SINGLE
        self.user.profile.save()
        panel = self.panel("page")
        self.assertFalse(
            panel.select_one('[data-drawer-row="random_target"]').has_attr("hidden")
        )

        self.user.profile.enable_random_button = False
        self.user.profile.save()
        panel = self.panel("page")
        self.assertTrue(
            panel.select_one('[data-drawer-row="random_mode"]').has_attr("hidden")
        )

    def test_sidebar_group_owns_module_settings(self):
        panel = self.panel("page")
        self.assertIsNotNone(panel.select_one("input[name='show_sidebar']"))
        self.assertIsNotNone(panel.select_one("input[name='sticky_side_panel']"))

        module_list = panel.select_one('[data-drawer-list="sidebar_modules"]')
        self.assertIsNotNone(module_list)
        keys = [
            item.get("data-item-key")
            for item in module_list.select("[data-item-key]")
        ]
        # 「设置」模块自己是旧入口，不再列进来
        self.assertNotIn("settings", keys)
        self.assertIn("tags", keys)

    # --- 书签页 ---

    def test_list_only_rows_hidden_in_other_views(self):
        panel = self.panel("bookmark")
        for field in ("display_url", "enable_preview_images"):
            with self.subTest(field=field):
                self.assertIsNotNone(panel.select_one(f'input[name="{field}"]'))

        self.user.profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_TABLE
        self.user.profile.save()
        panel = self.panel("bookmark")
        # 表格视图下这些项没有意义：行仍在 DOM，但带 hidden
        for field in ("display_url", "permanent_notes", "enable_preview_images"):
            with self.subTest(field=field):
                row = panel.select_one(f'[data-drawer-views] input[name="{field}"]')
                self.assertIsNotNone(row)
                self.assertTrue(row.find_parent(attrs={"data-drawer-views": True}).has_attr("hidden"))

    def test_placeholder_row_follows_preview_switch(self):
        self.user.profile.enable_preview_images = False
        self.user.profile.save()
        panel = self.panel("bookmark")
        self.assertTrue(
            panel.select_one('[data-drawer-row="preview_placeholders"]').has_attr(
                "hidden"
            )
        )

    def test_toolbar_module_gates_cover_every_module(self):
        panel = self.panel("bookmark")
        gates = {el.get("data-drawer-gate") for el in panel.select("[data-drawer-gate]")}
        # 日期格式在「内容」组，其余四个是工具栏模块的设置项
        self.assertEqual(
            gates, {"date", "actions", "quick_edits", "quick_tags", "statuses"}
        )

    def test_toolbar_module_list_matches_profile(self):
        self.user.profile.bookmark_toolbar_modules = [
            {"key": "statuses", "enabled": True},
            {"key": "date", "enabled": False},
        ]
        self.user.profile.save()

        panel = self.panel("bookmark")
        items = panel.select(
            '[data-drawer-list="bookmark_toolbar_modules"] [data-item-key]'
        )
        keys = [item.get("data-item-key") for item in items]
        self.assertEqual(keys[:2], ["statuses", "date"])
        self.assertFalse(items[1].select_one("[data-item-enabled]").has_attr("checked"))

    def test_action_status_and_quick_edit_lists_match_profile(self):
        self.user.profile.bookmark_actions = [{"key": "edit", "enabled": False}]
        self.user.profile.bookmark_statuses = [{"key": "unread", "enabled": False}]
        self.user.profile.bookmark_quick_edits = [{"key": "notes", "enabled": False}]
        self.user.profile.save()

        panel = self.panel("bookmark")
        for field, key in (
            ("bookmark_actions", "edit"),
            ("bookmark_statuses", "unread"),
            ("bookmark_quick_edits", "notes"),
        ):
            with self.subTest(field=field):
                item = panel.select_one(
                    f'[data-drawer-list="{field}"] [data-item-key="{key}"]'
                )
                self.assertIsNotNone(item)
                self.assertFalse(
                    item.select_one("[data-item-enabled]").has_attr("checked")
                )

    def test_quick_tags_row_opens_the_editor(self):
        panel = self.panel("bookmark")
        self.assertIsNotNone(panel.select_one("[data-drawer-quick-tags]"))
        drawer = self.drawer()
        self.assertEqual(
            drawer.get("data-quick-tags-url"),
            reverse("linkding:settings.quick_tags_fragment"),
        )

    # --- 生效方式 ---

    def test_apply_strategy_per_field(self):
        drawer = self.drawer()
        expected = {
            "bookmark_view_mode": "fragment",
            "items_per_page": "fragment",
            "display_url": "fragment",
            "permanent_notes": "fragment",
            "sticky_header_controls": "dom",
            "sticky_pagination": "dom",
            "show_sidebar": "dom",
            "bookmark_toolbar_auto_hide": "dom",
            "enable_random_button": "dom",
            "theme": "reload",
        }
        for field, apply in expected.items():
            with self.subTest(field=field):
                input_el = drawer.select_one(f'[data-pref="{field}"]')
                self.assertIsNotNone(input_el, field)
                self.assertEqual(input_el.get("data-pref-apply"), apply, field)

    def test_drawer_fields_are_real_profile_quick_fields(self):
        # 抽屉里出现的每个字段都必须能在对应表单里存下去
        quick_fields = set(UserProfileQuickSettingsForm().fields)
        drawer = self.drawer()
        rendered = {
            el["data-pref"]
            for el in drawer.select("[data-pref]")
            if el.get("data-pref-form") != "global_quick"
        }
        self.assertTrue(rendered)
        self.assertEqual(rendered - quick_fields, set())

        lists = {el["data-drawer-list"] for el in drawer.select("[data-drawer-list]")}
        self.assertEqual(lists - quick_fields, set())


class QuickTagsFragmentViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def test_returns_editor_only(self):
        self.user.profile.bookmark_quick_tags = [
            {"key": "1", "tag_names": ["news"], "label": "News", "enabled": True}
        ]
        self.user.profile.save()

        response = self.client.get(reverse("linkding:settings.quick_tags_fragment"))
        self.assertEqual(response.status_code, 200)
        soup = self.make_soup(response.content.decode())

        self.assertIsNotNone(soup.select_one("[data-bookmark-quick-tags-list]"))
        self.assertIsNotNone(soup.select_one("[data-qt-add]"))
        self.assertIsNotNone(
            soup.select_one("input[name='form_fields'][value='bookmark_quick_tags']")
        )
        # 只回编辑器，不带整页骨架
        self.assertIsNone(soup.select_one("html"))
        self.assertIsNone(soup.select_one(".settings-directory"))

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("linkding:settings.quick_tags_fragment"))
        self.assertEqual(response.status_code, 302)


class LayoutListFragmentViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def test_returns_only_the_list_container(self):
        self.setup_bookmark(title="first")
        response = self.client.get(
            reverse("linkding:bookmarks.list_fragment"), {"ctx": "active"}
        )
        self.assertEqual(response.status_code, 200)
        soup = self.make_soup(response.content.decode())

        container = soup.select_one("#bookmark-list-container")
        self.assertIsNotNone(container)
        self.assertIsNotNone(container.select_one("ul.bookmark-list"))
        # 局部刷新只该拿到列表本身，不能带页面骨架
        self.assertIsNone(soup.select_one("html"))
        self.assertIsNone(soup.select_one(".header-controls"))
        self.assertIsNone(soup.select_one("[data-layout-drawer]"))

    def test_group_by_date_marks_container(self):
        self.setup_bookmark()
        self.user.profile.group_by_date = True
        self.user.profile.save()

        response = self.client.get(
            reverse("linkding:bookmarks.list_fragment"), {"ctx": "active"}
        )
        soup = self.make_soup(response.content.decode())
        container = soup.select_one("#bookmark-list-container")
        self.assertTrue(container.has_attr("data-grouped"))

    def test_items_per_page_is_applied(self):
        for _ in range(3):
            self.setup_bookmark()
        self.user.profile.items_per_page = 10
        self.user.profile.save()

        response = self.client.get(
            reverse("linkding:bookmarks.list_fragment"), {"ctx": "active"}
        )
        soup = self.make_soup(response.content.decode())
        self.assertEqual(len(soup.select("li[ld-bookmark-item]")), 3)

    def test_uses_the_requested_context(self):
        self.setup_bookmark(title="active one")
        self.setup_bookmark(title="archived one", is_archived=True)

        response = self.client.get(
            reverse("linkding:bookmarks.list_fragment"), {"ctx": "archived"}
        )
        soup = self.make_soup(response.content.decode())
        titles = [a.get_text(strip=True) for a in soup.select("li[ld-bookmark-item] a")]
        self.assertIn("archived one", titles)
        self.assertNotIn("active one", titles)

    def test_rejects_unknown_context(self):
        response = self.client.get(
            reverse("linkding:bookmarks.list_fragment"), {"ctx": "nope"}
        )
        self.assertEqual(response.status_code, 400)

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("linkding:bookmarks.list_fragment"))
        self.assertEqual(response.status_code, 302)
