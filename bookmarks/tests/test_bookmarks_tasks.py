from datetime import timedelta
from unittest import mock

import waybackpy
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from huey.contrib.djhuey import HUEY as huey
from waybackpy.exceptions import WaybackError

from bookmarks.models import BookmarkAsset, UserProfile
from bookmarks.services import tasks
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

        self.mock_load_favicon_patcher = mock.patch(
            "bookmarks.services.favicon_loader.load_favicon"
        )
        self.mock_load_favicon = self.mock_load_favicon_patcher.start()
        self.mock_load_favicon.return_value = "https_example_com.png"

        self.mock_assets_create_snapshot_patcher = mock.patch(
            "bookmarks.services.assets.create_snapshot",
        )
        self.mock_assets_create_snapshot = (
            self.mock_assets_create_snapshot_patcher.start()
        )
        self.mock_assets_create_snapshot.side_effect = (
            lambda asset: BookmarkAsset.objects.filter(id=asset.id).update(
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
        self.mock_load_favicon_patcher.stop()
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

    def test_load_favicon_should_create_favicon_file(self):
        bookmark = self.setup_bookmark()

        tasks.load_favicon(self.get_or_create_test_user(), bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(self.executed_count(), 1)
        self.assertEqual(bookmark.favicon_file, "https_example_com.png")

    def test_load_favicon_should_update_favicon_file(self):
        bookmark = self.setup_bookmark(favicon_file="https_example_com.png")

        self.mock_load_favicon.return_value = "https_example_updated_com.png"

        tasks.load_favicon(self.get_or_create_test_user(), bookmark)

        bookmark.refresh_from_db()
        self.mock_load_favicon.assert_called_once()
        self.assertEqual(bookmark.favicon_file, "https_example_updated_com.png")

    def test_load_favicon_should_handle_missing_bookmark(self):
        tasks._load_favicon_task(123)

        self.mock_load_favicon.assert_not_called()

    def test_load_favicon_should_not_save_stale_bookmark_data(self):
        bookmark = self.setup_bookmark()

        # update bookmark during API call to check that saving
        # the favicon does not overwrite updated bookmark data
        def mock_load_favicon_impl(url):
            bookmark.title = "Updated title"
            bookmark.save()
            return "https_example_com.png"

        self.mock_load_favicon.side_effect = mock_load_favicon_impl

        tasks.load_favicon(self.get_or_create_test_user(), bookmark)
        bookmark.refresh_from_db()

        self.assertEqual(bookmark.title, "Updated title")
        self.assertEqual(bookmark.favicon_file, "https_example_com.png")

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_load_favicon_should_not_run_when_background_tasks_are_disabled(self):
        bookmark = self.setup_bookmark()
        tasks.load_favicon(self.get_or_create_test_user(), bookmark)

        self.assertEqual(self.executed_count(), 0)

    def test_load_favicon_should_not_run_when_favicon_feature_is_disabled(self):
        self.user.profile.enable_favicons = False
        self.user.profile.save()

        bookmark = self.setup_bookmark()
        tasks.load_favicon(self.get_or_create_test_user(), bookmark)

        self.assertEqual(self.executed_count(), 0)

    def test_schedule_bookmarks_without_favicons_should_load_favicon_for_all_bookmarks_without_favicon(
        self,
    ):
        user = self.get_or_create_test_user()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark(favicon_file="https_example_com.png")
        self.setup_bookmark(favicon_file="https_example_com.png")
        self.setup_bookmark(favicon_file="https_example_com.png")

        tasks.schedule_bookmarks_without_favicons(user)

        self.assertEqual(self.executed_count(), 4)
        self.assertEqual(self.mock_load_favicon.call_count, 3)

    def test_schedule_bookmarks_without_favicons_should_only_update_user_owned_bookmarks(
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

        tasks.schedule_bookmarks_without_favicons(user)

        self.assertEqual(self.mock_load_favicon.call_count, 3)

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_schedule_bookmarks_without_favicons_should_not_run_when_background_tasks_are_disabled(
        self,
    ):
        self.setup_bookmark()
        tasks.schedule_bookmarks_without_favicons(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

    def test_schedule_bookmarks_without_favicons_should_not_run_when_favicon_feature_is_disabled(
        self,
    ):
        self.user.profile.enable_favicons = False
        self.user.profile.save()

        self.setup_bookmark()
        tasks.schedule_bookmarks_without_favicons(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

    def test_schedule_refresh_favicons_should_update_favicon_for_all_bookmarks(self):
        user = self.get_or_create_test_user()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark()
        self.setup_bookmark(favicon_file="https_example_com.png")
        self.setup_bookmark(favicon_file="https_example_com.png")
        self.setup_bookmark(favicon_file="https_example_com.png")

        tasks.schedule_refresh_favicons(user)

        self.assertEqual(self.executed_count(), 7)
        self.assertEqual(self.mock_load_favicon.call_count, 6)

    def test_schedule_refresh_favicons_should_only_update_user_owned_bookmarks(self):
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

        tasks.schedule_refresh_favicons(user)

        self.assertEqual(self.mock_load_favicon.call_count, 3)

    @override_settings(LD_DISABLE_BACKGROUND_TASKS=True)
    def test_schedule_refresh_favicons_should_not_run_when_background_tasks_are_disabled(
        self,
    ):
        self.setup_bookmark()
        tasks.schedule_refresh_favicons(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

    @override_settings(LD_ENABLE_REFRESH_FAVICONS=False)
    def test_schedule_refresh_favicons_should_not_run_when_refresh_is_disabled(self):
        self.setup_bookmark()
        tasks.schedule_refresh_favicons(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

    def test_schedule_refresh_favicons_should_not_run_when_favicon_feature_is_disabled(
        self,
    ):
        self.user.profile.enable_favicons = False
        self.user.profile.save()

        self.setup_bookmark()
        tasks.schedule_refresh_favicons(self.get_or_create_test_user())

        self.assertEqual(self.executed_count(), 0)

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

        tasks.load_preview_image(self.get_or_create_test_user(), bookmark)

        bookmark.refresh_from_db()
        self.mock_load_preview_image.assert_called_once()
        self.assertEqual(bookmark.preview_image_file, "preview_image_upd.png")

    def test_load_preview_image_should_set_blank_when_none_is_returned(self):
        bookmark = self.setup_bookmark(
            preview_image_file="preview_image.png",
        )

        self.mock_load_preview_image.return_value = None

        tasks.load_preview_image(self.get_or_create_test_user(), bookmark)

        bookmark.refresh_from_db()
        self.mock_load_preview_image.assert_called_once()
        self.assertEqual(bookmark.preview_image_file, "")

    def test_load_preview_image_should_handle_missing_bookmark(self):
        tasks._load_preview_image_task(123)

        self.mock_load_preview_image.assert_not_called()

    def test_load_preview_image_should_not_save_stale_bookmark_data(self):
        bookmark = self.setup_bookmark()

        # update bookmark during API call to check that saving
        # the image does not overwrite updated bookmark data
        def mock_load_preview_image_impl(url, bookmark_obj):
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
            "bookmarks.services.tasks._kick_html_snapshot_dispatcher"
        ) as mock_kick_html_snapshot_dispatcher:
            tasks.create_html_snapshot(bookmark)
            self.assertEqual(BookmarkAsset.objects.count(), 1)

            tasks.create_html_snapshot(bookmark)
            self.assertEqual(BookmarkAsset.objects.count(), 2)

            assets = BookmarkAsset.objects.filter(bookmark=bookmark)
            for asset in assets:
                self.assertEqual(asset.bookmark, bookmark)
                self.assertEqual(asset.asset_type, BookmarkAsset.TYPE_SNAPSHOT)
                self.assertEqual(asset.content_type, BookmarkAsset.CONTENT_TYPE_HTML)
                self.assertIn("HTML snapshot", asset.display_name)
                self.assertEqual(asset.status, BookmarkAsset.STATUS_PENDING)

            self.assertEqual(mock_kick_html_snapshot_dispatcher.call_count, 2)
            self.mock_assets_create_snapshot.assert_not_called()

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_create_html_snapshots_should_kick_dispatcher_once(self):
        bookmarks = [
            self.setup_bookmark(url="https://example.com/1"),
            self.setup_bookmark(url="https://example.com/2"),
            self.setup_bookmark(url="https://example.com/3"),
        ]

        with mock.patch(
            "bookmarks.services.tasks._kick_html_snapshot_dispatcher"
        ) as mock_kick_html_snapshot_dispatcher:
            tasks.create_html_snapshots(bookmarks)

        self.assertEqual(BookmarkAsset.objects.count(), 3)
        self.assertEqual(mock_kick_html_snapshot_dispatcher.call_count, 1)

    @override_settings(LD_ENABLE_SNAPSHOTS=True)
    def test_schedule_html_snapshots_should_kick_dispatcher_for_pending_assets(self):
        bookmark = self.setup_bookmark(url="https://example.com")
        self.setup_asset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
        )

        with mock.patch(
            "bookmarks.services.tasks._kick_html_snapshot_dispatcher"
        ) as mock_kick_html_snapshot_dispatcher:
            tasks._schedule_html_snapshots_task()

        mock_kick_html_snapshot_dispatcher.assert_called_once_with()

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
            "bookmarks.services.tasks._kick_html_snapshot_dispatcher"
        ) as mock_kick_html_snapshot_dispatcher:
            tasks._schedule_html_snapshots_task()

        mock_kick_html_snapshot_dispatcher.assert_not_called()

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

    def test_refresh_metadata_task_should_handle_missing_bookmark(self):
        with mock.patch(
            "bookmarks.services.website_loader.load_website_metadata"
        ) as mock_load_website_metadata:
            tasks._refresh_metadata_task(123)

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
