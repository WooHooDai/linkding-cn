"""健康检查视图测试：单条检查 API、任务状态接口、批量动作、bundle 一键检查、设置表单。"""

from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bookmarks.models import (
    HEALTH_STATUS_DEAD,
    Bookmark,
    BookmarkBundle,
    CheckJob,
    UserProfileHealthForm,
)
from bookmarks.tests.helpers import BookmarkFactoryMixin
from bookmarks.views import health_check as health_check_views


class ChecksViewsTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)
        self.b1 = self.setup_bookmark()
        self.b2 = self.setup_bookmark()
        Bookmark.objects.filter(pk=self.b1.pk).update(
            health_status=HEALTH_STATUS_DEAD,
            health_details={"reason": "HTTP 404", "job_id": 1},
        )

    def test_check_single_returns_stale_and_label(self):
        """单条重检返回 checked_at_label / health_stale 供列表弹层刷新使用。"""
        import json
        from datetime import timedelta

        self.b1.health_status = "blocked"
        self.b1.health_details = {
            "checked_at": (timezone.now() - timedelta(days=1)).isoformat(),
            "http_status": 403,
        }
        self.b1.save()
        with mock.patch(
            "bookmarks.views.health_check.health_checker.check_bookmark"
        ) as m:
            m.return_value = None
            response = self.client.post(
                reverse("linkding:health.check", args=[self.b1.pk])
            )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["ok"], True)
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["checked_at_label"], "Checked at")
        self.assertEqual(data["health_stale"], False)

    def test_job_status_api(self):
        job = CheckJob.objects.create(
            owner=self.user,
            scope={"mode": "all"},
            state=CheckJob.STATE_RUNNING,
            total=10,
            done=4,
        )
        response = self.client.get(reverse("linkding:health.status", args=[job.pk]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["state"], "running")
        self.assertEqual(data["progress"], 40)

    def test_job_status_owner_isolation(self):
        from django.contrib.auth.models import User

        other = User.objects.create_user(
            "otheruser", "other@example.com", "password123"
        )
        job = CheckJob.objects.create(owner=other, scope={"mode": "all"})
        response = self.client.get(reverse("linkding:health.status", args=[job.pk]))
        self.assertEqual(response.status_code, 404)

    def test_check_single_updates_bookmark(self):
        def _fake_check(bookmark, job_id=None, write=True, username=""):
            result = {"status": HEALTH_STATUS_DEAD, "reason": "HTTP 404"}
            if write:
                health_check_views.health_checker.persist_bookmark_result(
                    bookmark, result, job_id
                )
            return result

        with mock.patch.object(
            health_check_views.health_checker,
            "check_bookmark",
            side_effect=_fake_check,
        ) as mock_check:
            response = self.client.post(
                reverse("linkding:health.check", args=[self.b2.pk])
            )
        mock_check.assert_called_once()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], HEALTH_STATUS_DEAD)
        self.assertEqual(data["status_display"], "Dead")
        self.assertIsNotNone(data["checked_at"])
        self.assertRegex(data["checked_at_display"], r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}")
        # 写回生效
        self.b2.refresh_from_db()
        self.assertEqual(self.b2.health_status, HEALTH_STATUS_DEAD)
        self.assertIn("checked_at", self.b2.health_details)

    def test_check_single_requires_owner(self):
        other_user = self.setup_user()
        other_bookmark = self.setup_bookmark(user=other_user)
        response = self.client.post(
            reverse("linkding:health.check", args=[other_bookmark.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_check_single_requires_post(self):
        response = self.client.get(reverse("linkding:health.check", args=[self.b1.pk]))
        self.assertEqual(response.status_code, 405)

    def test_check_single_unchecked_when_skipped(self):
        # 域名禁用健康检查（L1+L2 关）→ 返回 status=None，页面显示 unchecked
        with mock.patch.object(
            health_check_views.health_checker,
            "check_bookmark",
            return_value={"status": None, "skipped": True, "reason": "disabled"},
        ):
            response = self.client.post(
                reverse("linkding:health.check", args=[self.b2.pk])
            )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIsNone(data["status"])
        self.assertEqual(data["status_display"], "")
        self.b2.refresh_from_db()
        self.assertIsNone(self.b2.health_status)


@mock.patch("bookmarks.views.health_check._spawn_check_job")
class BulkCheckHealthTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)
        self.b1 = self.setup_bookmark()
        self.b2 = self.setup_bookmark()

    def _post_bulk(self, ids, across=False):
        data = {
            "bulk_execute": "",
            "bulk_action": "bulk_check_health",
            "bookmark_id": [str(i) for i in ids],
        }
        if across:
            data["bulk_select_across"] = "on"
        return self.client.post(reverse("linkding:bookmarks.index.action"), data)

    def test_starts_job_for_selected(self, mock_spawn):
        response = self._post_bulk([self.b1.pk, self.b2.pk])
        self.assertEqual(response.status_code, 302)
        job = CheckJob.objects.filter(owner=self.user).latest("created_at")
        self.assertEqual(job.scope["mode"], "ids")
        self.assertEqual(sorted(job.scope["ids"]), sorted([self.b1.pk, self.b2.pk]))
        mock_spawn.assert_called_once_with(job)

    def test_deduplicates_duplicate_ids(self, mock_spawn):
        # 前端会同时提交复选框与注入的隐藏域，同一 id 可能到达两次；
        # 任务 scope、total 与成功 toast 计数都必须按去重后的数量。
        import re

        from django.contrib.messages import get_messages

        response = self._post_bulk(
            [self.b1.pk, self.b1.pk, self.b2.pk, self.b2.pk]
        )
        self.assertEqual(response.status_code, 302)
        job = CheckJob.objects.filter(owner=self.user).latest("created_at")
        self.assertEqual(sorted(job.scope["ids"]), sorted([self.b1.pk, self.b2.pk]))
        self.assertEqual(job.total, 2)
        msgs = [str(m.message) for m in get_messages(response.wsgi_request)]
        toast_counts = [
            int(x) for m in msgs for x in re.findall(r"\d+", m) if m
        ]
        self.assertEqual(toast_counts, [2])
        mock_spawn.assert_called_once_with(job)

    def test_rejects_when_active_job(self, mock_spawn):
        CheckJob.objects.create(owner=self.user, scope={"mode": "all"})
        response = self._post_bulk([self.b1.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CheckJob.objects.filter(owner=self.user).count(), 1)
        mock_spawn.assert_not_called()


@mock.patch("bookmarks.views.health_check._spawn_check_job")
class BundleCheckHealthTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.client.force_login(self.user)
        self.bundle = BookmarkBundle.objects.create(
            owner=self.user,
            name="check me",
            search="title:foo",
        )

    def test_starts_bundle_job(self, mock_spawn):
        response = self.client.post(
            reverse("linkding:bundles.action"),
            {"check_bundle": str(self.bundle.pk)},
        )
        self.assertEqual(response.status_code, 302)
        job = CheckJob.objects.filter(owner=self.user).latest("created_at")
        self.assertEqual(job.scope["mode"], "bundle")
        self.assertEqual(job.scope["bundle_id"], self.bundle.pk)
        mock_spawn.assert_called_once_with(job)

    def test_bundle_button_in_list(self, mock_spawn):
        response = self.client.get(reverse("linkding:bundles.index"))
        html = response.content.decode()
        self.assertIn('name="check_bundle"', html)
        # 位于 Edit 之前
        self.assertLess(html.index('check_bundle'), html.index(">Edit<"))

    def test_bundle_check_toast_renders_once(self, mock_spawn):
        # 消息由 layout 统一渲染，页面内重复 include 会导致同一 toast 出现两次
        response = self.client.post(
            reverse("linkding:bundles.action"),
            {"check_bundle": str(self.bundle.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count("data-toast-message"), 1)
        self.assertIn("Health check started for filter", html)


class HealthSettingsFormTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.profile = self.user.profile

    def test_max_age_days_zero_is_valid(self):
        # 0 = 永不过期（功能设计），应为合法值
        form = UserProfileHealthForm(
            {"health_max_age_days": "0", "health_check_workers": "2", "health_domain_interval": "1"},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid())
        self.assertNotIn("health_max_age_days", form.errors)

    def test_max_age_days_negative_invalid(self):
        form = UserProfileHealthForm(
            {"health_max_age_days": "-1", "health_check_workers": "2", "health_domain_interval": "1"},
            instance=self.profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("health_max_age_days", form.errors)

    def test_empty_means_default(self):
        form = UserProfileHealthForm(
            {"health_max_age_days": "", "health_check_workers": "", "health_domain_interval": ""},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid())
        form.save()
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.health_max_age_days)
        self.assertIsNone(self.profile.health_check_workers)
        self.assertIsNone(self.profile.health_domain_interval)

    def test_saves_values(self):
        form = UserProfileHealthForm(
            {"health_max_age_days": "30", "health_check_workers": "4", "health_domain_interval": "2"},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid())
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.health_max_age_days, 30)
        self.assertEqual(self.profile.health_check_workers, 4)
        self.assertEqual(self.profile.health_domain_interval, 2)
