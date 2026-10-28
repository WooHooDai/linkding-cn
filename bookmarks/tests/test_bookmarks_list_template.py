import datetime

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.template import RequestContext, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from urllib.parse import unquote_plus

from bookmarks.middlewares import LinkdingMiddleware
from bookmarks.models import Bookmark, BookmarkSearch, User, UserProfile
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin
from bookmarks.utils import humanize_absolute_date_short
from bookmarks.views import contexts


class BookmarkListTemplateTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    def assertBookmarksLink(
        self, html: str, bookmark: Bookmark, link_target: str = "_blank"
    ):
        soup = self.make_soup(html)
        link = soup.find("a", href=bookmark.url, class_="title-link")
        self.assertIsNotNone(link)
        self.assertEqual(link.get("target"), link_target)
        self.assertIn("noopener", link.get("rel", []))

        title = link.find("span")
        self.assertIsNotNone(title)
        self.assertEqual(title.text.strip(), bookmark.resolved_title)

    def assertWebArchiveLink(
        self, html: str, label_content: str, url: str, link_target: str = "_blank"
    ):
        soup = self.make_soup(html)
        link = soup.find(
            "a",
            href=url,
            title="View snapshot on the Internet Archive Wayback Machine",
        )
        self.assertIsNotNone(link)
        self.assertEqual(link.get("target"), link_target)
        self.assertIn("noopener", link.get("rel", []))
        self.assertEqual(link.get_text(strip=True), label_content)

    def assertViewLink(self, html: str, bookmark: Bookmark, base_url=None):
        self.assertViewLinkCount(html, bookmark, base_url)

    def assertNoViewLink(self, html: str, bookmark: Bookmark, base_url=None):
        self.assertViewLinkCount(html, bookmark, base_url, count=0)

    def assertViewLinkCount(
        self,
        html: str,
        bookmark: Bookmark,
        base_url: str = None,
        count=1,
    ):
        if base_url is None:
            base_url = reverse("linkding:bookmarks.index")
        details_url = base_url + f"?details={bookmark.id}"
        self.assertInHTML(
            f"""
                <a href="{details_url}" class="view-action" data-turbo-action="replace" data-turbo-frame="details-modal">View</a>
            """,
            html,
            count=count,
        )

    def assertEditLinkCount(self, html: str, bookmark: Bookmark, count=1):
        edit_url = reverse("linkding:bookmarks.edit", args=[bookmark.id])
        self.assertInHTML(
            f"""
            <a href="{edit_url}?return_url=/bookmarks" class="edit-action">Edit</a>
        """,
            html,
            count=count,
        )

    def assertArchiveLinkCount(self, html: str, bookmark: Bookmark, count=1):
        soup = self.make_soup(html)
        buttons = soup.select(
            f'button[ld-confirm-button][name="archive"][value="{bookmark.id}"]'
        )
        self.assertEqual(len(buttons), count)
        for button in buttons:
            self.assertEqual(button.get("ld-confirm-question"), "Archive bookmark?")
            self.assertEqual(button.get_text(strip=True), "Archive")

    def assertDeleteLinkCount(self, html: str, bookmark: Bookmark, count=1):
        soup = self.make_soup(html)
        buttons = soup.select(
            f'button[ld-confirm-button][name="trash"][value="{bookmark.id}"]'
        )
        self.assertEqual(len(buttons), count)
        for button in buttons:
            self.assertEqual(button.get("ld-confirm-question"), "Move to trash?")
            self.assertEqual(button.get_text(strip=True), "Remove")

    def assertBookmarkActions(self, html: str, bookmark: Bookmark):
        self.assertBookmarkActionsCount(html, bookmark, count=1)

    def assertNoBookmarkActions(self, html: str, bookmark: Bookmark):
        self.assertBookmarkActionsCount(html, bookmark, count=0)

    def assertBookmarkActionsCount(self, html: str, bookmark: Bookmark, count=1):
        self.assertEditLinkCount(html, bookmark, count=count)
        self.assertArchiveLinkCount(html, bookmark, count=count)
        self.assertDeleteLinkCount(html, bookmark, count=count)

    def assertShareInfo(self, html: str, bookmark: Bookmark):
        self.assertShareInfoCount(html, bookmark, 1)

    def assertNoShareInfo(self, html: str, bookmark: Bookmark):
        self.assertShareInfoCount(html, bookmark, 0)

    def assertShareInfoCount(self, html: str, bookmark: Bookmark, count=1):
        # Shared by link
        self.assertInHTML(
            f"""
            <span>Shared by 
                <a href="?user={bookmark.owner.username}">{bookmark.owner.username}</a>
            </span>
        """,
            html,
            count=count,
        )

    def assertFaviconVisible(self, html: str, bookmark: Bookmark):
        self.assertFavicon(html, bookmark, True)

    def assertFaviconHidden(self, html: str, bookmark: Bookmark):
        self.assertFavicon(html, bookmark, False)

    def assertFavicon(self, html: str, bookmark: Bookmark, visible=True):
        soup = self.make_soup(html)
        favicon = soup.select_one(".favicon")
        if not visible:
            self.assertIsNone(favicon)
            return
        self.assertIsNotNone(favicon)
        self.assertTrue(favicon["src"].startswith("/favicon/"))

    def assertPreviewImageVisible(self, html: str, bookmark: Bookmark):
        self.assertPreviewImage(html, bookmark, True)

    def assertPreviewImageHidden(self, html: str, bookmark: Bookmark):
        self.assertPreviewImage(html, bookmark, False)

    def assertPreviewImage(self, html: str, bookmark: Bookmark, visible=True):
        soup = self.make_soup(html)
        preview_image = soup.select_one(".preview-image")

        if not visible:
            self.assertIsNone(preview_image)
            return

        url = f"/static/{bookmark.preview_image_file}"
        self.assertIsNotNone(preview_image)
        self.assertEqual(preview_image["src"], url)
        self.assertEqual(preview_image.get("alt"), "")

    def assertPreviewImagePlaceholder(self, html: str):
        soup = self.make_soup(html)
        placeholder = soup.select_one(".preview-image.placeholder")
        self.assertIsNotNone(placeholder)

    def assertBookmarkURLCount(
        self, html: str, bookmark: Bookmark, link_target: str = "_blank", count=0
    ):
        self.assertInHTML(
            f"""
        <div class="url-path truncate">
          <a href="{bookmark.url}" target="{link_target}" rel="noopener" 
          class="url-display">
            {bookmark.url}
          </a>
        </div>
        """,
            html,
            count,
        )

    def assertBookmarkURLVisible(self, html: str, bookmark: Bookmark):
        self.assertBookmarkURLCount(html, bookmark, count=1)

    def assertBookmarkURLHidden(
        self, html: str, bookmark: Bookmark, link_target: str = "_blank"
    ):
        self.assertBookmarkURLCount(html, bookmark, count=0)

    def assertNotes(self, html: str, notes_html: str, count=1):
        soup = self.make_soup(html)
        notes = soup.select(".inline-edit.inline-edit-notes")

        if not notes_html:
            self.assertEqual(len(notes), count)
            return

        markdown_html = "\n".join(
            str(note.select_one(".markdown")) for note in notes
        )
        self.assertInHTML(notes_html, markdown_html, count=count)

    def assertNotesToggle(self, html: str, count=1):
        self.assertInHTML(
            """
        <button type="button" class="btn btn-link btn-sm toggle-notes"
                title="Notes">
          <svg class="action-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16">
            <use xlink:href="#ld-icon-note"></use>
          </svg>
        </button>
          """,
            html,
            count=count,
        )

    def assertUnshareButton(self, html: str, bookmark: Bookmark, count=1):
        self.assertInHTML(
            f"""
        <button ld-confirm-button ld-confirm-question="Unshare?" type="submit" name="unshare" value="{bookmark.id}"
                data-action="unshare"
                class="btn btn-link btn-sm"
                title="Shared">
          <svg class="action-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16"><use xlink:href="#ld-icon-share"></use></svg>
        </button>
          """,
            html,
            count=count,
        )

    def assertMarkAsReadButton(self, html: str, bookmark: Bookmark, count=1):
        self.assertInHTML(
            f"""
        <button type="button" data-action="mark_as_read"
                class="btn btn-link btn-sm"
                title="Mark as read">
          <svg class="action-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16"><use xlink:href="#ld-icon-unread-x"></use></svg>
        </button>
          """,
            html,
            count=count,
        )

    def assertMarkAsUnreadButton(self, html: str, bookmark: Bookmark, count=1):
        self.assertInHTML(
            f"""
        <button type="button" data-action="mark_as_unread"
                class="btn btn-link btn-sm"
                title="Mark as unread">
          <svg class="action-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16"><use xlink:href="#ld-icon-read-check"></use></svg>
        </button>
          """,
            html,
            count=count,
        )

    def render_template(
        self,
        url="/bookmarks",
        context_type: type[
            contexts.BookmarkListContext
        ] = contexts.ActiveBookmarkListContext,
        user: User | AnonymousUser = None,
        is_preview: bool = False,
    ) -> str:
        rf = RequestFactory()
        request = rf.get(url)
        request.user = user or self.get_or_create_test_user()
        middleware = LinkdingMiddleware(lambda r: HttpResponse())
        middleware(request)

        search = BookmarkSearch.from_request(request, request.GET)
        bookmark_list_context = context_type(request, search)
        if is_preview:
            bookmark_list_context.is_preview = True
        profile = request.user.profile if hasattr(request.user, 'profile') else None
        context = RequestContext(request, {
            "bookmark_list": bookmark_list_context,
            "sticky_pagination": profile.sticky_pagination if profile else False,
            "show_notes": profile.permanent_notes if profile else False,
        })

        template = Template("{% include 'bookmarks/bookmark_list.html' %}")
        return template.render(context)

    def setup_date_format_test(
        self, date_display_setting: str, web_archive_url: str = "",
        date_route: str = "",
    ):
        bookmark = self.setup_bookmark()
        bookmark.date_added = timezone.now() - relativedelta(days=8)
        bookmark.web_archive_snapshot_url = web_archive_url
        bookmark.save()
        user = self.get_or_create_test_user()
        user.profile.bookmark_date_display = date_display_setting
        if date_route:
            user.profile.bookmark_date_route = date_route
        user.profile.save()
        return bookmark

    def inline_bookmark_description_test(self, bookmark):
        html = self.render_template()
        soup = self.make_soup(html)

        has_description = bool(bookmark.description)
        has_author = bool(bookmark.author)
        has_tags = len(bookmark.tags.all()) > 0

        # inline description block exists
        description = soup.select_one(
            ".description.inline .description-container.truncate"
        )
        self.assertIsNotNone(description)

        # separate description block does not exist
        separate_description = soup.select_one(".description.separate")
        self.assertIsNone(separate_description)

        # author 位于标题行（title-link 之后），不在描述行内
        title = soup.select_one(".title")
        self.assertIsNotNone(title)
        if has_author:
            author_span = title.select_one(".author")
            self.assertIsNotNone(author_span)
            self.assertEqual(author_span.text, bookmark.author)
            # author 可点击：链接到 author 搜索（空格等 URL 编码后仍可还原）
            self.assertEqual(author_span.name, "a")
            author_href = unquote_plus(author_span.get("href", ""))
            self.assertIn(f'q=author:"{bookmark.author}"', author_href)
            self.assertIsNone(
                description.select_one(".author"),
                "author must not render inside the description container",
            )
            title_link = title.select_one(".title-link")
            self.assertIsNotNone(title_link)
            # author 必须位于 title-link 之后
            self.assertTrue(
                list(title.children).index(author_span)
                > list(title.children).index(title_link)
            )
        else:
            self.assertIsNone(title.select_one(".author"))

        # 描述行：仅 tags、可选分隔符与 description
        expected_selectors = []
        if has_tags:
            expected_selectors.append(".tags")
        if has_tags and has_description:
            expected_selectors.append(".description-separator")
        if has_description:
            expected_selectors.append(".description-text")
        children = description.find_all(recursive=False)
        self.assertEqual(
            [child.get("class", [None])[0] for child in children],
            [selector.removeprefix(".") for selector in expected_selectors],
        )

        # separators exist between adjacent non-empty segments
        separators = description.select(".description-separator")
        segment_count = sum(1 for s in (has_tags, has_description) if s)
        self.assertEqual(
            len(separators), segment_count - 1 if segment_count > 1 else 0
        )
        for separator in separators:
            self.assertEqual(separator.text, " | ")

        # contains description text, without leading/trailing whitespace
        if has_description:
            description_text = description.find("span", string=bookmark.description)
            self.assertIsNotNone(description_text)

        if not has_tags:
            # no tags element
            tags = soup.select_one(".tags")
            self.assertIsNone(tags)
        else:
            # tags element exists
            tags = soup.select_one(".tags")
            self.assertIsNotNone(tags)

            # one link for each tag
            tag_links = tags.find_all("a")
            self.assertEqual(len(tag_links), len(bookmark.tags.all()))

            for tag in bookmark.tags.all():
                tag_link = tags.find("a", string=tag.name)
                self.assertIsNotNone(tag_link)
                self.assertEqual(tag_link["href"], f"?q=%23{tag.name}")

    def test_inline_bookmark_description(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_description_display = (
            UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_INLINE
        )
        profile.save()

        # no description, no tags, no author
        bookmark = self.setup_bookmark(description="")
        self.inline_bookmark_description_test(bookmark)

        # with description, no tags, no author
        bookmark = self.setup_bookmark(description="Test description")
        self.inline_bookmark_description_test(bookmark)

        # with author, no tags, no description
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(author="Test author")
        self.inline_bookmark_description_test(bookmark)

        # with author and description, no tags
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            author="Test author", description="Test description"
        )
        self.inline_bookmark_description_test(bookmark)

        # no description, no author, with tags
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            description="", tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()]
        )
        self.inline_bookmark_description_test(bookmark)

        # with author, with tags, no description
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            author="Test author",
            tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()],
        )
        self.inline_bookmark_description_test(bookmark)

        # with description, with tags, no author
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            description="Test description",
            tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()],
        )
        self.inline_bookmark_description_test(bookmark)

        # with description, with tags, with author
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            author="Test author",
            description="Test description",
            tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()],
        )
        self.inline_bookmark_description_test(bookmark)

    def separate_bookmark_description_test(self, bookmark):
        html = self.render_template()
        soup = self.make_soup(html)

        has_description = bool(bookmark.description)
        has_author = bool(bookmark.author)
        has_tags = len(bookmark.tags.all()) > 0

        # inline description block does not exist
        inline_description = soup.select_one(".description.inline")
        self.assertIsNone(inline_description)

        description = soup.select_one(".description .description-container.separate")
        self.assertIsNotNone(description)

        # author 位于标题行，不在描述容器内
        title = soup.select_one(".title")
        self.assertIsNotNone(title)
        if has_author:
            author_span = title.select_one(".author")
            self.assertIsNotNone(author_span)
            self.assertEqual(author_span.text, bookmark.author)
            title_link = title.select_one(".title-link")
            self.assertIsNotNone(title_link)
            # author 必须位于 title-link 之后
            self.assertTrue(
                list(title.children).index(author_span)
                > list(title.children).index(title_link)
            )
        else:
            self.assertIsNone(title.select_one(".author"))
        self.assertIsNone(description.select_one(".author"))

        if has_description:
            description_text = description.select_one(".description-text")
            self.assertIsNotNone(description_text)
            self.assertEqual(description_text.text, bookmark.description)
        else:
            self.assertIsNone(description.select_one(".description-text"))

        # 描述容器内不再有作者分隔符
        self.assertEqual(len(description.select(".description-separator")), 0)

        if not has_tags:
            # no tags element
            tags = soup.select_one(".tags")
            self.assertIsNone(tags)
        else:
            # tags element exists
            tags = soup.select_one(".tags")
            self.assertIsNotNone(tags)

            # one link for each tag
            tag_links = tags.find_all("a")
            self.assertEqual(len(tag_links), len(bookmark.tags.all()))

            for tag in bookmark.tags.all():
                tag_link = tags.find("a", string=tag.name)
                self.assertIsNotNone(tag_link)
                self.assertEqual(tag_link["href"], f"?q=%23{tag.name}")

    def test_separate_bookmark_description(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_description_display = (
            UserProfile.BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE
        )
        profile.save()

        # no description, no tags
        bookmark = self.setup_bookmark(description="")
        self.separate_bookmark_description_test(bookmark)

        # with author, no description, no tags
        bookmark = self.setup_bookmark(author="Test author")
        self.separate_bookmark_description_test(bookmark)

        # with description, no tags
        bookmark = self.setup_bookmark(description="Test description")
        self.separate_bookmark_description_test(bookmark)

        # with author and description, no tags
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            author="Test author", description="Test description"
        )
        self.separate_bookmark_description_test(bookmark)

        # no description, with tags
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            description="", tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()]
        )
        self.separate_bookmark_description_test(bookmark)

        # with description, with tags
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            description="Test description",
            tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()],
        )
        self.separate_bookmark_description_test(bookmark)

        # with author and description, with tags
        Bookmark.objects.all().delete()
        bookmark = self.setup_bookmark(
            author="Test author",
            description="Test description",
            tags=[self.setup_tag(), self.setup_tag(), self.setup_tag()],
        )
        self.separate_bookmark_description_test(bookmark)

    def test_bookmark_description_max_lines(self):
        self.setup_bookmark()
        html = self.render_template()
        soup = self.make_soup(html)
        bookmark_list = soup.select_one("ul.bookmark-list")
        style = bookmark_list["style"]
        self.assertIn("--ld-bookmark-description-max-lines:1;", style)

        profile = self.get_or_create_test_user().profile
        profile.bookmark_description_max_lines = 3
        profile.save()

        html = self.render_template()
        soup = self.make_soup(html)
        bookmark_list = soup.select_one("ul.bookmark-list")
        style = bookmark_list["style"]
        self.assertIn("--ld-bookmark-description-max-lines:3;", style)

    def test_bookmark_tag_ordering(self):
        bookmark = self.setup_bookmark()
        tag3 = self.setup_tag(name="tag3")
        tag1 = self.setup_tag(name="tag1")
        tag2 = self.setup_tag(name="tag2")
        bookmark.tags.add(tag3, tag1, tag2)

        html = self.render_template()
        soup = self.make_soup(html)
        tags = soup.select_one(".tags")
        tag_links = tags.find_all("a")
        self.assertEqual(len(tag_links), 3)
        self.assertEqual(tag_links[0].text, "tag1")
        self.assertEqual(tag_links[1].text, "tag2")
        self.assertEqual(tag_links[2].text, "tag3")

    def test_bookmark_tag_link_does_not_duplicate_existing_tag_filter(self):
        tag = self.setup_tag(name="tag1")
        self.setup_bookmark(tags=[tag])

        html = self.render_template(url="/bookmarks?q=%23tag1&sort=title_asc")
        soup = self.make_soup(html)
        tag_link = soup.select_one(".tags a")

        self.assertIsNotNone(tag_link)
        self.assertEqual(tag_link["href"], "?q=%23tag1&sort=title_asc")

    def test_bookmark_tag_link_does_not_duplicate_existing_lax_tag_filter(self):
        user = self.get_or_create_test_user()
        user.profile.tag_search = UserProfile.TAG_SEARCH_LAX
        user.profile.save()

        tag = self.setup_tag(name="tag1", user=user)
        self.setup_bookmark(tags=[tag], user=user)

        html = self.render_template(url="/bookmarks?q=tag1&sort=title_asc", user=user)
        soup = self.make_soup(html)
        tag_link = soup.select_one(".tags a")

        self.assertIsNotNone(tag_link)
        self.assertEqual(tag_link["href"], "?q=tag1&sort=title_asc")

    def test_bookmark_tag_query_string(self):
        # appends tag to existing query string
        bookmark = self.setup_bookmark(title="term1 term2")
        tag1 = self.setup_tag(name="tag1")
        bookmark.tags.add(tag1)

        html = self.render_template(url="/bookmarks?q=term1 and term2")
        soup = self.make_soup(html)
        tags = soup.select_one(".tags")
        tag_links = tags.find_all("a")
        self.assertEqual(len(tag_links), 1)
        self.assertEqual(tag_links[0]["href"], "?q=term1+and+term2+%23tag1")

        # wraps or expression in parentheses
        html = self.render_template(url="/bookmarks?q=term1 or term2")
        soup = self.make_soup(html)
        tags = soup.select_one(".tags")
        tag_links = tags.find_all("a")
        self.assertEqual(len(tag_links), 1)
        self.assertEqual(tag_links[0]["href"], "?q=%28term1+or+term2%29+%23tag1")

    def test_should_render_web_archive_link_with_absolute_date_setting(self):
        bookmark = self.setup_date_format_test(
            UserProfile.BOOKMARK_DATE_DISPLAY_ABSOLUTE,
            "https://web.archive.org/web/20210811214511/https://wanikani.com/",
            date_route=UserProfile.BOOKMARK_DATE_ROUTE_WEB_ARCHIVE,
        )
        html = self.render_template()
        formatted_date = humanize_absolute_date_short(bookmark.date_added)

        self.assertWebArchiveLink(
            html, formatted_date, bookmark.web_archive_snapshot_url
        )

    def test_should_render_web_archive_link_with_relative_date_setting(self):
        bookmark = self.setup_date_format_test(
            UserProfile.BOOKMARK_DATE_DISPLAY_RELATIVE,
            "https://web.archive.org/web/20210811214511/https://wanikani.com/",
            date_route=UserProfile.BOOKMARK_DATE_ROUTE_WEB_ARCHIVE,
        )
        html = self.render_template()

        self.assertWebArchiveLink(html, "1 week ago", bookmark.web_archive_snapshot_url)

    def test_should_render_generated_web_archive_link_without_saved_snapshot_url(self):
        user = self.get_or_create_test_user()
        user.profile.bookmark_date_display = UserProfile.BOOKMARK_DATE_DISPLAY_ABSOLUTE
        user.profile.bookmark_date_route = UserProfile.BOOKMARK_DATE_ROUTE_WEB_ARCHIVE
        user.profile.save()

        date_added = timezone.datetime(2023, 8, 11, 21, 45, 11, tzinfo=datetime.UTC)
        bookmark = self.setup_bookmark(
            url="https://example.com/article", added=date_added
        )

        html = self.render_template()
        formatted_date = humanize_absolute_date_short(bookmark.date_added)

        self.assertWebArchiveLink(
            html,
            formatted_date,
            "https://web.archive.org/web/20230811214511/https://example.com/article",
        )

    def test_bookmark_link_target_should_be_blank_by_default(self):
        bookmark = self.setup_bookmark()
        html = self.render_template()

        self.assertBookmarksLink(html, bookmark, link_target="_blank")

    def test_bookmark_link_target_should_respect_user_profile(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_SELF
        profile.save()

        bookmark = self.setup_bookmark()
        html = self.render_template()

        self.assertBookmarksLink(html, bookmark, link_target="_self")

    def test_web_archive_link_target_should_be_blank_by_default(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_date_route = UserProfile.BOOKMARK_DATE_ROUTE_WEB_ARCHIVE
        profile.save()
        bookmark = self.setup_bookmark()
        bookmark.date_added = timezone.now() - relativedelta(days=8)
        bookmark.web_archive_snapshot_url = "https://example.com"
        bookmark.save()

        html = self.render_template()

        self.assertWebArchiveLink(
            html, "1 week ago", bookmark.web_archive_snapshot_url, link_target="_blank"
        )

    def test_web_archive_link_target_should_respect_user_profile(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_link_target = UserProfile.BOOKMARK_LINK_TARGET_SELF
        profile.bookmark_date_route = UserProfile.BOOKMARK_DATE_ROUTE_WEB_ARCHIVE
        profile.save()

        bookmark = self.setup_bookmark()
        bookmark.date_added = timezone.now() - relativedelta(days=8)
        bookmark.web_archive_snapshot_url = "https://example.com"
        bookmark.save()

        html = self.render_template()

        self.assertWebArchiveLink(
            html, "1 week ago", bookmark.web_archive_snapshot_url, link_target="_self"
        )

    def test_should_render_latest_snapshot_link_if_one_exists(self):
        bookmark = self.setup_date_format_test(
            UserProfile.BOOKMARK_DATE_DISPLAY_ABSOLUTE
        )
        bookmark.latest_snapshot = self.setup_asset(bookmark)
        bookmark.save()

        html = self.render_template()
        formatted_date = humanize_absolute_date_short(bookmark.date_added)
        snapshot_url = reverse(
            "linkding:assets.view", args=[bookmark.latest_snapshot.id]
        )

        # Check that the snapshot link is rendered with the correct URL and title
        self.assertInHTML(
            f"""
            <a href="{snapshot_url}"
               class="date-link date-link-exists"
               title="View latest snapshot" target="_blank"
               data-turbo="false" rel="noopener">
                {formatted_date}
            </a>
            """,
            html,
        )

    def test_should_reflect_unread_state_as_css_class(self):
        self.setup_bookmark(unread=True)
        html = self.render_template()
        soup = self.make_soup(html)

        list_item = soup.select_one("ul.bookmark-list > li[ld-bookmark-item]")
        self.assertIsNotNone(list_item)
        self.assertListEqual(["unread"], list_item["class"])

    def test_should_reflect_shared_state_as_css_class(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        self.setup_bookmark(shared=True)
        html = self.render_template()
        soup = self.make_soup(html)

        list_item = soup.select_one("ul.bookmark-list > li[ld-bookmark-item]")
        self.assertIsNotNone(list_item)
        self.assertListEqual(["shared"], list_item["class"])

    def test_should_reflect_both_unread_and_shared_state_as_css_class(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        self.setup_bookmark(unread=True, shared=True)
        html = self.render_template()
        soup = self.make_soup(html)

        list_item = soup.select_one("ul.bookmark-list > li[ld-bookmark-item]")
        self.assertIsNotNone(list_item)
        self.assertListEqual(["unread", "shared"], list_item["class"])

    def test_show_bookmark_actions_for_owned_bookmarks(self):
        bookmark = self.setup_bookmark()
        html = self.render_template()

        self.assertViewLink(html, bookmark)
        self.assertBookmarkActions(html, bookmark)
        self.assertNoShareInfo(html, bookmark)

    def test_hide_view_link(self):
        bookmark = self.setup_bookmark()
        profile = self.get_or_create_test_user().profile
        profile.display_view_bookmark_action = False
        profile.save()

        html = self.render_template()
        self.assertViewLinkCount(html, bookmark, count=0)
        self.assertEditLinkCount(html, bookmark, count=1)
        self.assertArchiveLinkCount(html, bookmark, count=1)
        self.assertDeleteLinkCount(html, bookmark, count=1)

    def test_hide_edit_link(self):
        bookmark = self.setup_bookmark()
        profile = self.get_or_create_test_user().profile
        profile.display_edit_bookmark_action = False
        profile.save()

        html = self.render_template()
        self.assertViewLinkCount(html, bookmark, count=1)
        self.assertEditLinkCount(html, bookmark, count=0)
        self.assertArchiveLinkCount(html, bookmark, count=1)
        self.assertDeleteLinkCount(html, bookmark, count=1)

    def test_hide_archive_link(self):
        bookmark = self.setup_bookmark()
        profile = self.get_or_create_test_user().profile
        profile.display_archive_bookmark_action = False
        profile.save()

        html = self.render_template()
        self.assertViewLinkCount(html, bookmark, count=1)
        self.assertEditLinkCount(html, bookmark, count=1)
        self.assertArchiveLinkCount(html, bookmark, count=0)
        self.assertDeleteLinkCount(html, bookmark, count=1)

    def test_hide_remove_link(self):
        bookmark = self.setup_bookmark()
        profile = self.get_or_create_test_user().profile
        profile.display_remove_bookmark_action = False
        profile.save()

        html = self.render_template()
        self.assertViewLinkCount(html, bookmark, count=1)
        self.assertEditLinkCount(html, bookmark, count=1)
        self.assertArchiveLinkCount(html, bookmark, count=1)
        self.assertDeleteLinkCount(html, bookmark, count=0)

    def test_show_share_info_for_non_owned_bookmarks(self):
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        other_user.profile.enable_sharing = True
        other_user.profile.save()

        bookmark = self.setup_bookmark(user=other_user, shared=True)
        html = self.render_template(context_type=contexts.SharedBookmarkListContext)

        self.assertViewLink(
            html, bookmark, base_url=reverse("linkding:bookmarks.shared")
        )
        self.assertNoBookmarkActions(html, bookmark)
        self.assertShareInfo(html, bookmark)

    def test_share_info_user_link_keeps_query_params(self):
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        other_user.profile.enable_sharing = True
        other_user.profile.save()

        bookmark = self.setup_bookmark(user=other_user, shared=True, title="foo")
        html = self.render_template(
            url="/bookmarks?q=foo", context_type=contexts.SharedBookmarkListContext
        )

        self.assertInHTML(
            f"""
            <span>Shared by 
                <a href="?q=foo&user={bookmark.owner.username}">{bookmark.owner.username}</a>
            </span>
        """,
            html,
        )

    def test_preview_image_should_be_visible_when_preview_images_enabled(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_preview_images = True
        profile.save()

        bookmark = self.setup_bookmark(preview_image_file="preview.png")
        html = self.render_template()

        self.assertPreviewImageVisible(html, bookmark)

    def test_remote_preview_image_should_use_empty_alt_text(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_preview_images = True
        profile.save()

        bookmark = self.setup_bookmark()
        bookmark.preview_image_remote_url = "https://example.com/preview.png"
        bookmark.save(update_fields=["preview_image_remote_url"])
        html = self.render_template()
        soup = self.make_soup(html)
        preview_image = soup.select_one("img.preview-image")

        self.assertIsNotNone(preview_image)
        self.assertEqual(preview_image["src"], "https://example.com/preview.png")
        self.assertEqual(preview_image.get("alt"), "")

    def test_preview_image_should_be_hidden_when_preview_images_disabled(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_preview_images = False
        profile.save()

        bookmark = self.setup_bookmark(preview_image_file="preview.png")
        html = self.render_template()

        self.assertPreviewImageHidden(html, bookmark)

    def test_preview_image_shows_placeholder_when_there_is_no_preview_image(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_preview_images = True
        profile.save()

        self.setup_bookmark()
        html = self.render_template()

        self.assertPreviewImagePlaceholder(html)

    def test_favicon_should_be_visible_when_favicons_enabled(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_favicons = True
        profile.save()

        bookmark = self.setup_bookmark()
        from bookmarks.models import FaviconCache
        from bookmarks.utils import extract_hostname
        FaviconCache.objects.create(
            domain=extract_hostname(bookmark.url),
            favicon_file="https_example_com.png",
            status="success",
        )
        html = self.render_template()
        self.assertFaviconVisible(html, bookmark)

    def test_favicon_should_show_placeholder_when_there_is_no_icon(self):
        """favicon_file 为空时应显示 /favicon/{domain} URL（返回默认 favicon.svg）。"""
        profile = self.get_or_create_test_user().profile
        profile.enable_favicons = True
        profile.save()

        bookmark = self.setup_bookmark()
        html = self.render_template()

        soup = self.make_soup(html)
        favicon = soup.select_one(".favicon")
        self.assertIsNotNone(favicon)
        self.assertTrue(favicon["src"].startswith("/favicon/"))

    def test_favicon_should_be_hidden_when_favicons_disabled(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_favicons = False
        profile.save()

        bookmark = self.setup_bookmark()
        html = self.render_template()
        self.assertFaviconHidden(html, bookmark)

    def test_bookmark_url_should_be_hidden_by_default(self):
        profile = self.get_or_create_test_user().profile
        profile.save()

        bookmark = self.setup_bookmark()
        html = self.render_template()

        self.assertBookmarkURLHidden(html, bookmark)

    def test_show_bookmark_url_when_enabled(self):
        profile = self.get_or_create_test_user().profile
        profile.display_url = True
        profile.save()

        bookmark = self.setup_bookmark()
        html = self.render_template()

        self.assertBookmarkURLVisible(html, bookmark)

    def test_hide_bookmark_url_when_disabled(self):
        profile = self.get_or_create_test_user().profile
        profile.display_url = False
        profile.save()

        bookmark = self.setup_bookmark()
        html = self.render_template()

        self.assertBookmarkURLHidden(html, bookmark)

    def test_show_mark_as_read_when_unread(self):
        bookmark = self.setup_bookmark(unread=True)
        html = self.render_template()

        self.assertMarkAsReadButton(html, bookmark)

    def test_show_mark_as_unread_when_read(self):
        bookmark = self.setup_bookmark(unread=False)
        html = self.render_template()

        self.assertMarkAsUnreadButton(html, bookmark, count=1)

    def test_hide_mark_as_read_for_non_owned_bookmarks(self):
        other_user = self.setup_user(enable_sharing=True)

        bookmark = self.setup_bookmark(user=other_user, shared=True, unread=True)
        html = self.render_template(context_type=contexts.SharedBookmarkListContext)

        self.assertBookmarksLink(html, bookmark)
        self.assertMarkAsReadButton(html, bookmark, count=0)

    def test_show_unshare_button_when_shared(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        bookmark = self.setup_bookmark(shared=True)
        html = self.render_template()

        self.assertUnshareButton(html, bookmark)

    def test_hide_unshare_button_when_not_shared(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.save()

        bookmark = self.setup_bookmark(shared=False)
        html = self.render_template()

        self.assertUnshareButton(html, bookmark, count=0)

    def test_hide_unshare_button_when_sharing_is_disabled(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = False
        profile.save()

        bookmark = self.setup_bookmark(shared=True)
        html = self.render_template()

        self.assertUnshareButton(html, bookmark, count=0)

    def test_hide_unshare_for_non_owned_bookmarks(self):
        other_user = self.setup_user(enable_sharing=True)

        bookmark = self.setup_bookmark(user=other_user, shared=True)
        html = self.render_template(context_type=contexts.SharedBookmarkListContext)

        self.assertBookmarksLink(html, bookmark)
        self.assertUnshareButton(html, bookmark, count=0)

    def test_without_notes(self):
        self.setup_bookmark()
        html = self.render_template()

        self.assertNotes(html, "", 0)
        self.assertNotesToggle(html, 0)

    def test_with_notes(self):
        self.setup_bookmark(notes="Test note")
        html = self.render_template()

        note_html = "<p>Test note</p>"
        self.assertNotes(html, note_html, 1)

    def test_note_renders_markdown(self):
        self.setup_bookmark(notes='**Example:** `print("Hello world!")`')
        html = self.render_template()

        note_html = (
            '<p><strong>Example:</strong> <code>print("Hello world!")</code></p>'
        )
        self.assertNotes(html, note_html, 1)

    def test_note_renders_markdown_with_linkify(self):
        # Should linkify plain URL
        self.setup_bookmark(notes="Example: https://example.com")
        html = self.render_template()

        note_html = '<p>Example: <a href="https://example.com" rel="nofollow">https://example.com</a></p>'
        self.assertNotes(html, note_html, 1)

        # Should not linkify URL in markdown link
        self.setup_bookmark(notes="[https://example.com](https://example.com)")
        html = self.render_template()

        note_html = '<p><a href="https://example.com" rel="nofollow">https://example.com</a></p>'
        self.assertNotes(html, note_html, 1)

    def test_note_linkify_converts_schemeless_urls_to_https(self):
        self.setup_bookmark(notes="Example: example.com")
        html = self.render_template()

        note_html = '<p>Example: <a href="https://example.com" rel="nofollow">example.com</a></p>'
        self.assertNotes(html, note_html, 1)

        self.setup_bookmark(notes="Example: http://example.com")
        html = self.render_template()

        note_html = '<p>Example: <a href="http://example.com" rel="nofollow">http://example.com</a></p>'
        self.assertNotes(html, note_html, 1)

        self.setup_bookmark(notes="Example: https://example.com")
        html = self.render_template()

        note_html = '<p>Example: <a href="https://example.com" rel="nofollow">https://example.com</a></p>'
        self.assertNotes(html, note_html, 1)

        self.setup_bookmark(notes="Contact: hello@example.com")
        html = self.render_template()

        note_html = "<p>Contact: hello@example.com</p>"
        self.assertNotes(html, note_html, 1)

        self.setup_bookmark(notes="FTP: ftp://ftp.example.com")
        html = self.render_template()

        note_html = '<p>FTP: <a href="ftp://ftp.example.com" rel="nofollow">ftp://ftp.example.com</a></p>'
        self.assertNotes(html, note_html, 1)

    def test_note_cleans_html(self):
        self.setup_bookmark(notes='<script>alert("test")</script>')
        self.setup_bookmark(
            notes='<b ld-fetch="https://example.com" ld-on="click">bold text</b>'
        )
        html = self.render_template()

        note_html = '&lt;script&gt;alert("test")&lt;/script&gt;'
        self.assertIn(note_html, html, 1)

        note_html = "<b>bold text</b>"
        self.assertIn(note_html, html, 1)

    def test_notes_are_hidden_initially_by_default(self):
        self.setup_bookmark(notes="Test note")
        html = self.render_template()
        soup = self.make_soup(html)
        bookmark_list = soup.select_one("ul.bookmark-list.show-notes")

        self.assertIsNone(bookmark_list)

    def test_notes_are_hidden_initially_with_permanent_notes_disabled(self):
        profile = self.get_or_create_test_user().profile
        profile.permanent_notes = False
        profile.save()

        self.setup_bookmark(notes="Test note")
        html = self.render_template()
        soup = self.make_soup(html)
        bookmark_list = soup.select_one("ul.bookmark-list.show-notes")

        self.assertIsNone(bookmark_list)

    def test_notes_are_visible_initially_with_permanent_notes_enabled(self):
        profile = self.get_or_create_test_user().profile
        profile.permanent_notes = True
        profile.save()

        self.setup_bookmark(notes="Test note")
        html = self.render_template()
        soup = self.make_soup(html)
        # show-notes class is on the li element, not the ul
        bookmark_item = soup.select_one("li.show-notes")

        self.assertIsNotNone(bookmark_item)

    def test_toggle_notes_is_visible_by_default(self):
        self.setup_bookmark(notes="Test note")
        html = self.render_template()

        self.assertNotesToggle(html, 1)

    def test_toggle_notes_is_visible_with_permanent_notes_disabled(self):
        profile = self.get_or_create_test_user().profile
        profile.permanent_notes = False
        profile.save()

        self.setup_bookmark(notes="Test note")
        html = self.render_template()

        self.assertNotesToggle(html, 1)

    def test_toggle_notes_is_hidden_with_permanent_notes_enabled(self):
        profile = self.get_or_create_test_user().profile
        profile.permanent_notes = True
        profile.save()

        self.setup_bookmark(notes="Test note")
        html = self.render_template()

        self.assertNotesToggle(html, 0)

    def test_with_anonymous_user(self):
        profile = self.get_or_create_test_user().profile
        profile.enable_sharing = True
        profile.enable_public_sharing = True
        profile.save()

        bookmark = self.setup_bookmark()
        bookmark.date_added = timezone.now() - relativedelta(days=8)
        bookmark.web_archive_snapshot_url = (
            "https://web.archive.org/web/20230531200136/https://example.com"
        )
        bookmark.notes = '**Example:** `print("Hello world!")`'
        bookmark.shared = True
        bookmark.unread = True
        bookmark.save()

        html = self.render_template(
            context_type=contexts.SharedBookmarkListContext, user=AnonymousUser()
        )
        self.assertBookmarksLink(html, bookmark, link_target="_blank")
        # Anonymous users use default date_route (snapshot), not web_archive
        # so we don't check for web archive link
        self.assertNoBookmarkActions(html, bookmark)
        self.assertShareInfo(html, bookmark)
        self.assertMarkAsReadButton(html, bookmark, count=0)
        self.assertUnshareButton(html, bookmark, count=0)
        note_html = (
            '<p><strong>Example:</strong> <code>print("Hello world!")</code></p>'
        )
        self.assertNotes(html, note_html, 1)
        self.assertFaviconVisible(html, bookmark)

    def test_empty_state(self):
        html = self.render_template()

        self.assertInHTML(
            '<p class="empty-title h5">You have no bookmarks yet</p>', html
        )

    def test_empty_state_with_valid_query_no_results(self):
        self.setup_bookmark(title="Test Bookmark")
        html = self.render_template(url="/bookmarks?q=nonexistent")

        self.assertInHTML(
            '<p class="empty-title h5">You have no bookmarks yet</p>', html
        )

    def test_empty_state_with_invalid_query(self):
        self.setup_bookmark()
        html = self.render_template(url="/bookmarks?q=(test")

        self.assertInHTML('<p class="empty-title h5">Invalid search query</p>', html)
        self.assertIn("Expected RPAREN", html)

    def test_empty_state_with_legacy_search(self):
        profile = self.get_or_create_test_user().profile
        profile.legacy_search = True
        profile.save()

        self.setup_bookmark()
        html = self.render_template(url="/bookmarks?q=(test")

        # With legacy search, search queries are not validated
        self.assertInHTML(
            '<p class="empty-title h5">You have no bookmarks yet</p>', html
        )

    def test_pagination_is_sticky_by_default(self):
        self.setup_bookmark()
        html = self.render_template()

        soup = self.make_soup(html)
        pagination = soup.select_one(".bookmark-pagination")
        self.assertIsNotNone(pagination)
        self.assertEqual(pagination.get("data-sticky-on"), "true")

    def test_pagination_is_not_sticky_when_disabled_in_profile(self):
        self.setup_bookmark()
        profile = self.get_or_create_test_user().profile
        profile.sticky_pagination = False
        profile.save()
        html = self.render_template()

        soup = self.make_soup(html)
        pagination = soup.select_one(".bookmark-pagination")
        self.assertIsNotNone(pagination)
        self.assertEqual(pagination.get("data-sticky-on"), "false")

    def test_pagination_is_sticky_when_enabled_in_profile(self):
        self.setup_bookmark()
        profile = self.get_or_create_test_user().profile
        profile.sticky_pagination = True
        profile.save()
        html = self.render_template()

        soup = self.make_soup(html)
        pagination = soup.select_one(".bookmark-pagination")
        self.assertIsNotNone(pagination)
        self.assertEqual(pagination.get("data-sticky-on"), "true")

    def test_items_per_page_is_30_by_default(self):
        self.setup_numbered_bookmarks(50)
        html = self.render_template()

        soup = self.make_soup(html)
        bookmarks = soup.select("ul.bookmark-list > li[ld-bookmark-item]")
        self.assertEqual(30, len(bookmarks))

    def test_items_per_page_is_configurable(self):
        self.setup_numbered_bookmarks(50)
        profile = self.get_or_create_test_user().profile
        profile.items_per_page = 10
        profile.save()
        html = self.render_template()

        soup = self.make_soup(html)
        bookmarks = soup.select("ul.bookmark-list > li[ld-bookmark-item]")
        self.assertEqual(10, len(bookmarks))

    def test_no_actions_rendered_when_is_preview(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_date_route = UserProfile.BOOKMARK_DATE_ROUTE_WEB_ARCHIVE
        profile.save()
        bookmark = self.setup_bookmark()
        bookmark.date_added = timezone.now() - relativedelta(days=8)
        bookmark.web_archive_snapshot_url = "https://example.com"
        bookmark.save()

        html = self.render_template(is_preview=True)

        # Verify no actions are rendered
        self.assertNoViewLink(html, bookmark)
        self.assertNoBookmarkActions(html, bookmark)
        self.assertMarkAsReadButton(html, bookmark, count=0)
        self.assertUnshareButton(html, bookmark, count=0)
        self.assertNotesToggle(html, count=0)

        # But date should still be rendered
        self.assertWebArchiveLink(html, "1 week ago", bookmark.web_archive_snapshot_url)


    # ---- Health check 区块 ----

    def test_health_indicator_rendered_with_status(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_status_display_mode = UserProfile.ACTION_DISPLAY_MODE_TEXT
        profile.save()

        bookmark = self.setup_bookmark()
        bookmark.health_status = "dead"
        bookmark.health_details = {
            "checked_at": timezone.now().isoformat(),
            "http_status": 404,
            "reason": "HTTP 404",
        }
        bookmark.save()

        html = self.render_template()
        soup = self.make_soup(html)
        indicator = soup.select_one("ld-health-indicator")
        self.assertIsNotNone(indicator)
        # dot 状态色 + 状态文字
        self.assertIsNotNone(indicator.select_one(".health-dot--dead"))
        self.assertEqual(indicator.select_one(".health-text").get_text(strip=True), "Dead")
        # popover 详情：状态、HTTP 码、刷新按钮
        self.assertEqual(
            indicator.select_one(".health-popover-status").get_text(strip=True), "Dead"
        )
        self.assertEqual(
            indicator.select_one(".health-popover-row-http").get_text(strip=True),
            "HTTP 404",
        )
        self.assertIsNotNone(indicator.select_one("[data-health-check]"))

    def test_health_indicator_shows_unknown_when_unchecked(self):
        """未检查：indicator 仍渲染，状态派生为 Unknown（与详情弹层一致）。"""
        bookmark = self.setup_bookmark()  # 从未检查 → health_status 为 NULL
        html = self.render_template()
        soup = self.make_soup(html)
        indicator = soup.select_one("ld-health-indicator")
        self.assertIsNotNone(indicator)
        # 默认 icon 模式：只有带颜色 dot（无文字）
        self.assertIsNotNone(indicator.select_one(".health-dot--unknown"))
        self.assertIsNone(indicator.select_one(".health-text"))

    def test_health_indicator_icon_mode_uses_dot(self):
        # 默认 status_display_mode = icon：只有带颜色 dot（无文字）
        bookmark = self.setup_bookmark()
        bookmark.health_status = "blocked"
        bookmark.health_details = {"checked_at": timezone.now().isoformat(), "http_status": 403}
        bookmark.save()

        html = self.render_template()
        soup = self.make_soup(html)
        indicator = soup.select_one("ld-health-indicator")
        self.assertIsNotNone(indicator)
        self.assertIn("health-icon-mode", indicator.get("class", []))
        self.assertIsNotNone(indicator.select_one(".health-dot--blocked"))
        self.assertIsNone(indicator.select_one(".health-text"))

    def test_compact_view_renders_compact_row_without_expanded_blocks(self):
        user = self.get_or_create_test_user()
        user.profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_COMPACT
        user.profile.save()

        tag = self.setup_tag(name="tag1")
        bookmark = self.setup_bookmark(
            description="A long description that must not render in compact",
            author="Test author",
            tags=[tag],
            notes="Secret note",
        )

        html = self.render_template()
        soup = self.make_soup(html)

        ul = soup.select_one("ul.bookmark-list")
        self.assertIn("view-compact", ul.get("class", []))

        # 非表格视图不包裹表格横向轨道
        self.assertIsNone(soup.select_one(".table-body-scroll"))
        self.assertIsNone(soup.select_one(".table-header-sticky"))
        self.assertIsNone(soup.select_one(".table-footer-scroll"))

        # 紧凑分支：不渲染 URL 行与预览图；描述/笔记/动作留在 DOM 中由 CSS 收起
        self.assertIsNone(soup.select_one(".url-path"))
        self.assertIsNone(soup.select_one(".preview-image"))

        # 常驻渲染标题 + 作者名，不渲染独立日期；健康指示器由 CSS 隐藏
        self.assertIsNotNone(soup.select_one(".compact-head"))
        # 紧凑视图不渲染标签（右侧组留给展开按钮，作者名在标题行展示）
        self.assertIsNone(soup.select_one(".compact-tags"))
        author = soup.select_one(".compact-head .title .author")
        self.assertIsNotNone(author)
        self.assertEqual(author.get_text(strip=True), bookmark.author)
        self.assertIsNone(soup.select_one(".compact-date"))

        # 展开折叠按钮：移动端与桌面端常驻渲染，携带展开/折叠两套 tooltip 文案
        toggle_btn = soup.select_one(".compact-toggle-btn")
        self.assertIsNotNone(toggle_btn)
        self.assertEqual(toggle_btn.get("aria-expanded"), "false")
        self.assertEqual(toggle_btn.get("title"), "Expand")
        self.assertEqual(toggle_btn.get("data-title-expand"), "Expand")
        self.assertEqual(toggle_btn.get("data-title-collapse"), "Collapse")

        # 标题链接与动作（含详情入口）仍然存在
        self.assertIsNotNone(
            soup.select_one(f'a.title-link[href="{bookmark.url}"]')
        )
        self.assertIsNotNone(soup.select_one("a.view-action"))

        # 描述、笔记片段仍在 DOM 中，hover/点行展开时按 list 样式显示
        self.assertIsNotNone(soup.select_one(".description"))
        self.assertIsNotNone(soup.select_one(".inline-edit-notes"))

    def test_default_profile_uses_list_view(self):
        self.setup_bookmark(description="A description")

        html = self.render_template()
        soup = self.make_soup(html)

        ul = soup.select_one("ul.bookmark-list")
        self.assertNotIn("view-compact", ul.get("class", []))
        self.assertIsNone(soup.select_one(".table-body-scroll"))
        self.assertIsNone(soup.select_one(".table-header-sticky"))
        self.assertIsNone(soup.select_one(".table-footer-scroll"))
        self.assertIsNotNone(soup.select_one(".content"))
        self.assertIsNotNone(soup.select_one(".description"))

    # ---- 按日期分组（与视图模式正交） ----

    def test_group_by_date_renders_group_headers_by_default(self):
        self.setup_numbered_bookmarks(5)

        html = self.render_template()
        soup = self.make_soup(html)

        headers = soup.select("ul.bookmark-list > li.group-header")
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0].get("data-group-key"), "today")
        self.assertEqual(
            headers[0].select_one(".group-title").get_text(strip=True), "Today"
        )
        # 组内数量
        self.assertEqual(
            headers[0].select_one(".group-count").get_text(strip=True), "5"
        )
        # 折叠交互结构
        self.assertEqual(headers[0].select_one(".group-toggle").get("aria-expanded"), "true")
        self.assertIsNotNone(headers[0].select_one(".group-chevron"))

    def test_group_by_date_disabled_hides_group_headers(self):
        profile = self.get_or_create_test_user().profile
        profile.group_by_date = False
        profile.save()
        self.setup_numbered_bookmarks(5)

        html = self.render_template()
        soup = self.make_soup(html)

        self.assertIsNone(soup.select_one("ul.bookmark-list > li.group-header"))
        self.assertEqual(len(soup.select("ul.bookmark-list > li[ld-bookmark-item]")), 5)

    def test_group_by_date_splits_into_date_groups(self):
        now = timezone.now()
        self.setup_bookmark(url="https://today.example", added=now)
        self.setup_bookmark(url="https://yesterday.example", added=now - datetime.timedelta(days=1))
        # 40 天前必然跨月，归入按月分组
        month_ago = now - datetime.timedelta(days=40)
        self.setup_bookmark(url="https://month.example", added=month_ago)

        html = self.render_template()
        soup = self.make_soup(html)

        headers = soup.select("ul.bookmark-list > li.group-header")
        self.assertEqual(
            [h.get("data-group-key") for h in headers],
            ["today", "yesterday", timezone.localdate(month_ago).strftime("%Y-%m")],
        )
        # 月份组标题按本地化月格式显示
        month_title = headers[2].select_one(".group-title").get_text(strip=True)
        self.assertEqual(month_title, timezone.localdate(month_ago).strftime("%Y年%m月"))
        # 各组数量
        counts = [h.select_one(".group-count").get_text(strip=True) for h in headers]
        self.assertEqual(counts, ["1", "1", "1"])

    def test_group_count_shows_total_across_pages_with_hint(self):
        profile = self.get_or_create_test_user().profile
        profile.items_per_page = 3
        profile.save()
        self.setup_numbered_bookmarks(7)

        # 第 1 页：主数字 = 本页 3 条，引导提示为组内区间 + 总数（1-3，共 7）
        html = self.render_template(url="/bookmarks?page=1")
        soup = self.make_soup(html)
        header = soup.select_one("ul.bookmark-list > li.group-header")
        self.assertEqual(header.select_one(".group-count").get_text(strip=True), "3")
        more = header.select_one(".group-more")
        self.assertIsNotNone(more)
        text = more.get_text(strip=True)
        self.assertIn("1-3", text)
        self.assertIn("7", text)

        # 第 3 页：本页 1 条，区间为该组第 7 条（7 = 3 + 3 + 1）
        html = self.render_template(url="/bookmarks?page=3")
        soup = self.make_soup(html)
        header = soup.select_one("ul.bookmark-list > li.group-header")
        self.assertEqual(header.select_one(".group-count").get_text(strip=True), "1")
        more = header.select_one(".group-more")
        self.assertIsNotNone(more)
        text = more.get_text(strip=True)
        self.assertIn("7-7", text)
        self.assertIn("7", text)

    def test_group_count_hides_hint_when_group_fully_on_page(self):
        # 组在单页内显示全：无引导提示
        self.setup_numbered_bookmarks(3)
        html = self.render_template()
        soup = self.make_soup(html)
        header = soup.select_one("ul.bookmark-list > li.group-header")
        self.assertEqual(header.select_one(".group-count").get_text(strip=True), "3")
        self.assertIsNone(header.select_one(".group-more"))

    def test_group_key_computation(self):
        from bookmarks.views.contexts import _bookmark_group_key

        today = timezone.localdate()
        midnight = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time.min)
        )

        self.assertEqual(_bookmark_group_key(midnight), "today")
        self.assertEqual(
            _bookmark_group_key(midnight - datetime.timedelta(days=1)), "yesterday"
        )
        # 40 天前必然跨月 -> 按月分组键
        month_ago = today - datetime.timedelta(days=40)
        self.assertEqual(
            _bookmark_group_key(midnight - datetime.timedelta(days=40)),
            month_ago.strftime("%Y-%m"),
        )
        # this_week：本周一至昨天；若该区间存在（今天非周一/周二）
        monday = today - datetime.timedelta(days=today.weekday())
        if monday <= today - datetime.timedelta(days=2):
            monday_dt = timezone.make_aware(
                datetime.datetime.combine(monday, datetime.time.min)
            )
            self.assertEqual(_bookmark_group_key(monday_dt), "this_week")
        # this_month：本月 1 号起至本周一之前；若该区间存在（月初不在本周内）
        month_first = today.replace(day=1)
        if month_first < monday and month_first != today - datetime.timedelta(days=1):
            month_first_dt = timezone.make_aware(
                datetime.datetime.combine(month_first, datetime.time.min)
            )
            self.assertEqual(_bookmark_group_key(month_first_dt), "this_month")


class BookmarkTableViewTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    # 复用列表模板的渲染辅助，但不继承其测试用例（那些断言的是 list/compact 标记）
    render_template = BookmarkListTemplateTest.render_template

    def setUp(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_TABLE
        profile.save()

    def test_table_view_renders_header_and_rows(self):
        bookmark = self.setup_bookmark(
            description="Some description", tags=[self.setup_tag()]
        )
        html = self.render_template()
        soup = self.make_soup(html)

        # Django 的 {# #} 注释不支持换行，写多行会原样渲染成文本（曾把注释打进表头）
        self.assertNotIn("{#", html)

        headerList = soup.select_one("ul.table-header-list")
        bodyUl = soup.select_one(".table-body-scroll ul.bookmark-list.view-table")
        self.assertIsNotNone(headerList)
        self.assertIsNotNone(bodyUl)
        self.assertIn("view-table", bodyUl.get("class", []))

        header = headerList.select_one("li.table-header")
        self.assertIsNotNone(header)
        labels = [
            el.get_text(strip=True) for el in header.select(".th .th-label")
        ]
        self.assertEqual(
            labels,
            ["Title", "Author", "Date", "Tags", "Description", "Notes", "Status"],
        )

        row = bodyUl.select_one("li[ld-bookmark-item]")
        self.assertIsNotNone(row)
        self.assertIn(bookmark.resolved_title, row.select_one(".cell-title").text)
        self.assertIsNotNone(row.select_one(".cell-tags .tags a"))
        self.assertIn("Some description", row.select_one(".cell-desc").text)
        # state/health 不在默认列配置中
        self.assertIsNone(row.select_one(".cell-state"))
        self.assertIsNone(row.select_one(".cell-health"))

        # 表格视图：表头独立 sticky 轨道，主体数据由横向轨道 .table-body-scroll 包裹，
        # 底部常驻横滚条轨道 .table-footer-scroll 位于主体轨道之后，分页器留在三条轨道之外
        sticky = soup.select_one(".table-header-sticky")
        bodyScroll = soup.select_one(".table-body-scroll")
        footerScroll = soup.select_one(".table-footer-scroll")
        self.assertIsNotNone(sticky)
        self.assertIsNotNone(bodyScroll)
        self.assertIsNotNone(footerScroll)
        self.assertIsNotNone(sticky.select_one("ul.table-header-list"))
        self.assertIsNotNone(bodyScroll.select_one("ul.bookmark-list.view-table"))
        self.assertIsNotNone(footerScroll.select_one("ul.table-footer-list"))
        self.assertIsNone(bodyScroll.select_one(".bookmark-pagination"))

    def test_table_view_keeps_single_data_list(self):
        """两条轨道带各自的标记类，前端靠它们排除，取数据列表只能命中一个。"""
        self.setup_bookmark()
        soup = self.make_soup(self.render_template())

        data_lists = soup.select(
            "ul.bookmark-list:not(.table-header-list):not(.table-footer-list)"
        )
        self.assertEqual(len(data_lists), 1)
        self.assertIn("view-table", data_lists[0].get("class", []))
        self.assertIn("table-header-list", soup.select_one(".table-header-sticky ul").get("class", []))
        self.assertIn("table-footer-list", soup.select_one(".table-footer-scroll ul").get("class", []))

    def test_table_view_row_has_ops_slot(self):
        """行尾操作列是行内最后一条网格轨道（吸附靠 sticky），悬停操作栏挂在它上面。"""
        self.setup_bookmark()
        soup = self.make_soup(self.render_template())

        row = soup.select_one(".table-body-scroll li[ld-bookmark-item]")
        cells = row.select(":scope > .cell")
        ops = row.select_one(":scope > .cell-ops")
        self.assertIsNotNone(ops)
        # 轨道号 = 选择框列 + 可见列 + 操作列，即最后一个
        self.assertEqual(
            (ops.get("style") or "").strip(),
            f"grid-column: {len(cells)}",
        )
        self.assertIsNotNone(ops.select_one(".row-ops-btn"))
        self.assertIsNotNone(ops.select_one(".actions"))

    def test_table_view_column_grid_uses_server_side_widths(self):
        """首帧列宽由服务端渲染，与前端 resize 口径同源。"""
        self.setup_bookmark()
        soup = self.make_soup(self.render_template())

        for selector in (
            ".table-header-sticky ul",
            ".table-body-scroll ul",
            ".table-footer-scroll ul",
        ):
            style = soup.select_one(selector).get("style", "")
            self.assertIn("minmax(", style)
            self.assertNotIn("minmax(0,", style)

    def test_table_view_date_grouping(self):
        profile = self.get_or_create_test_user().profile
        profile.group_by_date = True
        profile.save()
        self.setup_bookmark()
        html = self.render_template()
        soup = self.make_soup(html)
        # 组头在表格内跨全行渲染，不再强制关闭
        header = soup.select_one("ul.view-table li.group-header.table-group-header")
        self.assertIsNotNone(header)

    def test_table_view_sort_links(self):
        self.setup_bookmark()
        html = self.render_template(url="/bookmarks?sort=added_desc")
        soup = self.make_soup(html)

        # 当前按 added_desc 排序：date 列点击切到 asc，title 列默认 asc
        date_link = soup.select_one('li.table-header .th[data-key="date"] a')
        self.assertIn("sort=added_asc", date_link["href"])
        title_link = soup.select_one('li.table-header .th[data-key="title"] a')
        self.assertIn("sort=title_asc", title_link["href"])

        html = self.render_template(url="/bookmarks?sort=title_asc")
        soup = self.make_soup(html)
        title_link = soup.select_one('li.table-header .th[data-key="title"] a')
        self.assertIn("sort=title_desc", title_link["href"])

    def test_table_view_sort_resets_page(self):
        self.setup_bookmark()
        html = self.render_template(url="/bookmarks?page=3")
        soup = self.make_soup(html)
        date_link = soup.select_one('li.table-header .th[data-key="date"] a')
        self.assertNotIn("page=3", date_link["href"])

    def test_table_view_column_config(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_table_columns = [
            {"key": "date", "w": 1},
            {"key": "domain", "w": 1},
            {"key": "bogus", "w": 1},
        ]
        profile.save()
        self.setup_bookmark()

        html = self.render_template()
        soup = self.make_soup(html)
        labels = [
            el.get_text(strip=True)
            for el in soup.select("li.table-header .th .th-label")
        ]
        # 非法 key 被丢弃；title 列被强制保留并排在最前
        self.assertEqual(labels, ["Title", "Date", "Domain"])


class BookmarkTableStatusColumnTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """状态列：直接复用书签工具栏的状态按钮。"""

    render_template = BookmarkListTemplateTest.render_template

    def setUp(self):
        profile = self.get_or_create_test_user().profile
        profile.bookmark_view_mode = UserProfile.BOOKMARK_VIEW_MODE_TABLE
        profile.bookmark_table_columns = [
            {"key": "title", "w": 2.4, "on": True},
            {"key": "status", "w": 1.4, "on": True},
        ]
        profile.save()

    def test_status_cell_renders_toolbar_buttons(self):
        self.setup_bookmark(unread=True)
        soup = self.make_soup(self.render_template())
        cell = soup.select_one("li[ld-bookmark-item] .cell-status")
        self.assertIsNotNone(cell)
        # 未读书签：工具栏的「标记为已读」按钮出现在状态列里
        self.assertIsNotNone(cell.select_one('[data-action="mark_as_read"]'))

    def test_read_bookmark_shows_mark_unread_button(self):
        self.setup_bookmark(unread=False)
        soup = self.make_soup(self.render_template())
        cell = soup.select_one("li[ld-bookmark-item] .cell-status")
        self.assertIsNotNone(cell)
        self.assertIsNotNone(cell.select_one('[data-action="mark_as_unread"]'))
