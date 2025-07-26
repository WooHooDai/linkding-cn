from typing import List

from django import template

from bookmarks.models import (
    BookmarkSearch,
    BookmarkSearchForm,
    User,
)

register = template.Library()


@register.inclusion_tag(
    "bookmarks/search.html", name="bookmark_search", takes_context=True
)
def bookmark_search(context, search: BookmarkSearch, mode: str = ""):
    search_form = BookmarkSearchForm(search, editable_fields=["q"])

    if mode == "shared":
        preferences_form = BookmarkSearchForm(search, editable_fields=["sort", "date_filter_type", "date_filter_start", "date_filter_end"])
    elif mode == "trash":
        # 回收站页面使用标准表单创建方式
        preferences_form = BookmarkSearchForm(
            search, editable_fields=["sort", "shared", "unread", "date_filter_type", "date_filter_start", "date_filter_end"]
        )
        # 为回收站页面添加删除相关的排序选项
        trash_sort_choices = [
            (BookmarkSearch.SORT_DELETED_ASC, "删除时间 ↑"),
            (BookmarkSearch.SORT_DELETED_DESC, "删除时间 ↓"),
        ]
        trash_date_filter_choices = [
            (BookmarkSearch.FILTER_DATE_DELETED, "删除"),
        ]
        preferences_form.fields["sort"].choices = trash_sort_choices + preferences_form.fields["sort"].choices
        preferences_form.fields["date_filter_type"].choices = preferences_form.fields["date_filter_type"].choices + trash_date_filter_choices
    else:
        preferences_form = BookmarkSearchForm(
            search, editable_fields=["sort", "shared", "unread", "date_filter_type", "date_filter_start", "date_filter_end"]
        )
    return {
        "request": context["request"],
        "search": search,
        "search_form": search_form,
        "preferences_form": preferences_form,
        "mode": mode,
    }


@register.inclusion_tag(
    "bookmarks/user_select.html", name="user_select", takes_context=True
)
def user_select(context, search: BookmarkSearch, users: List[User]):
    sorted_users = sorted(users, key=lambda x: str.lower(x.username))
    form = BookmarkSearchForm(search, editable_fields=["user"], users=sorted_users)
    return {
        "search": search,
        "users": sorted_users,
        "form": form,
    }


@register.inclusion_tag(
    "bookmarks/random_sort.html", name="random_sort"
)
def random_sort(search):
    return {"search": search}
