import hashlib
import calendar
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import Http404, QueryDict
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from pypinyin import Style, pinyin

from bookmarks import queries, utils
from bookmarks.services.icon_loader import load_quick_tags_icon
from bookmarks.models import (
    HEALTH_STATUS_CHOICES,
    HEALTH_STATUS_UNKNOWN,
    Annotation,
    Bookmark,
    BookmarkAsset,
    BookmarkBundle,
    BookmarkSearch,
    Tag,
    User,
    UserProfile,
)
from bookmarks.services.search_query_parser import (
    OrExpression,
    SearchQueryParseError,
    extract_tag_names_from_query,
    parse_search_query,
    strip_tag_from_query,
)
from bookmarks.services.wayback import generate_fallback_webarchive_url


def format_health_checked_at(checked_at: str) -> str:
    """将健康检查时间（ISO 字符串）转换为本地时区的展示格式。

    时区取环境变量 TZ（settings.TIME_ZONE），默认 Asia/Shanghai。
    返回形如 ``2026/09/05 22:33:05`` 的字符串；解析失败返回空串。
    """
    if not checked_at:
        return ""
    try:
        dt = datetime.fromisoformat(checked_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.UTC)
        local = dt.astimezone(ZoneInfo(settings.TIME_ZONE))
        return local.strftime("%Y/%m/%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError):
        return ""


def _health_reason_display(reason: str, http_status) -> str:
    """reason 与 HTTP 码重复时置空，避免界面重复显示。"""
    if reason and http_status and reason == f"HTTP {http_status}":
        return ""
    return reason


def _is_health_stale(checked_at: str, max_age_days: int) -> bool:
    """检查结果是否超过有效期（过期 = stale，界面按未检查弱化显示）。"""
    if not checked_at or max_age_days <= 0:
        return False
    try:
        dt = datetime.fromisoformat(checked_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.UTC)
        now = timezone.now()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        return (now - dt) > timedelta(days=max_age_days)
    except (ValueError, TypeError, OverflowError):
        return False


def _health_status_display(health_status) -> str:
    """NULL（未检查）派生为 unknown，其余映射 CHOICES 显示名。"""
    if health_status is None:
        return dict(HEALTH_STATUS_CHOICES).get(
            HEALTH_STATUS_UNKNOWN, "Unknown"
        )
    return dict(HEALTH_STATUS_CHOICES).get(health_status, health_status)
from bookmarks.type_defs import HttpRequest
from bookmarks.views import access

CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tag_sort_key(name: str):
    """Sort key that orders English tags alphabetically before CJK tags,
    with CJK tags sorted by their pinyin representation."""
    if name and CJK_RE.match(name[0]):
        py = pinyin(name, style=Style.FIRST_LETTER)
        pinyin_key = "".join(
            item[0].lower() if item and item[0] else char
            for item, char in zip(py, name, strict=False)
        )
        return (1, pinyin_key)
    return (0, name.lower())


class FaviconLookup:
    """将 hostname 解析为 favicon 规范域名（用于 /favicon/{domain} URL）。

    支持自定义域名别名展开。
    """

    def __init__(self, domain_config=None):
        self._domain_config = domain_config

    def get_domain(self, hostname: str) -> str:
        """将 hostname 解析为规范域名（用于 /favicon/{domain} URL）。"""
        if self._domain_config:
            from bookmarks.utils import resolve_favicon_domain

            return resolve_favicon_domain(hostname, config=self._domain_config)
        return hostname


class RequestContext:
    index_view = "linkding:bookmarks.index"
    action_view = "linkding:bookmarks.index.action"

    def __init__(self, request: HttpRequest):
        self.request = request
        self.index_url = reverse(self.index_view)
        self.action_url = reverse(self.action_view)
        self.query_params = request.GET.copy()
        self.query_params.pop("details", None)
        domain_config = utils.parse_domain_roots(
            request.user_profile.custom_domain_root
        )
        self._favicon_lookup = FaviconLookup(domain_config)

        self.query_is_valid = True
        self.query_error_message = None
        self.search_expression = None
        if not request.user_profile.legacy_search:
            try:
                self.search_expression = parse_search_query(request.GET.get("q"))
            except SearchQueryParseError as e:
                self.query_is_valid = False
                self.query_error_message = e.message

    def get_url(self, view_url: str, add: dict = None, remove: dict = None) -> str:
        query_params = self.query_params.copy()
        if add:
            query_params.update(add)
        if remove:
            for key in remove:
                query_params.pop(key, None)
        encoded_params = utils.clean_query_params(query_params)
        return view_url + "?" + encoded_params if encoded_params else view_url

    def index(self, add: dict = None, remove: dict = None) -> str:
        return self.get_url(self.index_url, add=add, remove=remove)

    def action(self, add: dict = None, remove: dict = None) -> str:
        return self.get_url(self.action_url, add=add, remove=remove)

    def details(self, bookmark_id: int) -> str:
        return self.get_url(self.index_url, add={"details": bookmark_id})

    def get_bookmark_query_set(self, search: BookmarkSearch):
        raise NotImplementedError("Must be implemented by subclass")

    def get_tag_query_set(self, search: BookmarkSearch):
        raise NotImplementedError("Must be implemented by subclass")


class ActiveBookmarksContext(RequestContext):
    index_view = "linkding:bookmarks.index"
    action_view = "linkding:bookmarks.index.action"

    def get_bookmark_query_set(self, search: BookmarkSearch):
        return queries.query_bookmarks(
            self.request.user, self.request.user_profile, search
        )

    def get_tag_query_set(self, search: BookmarkSearch):
        return queries.query_bookmark_tags(
            self.request.user, self.request.user_profile, search
        )


class ArchivedBookmarksContext(RequestContext):
    index_view = "linkding:bookmarks.archived"
    action_view = "linkding:bookmarks.archived.action"

    def get_bookmark_query_set(self, search: BookmarkSearch):
        return queries.query_archived_bookmarks(
            self.request.user, self.request.user_profile, search
        )

    def get_tag_query_set(self, search: BookmarkSearch):
        return queries.query_archived_bookmark_tags(
            self.request.user, self.request.user_profile, search
        )


class SharedBookmarksContext(RequestContext):
    index_view = "linkding:bookmarks.shared"
    action_view = "linkding:bookmarks.shared.action"

    def get_bookmark_query_set(self, search: BookmarkSearch):
        user = User.objects.filter(username=search.user).first()
        public_only = not self.request.user.is_authenticated
        return queries.query_shared_bookmarks(
            user, self.request.user_profile, search, public_only
        )

    def get_tag_query_set(self, search: BookmarkSearch):
        user = User.objects.filter(username=search.user).first()
        public_only = not self.request.user.is_authenticated
        return queries.query_shared_bookmark_tags(
            user, self.request.user_profile, search, public_only
        )


class BookmarkItem:
    def __init__(
        self,
        context: RequestContext,
        bookmark: Bookmark,
        user: User,
        profile: UserProfile,
    ) -> None:
        self.bookmark = bookmark

        is_editable = bookmark.owner == user
        self.is_editable = is_editable

        self.id = bookmark.id
        self.url = bookmark.url
        self.title = bookmark.resolved_title
        self.description = bookmark.resolved_description
        self.notes = bookmark.notes
        self.tag_names = bookmark.tag_names
        self.tag_names_set = set(bookmark.tag_names)
        self.tags = [AddTagItem(context, tag) for tag in bookmark.tags.all()]
        self.tags.sort(key=lambda item: item.name)
        self.has_snapshot = bool(bookmark.latest_snapshot_id)
        self.has_article = bool(bookmark.latest_article_id)
        self.web_archive_url = bookmark.web_archive_snapshot_url or ""
        self.web_archive_fallback_url = (
            bookmark.web_archive_snapshot_url
            or generate_fallback_webarchive_url(bookmark.url, bookmark.date_added)
        )
        self.reader_url = reverse("linkding:bookmarks.read", args=[bookmark.id])
        if bookmark.latest_snapshot_id:
            self.snapshot_url = reverse(
                "linkding:assets.view", args=[bookmark.latest_snapshot_id]
            )
            self.snapshot_title = "View latest snapshot"
        else:
            self.snapshot_url = bookmark.web_archive_snapshot_url
            self.snapshot_title = (
                "View snapshot on the Internet Archive Wayback Machine"
            )
            if not self.snapshot_url:
                self.snapshot_url = generate_fallback_webarchive_url(
                    bookmark.url, bookmark.date_added
                )
        hostname = utils.extract_hostname(bookmark.url)
        self.favicon_domain = context._favicon_lookup.get_domain(hostname)
        self.preview_image_remote_url = bookmark.preview_image_remote_url
        self.preview_image_file = bookmark.preview_image_file
        self.is_archived = bookmark.is_archived
        self.unread = bookmark.unread
        self.shared = bookmark.shared
        self.owner = bookmark.owner
        self.health_status = bookmark.health_status
        _health_details = bookmark.health_details or {}
        self.health_reason = _health_reason_display(
            _health_details.get("reason", ""), _health_details.get("http_status")
        )
        self.health_checked_at = _health_details.get("checked_at", "")
        self.health_http_status = _health_details.get("http_status")
        self.health_checked_at_display = format_health_checked_at(self.health_checked_at)
        self.health_status_display = _health_status_display(self.health_status)
        self.health_stale = _is_health_stale(
            self.health_checked_at, profile.health_max_age
        )
        self.health_display_status = (
            HEALTH_STATUS_UNKNOWN if self.health_stale else self.health_status
        )
        self.health_display_status_display = _health_status_display(self.health_display_status)
        self.details_url = context.details(bookmark.id)
        self.has_highlights = getattr(bookmark, "annotation_count", 0) > 0

        css_classes = []
        if bookmark.unread:
            css_classes.append("unread")
        if bookmark.shared:
            css_classes.append("shared")

        self.css_classes = " ".join(css_classes)

        if not bookmark.is_deleted:
            if (
                profile.bookmark_date_display
                == UserProfile.BOOKMARK_DATE_DISPLAY_RELATIVE
            ):
                self.display_date = utils.humanize_relative_date(bookmark.date_added)
            elif (
                profile.bookmark_date_display
                == UserProfile.BOOKMARK_DATE_DISPLAY_ABSOLUTE
            ):
                self.display_date = utils.humanize_absolute_date_short(
                    bookmark.date_added
                )
        else:
            # 若书签已被删除，则显示删除日期
            if (
                profile.bookmark_date_display
                == UserProfile.BOOKMARK_DATE_DISPLAY_RELATIVE
            ):
                self.display_date = utils.humanize_relative_date(bookmark.date_deleted)
            elif (
                profile.bookmark_date_display
                == UserProfile.BOOKMARK_DATE_DISPLAY_ABSOLUTE
            ):
                self.display_date = utils.humanize_absolute_date_short(
                    bookmark.date_deleted
                )


class SidebarSummaryStat:
    def __init__(
        self,
        key: str,
        label: str,
        value: int,
        url: str | None = None,
    ) -> None:
        self.key = key
        self.label = label
        self.value = value
        self.url = url


class SidebarCalendarDay:
    def __init__(
        self,
        value,
        day_number: int,
        count: int | None,
        url: str | None,
        level: int,
        title: str,
        is_available: bool = False,
        is_current_month: bool = True,
        is_selected: bool = False,
        is_in_range: bool = False,
        is_range_start: bool = False,
        is_range_end: bool = False,
        is_today: bool = False,
        has_bookmarks: bool = False,
        target_week: str | None = None,
    ) -> None:
        self.value = value
        self.day_number = day_number
        self.count = count
        self.url = url
        self.level = level
        self.title = title
        self.is_available = is_available
        self.is_current_month = is_current_month
        self.is_selected = is_selected
        self.is_in_range = is_in_range
        self.is_range_start = is_range_start
        self.is_range_end = is_range_end
        self.is_today = is_today
        self.has_bookmarks = has_bookmarks
        self.iso_value = value.isoformat()
        self.target_week = target_week


class SidebarUserSummaryContext:
    MODE_CALENDAR = "calendar"
    MODE_HEATMAP = "heatmap"
    PRESERVED_QUERY_PARAMS = ()
    RESET_SEARCH_PARAMS = (
        "q",
        "bundle",
        "shared",
        "unread",
        "tagged",
        "date_filter_by",
        "date_filter_type",
        "date_filter_relative_string",
        "date_filter_start",
        "date_filter_end",
    )

    def __init__(self, request: HttpRequest, search: BookmarkSearch) -> None:
        self.request = request
        self.search = search
        self.username = request.user.username
        self.mode = self._coerce_mode(request.user_profile.sum_mode)

        active_bookmarks = Bookmark.objects.filter(
            owner=request.user,
            is_archived=False,
            is_deleted=False,
        )
        oldest_bookmark = active_bookmarks.order_by("date_added").first()

        today = timezone.localdate()
        user_joined_day = timezone.localtime(request.user.date_joined).date()
        oldest_bookmark_day = (
            timezone.localtime(oldest_bookmark.date_added).date()
            if oldest_bookmark
            else None
        )
        self.selectable_start_day = oldest_bookmark_day or today
        self.collection_start_day = (
            min(user_joined_day, oldest_bookmark_day)
            if oldest_bookmark_day
            else user_joined_day
        )
        self.collection_days = (today - self.collection_start_day).days
        self.collection_start_label = self.collection_start_day.strftime("%Y/%m/%d")
        self.collection_start_prefix = _("Since")
        self.has_bookmarks = oldest_bookmark is not None
        self.show_weekdays = self._is_toggle_enabled("sum_show_weekdays")
        self.show_details = self._is_toggle_enabled("sum_show_details")
        self.selected_start, self.selected_end = self._get_selected_range()
        self.selected_start_iso = (
            self.selected_start.isoformat() if self.selected_start else ""
        )
        self.selected_end_iso = (
            self.selected_end.isoformat() if self.selected_end else ""
        )

        current_month_start = today.replace(day=1)
        earliest_month_start = self.selectable_start_day.replace(day=1)
        self.visible_month_start = self._resolve_visible_month(
            self._get_requested_month_value(),
            earliest_month_start,
            current_month_start,
        )
        self.visible_month_key = self.visible_month_start.strftime("%Y-%m")
        self.visible_month_label = self.visible_month_start.strftime("%Y/%m")
        self.visible_year = self.visible_month_start.year
        self.visible_month_number = self.visible_month_start.month
        self.current_month_key = current_month_start.strftime("%Y-%m")
        self.weekday_labels = self._build_weekday_labels()
        self.heatmap_weekday_labels = self._build_weekday_labels()
        self.calendar_year_options = self._build_calendar_year_options(
            earliest_month_start,
            current_month_start,
        )
        self.calendar_month_options = self._build_calendar_month_options(
            earliest_month_start,
            current_month_start,
        )
        self.current_week_start = today - timedelta(days=((today.weekday() + 1) % 7))
        self.earliest_week_start = self.selectable_start_day - timedelta(
            days=((self.selectable_start_day.weekday() + 1) % 7)
        )
        self.visible_week_start = self._resolve_visible_week(
            self._get_requested_week_value(),
            self.earliest_week_start,
            self.current_week_start,
        )
        self.visible_week_key = self._format_week_key(self.visible_week_start)
        self.visible_week_label = self._format_week_label(self.visible_week_start)
        self.current_week_key = self._format_week_key(self.current_week_start)
        self.visible_week_year, self.visible_week_number, _visible_weekday = (
            self.visible_week_start + timedelta(days=1)
        ).isocalendar()
        self.heatmap_year_groups = self._build_heatmap_year_groups()
        self.heatmap_year_options = self._build_heatmap_year_options()
        self.heatmap_week_options = self._build_heatmap_week_options()

        bookmarks_total = active_bookmarks.count()
        tags_total = Tag.objects.filter(owner=request.user).count()
        unread_total = active_bookmarks.filter(unread=True).count()

        stats_agg = Annotation.objects.filter(
            bookmark__owner=request.user,
            bookmark__is_archived=False,
            bookmark__is_deleted=False,
        ).aggregate(
            highlights=models.Count("id"),
            notes=models.Count("id", filter=models.Q(note_content__gt="")),
        )

        self.primary_stats = [
            SidebarSummaryStat(
                "bookmarks",
                _("Bookmarks"),
                bookmarks_total,
                self._build_url(reset_search=True),
            ),
            SidebarSummaryStat(
                "tags",
                _("Tags"),
                tags_total,
                reverse("linkding:tags.index"),
            ),
            SidebarSummaryStat("collection-days", _("Days"), self.collection_days),
            SidebarSummaryStat(
                "unread",
                _("Unread"),
                unread_total,
                self._build_url(reset_search=True, unread="yes"),
            ),
            SidebarSummaryStat(
                "highlights",
                _("Highlights"),
                stats_agg["highlights"],
                self._build_url(reset_search=True, highlight="yes"),
            ),
            SidebarSummaryStat(
                "annotations",
                _("Annotations"),
                stats_agg["notes"],
                self._build_url(reset_search=True, annotation="yes"),
            ),
        ]
        self.settings_options = self._build_settings_options()

        if self.mode == self.MODE_CALENDAR:
            self.mode_switch = {
                "key": self.MODE_HEATMAP,
                "action": "toggle_mode",
                "value": self.MODE_HEATMAP,
                "title": _("Heatmap"),
            }
        else:
            self.mode_switch = {
                "key": self.MODE_CALENDAR,
                "action": "toggle_mode",
                "value": self.MODE_CALENDAR,
                "title": _("Calendar"),
            }

        previous_month = self._shift_month(self.visible_month_start, -1)
        next_month = self._shift_month(self.visible_month_start, 1)
        self.previous_month_key = (
            previous_month.strftime("%Y-%m")
            if previous_month >= earliest_month_start
            else None
        )
        self.next_month_key = (
            next_month.strftime("%Y-%m") if next_month <= current_month_start else None
        )
        previous_week = self.visible_week_start - timedelta(days=7)
        next_week = self.visible_week_start + timedelta(days=7)
        self.previous_week_key = (
            self._format_week_key(previous_week)
            if previous_week >= self.earliest_week_start
            else None
        )
        self.next_week_key = (
            self._format_week_key(next_week)
            if next_week <= self.current_week_start
            else None
        )

        self.absolute_range_url = self._build_absolute_range_url()
        self.clear_range_url = self._build_url(
            reset_search=True,
            date_filter_by=None,
            date_filter_type=None,
            date_filter_relative_string=None,
            date_filter_start=None,
            date_filter_end=None,
        )
        self.calendar_weeks = self._build_calendar_weeks(active_bookmarks, today)
        self.heatmap_weeks = self._build_heatmap_weeks(active_bookmarks, today)
        self.heatmap_week_headers = self._build_heatmap_week_headers()
        self.toolbar_action = self._build_toolbar_action(current_month_start)
        self.activity_summary = self._build_activity_summary(active_bookmarks, today)

    def _build_calendar_weeks(self, active_bookmarks, today):
        calendar_weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(
            self.visible_month_start.year,
            self.visible_month_start.month,
        )
        start_day = calendar_weeks[0][0]
        end_day = calendar_weeks[-1][-1]
        daily_counts = self._load_daily_counts(active_bookmarks, start_day, end_day)

        weeks = []
        for week_days in calendar_weeks:
            week = []
            for value in week_days:
                is_current_month = (
                    value.year == self.visible_month_start.year
                    and value.month == self.visible_month_start.month
                )
                is_available = (
                    is_current_month
                    and self.selectable_start_day <= value <= today
                    and self.has_bookmarks
                )
                count = daily_counts.get(value, 0)
                week.append(
                    SidebarCalendarDay(
                        value=value,
                        day_number=value.day,
                        count=count,
                        url=self._build_absolute_day_url(value)
                        if is_available
                        else None,
                        level=self._heatmap_level(count) if is_current_month else 0,
                        title=self._build_day_title(value, count),
                        is_available=is_available,
                        is_current_month=is_current_month,
                        is_selected=self._is_selected_day(value),
                        is_in_range=self._is_in_selected_range(value, is_current_month),
                        is_range_start=self.selected_start == value,
                        is_range_end=self.selected_end == value,
                        is_today=value == today,
                        has_bookmarks=count > 0,
                    )
                )
            weeks.append(week)

        return weeks

    def _build_heatmap_weeks(self, active_bookmarks, today):
        if not self.has_bookmarks:
            return []

        heatmap_end = self.visible_week_start + timedelta(days=6)
        heatmap_start = self.visible_week_start - timedelta(days=(7 * 14))
        daily_counts = self._load_daily_counts(
            active_bookmarks, heatmap_start, heatmap_end
        )

        weeks = []
        week_start = heatmap_start
        while week_start <= heatmap_end:
            week_url = (
                self._build_absolute_week_url(week_start, today)
                if self.selectable_start_day
                <= min(today, week_start + timedelta(days=6))
                else None
            )
            week = []
            for offset in range(7):
                value = week_start + timedelta(days=offset)
                is_available = self.selectable_start_day <= value <= today
                count = daily_counts.get(value, 0) if is_available else 0
                week.append(
                    SidebarCalendarDay(
                        value=value,
                        day_number=value.day,
                        count=count,
                        url=week_url if is_available else None,
                        level=self._heatmap_level(count) if is_available else 0,
                        title=self._build_day_title(value, count),
                        is_available=is_available,
                        is_current_month=is_available,
                        is_selected=self._is_selected_day(value),
                        is_in_range=self._is_in_selected_range(value, is_available),
                        is_range_start=self.selected_start == value,
                        is_range_end=self.selected_end == value,
                        is_today=value == today,
                        has_bookmarks=count > 0,
                        target_week=self._format_week_key(week_start),
                    )
                )
            weeks.append(week)
            week_start += timedelta(days=7)

        return weeks

    @staticmethod
    def _load_daily_counts(
        active_bookmarks, start_day: date, end_day: date
    ) -> dict[date, int]:
        return {
            row["day"]: row["total"]
            for row in (
                active_bookmarks.filter(
                    date_added__date__gte=start_day,
                    date_added__date__lte=end_day,
                )
                .annotate(
                    day=TruncDate("date_added", tzinfo=timezone.get_current_timezone())
                )
                .values("day")
                .annotate(total=models.Count("id"))
                .order_by("day")
            )
        }

    @staticmethod
    def _heatmap_level(count: int) -> int:
        if count <= 0:
            return 0
        if count <= 3:
            return 1
        if count <= 6:
            return 2
        if count <= 9:
            return 3
        if count <= 15:
            return 4
        if count <= 20:
            return 5
        return 6

    @staticmethod
    def _coerce_mode(value: str | None) -> str:
        if value == SidebarUserSummaryContext.MODE_HEATMAP:
            return SidebarUserSummaryContext.MODE_HEATMAP
        return SidebarUserSummaryContext.MODE_CALENDAR

    def _get_requested_month_value(self) -> str | None:
        sum_month = self.request.GET.get("sum_month")
        if sum_month:
            return sum_month

        if self.selected_end:
            return self.selected_end.strftime("%Y-%m")

        return None

    def _get_requested_week_value(self) -> str | None:
        sum_week = self.request.GET.get("sum_week")
        if sum_week:
            return sum_week

        if self.selected_end:
            return self._format_week_key(self._start_of_week(self.selected_end))

        return None

    def _resolve_visible_month(
        self, value: str | None, earliest_month_start: date, current_month_start: date
    ) -> date:
        parsed_month = self._parse_month(value) or current_month_start
        if parsed_month < earliest_month_start:
            return earliest_month_start
        if parsed_month > current_month_start:
            return current_month_start
        return parsed_month

    def _resolve_visible_week(
        self, value: str | None, earliest_week_start: date, current_week_start: date
    ) -> date:
        parsed_week = self._parse_week(value) or current_week_start
        if parsed_week < earliest_week_start:
            return earliest_week_start
        if parsed_week > current_week_start:
            return current_week_start
        return parsed_week

    def _is_selected_day(self, value) -> bool:
        return self.selected_start == value and self.selected_end == value

    def _is_in_selected_range(self, value: date, is_current_month: bool) -> bool:
        return bool(
            is_current_month
            and self.selected_start
            and self.selected_end
            and self.selected_start <= value <= self.selected_end
        )

    def _get_selected_range(self) -> tuple[date | None, date | None]:
        start = self._coerce_date(self.search.date_filter_start)
        end = self._coerce_date(self.search.date_filter_end)
        if not (
            self.search.date_filter_by == BookmarkSearch.FILTER_DATE_BY_ADDED
            and start
            and end
        ):
            return None, None
        if start <= end:
            return start, end
        return end, start

    @staticmethod
    def _coerce_date(value):
        if hasattr(value, "isoformat") and not isinstance(value, str):
            return value
        if isinstance(value, str) and value:
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_month(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m").date().replace(day=1)
        except ValueError:
            return None

    @staticmethod
    def _parse_week(value: str | None) -> date | None:
        if not value:
            return None
        try:
            year, week = value.split("-W", maxsplit=1)
            return datetime.fromisocalendar(int(year), int(week), 1).date() - timedelta(
                days=1
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _start_of_week(value: date) -> date:
        return value - timedelta(days=((value.weekday() + 1) % 7))

    @staticmethod
    def _shift_month(month_start: date, offset: int) -> date:
        month_index = (month_start.year * 12) + (month_start.month - 1) + offset
        year, month_offset = divmod(month_index, 12)
        return date(year, month_offset + 1, 1)

    @staticmethod
    def _format_week_key(value: date) -> str:
        iso_year, iso_week, _ = (value + timedelta(days=1)).isocalendar()
        return f"{iso_year}-W{iso_week:02d}"

    @staticmethod
    def _format_week_label(value: date) -> str:
        iso_year, iso_week, _ = (value + timedelta(days=1)).isocalendar()
        return f"{iso_year}/W{iso_week:02d}"

    def _build_day_title(self, value: date, count: int) -> str:
        day_label = value.strftime("%Y/%m/%d")
        return ngettext(
            "%(count)s bookmark - %(date)s",
            "%(count)s bookmarks - %(date)s",
            count,
        ) % {"count": count, "date": day_label}

    def _build_absolute_day_url(self, value: date) -> str:
        return self._build_url(
            reset_search=True,
            date_filter_by=BookmarkSearch.FILTER_DATE_BY_ADDED,
            date_filter_type=BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
            date_filter_relative_string=None,
            date_filter_start=value.isoformat(),
            date_filter_end=value.isoformat(),
        )

    def _build_absolute_week_url(self, week_start: date, today: date) -> str:
        range_start = max(self.selectable_start_day, week_start)
        range_end = min(today, week_start + timedelta(days=6))
        return self._build_url(
            reset_search=True,
            date_filter_by=BookmarkSearch.FILTER_DATE_BY_ADDED,
            date_filter_type=BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
            date_filter_relative_string=None,
            date_filter_start=range_start.isoformat(),
            date_filter_end=range_end.isoformat(),
        )

    def _build_absolute_range_url(self) -> str:
        return self._build_url(
            reset_search=True,
            date_filter_by=BookmarkSearch.FILTER_DATE_BY_ADDED,
            date_filter_type=BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
            date_filter_relative_string=None,
            date_filter_start=None,
            date_filter_end=None,
        )

    def _build_calendar_year_options(
        self, earliest_month_start: date, current_month_start: date
    ):
        options = []
        for year in range(current_month_start.year, earliest_month_start.year - 1, -1):
            target_month = self._pick_month_for_year(
                year,
                earliest_month_start,
                current_month_start,
            )
            month_key = target_month.strftime("%Y-%m")
            options.append(
                {
                    "value": year,
                    "label": str(year),
                    "action": "nav_month",
                    "month_key": month_key,
                    "is_selected": year == self.visible_year,
                }
            )
        return options

    def _build_calendar_month_options(
        self, earliest_month_start: date, current_month_start: date
    ):
        options = []
        for month in range(1, 13):
            month_start = date(self.visible_year, month, 1)
            if not earliest_month_start <= month_start <= current_month_start:
                continue
            month_key = month_start.strftime("%Y-%m")
            options.append(
                {
                    "value": month,
                    "label": f"{month:02d}",
                    "action": "nav_month",
                    "month_key": month_key,
                    "is_selected": month == self.visible_month_number,
                }
            )
        return options

    def _pick_month_for_year(
        self,
        year: int,
        earliest_month_start: date,
        current_month_start: date,
    ) -> date:
        available_months = [
            date(year, month, 1)
            for month in range(1, 13)
            if earliest_month_start <= date(year, month, 1) <= current_month_start
        ]
        if not available_months:
            return self.visible_month_start

        for month_start in available_months:
            if month_start.month == self.visible_month_number:
                return month_start

        earlier_months = [
            month_start
            for month_start in available_months
            if month_start.month < self.visible_month_number
        ]
        if earlier_months:
            return earlier_months[-1]
        return available_months[0]

    def _build_heatmap_week_headers(self):
        headers = []
        week_start = self.visible_week_start - timedelta(days=(7 * 14))
        for _week_index in range(15):
            iso_year, iso_week, _iso_weekday = (
                week_start + timedelta(days=1)
            ).isocalendar()
            headers.append(
                {
                    "label": f"{iso_week:02d}",
                    "full_label": f"{iso_year}/W{iso_week:02d}",
                    "is_anchor": week_start == self.visible_week_start,
                }
            )
            week_start += timedelta(days=7)
        return headers

    def _build_heatmap_year_groups(self):
        groups: dict[int, list[dict]] = {}
        week_start = self.earliest_week_start
        while week_start <= self.current_week_start:
            iso_year, iso_week, _ = (week_start + timedelta(days=1)).isocalendar()
            groups.setdefault(iso_year, []).append(
                {
                    "year": iso_year,
                    "week_number": iso_week,
                    "label": f"W{iso_week:02d}",
                    "start": week_start,
                    "key": self._format_week_key(week_start),
                }
            )
            week_start += timedelta(days=7)
        return groups

    def _build_heatmap_year_options(self):
        options = []
        for year in sorted(self.heatmap_year_groups.keys(), reverse=True):
            target_week = self._pick_week_for_year(year)
            week_key = self._format_week_key(target_week)
            options.append(
                {
                    "value": year,
                    "label": str(year),
                    "action": "nav_week",
                    "week_key": week_key,
                    "is_selected": year == self.visible_week_year,
                }
            )
        return options

    def _build_heatmap_week_options(self):
        options = []
        for week in reversed(self.heatmap_year_groups.get(self.visible_week_year, [])):
            options.append(
                {
                    "value": week["week_number"],
                    "label": week["label"],
                    "action": "nav_week",
                    "week_key": week["key"],
                    "is_selected": week["start"] == self.visible_week_start,
                }
            )
        return options

    def _pick_week_for_year(self, year: int) -> date:
        year_weeks = self.heatmap_year_groups.get(year, [])
        if not year_weeks:
            return self.visible_week_start

        for week in year_weeks:
            if week["week_number"] == self.visible_week_number:
                return week["start"]

        earlier_weeks = [
            week
            for week in year_weeks
            if week["week_number"] < self.visible_week_number
        ]
        if earlier_weeks:
            return earlier_weeks[-1]["start"]
        return year_weeks[0]["start"]

    def _build_toolbar_action(self, current_month_start: date):
        if self.selected_start and self.selected_end:
            return {
                "key": "reset-range",
                "url": self.clear_range_url,
                "title": _("Reset date range"),
                "icon": "reset",
            }

        if self.mode == self.MODE_CALENDAR:
            if self.visible_month_start != current_month_start:
                return {
                    "key": "current-month",
                    "action": "nav_month",
                    "value": self.current_month_key,
                    "title": _("Go to current month"),
                    "icon": "target",
                }
            return None

        if self.visible_week_start != self.current_week_start:
            return {
                "key": "current-week",
                "action": "nav_week",
                "value": self.current_week_key,
                "title": _("Go to current week"),
                "icon": "target",
            }
        return None

    def _build_weekday_labels(self) -> tuple[str, ...]:
        return (
            _("Sun"),
            _("Mon"),
            _("Tue"),
            _("Wed"),
            _("Thu"),
            _("Fri"),
            _("Sat"),
        )

    def _build_settings_options(self):
        return [
            {
                "key": "weekdays",
                "label": _("Hide weekdays")
                if self.show_weekdays
                else _("Show weekdays"),
                "action": "toggle_show_weekdays",
                "value": "0" if self.show_weekdays else "1",
            },
            {
                "key": "details",
                "label": _("Hide summary") if self.show_details else _("Show summary"),
                "action": "toggle_show_details",
                "value": "0" if self.show_details else "1",
            },
        ]

    def _is_toggle_enabled(self, key: str) -> bool:
        return bool(getattr(self.request.user_profile, key, False))

    def _get_visible_heatmap_month_start(self, today: date) -> date:
        reference_day = min(today, self.visible_week_start + timedelta(days=6))
        return reference_day.replace(day=1)

    def _get_calendar_mode_switch_week_start(self, today: date) -> date:
        if self.selected_end:
            return self._start_of_week(self.selected_end)

        _, visible_month_last_day = calendar.monthrange(
            self.visible_month_start.year, self.visible_month_start.month
        )
        reference_day = min(
            today,
            self.visible_month_start.replace(day=visible_month_last_day),
        )
        return self._start_of_week(reference_day)

    @staticmethod
    def _calculate_longest_streak(
        daily_counts: dict[date, int], period_start: date, period_end: date
    ) -> int:
        longest_streak = 0
        current_streak = 0
        current_day = period_start
        while current_day <= period_end:
            if daily_counts.get(current_day, 0) > 0:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 0
            current_day += timedelta(days=1)
        return longest_streak

    def _resolve_activity_summary_period(self, today: date) -> tuple[date, date, str]:
        if self.selected_start and self.selected_end:
            period_start = self.selected_start
            period_end = self.selected_end
            lead = _("Selected range ({start} - {end}):").format(
                start=period_start.strftime("%Y/%m/%d"),
                end=period_end.strftime("%Y/%m/%d"),
            )
            return period_start, period_end, lead

        if self.mode == self.MODE_HEATMAP:
            period_start = self.visible_week_start
            period_end = self.visible_week_start + timedelta(days=6)
            lead_template = (
                _("This week ({start} - {end}):")
                if self.visible_week_start == self.current_week_start
                else _("Shown week ({start} - {end}):")
            )
            lead = lead_template.format(
                start=period_start.strftime("%Y/%m/%d"),
                end=period_end.strftime("%Y/%m/%d"),
            )
            return period_start, period_end, lead

        period_start = self.visible_month_start
        _weekday_index, last_day = calendar.monthrange(
            period_start.year, period_start.month
        )
        period_end = period_start.replace(day=last_day)
        lead_template = (
            _("This month ({start} - {end}):")
            if period_start == today.replace(day=1)
            else _("Shown month ({start} - {end}):")
        )
        lead = lead_template.format(
            start=period_start.strftime("%Y/%m/%d"),
            end=period_end.strftime("%Y/%m/%d"),
        )
        return period_start, period_end, lead

    @staticmethod
    @staticmethod
    def _build_activity_count_fragment(
        translated_text: str, count: int
    ) -> dict[str, str | int]:
        count_token = "__count__"
        template = translated_text % {"count": count_token}
        prefix, _separator, suffix = template.partition(count_token)
        return {
            "count": count,
            "prefix": prefix,
            "suffix": suffix,
            "text": translated_text % {"count": count},
        }

    @staticmethod
    def _build_activity_count_html(
        fragment: dict[str, str | int], url: str | None = None
    ):
        if url and fragment["count"] > 0:
            return format_html(
                '{}<a class="summary-activity-summary-value" href="{}">{}</a>{}',
                fragment["prefix"],
                url,
                fragment["count"],
                fragment["suffix"],
            )
        return format_html(
            '{}<strong class="summary-activity-summary-value">{}</strong>{}',
            fragment["prefix"],
            fragment["count"],
            fragment["suffix"],
        )

    def _build_activity_summary(self, active_bookmarks, today: date):
        period_start, period_end, lead = self._resolve_activity_summary_period(today)
        daily_counts = self._load_daily_counts(
            active_bookmarks, period_start, period_end
        )
        bookmark_total = sum(daily_counts.values())
        active_days = sum(1 for count in daily_counts.values() if count > 0)
        longest_streak = self._calculate_longest_streak(
            daily_counts, period_start, period_end
        )
        bookmark_fragment = self._build_activity_count_fragment(
            ngettext(
                "Bookmarked %(count)s item",
                "Bookmarked %(count)s items",
                bookmark_total,
            ),
            bookmark_total,
        )
        active_days_fragment = self._build_activity_count_fragment(
            ngettext(
                "active on %(count)s day",
                "active on %(count)s days",
                active_days,
            ),
            active_days,
        )
        longest_streak_fragment = self._build_activity_count_fragment(
            ngettext(
                "longest streak %(count)s day",
                "longest streak %(count)s days",
                longest_streak,
            ),
            longest_streak,
        )
        # 高亮和批注统计（按高亮/批注自身的创建日期筛选）
        annotations_agg = Annotation.objects.filter(
            bookmark__owner=self.request.user,
            bookmark__is_archived=False,
            bookmark__is_deleted=False,
            date_created__date__gte=period_start,
            date_created__date__lte=period_end,
        ).aggregate(
            highlights=models.Count("id"),
            notes=models.Count("id", filter=models.Q(note_content__gt="")),
        )
        highlights_total = annotations_agg["highlights"]
        notes_total = annotations_agg["notes"]
        highlights_fragment = self._build_activity_count_fragment(
            ngettext(
                "Added %(count)s highlight",
                "Added %(count)s highlights",
                highlights_total,
            ),
            highlights_total,
        )
        notes_fragment = self._build_activity_count_fragment(
            ngettext(
                "%(count)s annotation",
                "%(count)s annotations",
                notes_total,
            ),
            notes_total,
        )
        # 高亮/批注可点击跳转的 URL（带日期范围 + 过滤条件）
        date_range = dict(
            date_filter_type=BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE,
            date_filter_relative_string=None,
            date_filter_start=period_start.isoformat(),
            date_filter_end=period_end.isoformat(),
        )
        highlights_url = self._build_url(
            reset_search=True,
            date_filter_by=BookmarkSearch.FILTER_DATE_BY_HIGHLIGHT,
            highlight="yes",
            **date_range,
        )
        notes_url = self._build_url(
            reset_search=True,
            date_filter_by=BookmarkSearch.FILTER_DATE_BY_ANNOTATION,
            annotation="yes",
            **date_range,
        )
        copy_text = _("{bookmarks}, {days}, {streak}. {highlights}, {notes}.").format(
            bookmarks=bookmark_fragment["text"],
            days=active_days_fragment["text"],
            streak=longest_streak_fragment["text"],
            highlights=highlights_fragment["text"],
            notes=notes_fragment["text"],
        )
        text = _("{lead} {copy}").format(
            lead=lead,
            copy=copy_text,
        )
        return {
            "lead": lead,
            "bookmark_total": bookmark_total,
            "active_days": active_days,
            "longest_streak": longest_streak,
            "highlights_total": highlights_total,
            "notes_total": notes_total,
            "copy_html": format_html(
                _("{bookmarks}, {days}, {streak}. {highlights}, {notes}."),
                bookmarks=self._build_activity_count_html(bookmark_fragment),
                days=self._build_activity_count_html(active_days_fragment),
                streak=self._build_activity_count_html(longest_streak_fragment),
                highlights=self._build_activity_count_html(
                    highlights_fragment, url=highlights_url
                ),
                notes=self._build_activity_count_html(notes_fragment, url=notes_url),
            ),
            "text": text,
        }

    def _build_url(self, reset_search: bool = False, **updates) -> str:
        if reset_search:
            query_params = QueryDict("", mutable=True)
            for key in self.PRESERVED_QUERY_PARAMS:
                value = self.request.GET.get(key)
                if value:
                    query_params[key] = value
        else:
            query_params = self.request.GET.copy()

        for key in ("page", "details"):
            query_params.pop(key, None)

        if reset_search:
            for key in self.RESET_SEARCH_PARAMS:
                query_params.pop(key, None)

        for key, value in updates.items():
            if value in (None, ""):
                query_params.pop(key, None)
            else:
                query_params[key] = value

        encoded_params = utils.clean_query_params(query_params)
        base_url = reverse("linkding:bookmarks.index")
        return base_url + "?" + encoded_params if encoded_params else base_url


class BookmarkListContext:
    request_context = RequestContext

    def __init__(self, request: HttpRequest, search: BookmarkSearch) -> None:
        request_context = self.request_context(request)
        user = request.user
        user_profile = request.user_profile

        self.request = request
        self.search = search
        self.query_is_valid = request_context.query_is_valid
        self.query_error_message = request_context.query_error_message

        query_set = request_context.get_bookmark_query_set(self.search)
        page_number = request.GET.get("page")
        paginator = Paginator(query_set, user_profile.items_per_page)
        bookmarks_page = paginator.get_page(page_number)
        # Prefetch related objects, this avoids n+1 queries when accessing fields in templates
        models.prefetch_related_objects(bookmarks_page.object_list, "owner", "tags")

        # 仅当日期路由为高亮时，才计算当前页面书签的高亮计数
        if (
            user_profile.bookmark_date_route
            == UserProfile.BOOKMARK_DATE_ROUTE_HIGHLIGHTS
            and bookmarks_page.object_list
        ):
            from django.db.models import Count

            bookmark_ids = [b.id for b in bookmarks_page.object_list]
            annotation_counts = dict(
                Bookmark.objects.filter(id__in=bookmark_ids)
                .annotate(cnt=Count("annotations", distinct=True))
                .values_list("id", "cnt")
            )
            for bookmark in bookmarks_page.object_list:
                bookmark.annotation_count = annotation_counts.get(bookmark.id, 0)

        self.items = [
            BookmarkItem(request_context, bookmark, user, user_profile)
            for bookmark in bookmarks_page
        ]
        self.is_empty = paginator.count == 0
        self.bookmarks_page = bookmarks_page
        self.bookmarks_total = paginator.count

        self.return_url = request_context.index()
        self.action_url = request_context.action()

        self.link_target = user_profile.bookmark_link_target
        self.date_display = user_profile.bookmark_date_display
        self.date_route = user_profile.bookmark_date_route
        self.description_display = user_profile.bookmark_description_display
        self.description_max_lines = user_profile.bookmark_description_max_lines
        self.show_url = user_profile.display_url
        self.action_list = [
            {
                "key": item["key"],
                "enabled": item["enabled"],
                "label": user_profile.ACTION_LABELS[item["key"]],
            }
            for item in user_profile.get_bookmark_actions()
        ]
        self.action_display_mode = user_profile.bookmark_action_display_mode
        self.status_display_mode = user_profile.bookmark_status_display_mode
        self.quick_edit_display_mode = user_profile.bookmark_quick_edit_display_mode
        self.has_visible_actions = any(item["enabled"] for item in self.action_list)
        self.status_list = [
            {
                "key": item["key"],
                "enabled": item["enabled"],
                "label": user_profile.STATUS_LABELS[item["key"]],
            }
            for item in user_profile.get_bookmark_statuses()
        ]
        self.has_visible_statuses = any(item["enabled"] for item in self.status_list)
        self.quick_edit_list = [
            {
                "key": item["key"],
                "enabled": item["enabled"],
                "label": user_profile.QUICK_EDIT_LABELS[item["key"]],
            }
            for item in user_profile.get_bookmark_quick_edits()
        ]
        self.has_visible_quick_edits = any(
            item["enabled"] for item in self.quick_edit_list
        )
        quick_tags = user_profile.get_bookmark_quick_tags()
        self.quick_tags_direct = [
            {**qt, "index": i}
            for i, qt in enumerate(quick_tags)
            if qt["enabled"] and qt["tag_names"] and qt["display_position"] == "direct"
        ]
        self.quick_tags_submenu = [
            {**qt, "index": i}
            for i, qt in enumerate(quick_tags)
            if qt["enabled"] and qt["tag_names"] and qt["display_position"] == "submenu"
        ]
        self.has_quick_tags = bool(self.quick_tags_direct or self.quick_tags_submenu)
        # 为所有快捷标签加载图标 SVG 数据（内存缓存 → 本地文件 → API）
        icon_data_map = {}
        for qt in self.quick_tags_direct:
            icon_name = qt.get("icon_name")
            if icon_name:
                qt["icon_data"] = load_quick_tags_icon(icon_name)
                if qt["icon_data"]:
                    icon_data_map[icon_name] = qt["icon_data"]
            else:
                qt["icon_data"] = None
        # JSON 数据供前端 JS 使用（submenu）
        if self.quick_tags_submenu:
            submenu_data = []
            for qt in self.quick_tags_submenu:
                icon_name = qt["icon_name"] or "tabler:hash"
                icon_data = load_quick_tags_icon(icon_name)
                submenu_data.append(
                    {
                        "tagName": " ".join(qt["tag_names"]),
                        "tagNames": qt["tag_names"],
                        "label": qt["label"] or "Unnamed",
                        "shortLabel": qt["short_label"],
                        "iconName": icon_name,
                        "iconData": icon_data,
                        "displayMode": qt["display_mode"],
                    }
                )
                if icon_data:
                    icon_data_map[icon_name] = icon_data
            self.quick_tags_submenu_json = json.dumps(submenu_data, ensure_ascii=False)
        else:
            self.quick_tags_submenu_json = None
        # 工具栏模块顺序（用户可拖拽自定义），含各模块是否有可见内容的标记
        self.has_date_display = (
            user_profile.bookmark_date_display
            != UserProfile.BOOKMARK_DATE_DISPLAY_HIDDEN
        )
        self.toolbar_items = [
            {
                "key": module["key"],
                "has_content": {
                    UserProfile.TOOLBAR_MODULE_DATE: self.has_date_display,
                    UserProfile.TOOLBAR_MODULE_ACTIONS: self.has_visible_actions,
                    UserProfile.TOOLBAR_MODULE_QUICK_EDITS: self.has_visible_quick_edits,
                    UserProfile.TOOLBAR_MODULE_QUICK_TAGS: self.has_quick_tags,
                    UserProfile.TOOLBAR_MODULE_STATUSES: self.has_visible_statuses,
                }.get(module["key"], False),
            }
            for module in user_profile.get_bookmark_toolbar_modules()
            if module["enabled"]
        ]
        self.sharing_enabled = (
            user_profile.enable_sharing or user_profile.enable_public_sharing
        )
        self.show_favicons = user_profile.enable_favicons
        self.show_preview_images = user_profile.enable_preview_images
        self.show_preview_image_placeholders = (
            user_profile.enable_preview_image_placeholders
        )
        self.show_notes = user_profile.permanent_notes
        self.show_sidebar = user_profile.show_sidebar
        self.toolbar_auto_hide = user_profile.bookmark_toolbar_auto_hide
        self.is_preview = False
        self.snapshot_feature_enabled = settings.LD_ENABLE_SNAPSHOTS

    @staticmethod
    def generate_return_url(search: BookmarkSearch, base_url: str, page: int = None):
        query_params = search.query_params
        if page is not None:
            query_params["page"] = page
        query_string = utils.clean_query_params(query_params)

        return base_url if query_string == "" else base_url + "?" + query_string

    @staticmethod
    def generate_action_url(
        search: BookmarkSearch, base_action_url: str, return_url: str
    ):
        query_params = search.query_params
        query_params["return_url"] = return_url
        query_string = utils.clean_query_params(query_params)

        return (
            base_action_url
            if query_string == ""
            else base_action_url + "?" + query_string
        )


class ActiveBookmarkListContext(BookmarkListContext):
    request_context = ActiveBookmarksContext


class ArchivedBookmarkListContext(BookmarkListContext):
    request_context = ArchivedBookmarksContext


class SharedBookmarkListContext(BookmarkListContext):
    request_context = SharedBookmarksContext


class AddTagItem:
    def __init__(self, context: RequestContext, tag: Tag):
        self.tag = tag
        self.name = tag.name

        if context.request.user_profile.legacy_search:
            self.query_string = self._generate_query_string_legacy(context, tag)
        else:
            self.query_string = self._generate_query_string(context, tag)

    @staticmethod
    def _generate_query_string(context: RequestContext, tag: Tag) -> str:
        params = context.query_params.copy()
        query_with_tag = params.get("q", "")
        profile = context.request.user_profile

        selected_tags = {
            tag_name.lower()
            for tag_name in extract_tag_names_from_query(query_with_tag, profile)
        }
        if tag.name.lower() not in selected_tags:
            if isinstance(context.search_expression, OrExpression):
                # If the current search expression is an OR expression, wrap in parentheses
                query_with_tag = f"({query_with_tag})"
            query_with_tag = f"{query_with_tag} #{tag.name}".strip()

        params["q"] = query_with_tag
        params.pop("details", None)
        params.pop("page", None)

        encoded = utils.clean_query_params(params)
        return f"?{encoded}" if encoded else context.index_url

    @staticmethod
    def _generate_query_string_legacy(context: RequestContext, tag: Tag) -> str:
        params = context.query_params.copy()
        query_with_tag = params.get("q", "")
        parsed_query = queries.parse_query_string(query_with_tag)
        selected_tags = parsed_query["tag_names"]
        if context.request.user_profile.tag_search == UserProfile.TAG_SEARCH_LAX:
            selected_tags = selected_tags + parsed_query["search_terms"]
        selected_tags = {tag_name.lower() for tag_name in selected_tags}

        if tag.name.lower() not in selected_tags:
            query_with_tag = f"{query_with_tag} #{tag.name}".strip()

        params["q"] = query_with_tag
        params.pop("details", None)
        params.pop("page", None)

        encoded = utils.clean_query_params(params)
        return f"?{encoded}" if encoded else context.index_url


class RemoveTagItem:
    def __init__(self, context: RequestContext, tag: Tag):
        self.tag = tag
        self.name = tag.name

        if context.request.user_profile.legacy_search:
            self.query_string = self._generate_query_string_legacy(context, tag)
        else:
            self.query_string = self._generate_query_string(context, tag)

    @staticmethod
    def _generate_query_string(context: RequestContext, tag: Tag) -> str:
        params = context.query_params.copy()
        query = params.get("q", "")
        profile = context.request.user_profile
        query_without_tag = strip_tag_from_query(query, tag.name, profile)

        params["q"] = query_without_tag
        params.pop("details", None)
        params.pop("page", None)

        encoded = utils.clean_query_params(params)
        return f"?{encoded}" if encoded else context.index_url

    @staticmethod
    def _generate_query_string_legacy(context: RequestContext, tag: Tag) -> str:
        params = context.request.GET.copy()
        if params.__contains__("q"):
            # Split query string into parts
            query_string = params.__getitem__("q")
            query_parts = query_string.split()
            # Remove tag with hash
            tag_name_with_hash = "#" + tag.name
            query_parts = [
                part
                for part in query_parts
                if str.lower(part) != str.lower(tag_name_with_hash)
            ]
            # When using lax tag search, also remove tag without hash
            profile = context.request.user_profile
            if profile.tag_search == UserProfile.TAG_SEARCH_LAX:
                query_parts = [
                    part
                    for part in query_parts
                    if str.lower(part) != str.lower(tag.name)
                ]
            # Rebuild query string
            query_string = " ".join(query_parts)
            params.__setitem__("q", query_string)

        # Remove details ID and page number
        params.pop("details", None)
        params.pop("page", None)

        encoded = utils.clean_query_params(params)
        return f"?{encoded}" if encoded else context.index_url


class TagGroup:
    def __init__(self, context: RequestContext, char: str, is_cjk: bool = False):
        self.context = context
        self.tags = []
        self.char = char  # Group header letter
        self.is_cjk = is_cjk  # Is this a Chinese (CJK) group

    def __repr__(self):
        return f"<{self.char}{' CJK' if self.is_cjk else ''} TagGroup>"

    def add_tag(self, tag: Tag):
        self.tags.append(AddTagItem(self.context, tag))

    @staticmethod
    def create_tag_groups(context: RequestContext, mode: str, tags: set[Tag]):
        if mode == UserProfile.TAG_GROUPING_ALPHABETICAL:
            return TagGroup._create_tag_groups_alphabetical(context, tags)
        elif mode == UserProfile.TAG_GROUPING_DISABLED:
            return TagGroup._create_tag_groups_disabled(context, tags)
        elif mode == UserProfile.TAG_GROUPING_SMART_TREE:
            # Smart tree mode uses TagTreeNode instead; return empty groups
            return []
        else:
            raise ValueError(f"{mode} is not a valid tag grouping mode")

    @staticmethod
    def _create_tag_groups_alphabetical(context: RequestContext, tags: set[Tag]):
        def is_cjk(tag_name):
            return CJK_RE.match(tag_name[0]) is not None

        def get_pinyin_initials(tag_name):
            py = pinyin(tag_name, style=Style.FIRST_LETTER)
            return "".join(
                [
                    item[0].lower() if item and item[0] else char
                    for item, char in zip(py, tag_name, strict=False)
                ]
            )

        def get_cjk_group_char(tag_name):
            # Get the first letter of the pinyin(Chinese phonetic notation) for the first character
            first_char = tag_name[0]
            py = pinyin(first_char, style=Style.FIRST_LETTER)
            if py and py[0] and py[0][0]:
                return py[0][0].upper()
            else:
                return first_char.upper()

        def get_eng_group_char(tag_name):
            return tag_name[0].upper()

        # Split tags into English and Chinese (CJK)
        eng_tags = [tag for tag in tags if not is_cjk(tag.name)]
        cjk_tags = [tag for tag in tags if is_cjk(tag.name)]

        # Group English tags by first letter
        eng_group_map = {}
        for tag in eng_tags:
            group_char = get_eng_group_char(tag.name)
            if group_char not in eng_group_map:
                eng_group_map[group_char] = []
            eng_group_map[group_char].append(tag)
        eng_groups = []
        for group_char in sorted(eng_group_map.keys()):
            group = TagGroup(context, group_char, is_cjk=False)
            for tag in sorted(eng_group_map[group_char], key=lambda x: x.name.lower()):
                group.add_tag(tag)
            eng_groups.append(group)

        # Group Chinese tags by pinyin initial
        cjk_group_map = {}
        for tag in cjk_tags:
            group_char = get_cjk_group_char(tag.name)
            if group_char not in cjk_group_map:
                cjk_group_map[group_char] = []
            cjk_group_map[group_char].append(tag)
        cjk_groups = []
        for group_char in sorted(cjk_group_map.keys()):
            group = TagGroup(context, group_char, is_cjk=True)
            for tag in sorted(
                cjk_group_map[group_char], key=lambda x: get_pinyin_initials(x.name)
            ):
                group.add_tag(tag)
            cjk_groups.append(group)

        # English groups first, then Chinese groups
        return eng_groups + cjk_groups

    @staticmethod
    def _create_tag_groups_disabled(context: RequestContext, tags: set[Tag]):
        if len(tags) == 0:
            return []

        sorted_tags = sorted(tags, key=lambda t: _tag_sort_key(t.name))
        group = TagGroup(context, "Ungrouped")
        for tag in sorted_tags:
            group.add_tag(tag)

        return [group]


# ---- co-occurrence cache ----
# The raw (bookmark_id, tag_id) pairs for every user are cached so that
# the expensive SQL query only runs once.  The cache is invalidated by
# Django signals whenever the user's bookmarks or tags change (see
# ``signals.py``).  When building the tree for a specific search the
# cached pairs are filtered in Python, which is fast.
_CACHE_KEY_PREFIX = "tag_cooccurrence"
_CACHE_TIMEOUT = 3600  # seconds


def _get_cached_pairs(user):
    """Return all (bookmark_id, tag_id) pairs for *user*, from cache if possible."""
    from django.core.cache import cache

    key = f"{_CACHE_KEY_PREFIX}:{user.id}"
    pairs = cache.get(key)
    if pairs is None:
        through = Bookmark.tags.through
        all_bids = Bookmark.objects.filter(owner=user).values_list("id", flat=True)
        pairs = list(
            through.objects.filter(bookmark_id__in=all_bids).values_list(
                "bookmark_id", "tag_id"
            )
        )
        cache.set(key, pairs, _CACHE_TIMEOUT)
    return pairs


def invalidate_tag_cooccurrence_cache(user_id):
    """Evict the cached co-occurrence pairs for *user_id*."""
    from django.core.cache import cache

    cache.delete(f"{_CACHE_KEY_PREFIX}:{user_id}")


def _build_path_query_string(context, tag_names):
    """Build a query string that selects all *tag_names* (the full tree path)."""
    from bookmarks.services.search_query_parser import extract_tag_names_from_query

    params = context.query_params.copy()
    existing_query = params.get("q", "")
    profile = context.request.user_profile

    already_selected = {
        name.lower() for name in extract_tag_names_from_query(existing_query, profile)
    }

    parts = existing_query.strip() if existing_query else ""
    for name in tag_names:
        if name.lower() not in already_selected:
            if isinstance(context.search_expression, OrExpression):
                parts = f"({parts})" if parts else ""
            parts = f"{parts} #{name}".strip()

    params["q"] = parts
    params.pop("details", None)
    params.pop("page", None)

    encoded = utils.clean_query_params(params)
    return f"?{encoded}" if encoded else context.index_url


class TagTreeNode:
    """A node in the recursive tag co-occurrence tree.

    Unified structure for both roots and children:
    - root nodes: co_count = 0
    - child nodes: co_count = co-occurrence count with parent
    """

    def __init__(
        self, tag_item, count, co_count=0, children=None, path_query_string=""
    ):
        self.tag = tag_item  # AddTagItem
        self.name = tag_item.name
        self.count = count  # Total bookmark count for this tag
        self.co_count = co_count  # Co-occurrence with parent (0 for roots)
        self.children = children or []  # List of TagTreeNode
        self.has_children = len(self.children) > 0
        # URL that selects every tag on the path from the root to this node
        self.path_query_string = path_query_string


def _build_tag_tree(context, bookmark_queryset, tags):
    """Build root nodes for the tag co-occurrence tree.

    Only the root level is computed here — children are loaded on demand
    via the ``/tag-tree/children`` AJAX endpoint.  This keeps the initial
    page render fast regardless of how many tags or bookmarks the user has.

    Each root node's ``has_children`` flag is determined by a cheap check:
    does at least one bookmark with this tag also carry a *different* tag?
    """
    from collections import defaultdict

    all_pairs = _get_cached_pairs(context.request.user)
    bookmark_ids = set(bookmark_queryset.values_list("id", flat=True))
    pairs = [(bid, tid) for bid, tid in all_pairs if bid in bookmark_ids]

    # Build reverse mapping: bookmark -> tags
    bookmark_tags: dict[int, set[int]] = defaultdict(set)
    tag_bookmark_count: dict[int, int] = defaultdict(int)
    for bid, tid in pairs:
        bookmark_tags[bid].add(tid)
        tag_bookmark_count[tid] += 1

    # Precompute: which tags appear on a bookmark that has >1 tag?
    tags_with_children: set[int] = set()
    for tids in bookmark_tags.values():
        if len(tids) > 1:
            tags_with_children.update(tids)

    roots = []
    for tag in sorted(
        tags,
        key=lambda t: (-tag_bookmark_count.get(t.id, 0), _tag_sort_key(t.name)),
    ):
        count = tag_bookmark_count.get(tag.id, 0)
        if count == 0:
            continue

        root_names = [tag.name]
        node = TagTreeNode(
            AddTagItem(context, tag),
            count,
            children=[],
            path_query_string=_build_path_query_string(context, root_names),
        )
        node.has_children = tag.id in tags_with_children
        roots.append(node)

    return roots


def get_tag_tree_children(request_context, user, search, path_names):
    """Compute children for a specific tree path (AJAX endpoint).

    *path_names* is the ordered list of tag names from root to the
    node whose children are requested.  Returns a list of
    ``TagTreeNode`` objects (the children), or an empty list if the
    path is invalid.
    """
    from collections import defaultdict

    # Resolve tag names → Tag objects (case-insensitive)
    all_tags = list(Tag.objects.filter(owner=user))
    tag_by_name = {t.name.lower(): t for t in all_tags}
    path_tags = []
    for name in path_names:
        tag = tag_by_name.get(name.lower())
        if tag is None:
            return []
        path_tags.append(tag)

    # Load cached pairs & filter by current search
    all_pairs = _get_cached_pairs(user)
    search_bids = set(
        request_context.get_bookmark_query_set(search).values_list("id", flat=True)
    )
    pairs = [(bid, tid) for bid, tid in all_pairs if bid in search_bids]

    bookmark_tags: dict[int, set[int]] = defaultdict(set)
    for bid, tid in pairs:
        bookmark_tags[bid].add(tid)

    tag_bookmarks: dict[int, set[int]] = defaultdict(set)
    for bid, tid in pairs:
        tag_bookmarks[tid].add(bid)

    # Compute ancestor bookmark set: bookmarks with ALL path tags
    ancestor_bids = search_bids.copy()
    for tag in path_tags:
        ancestor_bids &= tag_bookmarks.get(tag.id, set())

    if not ancestor_bids:
        return []

    visited = {t.id for t in path_tags}
    tag_map = {t.id: t for t in all_tags}

    # Count co-occurrences
    co_tag_counts: dict[int, int] = defaultdict(int)
    for bid in ancestor_bids:
        for tid in bookmark_tags.get(bid, set()):
            if tid not in visited and tid in tag_map:
                co_tag_counts[tid] += 1

    sorted_candidates = sorted(
        co_tag_counts.items(),
        key=lambda x: (-x[1], _tag_sort_key(tag_map[x[0]].name)),
    )

    children = []
    for co_tag_id, co_count in sorted_candidates:
        co_tag = tag_map[co_tag_id]
        child_names = path_names + [co_tag.name]
        child_bids = ancestor_bids & tag_bookmarks[co_tag_id]

        # Check if this child would have grandchildren (without building them)
        has_children = False
        if child_bids:
            child_visited = visited | {co_tag_id}
            for bid in child_bids:
                for tid in bookmark_tags.get(bid, set()):
                    if tid not in child_visited and tid in tag_map:
                        has_children = True
                        break
                if has_children:
                    break

        children.append(
            TagTreeNode(
                AddTagItem(request_context, co_tag),
                len(tag_bookmarks.get(co_tag_id, set())),
                co_count=co_count,
                children=[],  # loaded on next AJAX call
                path_query_string=_build_path_query_string(
                    request_context, child_names
                ),
            )
        )
        # has_children is computed dynamically based on whether there
        # are any tags that co-occur with the full child path.
        children[-1].has_children = has_children

    return children


class TagCloudContext:
    request_context = RequestContext

    def __init__(self, request: HttpRequest, search: BookmarkSearch) -> None:
        request_context = self.request_context(request)
        user_profile = request.user_profile

        self.request = request
        self.search = search

        query_set = request_context.get_tag_query_set(self.search)
        tags = list(query_set)
        selected_tags = self.get_selected_tags()
        unique_tags = utils.unique(tags, key=lambda x: str.lower(x.name))
        unique_selected_tags = utils.unique(
            selected_tags, key=lambda x: str.lower(x.name)
        )
        has_selected_tags = len(unique_selected_tags) > 0
        unselected_tags = set(unique_tags).symmetric_difference(unique_selected_tags)

        self.tag_grouping = user_profile.tag_grouping

        if self.tag_grouping == UserProfile.TAG_GROUPING_SMART_TREE:
            # Build co-occurrence tree from all visible tags (including selected)
            bookmark_qs = request_context.get_bookmark_query_set(self.search)
            self.tag_tree = _build_tag_tree(request_context, bookmark_qs, unique_tags)
            self.groups = []
        else:
            self.tag_tree = []
            self.groups = TagGroup.create_tag_groups(
                request_context, self.tag_grouping, unselected_tags
            )

        selected_tag_items = []
        for tag in unique_selected_tags:
            selected_tag_items.append(RemoveTagItem(request_context, tag))

        self.tags = unique_tags
        self.selected_tags = selected_tag_items
        self.has_selected_tags = has_selected_tags

        # --- menu options ---
        is_tree = self.tag_grouping == UserProfile.TAG_GROUPING_SMART_TREE
        self.mode_options = [
            (UserProfile.TAG_GROUPING_ALPHABETICAL, _("Flat mode"), not is_tree),
            (UserProfile.TAG_GROUPING_SMART_TREE, _("Tree mode"), is_tree),
        ]
        self.grouping_options = (
            [
                (
                    UserProfile.TAG_GROUPING_ALPHABETICAL,
                    _("Alphabetical grouping"),
                    self.tag_grouping == UserProfile.TAG_GROUPING_ALPHABETICAL,
                ),
                (
                    UserProfile.TAG_GROUPING_DISABLED,
                    _("Disable grouping"),
                    self.tag_grouping == UserProfile.TAG_GROUPING_DISABLED,
                ),
            ]
            if not is_tree
            else []
        )

    def get_selected_tags(self):
        raise NotImplementedError("Must be implemented by subclass")

    def get_selected_tags_legacy(self, tags: list[Tag]):
        parsed_query = queries.parse_query_string(self.search.q)
        tag_names = parsed_query["tag_names"]
        if self.request.user_profile.tag_search == UserProfile.TAG_SEARCH_LAX:
            tag_names = tag_names + parsed_query["search_terms"]
        tag_names = [tag_name.lower() for tag_name in tag_names]

        return [tag for tag in tags if tag.name.lower() in tag_names]


class ActiveTagCloudContext(TagCloudContext):
    request_context = ActiveBookmarksContext

    def get_selected_tags(self):
        return list(
            queries.get_tags_for_query(
                self.request.user, self.request.user_profile, self.search.q
            )
        )


class ArchivedTagCloudContext(TagCloudContext):
    request_context = ArchivedBookmarksContext

    def get_selected_tags(self):
        return list(
            queries.get_tags_for_query(
                self.request.user, self.request.user_profile, self.search.q
            )
        )


class SharedTagCloudContext(TagCloudContext):
    request_context = SharedBookmarksContext

    def get_selected_tags(self):
        user = User.objects.filter(username=self.search.user).first()
        public_only = not self.request.user.is_authenticated
        return list(
            queries.get_shared_tags_for_query(
                user, self.request.user_profile, self.search.q, public_only
            )
        )


class DomainTreeNode:
    def __init__(
        self,
        hostname: str,
        level: int,
        include_subdomains: bool,
        label: str | None = None,
        is_group_node: bool = False,
        node_id: str | None = None,
        is_under_group_node: bool = False,
        filter_value_override: str | None = None,
    ) -> None:
        self.hostname = hostname
        self.level = level
        self.include_subdomains = include_subdomains
        self.label = label or hostname
        self.is_group_node = is_group_node
        self.node_id = node_id or hostname
        self.is_under_group_node = is_under_group_node
        self.total = 0
        self.children: dict[str, DomainTreeNode] = {}
        self._filter_value_override = filter_value_override

    @property
    def filter_value(self) -> str | None:
        if self.is_group_node:
            return None
        if self._filter_value_override:
            return self._filter_value_override
        return utils.build_domain_filter_value(
            self.hostname, include_subdomains=self.include_subdomains
        )

    def add_bookmark(self) -> None:
        self.total += 1


class DomainItem:
    def __init__(
        self,
        request_context: RequestContext,
        search_query: str,
        node: DomainTreeNode,
        selected_domain_terms: list[str],
        children: list["DomainItem"],
    ) -> None:
        selected_domain_filters = set(selected_domain_terms)
        self.node_id = node.node_id
        self.host = node.hostname
        self.label = node.label
        self.count = node.total
        self.level = node.level
        self.filter_value = node.filter_value
        self.favicon_domain = node.hostname
        self.is_group_node = node.is_group_node
        self.is_under_group_node = node.is_under_group_node
        self.prefers_icon_layout = node.level == 0 or node.is_under_group_node
        self.children_prefer_icon_layout = (
            node.is_group_node or node.is_under_group_node
        )
        self.is_selected = (
            False
            if self.is_group_node or not self.filter_value
            else self.filter_value in selected_domain_filters
        )
        self.children = children
        self.has_children = len(children) > 0
        self.url = None

        if self.filter_value:
            next_domain_terms = (
                [value for value in selected_domain_terms if value != self.filter_value]
                if self.filter_value in selected_domain_filters
                else [self.filter_value]
            )
            query_string = queries.replace_field_terms(
                search_query, "domain", next_domain_terms
            )
            query_params = request_context.query_params.copy()
            query_params.setlist("q", [query_string])
            query_params.pop("page", None)
            encoded_query = utils.clean_query_params(query_params)
            self.url = (
                "?" + encoded_query if encoded_query else request_context.index_url
            )


class DomainsContext:
    request_context = RequestContext
    TOP_ROOT_LIMIT = 10

    def _init_toggle_state(
        self,
        request,
        view_mode_action="toggle_domain_view_mode",
        compact_mode_action="toggle_domain_compact_mode",
    ):
        """Initialize view mode / compact mode state and toggle labels."""
        self.view_mode = self._parse_view_mode(request)
        self.is_icon_mode = self.view_mode == "icon"
        self.is_compact_mode = self._parse_compact_mode(request)
        self.toggle_view_mode_label = (
            _("Full mode") if self.is_icon_mode else _("Icon mode")
        )
        self.toggle_view_mode_action = view_mode_action
        self.toggle_view_mode_value = "full" if self.is_icon_mode else "icon"
        self.toggle_compact_mode_label = (
            _("All domains") if self.is_compact_mode else _("Only important domains")
        )
        self.toggle_compact_mode_action = compact_mode_action
        self.toggle_compact_mode_value = "0" if self.is_compact_mode else "1"

        # Fingerprint of domain normalization config, used by client-side
        # sessionStorage cache key; changes when custom domain roots change.
        raw = request.user_profile.custom_domain_root or ""
        self.config_fingerprint = (
            hashlib.md5(raw.encode()).hexdigest()[:12] if raw else "default"
        )

    def __init__(self, request: HttpRequest, search: BookmarkSearch) -> None:
        request_context = self.request_context(request)
        config = utils.parse_domain_roots(request.user_profile.custom_domain_root)
        self._init_toggle_state(request)

        parsed_query = queries.parse_query_string(search.q)
        selected_domain_terms = [
            utils.canonicalize_domain_filter_value(value)
            for value in parsed_query["field_terms"]["domain"]
            if value
        ]

        bookmarks = list(request_context.get_bookmark_query_set(search).values("url"))
        bookmarks.sort(key=lambda bookmark: bookmark["url"])

        root_nodes = self._build_domain_tree(bookmarks, config)
        if self.is_compact_mode:
            root_nodes = self._compact_root_nodes(root_nodes)
        self.roots = self._build_items(
            root_nodes,
            request_context,
            search.q,
            selected_domain_terms,
        )
        self.items = self._flatten_items(self.roots)
        self.is_empty = len(self.items) == 0

    @staticmethod
    def _build_domain_tree(
        bookmarks: list[dict],
        config: utils.DomainConfig,
    ) -> list[DomainTreeNode]:
        root_nodes: dict[str, DomainTreeNode] = {}

        for bookmark in bookmarks:
            hostname = utils.extract_hostname(bookmark["url"])
            if not hostname:
                continue

            path = utils.get_matching_domain_roots(hostname, config)
            if not path:
                path = [hostname]

            current_nodes = root_nodes
            for level, node_host in enumerate(path):
                include_subdomains = node_host in config.roots
                node = current_nodes.get(node_host)
                if node is None:
                    filter_override = None
                    if node_host in config.roots:
                        alias_domains = utils.get_alias_domains_for_root(
                            node_host, config
                        )
                        if len(alias_domains) > 1:
                            filter_override = (
                                utils.build_domain_filter_value_with_aliases(
                                    node_host,
                                    include_subdomains=True,
                                    config=config,
                                )
                            )

                    node = DomainTreeNode(
                        node_host,
                        level=level,
                        include_subdomains=include_subdomains,
                        filter_value_override=filter_override,
                    )
                    current_nodes[node_host] = node

                node.add_bookmark()
                current_nodes = node.children

        return DomainsContext._sorted_nodes(root_nodes.values())

    @staticmethod
    def _sorted_nodes(nodes):
        return sorted(nodes, key=lambda node: (-node.total, node.hostname.lower()))

    @staticmethod
    def _parse_view_mode(request: HttpRequest) -> str:
        return request.user_profile.domain_view_mode

    @staticmethod
    def _parse_compact_mode(request: HttpRequest) -> bool:
        return request.user_profile.domain_compact_mode

    @classmethod
    def _compact_root_nodes(
        cls, root_nodes: list[DomainTreeNode]
    ) -> list[DomainTreeNode]:
        if len(root_nodes) <= cls.TOP_ROOT_LIMIT:
            return root_nodes

        visible_nodes = list(root_nodes[: cls.TOP_ROOT_LIMIT])
        overflow_nodes = list(root_nodes[cls.TOP_ROOT_LIMIT :])

        for node in overflow_nodes:
            cls._mark_under_group(node)
            cls._offset_levels(node, 1)

        other_node = DomainTreeNode(
            "__other__",
            level=0,
            include_subdomains=False,
            label=_("Other"),
            is_group_node=True,
            node_id="__other__",
        )
        other_node.total = sum(node.total for node in overflow_nodes)
        other_node.children = {node.hostname: node for node in overflow_nodes}

        visible_nodes.append(other_node)
        return visible_nodes

    @classmethod
    def _mark_under_group(cls, node: DomainTreeNode) -> None:
        node.is_under_group_node = True
        for child in node.children.values():
            cls._mark_under_group(child)

    @classmethod
    def _offset_levels(cls, node: DomainTreeNode, delta: int) -> None:
        node.level += delta
        for child in node.children.values():
            cls._offset_levels(child, delta)

    @classmethod
    def _build_items(
        cls,
        nodes: list[DomainTreeNode],
        request_context: RequestContext,
        search_query: str,
        selected_domain_terms: list[str],
    ) -> list[DomainItem]:
        items = []

        for node in nodes:
            children = cls._build_items(
                cls._sorted_nodes(node.children.values()),
                request_context,
                search_query,
                selected_domain_terms,
            )
            items.append(
                DomainItem(
                    request_context,
                    search_query,
                    node,
                    selected_domain_terms,
                    children,
                )
            )

        return items

    @classmethod
    def _flatten_items(cls, items: list[DomainItem]) -> list[DomainItem]:
        flattened_items = []

        for item in items:
            flattened_items.append(item)
            flattened_items.extend(cls._flatten_items(item.children))

        return flattened_items


class ActiveDomainsContext(DomainsContext):
    request_context = ActiveBookmarksContext


class ArchivedDomainsContext(DomainsContext):
    request_context = ArchivedBookmarksContext


class SharedDomainsContext(DomainsContext):
    request_context = SharedBookmarksContext


class BookmarkAssetItem:
    def __init__(self, asset: BookmarkAsset):
        self.asset = asset

        self.id = asset.id
        self.display_name = asset.display_name
        self.asset_type = asset.asset_type
        self.file = asset.file
        self.file_size = asset.file_size
        self.content_type = asset.content_type
        self.status = asset.status

        icon_classes = []
        text_classes = []
        if asset.status == BookmarkAsset.STATUS_PENDING:
            icon_classes.append("text-tertiary")
            text_classes.append("text-tertiary")
        elif asset.status == BookmarkAsset.STATUS_FAILURE:
            icon_classes.append("text-error")
            text_classes.append("text-error")
        else:
            icon_classes.append("icon-color")

        self.icon_classes = " ".join(icon_classes)
        self.text_classes = " ".join(text_classes)


class BookmarkDetailsContext:
    request_context = RequestContext

    def __init__(self, request: HttpRequest, bookmark: Bookmark):
        request_context = self.request_context(request)

        user = request.user
        user_profile = request.user_profile

        self.edit_return_url = request_context.details(bookmark.id)
        self.action_url = request_context.action(add={"details": bookmark.id})
        self.delete_url = request_context.action()
        self.close_url = request_context.index()

        self.bookmark = bookmark
        self.tags = [AddTagItem(request_context, tag) for tag in bookmark.tags.all()]
        self.tags.sort(key=lambda item: item.name)

        self.profile = request.user_profile
        self.is_editable = bookmark.owner == user
        self.sharing_enabled = user_profile.enable_sharing
        self.preview_image_enabled = user_profile.enable_preview_images
        hostname = utils.extract_hostname(bookmark.url)
        self.favicon_domain = request_context._favicon_lookup.get_domain(hostname)
        self.snapshots_enabled = settings.LD_ENABLE_SNAPSHOTS
        self.uploads_enabled = not settings.LD_DISABLE_ASSET_UPLOAD

        self.web_archive_snapshot_url = bookmark.web_archive_snapshot_url
        if not self.web_archive_snapshot_url:
            self.web_archive_snapshot_url = generate_fallback_webarchive_url(
                bookmark.url, bookmark.date_added
            )

        self.assets = [
            BookmarkAssetItem(asset) for asset in bookmark.bookmarkasset_set.all()
        ]
        # 健康状态（预处理：reason 与 HTTP 码重复时去重，避免弹窗重复显示）
        _hd = bookmark.health_details or {}
        self.health_status = bookmark.health_status
        self.health_checked_at = _hd.get("checked_at", "")
        self.health_http_status = _hd.get("http_status")
        self.health_reason = _health_reason_display(
            _hd.get("reason", ""), _hd.get("http_status")
        )
        self.health_checked_at_display = format_health_checked_at(self.health_checked_at)
        self.health_status_display = _health_status_display(self.health_status)
        self.health_stale = _is_health_stale(
            self.health_checked_at, user_profile.health_max_age
        )
        self.health_display_status = (
            HEALTH_STATUS_UNKNOWN if self.health_stale else self.health_status
        )
        self.health_display_status_display = _health_status_display(self.health_display_status)
        self.has_pending_assets = any(
            asset.status == BookmarkAsset.STATUS_PENDING for asset in self.assets
        )
        self.latest_snapshot = next(
            (
                asset
                for asset in self.assets
                if asset.asset.asset_type == BookmarkAsset.TYPE_SNAPSHOT
                and asset.status == BookmarkAsset.STATUS_COMPLETE
            ),
            None,
        )

        self.preview_image_file = bookmark.preview_image_file

        # 高亮和批注数量（单次查询）
        from django.db.models import Count, Q

        from bookmarks.models import Annotation

        agg = Annotation.objects.filter(bookmark=bookmark).aggregate(
            total=Count("id"),
            with_note=Count("id", filter=Q(note_content__gt="")),
        )
        self.annotation_count = agg["total"]
        self.note_count = agg["with_note"]


class ActiveBookmarkDetailsContext(BookmarkDetailsContext):
    request_context = ActiveBookmarksContext


class ArchivedBookmarkDetailsContext(BookmarkDetailsContext):
    request_context = ArchivedBookmarksContext


class SharedBookmarkDetailsContext(BookmarkDetailsContext):
    request_context = SharedBookmarksContext


class TrashedBookmarksContext(RequestContext):
    index_view = "linkding:bookmarks.trashed"
    action_view = "linkding:bookmarks.trashed.action"

    def get_bookmark_query_set(self, search: BookmarkSearch):
        return queries.query_trashed_bookmarks(
            self.request.user, self.request.user_profile, search
        )

    def get_tag_query_set(self, search: BookmarkSearch):
        return queries.query_trashed_bookmark_tags(
            self.request.user, self.request.user_profile, search
        )


class TrashedBookmarkListContext(BookmarkListContext):
    request_context = TrashedBookmarksContext

    def __init__(self, request: HttpRequest, search: BookmarkSearch):
        super().__init__(request, search)
        self.is_trash_page = True


class TrashedTagCloudContext(TagCloudContext):
    request_context = TrashedBookmarksContext

    def get_selected_tags(self):
        return list(
            queries.get_tags_for_query(
                self.request.user, self.request.user_profile, self.search.q
            )
        )


class TrashedDomainsContext(DomainsContext):
    request_context = TrashedBookmarksContext


class TrashedBookmarkDetailsContext(BookmarkDetailsContext):
    request_context = TrashedBookmarksContext


def get_details_context(
    request: HttpRequest, context_type
) -> BookmarkDetailsContext | None:
    bookmark_id = request.GET.get("details")
    if not bookmark_id:
        return None

    try:
        bookmark = access.bookmark_read(request, bookmark_id)
    except Http404:
        # just ignore, might end up in a situation where the bookmark was deleted
        # in between navigating back and forth
        return None

    return context_type(request, bookmark)


class BundlesContext:
    def __init__(self, request: HttpRequest) -> None:
        self.request = request
        self.user = request.user
        self.user_profile = request.user_profile

        self.bundles = (
            BookmarkBundle.objects.filter(owner=self.user).order_by("order").all()
        )

        # 根据当前页面类型选择合适的上下文类
        current_path = request.path
        if current_path.endswith("/trash") or current_path.endswith("/trashed"):
            context_class = TrashedBookmarksContext  # 回收站
        elif current_path.endswith("/archived"):
            context_class = ArchivedBookmarksContext  # 归档
        elif current_path.endswith("/shared"):
            context_class = SharedBookmarksContext  # 分享
        else:
            context_class = ActiveBookmarksContext  # 正常

        # 为每个 bundle 统计书签数量
        for bundle in self.bundles:
            if getattr(bundle, "show_count", True):
                search = bundle.search_object
                context = context_class(request)
                queryset = context.get_bookmark_query_set(search)
                bundle.bookmarks_total = queryset.count()
            else:
                bundle.bookmarks_total = None
        self.is_empty = len(self.bundles) == 0

        # 新增：为每个 folder bundle 增加 has_child 属性
        bundles_list = list(self.bundles)
        for i, bundle in enumerate(bundles_list):
            if getattr(bundle, "is_folder", False):
                has_child = False
                for next_bundle in bundles_list[i + 1 :]:
                    if getattr(next_bundle, "is_folder", False):
                        break
                    else:
                        has_child = True
                        break
                bundle.has_child = has_child
            else:
                bundle.has_child = False

        selected_bundle_id = (
            int(request.GET.get("bundle")) if request.GET.get("bundle") else None
        )
        self.selected_bundle = next(
            (bundle for bundle in self.bundles if bundle.id == selected_bundle_id),
            None,
        )


# ── Highlight-specific sidebar contexts ──────────────────────────────


class HighlightRequestContext(RequestContext):
    """RequestContext for the highlights page — uses highlights URL."""

    index_view = "linkding:bookmarks.highlights"
    action_view = "linkding:bookmarks.highlights"

    def __init__(self, request: HttpRequest):
        super().__init__(request)
        # Remove non-highlight params that might linger from bookmarks page
        for key in ("details", "bundle", "shared", "unread", "tagged"):
            self.query_params.pop(key, None)


def _get_filtered_annotation_qs(request, search, with_related=False):
    """Build a filtered annotation queryset from HighlightSearch."""
    return queries.query_annotations(
        user=request.user,
        search_q=search.q,
        colors=search.colors_list or None,
        note_filter=search.note_filter,
        sort="-date_created",
        group_by="none",
        date_filter_by=search.date_filter_by,
        date_filter_start=search.date_filter_start,
        date_filter_end=search.date_filter_end,
        bookmark_id=search.bookmark_id_int,
        with_related=with_related,
    )


def _replace_node_counts_with_highlights(nodes, hostname_hl_counts):
    """Recursively replace DomainTreeNode.total with highlight counts."""
    for node in nodes:
        node.total = hostname_hl_counts.get(node.hostname, 0)
        _replace_node_counts_with_highlights(node.children.values(), hostname_hl_counts)


class HighlightDomainsContext(DomainsContext):
    """DomainsContext for highlights: filtered by current search, shows highlight counts."""

    @staticmethod
    def _parse_view_mode(request: HttpRequest) -> str:
        return request.user_profile.highlights_domain_view_mode

    @staticmethod
    def _parse_compact_mode(request: HttpRequest) -> bool:
        return request.user_profile.highlights_domain_compact_mode

    def __init__(self, request: HttpRequest, search) -> None:
        config = utils.parse_domain_roots(request.user_profile.custom_domain_root)
        self._init_toggle_state(
            request,
            view_mode_action="hl_toggle_domain_view_mode",
            compact_mode_action="hl_toggle_domain_compact_mode",
        )

        # Query filtered annotations to get bookmarks and highlight counts
        qs = _get_filtered_annotation_qs(request, search, with_related=False)
        bm_hl_counts = qs.values("bookmark__url").annotate(hl_count=Count("id"))

        # Build hostname → highlight count mapping
        hostname_hl_counts = {}
        for row in bm_hl_counts:
            hostname = utils.extract_hostname(row["bookmark__url"])
            if hostname:
                hostname_hl_counts[hostname] = (
                    hostname_hl_counts.get(hostname, 0) + row["hl_count"]
                )

        # Build domain tree from filtered bookmarks
        bookmarks = [{"url": row["bookmark__url"]} for row in bm_hl_counts]
        bookmarks.sort(key=lambda b: b["url"])

        request_context = HighlightRequestContext(request)
        root_nodes = self._build_domain_tree(bookmarks, config)

        # Replace bookmark counts with highlight counts
        _replace_node_counts_with_highlights(root_nodes, hostname_hl_counts)

        if self.is_compact_mode:
            root_nodes = self._compact_root_nodes(root_nodes)

        # Parse selected domains from search query for DomainItem.is_selected
        parsed_query = queries.parse_query_string(search.q or "")
        selected_domain_terms = [
            utils.canonicalize_domain_filter_value(value)
            for value in parsed_query["field_terms"]["domain"]
            if value
        ]

        self.roots = self._build_items(
            root_nodes, request_context, search.q or "", selected_domain_terms
        )
        self.items = self._flatten_items(self.roots)
        self.is_empty = len(self.items) == 0


class HighlightTagCloudContext:
    """TagCloudContext for highlights: filtered by current search, shows highlight counts."""

    def __init__(self, request: HttpRequest, search) -> None:
        user_profile = request.user_profile
        self.request = request
        self.search = search
        self.tag_grouping = user_profile.highlights_tag_grouping

        # Query filtered annotations grouped by tag
        qs = _get_filtered_annotation_qs(request, search)
        tag_hl_counts = (
            qs.filter(bookmark__tags__isnull=False)
            .values("bookmark__tags__name")
            .annotate(hl_count=Count("id"))
            .order_by()
        )
        tag_count_map = {
            row["bookmark__tags__name"].lower(): row["hl_count"]
            for row in tag_hl_counts
        }

        # Build tag objects from the tag names
        tag_names = list(tag_count_map.keys())
        tags = list(
            Tag.objects.filter(
                name__in=tag_names, bookmark__owner=request.user
            ).distinct()
        )
        unique_tags = utils.unique(tags, key=lambda x: str.lower(x.name))

        request_context = HighlightRequestContext(request)

        # Determine selected tags (from search query) — show at top, exclude from cloud
        selected_tag_names = extract_tag_names_from_query(search.q or "", user_profile)
        selected_tag_names_lower = [name.lower() for name in selected_tag_names]
        all_tags_for_selected = list(
            Tag.objects.filter(
                name__in=selected_tag_names_lower, bookmark__owner=request.user
            ).distinct()
        )
        unique_selected_tags = utils.unique(
            all_tags_for_selected, key=lambda x: str.lower(x.name)
        )
        self.selected_tags = [
            RemoveTagItem(request_context, tag) for tag in unique_selected_tags
        ]
        self.has_selected_tags = len(self.selected_tags) > 0

        if self.tag_grouping == UserProfile.TAG_GROUPING_SMART_TREE:
            # Build co-occurrence tree from annotation bookmark queryset
            bookmark_qs = Bookmark.objects.filter(
                id__in=qs.values("bookmark_id")
            ).distinct()
            self.tag_tree = _build_tag_tree(request_context, bookmark_qs, unique_tags)
            self.groups = []
        else:
            self.tag_tree = []
            # Build groups from UNSELECTED tags only
            unselected_tags = set(unique_tags).symmetric_difference(
                unique_selected_tags
            )
            groups = TagGroup.create_tag_groups(
                request_context, self.tag_grouping, unselected_tags
            )

            # Post-process: set highlight counts on tag items
            for group in groups:
                for tag_item in group.tags:
                    tag_item.count = tag_count_map.get(tag_item.name.lower(), 0)
            self.groups = groups

        self.tags = unique_tags

        is_tree = self.tag_grouping == UserProfile.TAG_GROUPING_SMART_TREE
        self.mode_options = [
            (UserProfile.TAG_GROUPING_ALPHABETICAL, _("Flat mode"), not is_tree),
            (UserProfile.TAG_GROUPING_SMART_TREE, _("Tree mode"), is_tree),
        ]
        self.grouping_options = (
            [
                (
                    UserProfile.TAG_GROUPING_ALPHABETICAL,
                    _("Alphabetical grouping"),
                    self.tag_grouping == UserProfile.TAG_GROUPING_ALPHABETICAL,
                ),
                (
                    UserProfile.TAG_GROUPING_DISABLED,
                    _("Disable grouping"),
                    self.tag_grouping == UserProfile.TAG_GROUPING_DISABLED,
                ),
            ]
            if not is_tree
            else []
        )

    def get_selected_tags(self):
        return []
