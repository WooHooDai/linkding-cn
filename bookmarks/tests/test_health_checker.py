"""书签健康检查引擎与任务状态机单测：分类判定、内容信号、写回、执行/断点续跑/中断/放弃/心跳。"""

from datetime import timedelta
from unittest import mock

import requests
from django.test import TestCase
from django.utils import timezone

from bookmarks.models import (
    HEALTH_STATUS_BLOCKED,
    HEALTH_STATUS_MISSING,
    HEALTH_STATUS_DEAD,
    HEALTH_STATUS_OK,
    HEALTH_STATUS_FAILED,
    CheckJob,
)
from bookmarks.services import health_checker
from bookmarks.tests.helpers import BookmarkFactoryMixin


def _fetch_mock(status, final_url=None, content=b""):
    # 引擎 _fetch 现返回 (status, final_url, content, probes) 4 元组
    def _inner(url, config):
        return status, final_url or url, content, {"head": None, "get": None}

    return _inner


class CheckUrlTestCase(TestCase):
    def setUp(self):
        self.default_config = health_checker._default_health_config()

    def test_ok_status(self):
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=b"<html>ok</html>"),
        ):
            result = health_checker.check_url("https://example.com/")
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["reason"], "")

    def test_dead_status(self):
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(404)):
            result = health_checker.check_url("https://example.com/gone")
        self.assertEqual(result["status"], HEALTH_STATUS_DEAD)
        self.assertEqual(result["reason"], "HTTP 404")

    def test_blocked_status_default(self):
        # 403 默认归 blocked（反爬/需登录），而不是 dead
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url("https://example.com/")
        self.assertEqual(result["status"], HEALTH_STATUS_BLOCKED)
        self.assertEqual(result["reason"], "HTTP 403")

    def test_blocked_status_custom(self):
        config = dict(self.default_config)
        config["blocked_status"] = [401, 403, 407, 429, 451]
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(451)):
            result = health_checker.check_url("https://example.com/", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_BLOCKED)

    def test_request_failure_is_unreachable(self):
        def _raise(url, config):
            raise requests.ConnectionError("connection refused")

        with mock.patch.object(health_checker, "_fetch", side_effect=_raise):
            result = health_checker.check_url("https://example.com/")
        self.assertEqual(result["status"], HEALTH_STATUS_FAILED)
        self.assertTrue(result["reason"].startswith("request failed"))

    def test_custom_reason_overrides_default(self):
        """适配器配置 reason 时，非 ok 判定使用自定义文案。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/", {"reason": "被站点拦截"}
            )
        self.assertEqual(result["status"], HEALTH_STATUS_BLOCKED)
        self.assertEqual(result["reason"], "被站点拦截")

    def test_custom_reason_http_placeholder(self):
        """{http_status} 占位符替换为实际 HTTP 状态码。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/", {"reason": "HTTP {http_status} 被拦截"}
            )
        self.assertEqual(result["reason"], "HTTP 403 被拦截")

    def test_custom_reason_not_applied_to_ok(self):
        """ok 判定不使用自定义 reason（引擎置空）。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(200)):
            result = health_checker.check_url(
                "https://example.com/", {"reason": "不该出现"}
            )
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        self.assertEqual(result["reason"], "")

    def test_custom_reason_skipped_not_applied(self):
        """health_enabled=false（skipped）不属于判定结果，reason 不覆盖。"""
        result = health_checker.check_url(
            "https://example.com/",
            {
                "reason": "不该出现",
                "health_enabled": False,
            },
        )
        self.assertTrue(result.get("skipped"))
        self.assertIsNone(result["status"])

    # ── reason 分层（字符串或对象，default → status → http_code）──

    def test_reason_object_status_default(self):
        """对象形态：命中 <status>.default。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/",
                {
                    "reason": {
                        "default": "全局兜底",
                        "blocked": {"default": "被站点拦截"},
                    }
                },
            )
        self.assertEqual(result["status"], HEALTH_STATUS_BLOCKED)
        self.assertEqual(result["reason"], "被站点拦截")

    def test_reason_object_http_code_wins(self):
        """对象形态：<status>.<http_code> 优先于 <status>.default。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/",
                {
                    "reason": {
                        "blocked": {
                            "default": "被站点拦截",
                            "403": "被反爬拦截，请稍后重试",
                        }
                    }
                },
            )
        self.assertEqual(result["reason"], "被反爬拦截，请稍后重试")

    def test_reason_object_falls_back_to_global_default(self):
        """对象形态：未命中状态级配置时回退 reason.default。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/",
                {"reason": {"default": "全局兜底（HTTP {http_status}）"}},
            )
        self.assertEqual(result["reason"], "全局兜底（HTTP 403）")

    def test_reason_object_no_match_keeps_engine_default(self):
        """对象形态：全部未命中时保留引擎默认文案（不覆盖）。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/",
                {"reason": {"dead": "仅 dead 用"}},
            )
        self.assertNotEqual(result["reason"], "仅 dead 用")
        self.assertIn("HTTP 403", result["reason"])

    def test_reason_string_is_global_fallback(self):
        """字符串形态等价于旧行为（全局兜底）。"""
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url(
                "https://example.com/", {"reason": "被站点拦截"}
            )
        self.assertEqual(result["reason"], "被站点拦截")

    def test_too_many_redirects_is_dead(self):
        def _raise(url, config):
            raise requests.exceptions.TooManyRedirects("loop")

        with mock.patch.object(health_checker, "_fetch", side_effect=_raise):
            result = health_checker.check_url("https://example.com/")
        self.assertEqual(result["status"], HEALTH_STATUS_DEAD)
        self.assertEqual(result["reason"], "redirect loop")

    def test_content_signal_keyword(self):
        html = "<html><head><title>页面不存在</title></head><body>x</body></html>"
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone")
        self.assertEqual(result["status"], HEALTH_STATUS_MISSING)
        self.assertTrue(result["content_signals"])
        self.assertEqual(result["content_signals"][0]["type"], "invalid_pattern")

    def test_content_signal_selector(self):
        config = dict(self.default_config)
        config["invalid_selectors"] = ["h1.not-found"]
        html = '<html><body><h1 class="not-found">404</h1></body></html>'
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_MISSING)

    def test_valid_selector_short_circuits(self):
        config = dict(self.default_config)
        config["valid_selectors"] = [".article-body"]
        config["invalid_patterns"] = ["404", "not found"]
        html = (
            '<html><body><h1 class="not-found">404</h1>'
            '<div class="article-body">real content</div></body></html>'
        )
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        self.assertEqual(result["reason"], "valid content matched")

    def test_valid_pattern_short_circuits(self):
        config = dict(self.default_config)
        config["valid_patterns"] = ["dashboard"]
        html = "<html><title>dashboard</title><body>404 not found</body></html>"
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)

    def test_blocked_location_patterns_blocked(self):
        config = dict(self.default_config)
        config["blocked_location_patterns"] = ["login", "signin"]
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, final_url="https://example.com/login"),
        ):
            result = health_checker.check_url(
                "https://example.com/private", config=config
            )
        self.assertEqual(result["status"], HEALTH_STATUS_BLOCKED)
        self.assertIn("login", result["reason"])

    def test_check_selectors_body_only(self):
        config = dict(self.default_config)
        config["check_selectors"] = ["body"]
        html = "<html><title>404 not found</title><body>real content</body></html>"
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)

    def test_nested_config_aligns_with_cookie_verify(self):
        # 嵌套结构 http_head_probe.* / content_check.* 应被归一化识别
        config = {
            "http_head_probe": {
                "accept_status": [200],
                "blocked_status": [403],
                "blocked_location_patterns": ["login"],
            },
            "content_check": {
                "enabled": True,
                "valid_selectors": [".article"],
                "invalid_patterns": ["404"],
            },
        }
        html = '<html><body><div class="article">ok</div></body></html>'
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        # blocked_status 覆盖默认 [401,403,407,429] → 403 仍 blocked
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(403)):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_BLOCKED)

    def test_valid_and_invalid_both_present_valid_wins(self):
        # valid_* 与 invalid_* 同时命中时，正向短路优先（文档化语义）
        config = {
            "content_check": {
                "enabled": True,
                "valid_patterns": ["dashboard"],
                "invalid_patterns": ["404"],
            },
        }
        html = "<html><title>dashboard 404</title></html>"
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        self.assertEqual(result["reason"], "valid content matched")

    def test_probe_disabled_skips_head_uses_get(self):
        # L1 禁用：跳过 HEAD，直接 GET 判定（正常显示结果）
        config = dict(self.default_config)
        config["probe_enabled"] = False
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(404)):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_DEAD)

    def test_health_enabled_false_skips_check(self):
        # health_enabled=false：该域名不做健康检查 → skipped，status=None（unchecked）
        # 且不应调用 _fetch（在 check_url 入口即短路）
        config = dict(self.default_config)
        config["health_enabled"] = False
        called = {"n": 0}

        def _spy(*a, **kw):
            called["n"] += 1
            return _fetch_mock(200)(*a, **kw)

        with mock.patch.object(health_checker, "_fetch", side_effect=_spy):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertTrue(result["skipped"])
        self.assertIsNone(result["status"])
        self.assertEqual(called["n"], 0)

    def test_l1_l2_sub_switches_still_produce_verdict(self):
        # L1 关闭 + L2 关闭 ≠ skipped：仍走 GET 按状态码判定（不读正文）
        # 双关的"跳过"语义由顶层 health_enabled 承担
        config = dict(self.default_config)
        config["probe_enabled"] = False
        config["check_content"] = False
        with mock.patch.object(health_checker, "_fetch", side_effect=_fetch_mock(200)):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        self.assertNotIn("skipped", result)

    def test_request_url_overrides_probe_target(self):
        # 通用 request_url（resolver 输出 _request_url）改写探测目标
        config = dict(self.default_config)
        config["_request_url"] = "https://api.example.com/items/1"
        fetched = {}

        class _FakeResponse:
            def __init__(self, url):
                self.status_code = 200
                self.url = url

            def iter_content(self, chunk_size=1024):
                return iter([b"<html>ok</html>"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _fake_head(url, **kwargs):
            fetched["head"] = url
            return _FakeResponse(url)

        def _fake_get(url, **kwargs):
            fetched["get"] = url
            return _FakeResponse(url)

        with (
            mock.patch.object(health_checker.requests, "head", side_effect=_fake_head),
            mock.patch.object(health_checker.requests, "get", side_effect=_fake_get),
        ):
            result = health_checker.check_url(
                "https://example.com/items/1", config=config
            )
        self.assertEqual(fetched["head"], "https://api.example.com/items/1")
        self.assertEqual(fetched["get"], "https://api.example.com/items/1")
        self.assertEqual(result["status"], HEALTH_STATUS_OK)

    def test_redirect_url_recorded(self):
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, final_url="https://new.example.com/"),
        ):
            result = health_checker.check_url("https://example.com/")
        self.assertEqual(result["redirect_url"], "https://new.example.com/")

    def test_check_content_disabled(self):
        config = dict(self.default_config)
        config["check_content"] = False
        html = "<html><title>页面不存在</title></html>"
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(200, content=html.encode()),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)


    def test_head_4xx_falls_back_to_get(self):
        # HEAD 返回 404 不可直接采信（部分站点/CDN 对 HEAD 特殊处理，如 xhslink.cn
        # 恒返回 404 但 GET 正常）：应落入 GET 复核，以 GET 结果为准
        config = dict(self.default_config)

        class _FakeResponse:
            def __init__(self, status_code, url="https://example.com/gone"):
                self.status_code = status_code
                self.url = url

            def iter_content(self, chunk_size=1024):
                return iter([b"<html>ok</html>"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with (
            mock.patch.object(
                health_checker.requests, "head", side_effect=lambda url, **kw: _FakeResponse(404)
            ),
            mock.patch.object(
                health_checker.requests, "get", side_effect=lambda url, **kw: _FakeResponse(200)
            ),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_OK)
        self.assertEqual(result["http_status"], 200)

    def test_head_4xx_get_also_4xx_still_dead(self):
        # HEAD 与 GET 均为 404 → 仍应判 dead（GET 复核保持原判定）
        config = dict(self.default_config)

        class _FakeResponse:
            def __init__(self, status_code, url="https://example.com/gone"):
                self.status_code = status_code
                self.url = url

            def iter_content(self, chunk_size=1024):
                return iter([b"<html>gone</html>"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with (
            mock.patch.object(
                health_checker.requests, "head", side_effect=lambda url, **kw: _FakeResponse(404)
            ),
            mock.patch.object(
                health_checker.requests, "get", side_effect=lambda url, **kw: _FakeResponse(404)
            ),
        ):
            result = health_checker.check_url("https://example.com/gone", config=config)
        self.assertEqual(result["status"], HEALTH_STATUS_DEAD)
        self.assertEqual(result["http_status"], 404)


class CheckBookmarkTestCase(TestCase, BookmarkFactoryMixin):
    def test_writes_back_status_and_details(self):
        bookmark = self.setup_bookmark()
        with mock.patch.object(
            health_checker,
            "_fetch",
            side_effect=_fetch_mock(404, content=b"<html></html>"),
        ):
            result = health_checker.check_bookmark(bookmark, job_id=42)

        bookmark.refresh_from_db()
        self.assertEqual(result["status"], HEALTH_STATUS_DEAD)
        self.assertEqual(bookmark.health_status, HEALTH_STATUS_DEAD)
        self.assertEqual(bookmark.health_details["http_status"], 404)
        self.assertEqual(bookmark.health_details["job_id"], 42)
        self.assertIn("checked_at", bookmark.health_details)


class ContentSignalsTestCase(TestCase):
    def test_keyword_matches_title(self):
        signals = health_checker._content_signals(
            "<html><title>文章已删除</title></html>",
            health_checker._default_health_config(),
        )
        self.assertTrue(signals)
        self.assertEqual(signals[0]["type"], "invalid_pattern")

    def test_no_signals(self):
        signals = health_checker._content_signals(
            "<html><title>Hello world</title><body>Content here</body></html>",
            health_checker._default_health_config(),
        )
        self.assertEqual(signals, [])

    def test_custom_selector(self):
        config = health_checker._default_health_config()
        config["invalid_selectors"] = [".post-error"]
        signals = health_checker._content_signals(
            '<html><body><div class="post-error">gone</div></body></html>', config
        )
        self.assertTrue(signals)
        self.assertEqual(signals[0]["type"], "invalid_selector")

    def test_valid_short_circuits_invalid(self):
        config = health_checker._default_health_config()
        config["valid_selectors"] = [".article"]
        config["invalid_patterns"] = ["404"]
        html = (
            '<html><body><div class="article">x</div><title>404</title></body></html>'
        )
        signals = health_checker._content_signals(html, config)
        self.assertEqual(signals, [{"type": "valid_selector", "value": ".article"}])


class ResolveJobBookmarksTestCase(TestCase, BookmarkFactoryMixin):
    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.active = self.setup_bookmark()
        self.archived = self.setup_bookmark(is_archived=True)
        self.deleted = self.setup_bookmark()
        self.deleted.is_deleted = True
        self.deleted.save()

    def _job(self, scope):
        return CheckJob.objects.create(owner=self.user, scope=scope)

    def test_all_mode_includes_archived_but_not_deleted(self):
        job = self._job({"mode": "all"})
        bookmarks = list(health_checker.resolve_job_bookmarks(job))
        ids = [b.pk for b in bookmarks]
        self.assertIn(self.active.pk, ids)
        self.assertIn(self.archived.pk, ids)
        self.assertNotIn(self.deleted.pk, ids)

    def test_all_mode_orders_archived_after_active(self):
        job = self._job({"mode": "all"})
        bookmarks = list(health_checker.resolve_job_bookmarks(job))
        self.assertLess(bookmarks.index(self.active), bookmarks.index(self.archived))

    def test_query_mode_includes_archived(self):
        job = self._job({"mode": "query", "query": ""})
        bookmarks = list(health_checker.resolve_job_bookmarks(job))
        ids = [b.pk for b in bookmarks]
        self.assertIn(self.active.pk, ids)
        self.assertIn(self.archived.pk, ids)
        self.assertNotIn(self.deleted.pk, ids)

    def test_ids_mode_excludes_deleted(self):
        job = self._job({"mode": "ids", "ids": [self.active.pk, self.deleted.pk]})
        bookmarks = list(health_checker.resolve_job_bookmarks(job))
        self.assertEqual([b.pk for b in bookmarks], [self.active.pk])

    def test_mode_ids(self):
        job = self._job({"mode": "ids", "ids": [self.active.pk]})
        ids = [b.pk for b in health_checker.resolve_job_bookmarks(job)]
        self.assertEqual(ids, [self.active.pk])

    def test_mode_ids_isolated_by_owner(self):
        from django.contrib.auth.models import User

        other = User.objects.create_user(
            "otheruser", "other@example.com", "password123"
        )
        other_bookmark = self.setup_bookmark(user=other)
        job = self._job({"mode": "ids", "ids": [self.active.pk, other_bookmark.pk]})
        ids = list(
            health_checker.resolve_job_bookmarks(job).values_list("id", flat=True)
        )
        self.assertEqual(ids, [self.active.pk])

    def test_mode_query(self):
        bookmark = self.setup_bookmark(title="health-check-token-bookmark")
        job = self._job({"mode": "query", "query": "health-check-token-bookmark"})
        ids = list(
            health_checker.resolve_job_bookmarks(job).values_list("id", flat=True)
        )
        self.assertIn(bookmark.pk, ids)



class RunJobErrorTestCase(TestCase, BookmarkFactoryMixin):
    def test_engine_error_does_not_write_status(self):
        """引擎异常等同于未完成检查：不写回任何状态（书签保持原值/NULL）。"""
        bookmark = self.setup_bookmark()
        job = CheckJob.objects.create(
            owner=self.user,
            scope={"mode": "ids", "ids": [bookmark.pk]},
            total=1,
            done=0,
        )
        with mock.patch.object(
            health_checker,
            "check_bookmark",
            side_effect=RuntimeError("boom"),
        ):
            health_checker.run_job(job)
        bookmark.refresh_from_db()
        # 未写回：保持 NULL（未检查）
        self.assertIsNone(bookmark.health_status)
        self.assertEqual(bookmark.health_details or {}, {})
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_COMPLETED)


class RunJobTestCase(TestCase, BookmarkFactoryMixin):
    """CheckJob 任务状态机：执行、断点续跑、中断、放弃、失败。"""

    def setUp(self):
        self.user = self.get_or_create_test_user()
        self.b1 = self.setup_bookmark()
        self.b2 = self.setup_bookmark()

    def _job(self, scope=None):
        return CheckJob.objects.create(
            owner=self.user,
            scope=scope or {"mode": "ids", "ids": [self.b1.pk, self.b2.pk]},
        )

    OK_RESULT = {"status": HEALTH_STATUS_OK, "http_status": 200, "reason": ""}

    @mock.patch.object(health_checker, "check_bookmark", autospec=True)
    def test_completes_job(self, mock_check):
        mock_check.return_value = self.OK_RESULT
        job = self._job()
        health_checker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_COMPLETED)
        self.assertEqual(job.done, 2)
        self.assertEqual(job.total, 2)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(mock_check.call_count, 2)
        # 主线程写库：书签已落结果
        self.b1.refresh_from_db()
        self.assertEqual(self.b1.health_status, HEALTH_STATUS_OK)
        self.assertEqual(self.b1.health_details["job_id"], job.pk)

    @mock.patch.object(health_checker, "check_bookmark", autospec=True)
    def test_resume_skips_already_checked(self, mock_check):
        mock_check.return_value = self.OK_RESULT
        job = self._job()
        health_checker.run_job(job)

        # 中断后续跑：已检查的应被跳过
        CheckJob.objects.filter(pk=job.pk).update(state=CheckJob.STATE_INTERRUPTED)
        job.refresh_from_db()
        mock_check.reset_mock()
        health_checker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_COMPLETED)
        self.assertEqual(job.done, 2)
        self.assertEqual(mock_check.call_count, 0)

    @mock.patch.object(health_checker, "check_bookmark", autospec=True)
    @mock.patch.object(
        health_checker,
        "persist_bookmark_result",
        wraps=health_checker.persist_bookmark_result,
    )
    def test_interrupt_stops_cleanly(self, mock_persist, mock_check):
        mock_check.return_value = self.OK_RESULT

        def _persist(bookmark, result, job_id=None):
            if not getattr(_persist, "called", False):
                _persist.called = True
                # 第一条落库后请求暂停（主线程内可见，SQLite 事务隔离下可靠）
                CheckJob.objects.filter(pk=job_id).update(stop_requested=True)
            return mock.DEFAULT  # wraps：交回真实写库函数

        mock_persist.side_effect = _persist
        job = self._job()
        health_checker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_INTERRUPTED)
        self.assertEqual(job.done, 1)
        self.assertIsNotNone(job.finished_at)

    @mock.patch.object(health_checker, "check_bookmark", autospec=True)
    @mock.patch.object(
        health_checker,
        "persist_bookmark_result",
        wraps=health_checker.persist_bookmark_result,
    )
    def test_cancel_while_running_keeps_cancelled(self, mock_persist, mock_check):
        mock_check.return_value = self.OK_RESULT

        def _persist(bookmark, result, job_id=None):
            CheckJob.objects.filter(pk=job_id).update(
                state=CheckJob.STATE_CANCELLED, stop_requested=True
            )
            return mock.DEFAULT

        mock_persist.side_effect = _persist
        job = self._job()
        health_checker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_CANCELLED)

    def test_paused_before_start_interrupts(self):
        job = self._job()
        CheckJob.objects.filter(pk=job.pk).update(stop_requested=True)
        health_checker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_INTERRUPTED)

    def test_rejects_completed_job(self):
        job = self._job()
        CheckJob.objects.filter(pk=job.pk).update(state=CheckJob.STATE_COMPLETED)
        job.refresh_from_db()
        with self.assertRaises(health_checker.HealthCheckError):
            health_checker.run_job(job)

    @mock.patch.object(health_checker, "check_bookmark", autospec=True)
    @mock.patch.object(health_checker, "persist_bookmark_result", autospec=True)
    def test_failure_marks_job_failed(self, mock_persist, mock_check):
        mock_check.return_value = self.OK_RESULT

        def _persist(bookmark, result, job_id=None):
            raise RuntimeError("boom")

        mock_persist.side_effect = _persist
        job = self._job()
        health_checker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.state, CheckJob.STATE_FAILED)
        self.assertIn("boom", job.error)


class HeartbeatStaleTestCase(TestCase, BookmarkFactoryMixin):
    def test_no_heartbeat_not_stale(self):
        job = CheckJob.objects.create(
            owner=self.get_or_create_test_user(), scope={"mode": "all"}
        )
        self.assertFalse(health_checker.is_heartbeat_stale(job))

    def test_fresh_heartbeat_not_stale(self):
        job = CheckJob.objects.create(
            owner=self.get_or_create_test_user(), scope={"mode": "all"}
        )
        CheckJob.objects.filter(pk=job.pk).update(heartbeat=timezone.now())
        job.refresh_from_db()
        self.assertFalse(health_checker.is_heartbeat_stale(job))

    def test_old_heartbeat_stale(self):
        job = CheckJob.objects.create(
            owner=self.get_or_create_test_user(), scope={"mode": "all"}
        )
        old = timezone.now() - timedelta(seconds=3600)
        CheckJob.objects.filter(pk=job.pk).update(heartbeat=old)
        job.refresh_from_db()
        self.assertTrue(health_checker.is_heartbeat_stale(job))
