import functools
import logging
import os
import random
import time
from datetime import timedelta
from typing import Callable, List

import waybackpy
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from huey import crontab
from huey.contrib.djhuey import HUEY as huey
from huey.exceptions import TaskLockedException
from waybackpy.exceptions import WaybackError, TooManyRequestsError

from bookmarks.models import Bookmark, BookmarkAsset, UserProfile
from bookmarks.services import assets, favicon_loader, preview_image_loader
from bookmarks.utils import get_registrable_domain
from bookmarks.services.website_loader import load_website_metadata

logger = logging.getLogger(__name__)
HTML_SNAPSHOT_DISPATCHER_LOCK = huey.lock_task("html-snapshot-dispatcher-lock")


# Create custom decorator for Huey tasks that implements exponential backoff
# Taken from: https://huey.readthedocs.io/en/latest/guide.html#tips-and-tricks
# Retry 1: 60
# Retry 2: 240
# Retry 3: 960
# Retry 4: 3840
# Retry 5: 15360
def task(retries=5, retry_delay=15, retry_backoff=4):
    def deco(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            task = kwargs.pop("task")
            try:
                return fn(*args, **kwargs)
            except TaskLockedException as exc:
                # Task locks are currently only used as workaround to enforce
                # running specific types of tasks (e.g. singlefile snapshots)
                # sequentially. In that case don't reduce the number of retries.
                task.retries = retries
                raise exc
            except Exception as exc:
                task.retry_delay *= retry_backoff
                raise exc

        return huey.task(retries=retries, retry_delay=retry_delay, context=True)(inner)

    return deco


def is_web_archive_integration_active(user: User) -> bool:
    background_tasks_enabled = not settings.LD_DISABLE_BACKGROUND_TASKS
    web_archive_integration_enabled = (
        user.profile.web_archive_integration
        == UserProfile.WEB_ARCHIVE_INTEGRATION_ENABLED
    )

    return background_tasks_enabled and web_archive_integration_enabled


def create_web_archive_snapshot(user: User, bookmark: Bookmark, force_update: bool):
    if is_web_archive_integration_active(user):
        _create_web_archive_snapshot_task(bookmark.id, force_update)


def _create_snapshot(bookmark: Bookmark):
    logger.info(f"Create new snapshot for bookmark. url={bookmark.url}...")
    archive = waybackpy.WaybackMachineSaveAPI(
        bookmark.url, settings.LD_DEFAULT_USER_AGENT, max_tries=1
    )
    archive.save()
    bookmark.web_archive_snapshot_url = archive.archive_url
    bookmark.save(update_fields=["web_archive_snapshot_url"])
    logger.info(f"Successfully created new snapshot for bookmark:. url={bookmark.url}")


@task()
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
        _create_snapshot(bookmark)
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


@task()
def _load_web_archive_snapshot_task(bookmark_id: int):
    # Loading snapshots from CDX API has been removed, keeping the task function
    # for now to prevent errors when huey tries to run the task
    pass


@task()
def _schedule_bookmarks_without_snapshots_task(user_id: int):
    # Loading snapshots from CDX API has been removed, keeping the task function
    # for now to prevent errors when huey tries to run the task
    pass


def is_favicon_feature_active(user: User) -> bool:
    background_tasks_enabled = not settings.LD_DISABLE_BACKGROUND_TASKS

    return background_tasks_enabled and user.profile.enable_favicons


def is_preview_feature_active(user: User) -> bool:
    return (
        user.profile.enable_preview_images and not settings.LD_DISABLE_BACKGROUND_TASKS
    )


def update_bookmark_favicon(bookmark: Bookmark, new_favicon_file: str):
    if new_favicon_file != bookmark.favicon_file:
        bookmark.favicon_file = new_favicon_file
        bookmark.save(update_fields=["favicon_file"])
        logger.info(
            f"Successfully updated favicon for bookmark. url={bookmark.url} icon={new_favicon_file}"
        )


def load_favicon(user: User, bookmark: Bookmark):
    if is_favicon_feature_active(user):
        cached_favicon = favicon_loader.get_cached_favicon(bookmark.url)
        if cached_favicon:
            update_bookmark_favicon(bookmark, cached_favicon.filename)
            if not cached_favicon.is_stale:
                return
        _load_favicon_task(bookmark.id)


def refresh_favicon(user: User, bookmark: Bookmark):
    if is_favicon_feature_active(user):
        _load_favicon_task(bookmark.id)


@task(retries=3)
def _load_favicon_task(bookmark_id: int):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info(f"Refresh favicon for bookmark. url={bookmark.url}")

    new_favicon_file = favicon_loader.refresh_favicon(bookmark.url)
    update_bookmark_favicon(bookmark, new_favicon_file)


def schedule_bookmarks_without_favicons(user: User):
    if is_favicon_feature_active(user):
        _schedule_bookmarks_without_favicons_task(user.id)


@task()
def _schedule_bookmarks_without_favicons_task(user_id: int):
    user = User.objects.get(id=user_id)
    bookmarks = Bookmark.objects.filter(favicon_file__exact="", owner=user)

    # TODO: Implement bulk task creation
    for bookmark in bookmarks:
        load_favicon(user, bookmark)


def schedule_refresh_favicons(user: User):
    if is_favicon_feature_active(user) and settings.LD_ENABLE_REFRESH_FAVICONS:
        _schedule_refresh_favicons_task(user.id)


@task()
def _schedule_refresh_favicons_task(user_id: int):
    user = User.objects.get(id=user_id)
    bookmarks = Bookmark.objects.filter(owner=user)

    # TODO: Implement bulk task creation
    for bookmark in bookmarks:
        refresh_favicon(user, bookmark)


def load_preview_image(user: User, bookmark: Bookmark):
    if is_preview_feature_active(user):
        _load_preview_image_task(bookmark.id)


@task()
def delete_preview_image_temp_file(filepath: str):
    logger.debug(f"Followed temporary preview image file will be deleted after a while: {filepath}")
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Deleted temporary preview image file: {filepath}")

@task()
def _load_preview_image_task(bookmark_id: int):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info(f"Load preview image for bookmark. url={bookmark.url}")

    new_preview_image_file = preview_image_loader.load_preview_image(bookmark.url, bookmark)

    if new_preview_image_file != bookmark.preview_image_file:
        bookmark.preview_image_file = new_preview_image_file or ""
        bookmark.save(update_fields=["preview_image_file"])
        logger.info(
            f"Successfully updated preview image for bookmark. url={bookmark.url} preview_image_file={new_preview_image_file}"
        )


def schedule_bookmarks_without_previews(user: User):
    if is_preview_feature_active(user):
        _schedule_bookmarks_without_previews_task(user.id)


@task()
def _schedule_bookmarks_without_previews_task(user_id: int):
    user = User.objects.get(id=user_id)
    bookmarks = Bookmark.objects.filter(
        Q(preview_image_file__exact=""),
        owner=user,
    )

    # TODO: Implement bulk task creation
    for bookmark in bookmarks:
        try:
            _load_preview_image_task(bookmark.id)
        except Exception as exc:
            logging.exception(exc)


def refresh_metadata(bookmark: Bookmark):
    if not settings.LD_DISABLE_BACKGROUND_TASKS:
        _refresh_metadata_task(bookmark.id)


@task()
def _refresh_metadata_task(bookmark_id: int):
    try:
        bookmark = Bookmark.objects.get(id=bookmark_id)
    except Bookmark.DoesNotExist:
        return

    logger.info(f"Refresh metadata for bookmark. url={bookmark.url}")

    metadata = load_website_metadata(bookmark.url, ignore_cache=True)
    update_fields = []

    if metadata.title or metadata.title=='':
        bookmark.title = metadata.title
        update_fields.append("title")
    if metadata.description or metadata.description=='':
        bookmark.description = metadata.description
        update_fields.append("description")
    if metadata.preview_image:
        bookmark.preview_image_remote_url = metadata.preview_image
        update_fields.append("preview_image_remote_url")
    if metadata.url and metadata.url != bookmark.url:
        bookmark.url = metadata.url
        update_fields.append("url")
    bookmark.date_modified = timezone.now()

    bookmark.save(update_fields=update_fields)
    logger.info(f"Successfully refreshed metadata for bookmark. url={bookmark.url}")

    # 若url变动，则按需更新html快照
    if bookmark.owner.profile.enable_automatic_html_snapshots:
        pending_assets = BookmarkAsset.objects.filter(bookmark=bookmark, status=BookmarkAsset.STATUS_PENDING)
        if pending_assets.exists(): # 若有下载中的快照，则移除
            pending_assets.delete()
        
        create_html_snapshot(bookmark)

def is_html_snapshot_feature_active() -> bool:
    return settings.LD_ENABLE_SNAPSHOTS and not settings.LD_DISABLE_BACKGROUND_TASKS


def _kick_html_snapshot_dispatcher():
    _html_snapshot_dispatcher_task()


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
    pending_assets = (
        BookmarkAsset.objects.filter(
            asset_type=BookmarkAsset.TYPE_SNAPSHOT,
            status=BookmarkAsset.STATUS_PENDING,
        )
        .select_related("bookmark")
        .order_by("-date_created", "-id")
    )

    next_wake_at = None
    for asset in pending_assets:
        domain = get_registrable_domain(asset.bookmark.url)
        eligible_at = next_eligible_at.get(domain)
        if eligible_at is None or eligible_at <= now:
            return asset, None
        if next_wake_at is None or eligible_at < next_wake_at:
            next_wake_at = eligible_at

    return None, next_wake_at


def _get_html_snapshot_dispatcher_sleep_seconds(now, next_wake_at) -> float:
    remaining_seconds = max((next_wake_at - now).total_seconds(), 0)
    if remaining_seconds == 0:
        return 0
    return min(remaining_seconds, _get_html_snapshot_dispatcher_tick_seconds())


def _run_html_snapshot_dispatcher_loop(
    now_func: Callable[[], object] | None = None,
    sleep_func: Callable[[float], None] | None = None,
    cooldown_func: Callable[[], int] | None = None,
):
    now_func = now_func or timezone.now
    sleep_func = sleep_func or time.sleep
    cooldown_func = cooldown_func or _get_html_snapshot_cooldown_seconds
    next_eligible_at: dict[str, object] = {}

    while True:
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


@task(retries=0, retry_delay=0)
def _html_snapshot_dispatcher_task():
    try:
        with HTML_SNAPSHOT_DISPATCHER_LOCK:
            _run_html_snapshot_dispatcher_loop()
    except TaskLockedException:
        logger.debug("HTML snapshot dispatcher already running.")


def create_html_snapshot(bookmark: Bookmark):
    if not is_html_snapshot_feature_active():
        return

    asset = assets.create_snapshot_asset(bookmark)
    asset.save()
    _kick_html_snapshot_dispatcher()


def create_html_snapshots(bookmark_list: List[Bookmark]):
    if not is_html_snapshot_feature_active():
        return

    assets_to_create = []
    for bookmark in bookmark_list:
        asset = assets.create_snapshot_asset(bookmark)
        assets_to_create.append(asset)

    if not assets_to_create:
        return

    BookmarkAsset.objects.bulk_create(assets_to_create)
    _kick_html_snapshot_dispatcher()


# SingleFile does not support running multiple snapshot captures in parallel.
# Keep a periodic fallback that can re-kick the dispatcher if pending work was
# missed due to an interrupted worker or process restart.
@huey.periodic_task(crontab(minute="*"))
def _schedule_html_snapshots_task():
    if BookmarkAsset.objects.filter(
        asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        status=BookmarkAsset.STATUS_PENDING,
    ).exists():
        _kick_html_snapshot_dispatcher()


def _create_html_snapshot_task(asset_id: int):
    try:
        asset = BookmarkAsset.objects.get(id=asset_id)
    except BookmarkAsset.DoesNotExist:
        return

    logger.info(f"Create HTML snapshot for bookmark. url={asset.bookmark.url}")

    try:
        assets.create_snapshot(asset)

        logger.info(
            f"Successfully created HTML snapshot for bookmark. url={asset.bookmark.url}"
        )
    except Exception as error:
        logger.error(
            f"Failed to HTML snapshot for bookmark. url={asset.bookmark.url}",
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

    create_html_snapshots(list(bookmarks_without_snapshots))

    return bookmarks_without_snapshots.count()
