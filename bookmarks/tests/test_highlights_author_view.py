from urllib.parse import unquote_plus
from urllib.parse import urlparse

from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Annotation, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class HighlightAuthorViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def setup_highlight(self, author="", title=None, text="a highlight"):
        bookmark = self.setup_bookmark(author=author, title=title)
        return Annotation.objects.create(
            bookmark=bookmark,
            selector={"type": "TextQuoteSelector", "exact": text},
            selected_text=text,
            color=Annotation.COLOR_YELLOW,
        )

    def render(self, **params):
        response = self.client.get(reverse("linkding:bookmarks.highlights"), params)
        self.assertEqual(response.status_code, 200)
        return self.make_soup(response.content.decode())

    def author_module(self, soup):
        module = soup.select_one('[data-sidebar-module="authors"]')
        self.assertIsNotNone(module)
        return module

    def author_items(self, module):
        """按渲染顺序返回 (作者名, 计数) —— count_desc 模式下是每行一个的列表"""

        def item_text(link):
            count_el = link.select_one(".count")
            if count_el is None:
                return link.get_text("", strip=True), None
            count = count_el.text.strip()
            name = link.get_text("", strip=True).replace(count, "", 1).strip()
            # 选中态前面带「-」前缀
            name = name.lstrip("-")
            return name, int(count)

        return [item_text(link) for link in module.select("a[data-is-author-item]")]

    # --- 侧边栏「作者」模块 ---

    def test_author_module_counts_highlights(self):
        # 按计数降序：每行一个作者，计数单位是高亮条数
        self.user.profile.highlights_author_mode = UserProfile.AUTHOR_MODE_COUNT_DESC
        self.user.profile.save()
        self.setup_highlight(author="鲁迅")
        self.setup_highlight(author="鲁迅")
        self.setup_highlight(author="王垠")

        module = self.author_module(self.render())
        self.assertEqual(self.author_items(module), [("鲁迅", 2), ("王垠", 1)])

    def test_author_module_defaults_to_first_letter_grouping(self):
        # 与书签页作者模块的默认观感一致
        self.assertEqual(
            self.user.profile.highlights_author_mode,
            UserProfile.AUTHOR_MODE_ALPHABETICAL,
        )
        self.setup_highlight(author="鲁迅")

        module = self.author_module(self.render())
        self.assertEqual(
            [head.get_text(strip=True) for head in module.select(".cjk-group-head")],
            ["L"],
        )

    def test_author_module_ignores_highlights_without_author(self):
        self.user.profile.highlights_author_mode = UserProfile.AUTHOR_MODE_COUNT_DESC
        self.user.profile.save()
        self.setup_highlight(author="")
        self.setup_highlight(author="鲁迅")

        module = self.author_module(self.render())
        self.assertEqual([name for name, _ in self.author_items(module)], ["鲁迅"])

    def test_author_module_click_filters_highlights(self):
        self.user.profile.highlights_author_mode = UserProfile.AUTHOR_MODE_COUNT_DESC
        self.user.profile.save()
        self.setup_highlight(author="鲁迅", title="朝花夕拾")
        self.setup_highlight(author="王垠")

        module = self.author_module(self.render())
        link = next(
            link
            for link in module.select("a[data-is-author-item]")
            if "鲁迅" in link.get_text()
        )
        query = unquote_plus(urlparse(link["href"]).query)
        # 不含空格的作者名按 `author:鲁迅` 生成，带引号形式等价
        self.assertRegex(query, r'author:?"?鲁迅')

        # 点进去后只留下该作者的高亮，且该作者处于选中态
        soup = self.render(q='author:"鲁迅"')
        module = self.author_module(soup)
        self.assertEqual([name for name, _ in self.author_items(module)], ["鲁迅"])
        selected = module.select_one(".selected")
        self.assertIsNotNone(selected)
        self.assertEqual(len(soup.select(".highlight-item")), 1)

    def test_author_module_shows_flat_mode_when_disabled(self):
        self.user.profile.highlights_author_mode = UserProfile.AUTHOR_MODE_ALPHABETICAL
        self.user.profile.save()
        self.setup_highlight(author="王垠")

        module = self.author_module(self.render())
        # 首字母分组：CJK 作者按拼音首字母归组
        self.assertEqual(
            [
                head.get_text(strip=True)
                for head in module.select(".cjk-group-head")
            ],
            ["W"],
        )

    def test_author_module_hidden_when_disabled_in_module_settings(self):
        self.user.profile.highlights_sidebar_modules = [
            {"key": UserProfile.SIDEBAR_MODULE_COLORS, "enabled": True},
            {"key": UserProfile.SIDEBAR_MODULE_DOMAINS, "enabled": True},
            {"key": UserProfile.SIDEBAR_MODULE_TAGS, "enabled": True},
            {"key": UserProfile.SIDEBAR_MODULE_AUTHORS, "enabled": False},
        ]
        self.user.profile.save()
        self.setup_highlight(author="鲁迅")

        soup = self.render()
        self.assertIsNone(soup.select_one('[data-sidebar-module="authors"]'))

    def test_author_module_mode_toggle(self):
        self.setup_highlight(author="王垠")

        response = self.client.post(
            reverse("linkding:bookmarks.highlights"),
            {"pref_action": "hl_toggle_author_mode", "value": UserProfile.AUTHOR_MODE_DISABLED},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/vnd.turbo-stream.html")
        soup = self.make_soup(response.content.decode())
        self.assertEqual(
            soup.select_one("turbo-stream")["target"], "hl-authors-container"
        )

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.highlights_author_mode,
            UserProfile.AUTHOR_MODE_DISABLED,
        )

    def test_author_module_mode_toggle_rejects_unknown_value(self):
        response = self.client.post(
            reverse("linkding:bookmarks.highlights"),
            {"pref_action": "hl_toggle_author_mode", "value": "nope"},
        )
        self.assertEqual(response.status_code, 302)

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.highlights_author_mode,
            UserProfile.AUTHOR_MODE_ALPHABETICAL,
        )

    def test_author_mode_is_independent_from_bookmarks_page(self):
        self.user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_ALPHABETICAL
        self.user.profile.author_sort = UserProfile.AUTHOR_SORT_COUNT
        self.user.profile.save()

        self.client.post(
            reverse("linkding:bookmarks.highlights"),
            {"pref_action": "hl_toggle_author_mode", "value": UserProfile.AUTHOR_MODE_DISABLED},
        )

        self.user.profile.refresh_from_db()
        self.assertEqual(
            self.user.profile.author_grouping, UserProfile.AUTHOR_GROUPING_ALPHABETICAL
        )
        self.assertEqual(self.user.profile.author_sort, UserProfile.AUTHOR_SORT_COUNT)

    # --- 高亮条目里的作者 ---

    def meta_sequence(self, item):
        meta = item.select_one(".highlight-meta")
        elements = meta.select(
            ".highlight-date, .highlight-author, .highlight-meta-sep, .highlight-bookmark-title"
        )
        return [element.get("class")[0] for element in elements]

    def test_item_shows_date_then_author_then_title(self):
        self.setup_highlight(author="鲁迅", title="朝花夕拾")

        item = self.render().select_one(".highlight-item")
        self.assertEqual(
            self.meta_sequence(item),
            [
                "highlight-date",
                "highlight-meta-sep",
                "highlight-author",
                "highlight-meta-sep",
                "highlight-bookmark-title",
            ],
        )
        author = item.select_one(".highlight-author")
        self.assertEqual(author.get_text(strip=True), "鲁迅")
        self.assertEqual(author["title"], "Author")
        title = item.select_one(".highlight-bookmark-title")
        self.assertEqual(title.get_text(strip=True), "朝花夕拾")
        # 作者链接进入 author 搜索
        self.assertRegex(
            unquote_plus(urlparse(author["href"]).query), r'author:?"?鲁迅'
        )

    def test_item_without_author_omits_author_and_separator(self):
        self.setup_highlight(author="", title="朝花夕拾")

        item = self.render().select_one(".highlight-item")
        self.assertIsNone(item.select_one(".highlight-author"))
        # 没有作者时只剩日期与标题之间的那一根分隔符
        self.assertEqual(len(item.select(".highlight-meta-sep")), 1)
        self.assertEqual(
            self.meta_sequence(item),
            ["highlight-date", "highlight-meta-sep", "highlight-bookmark-title"],
        )

    # --- 分组依据：作者 ---

    def test_group_by_author_uses_author_as_group_header(self):
        self.setup_highlight(author="鲁迅", title="朝花夕拾")
        self.setup_highlight(author="鲁迅", title="朝花夕拾")
        self.setup_highlight(author="王垠", title="人为什么活着")

        soup = self.render(group_by="author")
        headers = [
            header.get_text(" ", strip=True)
            for header in soup.select(".highlight-group-header h3")
        ]
        self.assertEqual(sorted(headers), sorted(["鲁迅", "王垠"]))

        counts = [
            count.get_text(strip=True)
            for count in soup.select(".highlight-group-count")
        ]
        self.assertEqual(sorted(counts), ["(1)", "(2)"])

    def test_group_by_author_does_not_repeat_author_in_items(self):
        self.setup_highlight(author="鲁迅", title="朝花夕拾")

        item = self.render(group_by="author").select_one(".highlight-item")
        self.assertIsNone(item.select_one(".highlight-author"))
        self.assertEqual(len(item.select(".highlight-meta-sep")), 1)
        self.assertIsNotNone(item.select_one(".highlight-bookmark-title"))

    def test_group_by_author_groups_highlights_without_author(self):
        self.setup_highlight(author="")
        self.setup_highlight(author="鲁迅")

        soup = self.render(group_by="author")
        headers = [
            header.get_text(" ", strip=True)
            for header in soup.select(".highlight-group-header h3")
        ]
        self.assertIn("No author", headers)

    def test_group_by_bookmark_shows_author_in_group_header(self):
        self.setup_highlight(author="鲁迅", title="朝花夕拾")

        soup = self.render(group_by="bookmark")
        header = soup.select_one(".highlight-group-header")
        self.assertEqual(header.select_one("h3").get_text(strip=True), "朝花夕拾")
        self.assertEqual(
            header.select_one(".highlight-group-author").get_text(strip=True), "鲁迅"
        )

        item = soup.select_one(".highlight-item")
        self.assertIsNone(item.select_one(".highlight-author"))
        self.assertIsNone(item.select_one(".highlight-bookmark-title"))

    def test_group_by_bookmark_omits_author_when_bookmark_has_none(self):
        self.setup_highlight(author="", title="朝花夕拾")

        soup = self.render(group_by="bookmark")
        header = soup.select_one(".highlight-group-header")
        self.assertIsNone(header.select_one(".highlight-group-author"))
        self.assertIsNone(header.select_one(".highlight-group-sep"))

    # --- 侧边栏内容接口 ---

    def test_sidebar_content_endpoint_renders_highlights_author_module(self):
        self.setup_highlight(author="鲁迅")

        response = self.client.get(
            reverse("linkding:sidebar.content"),
            {"ctx": "highlights", "modules": "authors"},
        )
        self.assertEqual(response.status_code, 200)
        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(soup.select_one('[data-sidebar-module="authors"]'))
        self.assertEqual(
            [
                link.get_text("", strip=True)
                for link in soup.select("a[data-is-author-item]")
            ],
            ["鲁迅1"],
        )
