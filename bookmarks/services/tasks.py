"""
异步任务模块

集中管理所有 Huey 异步任务，按功能分为以下几大类：
  1. 通用任务工具（优先级、队列管理）
  2. Web Archive（Wayback Machine）快照
  3. Favicon 加载与刷新
  4. 预览图加载
  5. 元数据补全与刷新
  6. HTML 快照生成（含域级冷却调度器）
  7. 文章提取（阅读模式，defuddle 解析）
"""

import functools
import gzip
import logging
import os
import random
import threading
import time
from collections.abc import Callable
from datetime import timedelta

import waybackpy
from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import HUEY as huey
from huey.exceptions import RetryTask, TaskLockedException
from waybackpy.exceptions import TooManyRequestsError, WaybackError

from bookmarks.models import Bookmark, BookmarkAsset, UserProfile
from bookmarks.services import assets, favicon_loader, preview_image_loader
from bookmarks.services.website_loader import load_website_metadata
from bookmarks.utils import (
    get_registrable_domain,
    normalize_url,
    parse_domain_roots,
)

logger = logging.getLogger(__name__)
HTML_SNAPSHOT_DISPATCHER_LOCK = huey.lock_task("html-snapshot-dispatcher-lock")
BACKGROUND_SERIAL_LOCK = huey.lock_task("background-serial-lock")

HTML_SNAPSHOT_DISPATCHER_HEARTBEAT_KEY = "html-snapshot-dispatcher-heartbeat"
HTML_SNAPSHOT_DISPATCHER_HEARTBEAT_MIN_TIMEOUT_SEC = 120

PRIORITY_READING = 100
PRIORITY_MANUAL_SNAPSHOT = 90
PRIORITY_NEW_BOOKMARK = 80
PRIORITY_CORE = 60
PRIORITY_SUBSCRIPTION = 40
PRIORITY_FAVICON = 20
PRIORITY_PREVIEW = 10

NON_URGENT_MAX_CONCURRENCY = 3
_non_urgent_slots = threading.BoundedSemaphore(NON_URGENT_MAX_CONCURRENCY)

# SingleFile does not support running multiple snapshot captures in parallel.
# Temporary snapshots (adapter snapshot.enabled=false) bypass the dispatcher,
# so serialize in-process create_snapshot calls with this lock.
_TEMPORARY_SNAPSHOT_LOCK = threading.RLock()


def acquire_non_urgent_slot() -> bool:
    return _non_urgent_slots.acquire(blocking=False)


def release_non_urgent_slot():
    _non_urgent_slots.release()


PREVIEW_IMAGE_MAX_RETRIES = 3
PREVIEW_IMAGE_RETRY_DELAYS = [60, 240, 960]
READER_SNAPSHOT_WAIT_TIMEOUT = 60


# ---------------------------------------------------------------------------
# 通用任务工具
# ---------------------------------------------------------------------------

# 自定义 Huey 任务装饰器，实现指数退避重试策略
# 参考: https://huey.readthedocs.io/en/latest/guide.html#tips-and-tricks
# 退避序列: 60 → 240 → 960 → 3840 → 15360 秒


def task(retries=5, retry_delay=15, retry_backoff=4, priority=0):
    def deco(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            task = kwargs.pop("task", None)
            acquired_slot = False
            try:
                if task is not None and task.priority < PRIORITY_READING:
                    if not acquire_non_urgent_slot():
                        raise RetryTask(delay=random.uniform(1, 3)) from None
                    acquired_slot = True
                return fn(*args, **kwargs)
            except TaskLockedException as exc:
                # Task locks are currently only used as workaround to enforce
                # running specific types of tasks (e.g. singlefile snapshots)
                # sequentially. In that case don't reduce the number of retries.
                if task is not None:
                    task.retries = retries
                raise exc
            except Exception as exc:
                if task is not None:
                    task.retry_delay *= retry_backoff
                raise exc
            finally:
                if acquired_slot:
                    release_non_urgent_slot()

        return huey.task(
            retries=retries,
            retry_delay=retry_delay,
            context=True,
            priority=priority,
        )(inner)

    return deco


def _bookmark_username(bookmark: Bookmark) -> str:
    return bookmark.owner.username if bookmark and bookmark.owner else ""


# ---------------------------------------------------------------------------
# Web Archive（Wayback Machine）快照
# ---------------------------------------------------------------------------


def is_web_archive_integration_active(user: User) -> bool:
    background_tasks_enabled = not settings.LD_DISABLE_BACKGROUND_TASKS
    web_archive_integration_enabled = (
        user.profile.web_archive_integration
        == UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
    )

    return background_tasks_enabled and web_archive_integration_enabled


def create_web_archive_snapshot(
    user: User,
    bookmark: Bookmark,
    force_update: bool,
    priority: int | None = None,
):
    if is_web_archive_integration_active(user):
        _create_web_archive_snapshot_task(bookmark.id, force_update, priority=priority)


def _create_wayback_snapshot(bookmark: Bookmark):
    logger.info("Create new snapshot for bookmark. url=%s...", bookmark.url)
    archive = waybackpy.WaybackMachineSaveAPI(
        bookmark.url, settings.LD_DEFAULT_USER_AGENT, max_tries=1
    )
    archive.save()
    bookmark.web_archive_snapshot_url = archive.archive_url
    bookmark.save(update_fields=["web_archive_snapshot_url"])
    logger.info("Successfully created new snapshot for bookmark:. url=%s", bookmark.url)


@task(priority=PRIORITY_CORE)
def _create_web_archive_snapshot_task(bookmark_id: int, force_update: bool):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    # Skip if snapshot exists and update is not explicitly requested
    if bookmark.web_archive_snapshot_url and not force_update:
        return

    # Create new snapshot
    try:
        _create_wayback_snapshot(bookmark)
        return
    except TooManyRequestsError:
        logger.error(
            f"Failed to create snapshot due to rate limiting. url={bookmark.url}"
        )
    except WaybackError as error:
        logger.error(
            f"Failed to create snapshot. url={bookmark.url}",
            exc_info=error,
        )


# ---------------------------------------------------------------------------
# Favicon 加载与刷新（域名级）
# ---------------------------------------------------------------------------


def is_favicon_feature_active(user: User) -> bool:
    background_tasks_enabled = not settings.LD_DISABLE_BACKGROUND_TASKS
    return background_tasks_enabled and user.profile.enable_favicons


def _resolve_domain(url: str, domain_config=None) -> str:
    """从 URL 提取 hostname 并应用自定义域名归一化。"""
    from bookmarks.utils import extract_hostname, resolve_favicon_domain

    hostname = extract_hostname(url)
    if not hostname:
        return ""
    return resolve_favicon_domain(hostname, config=domain_config)


def ensure_favicon(user: User, url: str, priority: int | None = None):
    """确保指定 URL 的域名有 favicon。

    策略：
    - 磁盘有文件 → 同步 DB 记录
    - 磁盘无文件但 DB 有缓存 → 按状态处理（pending 等待/failed 到期重试/missing 不重试）
    - 无任何缓存 → 入队获取任务

    """
    if not is_favicon_feature_active(user):
        return

    domain_config = parse_domain_roots(user.profile.custom_domain_root)
    domain = _resolve_domain(url, domain_config)
    if not domain:
        return

    from bookmarks.models import FaviconCache

    # 1. 先查 DB（轻量，避免不必要的 os.listdir）
    cache = FaviconCache.objects.filter(domain=domain).first()

    if (
        cache
        and cache.status == FaviconCache.STATUS_SUCCESS
        and cache.favicon_file
        and favicon_loader.get_favicon_path(cache.favicon_file).is_file()
    ):
        return
    # 磁盘文件丢失 → 继续到步骤 2 重新获取

    # 2. 磁盘扫描（仅在 DB 无有效记录时执行，支持旧命名迁移和损坏文件清理）
    cached_file = favicon_loader.find_cached_favicon_file(domain)
    if cached_file:
        if cache:
            cache.favicon_file = cached_file
            cache.status = FaviconCache.STATUS_SUCCESS
            if not cache.fetched_at:
                cache.fetched_at = timezone.now()
            cache.save(update_fields=["favicon_file", "status", "fetched_at"])
        else:
            FaviconCache.objects.create(
                domain=domain,
                favicon_file=cached_file,
                status=FaviconCache.STATUS_SUCCESS,
                fetched_at=timezone.now(),
            )
        return

    # 3. 无磁盘文件 → 按 DB 状态处理
    if not cache:
        FaviconCache.objects.create(domain=domain, status=FaviconCache.STATUS_PENDING)
        _enqueue_favicon_task(user.id, domain, priority=priority)
        return

    if cache.status == FaviconCache.STATUS_PENDING:
        return

    if cache.status == FaviconCache.STATUS_FAILED:
        if cache.next_retry_at and cache.next_retry_at <= timezone.now():
            _enqueue_favicon_task(user.id, domain, priority=priority)
        return

    if cache.status == FaviconCache.STATUS_MISSING:
        # MISSING 状态下，如果 next_retry_at 已过期，允许重试
        if cache.next_retry_at and cache.next_retry_at <= timezone.now():
            _enqueue_favicon_task(user.id, domain, priority=priority)
        return

    # STATUS_SUCCESS 但文件丢失（已在步骤 1 处理，此处兜底）
    _enqueue_favicon_task(user.id, domain, priority=priority)


def refresh_favicon_for_url(user: User, url: str, priority: int | None = None):
    """强制刷新指定 URL 的域名 favicon（替代原来的 refresh_favicon(bookmark)）。"""
    if not is_favicon_feature_active(user):
        return
    domain_config = parse_domain_roots(user.profile.custom_domain_root)
    domain = _resolve_domain(url, domain_config)
    if domain and _set_favicon_pending_for_enqueue(domain, force=True):
        _enqueue_favicon_task(user.id, domain, priority=priority)


def refresh_favicons_for_bookmarks(user: User, bookmarks) -> int:
    """批量强制刷新书签所属域名的 favicon，按域名去重。返回入队数量。"""
    if not is_favicon_feature_active(user):
        return 0

    domain_config = parse_domain_roots(user.profile.custom_domain_root)
    domains_seen = set()
    for bookmark in bookmarks:
        domain = _resolve_domain(bookmark.url, domain_config)
        if (
            domain
            and domain not in domains_seen
            and _set_favicon_pending_for_enqueue(domain, force=True)
        ):
            domains_seen.add(domain)
            _enqueue_favicon_task(user.id, domain)

    return len(domains_seen)


def load_favicon(
    user: User,
    bookmark: Bookmark,
    domain_config=None,
    priority: int | None = None,
):
    """兼容旧接口：书签创建/更新时调用。"""
    ensure_favicon(user, bookmark.url, priority=priority)


def refresh_favicon(user: User, bookmark: Bookmark, priority: int | None = None):
    """兼容旧接口：强制刷新书签的 favicon。"""
    refresh_favicon_for_url(user, bookmark.url, priority=priority)


def _enqueue_favicon_task(user_id: int, domain: str, priority: int | None = None):
    """入队 favicon 获取任务。

    入队前检查分布式锁，避免同一域名在短时间内被反复入队，
    防止因并发请求导致任务队列被 favicon 任务灌满。
    任务内部仍保留分布式锁，保证同一域名同时只有一个任务在执行。
    """
    from django.core.cache import cache as django_cache

    lock_key = f"favicon_task_lock:{domain}"
    if django_cache.get(lock_key):
        logger.debug(
            f"Skipping favicon enqueue for domain={domain}, task already running or queued"
        )
        return

    _fetch_domain_favicon_task(user_id, domain, priority=priority)


def _set_favicon_pending_for_enqueue(domain: str, force: bool = False) -> bool:
    """把域名标记为 PENDING，作为跨进程入队去重标记。

    批量任务用 DB 状态而不是进程内缓存判断，因为 web 和 huey 进程默认
    使用各自独立的 LocMemCache，缓存锁无法在进程间生效。
    """
    from bookmarks.models import FaviconCache

    now = timezone.now()
    cache = FaviconCache.objects.filter(domain=domain).first()

    if cache is None:
        try:
            FaviconCache.objects.create(
                domain=domain, status=FaviconCache.STATUS_PENDING
            )
        except IntegrityError:
            return False
        return True

    if cache.status == FaviconCache.STATUS_PENDING:
        return False

    if not force and cache.status == FaviconCache.STATUS_SUCCESS:
        return False

    if (
        not force
        and cache.status in (FaviconCache.STATUS_FAILED, FaviconCache.STATUS_MISSING)
        and cache.next_retry_at
        and cache.next_retry_at > now
    ):
        return False

    updated = (
        FaviconCache.objects.filter(id=cache.id)
        .exclude(status=FaviconCache.STATUS_PENDING)
        .update(status=FaviconCache.STATUS_PENDING, next_retry_at=None)
    )
    return bool(updated)


@task(retries=0, priority=PRIORITY_FAVICON)
def _fetch_domain_favicon_task(user_id: int, domain: str):
    """per-domain 的 favicon 获取任务。

    全局锁保证同时只有一个 favicon 抓取任务在执行；
    锁被占用时延迟重排，不消耗 Huey 重试次数。
    """
    try:
        with BACKGROUND_SERIAL_LOCK:
            return _fetch_domain_favicon_task_unlocked(user_id, domain)
    except TaskLockedException:
        logger.debug(
            "Skipping favicon fetch for domain=%s, global favicon lock is busy", domain
        )
        raise RetryTask(delay=random.uniform(5, 15)) from None


def _fetch_domain_favicon_task_unlocked(user_id: int, domain: str):
    """执行 favicon 抓取并更新 FaviconCache。

    成功后更新 FaviconCache；失败时更新重试计数和下次重试时间。
    """
    from django.core.cache import cache as django_cache

    from bookmarks.models import FaviconCache

    # 分布式锁：任务执行时才加锁（180s 超时覆盖所有 provider 尝试）
    lock_key = f"favicon_task_lock:{domain}"
    if not django_cache.add(lock_key, "1", timeout=180):
        logger.debug(
            "Skipping favicon fetch for domain=%s, another task is running", domain
        )
        return

    try:
        cache, _ = FaviconCache.objects.get_or_create(
            domain=domain,
            defaults={"status": FaviconCache.STATUS_PENDING},
        )

        # MISSING 状态的重试间隔序列（天），之后以最后值（7天）为间隔无限重试
        MISSING_RETRY_DELAYS = [1, 1, 1, 2, 2, 3, 3, 4, 5, 6, 7]

        logger.info("Fetching favicon for domain=%s", domain)

        favicon_file = favicon_loader.fetch_and_save_favicon(domain)

        RETRY_DELAYS = FaviconCache.RETRY_DELAYS
        MAX_RETRIES = len(RETRY_DELAYS)

        if favicon_file:
            cache.favicon_file = favicon_file
            cache.status = FaviconCache.STATUS_SUCCESS
            cache.fetched_at = timezone.now()
            cache.retry_count = 0
            cache.next_retry_at = None
            cache.save()
            # 旧变体清理已移至 find_cached_favicon_file（读取时触发），
            # 避免多 worker 并发写入时 _remove_existing_variants 竞态
        else:
            # MISSING 状态下的重试失败
            if cache.status == FaviconCache.STATUS_MISSING:
                cache.retry_count += 1
                idx = min(cache.retry_count, len(MISSING_RETRY_DELAYS) - 1)
                delay_days = MISSING_RETRY_DELAYS[idx]
                cache.next_retry_at = timezone.now() + timedelta(days=delay_days)
                logger.info(
                    "Favicon still missing for domain=%s, will retry in %s day(s) (attempt %s)",
                    domain,
                    delay_days,
                    cache.retry_count,
                )
                cache.save()
                return
            cache.retry_count += 1
            if cache.retry_count >= MAX_RETRIES:
                cache.status = FaviconCache.STATUS_MISSING
                # 保留旧的 favicon_file，不清空（过期图标仍可使用）
                cache.retry_count = 0
                cache.next_retry_at = timezone.now() + timedelta(
                    days=MISSING_RETRY_DELAYS[0]
                )
                logger.info(
                    "Favicon not found for domain=%s after %s retries, marking as missing (will retry in %s day(s))",
                    domain,
                    MAX_RETRIES,
                    MISSING_RETRY_DELAYS[0],
                )
            else:
                cache.status = FaviconCache.STATUS_FAILED
                delay_seconds = RETRY_DELAYS[cache.retry_count - 1]
                cache.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
                logger.info(
                    "Favicon fetch failed for domain=%s, retry #%s in %ss",
                    domain,
                    cache.retry_count,
                    delay_seconds,
                )
            cache.save()
    finally:
        django_cache.delete(lock_key)


def schedule_bookmarks_without_favicons(user: User):
    """为用户所有缺少 favicon 的书签入队获取任务（去重到域名级）。"""
    if not is_favicon_feature_active(user):
        return
    _batch_load_favicons_task(user.id)


@task(priority=PRIORITY_FAVICON)
def _batch_load_favicons_task(user_id: int):
    from bookmarks.models import FaviconCache

    user = User.objects.get(id=user_id)
    domain_config = parse_domain_roots(user.profile.custom_domain_root)

    # 预扫描磁盘目录（一次 I/O），避免重复 os.listdir
    disk_scan = favicon_loader._scan_favicon_folder()

    # 收集所有唯一域名，逐个检查是否已有成功缓存（exists 查询走索引，不加载全量到内存）
    raw_urls = (
        Bookmark.objects.filter(owner=user, is_deleted=False)
        .values_list("url", flat=True)
        .iterator()
    )
    domains_to_fetch = set()
    for url in raw_urls:
        domain = _resolve_domain(url, domain_config)
        if not domain or domain in domains_to_fetch:
            continue
        if FaviconCache.objects.filter(
            domain=domain, status=FaviconCache.STATUS_SUCCESS
        ).exists():
            continue

        # 从预扫描结果中查找（无额外磁盘 I/O）
        cached_file = favicon_loader._find_cached_favicon_file_from_scan(
            domain, disk_scan
        )
        if cached_file:
            FaviconCache.objects.update_or_create(
                domain=domain,
                defaults={
                    "favicon_file": cached_file,
                    "status": FaviconCache.STATUS_SUCCESS,
                    "fetched_at": timezone.now(),
                    "retry_count": 0,
                    "next_retry_at": None,
                },
            )
            logger.debug(f"Synced manually placed favicon for {domain}: {cached_file}")
        else:
            if _set_favicon_pending_for_enqueue(domain):
                domains_to_fetch.add(domain)

    # 为缺少 favicon 的域名入队
    for domain in domains_to_fetch:
        _enqueue_favicon_task(user.id, domain)

    logger.info("Queued favicon tasks for %s unique domains", len(domains_to_fetch))


def schedule_refresh_favicons(user: User):
    """手动触发：刷新该用户所有域名的 favicon。"""
    if not is_favicon_feature_active(user) or not settings.LD_ENABLE_REFRESH_FAVICONS:
        return
    _batch_refresh_favicons_task(user.id)


@task(priority=PRIORITY_FAVICON)
def _batch_refresh_favicons_task(user_id: int):
    """刷新该用户书签涉及的所有域名的 favicon。"""
    user = User.objects.get(id=user_id)
    domain_config = parse_domain_roots(user.profile.custom_domain_root)

    domains_seen = set()
    for bm in (
        Bookmark.objects.filter(owner=user, is_deleted=False).values("url").iterator()
    ):
        domain = _resolve_domain(bm["url"], domain_config)
        if (
            domain
            and domain not in domains_seen
            and _set_favicon_pending_for_enqueue(domain, force=True)
        ):
            domains_seen.add(domain)
            _enqueue_favicon_task(user.id, domain)

    logger.info("Refreshed favicons for %s unique domains", len(domains_seen))


def rename_favicon_for_domain_config(user, old_config_str: str, new_config_str: str):
    """自定义域名规则变更后，无需操作。

    FaviconCache 是全局的，Bookmark.favicon_file 已移除。
    规则变更只是改变了查询 key，渲染时自动使用新规则查表。
    """


# ---------------------------------------------------------------------------
# Favicon 定时刷新（periodic task）
# ---------------------------------------------------------------------------


def _parse_cron_schedule(cron_str: str) -> dict | None:
    """解析五字段 cron 表达式为 huey crontab 的 kwargs。

    标准 cron 格式：分钟 小时 日 月 星期
    空字符串或 "off" 表示禁用定时刷新。
    解析失败时回退到默认值 "0 0 */7 * *"。
    """
    if not cron_str or cron_str.strip().lower() == "off":
        return None
    fields = cron_str.strip().split()
    if len(fields) != 5:
        cron_str = "0 0 */7 * *"
        fields = cron_str.split()
    return {
        "minute": fields[0],
        "hour": fields[1],
        "day": fields[2],
        "month": fields[3],
        "day_of_week": fields[4],
    }


def _cron_interval_seconds(schedule: dict) -> int:
    """从 cron schedule 估算最小间隔（秒），用于 staleness 过滤。

    day_of_week 非 * → 周期间隔（7 天）
    day 为 */N → N 天
    day 为 * → 每天
    day 为逗号分隔日期 → 计算最小间隔天数
    其他 → 默认 7 天
    """
    day = schedule.get("day", "*")
    dow = schedule.get("day_of_week", "*")

    if dow != "*":
        return 7 * 86400

    if day.startswith("*/"):
        try:
            n = int(day[2:])
            if n > 0:
                return n * 86400
        except ValueError:
            pass

    if day == "*":
        return 86400

    if "," in day:
        try:
            days = sorted(int(d.strip()) for d in day.split(",") if d.strip())
            if len(days) >= 2:
                gaps = [days[i + 1] - days[i] for i in range(len(days) - 1)]
                wrap = days[0] + 31 - days[-1]
                return min(gaps + [wrap]) * 86400
        except (ValueError, IndexError):
            pass

    return 7 * 86400


_favicon_refresh_schedule = _parse_cron_schedule(settings.LD_FAVICON_REFRESH_SCHEDULE)
if _favicon_refresh_schedule:

    @huey.periodic_task(crontab(**_favicon_refresh_schedule))
    def _scheduled_favicon_refresh_task():
        """定时刷新超过间隔天数的 favicon（基于 cron 估算的间隔）。

        遍历 FaviconCache 中所有 SUCCESS 状态的域名，
        入队重新获取任务。已存在且最新的 favicon 不会被覆盖（stale-while-revalidate）。
        运行时检查 LD_ENABLE_REFRESH_FAVICONS 和 LD_DISABLE_BACKGROUND_TASKS。
        """
        if not settings.LD_ENABLE_REFRESH_FAVICONS:
            return
        if settings.LD_DISABLE_BACKGROUND_TASKS:
            return

        # 检查是否有任何用户启用了 favicon，如果没有则跳过刷新
        from bookmarks.models import FaviconCache, UserProfile

        if not UserProfile.objects.filter(enable_favicons=True).exists():
            logger.debug("No users with favicons enabled, skipping scheduled refresh")
            return

        interval = _cron_interval_seconds(_favicon_refresh_schedule)
        stale_threshold = timezone.now() - timedelta(seconds=interval)

        domains = list(
            FaviconCache.objects.filter(
                status=FaviconCache.STATUS_SUCCESS,
                fetched_at__lt=stale_threshold,
            )
            .exclude(favicon_file="")
            .values_list("domain", flat=True)
        )
        for domain in domains:
            _fetch_domain_favicon_task(0, domain)

        logger.info(
            "Scheduled favicon refresh: enqueued %d domains (stale > %d days)",
            len(domains),
            interval // 86400,
        )
# ---------------------------------------------------------------------------
# 预览图加载
# ---------------------------------------------------------------------------


def is_preview_feature_active(user: User) -> bool:
    return (
        user.profile.enable_preview_images and not settings.LD_DISABLE_BACKGROUND_TASKS
    )


def _preview_image_should_skip(bookmark: Bookmark) -> bool:
    """判断预览图是否处于无需自动重试的状态。"""
    if bookmark.preview_image_file:
        return True
    if bookmark.preview_image_retry_count >= PREVIEW_IMAGE_MAX_RETRIES:
        return True
    return bool(
        bookmark.preview_image_next_retry_at
        and bookmark.preview_image_next_retry_at > timezone.now()
    )


def load_preview_image(
    user: User,
    bookmark: Bookmark,
    priority: int | None = None,
    force: bool = False,
):
    if not is_preview_feature_active(user):
        return
    if not force and _preview_image_should_skip(bookmark):
        return
    if force:
        bookmark.preview_image_retry_count = 0
        bookmark.preview_image_next_retry_at = None
        bookmark.save(
            update_fields=["preview_image_retry_count", "preview_image_next_retry_at"]
        )
    _load_preview_image_task(bookmark.id, force=force, priority=priority)


@task(priority=PRIORITY_PREVIEW)
def delete_preview_image_temp_file(filepath: str):
    logger.debug(
        f"Followed temporary preview image file will be deleted after a while: {filepath}"
    )
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info("Deleted temporary preview image file: %s", filepath)


@task(priority=PRIORITY_PREVIEW)
def _load_preview_image_task(bookmark_id: int, force: bool = False):
    try:
        with BACKGROUND_SERIAL_LOCK:
            return _load_preview_image_task_unlocked(bookmark_id, force)
    except TaskLockedException:
        logger.debug(
            "Skipping preview image for bookmark_id=%s, background serial lock is busy",
            bookmark_id,
        )
        raise RetryTask(delay=random.uniform(1, 3)) from None


def _load_preview_image_task_unlocked(bookmark_id: int, force: bool = False):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info("Load preview image for bookmark. url=%s", bookmark.url)

    try:
        new_preview_image_file = preview_image_loader.load_preview_image(
            bookmark.url, bookmark, force=force
        )
    except Exception:
        logger.exception("Failed to load preview image. bookmark_id=%s", bookmark_id)
        new_preview_image_file = None

    if new_preview_image_file:
        bookmark.preview_image_file = new_preview_image_file
        bookmark.preview_image_retry_count = 0
        bookmark.preview_image_next_retry_at = None
        bookmark.save(
            update_fields=[
                "preview_image_file",
                "preview_image_retry_count",
                "preview_image_next_retry_at",
            ]
        )
        logger.info(
            f"Successfully updated preview image for bookmark. url={bookmark.url} preview_image_file={new_preview_image_file}"
        )
        return

    next_count = bookmark.preview_image_retry_count + 1
    if next_count < PREVIEW_IMAGE_MAX_RETRIES:
        delay = PREVIEW_IMAGE_RETRY_DELAYS[next_count - 1]
        bookmark.preview_image_retry_count = next_count
        bookmark.preview_image_next_retry_at = timezone.now() + timedelta(seconds=delay)
        bookmark.save(
            update_fields=[
                "preview_image_retry_count",
                "preview_image_next_retry_at",
            ]
        )
        raise RetryTask(delay=delay) from None

    bookmark.preview_image_retry_count = PREVIEW_IMAGE_MAX_RETRIES
    bookmark.preview_image_next_retry_at = None
    bookmark.save(
        update_fields=["preview_image_retry_count", "preview_image_next_retry_at"]
    )
    logger.info("Preview image failed permanently for bookmark. url=%s", bookmark.url)


def schedule_bookmarks_without_previews(user: User):
    if is_preview_feature_active(user):
        _batch_load_preview_images_task(user.id)


@task(priority=PRIORITY_PREVIEW)
def _batch_load_preview_images_task(user_id: int):
    user = User.objects.get(id=user_id)
    bookmarks = Bookmark.objects.filter(
        Q(preview_image_file__exact=""),
        owner=user,
    )

    # TODO: Implement bulk task creation
    for bookmark in bookmarks:
        if _preview_image_should_skip(bookmark):
            continue
        try:
            _load_preview_image_task(bookmark.id)
        except Exception as exc:
            logging.exception(exc)


# ---------------------------------------------------------------------------
# 元数据补齐与刷新
#
# 两个动作语义不同，不可互相替代：
#   - 补齐（enrich）：只填为空的字段，不覆盖已有内容
#   - 刷新（refresh）：重新抓取并覆盖，抓不到（None）时保留原值
# 两者都不生成 HTML 快照。
# ---------------------------------------------------------------------------


def schedule_metadata_enrichment(
    bookmark: Bookmark,
    overwrite: bool = False,
    ignore_cache: bool = True,
    priority: int | None = None,
):
    if not settings.LD_DISABLE_BACKGROUND_TASKS:
        _enrich_metadata_task(
            bookmark.id,
            overwrite=overwrite,
            ignore_cache=ignore_cache,
            priority=priority,
        )


def schedule_metadata_refresh(bookmark: Bookmark, priority: int | None = None):
    if not settings.LD_DISABLE_BACKGROUND_TASKS:
        _refresh_metadata_task(bookmark.id, priority=priority)


@task(retries=3, priority=PRIORITY_CORE)
def _enrich_metadata_task(
    bookmark_id: int,
    overwrite: bool = False,
    ignore_cache: bool = True,
):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info("Enrich metadata for bookmark. url=%s", bookmark.url)

    metadata = load_website_metadata(
        bookmark.url,
        ignore_cache=ignore_cache,
        username=_bookmark_username(bookmark),
    )
    update_fields = []

    if (
        (overwrite or bookmark.has_placeholder_title)
        and metadata.title is not None
        and metadata.title != bookmark.title
    ):
        bookmark.title = metadata.title
        update_fields.append("title")

    if (
        (overwrite or not bookmark.description)
        and metadata.description is not None
        and metadata.description != bookmark.description
    ):
        bookmark.description = metadata.description
        update_fields.append("description")

    if (
        (overwrite or not bookmark.author)
        and metadata.author
        and metadata.author != bookmark.author
    ):
        bookmark.author = metadata.author
        update_fields.append("author")

    if (
        (overwrite or not bookmark.preview_image_remote_url)
        and metadata.preview_image
        and metadata.preview_image != bookmark.preview_image_remote_url
    ):
        bookmark.preview_image_remote_url = metadata.preview_image
        update_fields.append("preview_image_remote_url")

    if update_fields:
        bookmark.date_modified = timezone.now()
        update_fields.append("date_modified")
        bookmark.save(update_fields=update_fields)
        logger.info("Successfully enriched metadata for bookmark. url=%s", bookmark.url)


@task(priority=PRIORITY_CORE)
def _refresh_metadata_task(bookmark_id: int):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info("Refresh metadata for bookmark. url=%s", bookmark.url)

    metadata = load_website_metadata(
        bookmark.url,
        ignore_cache=True,
        username=_bookmark_username(bookmark),
    )
    update_fields = []
    preview_image_changed = False

    if metadata.title is not None:
        bookmark.title = metadata.title
        update_fields.append("title")
    if metadata.description is not None:
        bookmark.description = metadata.description
        update_fields.append("description")
    if metadata.author:
        bookmark.author = metadata.author
        update_fields.append("author")
    if metadata.preview_image:
        preview_image_changed = (
            metadata.preview_image != bookmark.preview_image_remote_url
        )
        bookmark.preview_image_remote_url = metadata.preview_image
        update_fields.append("preview_image_remote_url")
        if preview_image_changed and bookmark.preview_image_file:
            # 让新远程图先展示，本地文件由下方下载任务完成后写回
            bookmark.preview_image_file = ""
            update_fields.append("preview_image_file")
    if metadata.url and metadata.url != bookmark.url:
        bookmark.url = metadata.url
        bookmark.url_normalized = normalize_url(bookmark.url)
        update_fields.extend(["url", "url_normalized"])

    if update_fields:
        bookmark.date_modified = timezone.now()
        update_fields.append("date_modified")
        bookmark.save(update_fields=update_fields)
        logger.info("Successfully refreshed metadata for bookmark. url=%s", bookmark.url)

    if preview_image_changed and not settings.LD_DISABLE_BACKGROUND_TASKS:
        # 远程图已换，重新下载本地预览文件
        load_preview_image(bookmark.owner, bookmark, force=True)


# ---------------------------------------------------------------------------
# HTML 快照生成（SingleFile 归档）
#
# 采用"调度器 + 冷却窗口"模式：
#   - 每次需要生成快照时，创建 STATUS_PENDING 资产并启动调度器
#   - 调度器按域名冷却间隔串行调度，避免对同一域名频繁抓取
#   - 每分钟定时兜底，确保中断后未完成的任务能被重新拾起
# ---------------------------------------------------------------------------


def is_html_snapshot_feature_active() -> bool:
    return settings.LD_ENABLE_SNAPSHOTS and not settings.LD_DISABLE_BACKGROUND_TASKS


def _trigger_html_snapshot_dispatcher(priority: int | None = None):
    _html_snapshot_dispatcher_task(priority=priority)


def _is_snapshot_disabled_by_adapter(url: str, username: str = '') -> bool:
    """Check whether the site-adapter config disables snapshots for this URL.

    Reads the 'enabled' field from the resolved snapshot section. The field
    defaults to True when absent, so this only returns True when an adapter
    explicitly sets ``snapshot.enabled: false`` for the matched domain/route.
    System-level and user-level toggles are checked separately by the callers.
    """
    try:
        from site_adapters.services.config.resolver import get_snapshot_config
        config = get_snapshot_config(url, username=username)
        if config is None:
            return False
        return config.get('enabled', True) is False
    except Exception:
        logger.debug("Failed to read snapshot enabled flag for %s", url, exc_info=True)
        return False


def _get_bookmark_username(bookmark: Bookmark) -> str:
    return bookmark.owner.username if bookmark and bookmark.owner else ""


def _get_html_snapshot_cooldown_seconds(
    randint_func: Callable[[int, int], int] | None = None,
) -> int:
    min_seconds = settings.LD_SNAPSHOT_DOMAIN_COOLDOWN_MIN_SEC
    max_seconds = settings.LD_SNAPSHOT_DOMAIN_COOLDOWN_MAX_SEC
    if max_seconds < min_seconds:
        min_seconds, max_seconds = max_seconds, min_seconds

    randint = randint_func or random.randint
    return randint(min_seconds, max_seconds)


def _get_html_snapshot_dispatcher_tick_seconds() -> int:
    return max(settings.LD_SNAPSHOT_DISPATCHER_TICK_SEC, 1)


def _select_next_html_snapshot_asset(now, next_eligible_at: dict[str, object]):
    """
    从待处理快照队列中选出下一个可执行的资产。

    优先选 date_created 最新的（LIFO），但跳过仍处于冷却期的域名。
    返回 (asset, next_wake_at)：
      - asset 不为 None  → 立即执行
      - asset 为 None     → 所有 pending 均在冷却中，next_wake_at 为最早可唤醒时间
    """
    # 所有 pending 资产（包括有重试时间的）
    all_pending = (
        BookmarkAsset.objects.filter(
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
        )
        .select_related("bookmark")
        .order_by("-scheduling_priority", "-date_created", "-id")
    )

    # 可立即执行的（无重试时间或重试时间已过）
    executable = all_pending.filter(
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)
    )

    next_wake_at = None

    # 先检查可执行的资产
    for asset in executable:
        domain = get_registrable_domain(asset.bookmark.url)
        eligible_at = next_eligible_at.get(domain)
        if eligible_at is None or eligible_at <= now:
            return asset, None
        if next_wake_at is None or eligible_at < next_wake_at:
            next_wake_at = eligible_at

    # 再检查有未来重试时间的资产，更新 next_wake_at
    waiting = all_pending.filter(next_retry_at__gt=now)
    for asset in waiting:
        if next_wake_at is None or asset.next_retry_at < next_wake_at:
            next_wake_at = asset.next_retry_at

    return None, next_wake_at


def _get_html_snapshot_dispatcher_sleep_seconds(now, next_wake_at) -> float:
    remaining_seconds = max((next_wake_at - now).total_seconds(), 0)
    if remaining_seconds == 0:
        return 0
    return min(remaining_seconds, _get_html_snapshot_dispatcher_tick_seconds())


def _html_snapshot_dispatcher_heartbeat_timeout() -> float:
    """心跳超时阈值：至少 MIN_TIMEOUT，且不小于 10 个 tick。

    循环每次 sleep 最长一个 tick，正常情况下心跳间隔远小于该阈值；
    超时只可能是持有者已经死了。
    """
    tick = _get_html_snapshot_dispatcher_tick_seconds()
    return max(float(HTML_SNAPSHOT_DISPATCHER_HEARTBEAT_MIN_TIMEOUT_SEC), tick * 10.0)


def _touch_html_snapshot_dispatcher_heartbeat():
    huey.put(HTML_SNAPSHOT_DISPATCHER_HEARTBEAT_KEY, time.time())


def _html_snapshot_dispatcher_lock_is_stale() -> bool:
    """锁还在，但写心跳的持有者已经不在了（或从未写过心跳）。"""
    last = huey.get(HTML_SNAPSHOT_DISPATCHER_HEARTBEAT_KEY, peek=True)
    if last is None:
        return True
    if isinstance(last, bytes):
        last = last.decode("utf-8", "ignore")
    try:
        return time.time() - float(last) > _html_snapshot_dispatcher_heartbeat_timeout()
    except (TypeError, ValueError):
        return True


def _run_html_snapshot_dispatcher_loop(
    now_func: Callable[[], object] | None = None,
    sleep_func: Callable[[float], None] | None = None,
    cooldown_func: Callable[[], int] | None = None,
):
    """
    调度器主循环：持续消费待处理快照，直到队列清空。

    工作逻辑：
      1. 从 pending 队列中选出下一个可执行资产（跳过冷却中的域名）
      2. 选中 → 提交任务，记录该域名的下次可用时间
      3. 无可执行资产 → 计算最早唤醒时间，sleep 等待后重试
      4. 队列完全为空 → 退出循环
    """
    now_func = now_func or timezone.now
    sleep_func = sleep_func or time.sleep
    cooldown_func = cooldown_func or _get_html_snapshot_cooldown_seconds
    next_eligible_at: dict[str, object] = {}

    while True:
        _touch_html_snapshot_dispatcher_heartbeat()
        now = now_func()
        asset, next_wake_at = _select_next_html_snapshot_asset(now, next_eligible_at)
        if asset is None:
            if next_wake_at is None:
                return
            sleep_seconds = _get_html_snapshot_dispatcher_sleep_seconds(
                now, next_wake_at
            )
            if sleep_seconds > 0:
                sleep_func(sleep_seconds)
            continue

        domain = get_registrable_domain(asset.bookmark.url)
        _create_html_snapshot_task(asset.id)
        next_eligible_at[domain] = now_func() + timedelta(seconds=cooldown_func())


@task(retries=0, retry_delay=0, priority=PRIORITY_CORE)
def _html_snapshot_dispatcher_task():
    for attempt in (1, 2):
        try:
            with HTML_SNAPSHOT_DISPATCHER_LOCK:
                _run_html_snapshot_dispatcher_loop()
                return
        except TaskLockedException:
            if attempt == 2 or not _html_snapshot_dispatcher_lock_is_stale():
                logger.debug("HTML snapshot dispatcher already running.")
                return
            logger.warning(
                "Releasing stale HTML snapshot dispatcher lock (holder heartbeat expired)."
            )
            HTML_SNAPSHOT_DISPATCHER_LOCK.clear()


def create_html_snapshot(bookmark: Bookmark, priority: int | None = None):
    if not is_html_snapshot_feature_active():
        return

    if _is_snapshot_disabled_by_adapter(bookmark.url, username=_get_bookmark_username(bookmark)):
        logger.debug("Snapshot disabled by adapter config for url=%s", bookmark.url)
        return

    asset = assets.create_snapshot_asset(bookmark)
    asset.scheduling_priority = priority or 0
    asset.save()
    _trigger_html_snapshot_dispatcher(priority=priority)


def create_html_snapshots(bookmark_list: list[Bookmark], priority: int | None = None):
    if not is_html_snapshot_feature_active():
        return

    bookmarks = list(bookmark_list)
    if not bookmarks:
        return

    # 已有排队中的快照则跳过，避免连点造成同一页面重复抓取；
    # 已有完成快照的仍会新建（"创建快照"的语义就是重新归档当前页面）。
    pending_bookmark_ids = set(
        BookmarkAsset.objects.filter(
            bookmark__in=bookmarks,
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
        ).values_list("bookmark_id", flat=True)
    )

    assets_to_create = []
    for bookmark in bookmarks:
        if bookmark.id in pending_bookmark_ids:
            logger.debug("Snapshot already pending for url=%s", bookmark.url)
            continue
        if _is_snapshot_disabled_by_adapter(bookmark.url, username=_get_bookmark_username(bookmark)):
            logger.debug("Snapshot disabled by adapter config for url=%s", bookmark.url)
            continue
        asset = assets.create_snapshot_asset(bookmark)
        asset.scheduling_priority = priority or 0
        assets_to_create.append(asset)

    if not assets_to_create:
        return

    BookmarkAsset.objects.bulk_create(assets_to_create)
    _trigger_html_snapshot_dispatcher(priority=priority)


# SingleFile does not support running multiple snapshot captures in parallel.
# Keep a periodic fallback that can re-trigger the dispatcher if pending work was
# missed due to an interrupted worker or process restart.
@huey.periodic_task(crontab(minute="*"))
def _schedule_html_snapshots_task():
    if BookmarkAsset.objects.filter(
        asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        status=BookmarkAsset.STATUS_PENDING,
    ).exists():
        _trigger_html_snapshot_dispatcher()


def _create_html_snapshot_task(asset_id: int):
    try:
        asset = BookmarkAsset.objects.get(id=asset_id)
    except BookmarkAsset.DoesNotExist:
        return

    logger.info("Create HTML snapshot for bookmark. url=%s", asset.bookmark.url)

    try:
        assets.create_snapshot(asset)

        logger.info(
            f"Successfully created HTML snapshot for bookmark. url={asset.bookmark.url}"
        )
    except Exception as error:
        # 刷新以获取 assets.create_snapshot 设置的最新状态
        asset.refresh_from_db()

        retry_delays = settings.LD_SNAPSHOT_RETRY_DELAYS
        max_retries = len(retry_delays)

        # 重试逻辑：使用配置的延迟数组
        if asset.retry_count < max_retries:
            delay_seconds = retry_delays[asset.retry_count]
            asset.retry_count += 1
            asset.next_retry_at = timezone.now() + timedelta(seconds=delay_seconds)
            asset.status = BookmarkAsset.STATUS_PENDING  # 覆盖 STATUS_FAILURE
            asset.save()
            logger.warning(
                f"Snapshot failed, will retry #{asset.retry_count} at {asset.next_retry_at}. "
                f"url={asset.bookmark.url}"
            )
        else:
            # 已达最大重试次数，保持 STATUS_FAILURE（由 assets.create_snapshot 设置）
            logger.error(
                f"Snapshot failed after {asset.retry_count} retries. "
                f"url={asset.bookmark.url}",
                exc_info=error,
            )


def create_missing_html_snapshots(user: User) -> int:
    if not is_html_snapshot_feature_active():
        return 0

    bookmarks_without_snapshots = Bookmark.objects.filter(owner=user).exclude(
        bookmarkasset__asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        bookmarkasset__status__in=[
            BookmarkAsset.STATUS_PENDING,
            BookmarkAsset.STATUS_COMPLETE,
        ],
    )
    bookmarks_without_snapshots |= Bookmark.objects.filter(owner=user).exclude(
        bookmarkasset__asset_type=BookmarkAsset.TYPE_SNAPSHOT
    )

    # Filter out bookmarks whose adapter config disables snapshots, so the
    # returned count reflects only the bookmarks actually queued.
    eligible_bookmarks = [
        bm for bm in bookmarks_without_snapshots
        if not _is_snapshot_disabled_by_adapter(bm.url, username=bm.owner.username if bm.owner else "")
    ]

    create_html_snapshots(eligible_bookmarks)

    return len(eligible_bookmarks)


# ---------------------------------------------------------------------------
# 文章提取（阅读模式，defuddle 解析）
#
# 解析优先级：
#   1. 已有 HTML 快照 → 直接用 defuddle 解析 HTML
#   2. 域名配置了自定义 snapshot_processor → 先生成快照再解析
#   3. 都没有 → 让 defuddle 直接抓取 URL；失败则回退到生成快照再解析
# ---------------------------------------------------------------------------


def create_article(
    bookmark: Bookmark, priority: int | None = None
) -> BookmarkAsset:
    """创建 pending 状态的文章资产，并提交 defuddle 解析任务。"""
    from bookmarks.services.articles import create_article_asset_pending

    asset = create_article_asset_pending(bookmark)
    _create_article_task(
        asset.id, reader_priority=priority, priority=priority
    )
    return asset


def create_html_articles(
    bookmark_list: list[Bookmark], priority: int | None = None
):
    """批量创建 pending 状态的文章资产，并逐个提交 defuddle 解析任务。"""
    from bookmarks.services.articles import create_article_asset_pending

    for bookmark in bookmark_list:
        asset = create_article_asset_pending(bookmark)
        _create_article_task(
            asset.id, reader_priority=priority, priority=priority
        )


def _load_snapshot_asset_content(
    snapshot: BookmarkAsset | None,
) -> tuple[str | None, str | None]:
    """从快照资产文件中读取原始内容，无法读取时返回 (None, None)。"""
    if (
        not snapshot
        or snapshot.status != BookmarkAsset.STATUS_COMPLETE
        or not snapshot.file
    ):
        return None, None

    filepath = os.path.join(settings.LD_ASSET_FOLDER, snapshot.file)
    if not os.path.exists(filepath):
        return None, None

    try:
        if snapshot.gzip:
            with gzip.open(filepath, "rb") as f:
                return f.read().decode("utf-8"), snapshot.content_type
        else:
            with open(filepath, encoding="utf-8") as f:
                return f.read(), snapshot.content_type
    except Exception:
        logger.warning(
            f"Failed to read snapshot for bookmark. url={snapshot.bookmark.url}",
            exc_info=True,
        )
        return None, None


def _load_snapshot_content(
    bookmark: Bookmark,
) -> tuple[str | None, str | None]:
    """读取书签最新快照的原始内容，无可用快照时返回 (None, None)。"""
    return _load_snapshot_asset_content(bookmark.latest_snapshot)


def _requires_snapshot_before_article(url: str) -> bool:
    """检查站点是否需要先生成快照再提取文章。

    包括自定义脚本、声明式 snapshot 字段，以及使用 XPath/JSONPath
    的 reader contentSelector，因为 Reader 需要从 site-adapters
    处理后的快照中提取内容。
    """
    from site_adapters.services.config.resolver import get_snapshot_config

    config = get_snapshot_config(url)
    if config and (config.get("_raw") or {}).get("snapshot"):
        return True

    from bookmarks.services.reader_processor import (
        _is_json_path_selector,
        _is_xpath_selector,
    )
    from site_adapters.services.config.resolver import get_reader_config

    reader_config = get_reader_config(url) or {}
    selectors = (reader_config.get("defuddle_args") or {}).get("contentSelector")
    if isinstance(selectors, str):
        selectors = [selectors]
    return any(
        isinstance(selector, str)
        and (_is_xpath_selector(selector) or _is_json_path_selector(selector))
        for selector in (selectors or [])
    )


def _is_reader_use_async(url: str, username: str = "") -> bool:
    """Check whether the reader config enables defuddle async extractors.

    Reads the ``useAsync`` field from the resolved reader section's
    ``defuddle_args``. Defaults to False (set in ``_builtin``),
    so this returns True only when an adapter explicitly sets
    ``reader.defuddle_args.useAsync: true`` for the matched domain/route.
    """
    try:
        from site_adapters.services.config.resolver import get_reader_config
        config = get_reader_config(url, username=username)
        if not config:
            return False
        defuddle_args = config.get('defuddle_args', {})
        return defuddle_args.get('useAsync', False) is True
    except Exception:
        logger.debug("Failed to read reader useAsync flag for %s", url, exc_info=True)
        return False


def _create_snapshot_for_article(
    bookmark: Bookmark,
    priority: int = PRIORITY_CORE,
) -> tuple[BookmarkAsset | None, str | None, str | None]:
    """为文章解析生成快照，等待完成后返回内容。

    根据 ``snapshot.enabled`` 配置决定生成方式：
    - ``snapshot.enabled: true``（默认）→ 永久快照（走 dispatcher，创建 BookmarkAsset）
    - ``snapshot.enabled: false`` → 临时快照（直接调用 create_snapshot，不创建 BookmarkAsset）

    返回 (asset, content, content_type)；临时快照时 asset 为 None。
    """
    url = bookmark.url
    username = _bookmark_username(bookmark)

    if _is_snapshot_disabled_by_adapter(url, username=username):
        # snapshot.enabled=false → 临时快照，不创建 BookmarkAsset
        return _create_temporary_snapshot(bookmark, url, username)

    # snapshot.enabled=true → 永久快照，走 dispatcher
    asset = assets.create_snapshot_asset(bookmark)
    asset.scheduling_priority = priority
    asset.save()
    _trigger_html_snapshot_dispatcher(priority=priority)

    deadline = timezone.now() + timedelta(seconds=READER_SNAPSHOT_WAIT_TIMEOUT)
    while timezone.now() < deadline:
        asset.refresh_from_db()
        if asset.status == BookmarkAsset.STATUS_COMPLETE:
            content, content_type = _load_snapshot_asset_content(asset)
            return asset, content, content_type
        if asset.status == BookmarkAsset.STATUS_FAILURE:
            logger.warning(
                "Snapshot failed while preparing article. url=%s",
                bookmark.url,
            )
            return asset, None, None
        time.sleep(0.5)

    logger.warning(
        "Timed out waiting for snapshot before article. url=%s",
        bookmark.url,
    )
    return asset, None, None


def _create_temporary_snapshot(
    bookmark: Bookmark,
    url: str,
    username: str,
) -> tuple[None, str | None, str | None]:
    """生成临时快照 HTML，不创建 BookmarkAsset，用完即删。"""
    import tempfile
    from bookmarks.services.snapshot_processor import create_snapshot
    from site_adapters.services.config.resolver import get_snapshot_config

    config = get_snapshot_config(url, username=username)
    snapshot_extension = 'html'
    if config:
        ct = config.get('content_type', '')
        if ct in ('json', 'xml'):
            snapshot_extension = ct

    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix=f'.{snapshot_extension}', delete=False
        ) as tmp:
            tmp_path = tmp.name

        with _TEMPORARY_SNAPSHOT_LOCK:
            create_snapshot(url, tmp_path, username=username)
        with open(tmp_path, encoding='utf-8') as f:
            raw_content = f.read()
        content_type = (
            BookmarkAsset.CONTENT_TYPE_HTML
            if snapshot_extension == 'html'
            else snapshot_extension
        )
        return None, raw_content, content_type
    except Exception:
        logger.warning(
            "Failed to create temporary snapshot for article. url=%s",
            url,
            exc_info=True,
        )
        return None, None, None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _parse_snapshot_for_reader(
    content: str, content_type: str, bookmark: Bookmark
) -> dict:
    """Parse a snapshot with defuddle, dispatching XML/JSON through conversion."""
    from bookmarks.services import reader_processor

    username = _bookmark_username(bookmark)
    if content_type == BookmarkAsset.CONTENT_TYPE_HTML:
        return reader_processor.parse_html(content, url=bookmark.url, username=username)
    return reader_processor.parse_content(
        content,
        content_type,
        url=bookmark.url,
        username=username,
    )


@task(retries=2, priority=PRIORITY_CORE)
def _create_article_task(asset_id: int, reader_priority: int | None = None):
    """Huey 任务：抓取页面 → defuddle 解析 → 保存文章内容。"""
    from bookmarks.services.articles import remove_article, save_article_content

    try:
        asset = BookmarkAsset.objects.get(id=asset_id)
    except BookmarkAsset.DoesNotExist:
        return

    # LIFO dedup: if a newer pending article exists for the same bookmark, skip
    newer_pending = BookmarkAsset.objects.filter(
        bookmark=asset.bookmark,
        asset_type=BookmarkAsset.TYPE_ARTICLE,
        status=BookmarkAsset.STATUS_PENDING,
        date_created__gt=asset.date_created,
    ).exists()
    if newer_pending:
        logger.info(
            f"Skipping stale article task (newer pending exists). url={asset.bookmark.url}"
        )
        remove_article(asset)
        return

    bookmark = asset.bookmark
    logger.info("Create article for bookmark. url=%s", bookmark.url)

    fallback_snapshot = None
    try:
        from bookmarks.services import reader_processor

        url = bookmark.url
        username = _bookmark_username(bookmark)
        use_async = _is_reader_use_async(url, username=username)

        # 1. useAsync=true → defuddle 直接抓取/调 API，无需快照
        if use_async:
            logger.info("Parsing URL directly with defuddle (useAsync=true). url=%s", url)
            result = reader_processor.parse_url(url, username=username)
        else:
            # 2. useAsync=false → 需要快照 HTML 作为 defuddle 输入
            raw_content, snapshot_content_type = _load_snapshot_content(bookmark)
            if raw_content:
                # 2a. 已有快照 → 直接用
                logger.info("Using existing snapshot. url=%s", url)
                result = _parse_snapshot_for_reader(
                    raw_content, snapshot_content_type, bookmark
                )
            else:
                # 2b. 无快照 → 生成快照再解析
                # _create_snapshot_for_article 内部判断 snapshot.enabled
                # 来决定生成永久快照（走 dispatcher）还是临时快照
                logger.info("Creating snapshot for article. url=%s", url)
                fallback_snapshot, raw_content, snapshot_content_type = (
                    _create_snapshot_for_article(
                        bookmark, priority=reader_priority or PRIORITY_CORE
                    )
                )
                if not raw_content:
                    raise Exception("Failed to create snapshot for article")
                result = _parse_snapshot_for_reader(
                    raw_content, snapshot_content_type, bookmark
                )

        # 生成标准 HTML 文档：元数据放 head，正文放 body
        from django.utils.html import escape

        content = result["content"]
        head_parts = []
        if result.get("title"):
            head_parts.append(
                f'<meta name="title" content="{escape(result["title"])}">'
            )
        if result.get("wordCount"):
            head_parts.append(
                f'<meta name="word-count" content="{result["wordCount"]}">'
            )
        head = "".join(head_parts)
        title_tag = (
            f"<title>{escape(result['title'])}</title>" if result.get("title") else ""
        )
        content = f"<!DOCTYPE html><html><head>{title_tag}{head}</head><body>{content}</body></html>"

        # Save parsed content
        save_article_content(asset, content, title=result["title"])

        logger.info("Successfully created article for bookmark. url=%s", bookmark.url)
    except Exception as error:
        if fallback_snapshot:
            try:
                assets.remove_asset(fallback_snapshot)
            except Exception:
                logger.warning(
                    f"Failed to clean up generated snapshot after article failure. url={bookmark.url}",
                    exc_info=True,
                )
        try:
            remove_article(asset)
        except Exception:
            logger.warning(
                f"Failed to clean up article asset after processing failure. url={bookmark.url}",
                exc_info=True,
            )
        logger.error(
            f"Failed to create article for bookmark. url={bookmark.url}",
            exc_info=error,
        )
