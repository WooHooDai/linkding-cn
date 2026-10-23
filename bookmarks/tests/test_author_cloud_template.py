from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
from django.template import RequestContext, Template
from django.test import RequestFactory, TestCase
from urllib.parse import unquote_plus
from bookmarks.middlewares import LinkdingMiddleware
from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin
from bookmarks.views import contexts


class AuthorCloudTemplateTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def render_template(
        self,
        context_type: type[contexts.AuthorCloudContext] = contexts.ActiveAuthorCloudContext,
        url: str = "/test",
        user: User | AnonymousUser = None,
    ):
        rf = RequestFactory()
        request = rf.get(url)
        request.user = user or self.get_or_create_test_user()
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)

        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        author_cloud_context = context_type(request, search)
        context = RequestContext(request, {"author_cloud": author_cloud_context})
        template_to_render = Template(
            "{% include 'bookmarks/sidebar/modules/authors/cloud.html' %}"
        )
        return template_to_render.render(context)

    @staticmethod
    def item_text(link) -> str:
        """名字 + 计数拼接（去掉 count 数字再拼回，避免名字内空格干扰）"""
        count_el = link.select_one(".count")
        if count_el is None:
            return link.get_text("", strip=True)
        count = count_el.text.strip()
        name = link.get_text("", strip=True).replace(count, "", 1).strip()
        return f"{name}{count}"

    def assertAuthorGroups(self, rendered_template: str, groups: list[list[str]], mode: str):
        soup = self.make_soup(rendered_template)

        if mode == "count_desc":
            # 每行一个作者（bundle 列表）
            link_elements = soup.select("ul.bundle-menu li a")
            items = [self.item_text(link) for link in link_elements]
            self.assertEqual(items, groups[0])
            return

        # alphabetical / disabled：标签云式 p.group 平铺
        group_elements = soup.select("p.group")
        self.assertEqual(len(group_elements), len(groups))

        for group_index, authors in enumerate(groups, start=0):
            group_element = group_elements[group_index]
            link_elements = group_element.select("a[data-is-author-item]")
            items = [self.item_text(link) for link in link_elements]
            self.assertEqual(items, authors)

    def test_count_desc_mode(self):
        user = self.get_or_create_test_user()
        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_DISABLED
        user.profile.author_sort = UserProfile.AUTHOR_SORT_COUNT
        user.profile.save()

        self.setup_bookmark(author="Grace Hopper")
        self.setup_bookmark(author="Grace Hopper")
        self.setup_bookmark(author="Linus Torvalds")
        self.setup_bookmark(author="")

        rendered = self.render_template(user=user)
        # 每行一个作者，计数降序
        self.assertAuthorGroups(
            rendered, [["Grace Hopper2", "Linus Torvalds1"]], mode="count_desc"
        )

    def test_alphabetical_mode(self):
        user = self.get_or_create_test_user()
        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_ALPHABETICAL
        user.profile.author_sort = UserProfile.AUTHOR_SORT_COUNT
        user.profile.save()

        self.setup_bookmark(author="Grace Hopper")
        self.setup_bookmark(author="Ada Lovelace")
        self.setup_bookmark(author="王垠")

        rendered = self.render_template(user=user)
        # 标签云式分组：A、G、W（CJK 组头）三组平铺
        self.assertAuthorGroups(
            rendered, [["Ada Lovelace1"], ["Grace Hopper1"], ["王垠1"]], mode="alphabetical"
        )

    def test_alphabetical_group_head_with_cjk(self):
        user = self.get_or_create_test_user()
        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_ALPHABETICAL
        user.profile.author_sort = UserProfile.AUTHOR_SORT_COUNT
        user.profile.save()

        self.setup_bookmark(author="王垠")
        self.setup_bookmark(author="张三")

        rendered = self.render_template(user=user)
        soup = self.make_soup(rendered)
        cjk_heads = soup.select("p.group .cjk-group-head")
        self.assertEqual([h.text.strip() for h in cjk_heads], ["W", "Z"])
        # CJK 组头与作者名同行（同一 p.group 内）
        for p in soup.select("p.group"):
            self.assertTrue(p.select(".cjk-group-head"))
            self.assertTrue(p.select("a[data-is-author-item]"))

    def test_disabled_mode(self):
        user = self.get_or_create_test_user()
        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_DISABLED
        user.profile.author_sort = UserProfile.AUTHOR_SORT_NAME
        user.profile.save()

        self.setup_bookmark(author="Grace Hopper")
        self.setup_bookmark(author="Ada Lovelace")
        self.setup_bookmark(author="Grace Hopper")

        rendered = self.render_template(user=user)
        # 标签云式平铺（不换行），按名称升序，单个 group
        self.assertAuthorGroups(
            rendered, [["Ada Lovelace1", "Grace Hopper2"]], mode="disabled"
        )

    def test_mode_options_mutually_exclusive(self):
        user = self.get_or_create_test_user()
        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_DISABLED
        user.profile.author_sort = UserProfile.AUTHOR_SORT_COUNT
        user.profile.save()

        request = RequestFactory().get("/test")
        request.user = user
        LinkdingMiddleware(lambda r: HttpResponse())(request)
        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        cloud = contexts.ActiveAuthorCloudContext(request, search)
        labels = [label for _, label, _ in cloud.mode_options]
        self.assertEqual(
            labels, ["By count desc", "Grouped by first letter", "Disable grouping"]
        )
        active = [is_active for _, _, is_active in cloud.mode_options]
        self.assertEqual(active, [True, False, False])

        # 切换模式后 active 唯一
        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_ALPHABETICAL
        user.profile.author_sort = UserProfile.AUTHOR_SORT_COUNT
        user.profile.save()
        request.user = user
        cloud2 = contexts.ActiveAuthorCloudContext(request, search)
        active2 = [is_active for _, _, is_active in cloud2.mode_options]
        self.assertEqual(active2, [False, True, False])

        user.profile.author_grouping = UserProfile.AUTHOR_GROUPING_DISABLED
        user.profile.author_sort = UserProfile.AUTHOR_SORT_NAME
        user.profile.save()
        request.user = user
        cloud3 = contexts.ActiveAuthorCloudContext(request, search)
        active3 = [is_active for _, _, is_active in cloud3.mode_options]
        self.assertEqual(active3, [False, False, True])

    def test_empty_cloud_renders_nothing(self):
        rendered = self.render_template()
        soup = self.make_soup(rendered)
        self.assertEqual(len(soup.select("ul.bundle-menu, p.group")), 0)

    def test_selected_author_toggles_off(self):
        user = self.get_or_create_test_user()
        tag = self.setup_tag(name="foo")
        self.setup_bookmark(author="Grace Hopper", tags=[tag])

        rf = RequestFactory()
        request = rf.get("/test", {"q": 'author:"Grace Hopper" #foo'})
        request.user = user
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)

        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        cloud = contexts.ActiveAuthorCloudContext(request, search)
        self.assertEqual(len(cloud.authors), 1)
        item = cloud.authors[0]
        # 已选中：再次点击取消筛选（移除 author 条件），保留其他条件
        self.assertTrue(item.is_selected)
        decoded_query = unquote_plus(item.query_string)
        self.assertNotIn('author:"Grace Hopper"', decoded_query)
        self.assertIn("#foo", decoded_query)

    def test_unselected_author_links_to_filter(self):
        user = self.get_or_create_test_user()
        tag = self.setup_tag(name="foo")
        self.setup_bookmark(author="Grace Hopper", tags=[tag])
        self.setup_bookmark(author="Linus Torvalds", tags=[tag])

        rf = RequestFactory()
        request = rf.get("/test", {"q": "#foo"})
        request.user = user
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)

        search = BookmarkSearch.from_request(
            request, request.GET, request.user_profile.search_preferences
        )
        cloud = contexts.ActiveAuthorCloudContext(request, search)
        grace = next(i for i in cloud.authors if i.name == "Grace Hopper")
        self.assertFalse(grace.is_selected)
        decoded_query = unquote_plus(grace.query_string)
        self.assertIn('author:"Grace Hopper"', decoded_query)
        self.assertIn("#foo", decoded_query)
