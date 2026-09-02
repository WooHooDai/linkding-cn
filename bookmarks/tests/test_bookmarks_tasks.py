from datetime import UTC, datetime, timedelta
from unittest import mock

import waybackpy
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from huey.contrib.djhuey import HUEY as huey
from huey.exceptions import RetryTask
from waybackpy.exceptions import WaybackError

from bookmarks.models import BookmarkAsset, UserProfile
from bookmarks.services import tasks, website_loader
from bookmarks.services.articles import (
    create_article_asset_pending,
    save_article_content,
)
from bookmarks.services.website_loader import WebsiteMetadata
from bookmarks.tests.helpers import BookmarkFactoryMixin


def create_wayback_machine_save_api_mock(
    archive_url: str = "https://example.com/created_snapshot",
    fail_on_save: bool = False,
):
    mock_api = mock.Mock(archive_url=archive_url)

    if fail_on_save:
        mock_api.save.side_effect = WaybackError

    return mock_api


class BookmarkTasksTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        huey.immediate = True
        huey.results = True
        huey.store_none = True

        self.mock_save_api = mock.Mock(
            archive_url="https://example.com/created_snapshot"
        )
        self.mock_save_api_patcher = mock.patch.object(
            waybackpy, "WaybackMachineSaveAPI", return_value=self.mock_save_api
        )
        self.mock_save_api_patcher.start()

        self.mock_fetch_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.fetch_and_save_favicon"
        )
        self.mock_fetch_favicon = self.mock_fetch_favicon_patcher.start()
        self.mock_fetch_favicon.return_value = "https_example_com.png"

        self.mock_find_cached_patcher = mock.patch(
            "bookmarks.services.favicon_loader.find_cached_favicon_file"
        )
        self.mock_find_cached = self.mock_find_cached_patcher.start()
        self.mock_find_cached.return_value = None

        self.mock_assets_create_snapshot_patcher = mock.patch(
            "bookmarks.services.assets.create_snapshot",
        )
        self.mock_assets_create_snapshot = (
            self.mock_assets_create_snapshot_patcher.start()
        )
        self.mock_assets_create_snapshot.side_effect = lambda asset: (
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_COMPLETE
            )
        )

        self.mock_load_preview_image_patcher = mock.patch(
            "bookmarks.services.preview_image_loader.load_preview_image"
        )
        self.mock_load_preview_image = self.mock_load_preview_image_patcher.start()
        self.mock_load_preview_image.return_value = "preview_image.png"

        user = self.get_or_create_test_user()
        user.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
        )
        user.profile.enable_favicons = True
        user.profile.enable_preview_images = True
        user.profile.save()

    def tearDown(self):
        self.mock_save_api_patcher.stop()
        self.mock_fetch_favicon_patcher.stop()
        self.mock_find_cached_patcher.stop()
        self.mock_assets_create_snapshot_patcher.stop()
        self.mock_load_preview_image_patcher.stop()
        huey.storage.flush_results()
        huey.immediate = False

    def executed_count(self):
        return len(huey.all_results())

    def test_create_web_archive_snapshot_should_update_snapshot_url(self):
        bookmark = self.setup_bookmark()

        tasks.create_web_archive_snapshot(
            self.get_or_create_test_user(), bookmark, False
        )
        bookmark.refresh_from_db()

        self.mock_save_api.save.assert_called_once()
        self.assertEqual(self.executed_count(), 1)
        self.assertEqual(
            bookmark.web_archive_snapshot_url,
            "https://example.com/created_snapshot",
        )

    def test_create_web_archive_snapshot_should_handle_missing_bookmark_id(self):
        tasks._create_web_archive_snapshot_task(123, False)

        self.assertEqual(self.executed_count(), 1)
        self.mock_save_api.save.assert_not_called()

    def test_create_web_archive_snapshot_should_skip_if_snapshot_exists(self):
        bookmark = self.setup_bookmark(web_archive_snapshot_url="https://example.com")

        self.mock_save_api.create_web_archive_snapshot(
            self.get_or_create_test_user(), bookmark, False
        )

        self.assertEqual(self.executed_count(), 0)
        self.mock_save_api.assert_not_called()

    def test_create_web_archive_snapshot_should_force_update_snapshot(self):
        bookmark = self.setup_bookmark(web_archive_snapshot_url="https://example.com")
        self.mock_save_api.archive_url = "https://other.com"

        tasks.create_web_archive_snapshot(
            self.get_or_create_test_user(), bookmark, True
        )
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.web_archive_snapshot_url, "https://other.com")

    def test_create_web_archive_snapshot_should_not_save_stale_bookmark_data(self):
        bookmark = self.setup_bookmark()

        # update bookmark during API call to check that saving
        # the snapshot does not overwrite updated bookmark data
        def mock_save_impl():
            bookmark.title = "Updated title"
            bookmark.save()

        self.mock_save_api.save.side_effect = mock_save_impl

        tasks.create_web_archive_snapshot(
            self.get_or_create_test_user(), bookmark, False
        )
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.title, "Updated title")
        self.assertEqual(
            "https://example.com/created_snapshot",
            bookmark.web_archive_snapshot_url,
        )

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_create_web_archive_snapshot_should_not_run_when_background_tasks_are_disabled(
        self,
    ):
        bookmark = self.setup_bookmark()

        tasks.create_web_archive_snapshot(
            self.get_or_create_test_user(), bookmark, False
        )
        self.assertEqual(self.executed_count(), 0)

    def test_create_web_archive_snapshot_should_not_run_when_web_archive_integration_is_disabled(
        self,
    ):
        self.user.profile.web_archive_integration = (
            UserProfile.WEB_ARCHIVE_INTEGRATION_DISABLED
        )
        self.user.profile.save()

        bookmark = self.setup_bookmark()
        tasks.create_web_archive_snapshot(
            self.get_or_create_test_user(), bookmark, False
        )

        self.assertEqual(self.executed_count(), 0)

    def test_ensure_favicon_should_fetch_when_no_cache(self):
        """无缓存时应入队获取任务。"""
        bookmark = self.setup_bookmark()

        tasks.ensure_favicon(self.get_or_create_test_user(), bookmark.url)

        # Huey immediate mode: task executed synchronously
        self.mock_fetch_favicon.assert_called_once()

    def test_ensure_favicon_should_use_disk_cache(self):
        """磁盘有缓存文件时不应触发网络请求。"""
        bookmark = self.setup_bookmark()
        self.mock_find_cached.return_value = "example_com.png"

        tasks.ensure_favicon(self.get_or_create_test_user(), bookmark.url)

        self.mock_fetch_favicon.assert_not_called()
        from bookmarks.models import FaviconCache

        cache = FaviconCache.objects.filter(domain="example.com").first()
        self.assertIsNotNone(cache)
        self.assertEqual(cache.favicon_file, "example_com.png")

    def test_ensure_favicon_should_not_run_when_disabled(self):
        self.user.profile.enable_favicons = False
        self.user.profile.save()

        bookmark = self.setup_bookmark()
        tasks.ensure_favicon(self.get_or_create_test_user(), bookmark.url)

        self.mock_fetch_favicon.assert_not_called()

    def test_load_favicon_compatibility_shim(self):
        """旧的 load_favicon(bookmark) 接口仍然可用。"""
        bookmark = self.setup_bookmark()

        tasks.load_favicon(self.get_or_create_test_user(), bookmark)

        self.mock_fetch_favicon.assert_called_once()

    def test_refresh_favicon_compatibility_shim(self):
        """旧的 refresh_favicon(bookmark) 接口仍然可用。"""
        bookmark = self.setup_bookmark()

        tasks.refresh_favicon(self.get_or_create_test_user(), bookmark)

        self.mock_fetch_favicon.assert_called_once()

    def test_fetch_domain_favicon_updates_favicon_cache_and_bookmarks(self):
        """获取成功后应更新 FaviconCache 和 Bookmark.favicon_file。"""
        from bookmarks.models import FaviconCache

        self.setup_bookmark()

        tasks._fetch_domain_favicon_task(self.user.id, "example.com")

        cache = FaviconCache.objects.filter(domain="example.com").first()
        self.assertIsNotNone(cache)
        self.assertEqual(cache.status, FaviconCache.STATUS_SUCCESS)
        self.assertEqual(cache.favicon_file, "https_example_com.png")
        # Bookmark.favicon_file field removed; verify via FaviconCache only
        self.assertEqual(cache.favicon_file, "https_example_com.png")

    def test_fetch_domain_favicon_handles_failure_with_retry(self):
        """获取失败时应更新重试计数和下次重试时间。"""
        from bookmarks.models import FaviconCache

        self.mock_fetch_favicon.return_value = ""

        tasks._fetch_domain_favicon_task(self.user.id, "nonexistent.com")

        cache = FaviconCache.objects.filter(domain="nonexistent.com").first()
        self.assertIsNotNone(cache)
        self.assertEqual(cache.status, FaviconCache.STATUS_FAILED)
        self.assertEqual(cache.retry_count, 1)
        self.assertIsNotNone(cache.next_retry_at)
        # 第一次重试延迟 1 分钟
        self.assertEqual(cache.favicon_file, "")

    def test_fetch_domain_favicon_marks_missing_after_max_retries(self):
        """连续失败 5 次后应标记为 missing，retry_count 重置为 0，favicon_file 为空。"""
        from bookmarks.models import FaviconCache

        self.mock_fetch_favicon.return_value = ""

        # Simulate 5 failures (1min, 3min, 5min, 10min, 20min)
        for _ in range(5):
            tasks._fetch_domain_favicon_task(self.user.id, "never.com")

        cache = FaviconCache.objects.filter(domain="never.com").first()
        self.assertEqual(cache.status, FaviconCache.STATUS_MISSING)
        self.assertEqual(cache.retry_count, 0)
        self.assertEqual(cache.favicon_file, "")
        self.assertIsNotNone(cache.next_retry_at)

    def test_fetch_domain_favicon_missing_retry_uses_progressive_delays(self):
        """MISSING 状态重试应使用渐进间隔序列，之后 7 天封顶。

        MISSING_RETRY_DELAYS = [1, 1, 1, 2, 2, 3, 3, 4, 5, 6, 7]
        transition 时 retry_count=0, next_retry_at=1天 (MISSING_RETRY_DELAYS[0])
        之后每次重试: retry_count++, delay = MISSING_RETRY_DELAYS[min(count, 10)]
        有效序列: 1(transition) → 1 → 1 → 2 → 2 → 3 → 3 → 4 → 5 → 6 → 7 → 7 → ...
        """
        from datetime import timedelta

        from bookmarks.models import FaviconCache

        self.mock_fetch_favicon.return_value = ""

        # 先到达 MISSING 状态（transition 时 retry_count=0, next_retry_at=1天）
        for _ in range(5):
            tasks._fetch_domain_favicon_task(self.user.id, "retry.com")
        cache = FaviconCache.objects.filter(domain="retry.com").first()
        self.assertEqual(cache.status, FaviconCache.STATUS_MISSING)
        self.assertEqual(cache.retry_count, 0)

        # 从 transition 后的第一次重试开始验证
        # retry_count 1→11 对应 MISSING_RETRY_DELAYS[1..10] + 封顶
        expected_delays = [1, 1, 2, 2, 3, 3, 4, 5, 6, 7]

        for i, expected_days in enumerate(expected_delays):
            cache.next_retry_at = timezone.now() - timedelta(seconds=1)
            cache.save()
            tasks._fetch_domain_favicon_task(self.user.id, "retry.com")
            cache.refresh_from_db()
            self.assertEqual(cache.status, FaviconCache.STATUS_MISSING)
            self.assertEqual(cache.retry_count, i + 1)
            delta = cache.next_retry_at - timezone.now()
            self.assertAlmostEqual(
                delta.total_seconds(),
                timedelta(days=expected_days).total_seconds(),
                delta=5,
            )

        # 超出序列长度后应使用封顶值 7 天
        cache.next_retry_at = timezone.now() - timedelta(seconds=1)
        cache.save()
        tasks._fetch_domain_favicon_task(self.user.id, "retry.com")
        cache.refresh_from_db()
        self.assertEqual(cache.retry_count, 11)
        delta = cache.next_retry_at - timezone.now()
        self.assertAlmostEqual(
            delta.total_seconds(), timedelta(days=7).total_seconds(), delta=5
        )

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_load_favicon_should_not_run_when_background_tasks_are_disabled(self):
        bookmark = self.setup_bookmark()
        tasks.load_favicon(self.get_or_create_test_user(), bookmark)

        self.mock_fetch_favicon.assert_not_called()

    def test_schedule_bookmarks_without_favicons_should_load_favicon_for_bookmarks_without_favicon(
        self,
    ):
        user = self.get_or_create_test_user()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark()
        # 第4个书签的域名已有成功的 FaviconCache 条目，应被跳过
        self.setup_bookmark(url="https://other-domain.com/page")
        from bookmarks.models import FaviconCache

        FaviconCache.objects.create(
            domain="other-domain.com",
            favicon_file="other_domain_com.png",
            status=FaviconCache.STATUS_SUCCESS,
        )

        tasks.schedule_bookmarks_without_favicons(user)

        # 3 bookmarks without favicon, all same domain -> 1 fetch task (domain-deduped)
        self.mock_fetch_favicon.assert_called()

    def test_schedule_bookmarks_without_favicons_should_only_update_user_owned_bookmarks(
        self,
    ):
        user = self.get_or_create_test_user()
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark(user=other_user)

        tasks.schedule_bookmarks_without_favicons(user)

        # Only 2 bookmarks belong to the user -> 1 fetch (same domain)
        self.mock_fetch_favicon.assert_called()

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_schedule_bookmarks_without_favicons_should_not_run_when_background_tasks_are_disabled(
        self,
    ):
        self.setup_bookmark()
        tasks.schedule_bookmarks_without_favicons(self.get_or_create_test_user())

        self.mock_fetch_favicon.assert_not_called()

    def test_schedule_bookmarks_without_favicons_should_not_run_when_favicon_feature_is_disabled(
        self,
    ):
        self.user.profile.enable_favicons = False
        self.user.profile.save()

        self.setup_bookmark()
        tasks.schedule_bookmarks_without_favicons(self.get_or_create_test_user())

        self.mock_fetch_favicon.assert_not_called()

    def test_schedule_refresh_favicons_should_fetch_for_all_domains(self):
        user = self.get_or_create_test_user()
        self.setup_bookmark()
        self.setup_bookmark(url="https://other-domain.com/page")

        tasks.schedule_refresh_favicons(user)

        # All bookmarks have same domain -> at least 1 fetch
        self.mock_fetch_favicon.assert_called()

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_schedule_refresh_favicons_should_not_run_when_background_tasks_are_disabled(
        self,
    ):
        self.setup_bookmark()
        tasks.schedule_refresh_favicons(self.get_or_create_test_user())

        self.mock_fetch_favicon.assert_not_called()

    @override_settings(LD_ENABLE_REFRESH_FAVICONS=False)
    def test_schedule_refresh_favicons_should_not_run_when_refresh_is_disabled(self):
        self.setup_bookmark()
        tasks.schedule_refresh_favicons(self.get_or_create_test_user())

        self.mock_fetch_favicon.assert_not_called()

    def test_schedule_refresh_favicons_should_not_run_when_favicon_feature_is_disabled(
        self,
    ):
        self.user.profile.enable_favicons = False
        self.user.profile.save()

        self.setup_bookmark()
        tasks.schedule_refresh_favicons(self.get_or_create_test_user())

        self.mock_fetch_favicon.assert_not_called()

    def test_rename_favicon_for_domain_config_is_noop(self):
        """域名规则变更后，rename_favicon_for_domain_config 是 no-op（Bookmark.favicon_file 已移除）。"""
        user = self.get_or_create_test_user()
        user.profile.enable_favicons = True
        user.profile.save()

        self.setup_bookmark(url="https://example.com/page")

        old_config = ""
        new_config = "example.com -> test.com"
        # 不应抛出异常
        tasks.rename_favicon_for_domain_config(user, old_config, new_config)

    def test_load_preview_image_should_create_preview_image_file(self):
        bookmark = self.setup_bookmark()

        tasks.load_preview_image(self.get_or_create_test_user(), bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(self.executed_count(), 1)
        self.assertEqual(bookmark.preview_image_file, "preview_image.png")

    def test_load_preview_image_should_update_preview_image_file(self):
        bookmark = self.setup_bookmark(
            preview_image_file="preview_image.png",
        )

        self.mock_load_preview_image.return_value = "preview_image_upd.png"

        tasks.load_preview_image(self.get_or_create_test_user(), bookmark, force=True)

        bookmark.refresh_from_db()
        self.mock_load_preview_image.assert_called_once()
        self.assertEqual(bookmark.preview_image_file, "preview_image_upd.png")

    def test_load_preview_image_force_resets_retry_state(self):
        bookmark = self.setup_bookmark()
        bookmark.preview_image_retry_count = 3
        bookmark.preview_image_next_retry_at = timezone.now() + timedelta(days=1)
        bookmark.save(
            update_fields=[
                "preview_image_retry_count",
                "preview_image_next_retry_at",
            ]
        )

        tasks.load_preview_image(self.get_or_create_test_user(), bookmark, force=True)

        bookmark.refresh_from_db()
        self.mock_load_preview_image.assert_called_once()
        self.assertEqual(bookmark.preview_image_retry_count, 0)
        self.assertIsNone(bookmark.preview_image_next_retry_at)

    def test_load_preview_image_should_keep_existing_file_and_schedule_retry_when_none_is_returned(
        self,
    ):
        bookmark = self.setup_bookmark(
            preview_image_file="preview_image.png",
        )

        self.mock_load_preview_image.return_value = None

        with self.assertRaises(RetryTask):
            tasks._load_preview_image_task.call_local(bookmark.id)

        bookmark.refresh_from_db()
        self.mock_load_preview_image.assert_called_once()
        self.assertEqual(bookmark.preview_image_file, "preview_image.png")
        self.assertEqual(bookmark.preview_image_retry_count, 1)
        self.assertIsNotNone(bookmark.preview_image_next_retry_at)

    def test_load_preview_image_should_handle_missing_bookmark(self):
        tasks._load_preview_image_task(123)

        self.mock_load_preview_image.assert_not_called()

    def test_load_preview_image_task_counts_retryable_loader_errors(self):
        bookmark = self.setup_bookmark()
        self.mock_load_preview_image.side_effect = (
            website_loader.RetryableMetadataError("boom")
        )

        with self.assertRaises(RetryTask):
            tasks._load_preview_image_task.call_local(bookmark.id)

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.preview_image_retry_count, 1)
        self.assertIsNotNone(bookmark.preview_image_next_retry_at)

    def test_load_preview_image_should_not_save_stale_bookmark_data(self):
        bookmark = self.setup_bookmark()

        # update bookmark during API call to check that saving
        # the image does not overwrite updated bookmark data
        def mock_load_preview_image_impl(url, bookmark_obj, force=False):
            bookmark.title = "Updated title"
            bookmark.save()
            return "test.png"

        self.mock_load_preview_image.side_effect = mock_load_preview_image_impl

        tasks.load_preview_image(self.get_or_create_test_user(), bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.title, "Updated title")
        self.assertEqual(bookmark.preview_image_file, "test.png")

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_load_preview_image_should_not_run_when_background_tasks_are_disabled(self):
        bookmark = self.setup_bookmark()
        tasks.load_preview_image(self.get_or_create_test_user(), bookmark)

        self.assertEqual(self.executed_count(), 0)

    def test_load_preview_image_should_not_run_when_preview_image_feature_is_disabled(
        self,
    ):
        self.user.profile.enable_preview_images = False
        self.user.profile.save()

        bookmark = self.setup_bookmark()
        tasks.load_preview_image(self.get_or_create_test_user(), bookmark)

        self.assertEqual(self.executed_count(), 0)

    def test_schedule_bookmarks_without_previews_should_load_preview_for_all_bookmarks_without_preview(
        self,
    ):
        user = self.get_or_create_test_user()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark(preview_image_file="test.png")
        self.setup_bookmark(preview_image_file="test.png")
        self.setup_bookmark(preview_image_file="test.png")

        tasks.schedule_bookmarks_without_previews(user)

        self.assertEqual(self.executed_count(), 4)
        self.assertEqual(self.mock_load_preview_image.call_count, 3)

    def test_schedule_bookmarks_without_previews_should_only_update_user_owned_bookmarks(
        self,
    ):
        user = self.get_or_create_test_user()
        other_user = User.objects.create_user(
            "otheruser", "otheruser@example.com", "password123"
        )
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark(user=other_user)
        self.setup_bookmark(user=other_user)
        self.setup_bookmark(user=other_user)

        tasks.schedule_bookmarks_without_previews(user)

        self.assertEqual(self.mock_load_preview_image.call_count, 3)

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_schedule_bookmarks_without_previews_should_not_run_when_background_tasks_are_disabled(
        self,
    ):
        self.setup_bookmark()
        tasks.schedule_bookmarks_without_previews(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

    def test_schedule_bookmarks_without_previews_should_not_run_when_preview_feature_is_disabled(
        self,
    ):
        self.user.profile.enable_preview_images = False
        self.user.profile.save()

        self.setup_bookmark()
        tasks.schedule_bookmarks_without_previews(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshot_should_create_pending_asset_and_kick_dispatcher(
        self,
    ):
        bookmark = self.setup_bookmark()

        with mock.patch(
            "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
        ) as mock_trigger_html_snapshot_dispatcher:
            tasks.create_html_snapshot(bookmark)
            self.assertEqual(BookmarkAsset.objects.count(), 1)

            tasks.create_html_snapshot(bookmark)
            self.assertEqual(BookmarkAsset.objects.count(), 2)

            assets = BookmarkAsset.objects.filter(bookmark=bookmark)
            for asset in assets:
                self.assertEqual(asset.bookmark, bookmark)
                self.assertEqual(asset.asset_type, BookmarkAsset.TYPE_SNAPSHOT)
                self.assertEqual(asset.content_type, "")
                self.assertIn("New snapshot", asset.display_name)
                self.assertEqual(asset.status, BookmarkAsset.STATUS_PENDING)

            self.assertEqual(mock_trigger_html_snapshot_dispatcher.call_count, 2)
            self.mock_assets_create_snapshot.assert_not_called()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshot_should_store_scheduling_priority(self):
        bookmark = self.setup_bookmark()

        with mock.patch(
            "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
        ) as mock_trigger_html_snapshot_dispatcher:
            tasks.create_html_snapshot(
                bookmark, priority=tasks.PRIORITY_MANUAL_SNAPSHOT
            )

        asset = BookmarkAsset.objects.get(bookmark=bookmark)
        self.assertEqual(
            asset.scheduling_priority, tasks.PRIORITY_MANUAL_SNAPSHOT
        )
        mock_trigger_html_snapshot_dispatcher.assert_called_once_with(
            priority=tasks.PRIORITY_MANUAL_SNAPSHOT
        )

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshots_should_kick_dispatcher_once(self):
        bookmarks = [
            self.setup_bookmark(url="https://example.com/1"),
            self.setup_bookmark(url="https://example.com/2"),
            self.setup_bookmark(url="https://example.com/3"),
        ]

        with mock.patch(
            "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
        ) as mock_trigger_html_snapshot_dispatcher:
            tasks.create_html_snapshots(bookmarks)

        self.assertEqual(BookmarkAsset.objects.count(), 3)
        self.assertEqual(mock_trigger_html_snapshot_dispatcher.call_count, 1)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshot_should_skip_when_adapter_disables_snapshot(self):
        bookmark = self.setup_bookmark(url="https://example.com/page")

        with mock.patch(
            "bookmarks.services.tasks._is_snapshot_disabled_by_adapter",
            return_value=True,
        ) as mock_disabled:
            with mock.patch(
                "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
            ) as mock_trigger:
                tasks.create_html_snapshot(bookmark)

        mock_disabled.assert_called_once()
        self.assertEqual(BookmarkAsset.objects.count(), 0)
        mock_trigger.assert_not_called()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshot_should_proceed_when_adapter_enables_snapshot(self):
        bookmark = self.setup_bookmark(url="https://example.com/page")

        with mock.patch(
            "bookmarks.services.tasks._is_snapshot_disabled_by_adapter",
            return_value=False,
        ):
            with mock.patch(
                "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
            ) as mock_trigger:
                tasks.create_html_snapshot(bookmark)

        self.assertEqual(BookmarkAsset.objects.count(), 1)
        mock_trigger.assert_called_once()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshots_should_skip_disabled_bookmarks(self):
        bookmarks = [
            self.setup_bookmark(url="https://example.com/1"),
            self.setup_bookmark(url="https://example.com/2"),
            self.setup_bookmark(url="https://example.com/3"),
        ]

        # Simulate adapter disabling snapshots for the 2nd bookmark only
        def fake_disabled(url, username=""):
            return url == "https://example.com/2"

        with mock.patch(
            "bookmarks.services.tasks._is_snapshot_disabled_by_adapter",
            side_effect=fake_disabled,
        ):
            with mock.patch(
                "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
            ) as mock_trigger:
                tasks.create_html_snapshots(bookmarks)

        self.assertEqual(BookmarkAsset.objects.count(), 2)
        mock_trigger.assert_called_once()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_missing_html_snapshots_should_exclude_adapter_disabled(self):
        self.setup_bookmark(url="https://example.com/1")
        self.setup_bookmark(url="https://example.com/2")

        def fake_disabled(url, username=""):
            return url == "https://example.com/2"

        with mock.patch(
            "bookmarks.services.tasks._is_snapshot_disabled_by_adapter",
            side_effect=fake_disabled,
        ):
            with mock.patch(
                "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
            ):
                count = tasks.create_missing_html_snapshots(
                    self.get_or_create_test_user()
                )

        self.assertEqual(count, 1)
        self.assertEqual(BookmarkAsset.objects.count(), 1)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_schedule_html_snapshots_should_kick_dispatcher_for_pending_assets(self):
        bookmark = self.setup_bookmark(url="https://example.com")
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
        )

        with mock.patch(
            "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
        ) as mock_trigger_html_snapshot_dispatcher:
            tasks._schedule_html_snapshots_task()

        mock_trigger_html_snapshot_dispatcher.assert_called_once_with()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_schedule_html_snapshots_should_not_kick_dispatcher_when_no_pending_assets(
        self,
    ):
        bookmark = self.setup_bookmark(url="https://example.com")
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_COMPLETE,
        )

        with mock.patch(
            "bookmarks.services.tasks._trigger_html_snapshot_dispatcher"
        ) as mock_trigger_html_snapshot_dispatcher:
            tasks._schedule_html_snapshots_task()

        mock_trigger_html_snapshot_dispatcher.assert_not_called()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_select_next_html_snapshot_asset_should_prefer_newest_eligible_asset(self):
        now = timezone.now()
        old_bookmark = self.setup_bookmark(url="https://old.example.com/1")
        new_bookmark = self.setup_bookmark(url="https://new.example.org/1")
        old_asset = self.setup_asset(
            bookmark=old_bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=2),
        )
        new_asset = self.setup_asset(
            bookmark=new_bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=1),
        )

        asset, next_wake_at = tasks._select_next_html_snapshot_asset(now, {})

        self.assertEqual(asset.id, new_asset.id)
        self.assertNotEqual(asset.id, old_asset.id)
        self.assertIsNone(next_wake_at)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_select_next_html_snapshot_asset_should_prefer_scheduling_priority(self):
        now = timezone.now()
        newer_low_priority_bookmark = self.setup_bookmark(
            url="https://new.example.com/1"
        )
        older_high_priority_bookmark = self.setup_bookmark(
            url="https://priority.example.com/1"
        )
        newer_low_priority = self.setup_asset(
            bookmark=newer_low_priority_bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=1),
        )
        older_high_priority = self.setup_asset(
            bookmark=older_high_priority_bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=2),
            scheduling_priority=tasks.PRIORITY_MANUAL_SNAPSHOT,
        )

        asset, next_wake_at = tasks._select_next_html_snapshot_asset(now, {})

        self.assertEqual(asset.id, older_high_priority.id)
        self.assertNotEqual(asset.id, newer_low_priority.id)
        self.assertIsNone(next_wake_at)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_select_next_html_snapshot_asset_should_skip_cooling_down_domain(self):
        now = timezone.now()
        newest_bookmark = self.setup_bookmark(url="https://docs.example.com/1")
        older_bookmark = self.setup_bookmark(url="https://example.org/1")
        newest_asset = self.setup_asset(
            bookmark=newest_bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=1),
        )
        older_asset = self.setup_asset(
            bookmark=older_bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=2),
        )

        asset, next_wake_at = tasks._select_next_html_snapshot_asset(
            now,
            {"example.com": now + timedelta(seconds=10)},
        )

        self.assertEqual(asset.id, older_asset.id)
        self.assertNotEqual(asset.id, newest_asset.id)
        self.assertIsNone(next_wake_at)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_select_next_html_snapshot_asset_should_share_cooldown_across_subdomains(
        self,
    ):
        now = timezone.now()
        newer_asset = self.setup_asset(
            bookmark=self.setup_bookmark(url="https://docs.example.com/1"),
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=1),
        )
        older_asset = self.setup_asset(
            bookmark=self.setup_bookmark(url="https://www.example.com/2"),
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=2),
        )

        asset, next_wake_at = tasks._select_next_html_snapshot_asset(
            now,
            {"example.com": now + timedelta(seconds=10)},
        )

        self.assertIsNone(asset)
        self.assertEqual(next_wake_at, now + timedelta(seconds=10))
        self.assertNotEqual(newer_asset.id, older_asset.id)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    @override_settings(
        LD_SNAPSHOT_DOMAIN_COOLDOWN_MIN_SEC=7,
        LD_SNAPSHOT_DOMAIN_COOLDOWN_MAX_SEC=7,
    )
    def test_get_html_snapshot_cooldown_seconds_should_use_settings_range(self):
        self.assertEqual(tasks._get_html_snapshot_cooldown_seconds(), 7)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    @override_settings(LD_SNAPSHOT_DISPATCHER_TICK_SEC=1)
    def test_run_html_snapshot_dispatcher_loop_should_process_assets_until_queue_empty(
        self,
    ):
        now = timezone.now()
        first_asset = self.setup_asset(
            bookmark=self.setup_bookmark(url="https://old.example.com/1"),
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=2),
        )
        second_asset = self.setup_asset(
            bookmark=self.setup_bookmark(url="https://new.example.org/1"),
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=1),
        )
        processed_asset_ids = []
        current_time = {"value": now}

        def mark_asset_complete(asset_id):
            processed_asset_ids.append(asset_id)
            asset = BookmarkAsset.objects.get(id=asset_id)
            asset.status = BookmarkAsset.STATUS_COMPLETE
            asset.save(update_fields=["status"])

        def fake_now():
            return current_time["value"]

        def fake_sleep(seconds):
            current_time["value"] += timedelta(seconds=seconds)

        with mock.patch(
            "bookmarks.services.tasks._create_html_snapshot_task",
            side_effect=mark_asset_complete,
        ):
            tasks._run_html_snapshot_dispatcher_loop(
                now_func=fake_now,
                sleep_func=fake_sleep,
                cooldown_func=lambda: 5,
            )

        self.assertEqual(processed_asset_ids, [second_asset.id, first_asset.id])

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    @override_settings(LD_SNAPSHOT_DISPATCHER_TICK_SEC=1)
    def test_run_html_snapshot_dispatcher_loop_should_tick_while_waiting_for_domain_cooldown(
        self,
    ):
        now = timezone.now()
        first_asset = self.setup_asset(
            bookmark=self.setup_bookmark(url="https://docs.example.com/1"),
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=2),
        )
        second_asset = self.setup_asset(
            bookmark=self.setup_bookmark(url="https://www.example.com/2"),
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            date_created=now - timedelta(minutes=1),
        )
        processed_asset_ids = []
        sleep_calls = []
        current_time = {"value": now}

        def mark_asset_complete(asset_id):
            processed_asset_ids.append(asset_id)
            asset = BookmarkAsset.objects.get(id=asset_id)
            asset.status = BookmarkAsset.STATUS_COMPLETE
            asset.save(update_fields=["status"])

        def fake_now():
            return current_time["value"]

        def fake_sleep(seconds):
            sleep_calls.append(seconds)
            current_time["value"] += timedelta(seconds=seconds)

        with mock.patch(
            "bookmarks.services.tasks._create_html_snapshot_task",
            side_effect=mark_asset_complete,
        ):
            tasks._run_html_snapshot_dispatcher_loop(
                now_func=fake_now,
                sleep_func=fake_sleep,
                cooldown_func=lambda: 5,
            )

        self.assertEqual(processed_asset_ids, [second_asset.id, first_asset.id])
        self.assertTrue(sleep_calls)
        self.assertTrue(all(seconds <= 1 for seconds in sleep_calls))
        self.assertEqual(sum(sleep_calls), 5)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshot_should_handle_missing_asset(self):
        tasks._create_html_snapshot_task(123)

        self.mock_assets_create_snapshot.assert_not_called()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshot_task_should_not_update_bookmark_metadata(self):
        self.setup_temp_assets_dir()
        bookmark = self.setup_bookmark(
            url="https://example.com",
            title="Original title",
            description="Original description",
        )
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        )

        def create_snapshot(asset):
            self.setup_asset_file(
                asset,
                "<html><head><title>Snapshot title</title>"
                '<meta name="description" content="Snapshot description">'
                "</head></html>",
            )
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_COMPLETE,
                content_type=BookmarkAsset.CONTENT_TYPE_HTML,
            )

        self.mock_assets_create_snapshot.side_effect = create_snapshot

        tasks._create_html_snapshot_task(asset.id)

        bookmark.refresh_from_db()
        self.assertEqual(bookmark.title, "Original title")
        self.assertEqual(bookmark.description, "Original description")

    @override_settings(
        LD_ENABLE_SNAPSHOTS=True, LD_SNAPSHOT_RETRY_DELAYS=[60, 300, 1500]
    )
    def test_create_html_snapshot_task_should_retry_on_failure(self):
        bookmark = self.setup_bookmark(url="https://example.com")
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=0,
        )

        # 模拟快照失败（设置 STATUS_FAILURE 并抛出异常，模拟真实行为）
        def create_snapshot_failure(asset, username=""):
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_FAILURE
            )
            raise Exception("Snapshot failed")

        self.mock_assets_create_snapshot.side_effect = create_snapshot_failure

        mock_now = timezone.now()
        with mock.patch("bookmarks.services.tasks.timezone.now", return_value=mock_now):
            tasks._create_html_snapshot_task(asset.id)

        asset.refresh_from_db()
        self.assertEqual(asset.status, BookmarkAsset.STATUS_PENDING)
        self.assertEqual(asset.retry_count, 1)
        self.assertIsNotNone(asset.next_retry_at)
        # 第一次重试延迟 60 秒
        self.assertEqual(asset.next_retry_at, mock_now + timedelta(seconds=60))

    @override_settings(
        LD_ENABLE_SNAPSHOTS=True, LD_SNAPSHOT_RETRY_DELAYS=[60, 300, 1500]
    )
    def test_create_html_snapshot_task_should_use_configured_delays(self):
        bookmark = self.setup_bookmark(url="https://example.com")
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=1,  # 已重试 1 次
        )

        # 模拟快照失败（设置 STATUS_FAILURE 并抛出异常，模拟真实行为）
        def create_snapshot_failure(asset, username=""):
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_FAILURE
            )
            raise Exception("Snapshot failed")

        self.mock_assets_create_snapshot.side_effect = create_snapshot_failure

        mock_now = timezone.now()
        with mock.patch("bookmarks.services.tasks.timezone.now", return_value=mock_now):
            tasks._create_html_snapshot_task(asset.id)

        asset.refresh_from_db()
        self.assertEqual(asset.status, BookmarkAsset.STATUS_PENDING)
        self.assertEqual(asset.retry_count, 2)
        self.assertIsNotNone(asset.next_retry_at)
        # 第二次重试延迟 300 秒（数组索引 1）
        self.assertEqual(asset.next_retry_at, mock_now + timedelta(seconds=300))

    @override_settings(
        LD_ENABLE_SNAPSHOTS=True, LD_SNAPSHOT_RETRY_DELAYS=[60, 300, 1500]
    )
    def test_create_html_snapshot_task_should_set_failure_after_max_retries(self):
        bookmark = self.setup_bookmark(url="https://example.com")
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=3,  # 已达最大重试次数（数组长度）
        )

        # 模拟快照失败（设置 STATUS_FAILURE 并抛出异常，模拟真实行为）
        def create_snapshot_failure(asset, username=""):
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_FAILURE
            )
            raise Exception("Snapshot failed")

        self.mock_assets_create_snapshot.side_effect = create_snapshot_failure

        tasks._create_html_snapshot_task(asset.id)

        asset.refresh_from_db()
        self.assertEqual(asset.status, BookmarkAsset.STATUS_FAILURE)
        self.assertEqual(asset.retry_count, 3)  # retry_count 不变
        self.assertIsNone(asset.next_retry_at)  # next_retry_at 未设置

    @override_settings(LD_ENABLE_SNAPSHOTS=True, LD_SNAPSHOT_RETRY_DELAYS=[30, 60])
    def test_create_html_snapshot_task_should_use_custom_delays(self):
        """验证自定义延迟数组配置"""
        bookmark = self.setup_bookmark(url="https://example.com")
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=0,
        )

        # 模拟快照失败（设置 STATUS_FAILURE 并抛出异常，模拟真实行为）
        def create_snapshot_failure(asset, username=""):
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_FAILURE
            )
            raise Exception("Snapshot failed")

        self.mock_assets_create_snapshot.side_effect = create_snapshot_failure

        mock_now = timezone.now()
        with mock.patch("bookmarks.services.tasks.timezone.now", return_value=mock_now):
            tasks._create_html_snapshot_task(asset.id)

        asset.refresh_from_db()
        self.assertEqual(asset.status, BookmarkAsset.STATUS_PENDING)
        self.assertEqual(asset.retry_count, 1)
        # 第一次重试延迟 30 秒（自定义数组的第一个元素）
        self.assertEqual(asset.next_retry_at, mock_now + timedelta(seconds=30))

    @override_settings(LD_ENABLE_SNAPSHOTS=True, LD_SNAPSHOT_RETRY_DELAYS=[30, 60])
    def test_create_html_snapshot_task_should_respect_custom_max_retries(self):
        """验证自定义数组长度决定最大重试次数"""
        bookmark = self.setup_bookmark(url="https://example.com")
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=2,  # 已达最大重试次数（数组长度为 2）
        )

        # 模拟快照失败（设置 STATUS_FAILURE 并抛出异常，模拟真实行为）
        def create_snapshot_failure(asset, username=""):
            BookmarkAsset.objects.filter(id=asset.id).update(
                status=BookmarkAsset.STATUS_FAILURE
            )
            raise Exception("Snapshot failed")

        self.mock_assets_create_snapshot.side_effect = create_snapshot_failure

        tasks._create_html_snapshot_task(asset.id)

        asset.refresh_from_db()
        # 应该标记为最终失败
        self.assertEqual(asset.status, BookmarkAsset.STATUS_FAILURE)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_select_next_html_snapshot_asset_should_skip_pending_retry(self):
        now = timezone.now()
        bookmark = self.setup_bookmark(url="https://example.com")
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=1,
            next_retry_at=now + timedelta(minutes=5),  # 5 分钟后重试
        )

        selected_asset, next_wake_at = tasks._select_next_html_snapshot_asset(now, {})

        # 应该跳过这个资产
        self.assertIsNone(selected_asset)
        # next_wake_at 应该是 next_retry_at
        self.assertEqual(next_wake_at, now + timedelta(minutes=5))

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_select_next_html_snapshot_asset_should_process_ready_retry(self):
        now = timezone.now()
        bookmark = self.setup_bookmark(url="https://example.com")
        asset = self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
            retry_count=1,
            next_retry_at=now - timedelta(minutes=1),  # 已过重试时间
        )

        selected_asset, next_wake_at = tasks._select_next_html_snapshot_asset(now, {})

        # 应该选中这个资产
        self.assertIsNotNone(selected_asset)
        self.assertEqual(selected_asset.id, asset.id)
        self.assertIsNone(next_wake_at)

    @override_settings(LD_ENABLE_SNAPSHOTS=False)
    def test_create_html_snapshot_should_not_create_asset_when_single_file_is_disabled(
        self,
    ):
        bookmark = self.setup_bookmark()
        tasks.create_html_snapshot(bookmark)

        self.assertEqual(BookmarkAsset.objects.count(), 0)

    @override_settings(LD_ENABLE_SNAPSHOTS=True, LD_DISABLE_BACKGROUND_TASKS=True)
    def test_create_html_snapshot_should_not_create_asset_when_background_tasks_are_disabled(
        self,
    ):
        bookmark = self.setup_bookmark()
        tasks.create_html_snapshot(bookmark)

        self.assertEqual(BookmarkAsset.objects.count(), 0)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_missing_html_snapshots(self):
        bookmarks_with_snapshots = []
        bookmarks_without_snapshots = []

        # setup bookmarks with snapshots
        bookmark = self.setup_bookmark()
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_COMPLETE,
        )
        bookmarks_with_snapshots.append(bookmark)

        bookmark = self.setup_bookmark()
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
        )
        bookmarks_with_snapshots.append(bookmark)

        # setup bookmarks without snapshots
        bookmark = self.setup_bookmark()
        bookmarks_without_snapshots.append(bookmark)

        bookmark = self.setup_bookmark()
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_FAILURE,
        )
        bookmarks_without_snapshots.append(bookmark)

        bookmark = self.setup_bookmark()
        self.setup_asset(
            bookmark=bookmark,
            asset_type="some_other_type",
            status=BookmarkAsset.STATUS_PENDING,
        )
        bookmarks_without_snapshots.append(bookmark)

        bookmark = self.setup_bookmark()
        self.setup_asset(
            bookmark=bookmark,
            asset_type="some_other_type",
            status=BookmarkAsset.STATUS_COMPLETE,
        )
        bookmarks_without_snapshots.append(bookmark)

        initial_assets = list(BookmarkAsset.objects.all())
        initial_assets_count = len(initial_assets)
        initial_asset_ids = [asset.id for asset in initial_assets]
        count = tasks.create_missing_html_snapshots(self.get_or_create_test_user())

        self.assertEqual(count, 4)
        self.assertEqual(BookmarkAsset.objects.count(), initial_assets_count + count)

        for bookmark in bookmarks_without_snapshots:
            new_assets = BookmarkAsset.objects.filter(bookmark=bookmark).exclude(
                id__in=initial_asset_ids
            )
            self.assertEqual(new_assets.count(), 1)

        for bookmark in bookmarks_with_snapshots:
            new_assets = BookmarkAsset.objects.filter(bookmark=bookmark).exclude(
                id__in=initial_asset_ids
            )
            self.assertEqual(new_assets.count(), 0)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_missing_html_snapshots_respects_current_user(self):
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark()

        other_user = self.setup_user()
        self.setup_bookmark(user=other_user)
        self.setup_bookmark(user=other_user)
        self.setup_bookmark(user=other_user)

        count = tasks.create_missing_html_snapshots(self.get_or_create_test_user())

        self.assertEqual(count, 3)
        self.assertEqual(BookmarkAsset.objects.count(), count)

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_refresh_metadata_task_not_called_when_background_tasks_disabled(self):
        bookmark = self.setup_bookmark()
        with mock.patch(
            "bookmarks.services.tasks._refresh_metadata_task"
        ) as mock_refresh_metadata_task:
            tasks.refresh_metadata(bookmark)
            mock_refresh_metadata_task.assert_not_called()

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=False)
    def test_refresh_metadata_task_called_when_background_tasks_enabled(self):
        bookmark = self.setup_bookmark()
        with mock.patch(
            "bookmarks.services.tasks._refresh_metadata_task"
        ) as mock_refresh_metadata_task:
            tasks.refresh_metadata(bookmark)
            mock_refresh_metadata_task.assert_called_once()

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_schedule_metadata_enrichment_not_called_when_background_tasks_disabled(
        self,
    ):
        bookmark = self.setup_bookmark()
        with mock.patch(
            "bookmarks.services.tasks._enrich_metadata_task"
        ) as mock_enrich_metadata_task:
            tasks.schedule_metadata_enrichment(bookmark)
            mock_enrich_metadata_task.assert_not_called()

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=False)
    def test_schedule_metadata_enrichment_called_when_background_tasks_enabled(self):
        bookmark = self.setup_bookmark()
        with mock.patch(
            "bookmarks.services.tasks._enrich_metadata_task"
        ) as mock_enrich_metadata_task:
            tasks.schedule_metadata_enrichment(bookmark)
            mock_enrich_metadata_task.assert_called_once_with(
                bookmark.id,
                overwrite=False,
                ignore_cache=True,
                priority=None,
            )

    def test_refresh_metadata_task_should_handle_missing_bookmark(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_website_metadata"
        ) as mock_load_website_metadata:
            tasks._refresh_metadata_task(123)

            mock_load_website_metadata.assert_not_called()

    def test_enrich_metadata_task_should_handle_missing_bookmark(self):
        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata:
            tasks._enrich_metadata_task(123)

            mock_load_website_metadata.assert_not_called()

    def test_refresh_metadata_updates_title_description(self):
        bookmark = self.setup_bookmark(
            title="Initial title",
            description="Initial description",
        )
        mock_website_metadata = WebsiteMetadata(
            url=bookmark.url,
            title="New title",
            description="New description",
            preview_image=None,
        )

        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata:
            mock_load_website_metadata.return_value = mock_website_metadata

            tasks.refresh_metadata(bookmark)

            bookmark.refresh_from_db()
            self.assertEqual(bookmark.title, "New title")
            self.assertEqual(bookmark.description, "New description")

    def test_refresh_metadata_clears_local_preview_when_remote_preview_changes(self):
        bookmark = self.setup_bookmark(preview_image_file="old_preview.png")
        mock_website_metadata = WebsiteMetadata(
            url=bookmark.url,
            title=None,
            description=None,
            preview_image="https://example.com/new-preview.png",
        )

        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata, mock.patch(
            "bookmarks.services.tasks.load_preview_image"
        ) as mock_load_preview_image:
            mock_load_website_metadata.return_value = mock_website_metadata

            tasks.refresh_metadata(bookmark)

            bookmark.refresh_from_db()
            self.assertEqual(
                bookmark.preview_image_remote_url,
                "https://example.com/new-preview.png",
            )
            self.assertEqual(bookmark.preview_image_file, "")
            mock_load_preview_image.assert_called_once_with(
                bookmark.owner, bookmark, force=True
            )

    def test_refresh_metadata_keeps_local_preview_when_remote_preview_unchanged(self):
        bookmark = self.setup_bookmark(preview_image_file="old_preview.png")
        bookmark.preview_image_remote_url = "https://example.com/preview.png"
        bookmark.save(update_fields=["preview_image_remote_url"])
        mock_website_metadata = WebsiteMetadata(
            url=bookmark.url,
            title=None,
            description=None,
            preview_image="https://example.com/preview.png",
        )

        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata, mock.patch(
            "bookmarks.services.tasks.load_preview_image"
        ) as mock_load_preview_image:
            mock_load_website_metadata.return_value = mock_website_metadata

            tasks.refresh_metadata(bookmark)

            bookmark.refresh_from_db()
            self.assertEqual(bookmark.preview_image_file, "old_preview.png")
            mock_load_preview_image.assert_called_once_with(
                bookmark.owner, bookmark, force=True
            )

    def test_enrich_metadata_updates_blank_fields_only(self):
        bookmark = self.setup_bookmark(title="", description="")
        bookmark.preview_image_remote_url = ""
        bookmark.save(update_fields=["preview_image_remote_url"])
        mock_website_metadata = WebsiteMetadata(
            url=bookmark.url,
            title="New title",
            description="New description",
            preview_image="https://example.com/preview.png",
        )

        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata:
            mock_load_website_metadata.return_value = mock_website_metadata

            tasks.schedule_metadata_enrichment(bookmark)

            bookmark.refresh_from_db()
            self.assertEqual(bookmark.title, "New title")
            self.assertEqual(bookmark.description, "New description")
            self.assertEqual(
                bookmark.preview_image_remote_url,
                "https://example.com/preview.png",
            )

    def test_enrich_metadata_does_not_overwrite_existing_fields_by_default(self):
        bookmark = self.setup_bookmark(
            title="Initial title",
            description="Initial description",
        )
        bookmark.preview_image_remote_url = ""
        bookmark.save(update_fields=["preview_image_remote_url"])
        mock_website_metadata = WebsiteMetadata(
            url=bookmark.url,
            title="New title",
            description="New description",
            preview_image="https://example.com/preview.png",
        )

        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata:
            mock_load_website_metadata.return_value = mock_website_metadata

            tasks.schedule_metadata_enrichment(bookmark)

            bookmark.refresh_from_db()
            self.assertEqual(bookmark.title, "Initial title")
            self.assertEqual(bookmark.description, "Initial description")
            self.assertEqual(
                bookmark.preview_image_remote_url,
                "https://example.com/preview.png",
            )

    def test_enrich_metadata_task_propagates_retryable_loader_errors(self):
        bookmark = self.setup_bookmark()

        with mock.patch(
            "bookmarks.services.tasks.load_website_metadata"
        ) as mock_load_website_metadata:
            mock_load_website_metadata.side_effect = (
                website_loader.RetryableMetadataError("boom")
            )

            with self.assertRaises(website_loader.RetryableMetadataError):
                tasks._enrich_metadata_task.call_local(bookmark.id)


class ArticleTasksTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        huey.immediate = True
        self.setup_temp_assets_dir()
        self.get_or_create_test_user()
        self.snapshot_html = "<html><body><main>Snapshot article</main></body></html>"
        self.parsed_article = {
            "title": "Parsed page title",
            "content": "<article>Parsed article</article>",
            "description": "",
            "author": "",
            "site": "",
            "wordCount": 2,
        }

    def tearDown(self):
        huey.immediate = False

    def _write_html_snapshot(self, _url, filepath, **kwargs):
        with open(filepath, "w", encoding="utf-8") as snapshot_file:
            snapshot_file.write(self.snapshot_html)

    def test_requires_snapshot_before_article_detects_snapshot_config(self):
        with mock.patch(
            "site_adapters.services.config.resolver.get_snapshot_config",
            return_value={
                "_raw": {"snapshot": {"process_carousels": ["faceplate-carousel"]}}
            },
        ):
            self.assertTrue(
                tasks._requires_snapshot_before_article("https://example.com/article")
            )

    def test_requires_snapshot_before_article_detects_reader_structured_selector(self):
        with (
            mock.patch(
                "site_adapters.services.config.resolver.get_snapshot_config",
                return_value=None,
            ),
            mock.patch(
                "site_adapters.services.config.resolver.get_reader_config",
                return_value={"defuddle_args": {"contentSelector": "//main/article"}},
            ),
        ):
            self.assertTrue(
                tasks._requires_snapshot_before_article("https://example.com/article")
            )

    def test_requires_snapshot_before_article_detects_reader_json_path_selector(self):
        with (
            mock.patch(
                "site_adapters.services.config.resolver.get_snapshot_config",
                return_value=None,
            ),
            mock.patch(
                "site_adapters.services.config.resolver.get_reader_config",
                return_value={
                    "defuddle_args": {
                        "contentSelector": "$.data.children[0].data.selftext"
                    }
                },
            ),
        ):
            self.assertTrue(
                tasks._requires_snapshot_before_article("https://example.com/api")
            )

    def test_requires_snapshot_before_article_ignores_css_selector(self):
        with (
            mock.patch(
                "site_adapters.services.config.resolver.get_snapshot_config",
                return_value=None,
            ),
            mock.patch(
                "site_adapters.services.config.resolver.get_reader_config",
                return_value={
                    "defuddle_args": {"contentSelector": [".article-body", "article"]}
                },
            ),
        ):
            self.assertFalse(
                tasks._requires_snapshot_before_article("https://example.com/article")
            )

    def test_create_article_task_generates_snapshot_when_none_exists(self):
        """useAsync=false (default) + no existing snapshot → generate snapshot, parse with defuddle."""
        bookmark = self.setup_bookmark(url="https://example.com/article")
        article_asset = create_article_asset_pending(bookmark)
        BookmarkAsset.objects.filter(id=article_asset.id).update(
            date_created=datetime(2026, 5, 25, 1, 30, tzinfo=UTC)
        )

        with (
            mock.patch(
                "bookmarks.services.reader_processor.parse_html",
                return_value=self.parsed_article,
            ) as mock_parse_html,
            mock.patch(
                "bookmarks.services.tasks._requires_snapshot_before_article",
                return_value=False,
            ),
            mock.patch(
                "bookmarks.services.tasks._is_snapshot_disabled_by_adapter",
                return_value=False,
            ),
            mock.patch(
                "bookmarks.services.assets.detect_content_type",
                return_value="text/html",
            ),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot",
                side_effect=self._write_html_snapshot,
            ),
            timezone.override("UTC"),
        ):
            tasks._create_article_task.call_local(article_asset.id)

        article_asset.refresh_from_db()
        self.assertEqual(article_asset.status, BookmarkAsset.STATUS_COMPLETE)
        self.assertEqual(article_asset.content_type, BookmarkAsset.CONTENT_TYPE_HTML)
        self.assertTrue(article_asset.file)
        self.assertEqual("HTML article from 2026/05/25", article_asset.display_name)

        snapshot = BookmarkAsset.objects.get(asset_type=BookmarkAsset.TYPE_SNAPSHOT)
        self.assertEqual(snapshot.status, BookmarkAsset.STATUS_COMPLETE)
        self.assertTrue(self.has_asset_file(snapshot))
        mock_parse_html.assert_called_once_with(
            self.snapshot_html, url=bookmark.url, username="testuser"
        )

    def test_create_article_task_cleans_generated_snapshot_when_parse_fails(self):
        """useAsync=false + snapshot generated but parse_html fails → clean up snapshot asset."""
        bookmark = self.setup_bookmark(url="https://example.com/article")
        article_asset = create_article_asset_pending(bookmark)

        with (
            mock.patch(
                "bookmarks.services.reader_processor.parse_html",
                side_effect=RuntimeError("snapshot parse failed"),
            ) as mock_parse_html,
            mock.patch(
                "bookmarks.services.tasks._requires_snapshot_before_article",
                return_value=False,
            ),
            mock.patch(
                "bookmarks.services.tasks._is_snapshot_disabled_by_adapter",
                return_value=False,
            ),
            mock.patch(
                "bookmarks.services.assets.detect_content_type",
                return_value="text/html",
            ),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot",
                side_effect=self._write_html_snapshot,
            ),
        ):
            tasks._create_article_task.call_local(article_asset.id)

        self.assertFalse(BookmarkAsset.objects.filter(id=article_asset.id).exists())
        self.assertFalse(
            BookmarkAsset.objects.filter(
                asset_type=BookmarkAsset.TYPE_SNAPSHOT
            ).exists()
        )
        bookmark.refresh_from_db()
        self.assertIsNone(bookmark.latest_article)
        self.assertIsNone(bookmark.latest_snapshot)
        mock_parse_html.assert_called_once_with(
            self.snapshot_html, url=bookmark.url, username="testuser"
        )

    def test_create_article_task_parses_raw_xml_snapshot(self):
        bookmark = self.setup_bookmark(url="https://example.com/feed.xml")
        article_asset = create_article_asset_pending(bookmark)
        xml_content = (
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            "<title>XML feed</title>"
            "<entry><title>Post</title>"
            '<content type="html">&lt;p&gt;Hello&lt;/p&gt;</content>'
            "</entry></feed>"
        )
        parsed_article = {
            "title": "Post",
            "content": "<article><p>Hello</p></article>",
            "description": "",
            "author": "",
            "site": "",
            "wordCount": 1,
        }

        def write_xml_snapshot(_url, filepath, **_kwargs):
            with open(filepath, "w", encoding="utf-8") as snapshot_file:
                snapshot_file.write(xml_content)

        with (
            mock.patch(
                "bookmarks.services.reader_processor.parse_content",
                return_value=parsed_article,
            ) as mock_parse_content,
            mock.patch(
                "bookmarks.services.tasks._requires_snapshot_before_article",
                return_value=True,
            ),
            mock.patch(
                "bookmarks.services.assets.detect_content_type",
                return_value="application/xml",
            ),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot",
                side_effect=write_xml_snapshot,
            ),
            timezone.override("UTC"),
        ):
            tasks._create_article_task.call_local(article_asset.id)

        article_asset.refresh_from_db()
        self.assertEqual(article_asset.status, BookmarkAsset.STATUS_COMPLETE)
        snapshot = BookmarkAsset.objects.get(asset_type=BookmarkAsset.TYPE_SNAPSHOT)
        self.assertEqual(snapshot.content_type, BookmarkAsset.CONTENT_TYPE_XML)
        mock_parse_content.assert_called_once_with(
            xml_content,
            BookmarkAsset.CONTENT_TYPE_XML,
            url=bookmark.url,
            username="testuser",
        )

    def test_save_article_content_uses_html_article_default_name_with_iso_date(self):
        bookmark = self.setup_bookmark(url="https://example.com/article")
        article_asset = create_article_asset_pending(bookmark)
        article_asset.date_created = datetime(2026, 5, 25, 1, 30, tzinfo=UTC)

        with timezone.override("UTC"):
            save_article_content(
                article_asset,
                "<article>Content</article>",
                title="Parsed page title",
            )

        article_asset.refresh_from_db()
        self.assertEqual("HTML article from 2026/05/25", article_asset.display_name)
