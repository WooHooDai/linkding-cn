import logging
import time

from django.conf import settings
from django.contrib.auth.middleware import RemoteUserMiddleware
from django.db import connections
from django.utils import translation

from bookmarks.models import GlobalSettings, UserProfile

logger = logging.getLogger(__name__)


class CustomRemoteUserMiddleware(RemoteUserMiddleware):
    header = settings.LD_AUTH_PROXY_USERNAME_HEADER


class UserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            profile = getattr(user, "profile", None)
            language = getattr(profile, "language", None)
            if language:
                translation.activate(language)
                request.LANGUAGE_CODE = translation.get_language()

        response = self.get_response(request)

        return response


default_global_settings = GlobalSettings()

# Cookie names for anonymous user preferences (stored client-side)
PREF_COOKIE_DOMAIN_VIEW_MODE = "ld_domain_view_mode"
PREF_COOKIE_DOMAIN_COMPACT_MODE = "ld_domain_compact_mode"
PREF_COOKIE_TAG_GROUPING = "ld_tag_grouping"


def _build_anonymous_profile(request) -> UserProfile:
    """Build a UserProfile for anonymous users, applying cookie-stored preferences."""
    profile = UserProfile()
    profile.enable_favicons = True

    domain_view_mode = request.COOKIES.get(
        PREF_COOKIE_DOMAIN_VIEW_MODE, UserProfile.DOMAIN_VIEW_ICON
    )
    if domain_view_mode in dict(UserProfile.DOMAIN_VIEW_CHOICES):
        profile.domain_view_mode = domain_view_mode

    compact_val = request.COOKIES.get(PREF_COOKIE_DOMAIN_COMPACT_MODE, "1")
    profile.domain_compact_mode = compact_val == "1"

    tag_grouping = request.COOKIES.get(
        PREF_COOKIE_TAG_GROUPING, UserProfile.TAG_GROUPING_ALPHABETICAL
    )
    if tag_grouping in dict(UserProfile.TAG_GROUPING_CHOICES):
        profile.tag_grouping = tag_grouping

    return profile


class LinkdingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # add global settings to request
        try:
            global_settings = GlobalSettings.get()
        except Exception:
            global_settings = default_global_settings
        request.global_settings = global_settings

        # add user profile to request
        if request.user.is_authenticated:
            request.user_profile = request.user.profile
        else:
            if global_settings.guest_profile_user:
                request.user_profile = global_settings.guest_profile_user.profile
            else:
                request.user_profile = _build_anonymous_profile(request)

        response = self.get_response(request)

        return response


class TransactionGuardMiddleware:
    """事务泄漏守卫（防 SQLite WAL 陈旧快照）。

    正常请求结束时连接应处于 autocommit 且无未闭合的 atomic 块。
    若请求结束后连接仍停留在事务内（in_atomic_block 或手动事务模式），
    该连接会在后续请求中读到被钉住的旧快照（linkding 生产实测故障）。

    守卫动作：记录告警日志（探针），并关闭连接（回滚未提交事务、重建连接）。
    仅对真实泄漏生效；测试框架的外层事务（atomic_blocks 中 _from_testcase=True）
    不会被误伤。

    前提与边界：
    - 假设未开启 ATOMIC_REQUESTS（请求结束连接应已 autocommit）。若启用
      ATOMIC_REQUESTS，每次请求都会停留在事务内，守卫会误报并关闭连接；
      此时应设置 LD_TRANSACTION_GUARD_ENABLED=False 关闭守卫。
    - 仅覆盖视图返回前的阶段：若响应为流式（如 FileResponse）且事务泄漏发生在
      流 body 迭代期间，守卫检测不到——约定"流内不开启事务"。
    - 判定依赖 Django 私有 API（atomic_blocks / Atomic._from_testcase），
      已用 getattr 兜底；Django 升级时需回归验证。
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # 探针告警限流：同一 (method, path, alias) 60 秒内至多一条
        self._last_warn_at: dict[str, float] = {}

    def __call__(self, request):
        if not getattr(settings, "LD_TRANSACTION_GUARD_ENABLED", True):
            return self.get_response(request)
        try:
            response = self.get_response(request)
        except Exception:
            # 异常路径同样可能残留事务，先清理再向上抛
            self._safe_guard(request)
            raise
        self._guard(request)
        return response

    def _safe_guard(self, request) -> None:
        """异常路径下的清理：守卫自身失败不得掩盖原始异常。"""
        try:
            self._guard(request)
        except Exception:
            logger.exception(
                "TransactionGuard 清理失败（原始异常将重新抛出）：%s %s",
                request.method,
                request.path,
            )

    @staticmethod
    def _leaked(conn) -> bool:
        # 连接未打开则跳过（读取无副作用，不会触发连接建立）
        if conn.connection is None:
            return False
        if getattr(conn, "atomic_blocks", []):
            # 只允许测试框架的外层事务块残留；出现任何非测试块即泄漏
            return any(not getattr(b, "_from_testcase", False) for b in conn.atomic_blocks)
        # 无 atomic 块时要求 autocommit 开启
        return not conn.get_autocommit()

    def _guard(self, request) -> None:
        for conn in connections.all():
            if not self._leaked(conn):
                continue
            # 告警限流：防泄漏路径刷日志
            key = f"{request.method} {request.path} {conn.alias}"
            now = time.monotonic()
            if now - self._last_warn_at.get(key, 0.0) >= 60.0:
                self._last_warn_at[key] = now
                logger.warning(
                    "请求结束检测到残留事务（探针命中）：%s %s -> 关闭连接 %s "
                    "(in_atomic_block=%s, autocommit=%s)",
                    request.method,
                    request.path,
                    conn.alias,
                    conn.in_atomic_block,
                    conn.get_autocommit(),
                )
            conn.close()
