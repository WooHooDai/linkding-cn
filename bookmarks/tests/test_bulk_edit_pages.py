from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookmarks.models import Annotation, Bookmark
from bookmarks.tests.helpers import BookmarkFactoryMixin


class BulkEditPagesTest(TestCase, BookmarkFactoryMixin):
    """批量编辑的根节点契约。

    行为属性各页不同（书签 ld-bulk-edit，高亮 ld-highlights-bulk-edit），
    样式与表单却必须一致，所以 CSS 认 .bulk-edit-root 类，JS 认 [data-bulk-form]。
    少了任一个，页面能点但提示条不显形 / 提交拿不到选区，故在此钉住。
    """

    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)

    def test_pages_expose_bulk_root_and_form(self):
        self.setup_bookmark()
        self.setup_bookmark(is_archived=True)
        trashed = self.setup_bookmark()
        Bookmark.objects.filter(id=trashed.id).update(
            is_deleted=True, date_deleted=timezone.now()
        )
        bookmark = self.setup_bookmark()
        Annotation.objects.create(
            bookmark=bookmark,
            selector={"type": "TextQuoteSelector", "exact": "a highlight"},
            selected_text="a highlight",
        )

        # 每个列表页一套存储命名空间：选区是 ID 列表，跨列表复用只会张冠李戴
        pages = {
            reverse("linkding:bookmarks.index"): "active",
            reverse("linkding:bookmarks.archived"): "archived",
            reverse("linkding:bookmarks.trashed"): "trash",
            reverse("linkding:bookmarks.highlights"): "highlights",
        }
        for url, namespace in pages.items():
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()

                self.assertIn("bulk-edit-root", html)
                self.assertIn("data-bulk-form", html)
                self.assertIn("bulk-edit-bar", html)
                self.assertIn(f'data-bulk-storage="{namespace}"', html)
