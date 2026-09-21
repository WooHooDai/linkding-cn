"""UI 健康筛选测试：health_status 多选 + Health checked 日期筛选。"""

from datetime import date

from django.test import TestCase

from bookmarks.models import (
    HEALTH_STATUS_BLOCKED,
    HEALTH_STATUS_DEAD,
    HEALTH_STATUS_OK,
    Bookmark,
    BookmarkSearch,
)
from bookmarks.queries import query_bookmarks
from bookmarks.tests.helpers import BookmarkFactoryMixin


class HealthFilterSearchTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.profile = self.user.profile
        self.ok_bookmark = self.setup_bookmark()
        self.dead_bookmark = self.setup_bookmark()
        self.blocked_bookmark = self.setup_bookmark()
        self.unchecked_bookmark = self.setup_bookmark()
        Bookmark.objects.filter(pk=self.ok_bookmark.pk).update(
            health_status=HEALTH_STATUS_OK, health_details={"checked_at": "2026-09-01T10:00:00+00:00"}
        )
        Bookmark.objects.filter(pk=self.dead_bookmark.pk).update(
            health_status=HEALTH_STATUS_DEAD, health_details={"checked_at": "2026-08-15T10:00:00+00:00"}
        )
        Bookmark.objects.filter(pk=self.blocked_bookmark.pk).update(
            health_status=HEALTH_STATUS_BLOCKED, health_details={"checked_at": "2026-09-10T10:00:00+00:00"}
        )

    def _ids(self, search):
        return set(query_bookmarks(self.user, self.profile, search).values_list("id", flat=True))

    def test_all_value_ignored_in_filter(self):
        # "all" 是筛选面板的全选控件，不作为过滤条件（与未勾选等价，返回全部）
        ids = self._ids(BookmarkSearch(health_status=["all"]))
        self.assertEqual(
            ids,
            {self.ok_bookmark.pk, self.dead_bookmark.pk, self.blocked_bookmark.pk, self.unchecked_bookmark.pk},
        )

    def test_from_request_strips_all(self):
        # 提交 ?health_status=all 时后端忽略 all
        from django.http import QueryDict

        qd = QueryDict("health_status=all&health_status=dead")
        search = BookmarkSearch.from_request(None, qd)
        self.assertEqual(search.health_status, ["dead"])

    def test_single_status(self):
        ids = self._ids(BookmarkSearch(health_status=["dead"]))
        self.assertEqual(ids, {self.dead_bookmark.pk})

    def test_multiple_status_or(self):
        ids = self._ids(BookmarkSearch(health_status=["dead", "blocked"]))
        self.assertEqual(ids, {self.dead_bookmark.pk, self.blocked_bookmark.pk})

    def test_unknown_matches_only_unchecked(self):
        ids = self._ids(BookmarkSearch(health_status=["unknown"]))
        self.assertEqual(ids, {self.unchecked_bookmark.pk})
        self.assertNotIn(self.ok_bookmark.pk, ids)

    def test_unknown_combined_with_status(self):
        ids = self._ids(BookmarkSearch(health_status=["dead", "unknown"]))
        self.assertEqual(ids, {self.dead_bookmark.pk, self.unchecked_bookmark.pk})

    def test_health_checked_date_range(self):
        ids = self._ids(
            BookmarkSearch(
                date_filter_by="health",
                date_filter_start=date(2026, 9, 1),
                date_filter_end=date(2026, 9, 30),
            )
        )
        # ok(9/1) 与 blocked(9/10) 在范围内；dead(8/15) 不在
        self.assertEqual(ids, {self.ok_bookmark.pk, self.blocked_bookmark.pk})

    def test_health_checked_date_start_only(self):
        ids = self._ids(
            BookmarkSearch(
                date_filter_by="health",
                date_filter_start=date(2026, 9, 1),
            )
        )
        self.assertEqual(ids, {self.ok_bookmark.pk, self.blocked_bookmark.pk})

    def test_combined_status_and_date(self):
        ids = self._ids(
            BookmarkSearch(
                health_status=["ok"],
                date_filter_by="health",
                date_filter_start=date(2026, 9, 1),
                date_filter_end=date(2026, 9, 30),
            )
        )
        self.assertEqual(ids, {self.ok_bookmark.pk})
