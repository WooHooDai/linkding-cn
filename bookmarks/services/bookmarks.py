import json
import re

from django.db import transaction
from django.utils import timezone

from bookmarks.models import Bookmark, BookmarkAsset, User, parse_tag_string
from bookmarks.services import auto_tagging, tasks, website_loader
from bookmarks.services.tags import get_existing_tags, get_or_create_tags
from bookmarks.utils import normalize_url


def create_bookmark(
    bookmark: Bookmark,
    tag_string: str,
    current_user: User,
    disable_html_snapshot: bool = False,
    schedule_metadata_enrichment: bool = False,
    excluded_auto_tags: list[str] | None = None,
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
            return update_bookmark(
                existing_bookmark,
                tag_string,
                current_user,
                excluded_auto_tags=excluded_auto_tags,
            )

        # Set currently logged in user as owner
        bookmark.owner = current_user
        if not bookmark.date_added:
            bookmark.date_added = timezone.now()
        if not bookmark.date_modified:
            bookmark.date_modified = timezone.now()
        bookmark.save()
        # Update tag list
        _update_bookmark_tags(bookmark, tag_string, current_user, excluded_auto_tags)
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
    excluded_auto_tags: list[str] | None = None,
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
        _update_bookmark_tags(bookmark, tag_string, current_user, excluded_auto_tags)
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


# --- 批量编辑元数据 -------------------------------------------------------

METADATA_EDIT_FIELDS = ("title", "description", "author", "notes")
METADATA_EDIT_TAG_FIELD = "tags"
METADATA_EDIT_MODES = ("set", "clear", "replace", "normalize")
METADATA_EDIT_TAG_MODES = ("add", "remove", "clear")
# 同字段的替换规则按顺序执行，条数上限避免弹窗纵向失控与误配
MAX_METADATA_REPLACE_RULES = 10
# 单次改写的安全阀，不是性能阀：上限内的事务能在请求超时线之前落定，
# 超出则整体拒绝，而不是让一部分书签被改、一部分没改
METADATA_EDIT_LIMIT = 5000

_INLINE_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")


def _empty_edit_stats():
    return {
        "total": 0,
        "updated": 0,
        "fields": {},
        "steps": {},
        "tags": {"added": 0, "removed": 0, "cleared": 0, "bookmarks": 0},
    }


def parse_metadata_ops(raw) -> dict:
    """把弹窗提交的 ops 规范化，未知字段、未知模式、无效查找串一律丢弃。"""
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    elif isinstance(raw, dict):
        data = raw
    else:
        return {}

    if not isinstance(data, dict):
        return {}

    ops = {}
    for field in METADATA_EDIT_FIELDS:
        op = data.get(field)
        if not isinstance(op, dict):
            continue
        mode = op.get("mode")
        if mode not in METADATA_EDIT_MODES:
            continue

        entry = {"mode": mode}
        if mode == "set":
            entry["value"] = str(op.get("value") or "")
        elif mode == "replace":
            # 兼容单条 {find, replace} 与多规则 {rules: [...]}，统一按有序列表处理
            candidates = op.get("rules") if isinstance(op.get("rules"), list) else [op]
            rules = []
            for rule in candidates:
                if not isinstance(rule, dict):
                    continue
                find = str(rule.get("find") or "")
                if not find:
                    continue
                rules.append(
                    {"find": find, "replace": str(rule.get("replace") or "")}
                )
                if len(rules) >= MAX_METADATA_REPLACE_RULES:
                    break
            if not rules:
                continue
            entry["rules"] = rules
            entry["case_insensitive"] = bool(op.get("case_insensitive"))
        ops[field] = entry

    tag_op = data.get(METADATA_EDIT_TAG_FIELD)
    if isinstance(tag_op, dict):
        add_names, remove_names = _tag_op_lists(tag_op)
        if add_names or remove_names:
            ops[METADATA_EDIT_TAG_FIELD] = {
                "add": add_names,
                "remove": remove_names,
            }

    return ops


def _normalize_tag_names(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw = " ".join(str(item) for item in value)
    else:
        raw = str(value or "")
    # 输入用空格分隔，服务层解析用逗号
    raw = raw.replace(" ", ",")
    return [name for name in parse_tag_string(raw) if len(name) <= 64]


def _tag_op_lists(op: dict):
    """标签动作是"添加一份 + 移除一份"，同时兼容旧的单模式写法。"""
    add = op.get("add")
    remove = op.get("remove")
    if add is None and remove is None:
        names = op.get("names") or op.get("value") or []
        if op.get("mode") == "add":
            add = names
        elif op.get("mode") == "remove":
            remove = names
    return _normalize_tag_names(add), _normalize_tag_names(remove)


def _replace_rules(op: dict) -> list:
    rules = op.get("rules")
    if isinstance(rules, list) and rules:
        return rules[:MAX_METADATA_REPLACE_RULES]
    return [op]


def _apply_text_op(value: str, op: dict):
    """返回 (新值, 每条规则的命中标记)。非替换模式的命中标记为空列表。"""
    mode = op["mode"]
    if mode == "set":
        return op["value"], []
    if mode == "clear":
        return "", []
    if mode == "normalize":
        # 按行清理：行首尾空白与行内连续空白都压掉，换行与空行结构保留
        return (
            "\n".join(
                _INLINE_WHITESPACE_RE.sub(" ", line.strip())
                for line in value.strip().splitlines()
            ),
            [],
        )
    if mode == "replace":
        # 规则按顺序串联：后一条看到的是前一条改写后的值
        hits = []
        for rule in _replace_rules(op):
            find = rule.get("find") or ""
            if not find:
                hits.append(0)
                continue
            replacement = rule.get("replace") or ""
            if rule.get("case_insensitive", op.get("case_insensitive")):
                updated = re.sub(
                    re.escape(find),
                    lambda _match, repl=replacement: repl,
                    value,
                    flags=re.IGNORECASE,
                )
            else:
                updated = value.replace(find, replacement)
            hits.append(1 if updated != value else 0)
            value = updated
        return value, hits
    return value, []


def collect_metadata_changes(bookmarks, ops):
    """计算每条书签需要写入的字段值。预览与执行共用，避免两套逻辑漂移。"""
    fields = [field for field in METADATA_EDIT_FIELDS if field in ops]
    changes = []
    counts = {field: 0 for field in fields}
    # 每条替换规则各自命中多少条，用来告诉用户哪一步没起作用
    steps = {
        field: [0] * len(_replace_rules(ops[field]))
        for field in fields
        if ops[field]["mode"] == "replace"
    }
    if not fields:
        return changes, counts, steps

    for bookmark in bookmarks:
        values = {}
        for field in fields:
            current = getattr(bookmark, field) or ""
            updated, hits = _apply_text_op(current, ops[field])
            if hits:
                for index, hit in enumerate(hits):
                    if hit:
                        steps[field][index] += 1
            if updated != current:
                values[field] = updated
                counts[field] += 1
        if values:
            changes.append((bookmark, values))

    return changes, counts, steps


def _preview_tag_op(bookmark_ids: list[int], op: dict, user: User):
    through = Bookmark.tags.through
    stats = {"added": 0, "removed": 0, "cleared": 0}
    changed_ids = set()
    removed_tag_ids = set()

    add_names, remove_names = _tag_op_lists(op)

    if remove_names:
        tags = get_existing_tags(remove_names, user)
        if tags:
            removed_tag_ids = {tag.id for tag in tags}
            qs = through.objects.filter(bookmark_id__in=bookmark_ids, tag__in=tags)
            changed_ids |= set(qs.values_list("bookmark_id", flat=True))
            stats["removed"] = qs.count()

    if add_names:
        existing = {
            tag.name.lower(): tag.id for tag in get_existing_tags(add_names, user)
        }
        linked = set(
            through.objects.filter(
                bookmark_id__in=bookmark_ids, tag_id__in=existing.values()
            ).values_list("bookmark_id", "tag_id")
        )
        # 执行是先移除后添加，预览也要按同样的中间态判断"是否已存在"
        linked = {
            (bookmark_id, tag_id)
            for bookmark_id, tag_id in linked
            if tag_id not in removed_tag_ids
        }
        for bookmark_id in bookmark_ids:
            for name in add_names:
                tag_id = existing.get(name.lower())
                if tag_id is None or (bookmark_id, tag_id) not in linked:
                    stats["added"] += 1
                    changed_ids.add(bookmark_id)

    return stats, changed_ids


def preview_metadata_edits(bookmark_ids: list[int | str], ops: dict, current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    stats = _empty_edit_stats()
    if not sanitized_bookmark_ids or not ops:
        return stats

    owned_bookmarks = Bookmark.objects.filter(
        owner=current_user, id__in=sanitized_bookmark_ids
    )
    stats["total"] = owned_bookmarks.count()
    ids = list(owned_bookmarks.values_list("id", flat=True))

    fields = [field for field in METADATA_EDIT_FIELDS if field in ops]
    changed_ids = set()
    if fields:
        changes, counts, steps = collect_metadata_changes(
            owned_bookmarks.only("id", *fields).iterator(chunk_size=1000), ops
        )
        stats["fields"] = counts
        stats["steps"] = steps
        changed_ids |= {bookmark.id for bookmark, _values in changes}

    tag_op = ops.get(METADATA_EDIT_TAG_FIELD)
    if tag_op:
        tag_stats, tag_changed_ids = _preview_tag_op(ids, tag_op, current_user)
        tag_stats["bookmarks"] = len(tag_changed_ids)
        stats["tags"] = tag_stats
        changed_ids |= tag_changed_ids

    stats["updated"] = len(changed_ids)
    return stats


def bulk_edit_metadata(bookmark_ids: list[int | str], ops: dict, current_user: User):
    sanitized_bookmark_ids = _sanitize_id_list(bookmark_ids)
    stats = _empty_edit_stats()
    if not sanitized_bookmark_ids or not ops:
        return stats

    fields = [field for field in METADATA_EDIT_FIELDS if field in ops]
    tag_op = ops.get(METADATA_EDIT_TAG_FIELD)

    # 整个改写必须原子：中途任何异常都整体回滚，不留下改了一半的集合
    with transaction.atomic():
        bookmarks = list(
            Bookmark.objects.filter(
                owner=current_user, id__in=sanitized_bookmark_ids
            ).only("id", "date_modified", *fields)
        )
        stats["total"] = len(bookmarks)
        if not bookmarks:
            return stats

        now = timezone.now()
        text_changed_ids = set()
        tag_changed_ids = set()
        if fields:
            changes, counts, steps = collect_metadata_changes(bookmarks, ops)
            stats["fields"] = counts
            stats["steps"] = steps
            for bookmark, values in changes:
                for field, value in values.items():
                    setattr(bookmark, field, value)
                bookmark.date_modified = now
                text_changed_ids.add(bookmark.id)
            if changes:
                Bookmark.objects.bulk_update(
                    [bookmark for bookmark, _values in changes],
                    [*fields, "date_modified"],
                )

        if tag_op:
            tag_stats, tag_changed_ids = _apply_tag_op(
                [bookmark.id for bookmark in bookmarks], tag_op, current_user
            )
            tag_stats["bookmarks"] = len(tag_changed_ids)
            stats["tags"] = tag_stats
            # 文本与标签都变的不必重复写时间戳
            only_tag_changed = tag_changed_ids - text_changed_ids
            if only_tag_changed:
                Bookmark.objects.filter(id__in=only_tag_changed).update(
                    date_modified=now
                )

        stats["updated"] = len(text_changed_ids | tag_changed_ids)

    return stats


def _apply_tag_op(bookmark_ids: list[int], op: dict, user: User):
    through = Bookmark.tags.through
    stats = {"added": 0, "removed": 0, "cleared": 0}
    changed_ids = set()

    add_names, remove_names = _tag_op_lists(op)

    # 先移除后添加：同一标签同时出现在两边时，用户最终是想留下它
    if remove_names:
        tags = get_existing_tags(remove_names, user)
        if tags:
            qs = through.objects.filter(bookmark_id__in=bookmark_ids, tag__in=tags)
            changed_ids |= set(qs.values_list("bookmark_id", flat=True))
            stats["removed"] = qs.count()
            qs.delete()

    if add_names:
        tags = get_or_create_tags(add_names, user)
        if tags:
            existing = set(
                through.objects.filter(
                    bookmark_id__in=bookmark_ids, tag__in=tags
                ).values_list("bookmark_id", "tag_id")
            )
            relationships = [
                through(bookmark_id=bookmark_id, tag=tag)
                for tag in tags
                for bookmark_id in bookmark_ids
                if (bookmark_id, tag.id) not in existing
            ]
            if relationships:
                through.objects.bulk_create(
                    relationships, batch_size=1000, ignore_conflicts=True
                )
                stats["added"] = len(relationships)
                changed_ids |= {
                    relationship.bookmark_id for relationship in relationships
                }

    return stats, changed_ids


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


def _update_bookmark_tags(
    bookmark: Bookmark,
    tag_string: str,
    user: User,
    excluded_auto_tags: list[str] | None = None,
):
    tag_names = parse_tag_string(tag_string)
    # 用户在表单里明确取消的规则标签不再补：自动标签是默认值，不是不可撤销的强制
    excluded = {name.lower() for name in excluded_auto_tags or ()}

    if user.profile.auto_tagging_rules:
        auto_tag_names = auto_tagging.get_tags_for_url(
            user.profile.auto_tagging_rules, bookmark.url
        )
        existing = {name.lower() for name in tag_names}
        for auto_tag_name in auto_tag_names:
            key = auto_tag_name.lower()
            if key in excluded or key in existing:
                continue
            tag_names.append(auto_tag_name)
            existing.add(key)

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
