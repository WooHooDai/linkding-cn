from django import template
from django.utils.translation import gettext as _

from bookmarks.models import (
    BookmarkSearch,
    BookmarkSearchForm,
    User,
)
from bookmarks.utils import parse_relative_date_string

register = template.Library()


@register.inclusion_tag(
    "bookmarks/search.html", name="bookmark_search", takes_context=True
)
def bookmark_search(context, search: BookmarkSearch, mode: str = ""):
    search_form = BookmarkSearchForm(search, editable_fields=["q"])

    if mode == "shared":
        preferences_form = BookmarkSearchForm(
            search,
            editable_fields=[
                "sort",
                "health_status",
                "date_filter_by",
                "date_filter_type",
                "date_filter_start",
                "date_filter_end",
                "date_filter_relative_string",
            ],
        )
    elif mode == "trash":
        preferences_form = BookmarkSearchForm(
            search,
            editable_fields=[
                "sort",
                "shared",
                "unread",
                "tagged",
                "health_status",
                "date_filter_by",
                "date_filter_type",
                "date_filter_start",
                "date_filter_end",
                "date_filter_relative_string",
            ],
        )
        deleted_date_label = _("Date deleted")
        trash_sort_choices = [
            (BookmarkSearch.SORT_DELETED_ASC, f"{deleted_date_label} ↑"),
            (BookmarkSearch.SORT_DELETED_DESC, f"{deleted_date_label} ↓"),
        ]
        trash_date_filter_choices = [
            (BookmarkSearch.FILTER_DATE_BY_DELETED, deleted_date_label),
        ]
        preferences_form.fields["sort"].choices = (
            trash_sort_choices + preferences_form.fields["sort"].choices
        )
        preferences_form.fields["date_filter_by"].choices = (
            preferences_form.fields["date_filter_by"].choices
            + trash_date_filter_choices
        )
    else:
        preferences_form = BookmarkSearchForm(
            search,
            editable_fields=[
                "sort",
                "shared",
                "unread",
                "tagged",
                "highlight",
                "annotation",
                "health_status",
                "date_filter_by",
                "date_filter_type",
                "date_filter_start",
                "date_filter_end",
                "date_filter_relative_string",
            ],
        )

    # 解析相对日期字符串，用于前端显示
    date_filter_relative_value, date_filter_relative_unit = parse_relative_date_string(
        search.date_filter_relative_string
    )

    return {
        "request": context["request"],
        "search": search,
        "search_form": search_form,
        "preferences_form": preferences_form,
        "mode": mode,
        "date_filter_relative_value": date_filter_relative_value,
        "date_filter_relative_unit": date_filter_relative_unit,
    }


@register.inclusion_tag(
    "bookmarks/user_select.html", name="user_select", takes_context=True
)
def user_select(context, search: BookmarkSearch, users: list[User]):
    sorted_users = sorted(users, key=lambda x: str.lower(x.username))
    form = BookmarkSearchForm(search, editable_fields=["user"], users=sorted_users)
    return {
        "search": search,
        "users": sorted_users,
        "form": form,
    }


# 允许通过随机按钮传递的查询参数白名单
# 包含所有 BookmarkSearch 的筛选参数（排除 sort，因为随机按钮会设置 sort=random）
# 参考：BookmarkSearch.params 模型定义
RANDOM_SORT_ALLOWED_PARAMS = {
    # 搜索和用户筛选
    "q",           # 搜索关键词
    "user",        # 用户筛选
    "bundle",      # 捆绑包 ID

    # 状态筛选
    "shared",      # 分享状态: off/yes/no
    "unread",      # 已读状态: off/yes/no
    "tagged",      # 标签状态: off/yes/no

    # 日期筛选（支持相对和绝对日期）
    "date_filter_by",              # 筛选字段: added/modified/deleted/highlight/annotation
    "date_filter_type",            # 日期类型: absolute/relative
    "date_filter_relative_string", # 相对日期字符串: last_7_days/this_month/...
    "date_filter_start",           # 绝对开始日期: YYYY-MM-DD
    "date_filter_end",             # 绝对结束日期: YYYY-MM-DD

    # 资源筛选
    "html_snapshot",  # HTML 快照: off/yes/no
    "preview_image",  # 预览图: off/yes/no
    "favicon",        # 图标: off/yes/no
    "highlight",      # 高亮: off/yes/no
    "annotation",     # 批注: off/yes/no

    # 时间筛选
    "modified_since",  # 修改时间
    "added_since",     # 添加时间
    "deleted_since",   # 删除时间
}


@register.inclusion_tag("bookmarks/random_sort.html", name="random_sort", takes_context=True)
def random_sort(context, search):
    """
    随机排序模板标签，生成包含所有筛选条件的随机排序按钮。

    从当前 URL 的查询参数中提取所有筛选条件，生成隐藏字段，
    确保点击随机按钮时保留所有筛选条件。

    Args:
        context: 模板上下文，包含 request 对象
        search: BookmarkSearch 对象，用于模板标签调用接口一致性
                （模板中使用 {% random_sort search %}）

    Returns:
        dict: 包含 search 和 filtered_params 的字典
              - search: BookmarkSearch 对象
              - filtered_params: 过滤后的查询参数字典
              - profile: UserProfile 对象（包含随机按钮设置）
    """
    request = context.get("request")
    # 空安全检查：如果 request 不存在，返回空参数
    if not request:
        return {"search": search, "filtered_params": {}, "profile": None}

    # 使用白名单过滤参数，排除空值，确保安全性
    filtered_params = {
        k: v
        for k, v in request.GET.items()
        if k in RANDOM_SORT_ALLOWED_PARAMS and v  # 排除空值
    }
    profile = getattr(request, "user_profile", None)
    return {"search": search, "filtered_params": filtered_params, "profile": profile}
