from django.test import TestCase
from django.urls import reverse

from bookmarks.models import Annotation
from bookmarks.tests.helpers import BookmarkFactoryMixin, HtmlTestMixin


class HighlightDetailsModalTest(TestCase, BookmarkFactoryMixin, HtmlTestMixin):
    """条目里的「查看」把书签详情弹窗就地拉起来，不离开高亮列表。"""

    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)
        self.bookmark = self.setup_bookmark(title="a bookmark")
        Annotation.objects.create(
            bookmark=self.bookmark,
            selector={"type": "TextQuoteSelector", "exact": "a highlight"},
            selected_text="a highlight",
        )

    def render(self, **params):
        response = self.client.get(reverse("linkding:bookmarks.highlights"), params)
        self.assertEqual(response.status_code, 200)
        return self.make_soup(response.content.decode())

    def test_item_has_view_action_pointing_at_details_url(self):
        soup = self.render()
        view_link = soup.select_one("li.highlight-item a[data-turbo-frame='details-modal']")
        self.assertIsNotNone(view_link)
        # 必须是绝对路径：相对地址 Turbo 不接管，会退化成整页导航并滚回顶部
        self.assertTrue(
            view_link["href"].startswith(reverse("linkding:bookmarks.highlights"))
        )
        self.assertIn(f"details={self.bookmark.id}", view_link["href"])
        self.assertEqual(view_link["data-turbo-action"], "replace")

    def test_details_param_renders_modal_on_highlights_page(self):
        soup = self.render(details=self.bookmark.id)
        self.assertIsNotNone(soup.select_one("ld-details-modal"))
        self.assertIsNotNone(soup.select_one("turbo-frame#details-modal"))

    def test_modal_closes_back_to_highlights_page(self):
        soup = self.render(q="highlight", details=self.bookmark.id)
        modal = soup.select_one("ld-details-modal")
        close_url = modal["data-close-url"]
        self.assertTrue(close_url.startswith(reverse("linkding:bookmarks.highlights")))
        # 关闭时保留当前筛选，不带 details
        self.assertIn("q=highlight", close_url)
        self.assertNotIn("details=", close_url)

    def test_frame_request_returns_only_the_modal(self):
        response = self.client.get(
            reverse("linkding:bookmarks.highlights"),
            {"details": self.bookmark.id},
            HTTP_TURBO_FRAME="details-modal",
        )
        self.assertEqual(response.status_code, 200)
        soup = self.make_soup(response.content.decode())
        self.assertIsNotNone(soup.select_one("ld-details-modal"))
        self.assertIsNone(soup.select_one(".highlights-page"))
