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


@register.inclusion_tag("bookmarks/random_sort.html", name="random_sort")
def random_sort(search):
    return {"search": search}
