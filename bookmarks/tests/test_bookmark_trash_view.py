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

    def test_trash_actions_ignore_bookmark_page_action_settings(self):
        bookmark = self._setup_trashed_bookmark()
        profile = self.user.profile
        profile.bookmark_actions = [
            {"key": key, "enabled": False} for key in profile.ACTION_KEYS
        ]
        profile.save()

        response = self.client.get(reverse("linkding:bookmarks.trashed"))
        html = response.content.decode()

        # 两套动作互不影响：书签页全关也不影响回收站的恢复与彻底删除
        self.assertIn(f'name="restore" value="{bookmark.id}"', html)
        self.assertIn(f'name="remove" value="{bookmark.id}"', html)

    def test_trash_actions_have_own_defaults(self):
        profile = self.user.profile
        self.assertEqual(
            [item["key"] for item in profile.get_trash_bookmark_actions()],
            ["edit", "view", "read", "archive", "restore", "remove", "highlight"],
        )
        self.assertEqual(
            [
                item["key"]
                for item in profile.get_trash_bookmark_actions()
                if item["enabled"]
            ],
            ["edit", "view", "restore", "remove"],
        )

    def test_trash_drawer_uses_trash_action_list(self):
        self._setup_trashed_bookmark()

        html = self.client.get(reverse("linkding:bookmarks.trashed")).content.decode()
        self.assertIn('data-drawer-list="trash_bookmark_actions"', html)

        html = self.client.get(reverse("linkding:bookmarks.index")).content.decode()
        self.assertIn('data-drawer-list="bookmark_actions"', html)

    def test_trash_toolbar_shows_default_actions(self):
        bookmark = self._setup_trashed_bookmark()
        html = self.client.get(reverse("linkding:bookmarks.trashed")).content.decode()

        self.assertIn('class="edit-action"', html)
        self.assertIn('class="view-action"', html)
        self.assertIn(f'name="restore" value="{bookmark.id}"', html)
        self.assertIn(f'name="remove" value="{bookmark.id}"', html)
        self.assertNotIn('name="archive"', html)
        self.assertNotIn('name="trash"', html)

    def test_trash_action_settings_save_independently(self):
        profile = self.user.profile
        response = self.client.post(
            reverse("linkding:settings.save"),
            {
                "form_id": "profile_quick",
                "form_fields": "trash_bookmark_actions",
                "trash_bookmark_actions": (
                    '[{"key": "restore", "enabled": false},'
                    ' {"key": "remove", "enabled": false}]'
                ),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        actions = {
            item["key"]: item["enabled"]
            for item in profile.get_trash_bookmark_actions()
        }
        self.assertFalse(actions["restore"])
        self.assertFalse(actions["remove"])
        # 未提交的键回落到默认值
        self.assertTrue(actions["edit"])
        # 书签页那份不受影响
        bookmark_actions = {
            item["key"]: item["enabled"]
            for item in profile.get_bookmark_actions()
        }
        self.assertTrue(bookmark_actions["remove"])
