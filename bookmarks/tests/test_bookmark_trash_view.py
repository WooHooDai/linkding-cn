import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookmarks.models import Bookmark
from bookmarks.tests.helpers import BookmarkFactoryMixin


class BookmarkTrashViewTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.get_or_create_test_user()
        self.client.force_login(self.user)

    def _setup_trashed_bookmark(self):
        bookmark = self.setup_bookmark()
        Bookmark.objects.filter(id=bookmark.id).update(
            is_deleted=True, date_deleted=timezone.now()
        )
        return bookmark

    def _get_bulk_action_values(self, response):
        return re.findall(r'<option value="(bulk_[a-z_]+)"', response.content.decode())

    def test_allowed_bulk_actions(self):
        self._setup_trashed_bookmark()
        response = self.client.get(reverse("linkding:bookmarks.trashed"))
        values = self._get_bulk_action_values(response)

        for value in ["bulk_restore", "bulk_delete", "bulk_remove_snapshot"]:
            self.assertIn(value, values)

    def test_bulk_actions_not_applicable_to_trashed_bookmarks_are_hidden(self):
        self._setup_trashed_bookmark()
        response = self.client.get(reverse("linkding:bookmarks.trashed"))
        values = self._get_bulk_action_values(response)

        # 已删除的书签做健康检查/元数据补齐没有意义
        for value in [
            "bulk_check_health",
            "bulk_enrich_metadata",
            "bulk_refresh_metadata",
        ]:
            self.assertNotIn(value, values)
