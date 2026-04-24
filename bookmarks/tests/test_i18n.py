from django.test import TestCase
from django.urls import reverse
from bs4 import BeautifulSoup

from bookmarks.tests.helpers import BookmarkFactoryMixin


class I18nTestCase(TestCase, BookmarkFactoryMixin):
    def login_user_with_english_profile(self):
        user = self.get_or_create_test_user()
        user.profile.language = "en"
        user.profile.enable_sharing = True
        user.profile.save()

        self.client.force_login(user)
        # Simulate stale language cookie and ensure profile language still wins
        self.client.cookies["django_language"] = "zh-hans"
        return user

    def test_login_page_defaults_to_english(self):
        response = self.client.get(reverse("login"))
        html = response.content.decode()

        self.assertContains(response, "lang=\"en\"")
        self.assertIn("<h1 id=\"main-heading\">Login</h1>", html)

    def test_login_page_renders_language_switcher(self):
        response = self.client.get(reverse("login"))
        html = response.content.decode()
        soup = BeautifulSoup(html, "html.parser")
        nav = soup.find("nav")
        trigger = soup.select_one("nav .language-switcher .dropdown-toggle")
        menu = soup.select_one("nav .language-switcher .menu")

        self.assertIsNotNone(nav)
        self.assertIsNotNone(nav.find("div", class_="language-switcher"))
        self.assertIsNotNone(trigger)
        self.assertIn("btn-link", trigger.get("class", []))
        self.assertIn("dropdown-toggle", trigger.get("class", []))
        self.assertIsNotNone(menu)
        self.assertIsNone(soup.select_one("#login-language-switcher"))
        self.assertNotIn(
            f'<a href="{reverse("login")}" class="btn btn-link">Login</a>',
            html,
        )
        self.assertNotIn("form-select", html)

    def test_login_page_can_render_chinese_from_language_cookie(self):
        self.client.cookies["django_language"] = "zh-hans"

        response = self.client.get(reverse("login"))

        self.assertEqual(response.wsgi_request.LANGUAGE_CODE, "zh-hans")
        self.assertContains(response, 'lang="zh-hans"')
        self.assertContains(response, "<h1 id=\"main-heading\">登录</h1>", html=True)

    def test_login_page_language_options_keep_native_names(self):
        self.client.cookies["django_language"] = "zh-hans"

        response = self.client.get(reverse("login"))
        soup = BeautifulSoup(response.content.decode(), "html.parser")
        labels = soup.select("nav .language-switcher .menu .menu-link")

        self.assertEqual(
            [label.get_text(strip=True) for label in labels], ["English", "简体中文"]
        )

    def test_language_update_route_updates_language_cookie(self):
        response = self.client.post(
            reverse("language-update"),
            {"language": "zh-hans", "next": reverse("login")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("login"))
        self.assertEqual(response.cookies["django_language"].value, "zh-hans")

    def test_authenticated_language_update_route_persists_user_preference(self):
        user = self.get_or_create_test_user()
        self.client.force_login(user)

        response = self.client.post(
            reverse("language-update"),
            {"language": "zh-hans", "next": reverse("linkding:settings.general")},
        )

        user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("linkding:settings.general")
        )
        self.assertEqual(response.cookies["django_language"].value, "zh-hans")
        self.assertEqual(user.profile.language, "zh-hans")

    def test_authenticated_language_update_route_rejects_unsupported_language(self):
        user = self.get_or_create_test_user()
        self.client.force_login(user)

        response = self.client.post(
            reverse("language-update"),
            {"language": "fr", "next": reverse("linkding:settings.general")},
        )

        user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(user.profile.language, "en")
        self.assertNotIn("django_language", response.cookies)


    def test_authenticated_user_profile_language_overrides_cookie(self):
        user = self.get_or_create_test_user()
        user.profile.language = "zh-hans"
        user.profile.save()

        self.client.force_login(user)
        self.client.cookies["django_language"] = "en"

        response = self.client.get(reverse("linkding:settings.general"))

        self.assertEqual(response.wsgi_request.LANGUAGE_CODE, "zh-hans")
        self.assertContains(response, 'lang="zh-hans"')

    def test_javascript_catalog_endpoint_is_available(self):
        response = self.client.get(reverse("javascript-catalog"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "text/javascript")
        self.assertIn("django.catalog", response.content.decode())

    def test_layout_loads_javascript_catalog_script(self):
        user = self.get_or_create_test_user()
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:bookmarks.index"))

        self.assertContains(
            response,
            f'<script src="{reverse("javascript-catalog")}"></script>',
            html=True,
        )

    def test_bookmark_pages_use_english_labels_when_profile_language_is_en(self):
        user = self.login_user_with_english_profile()
        bookmark = self.setup_bookmark(user=user)

        pages = {
            reverse("linkding:bookmarks.index"): "Bookmarks",
            reverse("linkding:bookmarks.shared"): "Shared bookmarks",
            reverse("linkding:bookmarks.archived"): "Archived bookmarks",
            reverse("linkding:bookmarks.trashed"): "Trash",
            reverse("linkding:bookmarks.new"): "Add bookmark",
            reverse("linkding:bookmarks.edit", args=[bookmark.id]): "Edit bookmark",
        }

        for url, expected_heading in pages.items():
            response = self.client.get(url)
            self.assertEqual(response.wsgi_request.LANGUAGE_CODE, "en")
            self.assertContains(
                response,
                f'<h1 id="main-heading">{expected_heading}',
                html=False,
            )

        index_response = self.client.get(reverse("linkding:bookmarks.index"))
        index_html = index_response.content.decode()
        self.assertIn("Add bookmark", index_html)
        self.assertIn("Bookmarks", index_html)
        self.assertIn("Settings", index_html)
        self.assertIn('placeholder="Search for words or #tags"', index_html)
        self.assertIn("Filters", index_html)
        self.assertIn(">Bundles<", index_html)
        self.assertIn(">Tags<", index_html)

    def test_trash_page_filter_labels_are_english(self):
        self.login_user_with_english_profile()

        response = self.client.get(reverse("linkding:bookmarks.trashed"))
        html = response.content.decode()
        soup = BeautifulSoup(html, "html.parser")
        date_filter_options = [
            label.get_text(strip=True)
            for label in soup.select("#date-filter-by-group .form-radio")
        ]

        self.assertContains(response, "Date deleted ↑")
        self.assertContains(response, "Date deleted ↓")
        self.assertIn("Date deleted", date_filter_options)
        self.assertNotIn("删除时间 ↑", html)
        self.assertNotIn("删除时间 ↓", html)
        self.assertNotIn(">删除<", html)

    def test_pagination_uses_translated_navigation_labels(self):
        user = self.login_user_with_english_profile()
        self.setup_numbered_bookmarks(31, user=user)

        response = self.client.get(reverse("linkding:bookmarks.index") + "?page=2")
        html = response.content.decode()

        self.assertContains(response, ">Previous<", html=False)
        self.assertContains(response, ">Next<", html=False)
        self.assertNotIn(">上一页<", html)
        self.assertNotIn(">下一页<", html)

    def test_bundle_pages_use_english_labels_when_profile_language_is_en(self):
        user = self.login_user_with_english_profile()
        bundle = self.setup_bundle(user=user, name="Test Bundle")

        new_response = self.client.get(reverse("linkding:bundles.new"))
        new_html = new_response.content.decode()
        self.assertContains(new_response, '<h1 id="main-heading">Add filter</h1>', html=True)
        self.assertContains(new_response, '<h2 id="preview-heading">Preview</h2>', html=True)
        self.assertContains(new_response, "Filter name")
        self.assertContains(new_response, "Search terms")
        self.assertContains(new_response, "Required tags")
        self.assertContains(new_response, "Excluded tags")
        self.assertContains(new_response, 'value="Save"')
        self.assertContains(new_response, ">Cancel<", html=False)
        self.assertNotIn("新增过滤器", new_html)
        self.assertNotIn("过滤器名称", new_html)
        self.assertNotIn("保存", new_html)
        self.assertNotIn("取消", new_html)

        edit_response = self.client.get(reverse("linkding:bundles.edit", args=[bundle.id]))
        edit_html = edit_response.content.decode()
        self.assertContains(edit_response, '<h1 id="main-heading">Edit filter</h1>', html=True)
        self.assertNotIn("编辑过滤器", edit_html)

    def test_tag_pages_use_english_labels_when_profile_language_is_en(self):
        user = self.login_user_with_english_profile()
        tag = self.setup_tag(user=user, name="test-tag")

        new_response = self.client.get(reverse("linkding:tags.new"))
        new_html = new_response.content.decode()
        self.assertContains(new_response, '<h1 id="main-heading">Add tag</h1>', html=True)
        self.assertContains(new_response, "Name")
        self.assertContains(new_response, ">Save<", html=False)
        self.assertContains(new_response, ">Cancel<", html=False)
        self.assertNotIn("名称", new_html)
        self.assertNotIn("保存", new_html)
        self.assertNotIn("取消", new_html)

        edit_response = self.client.get(reverse("linkding:tags.edit", args=[tag.id]))
        edit_html = edit_response.content.decode()
        self.assertContains(edit_response, '<h1 id="main-heading">Edit tag</h1>', html=True)
        self.assertNotIn("编辑标签", edit_html)
