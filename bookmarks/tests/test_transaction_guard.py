"""TransactionGuardMiddleware 回归测试。

守卫的目标：请求结束时若连接停留在事务内（手动事务模式或未闭合的 atomic 块），
记录探针告警日志并关闭连接（回滚+重建），使 SQLite WAL 陈旧快照不可能跨请求存活。

注意：Django 的 sqlite 测试库默认是 :memory:，而 sqlite 后端对内存库会
故意忽略 close()（防止误销毁数据库），因此无法在测试中直接断言"连接已被关闭"。
测试改为验证：(1) 泄漏判定逻辑 _leaked() 的正确性；(2) 探针告警在真实泄漏请求上
触发、在正常请求上不触发。关闭连接的端到端行为已在生产（文件型 DB）实测验证。
"""

from django.db import connection, transaction
from django.http import HttpResponse
from django.test import Client, TransactionTestCase, override_settings
from django.urls import path

from bookmarks.middlewares import TransactionGuardMiddleware
from bookmarks.models import FaviconCache


def leaked_view(request):
    """模拟事务泄漏：进入手动事务模式并写入后直接返回（不提交）。"""
    transaction.set_autocommit(False)
    FaviconCache.objects.create(
        domain="leak-test.example.com", status=FaviconCache.STATUS_PENDING
    )
    return HttpResponse("leaked")


def raising_leaked_view(request):
    """模拟事务泄漏 + 异常：异常路径下事务同样残留。"""
    transaction.set_autocommit(False)
    FaviconCache.objects.create(
        domain="leak-exc.example.com", status=FaviconCache.STATUS_PENDING
    )
    raise ValueError("boom")


def normal_view(request):
    return HttpResponse("ok")


urlpatterns = [
    path("leak/", leaked_view),
    path("raise-leak/", raising_leaked_view),
    path("normal/", normal_view),
]


@override_settings(ROOT_URLCONF=__name__)
class TransactionGuardLogicTest(TransactionTestCase):
    """_leaked() 判定矩阵：真实泄漏=真；正常/测试框架事务=假。"""

    def test_manual_transaction_mode_is_leak(self):
        transaction.set_autocommit(False)
        try:
            self.assertTrue(TransactionGuardMiddleware._leaked(connection))
        finally:
            transaction.rollback()
            transaction.set_autocommit(True)

    def test_normal_state_is_not_leak(self):
        self.assertFalse(TransactionGuardMiddleware._leaked(connection))

    def test_testcase_atomic_block_is_not_leak(self):
        atomic = transaction.atomic()
        atomic._from_testcase = True
        with atomic:
            self.assertFalse(TransactionGuardMiddleware._leaked(connection))

    def test_plain_atomic_block_is_leak(self):
        with transaction.atomic():
            self.assertTrue(TransactionGuardMiddleware._leaked(connection))


@override_settings(ROOT_URLCONF=__name__)
class TransactionGuardProbeTest(TransactionTestCase):
    """探针告警：泄漏请求必须触发，正常请求不得误报。"""

    def setUp(self):
        self.client = Client()

    def test_probe_fires_on_leaked_request(self):
        with self.assertLogs("bookmarks.middlewares", level="WARNING") as cm:
            self.client.get("/leak/")
        self.assertTrue(
            any("残留事务" in msg for msg in cm.output),
            "泄漏请求应触发探针告警",
        )

    def test_probe_fires_on_exception_path(self):
        with self.assertRaises(ValueError), self.assertLogs(
            "bookmarks.middlewares", level="WARNING"
        ) as cm:
            self.client.get("/raise-leak/")
        self.assertTrue(any("残留事务" in msg for msg in cm.output))

    def test_no_probe_on_normal_request(self):
        with self.assertNoLogs("bookmarks.middlewares", level="WARNING"):
            self.client.get("/normal/")

    @override_settings(LD_TRANSACTION_GUARD_ENABLED=False)
    def test_no_probe_when_guard_disabled(self):
        """开关关闭时守卫不介入：泄漏请求不告警（用于 ATOMIC_REQUESTS 等场景）。"""
        with self.assertNoLogs("bookmarks.middlewares", level="WARNING"):
            self.client.get("/leak/")
