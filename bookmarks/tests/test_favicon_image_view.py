import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import Client, TestCase, override_settings

from bookmarks.models import FaviconCache, User
from bookmarks.views import bookmarks as bookmark_views


class FaviconImageViewRegressionTest(TestCase):
    """favicon_image 视图兜底行为的回归测试。

    覆盖生产镜像故障（docker/default.Dockerfile 在 collectstatic 后删除
    bookmarks/static、prod settings 无 STATICFILES_DIRS，导致 find('favicon.svg')
    返回 None 时视图 404、浏览器显示损坏图标）及损坏文件被当真实图标输出的隐患。
    """

    def setUp(self):
        self.client = Client()
        # 清模块级缓存，保证每个用例独立解析兜底路径
        bookmark_views._FALLBACK_FAVICON_CACHE = None

    def tearDown(self):
        bookmark_views._FALLBACK_FAVICON_CACHE = None

    @mock.patch("django.contrib.staticfiles.finders.find", return_value=None)
    def test_fallback_svg_served_when_static_finder_returns_none(self, mock_find):
        """回归：生产镜像（find() 找不到兜底文件）下应回退 STATIC_ROOT 等位置返回 200 SVG，而非 404。"""
        resp = self.client.get("/favicon/example.org")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/svg+xml")
        self.assertEqual(resp.headers["Cache-Control"], "no-cache")
        mock_find.assert_called()

    @mock.patch(
        "django.contrib.staticfiles.finders.find",
        return_value=str(Path(settings.BASE_DIR) / "bookmarks" / "static" / "favicon.svg"),
    )
    def test_fallback_svg_served_without_db_record(self, mock_find):
        """无 FaviconCache 记录时返回兜底 SVG（开发环境 find() 命中路径，与生产回退路径互补）。"""
        resp = self.client.get("/favicon/example.org")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/svg+xml")
        mock_find.assert_called()

    def test_missing_status_with_empty_file_serves_fallback(self):
        """status=missing 且 favicon_file 为空 → 兜底 SVG（生产缺失域的真实形态）。"""
        FaviconCache.objects.create(
            domain="example.org",
            status=FaviconCache.STATUS_MISSING,
            retry_count=5,
        )
        resp = self.client.get("/favicon/example.org")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/svg+xml")

    def test_corrupt_file_is_not_served_as_real_icon(self):
        """回归：favicon_file 指向存在但内容损坏的文件时，不应把损坏内容当真实图标输出，应回退兜底。"""
        with tempfile.TemporaryDirectory() as tmp, override_settings(LD_FAVICON_FOLDER=tmp):
            corrupt = Path(tmp) / "corrupt_domain.png"
            corrupt.write_bytes(b"this is not an image")
            FaviconCache.objects.create(
                domain="corrupt.example.com",
                status=FaviconCache.STATUS_SUCCESS,
                favicon_file=corrupt.name,
            )
            resp = self.client.get("/favicon/corrupt.example.com")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["Content-Type"], "image/svg+xml")

    def test_conditional_request_304_with_bare_and_quoted_etag(self):
        """ETag 条件请求：响应为规范 quoted-string，If-None-Match 裸值与引号形式都应命中 304。"""
        resp1 = self.client.get("/favicon/etag304.example.com")
        self.assertEqual(resp1.status_code, 200)
        etag = resp1.headers["ETag"]
        # 响应 ETag 应为 quoted-string，且内部 hex 可提取
        self.assertTrue(etag.startswith('"') and etag.endswith('"'))
        bare = etag.strip('"')
        self.assertEqual(
            self.client.get("/favicon/etag304.example.com", HTTP_IF_NONE_MATCH=bare).status_code,
            304,
        )
        self.assertEqual(
            self.client.get("/favicon/etag304.example.com", HTTP_IF_NONE_MATCH=etag).status_code,
            304,
        )

    @mock.patch("bookmarks.services.tasks._enqueue_favicon_task")
    def test_missing_domain_creates_pending_and_enqueues(self, mock_enqueue):
        """认证用户请求未缓存域名：应创建 PENDING 记录并入队抓取任务（should_fetch 路径）。

        enable_favicons 默认 True；UserProfile 随 User 创建由 post_save signal 自动生成。
        """
        user = User.objects.create_user("favicon-pending-user", "fp@example.com", "pass")
        self.client.force_login(user)

        resp = self.client.get("/favicon/pending-test.example.com")

        self.assertEqual(resp.status_code, 200)
        cache = FaviconCache.objects.get(domain="pending-test.example.com")
        self.assertEqual(cache.status, FaviconCache.STATUS_PENDING)
        mock_enqueue.assert_called_once_with(user.id, "pending-test.example.com")

    def test_path_traversal_favicon_file_is_not_served(self):
        """回归：favicon_file 含 ../ 指向 LD_FAVICON_FOLDER 之外的文件时，不得按真实图标输出。"""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "favicons"
            folder.mkdir()
            # LD_FAVICON_FOLDER 之外放置一个合法 PNG，favicon_file 用 ../ 指向它
            outside = Path(tmp) / "outside.png"
            outside.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 40)
            with override_settings(LD_FAVICON_FOLDER=str(folder)):
                FaviconCache.objects.create(
                    domain="traversal.example.com",
                    status=FaviconCache.STATUS_SUCCESS,
                    favicon_file="../outside.png",
                )
                resp = self.client.get("/favicon/traversal.example.com")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.headers["Content-Type"], "image/svg+xml")

    def test_valid_file_is_served_as_real_icon(self):
        """正常图片文件仍按真实图标输出（不被内容校验误伤）。"""
        with tempfile.TemporaryDirectory() as tmp, override_settings(LD_FAVICON_FOLDER=tmp):
            png = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 40
            icon = Path(tmp) / "valid_domain.png"
            icon.write_bytes(png)
            FaviconCache.objects.create(
                domain="valid.example.com",
                status=FaviconCache.STATUS_SUCCESS,
                favicon_file=icon.name,
            )
            resp = self.client.get("/favicon/valid.example.com")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.headers["Content-Type"], "image/png")
            # ETag 指纹分支（含文件大小/mtime）应正常产出
            self.assertTrue(resp.headers.get("ETag"))
