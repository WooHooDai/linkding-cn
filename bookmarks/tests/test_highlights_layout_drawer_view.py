import json

from django.test import TestCase
from django.urls import reverse

from bookmarks.models import (
    Annotation,
    UserProfileHighlightCopyFormatForm,
    UserProfileQuickSettingsForm,
)
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class HighlightsLayoutDrawerViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def setup_annotation(self, text="a highlight", color=Annotation.COLOR_YELLOW):
        bookmark = self.setup_bookmark()
        return Annotation.objects.create(
            bookmark=bookmark,
            selector={"type": "TextQuoteSelector", "exact": text},
            selected_text=text,
            color=color,
        )

    def render(self):
        response = self.client.get(reverse("linkding:bookmarks.highlights"))
        self.assertEqual(response.status_code, 200)
        return self.make_soup(response.content.decode())

    def drawer(self, soup=None):
        soup = soup or self.render()
        drawer = soup.select_one("[data-layout-drawer]")
        self.assertIsNotNone(drawer)
        return drawer

    # --- 结构 ---

    def test_drawer_renders_closed(self):
        soup = self.render()
        drawer = self.drawer(soup)
        self.assertEqual(drawer.get("aria-hidden"), "true")
        self.assertTrue(drawer.has_attr("inert"))
        self.assertFalse(drawer.has_attr("is-open"))

        button = soup.select_one("[data-layout-drawer-toggle]")
        self.assertIsNotNone(button)
        self.assertEqual(button.get("aria-controls"), "layout-drawer")

    def test_drawer_has_no_tabs(self):
        drawer = self.drawer()
        self.assertIsNone(drawer.select_one("[data-layout-tab]"))
        self.assertIsNone(drawer.select_one(".layout-drawer-tabs"))
        panel = drawer.select_one("[data-layout-panel]")
        self.assertIsNotNone(panel)
        self.assertFalse(panel.has_attr("hidden"))

    def test_toolbar_flags_random_state_for_narrow_layout(self):
        # 窄屏工具栏靠这个类在「随机 + 更多」与三个按钮平铺之间切换
        soup = self.render()
        toolbar = soup.select_one(".highlights-toolbar")
        self.assertIsNotNone(toolbar)
        self.assertTrue("has-random" in toolbar.get("class"))
        actions = [
            action.get("data-menu-action")
            for action in soup.select(".highlights-toolbar .header-more-panel .header-menu-action")
        ]
        self.assertEqual(actions, ["bulk-edit", "layout"])

        self.user.profile.highlights_enable_random_button = False
        self.user.profile.save()
        soup = self.render()
        toolbar = soup.select_one(".highlights-toolbar")
        self.assertNotIn("has-random", toolbar.get("class"))

    def test_groups_order(self):
        drawer = self.drawer()
        labels = [
            label.get_text(strip=True)
            for label in drawer.select(".layout-drawer-group-label")
        ]
        self.assertEqual(
            labels,
            ["Interface", "View", "Page toolbar", "Sidebar", "Highlight toolbar"],
        )

    def test_groups_are_collapsible_and_open_by_default(self):
        drawer = self.drawer()
        groups = drawer.select("details[data-drawer-group]")
        self.assertEqual(
            [group.get("data-drawer-group") for group in groups],
            ["interface", "view", "toolbar", "sidebar", "highlight-toolbar"],
        )
        for group in groups:
            with self.subTest(group=group.get("data-drawer-group")):
                self.assertTrue(group.has_attr("open"))
                summary = group.select_one("summary.layout-drawer-group-label")
                self.assertIsNotNone(summary)
                self.assertIsNotNone(summary.select_one(".ld-chevron"))

    def test_header_has_expand_all_and_collapse_all(self):
        drawer = self.drawer()
        expand = drawer.select_one("[data-layout-groups-expand]")
        collapse = drawer.select_one("[data-layout-groups-collapse]")
        self.assertIsNotNone(expand)
        self.assertIsNotNone(collapse)
        self.assertEqual(expand.get("aria-label"), "Expand all")
        self.assertEqual(collapse.get("aria-label"), "Collapse all")
        # 放在标题栏，和关闭按钮同一组
        actions = drawer.select_one(".layout-drawer-header-actions")
        self.assertIsNotNone(actions)
        self.assertIsNotNone(actions.select_one("[data-layout-drawer-close]"))

    # --- 视图 ---

    def test_highlights_per_page_keeps_custom_value(self):
        self.user.profile.highlights_per_page = 25
        self.user.profile.save()

        drawer = self.drawer()
        field = drawer.select_one('input[name="highlights_per_page"]')
        self.assertEqual(field.get("value"), "25")
        self.assertIsNotNone(
            drawer.select_one('[data-field-error-for="highlights_per_page"]')
        )

    def test_group_by_select_matches_saved_preference(self):
        self.user.profile.highlights_search_preferences = {"group_by": "domain"}
        self.user.profile.save()

        drawer = self.drawer()
        select = drawer.select_one('select[name="highlights_group_by"]')
        self.assertIsNotNone(select)
        self.assertEqual(
            [option.get("value") for option in select.select("option")],
            ["none", "bookmark", "domain", "color"],
        )
        self.assertEqual(
            [option.get("value") for option in select.select("option[selected]")],
            ["domain"],
        )

    def test_group_by_row_is_labelled_group_by(self):
        drawer = self.drawer()
        labels = [
            row.select_one(".ld-row-label").get_text(" ", strip=True)
            for row in drawer.select(".ld-row")
        ]
        self.assertTrue(any(label.startswith("Group by") for label in labels))
        self.assertFalse(any(label.startswith("Highlight grouping") for label in labels))

    def test_collapse_groups_row_only_when_grouped(self):
        drawer = self.drawer()
        self.assertIsNone(drawer.select_one("[data-hl-group-toggle-all]"))

        self.user.profile.highlights_search_preferences = {"group_by": "color"}
        self.user.profile.save()

        drawer = self.drawer()
        # 「分组依据」下面紧跟一行「分组折叠」
        rows = drawer.select(".ld-row")
        labels = [row.select_one(".ld-row-label").get_text(" ", strip=True) for row in rows]
        group_index = next(i for i, label in enumerate(labels) if label.startswith("Group by"))
        self.assertTrue(labels[group_index + 1].startswith("Collapse groups"))

        button = drawer.select_one("[data-hl-group-toggle-all]")
        self.assertIsNotNone(button)
        self.assertEqual(button.get_text(strip=True), "Collapse")
        self.assertEqual(button.get("data-label-expand"), "Expand")

    # --- 页面工具栏 ---

    def test_random_button_hidden_when_disabled(self):
        soup = self.render()
        self.assertFalse(soup.select_one("[data-hl-random]").has_attr("hidden"))

        self.user.profile.highlights_enable_random_button = False
        self.user.profile.save()
        soup = self.render()
        self.assertTrue(soup.select_one("[data-hl-random]").has_attr("hidden"))

    def test_sticky_pagination_flag_is_rendered(self):
        self.setup_annotation()

        soup = self.render()
        self.assertEqual(
            soup.select_one(".highlights-pagination").get("data-sticky-on"), "true"
        )

        self.user.profile.highlights_sticky_pagination = False
        self.user.profile.save()
        soup = self.render()
        self.assertEqual(
            soup.select_one(".highlights-pagination").get("data-sticky-on"), "false"
        )

    # --- 侧边栏 ---

    def test_sidebar_group_owns_module_settings(self):
        drawer = self.drawer()
        self.assertIsNotNone(drawer.select_one("input[name='show_highlights_sidebar']"))
        self.assertIsNotNone(
            drawer.select_one("input[name='highlights_sticky_side_panel']")
        )

        module_list = drawer.select_one(
            '[data-drawer-list="highlights_sidebar_modules"]'
        )
        self.assertIsNotNone(module_list)
        keys = [
            item.get("data-item-key")
            for item in module_list.select("[data-item-key]")
        ]
        self.assertEqual(keys, ["colors", "domains", "tags"])

    # --- 高亮工具栏 ---

    def test_copy_settings_rows(self):
        drawer = self.drawer()

        action = drawer.select_one('select[name="highlight_copy_default_action"]')
        self.assertIsNotNone(action)
        self.assertEqual(
            [option.get("value") for option in action.select("option")],
            ["both", "highlight", "note"],
        )
        # 没设置过就是默认格式
        self.assertEqual(
            drawer.select_one('textarea[name="item_format"]').text,
            "> ${highlight}\n\n${annotation}",
        )
        self.assertEqual(
            drawer.select_one('textarea[name="separator"]').text, "\n\n---\n\n"
        )

    def test_copy_format_rows_are_collapsed_by_default(self):
        drawer = self.drawer()
        details = [
            el
            for el in drawer.select("details.ld-details")
            if el.select_one("textarea")
        ]
        self.assertEqual(len(details), 2)
        for el in details:
            with self.subTest(field=el.select_one("textarea").get("name")):
                self.assertFalse(el.has_attr("open"))
                summary = el.select_one("summary.ld-row-summary")
                self.assertIsNotNone(summary)
                self.assertIsNotNone(summary.select_one(".ld-chevron"))

    # --- 生效方式 ---

    def test_apply_strategy_per_field(self):
        drawer = self.drawer()
        expected = {
            "theme": "reload",
            "highlights_per_page": "reload",
            "highlights_group_by": "reload",
            "highlights_sticky_header_controls": "dom",
            "highlights_sticky_pagination": "dom",
            "highlights_enable_random_button": "dom",
            "show_highlights_sidebar": "dom",
            "highlights_sticky_side_panel": "dom",
            "highlight_copy_default_action": "dom",
            "item_format": "dom",
            "separator": "dom",
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
            if el.get("data-pref-form") in (None, "profile_quick")
        }
        self.assertTrue(rendered)
        self.assertEqual(rendered - quick_fields, set())

        lists = {el["data-drawer-list"] for el in drawer.select("[data-drawer-list]")}
        self.assertEqual(lists - quick_fields, set())


class HighlightsGroupBySaveViewTest(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def post(self, form_id, **fields):
        data = {"form_id": form_id, "form_fields": ",".join(fields), **fields}
        return self.client.post(
            reverse("linkding:settings.save"),
            data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def test_saves_group_by_into_search_preferences(self):
        self.user.profile.highlights_search_preferences = {"sort": "date_modified"}
        self.user.profile.save()

        response = self.post("profile_highlights_group_by", highlights_group_by="color")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"status": "ok"})

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.highlights_search_preferences,
            {"sort": "date_modified", "group_by": "color"},
        )

    def test_rejects_unknown_value(self):
        response = self.post("profile_highlights_group_by", highlights_group_by="nope")
        self.assertEqual(response.status_code, 422)
        self.assertIn("highlights_group_by", json.loads(response.content)["errors"])

        self.user.profile.refresh_from_db()
        self.assertNotIn(
            "group_by", self.user.profile.highlights_search_preferences or {}
        )


class HighlightCopyFormatSaveViewTest(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def post(self, form_id, form_fields, **fields):
        data = {"form_id": form_id, "form_fields": form_fields, **fields}
        return self.client.post(
            reverse("linkding:settings.save"),
            data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def test_saving_one_field_keeps_the_other(self):
        self.user.profile.highlight_copy_format = {
            "item_format": "> ${highlight}",
            "separator": "old",
        }
        self.user.profile.save()

        response = self.post("profile_hl_separator", "separator", separator="new")
        self.assertEqual(response.status_code, 200)

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.highlight_copy_format,
            {"item_format": "> ${highlight}", "separator": "new"},
        )

    def test_never_saved_format_falls_back_to_defaults(self):
        response = self.post("profile_hl_separator", "separator", separator="---")
        self.assertEqual(response.status_code, 200)

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.highlight_copy_format,
            {
                "item_format": UserProfileHighlightCopyFormatForm.DEFAULT_ITEM_FORMAT,
                "separator": "---",
            },
        )


class HighlightsQuickPrefsSaveViewTest(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def test_saves_random_button_switch(self):
        # 未勾选时字段不出现在 POST 里，表示关闭
        response = self.client.post(
            reverse("linkding:settings.save"),
            {
                "form_id": "profile_quick",
                "form_fields": "highlights_enable_random_button",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)

        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.highlights_enable_random_button)
