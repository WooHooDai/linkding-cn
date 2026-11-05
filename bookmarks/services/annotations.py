from django.utils import timezone

from bookmarks.models import Annotation


def sanitize_annotation_ids(annotation_ids) -> list[int]:
    """非法值（None / 非数字字符串）直接忽略，避免单个脏 id 让整个批量请求 500。"""
    sanitized = []
    for annotation_id in annotation_ids:
        try:
            sanitized.append(int(annotation_id))
        except (TypeError, ValueError):
            continue
    # 批量表单会同时提交复选框与跨页注入的 hidden 字段，同一个 id 可能来两次
    return list(dict.fromkeys(sanitized))


def _owned_annotations(annotation_ids, current_user):
    return Annotation.objects.filter(
        pk__in=sanitize_annotation_ids(annotation_ids),
        bookmark__owner=current_user,
    )


def delete_annotations(annotation_ids, current_user) -> int:
    annotations = _owned_annotations(annotation_ids, current_user)
    count = annotations.count()
    annotations.delete()
    return count


def clear_annotation_notes(annotation_ids, current_user) -> int:
    # QuerySet.update 不触发 auto_now，排序/展示用的 date_modified 要显式写
    return _owned_annotations(annotation_ids, current_user).update(
        note_content="", date_modified=timezone.now()
    )


def set_annotation_color(annotation_ids, current_user, color: str) -> int:
    if color not in dict(Annotation.COLOR_CHOICES):
        return 0
    return _owned_annotations(annotation_ids, current_user).update(
        color=color, date_modified=timezone.now()
    )


def copy_annotations(annotation_ids, current_user) -> list[dict]:
    """复制需要选中项的原文与批注，前端按当前格式设置渲染。"""
    return list(
        _owned_annotations(annotation_ids, current_user)
        .order_by("date_created", "id")
        .values("selected_text", "note_content")
    )
