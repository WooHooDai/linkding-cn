import logging

from django.db import transaction
from django.utils import timezone

from bookmarks.models import Bookmark, BookmarkAsset, User, parse_tag_string
from bookmarks.services import auto_tagging, tasks, website_loader
from bookmarks.services.tags import get_existing_tags, get_or_create_tags
from bookmarks.utils import normalize_url

logger = logging.getLogger(__name__)


def create_bookmark(
    bookmark: Bookmark,
    tag_string: str,
    current_user: User,
    disable_html_snapshot: bool = False,
    schedule_metadata_enrichment: bool = False,
):
    from django.db.models import Q

    # Use transaction and select_for_update to prevent TOCTOU race condition
    with transaction.atomic():
        normalized_url = normalize_url(bookmark.url)
        # Lock the row if it exists, preventing concurrent creates for the same URL
        # Fall back to exact URL match if normalized URL is empty (for legacy data)
        existing_bookmark: Bookmark = (
            Bookmark.objects.select_for_update()
            .filter(
                Q(owner=current_user)
                & (
                    Q(url_normalized=normalized_url)
                    | Q(url_normalized="", url=bookmark.url)
                )
            )
            .first()
        )

        if existing_bookmark is not None:
            _merge_bookmark_data(bookmark, existing_bookmark)
            return update_bookmark(existing_bookmark, tag_string, current_user)

        # Set currently logged in user as owner
        bookmark.owner = current_user
        if not bookmark.date_added:
            bookmark.date_added = timezone.now()
        if not bookmark.date_modified:
            bookmark.date_modified = timezone.now()
        bookmark.save()
        # Update tag list
        _update_bookmark_tags(bookmark, tag_string, current_user)
        bookmark.save()

    # Tasks that don't need to be in the transaction
    # Create snapshot on web archive
    tasks.create_web_archive_snapshot(
        current_user,
        bookmark,
        False,
        priority=tasks.PRIORITY_NEW_BOOKMARK,
    )
    # Load favicon and let task logic decide whether to reuse cached state
    tasks.load_favicon(current_user, bookmark, priority=tasks.PRIORITY_NEW_BOOKMARK)
    # Load preview image
    tasks.load_preview_image(
        current_user, bookmark, priority=tasks.PRIORITY_NEW_BOOKMARK
    )
    # Create HTML snapshot
    if (
        current_user.profile.enable_automatic_html_snapshots
        and not disable_html_snapshot
    ):
        tasks.create_html_snapshot(bookmark, priority=tasks.PRIORITY_NEW_BOOKMARK)

    if schedule_metadata_enrichment and _needs_metadata_enrichment(bookmark):
        tasks.schedule_metadata_enrichment(
            bookmark, priority=tasks.PRIORITY_NEW_BOOKMARK
        )

    return bookmark


def update_bookmark(
    bookmark: Bookmark,
    tag_string,
    current_user: User,
    tags_may_have_changed: bool = True,
):
    # Detect URL change
    original_bookmark = Bookmark.objects.get(id=bookmark.id)
    has_url_changed = original_bookmark.url != bookmark.url
    has_preview_image_changed = (
        original_bookmark.preview_image_remote_url != bookmark.preview_image_remote_url
    )
    # 只有内容字段变化才算修改；仅切换已读/分享等状态不改修改时间
    has_content_changed = (
        has_url_changed
        or has_preview_image_changed
        or original_bookmark.title != bookmark.title
        or original_bookmark.description != bookmark.description
        or original_bookmark.author != bookmark.author
        or original_bookmark.notes != bookmark.notes
    )
    # 调用方未提交标签时无需处理标签，也就不必比较标签集合
    has_tags_changed = False
    if tags_may_have_changed:
        # 内容变化时必然要更新修改时间，无需比较标签
        original_tag_names = (
            None
            if has_content_changed
            else set(original_bookmark.tags.values_list("name", flat=True))
        )
        # Update tag list
        _update_bookmark_tags(bookmark, tag_string, current_user)
        has_tags_changed = original_tag_names is not None and (
            set(bookmark.tags.values_list("name", flat=True)) != original_tag_names
        )
    # Update dates
    bookmark.date_modified = (
        timezone.now()
        if has_content_changed or has_tags_changed
        else original_bookmark.date_modified
    )
    if has_preview_image_changed:
        # 让新远程图先展示，本地文件由下载任务完成后写回
        bookmark.preview_image_file = ""
    bookmark.save()
    # Update favicon
    tasks.load_favicon(
        current_user,
        bookmark,
        priority=tasks.PRIORITY_NEW_BOOKMARK if has_url_changed else None,
    )
    # Update preview image
    tasks.load_preview_image(
        current_user,
        bookmark,
        force=has_url_changed or has_preview_image_changed,
        priority=tasks.PRIORITY_NEW_BOOKMARK if has_url_changed else None,
    )

    if has_url_changed:
        # Update web archive snapshot, if URL changed
        tasks.create_web_archive_snapshot(current_user, bookmark, True)

    return bookmark


def enhance_with_website_metadata(bookmark: Bookmark):
    username = bookmark.owner.username if bookmark.owner else ""
    metadata = website_loader.load_website_metadata(bookmark.url, username=username)
    update_fields = []

    if bookmark.has_placeholder_title:
        bookmark.title = metadata.title or ""
        update_fields.append("title")

    if not bookmark.description:
        bookmark.description = metadata.description or ""
        update_fields.append("description")

    if not bookmark.author:
        bookmark.author = metadata.author or ""
        update_fields.append("author")

    if not bookmark.preview_image_remote_url and metadata.preview_image:
        bookmark.preview_image_remote_url = metadata.preview_image
        update_fields.append("preview_image_remote_url")

    if update_fields:
        bookmark.date_modified = timezone.now()
        update_fields.append("date_modified")
        bookmark.save(update_fields=update_fields)


def _needs_metadata_enrichment(bookmark: Bookmark):
    return (
        bookmark.has_placeholder_title
        or not bookmark.description
        or not bookmark.author
        or not bookmark.preview_image_remote_url
    )


def archive_bookmark(bookmark: Bookmark):
    bookmark.is_archived = True
    bookmark.save()
    return bookmark


def archive_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        is_archived=True
    )


def unarchive_bookmark(bookmark: Bookmark):
    bookmark.is_archived = False
    bookmark.save()
    return bookmark


def unarchive_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        is_archived=False
    )


def delete_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).delete()


def trash_bookmark(bookmark: Bookmark):
    bookmark.is_deleted = True
    bookmark.date_deleted = timezone.now()
    bookmark.save()


def trash_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        is_deleted=True, date_deleted=timezone.now()
    )


def restore_bookmark(bookmark: Bookmark):
    bookmark.is_deleted = False
    bookmark.date_deleted = None
    bookmark.save()


def restore_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        is_deleted=False, date_deleted=None
    )


def tag_bookmarks(bookmark_ids: [int | str], tag_string: str, current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmark_ids = list(
        Bookmark.objects.filter(
            owner=current_user, id__in=sanitized_bookmark_ids
        ).values_list("id", flat=True)
    )
    tag_names = parse_tag_string(tag_string)
    if not owned_bookmark_ids or not tag_names:
        return

    tags = get_or_create_tags(tag_names, current_user)
    if not tags:
        return

    BookmarkToTagRelationShip = Bookmark.tags.through
    existing = set(
        BookmarkToTagRelationShip.objects.filter(
            bookmark_id__in=owned_bookmark_ids, tag__in=tags
        ).values_list("bookmark_id", "tag_id")
    )
    relationships = [
        BookmarkToTagRelationShip(bookmark_id=bookmark_id, tag=tag)
        for tag in tags
        for bookmark_id in owned_bookmark_ids
        if (bookmark_id, tag.id) not in existing
    ]
    if not relationships:
        # 关联已全部存在，不算修改
        return

    # Insert all bookmark -> tag associations at once, should ignore errors if association already exists
    BookmarkToTagRelationShip.objects.bulk_create(relationships, ignore_conflicts=True)
    Bookmark.objects.filter(id__in=owned_bookmark_ids).update(
        date_modified=timezone.now()
    )


def untag_bookmarks(bookmark_ids: [int | str], tag_string: str, current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmark_ids = list(
        Bookmark.objects.filter(
            owner=current_user, id__in=sanitized_bookmark_ids
        ).values_list("id", flat=True)
    )
    tag_names = parse_tag_string(tag_string)
    if not owned_bookmark_ids or not tag_names:
        return

    # 移除标签不应创建标签：只处理确实存在的标签
    tags = get_existing_tags(tag_names, current_user)
    if not tags:
        return

    BookmarkToTagRelationShip = Bookmark.tags.through
    removed = 0
    for tag in tags:
        # Remove all bookmark -> tag associations for the owned bookmarks and the current tag
        deleted, _ = BookmarkToTagRelationShip.objects.filter(
            bookmark_id__in=owned_bookmark_ids, tag=tag
        ).delete()
        removed += deleted

    if not removed:
        # 没有任何关联被移除，不算修改
        return

    Bookmark.objects.filter(id__in=owned_bookmark_ids).update(
        date_modified=timezone.now()
    )


def mark_bookmarks_as_read(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        unread=False
    )


def mark_bookmarks_as_unread(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        unread=True
    )


def share_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        shared=True
    )


def unshare_bookmarks(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    Bookmark.objects.filter(owner=current_user, id__in=sanitized_bookmark_ids).update(
        shared=False
    )


def enrich_bookmarks_metadata(bookmark_ids: [int | str], current_user: User):
    """抓取选中书签的元数据，只填补为空的字段，不覆盖已有内容。"""
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmarks = Bookmark.objects.filter(
        owner=current_user, id__in=sanitized_bookmark_ids
    )

    for bookmark in owned_bookmarks:
        # 字段齐全的书签无需抓取
        if _needs_metadata_enrichment(bookmark):
            tasks.schedule_metadata_enrichment(bookmark)


def refresh_bookmarks_metadata(bookmark_ids: [int | str], current_user: User):
    """重新抓取选中书签的元数据并覆盖已有值（抓不到的字段保留原值）。"""
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmarks = Bookmark.objects.filter(
        owner=current_user, id__in=sanitized_bookmark_ids
    )

    for bookmark in owned_bookmarks:
        tasks.schedule_metadata_refresh(bookmark)


def refresh_bookmarks_favicons(bookmark_ids: list[int | str], current_user: User):
    """批量刷新选中书签的 favicon，按域名去重。"""
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmarks = Bookmark.objects.filter(
        owner=current_user, id__in=sanitized_bookmark_ids
    )

    tasks.refresh_favicons_for_bookmarks(current_user, owned_bookmarks)


def create_html_snapshots(bookmark_ids: list[int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmarks = Bookmark.objects.filter(
        owner=current_user, id__in=sanitized_bookmark_ids
    )

    tasks.create_html_snapshots(owned_bookmarks)


def remove_all_html_snapshots(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    BookmarkAsset.objects.filter(
        bookmark__owner=current_user,
        bookmark_id__in=sanitized_bookmark_ids,
        asset_type=BookmarkAsset.TYPE_SNAPSHOT,
    ).delete()


def create_html_articles(bookmark_ids: list[int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    owned_bookmarks = Bookmark.objects.filter(
        owner=current_user, id__in=sanitized_bookmark_ids
    )

    tasks.create_html_articles(owned_bookmarks)


def remove_all_html_articles(bookmark_ids: [int | str], current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)

    BookmarkAsset.objects.filter(
        bookmark__owner=current_user,
        bookmark_id__in=sanitized_bookmark_ids,
        asset_type=BookmarkAsset.TYPE_ARTICLE,
    ).delete()


def _merge_bookmark_data(from_bookmark: Bookmark, to_bookmark: Bookmark):
    to_bookmark.title = from_bookmark.title
    to_bookmark.description = from_bookmark.description
    to_bookmark.notes = from_bookmark.notes
    to_bookmark.unread = from_bookmark.unread
    to_bookmark.shared = from_bookmark.shared


def _update_bookmark_tags(bookmark: Bookmark, tag_string: str, user: User):
    tag_names = parse_tag_string(tag_string)

    if user.profile.auto_tagging_rules:
        try:
            auto_tag_names = auto_tagging.get_tags(
                user.profile.auto_tagging_rules, bookmark.url
            )
            for auto_tag_name in auto_tag_names:
                if auto_tag_name not in tag_names:
                    tag_names.append(auto_tag_name)
        except Exception as e:
            logger.error(
                f"Failed to auto-tag bookmark. url={bookmark.url}",
                exc_info=e,
            )

    tags = get_or_create_tags(tag_names, user)
    bookmark.tags.set(tags)


def _sanitize_id_list(bookmark_ids: [int | str]) -> [int]:
    # 非法值（None / 非数字字符串）直接忽略，避免单个脏 id 让整个批量请求 500
    sanitized = []
    for bm_id in bookmark_ids:
        try:
            sanitized.append(int(bm_id))
        except (TypeError, ValueError):
            continue
    return sanitized
