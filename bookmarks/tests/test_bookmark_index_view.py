import calendar
import urllib.parse

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.tests.helpers import (
    BookmarkFactoryMixin,
    BookmarkListTestMixin,
    DomainSidebarTestMixin,
    TagCloudTestMixin,
)


class BookmarkIndexViewTestCase(
    TestCase,
    BookmarkFactoryMixin,
    BookmarkListTestMixin,
    DomainSidebarTestMixin,
    TagCloudTestMixin,
):
    def setUp(self) -> None:
        user = self.get_or_create_test_user()
        self.client.force_login(user)

    def assertEditLink(self, response, url):
        soup = self.make_soup(response.content.decode())
        link = soup.select_one(f'a[href="{url}"]')
        self.assertIsNotNone(link)
        self.assertEqual(link.text.strip(), "Edit")

    def assertBulkActionForm(self, response, url: str):
        soup = self.make_soup(response.content.decode())
        form = soup.select_one("form.bookmark-actions")
        self.assertIsNotNone(form)
        self.assertEqual(form.attrs["action"], url)

    def assertVisibleBundles(self, soup, bundles):
        bundle_list = soup.select_one("ul.bundle-menu")
        self.assertIsNotNone(bundle_list)

        list_items = bundle_list.select("li.bundle-menu-item")
        self.assertEqual(len(list_items), len(bundles))

        for index, list_item in enumerate(list_items):
            bundle = bundles[index]
            link = list_item.select_one("a")
            href = link.attrs["href"]

            self.assertIn(bundle.name, list_item.text.strip())
            self.assertEqual(f"?bundle={bundle.id}", href)

    def get_summary_headers(
        self,
        *,
        mode=None,
        month=None,
        week=None,
        show_weekdays=None,
        show_details=None,
    ):
        headers = {}
        if mode is not None:
            headers["HTTP_X_LINKDING_SUMMARY_MODE"] = mode
        if month is not None:
            headers["HTTP_X_LINKDING_SUMMARY_MONTH"] = month
        if week is not None:
            headers["HTTP_X_LINKDING_SUMMARY_WEEK"] = week
        if show_weekdays is not None:
            headers["HTTP_X_LINKDING_SUMMARY_SHOW_WEEKDAYS"] = (
                "1" if show_weekdays else "0"
            )
        if show_details is not None:
            headers["HTTP_X_LINKDING_SUMMARY_SHOW_DETAILS"] = (
                "1" if show_details else "0"
            )
        return headers

    def get_domain_headers(self, *, view_mode=None, compact_mode=None):
        headers = {}
        if view_mode is not None:
            headers["HTTP_X_LINKDING_DOMAIN_VIEW"] = view_mode
        if compact_mode is not None:
            headers["HTTP_X_LINKDING_DOMAIN_COMPACT"] = compact_mode
        return headers

    def get_bookmark_page_stream_headers(self, **headers):
        return {
            "HTTP_ACCEPT": "text/vnd.turbo-stream.html",
            "HTTP_X_LINKDING_BOOKMARK_PAGE_STREAM": "1",
            **headers,
        }

    def set_profile_language(self, language: str):
        user = self.get_or_create_test_user()
        user.profile.language = language
        user.profile.save(update_fields=["language"])
        self.client.cookies["django_language"] = language
        return user

    def test_should_list_unarchived_and_user_owned_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        visible_bookmarks = self.setup_numbered_bookmarks(3)
        invisible_bookmarks = [
            self.setup_bookmark(is_archived=True),
            self.setup_bookmark(user=other_user),
        ]

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, invisible_bookmarks)

    def test_should_list_bookmarks_matching_query(self):
        visible_bookmarks = self.setup_numbered_bookmarks(3, prefix="foo")
        invisible_bookmarks = self.setup_numbered_bookmarks(3, prefix="bar")

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=foo")

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, invisible_bookmarks)

    def test_should_list_bookmarks_matching_bundle(self):
        visible_bookmarks = self.setup_numbered_bookmarks(3, prefix="foo")
        invisible_bookmarks = self.setup_numbered_bookmarks(3, prefix="bar")

        bundle = self.setup_bundle(search="foo")

        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?bundle={bundle.id}"
        )

        self.assertVisibleBookmarks(response, visible_bookmarks)
        self.assertInvisibleBookmarks(response, invisible_bookmarks)

    def test_should_list_tags_for_unarchived_and_user_owned_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        visible_bookmarks = self.setup_numbered_bookmarks(3, with_tags=True)
        archived_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, archived=True, tag_prefix="archived"
        )
        other_user_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, user=other_user, tag_prefix="otheruser"
        )

        visible_tags = self.get_tags_from_bookmarks(visible_bookmarks)
        invisible_tags = self.get_tags_from_bookmarks(
            archived_bookmarks + other_user_bookmarks
        )

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleTags(response, visible_tags)
        self.assertInvisibleTags(response, invisible_tags)

    def test_should_list_tags_for_bookmarks_matching_query(self):
        visible_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, prefix="foo", tag_prefix="foo"
        )
        invisible_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, prefix="bar", tag_prefix="bar"
        )

        visible_tags = self.get_tags_from_bookmarks(visible_bookmarks)
        invisible_tags = self.get_tags_from_bookmarks(invisible_bookmarks)

        response = self.client.get(reverse("linkding:bookmarks.index") + "?q=foo")

        self.assertVisibleTags(response, visible_tags)
        self.assertInvisibleTags(response, invisible_tags)

    def test_should_list_tags_for_bookmarks_matching_bundle(self):
        visible_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, prefix="foo", tag_prefix="foo"
        )
        invisible_bookmarks = self.setup_numbered_bookmarks(
            3, with_tags=True, prefix="bar", tag_prefix="bar"
        )

        visible_tags = self.get_tags_from_bookmarks(visible_bookmarks)
        invisible_tags = self.get_tags_from_bookmarks(invisible_bookmarks)

        bundle = self.setup_bundle(search="foo")

        response = self.client.get(
            reverse("linkding:bookmarks.index") + f"?bundle={bundle.id}"
        )

        self.assertVisibleTags(response, visible_tags)
        self.assertInvisibleTags(response, invisible_tags)

    def test_should_list_bookmarks_and_tags_for_search_preferences(self):
        user_profile = self.user.profile
        user_profile.search_preferences = {
            "unread": BookmarkSearch.FILTER_UNREAD_YES,
        }
        user_profile.save()

        unread_bookmarks = self.setup_numbered_bookmarks(
            3, unread=True, with_tags=True, prefix="unread", tag_prefix="unread"
        )
        read_bookmarks = self.setup_numbered_bookmarks(
            3, unread=False, with_tags=True, prefix="read", tag_prefix="read"
        )

        unread_tags = self.get_tags_from_bookmarks(unread_bookmarks)
        read_tags = self.get_tags_from_bookmarks(read_bookmarks)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        self.assertVisibleBookmarks(response, unread_bookmarks)
        self.assertInvisibleBookmarks(response, read_bookmarks)
        self.assertVisibleTags(response, unread_tags)
        self.assertInvisibleTags(response, read_tags)

    def test_should_display_selected_tags_from_query(self):
        tags = [
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
        ]
        self.setup_bookmark(tags=tags)

        response = self.client.get(
            reverse("linkding:bookmarks.index")
            + f"?q=%23{tags[0].name}+%23{tags[1].name.upper()}"
        )

        self.assertSelectedTags(response, [tags[0], tags[1]])

    def test_should_not_display_search_terms_from_query_as_selected_tags_in_strict_mode(
        self,
    ):
        tags = [
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
        ]
        self.setup_bookmark(title=tags[0].name, tags=tags)

        response = self.client.get(
            reverse("linkding:bookmarks.index")
            + f"?q={tags[0].name}+%23{tags[1].name.upper()}"
        )

        self.assertSelectedTags(response, [tags[1]])

    def test_should_display_search_terms_from_query_as_selected_tags_in_lax_mode(self):
        self.user.profile.tag_search = UserProfile.TAG_SEARCH_LAX
        self.user.profile.save()

        tags = [
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
            self.setup_tag(),
        ]
        self.setup_bookmark(tags=tags)

        response = self.client.get(
            reverse("linkding:bookmarks.index")
            + f"?q={tags[0].name}+%23{tags[1].name.upper()}"
        )

        self.assertSelectedTags(response, [tags[0], tags[1]])

    def test_should_open_bookmarks_in_new_page_by_default(self):
        visible_bookmarks = self.setup_numbered_bookmarks(3)

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleBookmarks(response, visible_bookmarks, "_blank")

    def test_should_open_bookmarks_in_same_page_if_specified_in_user_profile(self):
        user = self.get_or_create_test_user()
        user.profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_SELF
        user.profile.save()

        visible_bookmarks = self.setup_numbered_bookmarks(3)

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleBookmarks(response, visible_bookmarks, "_self")

    def test_edit_link_return_url_respects_search_options(self):
        bookmark = self.setup_bookmark(title="foo")
        edit_url = reverse("linkding:bookmarks.edit", args=[bookmark.id])
        base_url = reverse("linkding:bookmarks.index")

        # without query params
        return_url = urllib.parse.quote(base_url)
        url = f"{edit_url}?return_url={return_url}"

        response = self.client.get(base_url)
        self.assertEditLink(response, url)

        # with query
        url_params = "?q=foo"
        return_url = urllib.parse.quote(base_url + url_params)
        url = f"{edit_url}?return_url={return_url}"

        response = self.client.get(base_url + url_params)
        self.assertEditLink(response, url)

        # with query and sort and page
        url_params = "?q=foo&sort=title_asc&page=2"
        return_url = urllib.parse.quote(base_url + url_params)
        url = f"{edit_url}?return_url={return_url}"

        response = self.client.get(base_url + url_params)
        self.assertEditLink(response, url)

    def test_bulk_edit_respects_search_options(self):
        action_url = reverse("linkding:bookmarks.index.action")
        base_url = reverse("linkding:bookmarks.index")

        # without params
        url = f"{action_url}"

        response = self.client.get(base_url)
        self.assertBulkActionForm(response, url)

        # with query
        url_params = "?q=foo"
        url = f"{action_url}?q=foo"

        response = self.client.get(base_url + url_params)
        self.assertBulkActionForm(response, url)

        # with query and sort
        url_params = "?q=foo&sort=title_asc"
        url = f"{action_url}?q=foo&sort=title_asc"

        response = self.client.get(base_url + url_params)
        self.assertBulkActionForm(response, url)

    def _get_bulk_action_values(self, response):
        soup = self.make_soup(response.content.decode())
        select = soup.select_one('select[name="bulk_action"]')
        self.assertIsNotNone(select)
        return [opt["value"] for opt in select.select("option")]

    def test_allowed_bulk_actions(self):
        url = reverse("linkding:bookmarks.index")
        response = self.client.get(url)
        values = self._get_bulk_action_values(response)

        for v in ["bulk_read", "bulk_unread", "bulk_tag", "bulk_untag",
                   "bulk_refresh", "bulk_archive", "bulk_trash", "bulk_delete"]:
            self.assertIn(v, values)
        self.assertNotIn("bulk_share", values)
        self.assertNotIn("bulk_unshare", values)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_allowed_bulk_actions_with_html_snapshot_enabled(self):
        url = reverse("linkding:bookmarks.index")
        response = self.client.get(url)
        values = self._get_bulk_action_values(response)

        self.assertIn("bulk_snapshot", values)

    def test_allowed_bulk_actions_with_sharing_enabled(self):
        user_profile = self.user.profile
        user_profile.enable_sharing = True
        user_profile.save()

        url = reverse("linkding:bookmarks.index")
        response = self.client.get(url)
        values = self._get_bulk_action_values(response)

        self.assertIn("bulk_share", values)
        self.assertIn("bulk_unshare", values)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_allowed_bulk_actions_with_sharing_and_html_snapshot_enabled(self):
        user_profile = self.user.profile
        user_profile.enable_sharing = True
        user_profile.save()

        url = reverse("linkding:bookmarks.index")
        response = self.client.get(url)
        values = self._get_bulk_action_values(response)

        self.assertIn("bulk_share", values)
        self.assertIn("bulk_unshare", values)
        self.assertIn("bulk_snapshot", values)

    def test_apply_search_preferences(self):
        # no params
        response = self.client.post(reverse("linkding:bookmarks.index"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("linkding:bookmarks.index"))

        # some params
        response = self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "q": "foo",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, reverse("linkding:bookmarks.index") + "?q=foo&sort=title_asc"
        )

        # params with default value are removed
        response = self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "q": "foo",
                "user": "",
                "sort": BookmarkSearch.SORT_ADDED_DESC,
                "shared": BookmarkSearch.FILTER_SHARED_OFF,
                "unread": BookmarkSearch.FILTER_UNREAD_YES,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, reverse("linkding:bookmarks.index") + "?q=foo&unread=yes"
        )

        # page is removed
        response = self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "q": "foo",
                "page": "2",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, reverse("linkding:bookmarks.index") + "?q=foo&sort=title_asc"
        )

    DEFAULT_PREFERENCES = {
        "sort": BookmarkSearch.SORT_ADDED_DESC,
        "shared": BookmarkSearch.FILTER_SHARED_OFF,
        "unread": BookmarkSearch.FILTER_UNREAD_OFF,
        "tagged": BookmarkSearch.FILTER_TAGGED_OFF,
        "date_filter_by": BookmarkSearch.FILTER_DATE_OFF,
        "date_filter_type": BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
        "date_filter_relative_string": None,
    }

    def test_save_search_preferences(self):
        user_profile = self.user.profile

        # no params
        self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "save": "",
            },
        )
        user_profile.refresh_from_db()
        self.assertEqual(user_profile.search_preferences, self.DEFAULT_PREFERENCES)

        # with param
        self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "save": "",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        user_profile.refresh_from_db()
        self.assertEqual(
            user_profile.search_preferences,
            {**self.DEFAULT_PREFERENCES, "sort": BookmarkSearch.SORT_TITLE_ASC},
        )

        # add a param
        self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "save": "",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
                "unread": BookmarkSearch.FILTER_UNREAD_YES,
            },
        )
        user_profile.refresh_from_db()
        self.assertEqual(
            user_profile.search_preferences,
            {
                **self.DEFAULT_PREFERENCES,
                "sort": BookmarkSearch.SORT_TITLE_ASC,
                "unread": BookmarkSearch.FILTER_UNREAD_YES,
            },
        )

        # remove a param
        self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "save": "",
                "unread": BookmarkSearch.FILTER_UNREAD_YES,
            },
        )
        user_profile.refresh_from_db()
        self.assertEqual(
            user_profile.search_preferences,
            {**self.DEFAULT_PREFERENCES, "unread": BookmarkSearch.FILTER_UNREAD_YES},
        )

        # ignores non-preferences
        self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "save": "",
                "q": "foo",
                "user": "john",
                "page": "3",
                "sort": BookmarkSearch.SORT_TITLE_ASC,
            },
        )
        user_profile.refresh_from_db()
        self.assertEqual(
            user_profile.search_preferences,
            {**self.DEFAULT_PREFERENCES, "sort": BookmarkSearch.SORT_TITLE_ASC},
        )

    def test_url_encode_bookmark_actions_url(self):
        url = reverse("linkding:bookmarks.index") + "?q=%23foo"
        response = self.client.get(url)
        html = response.content.decode()
        soup = self.make_soup(html)
        actions_form = soup.select("form.bookmark-actions")[0]

        self.assertEqual(
            actions_form.attrs["action"],
            "/bookmarks/action?q=%23foo",
        )

    def test_encode_search_params(self):
        bookmark = self.setup_bookmark(description="alert('xss')")

        url = reverse("linkding:bookmarks.index") + "?q=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")
        self.assertContains(response, bookmark.url)

        url = reverse("linkding:bookmarks.index") + "?sort=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

        url = reverse("linkding:bookmarks.index") + "?unread=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

        url = reverse("linkding:bookmarks.index") + "?shared=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

        url = reverse("linkding:bookmarks.index") + "?user=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

        url = reverse("linkding:bookmarks.index") + "?page=alert(%27xss%27)"
        response = self.client.get(url)
        self.assertNotContains(response, "alert('xss')")

    def test_turbo_frame_details_modal_renders_details_modal_update(self):
        bookmark = self.setup_bookmark()
        url = reverse("linkding:bookmarks.index") + f"?bookmark_id={bookmark.id}"
        response = self.client.get(url, headers={"Turbo-Frame": "details-modal"})

        self.assertEqual(200, response.status_code)

        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(soup.select_one("turbo-frame#details-modal"))
        self.assertIsNone(soup.select_one("#bookmark-list-container"))
        self.assertIsNone(soup.select_one("#tag-cloud-container"))

    def test_does_not_include_rss_feed(self):
        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        feed = soup.select_one('head link[type="application/rss+xml"]')
        self.assertIsNone(feed)

    def test_list_bundles(self):
        books = self.setup_bundle(name="Books bundle", order=3)
        music = self.setup_bundle(name="Music bundle", order=1)
        tools = self.setup_bundle(name="Tools bundle", order=2)
        response = self.client.get(reverse("linkding:bookmarks.index"))
        html = response.content.decode()
        soup = self.make_soup(html)

        self.assertVisibleBundles(soup, [music, tools, books])

    def test_list_bundles_only_shows_user_owned_bundles(self):
        user_bundles = [self.setup_bundle(), self.setup_bundle(), self.setup_bundle()]
        other_user = self.setup_user()
        self.setup_bundle(user=other_user)
        self.setup_bundle(user=other_user)
        self.setup_bundle(user=other_user)

        response = self.client.get(reverse("linkding:bookmarks.index"))
        html = response.content.decode()
        soup = self.make_soup(html)

        self.assertVisibleBundles(soup, user_bundles)

    def test_sidebar_modules_respect_profile_order_and_enabled_state(self):
        self.setup_bundle(name="Bundle 1")
        self.setup_bookmark(url="https://example.com/a")
        self.setup_bookmark(url="https://another.example.com/b")
        tag = self.setup_tag(name="Tag 1")
        bookmark = self.setup_bookmark(url="https://tagged.example.com/c")
        bookmark.tags.add(tag)

        user_profile = self.get_or_create_test_user().profile
        user_profile.sidebar_modules = [
            {"key": "tags", "enabled": True},
            {"key": "summary", "enabled": True},
            {"key": "bundles", "enabled": False},
            {"key": "domains", "enabled": True},
        ]
        user_profile.save(update_fields=["sidebar_modules"])

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        module_keys = [
            element["data-sidebar-module"]
            for element in soup.select(".side-panel [data-sidebar-module]")
        ]
        self.assertEqual(module_keys, ["tags", "summary", "domains"])
        self.assertIsNone(
            soup.select_one(".side-panel [data-sidebar-module='bundles']")
        )

    def test_legacy_hide_bundles_still_applies_without_sidebar_configuration(self):
        user_profile = self.get_or_create_test_user().profile
        user_profile.hide_bundles = True
        user_profile.sidebar_modules = []
        user_profile.save(update_fields=["hide_bundles", "sidebar_modules"])

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        self.assertIsNone(
            soup.select_one(".side-panel [data-sidebar-module='bundles']")
        )

    def test_list_domains_without_normalization_rules(self):
        self.setup_bookmark(
            url="https://example.com/alpha", favicon_file="https_example_com.png"
        )
        self.setup_bookmark(
            url="https://sub.example.com/beta",
            favicon_file="https_sub_example_com.png",
        )

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleDomains(
            response,
            [
                {
                    "host": "example.com",
                    "label": "example.com",
                    "count": 1,
                    "level": 0,
                    "favicon": "https_example_com.png",
                },
                {
                    "host": "sub.example.com",
                    "label": "sub.example.com",
                    "count": 1,
                    "level": 0,
                    "favicon": "https_sub_example_com.png",
                },
            ],
        )

    def test_list_domains_with_custom_domain_hierarchy(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn"
        profile.save()

        self.setup_bookmark(
            url="https://docs.feishu.cn/123",
            favicon_file="https_docs_feishu_cn.png",
        )
        self.setup_bookmark(
            url="https://feishu.cn/blog", favicon_file="https_feishu_cn.png"
        )
        self.setup_bookmark(
            url="https://131312.feishu.cn",
            favicon_file="https_131312_feishu_cn.png",
        )

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleDomains(
            response,
            [
                {
                    "host": "feishu.cn",
                    "label": "feishu.cn",
                    "count": 3,
                    "level": 0,
                    "favicon": "https_feishu_cn.png",
                },
                {
                    "host": "docs.feishu.cn",
                    "label": "docs.feishu.cn",
                    "count": 1,
                    "level": 1,
                    "favicon": "https_docs_feishu_cn.png",
                },
            ],
        )

    def test_domain_links_replace_existing_domain_filter_and_highlight_selection(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn"
        profile.save()

        self.setup_bookmark(url="https://docs.feishu.cn/123", title="hello docs")
        self.setup_bookmark(url="https://feishu.cn/blog", title="hello root")

        response = self.client.get(
            reverse("linkding:bookmarks.index")
            + "?q=hello+domain:(docs.feishu.cn+|+.docs.feishu.cn)"
        )

        self.assertVisibleDomains(
            response,
            [
                {
                    "host": "feishu.cn",
                    "label": "feishu.cn",
                    "count": 1,
                    "level": 0,
                },
                {
                    "host": "docs.feishu.cn",
                    "label": "docs.feishu.cn",
                    "count": 1,
                    "level": 1,
                    "selected": True,
                },
            ],
        )

        soup = self.make_soup(response.content.decode())
        root_link = soup.select_one('li[data-domain-host="feishu.cn"] a')
        self.assertIsNotNone(root_link)
        self.assertEqual(
            root_link.attrs["href"], "?q=hello+domain%3A%28feishu.cn+%7C+.feishu.cn%29"
        )

        selected_link = soup.select_one('li[data-domain-host="docs.feishu.cn"] a')
        self.assertIsNotNone(selected_link)
        self.assertEqual(selected_link.attrs["href"], "?q=hello")
        selected_prefix = selected_link.select_one(".domain-selection-prefix")
        self.assertIsNotNone(selected_prefix)
        self.assertEqual(selected_prefix.text.strip(), "-")

    def test_selected_parent_domain_renders_prefix_before_favicon(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn"
        profile.save()

        self.setup_bookmark(url="https://docs.feishu.cn/123", title="hello docs")
        self.setup_bookmark(url="https://feishu.cn/blog", title="hello root")

        response = self.client.get(
            reverse("linkding:bookmarks.index") + "?q=domain:(feishu.cn+|+.feishu.cn)"
        )

        soup = self.make_soup(response.content.decode())
        selected_main = soup.select_one(
            'li[data-domain-host="feishu.cn"] [data-domain-primary] .domain-link-main'
        )
        self.assertIsNotNone(selected_main)

        child_classes = [
            child.attrs.get("class", [None])[0]
            for child in selected_main.find_all(recursive=False)
        ]
        self.assertEqual(child_classes[0], "domain-selection-prefix")

    def test_domain_groups_are_sorted_by_root_bookmark_count_desc(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn\ngithub.com"
        profile.save()

        self.setup_bookmark(url="https://docs.feishu.cn/doc-1")
        self.setup_bookmark(url="https://feishu.cn/doc-2")
        self.setup_bookmark(url="https://sub.feishu.cn/doc-3")
        self.setup_bookmark(url="https://github.com/repo-1")
        self.setup_bookmark(url="https://github.com/repo-2")

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleDomains(
            response,
            [
                {
                    "host": "feishu.cn",
                    "label": "feishu.cn",
                    "count": 3,
                    "level": 0,
                },
                {
                    "host": "docs.feishu.cn",
                    "label": "docs.feishu.cn",
                    "count": 1,
                    "level": 1,
                },
                {
                    "host": "github.com",
                    "label": "github.com",
                    "count": 2,
                    "level": 0,
                },
            ],
        )

    def test_domain_children_are_sorted_by_bookmark_count_desc(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = (
            "blog.feishu.cn\ndocs.feishu.cn\nfeishu.cn\ngithub.com"
        )
        profile.save()

        self.setup_bookmark(url="https://blog.feishu.cn/post-1")
        self.setup_bookmark(url="https://blog.feishu.cn/post-2")
        self.setup_bookmark(url="https://docs.feishu.cn/doc-1")
        self.setup_bookmark(url="https://feishu.cn/root-1")
        self.setup_bookmark(url="https://github.com/repo-1")
        self.setup_bookmark(url="https://github.com/repo-2")

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertVisibleDomains(
            response,
            [
                {
                    "host": "feishu.cn",
                    "label": "feishu.cn",
                    "count": 4,
                    "level": 0,
                },
                {
                    "host": "blog.feishu.cn",
                    "label": "blog.feishu.cn",
                    "count": 2,
                    "level": 1,
                },
                {
                    "host": "docs.feishu.cn",
                    "label": "docs.feishu.cn",
                    "count": 1,
                    "level": 1,
                },
                {
                    "host": "github.com",
                    "label": "github.com",
                    "count": 2,
                    "level": 0,
                },
            ],
        )

    def test_domain_tree_renders_nested_children_and_toggle_for_parent_nodes(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn"
        profile.save()

        self.setup_bookmark(url="https://docs.feishu.cn/doc-1")
        self.setup_bookmark(url="https://feishu.cn/root-1")

        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        root_item = soup.select_one('li[data-domain-host="feishu.cn"]')
        self.assertIsNotNone(root_item)
        self.assertEqual(root_item.attrs["data-domain-level"], "0")

        toggle_button = root_item.select_one(
            ":scope > .domain-row .domain-action .folder-toggle"
        )
        self.assertIsNotNone(toggle_button)

        nested_children = root_item.select_one(":scope > ul.domain-children")
        self.assertIsNotNone(nested_children)

        child_item = nested_children.select_one('li[data-domain-host="docs.feishu.cn"]')
        self.assertIsNotNone(child_item)
        self.assertEqual(child_item.attrs["data-domain-level"], "1")

    def test_domain_parent_rows_use_right_side_action_layout(self):
        profile = self.get_or_create_test_user().profile
        profile.custom_domain_root = "docs.feishu.cn\nfeishu.cn"
        profile.save()

        self.setup_bookmark(url="https://docs.feishu.cn/doc-1")
        self.setup_bookmark(url="https://feishu.cn/root-1")
        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        root_item = soup.select_one('li[data-domain-host="feishu.cn"]')
        self.assertIsNotNone(root_item)

        row = root_item.select_one(":scope > .domain-row")
        self.assertIsNotNone(row)

        content = row.select_one(":scope > .domain-content")
        self.assertIsNotNone(content)
        self.assertIsNotNone(content.select_one("a.domain-link"))

        action = row.select_one(":scope > .domain-action")
        self.assertIsNotNone(action)
        self.assertIsNotNone(action.select_one(".folder-toggle"))

        # Regression: the toggle should not live inline before the link text anymore.
        old_inline_toggle = root_item.select_one(":scope > .domain-node .folder-toggle")
        self.assertIsNone(old_inline_toggle)

    def test_domain_menu_shows_default_actions(self):
        response = self.client.get(reverse("linkding:bookmarks.index"))
        soup = self.make_soup(response.content.decode())

        menu = soup.select_one('[aria-label="Domains menu"]')
        self.assertIsNotNone(menu)

        menu_links = soup.select(
            "section[aria-labelledby='domains-heading'] .menu-link"
        )
        menu_texts = [link.text.strip() for link in menu_links]

        self.assertEqual(menu_texts, ["Icon mode", "All domains"])
        self.assertEqual(
            menu_links[0].attrs["href"], reverse("linkding:bookmarks.index")
        )
        self.assertEqual(
            menu_links[1].attrs["href"], reverse("linkding:bookmarks.index")
        )

    def test_domain_search_forms_do_not_render_domain_state_inputs(self):
        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            **self.get_domain_headers(view_mode="icon", compact_mode="0"),
        )
        soup = self.make_soup(response.content.decode())

        search_form = soup.select_one("form#search")
        self.assertIsNotNone(search_form)
        self.assertIsNone(search_form.select_one('input[name="domain_view"]'))
        self.assertIsNone(search_form.select_one('input[name="domain_compact"]'))

        search_preferences_form = soup.select_one("form#search_preferences")
        self.assertIsNotNone(search_preferences_form)
        self.assertIsNone(
            search_preferences_form.select_one('input[name="domain_view"]')
        )
        self.assertIsNone(
            search_preferences_form.select_one('input[name="domain_compact"]')
        )

    def test_domain_menu_shows_full_mode_action_when_icon_mode_is_enabled(self):
        self.setup_bookmark(url="https://example.com/alpha")

        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            **self.get_domain_headers(view_mode="icon"),
        )
        soup = self.make_soup(response.content.decode())

        menu_links = soup.select(
            "section[aria-labelledby='domains-heading'] .menu-link"
        )
        menu_texts = [link.text.strip() for link in menu_links]

        self.assertEqual(menu_texts, ["Full mode", "All domains"])
        self.assertEqual(
            menu_links[0].attrs["href"], reverse("linkding:bookmarks.index")
        )
        self.assertEqual(
            menu_links[1].attrs["href"], reverse("linkding:bookmarks.index")
        )

        domain_list = soup.select_one("ul.domain-menu")
        self.assertIsNotNone(domain_list)
        self.assertEqual(domain_list.attrs["data-domain-view-mode"], "icon")

        self.assertEqual(menu_links[0].attrs["data-domain-target-view-mode"], "full")
        self.assertEqual(menu_links[1].attrs["data-domain-target-compact-mode"], "0")

        root_item = domain_list.select_one('li[data-domain-host="example.com"]')
        self.assertIsNotNone(root_item)
        root_summary = root_item.select_one(".domain-root-icon-summary")
        self.assertIsNotNone(root_summary)
        self.assertIsNotNone(root_summary.select_one(".favicon"))
        count = root_summary.select_one(".count.domain-count-icon")
        self.assertIsNotNone(count)
        self.assertEqual(count.text.strip(), "1")

    def test_domain_compact_mode_groups_non_top_roots_under_other(self):
        for index in range(17):
            for count in range(17 - index):
                self.setup_bookmark(
                    url=f"https://domain-{index}.example.com/{count}",
                    favicon_file=f"https_domain_{index}_example_com.png",
                )

        response = self.client.get(reverse("linkding:bookmarks.index"))

        expected_domains = [
            {
                "host": f"domain-{index}.example.com",
                "label": f"domain-{index}.example.com",
                "count": 17 - index,
                "level": 0,
                "favicon": f"https_domain_{index}_example_com.png",
            }
            for index in range(10)
        ] + [
            {
                "host": "__other__",
                "label": "Other",
                "count": 28,
                "level": 0,
                "group": True,
                "clickable": False,
            },
            {
                "host": "domain-10.example.com",
                "label": "domain-10.example.com",
                "count": 7,
                "level": 1,
                "favicon": "https_domain_10_example_com.png",
            },
            {
                "host": "domain-11.example.com",
                "label": "domain-11.example.com",
                "count": 6,
                "level": 1,
                "favicon": "https_domain_11_example_com.png",
            },
            {
                "host": "domain-12.example.com",
                "label": "domain-12.example.com",
                "count": 5,
                "level": 1,
                "favicon": "https_domain_12_example_com.png",
            },
            {
                "host": "domain-13.example.com",
                "label": "domain-13.example.com",
                "count": 4,
                "level": 1,
                "favicon": "https_domain_13_example_com.png",
            },
            {
                "host": "domain-14.example.com",
                "label": "domain-14.example.com",
                "count": 3,
                "level": 1,
                "favicon": "https_domain_14_example_com.png",
            },
            {
                "host": "domain-15.example.com",
                "label": "domain-15.example.com",
                "count": 2,
                "level": 1,
                "favicon": "https_domain_15_example_com.png",
            },
            {
                "host": "domain-16.example.com",
                "label": "domain-16.example.com",
                "count": 1,
                "level": 1,
                "favicon": "https_domain_16_example_com.png",
            },
        ]

        self.assertVisibleDomains(response, expected_domains)

        soup = self.make_soup(response.content.decode())
        domain_list = soup.select_one("ul.domain-menu")
        self.assertIsNotNone(domain_list)
        self.assertEqual(domain_list.attrs["data-domain-compact-mode"], "true")

        root_hosts = [
            item.attrs["data-domain-host"]
            for item in domain_list.select(":scope > li.domain-menu-item")
        ]
        self.assertEqual(root_hosts[-1], "__other__")

        menu_links = soup.select(
            "section[aria-labelledby='domains-heading'] .menu-link"
        )
        menu_texts = [link.text.strip() for link in menu_links]
        self.assertEqual(menu_texts, ["Icon mode", "All domains"])

    def test_domain_compact_icon_mode_uses_icon_layout_for_other_children(self):
        for index in range(17):
            for count in range(17 - index):
                self.setup_bookmark(
                    url=f"https://domain-{index}.example.com/{count}",
                    favicon_file=f"https_domain_{index}_example_com.png",
                )

        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            **self.get_domain_headers(view_mode="icon"),
        )
        soup = self.make_soup(response.content.decode())

        other_item = soup.select_one('li[data-domain-host="__other__"]')
        self.assertIsNotNone(other_item)

        other_children = other_item.select_one(
            ":scope > ul.domain-children.domain-children-icon"
        )
        self.assertIsNotNone(other_children)

        other_child = other_children.select_one(
            'li[data-domain-host="domain-10.example.com"]'
        )
        self.assertIsNotNone(other_child)
        self.assertIsNotNone(
            other_child.select_one(":scope > .domain-row.domain-row-icon")
        )
        self.assertIsNotNone(
            other_child.select_one(":scope > .domain-row .domain-root-icon-summary")
        )

    def test_sidebar_summary_renders_compact_stats_and_calendar_shell(self):
        with translation.override("zh-hans"):
            self.set_profile_language("zh-hans")
            today = timezone.localdate()
            joined_at = timezone.make_aware(
                timezone.datetime(
                    today.year, today.month, max(today.day - 20, 1), 12, 0
                )
            ) - timezone.timedelta(days=70)
            self.user.date_joined = joined_at
            self.user.save(update_fields=["date_joined"])

            oldest_day = today - timezone.timedelta(days=30)
            recent_day = today - timezone.timedelta(days=4)
            oldest_added = timezone.make_aware(
                timezone.datetime(
                    oldest_day.year, oldest_day.month, oldest_day.day, 12, 0
                )
            )
            recent_added = timezone.make_aware(
                timezone.datetime(
                    recent_day.year, recent_day.month, recent_day.day, 12, 0
                )
            )

            alpha = self.setup_tag(name="alpha")
            self.setup_tag(name="beta")
            self.setup_bookmark(
                title="Old bookmark",
                tags=[alpha],
                added=oldest_added,
                modified=oldest_added,
            )
            self.setup_bookmark(
                title="Unread bookmark",
                unread=True,
                added=recent_added,
                modified=recent_added,
            )

            response = self.client.get(reverse("linkding:bookmarks.index"))
            soup = self.make_soup(response.content.decode())

            summary = soup.select_one("section[ld-sidebar-user-summary]")
            self.assertIsNotNone(summary)
            self.assertTrue(summary.has_attr("ld-collapse-button"))
            self.assertEqual(
                summary.attrs["data-toggle-storage-key"], "userSummarySectionState"
            )
            self.assertEqual(summary.attrs["data-summary-mode"], "calendar")
            self.assertEqual(
                summary.attrs["data-summary-month"], today.strftime("%Y-%m")
            )

            heading = summary.select_one("#sidebar-user-summary-heading")
            self.assertIsNotNone(heading)
            self.assertEqual(heading.text.strip(), self.user.username)
            self.assertIsNotNone(summary.select_one(".section-header .section-toggle"))
            self.assertIsNotNone(summary.select_one(".section-header .dropdown"))
            self.assertIsNone(summary.select_one(".summary-avatar"))
            self.assertNotIn("Collection overview", summary.get_text(" ", strip=True))
            menu_links = [
                item.text.strip()
                for item in summary.select(".section-header .dropdown .menu-link")
            ]
            self.assertIn("显示星期", menu_links)
            self.assertIn("显示总结", menu_links)

            expected_collection_days = max(
                (today - timezone.localtime(self.user.date_joined).date()).days,
                (today - oldest_day).days,
            )
            primary_stats = summary.select(".summary-primary-stats [data-summary-stat]")
            self.assertEqual(
                [item.attrs["data-summary-stat"] for item in primary_stats],
                ["bookmarks", "tags", "collection-days"],
            )
            self.assertEqual(len(primary_stats), 3)
            self.assertEqual(
                summary.select_one("[data-summary-stat='bookmarks']")
                .select_one(".summary-metric-value")
                .text.strip(),
                "2",
            )
            self.assertEqual(
                summary.select_one("[data-summary-stat='tags']")
                .select_one(".summary-metric-value")
                .text.strip(),
                "2",
            )
            self.assertEqual(
                summary.select_one("[data-summary-stat='collection-days']")
                .select_one(".summary-metric-value")
                .text.strip(),
                str(expected_collection_days),
            )
            self.assertEqual(
                summary.select_one("[data-summary-stat='collection-days']")
                .select_one(".summary-metric-label")
                .text.strip(),
                "天",
            )
            collection_days_toggle = summary.select_one(
                "[data-summary-collection-toggle]"
            )
            self.assertIsNotNone(collection_days_toggle)
            self.assertEqual(
                collection_days_toggle.attrs["data-summary-collection-start"],
                timezone.localtime(self.user.date_joined).date().strftime("%Y/%m/%d"),
            )
            self.assertEqual(
                collection_days_toggle.attrs["data-summary-collection-prefix"],
                "自",
            )
            collection_start_summary = summary.select_one(
                "[data-summary-collection-start-summary]"
            )
            self.assertIsNotNone(collection_start_summary)
            self.assertEqual(
                collection_start_summary.get_text("", strip=True),
                f"自{timezone.localtime(self.user.date_joined).date().strftime('%Y/%m/%d')}",
            )
            self.assertIsNone(
                collection_days_toggle.select_one(".summary-metric-face-alternate")
            )
            self.assertIsNone(
                collection_days_toggle.select_one(".summary-info-popover")
            )
            self.assertIsNone(summary.select_one("[data-summary-stat='unread']"))
            self.assertIsNone(summary.select_one("[data-summary-stat='untagged']"))

            self.assertIsNone(
                summary.select_one("[data-summary-mode-toggle='calendar']")
            )
            self.assertIsNotNone(
                summary.select_one("[data-summary-mode-toggle='heatmap']")
            )
            self.assertIsNone(summary.select_one("[data-summary-month-picker-trigger]"))
            year_picker_trigger = summary.select_one(
                "[data-summary-month-year-picker-trigger]"
            )
            month_picker_trigger = summary.select_one(
                "[data-summary-month-number-picker-trigger]"
            )
            self.assertIsNotNone(year_picker_trigger)
            self.assertIsNotNone(month_picker_trigger)
            self.assertEqual(year_picker_trigger.text.strip(), str(today.year))
            self.assertEqual(month_picker_trigger.text.strip(), f"{today.month:02d}")
            self.assertIsNone(
                summary.select_one(
                    "form[data-summary-month-picker] select[name='summary_month']"
                )
            )
            self.assertIsNone(summary.select_one("form[data-summary-month-picker]"))
            year_picker_options = summary.select("[data-summary-month-year-option]")
            month_picker_options = summary.select("[data-summary-month-option]")
            self.assertTrue(
                any(
                    option.text.strip() == str(today.year)
                    for option in year_picker_options
                )
            )
            self.assertTrue(
                any(
                    option.text.strip() == f"{today.month:02d}"
                    for option in month_picker_options
                )
            )
            self.assertIsNotNone(summary.select_one("[data-summary-range-url]"))
            self.assertIsNone(summary.select_one("[data-summary-range-hint]"))
            activity_disclosure = summary.select_one(
                "[data-summary-activity-disclosure]"
            )
            self.assertIsNotNone(activity_disclosure)
            self.assertTrue(activity_disclosure.has_attr("ld-collapse-button"))
            self.assertEqual(
                activity_disclosure.attrs["data-toggle-storage-key"],
                "userSummaryActivityState",
            )
            self.assertIsNotNone(summary.select_one("[data-summary-activity-toggle]"))
            self.assertIsNotNone(summary.select_one("[data-summary-calendar]"))
            self.assertIsNone(summary.select_one("[data-summary-heatmap]"))
            self.assertIsNone(summary.select_one("[data-summary-activity-summary]"))
            self.assertIsNone(summary.select_one("[data-summary-toolbar-action]"))
            self.assertEqual(summary.select(".summary-weekday"), [])

            recent_day_iso = recent_day.isoformat()
            bookmarked_day = summary.select_one(
                f"[data-summary-calendar-day='{recent_day_iso}']"
            )
            self.assertIsNotNone(bookmarked_day)
            self.assertIsNotNone(bookmarked_day.select_one(".summary-day-number"))
            self.assertIsNotNone(bookmarked_day.select_one("[data-summary-day-dot]"))
            self.assertEqual(
                bookmarked_day["title"],
                f"1 个书签 - {recent_day.strftime('%Y/%m/%d')}",
            )

            empty_day = next(
                (
                    day
                    for day in summary.select("[data-summary-calendar-day][title]")
                    if day["title"].startswith("0 个书签 - ")
                    and "is-outside-month" not in day.get("class", [])
                ),
                None,
            )
            self.assertIsNotNone(empty_day)
            self.assertIn("is-empty-day", empty_day.get("class", []))
            empty_day_dot = empty_day.select_one("[data-summary-day-dot]")
            self.assertIsNotNone(empty_day_dot)
            self.assertIn("is-empty", empty_day_dot.get("class", []))

    def test_sidebar_summary_builds_shortcut_and_calendar_urls(self):
        today = timezone.localdate()
        current_day = today.replace(day=max(today.day - 2, 1))
        current_added = timezone.make_aware(
            timezone.datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                12,
                0,
            )
        )
        self.setup_bookmark(
            title="Sidebar bookmark",
            unread=True,
            added=current_added,
            modified=current_added,
        )

        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            HTTP_X_LINKDING_SUMMARY_SHOW_WEEKDAYS="1",
            HTTP_X_LINKDING_SUMMARY_SHOW_DETAILS="1",
            **self.get_domain_headers(view_mode="icon"),
        )
        soup = self.make_soup(response.content.decode())
        summary = soup.select_one("section[ld-sidebar-user-summary]")
        self.assertIsNotNone(summary)

        bookmarks_link = summary.select_one("[data-summary-stat='bookmarks'][href]")
        self.assertIsNotNone(bookmarks_link)
        bookmarks_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(bookmarks_link["href"]).query
        )
        self.assertNotIn("domain_view", bookmarks_query)
        self.assertNotIn("domain_compact", bookmarks_query)
        self.assertNotIn("unread", bookmarks_query)
        self.assertNotIn("tagged", bookmarks_query)
        self.assertNotIn("summary_mode", bookmarks_query)
        self.assertNotIn("summary_month", bookmarks_query)
        self.assertNotIn("summary_week", bookmarks_query)
        self.assertNotIn("summary_show_weekdays", bookmarks_query)
        self.assertNotIn("summary_show_details", bookmarks_query)

        day_link = summary.select_one(
            f"[data-summary-calendar-day='{current_day.isoformat()}'][href]"
        )
        self.assertIsNotNone(day_link)
        day_query = urllib.parse.parse_qs(urllib.parse.urlsplit(day_link["href"]).query)
        self.assertEqual(
            day_query["date_filter_by"], [BookmarkSearch.FILTER_DATE_BY_ADDED]
        )
        self.assertEqual(
            day_query["date_filter_type"], [BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE]
        )
        self.assertEqual(day_query["date_filter_start"], [current_day.isoformat()])
        self.assertEqual(day_query["date_filter_end"], [current_day.isoformat()])
        self.assertNotIn("summary_mode", day_query)
        self.assertNotIn("summary_month", day_query)
        self.assertNotIn("summary_week", day_query)
        self.assertNotIn("summary_show_weekdays", day_query)
        self.assertNotIn("summary_show_details", day_query)
        self.assertEqual(
            day_link["data-summary-target-month"], current_day.strftime("%Y-%m")
        )

        range_url = summary.select_one("[data-summary-range-url]")[
            "data-summary-range-url"
        ]
        range_query = urllib.parse.parse_qs(urllib.parse.urlsplit(range_url).query)
        self.assertEqual(
            range_query["date_filter_by"], [BookmarkSearch.FILTER_DATE_BY_ADDED]
        )
        self.assertEqual(
            range_query["date_filter_type"], [BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE]
        )
        self.assertNotIn("summary_mode", range_query)
        self.assertNotIn("summary_month", range_query)
        self.assertNotIn("summary_week", range_query)
        self.assertNotIn("summary_show_weekdays", range_query)
        self.assertNotIn("summary_show_details", range_query)

    def test_sidebar_summary_renders_month_heatmap_mode(self):
        with translation.override("zh-hans"):
            self.set_profile_language("zh-hans")
            today = timezone.localdate()
            earliest_day = today - timezone.timedelta(days=400)
            oldest_visible_day = today - timezone.timedelta(days=40)
            selected_day = today - timezone.timedelta(days=10)
            earliest_added = timezone.make_aware(
                timezone.datetime(
                    earliest_day.year,
                    earliest_day.month,
                    earliest_day.day,
                    12,
                    0,
                )
            )
            oldest_visible_added = timezone.make_aware(
                timezone.datetime(
                    oldest_visible_day.year,
                    oldest_visible_day.month,
                    oldest_visible_day.day,
                    12,
                    0,
                )
            )
            self.setup_bookmark(
                title="Earliest heatmap bookmark",
                added=earliest_added,
                modified=earliest_added,
            )
            self.setup_bookmark(
                title="Older visible heatmap bookmark",
                added=oldest_visible_added,
                modified=oldest_visible_added,
            )
            activity_levels = [
                (today - timezone.timedelta(days=5), 1, 1),
                (today - timezone.timedelta(days=4), 4, 2),
                (today - timezone.timedelta(days=3), 7, 3),
                (today - timezone.timedelta(days=2), 10, 4),
                (today - timezone.timedelta(days=1), 16, 5),
                (today, 21, 6),
            ]
            for value, count, _expected_level in activity_levels:
                added = timezone.make_aware(
                    timezone.datetime(value.year, value.month, value.day, 12, 0)
                )
                for index in range(count):
                    self.setup_bookmark(
                        title=f"Heatmap bookmark {value.isoformat()} #{index}",
                        added=added,
                        modified=added,
                    )

            response = self.client.get(
                reverse("linkding:bookmarks.index"),
                **self.get_summary_headers(mode="heatmap"),
            )
            soup = self.make_soup(response.content.decode())
            summary = soup.select_one("section[ld-sidebar-user-summary]")
            self.assertIsNotNone(summary)
            self.assertEqual(summary.attrs["data-summary-mode"], "heatmap")

            self.assertIsNone(
                summary.select_one("[data-summary-mode-toggle='heatmap']")
            )
            self.assertIsNotNone(
                summary.select_one("[data-summary-mode-toggle='calendar']")
            )
            self.assertIsNone(summary.select_one("[data-summary-month-picker-trigger]"))
            self.assertIsNone(summary.select_one(".summary-week-label"))
            current_week_start = today - timezone.timedelta(
                days=((today.weekday() + 1) % 7)
            )
            current_week_key_date = current_week_start + timezone.timedelta(days=1)
            expected_year = str(current_week_key_date.isocalendar().year)
            expected_week = f"W{current_week_key_date.isocalendar().week:02d}"
            self.assertEqual(
                summary.select_one(
                    "[data-summary-week-year-picker-trigger]"
                ).text.strip(),
                expected_year,
            )
            self.assertEqual(
                summary.select_one(
                    "[data-summary-week-number-picker-trigger]"
                ).text.strip(),
                expected_week,
            )

            self.assertIsNotNone(summary.select_one("[data-summary-heatmap]"))
            self.assertIsNone(summary.select_one("[data-summary-calendar]"))
            self.assertIsNone(summary.select_one("[data-summary-toolbar-action]"))
            self.assertEqual(
                summary.select_one("[data-summary-heatmap]").attrs[
                    "data-summary-heatmap-total-columns"
                ],
                "15",
            )
            self.assertIsNone(summary.select_one("[data-summary-activity-summary]"))
            self.assertEqual(len(summary.select(".summary-heatmap-week")), 15)
            self.assertEqual(len(summary.select(".summary-heatmap-week-number")), 15)
            self.assertEqual(summary.select(".summary-heatmap-weekday"), [])
            self.assertEqual(
                summary.select(".summary-heatmap-week-number")[-1].text.strip(),
                f"{current_week_key_date.isocalendar().week:02d}",
            )

            year_options = summary.select("[data-summary-week-year-option]")
            year_option_values = [item.text.strip() for item in year_options]
            self.assertIn(expected_year, year_option_values)
            self.assertIn(str(earliest_day.isocalendar().year), year_option_values)

            older_year_option = next(
                item
                for item in year_options
                if item.text.strip() == str(earliest_day.isocalendar().year)
            )
            older_year_query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(older_year_option["href"]).query
            )
            self.assertNotIn("summary_mode", older_year_query)
            self.assertNotIn("summary_week", older_year_query)
            self.assertTrue(
                older_year_option["data-summary-target-week"].startswith(
                    f"{earliest_day.isocalendar().year}-W"
                )
            )

            week_options = summary.select("[data-summary-week-option]")
            self.assertTrue(
                any(item.text.strip() == expected_week for item in week_options)
            )

            oldest_visible_heatmap_day = summary.select_one(
                f"[data-summary-heatmap-day='{oldest_visible_day.isoformat()}']"
            )
            self.assertIsNotNone(oldest_visible_heatmap_day)

            for value, count, expected_level in activity_levels:
                heatmap_day = summary.select_one(
                    f"[data-summary-heatmap-day='{value.isoformat()}']"
                )
                self.assertIsNotNone(heatmap_day)
                self.assertEqual(
                    heatmap_day.attrs["data-summary-activity-level"],
                    str(expected_level),
                )
                self.assertIsNone(heatmap_day.select_one(".summary-day-number"))
                self.assertIsNone(heatmap_day.select_one("[data-summary-day-dot]"))
                self.assertEqual(
                    heatmap_day["title"],
                    f"{count} 个书签 - {value.strftime('%Y/%m/%d')}",
                )

            heatmap_day = summary.select_one(
                f"[data-summary-heatmap-day='{selected_day.isoformat()}']"
            )

            heatmap_query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(heatmap_day["href"]).query
            )
            selected_week_start = selected_day - timezone.timedelta(
                days=((selected_day.weekday() + 1) % 7)
            )
            selected_week_year, selected_week_number, _ = (
                selected_week_start + timezone.timedelta(days=1)
            ).isocalendar()
            self.assertNotIn("summary_mode", heatmap_query)
            self.assertNotIn("summary_week", heatmap_query)
            self.assertEqual(
                heatmap_query["date_filter_start"],
                [selected_week_start.isoformat()],
            )
            self.assertEqual(
                heatmap_query["date_filter_end"],
                [(selected_week_start + timezone.timedelta(days=6)).isoformat()],
            )
            self.assertEqual(
                heatmap_day["data-summary-target-week"],
                f"{selected_week_year}-W{selected_week_number:02d}",
            )

    def test_sidebar_summary_shows_calendar_and_heatmap_toolbar_actions(self):
        today = timezone.localdate()
        previous_month_day = today - timezone.timedelta(days=40)
        previous_month_added = timezone.make_aware(
            timezone.datetime(
                previous_month_day.year,
                previous_month_day.month,
                previous_month_day.day,
                12,
                0,
            )
        )
        current_day = today - timezone.timedelta(days=2)
        current_added = timezone.make_aware(
            timezone.datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                12,
                0,
            )
        )
        self.setup_bookmark(
            title="Previous month bookmark",
            added=previous_month_added,
            modified=previous_month_added,
        )
        self.setup_bookmark(
            title="Current bookmark",
            added=current_added,
            modified=current_added,
        )

        calendar_response = self.client.get(
            reverse("linkding:bookmarks.index"),
            **self.get_summary_headers(month=previous_month_day.strftime("%Y-%m")),
        )
        calendar_summary = self.make_soup(
            calendar_response.content.decode()
        ).select_one("section[ld-sidebar-user-summary]")
        self.assertIsNotNone(calendar_summary)
        current_month_action = calendar_summary.select_one(
            "[data-summary-toolbar-action='current-month']"
        )
        self.assertIsNotNone(current_month_action)
        current_month_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(current_month_action["href"]).query
        )
        self.assertNotIn("summary_month", current_month_query)
        self.assertNotIn("summary_mode", current_month_query)
        self.assertEqual(
            current_month_action["data-summary-target-month"], today.strftime("%Y-%m")
        )
        self.assertEqual(current_month_action["data-summary-target-mode"], "calendar")

        previous_week = (
            today
            - timezone.timedelta(days=((today.weekday() + 1) % 7))
            - timezone.timedelta(days=7)
        )
        previous_week_year, previous_week_number, _ = (
            previous_week + timezone.timedelta(days=1)
        ).isocalendar()
        heatmap_response = self.client.get(
            reverse("linkding:bookmarks.index"),
            **self.get_summary_headers(
                mode="heatmap",
                week=f"{previous_week_year}-W{previous_week_number:02d}",
            ),
        )
        heatmap_summary = self.make_soup(heatmap_response.content.decode()).select_one(
            "section[ld-sidebar-user-summary]"
        )
        self.assertIsNotNone(heatmap_summary)
        current_week_action = heatmap_summary.select_one(
            "[data-summary-toolbar-action='current-week']"
        )
        self.assertIsNotNone(current_week_action)
        current_week_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(current_week_action["href"]).query
        )
        self.assertNotIn("summary_mode", current_week_query)
        current_week_start = today - timezone.timedelta(
            days=((today.weekday() + 1) % 7)
        )
        current_week_key_date = current_week_start + timezone.timedelta(days=1)
        self.assertNotIn("summary_week", current_week_query)
        self.assertEqual(current_week_action["data-summary-target-mode"], "heatmap")
        self.assertEqual(
            current_week_action["data-summary-target-week"],
            (
                f"{current_week_key_date.isocalendar().year}-W"
                f"{current_week_key_date.isocalendar().week:02d}"
            ),
        )

    def test_sidebar_summary_marks_selected_absolute_range(self):
        today = timezone.localdate()
        first_day = today.replace(day=1)
        start_day = first_day
        end_day = first_day + timezone.timedelta(days=2)

        for offset in range(6, 1, -1):
            bookmark_day = today - timezone.timedelta(days=offset)
            bookmark_added = timezone.make_aware(
                timezone.datetime(
                    bookmark_day.year,
                    bookmark_day.month,
                    bookmark_day.day,
                    12,
                    0,
                )
            )
            self.setup_bookmark(
                title=f"Bookmark {offset}",
                added=bookmark_added,
                modified=bookmark_added,
            )

        response = self.client.get(
            reverse("linkding:bookmarks.index")
            + "?"
            + urllib.parse.urlencode(
                {
                    "date_filter_by": BookmarkSearch.FILTER_DATE_BY_ADDED,
                    "date_filter_type": BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
                    "date_filter_start": start_day.isoformat(),
                    "date_filter_end": end_day.isoformat(),
                }
            ),
            **self.get_summary_headers(month=today.strftime("%Y-%m")),
        )
        soup = self.make_soup(response.content.decode())
        summary = soup.select_one("section[ld-sidebar-user-summary]")
        self.assertIsNotNone(summary)

        start_cell = summary.select_one(
            f"[data-summary-calendar-day='{start_day.isoformat()}']"
        )
        end_cell = summary.select_one(
            f"[data-summary-calendar-day='{end_day.isoformat()}']"
        )
        middle_day = start_day + timezone.timedelta(days=1)
        middle_cell = summary.select_one(
            f"[data-summary-calendar-day='{middle_day.isoformat()}']"
        )

        self.assertIsNotNone(start_cell)
        self.assertIsNotNone(end_cell)
        self.assertIsNotNone(middle_cell)
        self.assertIn("is-range-start", start_cell.attrs["class"])
        self.assertIn("is-range-end", end_cell.attrs["class"])
        self.assertIn("is-in-range", middle_cell.attrs["class"])

        reset_action = summary.select_one("[data-summary-toolbar-action='reset-range']")
        self.assertIsNotNone(reset_action)
        reset_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(reset_action["href"]).query
        )
        self.assertNotIn("summary_mode", reset_query)
        self.assertNotIn("summary_month", reset_query)
        self.assertNotIn("date_filter_by", reset_query)
        self.assertNotIn("date_filter_type", reset_query)
        self.assertNotIn("date_filter_start", reset_query)
        self.assertNotIn("date_filter_end", reset_query)

        heatmap_response = self.client.get(
            reverse("linkding:bookmarks.index")
            + "?"
            + urllib.parse.urlencode(
                {
                    "date_filter_by": BookmarkSearch.FILTER_DATE_BY_ADDED,
                    "date_filter_type": BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
                    "date_filter_start": start_day.isoformat(),
                    "date_filter_end": end_day.isoformat(),
                }
            ),
            **self.get_summary_headers(mode="heatmap"),
        )
        heatmap_summary = self.make_soup(heatmap_response.content.decode()).select_one(
            "section[ld-sidebar-user-summary]"
        )
        self.assertIsNotNone(heatmap_summary)
        heatmap_reset_action = heatmap_summary.select_one(
            "[data-summary-toolbar-action='reset-range']"
        )
        self.assertIsNotNone(heatmap_reset_action)
        heatmap_reset_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(heatmap_reset_action["href"]).query
        )
        self.assertNotIn("summary_mode", heatmap_reset_query)
        self.assertNotIn("date_filter_by", heatmap_reset_query)
        self.assertNotIn("date_filter_type", heatmap_reset_query)
        self.assertNotIn("date_filter_start", heatmap_reset_query)
        self.assertNotIn("date_filter_end", heatmap_reset_query)

    def test_sidebar_summary_toggles_weekdays_and_monthly_summary(self):
        with translation.override("zh-hans"):
            self.set_profile_language("zh-hans")
            today = timezone.localdate()
            # Use 2 consecutive days in the current month for a streak of 2
            yesterday = today - timezone.timedelta(days=1)
            activity_days = [yesterday, today]
            expected_count = len(activity_days)

            for index, bookmark_day in enumerate(activity_days):
                bookmark_added = timezone.make_aware(
                    timezone.datetime(
                        bookmark_day.year,
                        bookmark_day.month,
                        bookmark_day.day,
                        12,
                        0,
                    )
                )
                self.setup_bookmark(
                    title=f"Monthly summary bookmark {index}",
                    added=bookmark_added,
                    modified=bookmark_added,
                )

            response = self.client.get(
                reverse("linkding:bookmarks.index"),
                **self.get_summary_headers(
                    month=today.strftime("%Y-%m"),
                    show_weekdays=True,
                    show_details=True,
                ),
            )
            summary = self.make_soup(response.content.decode()).select_one(
                "section[ld-sidebar-user-summary]"
            )
            self.assertIsNotNone(summary)
            self.assertEqual(
                [item.text.strip() for item in summary.select(".summary-weekday")],
                ["日", "一", "二", "三", "四", "五", "六"],
            )
            activity_summary = summary.select_one("[data-summary-activity-summary]")
            self.assertIsNotNone(activity_summary)
            self.assertEqual(
                activity_summary.select_one(".summary-activity-summary-lead").get_text(
                    "", strip=True
                ),
                (
                    f"本月（{today.replace(day=1).strftime('%Y/%m/%d')} - "
                    f"{today.replace(day=calendar.monthrange(today.year, today.month)[1]).strftime('%Y/%m/%d')}）："
                ),
            )
            self.assertEqual(
                activity_summary.select_one(".summary-activity-summary-copy").get_text(
                    " ", strip=True
                ),
                f"Bookmarked {expected_count} items，active on {expected_count} days，longest streak {expected_count} days。",
            )
            self.assertEqual(
                [
                    item.text.strip()
                    for item in activity_summary.select(
                        ".summary-activity-summary-value"
                    )
                ],
                [str(expected_count), str(expected_count), str(expected_count)],
            )

            menu_links = [
                item.text.strip()
                for item in summary.select(".section-header .dropdown .menu-link")
            ]
            self.assertIn("隐藏星期", menu_links)
            self.assertIn("隐藏总结", menu_links)

            week_start = today - timezone.timedelta(days=((today.weekday() + 1) % 7))
            week_key_date = week_start + timezone.timedelta(days=1)

            heatmap_response = self.client.get(
                reverse("linkding:bookmarks.index"),
                **self.get_summary_headers(
                    mode="heatmap",
                    week=(
                        f"{week_key_date.isocalendar().year}-W"
                        f"{week_key_date.isocalendar().week:02d}"
                    ),
                    show_weekdays=True,
                    show_details=True,
                ),
            )
            heatmap_summary = self.make_soup(
                heatmap_response.content.decode()
            ).select_one("section[ld-sidebar-user-summary]")
            self.assertIsNotNone(heatmap_summary)
            self.assertEqual(
                [
                    item.text.strip()
                    for item in heatmap_summary.select(".summary-heatmap-weekday")
                ],
                ["日", "一", "二", "三", "四", "五", "六"],
            )
            week_end = week_start + timezone.timedelta(days=6)
            week_activity_days = [
                day for day in activity_days if week_start <= day <= week_end
            ]
            expected_longest_streak = 0
            current_streak = 0
            current_day = week_start
            while current_day <= week_end:
                if current_day in week_activity_days:
                    current_streak += 1
                    expected_longest_streak = max(
                        expected_longest_streak, current_streak
                    )
                else:
                    current_streak = 0
                current_day += timezone.timedelta(days=1)
            heatmap_activity_summary = heatmap_summary.select_one(
                "[data-summary-activity-summary]"
            )
            self.assertEqual(
                heatmap_activity_summary.select_one(
                    ".summary-activity-summary-lead"
                ).get_text("", strip=True),
                (
                    f"本周（{week_start.strftime('%Y/%m/%d')} - "
                    f"{week_end.strftime('%Y/%m/%d')}）："
                ),
            )
            week_count = len(week_activity_days)
            self.assertEqual(
                heatmap_activity_summary.select_one(
                    ".summary-activity-summary-copy"
                ).get_text(" ", strip=True),
                (
                    f"Bookmarked {week_count} {'item' if week_count == 1 else 'items'}，"
                    f"active on {week_count} {'day' if week_count == 1 else 'days'}，"
                    f"longest streak {expected_longest_streak} {'day' if expected_longest_streak == 1 else 'days'}。"
                ),
            )

    def test_sidebar_summary_follows_selected_date_filter_without_explicit_period(self):
        self.set_profile_language("zh-hans")
        today = timezone.localdate()
        start_day = today - timezone.timedelta(days=70)
        end_day = start_day + timezone.timedelta(days=2)

        for offset in range(3):
            bookmark_day = start_day + timezone.timedelta(days=offset)
            bookmark_added = timezone.make_aware(
                timezone.datetime(
                    bookmark_day.year,
                    bookmark_day.month,
                    bookmark_day.day,
                    12,
                    0,
                )
            )
            self.setup_bookmark(
                title=f"Filtered bookmark {offset}",
                added=bookmark_added,
                modified=bookmark_added,
            )

        query_string = urllib.parse.urlencode(
            {
                "date_filter_by": BookmarkSearch.FILTER_DATE_BY_ADDED,
                "date_filter_type": BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
                "date_filter_start": start_day.isoformat(),
                "date_filter_end": end_day.isoformat(),
            }
        )

        calendar_response = self.client.get(
            reverse("linkding:bookmarks.index") + "?" + query_string,
            **self.get_summary_headers(show_details=True),
        )
        calendar_summary = self.make_soup(
            calendar_response.content.decode()
        ).select_one("section[ld-sidebar-user-summary]")
        self.assertIsNotNone(calendar_summary)
        self.assertEqual(
            calendar_summary.attrs["data-summary-month"],
            end_day.strftime("%Y-%m"),
        )
        self.assertIn(
            "is-range-start",
            calendar_summary.select_one(
                f"[data-summary-calendar-day='{start_day.isoformat()}']"
            ).attrs["class"],
        )
        self.assertIn(
            "is-range-end",
            calendar_summary.select_one(
                f"[data-summary-calendar-day='{end_day.isoformat()}']"
            ).attrs["class"],
        )
        calendar_activity_summary = calendar_summary.select_one(
            "[data-summary-activity-summary]"
        )
        self.assertEqual(
            calendar_activity_summary.select_one(
                ".summary-activity-summary-lead"
            ).get_text("", strip=True),
            (
                f"所选周期（{start_day.strftime('%Y/%m/%d')} - "
                f"{end_day.strftime('%Y/%m/%d')}）："
            ),
        )
        self.assertEqual(
            calendar_activity_summary.select_one(
                ".summary-activity-summary-copy"
            ).get_text(" ", strip=True),
            "Bookmarked 3 items，active on 3 days，longest streak 3 days。",
        )

        heatmap_response = self.client.get(
            reverse("linkding:bookmarks.index") + "?" + query_string,
            **self.get_summary_headers(mode="heatmap", show_details=True),
        )
        heatmap_summary = self.make_soup(heatmap_response.content.decode()).select_one(
            "section[ld-sidebar-user-summary]"
        )
        self.assertIsNotNone(heatmap_summary)
        expected_week_start = end_day - timezone.timedelta(
            days=((end_day.weekday() + 1) % 7)
        )
        expected_week_year, expected_week_number, _ = (
            expected_week_start + timezone.timedelta(days=1)
        ).isocalendar()
        self.assertEqual(
            heatmap_summary.select_one(
                "[data-summary-week-year-picker-trigger]"
            ).text.strip(),
            str(expected_week_year),
        )
        self.assertEqual(
            heatmap_summary.select_one(
                "[data-summary-week-number-picker-trigger]"
            ).text.strip(),
            f"W{expected_week_number:02d}",
        )
        heatmap_activity_summary = heatmap_summary.select_one(
            "[data-summary-activity-summary]"
        )
        self.assertEqual(
            heatmap_activity_summary.select_one(
                ".summary-activity-summary-lead"
            ).get_text("", strip=True),
            (
                f"所选周期（{start_day.strftime('%Y/%m/%d')} - "
                f"{end_day.strftime('%Y/%m/%d')}）："
            ),
        )
        self.assertEqual(
            heatmap_activity_summary.select_one(
                ".summary-activity-summary-copy"
            ).get_text(" ", strip=True),
            "Bookmarked 3 items，active on 3 days，longest streak 3 days。",
        )
        start_heatmap_day = heatmap_summary.select_one(
            f"[data-summary-heatmap-day='{start_day.isoformat()}']"
        )
        end_heatmap_day = heatmap_summary.select_one(
            f"[data-summary-heatmap-day='{end_day.isoformat()}']"
        )
        self.assertIsNotNone(start_heatmap_day)
        self.assertIsNotNone(end_heatmap_day)
        self.assertIn("is-range-start", start_heatmap_day.attrs["class"])
        self.assertIn("is-range-end", end_heatmap_day.attrs["class"])

    def test_sidebar_summary_turbo_stream_updates_search_date_filters(self):
        tag = self.setup_tag(name="alpha")
        selected_day = timezone.localdate() - timezone.timedelta(days=2)
        selected_added = timezone.make_aware(
            timezone.datetime(
                selected_day.year,
                selected_day.month,
                selected_day.day,
                12,
                0,
            )
        )
        self.setup_bookmark(
            title="Turbo summary bookmark",
            tags=[tag],
            added=selected_added,
            modified=selected_added,
        )

        initial_response = self.client.get(
            reverse("linkding:bookmarks.index")
            + "?"
            + urllib.parse.urlencode(
                {
                    "q": "#alpha",
                    "date_filter_by": BookmarkSearch.FILTER_DATE_BY_ADDED,
                    "date_filter_type": BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
                    "date_filter_start": selected_day.isoformat(),
                    "date_filter_end": selected_day.isoformat(),
                }
            ),
            **self.get_summary_headers(
                mode="heatmap",
                week=(
                    f"{selected_day.isocalendar().year}-W"
                    f"{selected_day.isocalendar().week:02d}"
                ),
            ),
        )
        initial_summary = self.make_soup(initial_response.content.decode()).select_one(
            "section[ld-sidebar-user-summary]"
        )
        self.assertIsNotNone(initial_summary)
        day_link = initial_summary.select_one(
            f"[data-summary-heatmap-day='{selected_day.isoformat()}'][href]"
        )
        self.assertIsNotNone(day_link)
        day_query = urllib.parse.parse_qs(urllib.parse.urlsplit(day_link["href"]).query)

        response = self.client.get(
            day_link["href"],
            **self.get_bookmark_page_stream_headers(),
        )

        self.assertEqual(response.status_code, 200)
        soup = self.make_soup(response.content.decode())
        search_stream = soup.select_one(
            "turbo-stream[action='update'][target='bookmark-search-container']"
        )
        self.assertIsNotNone(search_stream)

        template_soup = self.make_soup(search_stream.template.decode_contents())
        search_form = template_soup.select_one("form#search")
        self.assertIsNotNone(search_form)
        q_input = search_form.select_one("input[name='q']")
        q_component = search_form.select_one("[input-name='q']")
        self.assertTrue(q_input is not None or q_component is not None)

        search_preferences = template_soup.select_one("form#search_preferences")
        self.assertIsNotNone(search_preferences)

        selected_by = search_preferences.select_one(
            f"input[name='date_filter_by'][value='{BookmarkSearch.FILTER_DATE_BY_ADDED}']"
        )
        self.assertIsNotNone(selected_by)
        self.assertTrue(selected_by.has_attr("checked"))

        selected_type = search_preferences.select_one(
            f"input[name='date_filter_type'][value='{BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE}']"
        )
        self.assertIsNotNone(selected_type)
        self.assertTrue(selected_type.has_attr("checked"))

        self.assertEqual(
            search_preferences.select_one("input[name='date_filter_start']").attrs[
                "value"
            ],
            day_query["date_filter_start"][0],
        )
        self.assertEqual(
            search_preferences.select_one("input[name='date_filter_end']").attrs[
                "value"
            ],
            day_query["date_filter_end"][0],
        )

    def test_accepting_turbo_stream_without_partial_intent_renders_full_page(self):
        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            HTTP_ACCEPT="text/vnd.turbo-stream.html",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("text/vnd.turbo-stream.html", response["Content-Type"])

        soup = self.make_soup(response.content.decode())
        self.assertIsNone(soup.select_one("turbo-stream"))
        self.assertIsNotNone(soup.select_one("form#search"))
        self.assertIsNotNone(soup.select_one("#bookmark-list-container"))

    def test_sidebar_summary_state_is_not_rendered_into_search_forms(self):
        today = timezone.localdate()
        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            **self.get_summary_headers(
                mode="heatmap",
                week=f"{today.isocalendar().year}-W{today.isocalendar().week:02d}",
                show_weekdays=True,
                show_details=True,
            ),
        )

        soup = self.make_soup(response.content.decode())
        search_form = soup.select_one("form#search")
        self.assertIsNotNone(search_form)
        search_preferences = soup.select_one("form#search_preferences")
        self.assertIsNotNone(search_preferences)

        for selector in (
            "input[name='summary_mode']",
            "input[name='summary_month']",
            "input[name='summary_week']",
            "input[name='summary_show_weekdays']",
            "input[name='summary_show_details']",
        ):
            self.assertIsNone(search_form.select_one(selector))
            self.assertIsNone(search_preferences.select_one(selector))

    def test_sidebar_summary_accepts_state_from_headers(self):
        self.set_profile_language("zh-hans")
        today = timezone.localdate()
        bookmark_added = timezone.make_aware(
            timezone.datetime(today.year, today.month, today.day, 12, 0)
        )
        self.setup_bookmark(
            title="Header driven summary bookmark",
            added=bookmark_added,
            modified=bookmark_added,
        )

        week_start = today - timezone.timedelta(days=((today.weekday() + 1) % 7))
        week_key_date = week_start + timezone.timedelta(days=1)
        response = self.client.get(
            reverse("linkding:bookmarks.index"),
            HTTP_X_LINKDING_SUMMARY_MODE="heatmap",
            HTTP_X_LINKDING_SUMMARY_WEEK=(
                f"{week_key_date.isocalendar().year}-W"
                f"{week_key_date.isocalendar().week:02d}"
            ),
            HTTP_X_LINKDING_SUMMARY_SHOW_WEEKDAYS="1",
            HTTP_X_LINKDING_SUMMARY_SHOW_DETAILS="1",
        )

        summary = self.make_soup(response.content.decode()).select_one(
            "section[ld-sidebar-user-summary]"
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary["data-summary-mode"], "heatmap")
        self.assertEqual(
            summary["data-summary-week"],
            (
                f"{week_key_date.isocalendar().year}-W"
                f"{week_key_date.isocalendar().week:02d}"
            ),
        )
        self.assertEqual(
            [item.text.strip() for item in summary.select(".summary-heatmap-weekday")],
            ["日", "一", "二", "三", "四", "五", "六"],
        )
        self.assertIsNotNone(summary.select_one("[data-summary-activity-summary]"))

    def test_sidebar_summary_ignores_summary_state_query_params(self):
        today = timezone.localdate()
        bookmark_added = timezone.make_aware(
            timezone.datetime(today.year, today.month, today.day, 12, 0)
        )
        self.setup_bookmark(
            title="Query driven summary bookmark",
            added=bookmark_added,
            modified=bookmark_added,
        )

        previous_month = (today - timezone.timedelta(days=40)).strftime("%Y-%m")
        response = self.client.get(
            reverse("linkding:bookmarks.index")
            + "?"
            + urllib.parse.urlencode(
                {
                    "summary_mode": "heatmap",
                    "summary_month": previous_month,
                    "summary_week": "2020-W03",
                    "summary_show_weekdays": "1",
                    "summary_show_details": "1",
                }
            )
        )

        summary = self.make_soup(response.content.decode()).select_one(
            "section[ld-sidebar-user-summary]"
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary["data-summary-mode"], "calendar")
        self.assertEqual(summary["data-summary-month"], today.strftime("%Y-%m"))
        self.assertEqual(summary.select(".summary-weekday"), [])
        self.assertIsNone(summary.select_one("[data-summary-activity-summary]"))

    def test_search_action_does_not_preserve_summary_state_query_params(self):
        response = self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "q": "#alpha",
                "summary_mode": "heatmap",
                "summary_week": "2026-W17",
                "summary_show_weekdays": "1",
                "summary_show_details": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(response["Location"]).query)
        self.assertEqual(query["q"], ["#alpha"])
        self.assertNotIn("summary_mode", query)
        self.assertNotIn("summary_month", query)
        self.assertNotIn("summary_week", query)
        self.assertNotIn("summary_show_weekdays", query)
        self.assertNotIn("summary_show_details", query)

    def test_search_action_does_not_preserve_domain_state_query_params(self):
        response = self.client.post(
            reverse("linkding:bookmarks.index"),
            {
                "q": "#alpha",
                "domain_view": "icon",
                "domain_compact": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(response["Location"]).query)
        self.assertEqual(query["q"], ["#alpha"])
        self.assertNotIn("domain_view", query)
        self.assertNotIn("domain_compact", query)
