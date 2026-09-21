import binascii
import calendar
import hashlib
import json
import logging
import os
import re
from datetime import date, datetime, timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.http import QueryDict
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from bookmarks.utils import normalize_url, unique
from bookmarks.validators import BookmarkURLValidator

logger = logging.getLogger(__name__)

# ── Bookmark health check ────────────────────────────────────────────────
# health_status 取值（NULL = 未检查，显示/过滤层派生出 unknown）。
# 取值命名对齐业界通行术语（MDN / RFC 9110 / Google·Bing soft 404 定义）：
#   - error    = 请求错误（No response / Unverified）：网络层失败（DNS/超时/TLS），服务器不可达
#   - missing  = 内容缺失（Soft 404）：返回 200 但内容已不存在（Google/Bing 官方术语）
#   - unknown  = 未检查：不持久化（DB 恒为 NULL），由显示/过滤层派生
HEALTH_STATUS_OK = "ok"
HEALTH_STATUS_DEAD = "dead"
HEALTH_STATUS_FAILED = "failed"
HEALTH_STATUS_BLOCKED = "blocked"
HEALTH_STATUS_MISSING = "missing"
HEALTH_STATUS_UNKNOWN = "unknown"

HEALTH_STATUS_CHOICES = [
    # "all" 仅作为筛选面板的批量勾选控件，不参与过滤（from_request 忽略）
    ("all", _("All")),
    (HEALTH_STATUS_OK, _("Ok")),
    (HEALTH_STATUS_DEAD, _("Dead")),
    (HEALTH_STATUS_FAILED, _("Failed")),
    (HEALTH_STATUS_BLOCKED, _("Blocked")),
    (HEALTH_STATUS_MISSING, _("Missing")),
    (HEALTH_STATUS_UNKNOWN, _("Unknown")),
]

# 列表页需要展示角标的非 ok 状态（unknown 不展示——未检查不显示标记）
HEALTH_STATUS_BADGE_STATES = frozenset(
    {
        HEALTH_STATUS_DEAD,
        HEALTH_STATUS_FAILED,
        HEALTH_STATUS_BLOCKED,
        HEALTH_STATUS_MISSING,
    }
)


class Tag(models.Model):
    name = models.CharField(max_length=64)
    date_added = models.DateTimeField()
    owner = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.name


def sanitize_tag_name(tag_name: str):
    # strip leading/trailing spaces
    # replace inner spaces with replacement char
    return tag_name.strip().replace(" ", "-")


def parse_tag_string(tag_string: str, delimiter: str = ","):
    if not tag_string:
        return []
    names = tag_string.strip().split(delimiter)
    # remove empty names, sanitize remaining names
    names = [sanitize_tag_name(name) for name in names if name.strip()]
    # remove duplicates
    names = unique(names, str.lower)
    names.sort(key=str.lower)

    return names


def build_tag_string(tag_names: list[str], delimiter: str = ","):
    return delimiter.join(tag_names)


class Bookmark(models.Model):
    url = models.CharField(max_length=2048, validators=[BookmarkURLValidator()])
    url_normalized = models.CharField(max_length=2048, blank=True, db_index=True)
    title = models.TextField(blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    preview_image_remote_url = models.URLField(max_length=2048, blank=True)
    # Obsolete field, kept to not remove column when generating migrations
    website_title = models.CharField(max_length=512, blank=True, null=True)
    # Obsolete field, kept to not remove column when generating migrations
    website_description = models.TextField(blank=True, null=True)
    web_archive_snapshot_url = models.CharField(max_length=2048, blank=True)
    preview_image_file = models.CharField(max_length=512, blank=True)
    preview_image_retry_count = models.PositiveSmallIntegerField(default=0)
    preview_image_next_retry_at = models.DateTimeField(null=True, blank=True)
    unread = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    shared = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    date_added = models.DateTimeField()
    date_modified = models.DateTimeField()
    date_accessed = models.DateTimeField(blank=True, null=True)
    date_deleted = models.DateTimeField(blank=True, null=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag)
    latest_snapshot = models.ForeignKey(
        "BookmarkAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="latest_snapshot",
    )
    latest_article = models.ForeignKey(
        "BookmarkAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="latest_article",
    )
    # Bookmark health check（NULL = 未检查）
    health_status = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        db_index=True,
        choices=HEALTH_STATUS_CHOICES,
    )
    # 检查证据：http_status / checked_at / reason / redirect_url / adapter / content_signals / job_id
    health_details = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "url_normalized"],
                name="unique_bookmark_url_per_user",
            ),
        ]

    @property
    def resolved_title(self):
        if self.title:
            return self.title
        else:
            return self.url

    @property
    def resolved_description(self):
        return self.description

    @property
    def tag_names(self):
        names = [tag.name for tag in self.tags.all()]
        return sorted(names)

    def save(self, *args, **kwargs):
        self.url_normalized = normalize_url(self.url)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.resolved_title + " (" + self.url[:30] + "...)"

    @staticmethod
    def query_existing(owner: User, url: str) -> models.QuerySet:
        # Find existing bookmark by normalized URL, or fall back to exact URL if
        # normalized URL was not generated for whatever reason
        normalized_url = normalize_url(url)
        q = Q(owner=owner) & (
            Q(url_normalized=normalized_url) | Q(url_normalized="", url=url)
        )
        return Bookmark.objects.filter(q)


@receiver(post_delete, sender=Bookmark)
def bookmark_deleted(sender, instance, **kwargs):
    if instance.preview_image_file:
        filepath = os.path.join(settings.LD_PREVIEW_FOLDER, instance.preview_image_file)
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
            except Exception as error:
                logger.error(
                    f"Failed to delete preview image: {filepath}", exc_info=error
                )


class FaviconCache(models.Model):
    """全局域名级 favicon 缓存，所有用户共享。

    domain 为纯 hostname（已 lowercased，不含 scheme）。
    同一域名在磁盘上只有一个 favicon 文件，通过此表映射。
    """

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_MISSING = "missing"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_MISSING, "Missing"),
    ]

    # 退火重试延迟序列（秒）：1min, 3min, 5min, 10min, 20min
    RETRY_DELAYS = [60, 180, 300, 600, 1200]

    domain = models.CharField(max_length=512, unique=True, db_index=True)
    favicon_file = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    fetched_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
        ]

    def __str__(self):
        return f"{self.domain} -> {self.favicon_file} ({self.status})"


class BookmarkAsset(models.Model):
    TYPE_SNAPSHOT = "snapshot"
    TYPE_UPLOAD = "upload"
    TYPE_ARTICLE = "article"

    CONTENT_TYPE_HTML = "text/html"
    CONTENT_TYPE_PDF = "application/pdf"
    CONTENT_TYPE_JSON = "application/json"
    CONTENT_TYPE_XML = "application/xml"

    STATUS_PENDING = "pending"
    STATUS_COMPLETE = "complete"
    STATUS_FAILURE = "failure"

    bookmark = models.ForeignKey(Bookmark, on_delete=models.CASCADE)
    date_created = models.DateTimeField(auto_now_add=True, null=False)
    file = models.CharField(max_length=2048, blank=True, null=False)
    file_size = models.IntegerField(null=True)
    asset_type = models.CharField(max_length=64, blank=False, null=False)
    content_type = models.CharField(max_length=128, blank=False, null=False)
    display_name = models.CharField(max_length=2048, blank=True, null=False)
    status = models.CharField(max_length=64, blank=False, null=False)
    gzip = models.BooleanField(default=False, null=False)
    retry_count = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    scheduling_priority = models.IntegerField(default=0)

    @property
    def download_name(self):
        if self.asset_type == BookmarkAsset.TYPE_SNAPSHOT:
            if self.content_type == BookmarkAsset.CONTENT_TYPE_PDF:
                return f"{self.display_name}.pdf"
            if self.content_type == BookmarkAsset.CONTENT_TYPE_JSON:
                return f"{self.display_name}.json"
            if self.content_type == BookmarkAsset.CONTENT_TYPE_XML:
                return f"{self.display_name}.xml"
            return f"{self.display_name}.html"
        return self.display_name

    def save(self, *args, **kwargs):
        if self.file:
            try:
                file_path = os.path.join(settings.LD_ASSET_FOLDER, self.file)
                if os.path.isfile(file_path):
                    self.file_size = os.path.getsize(file_path)
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return self.display_name or f"Bookmark Asset #{self.pk}"


@receiver(post_delete, sender=BookmarkAsset)
def bookmark_asset_deleted(sender, instance, **kwargs):
    if instance.file:
        filepath = os.path.join(settings.LD_ASSET_FOLDER, instance.file)
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
            except Exception as error:
                logger.error(f"Failed to delete asset file: {filepath}", exc_info=error)


class Annotation(models.Model):
    COLOR_YELLOW = "yellow"
    COLOR_GREEN = "green"
    COLOR_BLUE = "blue"
    COLOR_PINK = "pink"
    COLOR_PRIMARY = "primary"
    COLOR_CHOICES = [
        (COLOR_YELLOW, _("Yellow")),
        (COLOR_GREEN, _("Green")),
        (COLOR_BLUE, _("Blue")),
        (COLOR_PINK, _("Pink")),
        (COLOR_PRIMARY, _("Theme")),
    ]

    bookmark = models.ForeignKey(
        Bookmark, on_delete=models.CASCADE, related_name="annotations"
    )
    article_asset = models.ForeignKey(
        BookmarkAsset,
        on_delete=models.SET_NULL,
        related_name="annotations",
        null=True,
        blank=True,
    )
    selector = models.JSONField()
    selected_text = models.TextField()
    color = models.CharField(
        max_length=16, choices=COLOR_CHOICES, default=COLOR_YELLOW
    )
    note_content = models.TextField(blank=True)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date_created"]

    def __str__(self):
        preview = self.selected_text[:50]
        return f"Annotation on '{self.bookmark.resolved_title}': {preview}..."


class ReadingProgress(models.Model):
    """每用户每书签的阅读进度，用于恢复阅读位置。"""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    bookmark = models.ForeignKey(
        Bookmark, on_delete=models.CASCADE, related_name="reading_progress"
    )
    article_asset = models.ForeignKey(
        BookmarkAsset, on_delete=models.SET_NULL, null=True, blank=True
    )
    # 文字锚点：视口顶部文字在正文 textContent 中的字符偏移
    text_position_start = models.IntegerField(null=True, blank=True)
    # 文字锚点：用于跨布局精确恢复的 TextQuoteSelector 字段
    text_quote_exact = models.CharField(max_length=1024, blank=True, default="")
    text_quote_prefix = models.CharField(max_length=512, blank=True, default="")
    text_quote_suffix = models.CharField(max_length=512, blank=True, default="")
    # 元素锚点：视口顶部为非文字元素（如 IMG）时，记录元素特征用于跨布局恢复
    element_selector = models.JSONField(null=True, blank=True)
    # 滚动比值（scrollTop / scrollableHeight），设备无关的阅读百分比
    progress = models.FloatField(
        default=0,
        validators=[MinValueValidator(0)],
    )
    # 滚动位置快照，同布局恢复时像素级精确
    scroll_top = models.IntegerField(default=0)
    scroll_height = models.IntegerField(default=0)
    client_width = models.IntegerField(default=0)
    client_height = models.IntegerField(default=0)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "bookmark"],
                name="unique_reading_progress_per_user_bookmark",
            ),
        ]

    def __str__(self):
        return (
            f"Reading progress for {self.user.username} on "
            f"{self.bookmark.resolved_title}: {self.progress:.2%}"
        )


class BookmarkBundle(models.Model):
    name = models.CharField(max_length=256, blank=False)
    search = models.CharField(max_length=256, blank=True)
    any_tags = models.CharField(max_length=1024, blank=True)
    all_tags = models.CharField(max_length=1024, blank=True)
    excluded_tags = models.CharField(max_length=1024, blank=True)
    order = models.IntegerField(null=False, default=0)
    date_created = models.DateTimeField(auto_now_add=True, null=False)
    date_modified = models.DateTimeField(auto_now=True, null=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    show_count = models.BooleanField(
        default=True, verbose_name=_("Show bookmark count")
    )
    is_folder = models.BooleanField(default=True)
    search_params = models.JSONField(
        default=dict, blank=True, verbose_name=_("Search parameters")
    )

    def __str__(self):
        return self.name

    @property
    def search_object(self):
        """返回基于配置的BookmarkSearch对象"""
        params = self.search_params.copy()

        # 反序列化：开始日期、结束日期字符串转date对象
        for date_field in ["date_filter_start", "date_filter_end"]:
            if date_field in params and params[date_field]:
                try:
                    if isinstance(params[date_field], str):
                        params[date_field] = datetime.strptime(
                            params[date_field], "%Y-%m-%d"
                        ).date()
                except (ValueError, TypeError):
                    params.pop(date_field, None)

        return BookmarkSearch(bundle=self, **params)


class BookmarkSearch:
    SORT_ADDED_ASC = "added_asc"
    SORT_ADDED_DESC = "added_desc"
    SORT_MODIFIED_ASC = "modified_asc"
    SORT_MODIFIED_DESC = "modified_desc"
    SORT_TITLE_ASC = "title_asc"
    SORT_TITLE_DESC = "title_desc"
    SORT_RANDOM = "random"
    SORT_DELETED_ASC = "deleted_asc"
    SORT_DELETED_DESC = "deleted_desc"

    FILTER_SHARED_OFF = "off"
    FILTER_SHARED_SHARED = "yes"
    FILTER_SHARED_UNSHARED = "no"

    FILTER_UNREAD_OFF = "off"
    FILTER_UNREAD_YES = "yes"
    FILTER_UNREAD_NO = "no"

    FILTER_TAGGED_OFF = "off"
    FILTER_TAGGED_TAGGED = "yes"
    FILTER_TAGGED_UNTAGGED = "no"

    FILTER_ASSET_OFF = "off"
    FILTER_ASSET_YES = "yes"
    FILTER_ASSET_NO = "no"

    FILTER_HIGHLIGHT_OFF = "off"
    FILTER_HIGHLIGHT_YES = "yes"
    FILTER_HIGHLIGHT_NO = "no"

    FILTER_ANNOTATION_OFF = "off"
    FILTER_ANNOTATION_YES = "yes"
    FILTER_ANNOTATION_NO = "no"

    FILTER_DATE_OFF = "off"
    FILTER_DATE_BY_ADDED = "added"
    FILTER_DATE_BY_MODIFIED = "modified"
    FILTER_DATE_BY_DELETED = "deleted"
    FILTER_DATE_BY_HIGHLIGHT = "highlight"
    FILTER_DATE_BY_ANNOTATION = "annotation"
    FILTER_DATE_BY_HEALTH = "health"

    FILTER_DATE_TYPE_ABSOLUTE = "absolute"
    FILTER_DATE_TYPE_RELATIVE = "relative"

    params = [
        "q",
        "user",
        "bundle",
        "sort",
        "shared",
        "unread",
        "tagged",
        "modified_since",
        "added_since",
        "deleted_since",
        "date_filter_by",
        "date_filter_type",
        "date_filter_relative_string",
        "date_filter_start",
        "date_filter_end",
        "html_snapshot",
        "preview_image",
        "favicon",
        "highlight",
        "annotation",
        "health_status",
    ]
    preferences = [
        "sort",
        "shared",
        "unread",
        "tagged",
        "date_filter_by",
        "date_filter_type",
        "date_filter_relative_string",
    ]
    defaults = {
        "q": "",
        "user": "",
        "bundle": None,
        "sort": SORT_ADDED_DESC,
        "shared": FILTER_SHARED_OFF,
        "unread": FILTER_UNREAD_OFF,
        "tagged": FILTER_TAGGED_OFF,
        "modified_since": None,
        "added_since": None,
        "deleted_since": None,
        "date_filter_by": FILTER_DATE_OFF,
        "date_filter_type": FILTER_DATE_TYPE_ABSOLUTE,
        "date_filter_relative_string": None,
        "date_filter_start": None,
        "date_filter_end": None,
        "html_snapshot": FILTER_ASSET_OFF,
        "preview_image": FILTER_ASSET_OFF,
        "favicon": FILTER_ASSET_OFF,
        "highlight": FILTER_HIGHLIGHT_OFF,
        "annotation": FILTER_ANNOTATION_OFF,
        "health_status": [],
    }

    @staticmethod
    def parse_relative_date_string(date_filter_relative_string):
        today = date.today()
        if date_filter_relative_string == "today":
            return today, today
        elif date_filter_relative_string == "yesterday":
            yesterday = today - timedelta(days=1)
            return yesterday, yesterday
        elif date_filter_relative_string == "this_week":
            days_since_monday = (
                today.weekday()
            )  # weekday() 返回 0-6，0 是周一，6 是周日
            monday = today - timedelta(days=days_since_monday)
            sunday = monday + timedelta(days=6)
            return monday, sunday
        elif date_filter_relative_string == "this_month":
            first_day = today.replace(day=1)
            _, last_day_of_month = calendar.monthrange(today.year, today.month)
            last_day = today.replace(day=last_day_of_month)
            return first_day, last_day
        elif date_filter_relative_string == "this_year":
            first_day = today.replace(month=1, day=1)
            last_day = today.replace(month=12, day=31)
            return first_day, last_day
        else:
            m = re.match(
                r"last_(\d+)_(day|week|month|year)s?", date_filter_relative_string
            )
            if m:
                value, unit = int(m.group(1)), m.group(2)
                if unit == "day":
                    start = today - timedelta(days=value - 1)
                    end = today
                elif unit == "week":
                    start = today - timedelta(days=value * 7 - 1)
                    end = today
                elif unit == "month":
                    start = today - timedelta(days=value * 30 - 1)
                    end = today
                elif unit == "year":
                    start = today - timedelta(days=value * 365 - 1)
                    end = today
                else:
                    return None, None
                return start, end
            return None, None

    def __init__(
        self,
        q: str = None,
        user: str = None,
        bundle: BookmarkBundle = None,
        sort: str = None,
        shared: str = None,
        unread: str = None,
        tagged: str = None,
        modified_since: str = None,
        added_since: str = None,
        deleted_since: str = None,
        date_filter_by: str = None,
        date_filter_type: str = None,
        date_filter_relative_string: str = None,
        date_filter_start=None,
        date_filter_end=None,
        html_snapshot: str = None,
        preview_image: str = None,
        favicon: str = None,
        highlight: str = None,
        annotation: str = None,
        health_status=None,
        preferences: dict = None,
        request: any = None,
    ):
        if not preferences:
            preferences = {}
        self.defaults = {**BookmarkSearch.defaults, **preferences}
        self.request = request

        # 合并参数：user参数 > bundle参数 > default参数
        user_params = {
            "q": q,
            "user": user,
            "bundle": bundle,
            "sort": sort,
            "shared": shared,
            "unread": unread,
            "tagged": tagged,
            "modified_since": modified_since,
            "added_since": added_since,
            "deleted_since": deleted_since,
            "date_filter_by": date_filter_by,
            "date_filter_type": date_filter_type,
            "date_filter_relative_string": date_filter_relative_string,
            "date_filter_start": date_filter_start,
            "date_filter_end": date_filter_end,
            "html_snapshot": html_snapshot,
            "preview_image": preview_image,
            "favicon": favicon,
            "highlight": highlight,
            "annotation": annotation,
            "health_status": health_status,
        }
        bundle_params = {}
        if bundle:
            bundle_params = bundle.search_params
        for param in self.params:
            user_value = user_params.get(param)
            bundle_value = bundle_params.get(param)
            default_value = self.defaults.get(param)
            if param in user_params and user_params[param] is not None:
                final_value = user_value
            else:
                final_value = bundle_value or default_value
            setattr(self, param, final_value)

    @property
    def date_filter_start(self):
        if (
            self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE
            and self.date_filter_relative_string
        ):
            start, _ = self.parse_relative_date_string(self.date_filter_relative_string)
            if start:
                return start
        return self.__dict__.get("date_filter_start")

    @property
    def date_filter_end(self):
        if (
            self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE
            and self.date_filter_relative_string
        ):
            _, end = self.parse_relative_date_string(self.date_filter_relative_string)
            if end:
                return end
        return self.__dict__.get("date_filter_end")

    @date_filter_start.setter
    def date_filter_start(self, value):
        self.__dict__["date_filter_start"] = value

    @date_filter_end.setter
    def date_filter_end(self, value):
        self.__dict__["date_filter_end"] = value

    def is_modified(self, param):
        value = self.__dict__[param]

        # 日期筛选类型为相对时，隐藏url参数中的开始日期、结束日期
        if self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE and param in [
            "date_filter_start",
            "date_filter_end",
        ]:
            return False

        return value != self.defaults[param]

    @property
    def modified_params(self):
        return [field for field in self.params if self.is_modified(field)]

    @property
    def modified_preferences(self):
        return [
            preference
            for preference in self.preferences
            if self.is_modified(preference)
        ]

    @property
    def has_modifications(self):
        return len(self.modified_params) > 0

    @property
    def has_modified_preferences(self):
        return len(self.modified_preferences) > 0

    @property
    def query_params(self):
        query_params = {}

        if self.bundle:
            query_params["bundle"] = self.bundle.id
            bundle_search_object = self.bundle.search_object

            for param in self.params:
                # 获取参数值，对于属性需要特殊处理
                if param in ["date_filter_start", "date_filter_end"]:
                    value = getattr(self, param)
                    bundle_value = getattr(bundle_search_object, param)
                else:
                    value = self.__dict__[param]
                    bundle_value = bundle_search_object.__dict__[param]

                # 特殊处理日期相关参数
                if param in ["date_filter_start", "date_filter_end"]:
                    if self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE:
                        continue
                    elif self.date_filter_type == self.FILTER_DATE_TYPE_ABSOLUTE:
                        bundle_start = bundle_search_object.date_filter_start
                        bundle_end = bundle_search_object.date_filter_end
                        if (
                            self.date_filter_start == bundle_start
                            and self.date_filter_end == bundle_end
                        ):
                            continue

                if (
                    value is not None and value != "" and value != bundle_value
                ):  # 用户参数与Bundle参数不同时url包含该参数
                    if param == "health_status" and isinstance(value, list):
                        # 多选状态序列化为单参数逗号分隔：?health_status=ok,dead
                        query_params[param] = ",".join(value)
                    elif isinstance(value, models.Model):
                        query_params[param] = value.id
                    else:
                        query_params[param] = value
        else:
            # 没有Bundle时，使用原逻辑（只包含modified_params）
            for param in self.modified_params:
                value = self.__dict__[param]
                if param == "health_status" and isinstance(value, list):
                    # 多选状态序列化为单参数逗号分隔：?health_status=ok,dead
                    query_params[param] = ",".join(value)
                elif isinstance(value, models.Model):
                    query_params[param] = value.id
                else:
                    query_params[param] = value

        return query_params

    @property
    def preferences_dict(self):
        return {
            preference: self.__dict__[preference] for preference in self.preferences
        }

    @staticmethod
    def from_request(request: any, query_dict: QueryDict, preferences: dict = None):
        initial_values = {}
        bundle = None

        bundle_id = query_dict.get("bundle")
        if bundle_id:
            bundle = BookmarkBundle.objects.filter(
                owner=request.user, pk=bundle_id
            ).first()

        for param in BookmarkSearch.params:
            if param == "bundle":
                continue
            if param == "health_status":
                # 单参数逗号分隔：?health_status=ok,dead,blocked
                # 兼容旧多值形式 ?health_status=a&health_status=b（POST 表单提交）
                if hasattr(query_dict, "getlist"):
                    values = query_dict.getlist(param)
                else:
                    values = query_dict.get(param)
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    values = []
                # 每个值都按逗号拆分（QueryDict.getlist 返回的单个元素可能含逗号）
                # "all" 是筛选面板的全选控件，不作为过滤条件
                value = []
                for v in values:
                    value.extend(
                        x.strip()
                        for x in str(v).split(",")
                        if x.strip() and x.strip() != "all"
                    )
                if not value:
                    value = None
            else:
                value = query_dict.get(param)
            if value:
                initial_values[param] = value

        if bundle:
            search = bundle.search_object
            for param, value in initial_values.items():  # 合并用户参数
                setattr(search, param, value)
            return search
        else:
            return BookmarkSearch(
                **initial_values, preferences=preferences, request=request
            )


class BookmarkSearchForm(forms.Form):
    SORT_CHOICES = [
        (BookmarkSearch.SORT_ADDED_ASC, _("Added ↑")),
        (BookmarkSearch.SORT_ADDED_DESC, _("Added ↓")),
        (BookmarkSearch.SORT_MODIFIED_ASC, _("Modified ↑")),
        (BookmarkSearch.SORT_MODIFIED_DESC, _("Modified ↓")),
        (BookmarkSearch.SORT_TITLE_ASC, _("Title ↑")),
        (BookmarkSearch.SORT_TITLE_DESC, _("Title ↓")),
        (BookmarkSearch.SORT_RANDOM, _("Random")),
    ]
    FILTER_SHARED_CHOICES = [
        (BookmarkSearch.FILTER_SHARED_OFF, _("Off")),
        (BookmarkSearch.FILTER_SHARED_SHARED, _("Shared")),
        (BookmarkSearch.FILTER_SHARED_UNSHARED, _("Unshared")),
    ]
    FILTER_UNREAD_CHOICES = [
        (BookmarkSearch.FILTER_UNREAD_OFF, _("Off")),
        (BookmarkSearch.FILTER_UNREAD_YES, pgettext_lazy("bookmark_status", "Unread")),
        (BookmarkSearch.FILTER_UNREAD_NO, pgettext_lazy("bookmark_status", "Read")),
    ]
    FILTER_TAGGED_CHOICES = [
        (BookmarkSearch.FILTER_TAGGED_OFF, _("Off")),
        (BookmarkSearch.FILTER_TAGGED_TAGGED, _("Tagged")),
        (BookmarkSearch.FILTER_TAGGED_UNTAGGED, _("Untagged")),
    ]
    FILTER_ASSET_CHOICES = [
        (BookmarkSearch.FILTER_ASSET_OFF, _("Off")),
        (BookmarkSearch.FILTER_ASSET_YES, _("Has")),
        (BookmarkSearch.FILTER_ASSET_NO, _("Missing")),
    ]
    FILTER_HIGHLIGHT_CHOICES = [
        (BookmarkSearch.FILTER_HIGHLIGHT_OFF, _("Off")),
        (BookmarkSearch.FILTER_HIGHLIGHT_YES, _("Has")),
        (BookmarkSearch.FILTER_HIGHLIGHT_NO, _("Missing")),
    ]
    FILTER_ANNOTATION_CHOICES = [
        (BookmarkSearch.FILTER_ANNOTATION_OFF, _("Off")),
        (BookmarkSearch.FILTER_ANNOTATION_YES, _("Has")),
        (BookmarkSearch.FILTER_ANNOTATION_NO, _("Missing")),
    ]
    FILTER_DATE_BY_CHOICES = [
        (BookmarkSearch.FILTER_DATE_OFF, _("Off")),
        (BookmarkSearch.FILTER_DATE_BY_ADDED, _("Added")),
        (BookmarkSearch.FILTER_DATE_BY_MODIFIED, _("Modified")),
        (BookmarkSearch.FILTER_DATE_BY_HIGHLIGHT, _("Highlighted")),
        (BookmarkSearch.FILTER_DATE_BY_ANNOTATION, _("Annotated")),
        (BookmarkSearch.FILTER_DATE_BY_HEALTH, _("Health checked")),
    ]
    FILTER_DATE_TYPE_CHOICES = [
        (BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE, _("Absolute")),
        (BookmarkSearch.FILTER_DATE_TYPE_RELATIVE, _("Relative")),
    ]

    q = forms.CharField()
    user = forms.ChoiceField(required=False)
    bundle = forms.CharField(required=False)
    sort = forms.ChoiceField(choices=SORT_CHOICES)
    shared = forms.ChoiceField(choices=FILTER_SHARED_CHOICES, widget=forms.RadioSelect)
    unread = forms.ChoiceField(choices=FILTER_UNREAD_CHOICES, widget=forms.RadioSelect)
    tagged = forms.ChoiceField(choices=FILTER_TAGGED_CHOICES, widget=forms.RadioSelect)
    modified_since = forms.CharField(required=False)
    added_since = forms.CharField(required=False)
    deleted_since = forms.CharField(required=False)
    date_filter_by = forms.ChoiceField(
        choices=FILTER_DATE_BY_CHOICES, widget=forms.RadioSelect
    )
    date_filter_type = forms.ChoiceField(
        choices=FILTER_DATE_TYPE_CHOICES, widget=forms.RadioSelect
    )
    date_filter_start = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_filter_end = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_filter_relative_string = forms.CharField(required=False)
    health_status = forms.MultipleChoiceField(
        required=False,
        choices=HEALTH_STATUS_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    html_snapshot = forms.ChoiceField(
        choices=FILTER_ASSET_CHOICES,
        widget=forms.RadioSelect,
        required=False,
    )
    preview_image = forms.ChoiceField(
        choices=FILTER_ASSET_CHOICES,
        widget=forms.RadioSelect,
        required=False,
    )
    favicon = forms.ChoiceField(
        choices=FILTER_ASSET_CHOICES,
        widget=forms.RadioSelect,
        required=False,
    )
    highlight = forms.ChoiceField(
        choices=FILTER_HIGHLIGHT_CHOICES,
        widget=forms.RadioSelect,
        required=False,
    )
    annotation = forms.ChoiceField(
        choices=FILTER_ANNOTATION_CHOICES,
        widget=forms.RadioSelect,
        required=False,
    )

    def __init__(
        self,
        search: BookmarkSearch,
        editable_fields: list[str] = None,
        users: list[User] = None,
    ):
        super().__init__()
        editable_fields = editable_fields or []
        self.editable_fields = editable_fields

        # set choices for user field if users are provided
        if users:
            user_choices = [(user.username, user.username) for user in users]
            user_choices.insert(0, ("", _("Everyone")))
            self.fields["user"].choices = user_choices

        for param in search.params:
            # set initial values for modified params
            if param in ["date_filter_start", "date_filter_end"]:
                value = getattr(search, param)
                # date对象转为字符串，供 DateField 使用
                value = value.isoformat() if hasattr(value, "isoformat") else value
            else:
                value = search.__dict__.get(param)

            if isinstance(value, models.Model):
                self.fields[param].initial = value.id
            else:
                self.fields[param].initial = value

            # Mark non-editable modified fields as hidden. That way, templates
            # rendering a form can just loop over hidden_fields to ensure that
            # all necessary search options are kept when submitting the form.
            if search.is_modified(param) and param not in editable_fields:
                self.fields[param].widget = forms.HiddenInput()


class UserProfile(models.Model):
    LANGUAGE_EN = "en"
    LANGUAGE_ZH_HANS = "zh-hans"
    LANGUAGE_CHOICES = [
        (LANGUAGE_EN, _("English")),
        (LANGUAGE_ZH_HANS, _("Simplified Chinese")),
    ]
    THEME_AUTO = "auto"
    THEME_LIGHT = "light"
    THEME_DARK = "dark"
    THEME_CHOICES = [
        (THEME_AUTO, _("Auto")),
        (THEME_LIGHT, _("Light")),
        (THEME_DARK, _("Dark")),
    ]
    BOOKMARK_DATE_DISPLAY_RELATIVE = "relative"
    BOOKMARK_DATE_DISPLAY_ABSOLUTE = "absolute"
    BOOKMARK_DATE_DISPLAY_HIDDEN = "hidden"
    BOOKMARK_DATE_DISPLAY_CHOICES = [
        (BOOKMARK_DATE_DISPLAY_HIDDEN, _("Hidden")),
        (BOOKMARK_DATE_DISPLAY_RELATIVE, _("Relative")),
        (BOOKMARK_DATE_DISPLAY_ABSOLUTE, _("Absolute")),
    ]
    BOOKMARK_DATE_ROUTE_DISABLED = "disabled"
    BOOKMARK_DATE_ROUTE_SNAPSHOT = "snapshot"
    BOOKMARK_DATE_ROUTE_READER = "reader"
    BOOKMARK_DATE_ROUTE_WEB_ARCHIVE = "web_archive"
    BOOKMARK_DATE_ROUTE_HIGHLIGHTS = "highlights"
    BOOKMARK_DATE_ROUTE_CHOICES = [
        (BOOKMARK_DATE_ROUTE_DISABLED, _("Disabled")),
        (BOOKMARK_DATE_ROUTE_SNAPSHOT, _("Latest snapshot")),
        (BOOKMARK_DATE_ROUTE_READER, _("Reader mode")),
        (BOOKMARK_DATE_ROUTE_WEB_ARCHIVE, _("Internet Archive")),
        (BOOKMARK_DATE_ROUTE_HIGHLIGHTS, _("Highlights & Annotations")),
    ]
    BOOKMARK_DESCRIPTION_DISPLAY_INLINE = "inline"
    BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE = "separate"
    BOOKMARK_DESCRIPTION_DISPLAY_CHOICES = [
        (BOOKMARK_DESCRIPTION_DISPLAY_INLINE, _("Inline")),
        (BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE, _("Separate")),
    ]
    BOOKMARK_LINK_TARGET_BLANK = "_blank"
    BOOKMARK_LINK_TARGET_SELF = "_self"
    BOOKMARK_LINK_TARGET_CHOICES = [
        (BOOKMARK_LINK_TARGET_BLANK, _("New page")),
        (BOOKMARK_LINK_TARGET_SELF, _("Same page")),
    ]
    WEB_ARCHIVE_INTEGRATION_DISABLED = "disabled"
    WEB_ARCHIVE_INTEGRATION_ENABLED = "enabled"
    WEB_ARCHIVE_INTEGRATION_CHOICES = [
        (WEB_ARCHIVE_INTEGRATION_DISABLED, _("Disabled")),
        (WEB_ARCHIVE_INTEGRATION_ENABLED, _("Enabled")),
    ]
    TAG_SEARCH_STRICT = "strict"
    TAG_SEARCH_LAX = "lax"
    TAG_SEARCH_CHOICES = [
        (TAG_SEARCH_STRICT, _("Strict")),
        (TAG_SEARCH_LAX, _("Lax")),
    ]
    TAG_GROUPING_ALPHABETICAL = "alphabetical"
    TAG_GROUPING_DISABLED = "disabled"
    TAG_GROUPING_SMART_TREE = "smart_tree"
    TAG_GROUPING_CHOICES = [
        (TAG_GROUPING_ALPHABETICAL, _("Alphabetical")),
        (TAG_GROUPING_SMART_TREE, _("Smart tree")),
        (TAG_GROUPING_DISABLED, _("Disabled")),
    ]
    SIDEBAR_MODULE_SUMMARY = "summary"
    SIDEBAR_MODULE_BUNDLES = "bundles"
    SIDEBAR_MODULE_DOMAINS = "domains"
    SIDEBAR_MODULE_TAGS = "tags"
    SIDEBAR_MODULE_COLORS = "colors"
    SIDEBAR_MODULE_LABELS = {
        SIDEBAR_MODULE_SUMMARY: _("User summary"),
        SIDEBAR_MODULE_BUNDLES: _("Filters"),
        SIDEBAR_MODULE_DOMAINS: _("Domains"),
        SIDEBAR_MODULE_TAGS: _("Tags"),
        SIDEBAR_MODULE_COLORS: _("Colors"),
    }

    TOOLBAR_MODULE_DATE = "date"
    TOOLBAR_MODULE_ACTIONS = "actions"
    TOOLBAR_MODULE_QUICK_EDITS = "quick_edits"
    TOOLBAR_MODULE_QUICK_TAGS = "quick_tags"
    TOOLBAR_MODULE_STATUSES = "statuses"
    TOOLBAR_MODULE_KEYS = [
        TOOLBAR_MODULE_DATE,
        TOOLBAR_MODULE_ACTIONS,
        TOOLBAR_MODULE_QUICK_EDITS,
        TOOLBAR_MODULE_QUICK_TAGS,
        TOOLBAR_MODULE_STATUSES,
    ]
    TOOLBAR_MODULE_LABELS = {
        TOOLBAR_MODULE_DATE: _("Bookmark date"),
        TOOLBAR_MODULE_ACTIONS: _("Bookmark actions"),
        TOOLBAR_MODULE_QUICK_EDITS: _("Quick edit"),
        TOOLBAR_MODULE_QUICK_TAGS: _("Quick tags"),
        TOOLBAR_MODULE_STATUSES: _("Bookmark status"),
    }

    ACTION_READ = "read"
    ACTION_VIEW = "view"
    ACTION_HIGHLIGHT = "highlight"
    ACTION_EDIT = "edit"
    ACTION_ARCHIVE = "archive"
    ACTION_REMOVE = "remove"
    ACTION_KEYS = [
        ACTION_READ,
        ACTION_VIEW,
        ACTION_HIGHLIGHT,
        ACTION_EDIT,
        ACTION_ARCHIVE,
        ACTION_REMOVE,
    ]
    ACTION_LABELS = {
        ACTION_READ: pgettext_lazy("bookmark_action", "Read"),
        ACTION_VIEW: _("View"),
        ACTION_HIGHLIGHT: _("Highlights"),
        ACTION_EDIT: _("Edit"),
        ACTION_ARCHIVE: _("Archive"),
        ACTION_REMOVE: _("Remove"),
    }
    ACTION_ICONS = {
        ACTION_READ: "ld-icon-unread",
        ACTION_VIEW: "ld-icon-view",
        ACTION_HIGHLIGHT: "ld-icon-highlight",
        ACTION_EDIT: "ld-icon-edit",
        ACTION_ARCHIVE: "ld-icon-archive",
        ACTION_REMOVE: "ld-icon-remove",
    }
    ACTION_FIELD_MAP = {
        ACTION_READ: "display_read_bookmark_action",
        ACTION_VIEW: "display_view_bookmark_action",
        ACTION_EDIT: "display_edit_bookmark_action",
        ACTION_ARCHIVE: "display_archive_bookmark_action",
        ACTION_REMOVE: "display_remove_bookmark_action",
    }

    STATUS_NOTES = "notes"
    STATUS_SHARE = "share"
    STATUS_UNREAD = "unread"
    STATUS_HEALTH = "health"
    # 顺序即设置页与工具栏的默认顺序：健康状态默认在最后
    STATUS_KEYS = [STATUS_NOTES, STATUS_SHARE, STATUS_UNREAD, STATUS_HEALTH]
    STATUS_LABELS = {
        STATUS_NOTES: pgettext_lazy("bookmark_status", "Notes"),
        STATUS_SHARE: pgettext_lazy("bookmark_status", "Share"),
        STATUS_UNREAD: pgettext_lazy("bookmark_status", "Unread"),
        STATUS_HEALTH: pgettext_lazy("bookmark_status", "Health status"),
    }
    STATUS_ICONS = {
        STATUS_NOTES: "ld-icon-note",
        STATUS_SHARE: "ld-icon-share",
        STATUS_UNREAD: "ld-icon-unread",
        STATUS_HEALTH: "",
    }

    # 快捷编辑按钮
    QUICK_EDIT_TITLE = "title"
    QUICK_EDIT_DESCRIPTION = "description"
    QUICK_EDIT_NOTES = "notes"
    QUICK_EDIT_TAGS = "tags"
    QUICK_EDIT_KEYS = [
        QUICK_EDIT_TITLE,
        QUICK_EDIT_DESCRIPTION,
        QUICK_EDIT_NOTES,
        QUICK_EDIT_TAGS,
    ]
    QUICK_EDIT_LABELS = {
        QUICK_EDIT_TITLE: _("Title"),
        QUICK_EDIT_DESCRIPTION: _("Description"),
        QUICK_EDIT_NOTES: _("Notes"),
        QUICK_EDIT_TAGS: _("Tags"),
    }
    QUICK_EDIT_ICONS = {
        QUICK_EDIT_TITLE: "ld-icon-edit-title",
        QUICK_EDIT_DESCRIPTION: "ld-icon-edit-description",
        QUICK_EDIT_NOTES: "ld-icon-edit-notes",
        QUICK_EDIT_TAGS: "ld-icon-tag",
    }

    ACTION_DISPLAY_MODE_TEXT = "text"
    ACTION_DISPLAY_MODE_ICON = "icon"
    ACTION_DISPLAY_MODE_CHOICES = [
        (ACTION_DISPLAY_MODE_TEXT, _("Text")),
        (ACTION_DISPLAY_MODE_ICON, _("Icon")),
    ]
    user = models.OneToOneField(User, related_name="profile", on_delete=models.CASCADE)
    language = models.CharField(max_length=20, blank=False, default=LANGUAGE_EN)
    theme = models.CharField(
        max_length=10, choices=THEME_CHOICES, blank=False, default=THEME_AUTO
    )
    bookmark_date_display = models.CharField(
        max_length=10,
        choices=BOOKMARK_DATE_DISPLAY_CHOICES,
        blank=False,
        default=BOOKMARK_DATE_DISPLAY_RELATIVE,
    )
    bookmark_date_route = models.CharField(
        max_length=12,
        choices=BOOKMARK_DATE_ROUTE_CHOICES,
        blank=False,
        default=BOOKMARK_DATE_ROUTE_SNAPSHOT,
    )
    bookmark_description_display = models.CharField(
        max_length=10,
        choices=BOOKMARK_DESCRIPTION_DISPLAY_CHOICES,
        blank=False,
        default=BOOKMARK_DESCRIPTION_DISPLAY_INLINE,
    )
    bookmark_description_max_lines = models.IntegerField(
        null=False,
        default=1,
    )
    bookmark_link_target = models.CharField(
        max_length=10,
        choices=BOOKMARK_LINK_TARGET_CHOICES,
        blank=False,
        default=BOOKMARK_LINK_TARGET_BLANK,
    )
    web_archive_integration = models.CharField(
        max_length=10,
        choices=WEB_ARCHIVE_INTEGRATION_CHOICES,
        blank=False,
        default=WEB_ARCHIVE_INTEGRATION_DISABLED,
    )
    tag_search = models.CharField(
        max_length=10,
        choices=TAG_SEARCH_CHOICES,
        blank=False,
        default=TAG_SEARCH_STRICT,
    )
    tag_grouping = models.CharField(
        max_length=12,
        choices=TAG_GROUPING_CHOICES,
        blank=False,
        default=TAG_GROUPING_ALPHABETICAL,
    )
    legacy_search = models.BooleanField(default=False, null=False)
    enable_sharing = models.BooleanField(default=False, null=False)
    enable_public_sharing = models.BooleanField(default=False, null=False)
    enable_favicons = models.BooleanField(default=True, null=False)
    enable_preview_images = models.BooleanField(default=False, null=False)
    enable_preview_image_placeholders = models.BooleanField(default=True, null=False)
    display_url = models.BooleanField(default=False, null=False)
    display_view_bookmark_action = models.BooleanField(default=True, null=False)
    display_edit_bookmark_action = models.BooleanField(default=True, null=False)
    display_archive_bookmark_action = models.BooleanField(default=True, null=False)
    display_remove_bookmark_action = models.BooleanField(default=True, null=False)
    display_read_bookmark_action = models.BooleanField(default=True, null=False)
    bookmark_actions = models.JSONField(default=list, blank=True, null=False)
    bookmark_statuses = models.JSONField(default=list, blank=True, null=False)
    bookmark_quick_edits = models.JSONField(default=list, blank=True, null=False)
    bookmark_action_display_mode = models.CharField(
        max_length=10,
        choices=ACTION_DISPLAY_MODE_CHOICES,
        blank=False,
        default=ACTION_DISPLAY_MODE_TEXT,
    )
    bookmark_status_display_mode = models.CharField(
        max_length=10,
        choices=ACTION_DISPLAY_MODE_CHOICES,
        blank=False,
        default=ACTION_DISPLAY_MODE_ICON,
    )
    bookmark_quick_edit_display_mode = models.CharField(
        max_length=10,
        choices=ACTION_DISPLAY_MODE_CHOICES,
        blank=False,
        default=ACTION_DISPLAY_MODE_ICON,
    )
    permanent_notes = models.BooleanField(default=False, null=False)
    # 健康状态过期窗口（天）：None 表示用全局默认 settings.LINK_HEALTH_MAX_AGE_DAYS；
    health_max_age_days = models.IntegerField(
        blank=True, null=True, default=None,
        verbose_name=_("Result validity (days)"),
        help_text=_(
            "How many days a health check result stays valid before it is "
            "considered expired. Leave empty to use the global default "
            "(LINK_HEALTH_MAX_AGE_DAYS, default 15)."
        ),
    )
    # 健康检查并发 workers：None 表示用全局默认 settings.LD_HEALTH_CHECK_WORKERS
    health_check_workers = models.IntegerField(
        blank=True, null=True, default=None,
        verbose_name=_("Check concurrency"),
        help_text=_(
            "Number of parallel health check workers. Leave empty to use the "
            "global default (LD_HEALTH_CHECK_WORKERS, default 8)."
        ),
    )
    # 每域名限流间隔（秒）：None 表示用全局默认 LD_METADATA_DOMAIN_COOLDOWN_SEC
    health_domain_interval = models.FloatField(
        blank=True, null=True, default=None,
        verbose_name=_("Per-domain interval (seconds)"),
        help_text=_(
            "Minimum interval between requests to the same domain. Leave "
            "empty to use the global default (LD_METADATA_DOMAIN_COOLDOWN_SEC)."
        ),
    )
    custom_css = models.TextField(blank=True, null=False)
    custom_css_hash = models.CharField(blank=True, null=False, max_length=32)
    custom_domain_root = models.TextField(blank=True, null=False, default="")
    auto_tagging_rules = models.TextField(blank=True, null=False)
    search_preferences = models.JSONField(default=dict, null=False)
    trash_search_preferences = models.JSONField(default=dict, null=False)
    highlights_search_preferences = models.JSONField(default=dict, null=False)
    sidebar_modules = models.JSONField(default=list, blank=True, null=False)
    highlights_sidebar_modules = models.JSONField(default=list, blank=True, null=False)
    enable_automatic_html_snapshots = models.BooleanField(default=True, null=False)
    default_mark_unread = models.BooleanField(default=True, null=False)
    default_mark_shared = models.BooleanField(default=False, null=False)
    items_per_page = models.IntegerField(
        null=False, default=30, validators=[MinValueValidator(10)]
    )
    highlights_per_page = models.IntegerField(
        null=False, default=50, validators=[MinValueValidator(1)]
    )
    sticky_header_controls = models.BooleanField(default=True, null=False)
    sticky_pagination = models.BooleanField(default=True, null=False)
    sticky_side_panel = models.BooleanField(default=True, null=False)
    show_sidebar = models.BooleanField(default=True, null=False)
    hide_bundles = models.BooleanField(default=False, null=False)
    reader_settings = models.JSONField(default=dict, null=False)
    bookmark_quick_tags = models.JSONField(default=list, blank=True, null=False)
    bookmark_toolbar_modules = models.JSONField(default=list, blank=True, null=False)
    bookmark_toolbar_auto_hide = models.BooleanField(default=False, null=False)

    # 随机按钮设置
    RANDOM_MODE_LIST = "list"
    RANDOM_MODE_SINGLE = "single"
    RANDOM_MODE_CHOICES = [
        (RANDOM_MODE_LIST, _("List")),
        (RANDOM_MODE_SINGLE, _("Single")),
    ]

    RANDOM_TARGET_URL = "url"
    RANDOM_TARGET_READER = "reader"
    RANDOM_TARGET_SNAPSHOT = "snapshot"
    RANDOM_TARGET_DETAILS = "details"
    RANDOM_TARGET_CHOICES = [
        (RANDOM_TARGET_URL, _("URL")),
        (RANDOM_TARGET_READER, _("Reader")),
        (RANDOM_TARGET_SNAPSHOT, _("Snapshot")),
        (RANDOM_TARGET_DETAILS, _("Details")),
    ]

    enable_random_button = models.BooleanField(default=True, null=False)
    random_mode = models.CharField(
        max_length=10,
        choices=RANDOM_MODE_CHOICES,
        blank=False,
        default=RANDOM_MODE_LIST,
    )
    random_target = models.CharField(
        max_length=10,
        choices=RANDOM_TARGET_CHOICES,
        blank=False,
        default=RANDOM_TARGET_URL,
    )

    # Summary display preferences
    SUM_MODE_CALENDAR = "calendar"
    SUM_MODE_HEATMAP = "heatmap"
    SUM_MODE_CHOICES = [
        (SUM_MODE_CALENDAR, _("Calendar")),
        (SUM_MODE_HEATMAP, _("Heatmap")),
    ]
    sum_mode = models.CharField(
        max_length=20,
        choices=SUM_MODE_CHOICES,
        blank=False,
        default=SUM_MODE_HEATMAP,
    )
    sum_show_weekdays = models.BooleanField(default=False, null=False)
    sum_show_details = models.BooleanField(default=True, null=False)

    # Domain display preferences
    DOMAIN_VIEW_FULL = "full"
    DOMAIN_VIEW_ICON = "icon"
    DOMAIN_VIEW_CHOICES = [
        (DOMAIN_VIEW_FULL, _("Full")),
        (DOMAIN_VIEW_ICON, _("Icon")),
    ]
    domain_view_mode = models.CharField(
        max_length=20,
        choices=DOMAIN_VIEW_CHOICES,
        blank=False,
        default=DOMAIN_VIEW_ICON,
    )
    domain_compact_mode = models.BooleanField(default=True, null=False)
    highlights_domain_view_mode = models.CharField(
        max_length=20,
        choices=DOMAIN_VIEW_CHOICES,
        blank=False,
        default=DOMAIN_VIEW_ICON,
    )
    highlights_domain_compact_mode = models.BooleanField(default=True, null=False)
    highlights_tag_grouping = models.CharField(
        max_length=12,
        choices=TAG_GROUPING_CHOICES,
        blank=False,
        default=TAG_GROUPING_ALPHABETICAL,
    )
    show_highlights_sidebar = models.BooleanField(default=True, null=False)
    highlights_sticky_header_controls = models.BooleanField(default=True, null=False)
    highlights_sticky_pagination = models.BooleanField(default=True, null=False)
    highlights_sticky_side_panel = models.BooleanField(default=True, null=False)
    highlight_copy_format = models.JSONField(default=dict, blank=True, null=False)

    HIGHLIGHT_COPY_ACTION_BOTH = "both"
    HIGHLIGHT_COPY_ACTION_HIGHLIGHT = "highlight"
    HIGHLIGHT_COPY_ACTION_NOTE = "note"
    HIGHLIGHT_COPY_ACTION_CHOICES = [
        (HIGHLIGHT_COPY_ACTION_BOTH, _("Copy all")),
        (HIGHLIGHT_COPY_ACTION_HIGHLIGHT, _("Copy highlight only")),
        (HIGHLIGHT_COPY_ACTION_NOTE, _("Copy annotation only")),
    ]
    highlight_copy_default_action = models.CharField(
        max_length=20,
        choices=HIGHLIGHT_COPY_ACTION_CHOICES,
        blank=False,
        default=HIGHLIGHT_COPY_ACTION_BOTH,
    )

    @classmethod
    def normalize_quick_tag(cls, qt: dict) -> dict:
        tag_name = (qt.get("tag_name") or "").strip()
        tag_names = [sanitize_tag_name(t) for t in tag_name.split() if t.strip()]
        tag_names = list(dict.fromkeys(tag_names))

        label = (qt.get("label") or "").strip()
        short_label = (qt.get("short_label") or "").strip()
        if not short_label:
            short_label = tag_names[0][0] if tag_names else ""

        icon_name = (qt.get("icon_name") or "").strip()
        display_position = qt.get("display_position", "direct")
        if display_position not in ("direct", "submenu"):
            display_position = "direct"
        display_mode = qt.get("display_mode", "icon")
        if display_mode not in ("text", "icon"):
            display_mode = "text"
        enabled = bool(qt.get("enabled", True))

        return {
            "tag_name": tag_name,
            "tag_names": tag_names,
            "label": label,
            "short_label": short_label,
            "icon_name": icon_name,
            "display_position": display_position,
            "display_mode": display_mode,
            "enabled": enabled,
        }

    @classmethod
    def normalize_bookmark_quick_tags(cls, quick_tags: list | None) -> list[dict]:
        if not isinstance(quick_tags, list):
            return []
        return [
            cls.normalize_quick_tag(qt) for qt in quick_tags if isinstance(qt, dict)
        ]

    def get_bookmark_quick_tags(self) -> list[dict]:
        return self.normalize_bookmark_quick_tags(self.bookmark_quick_tags)

    def save(self, *args, **kwargs):
        if self.custom_css:
            self.custom_css_hash = hashlib.md5(
                self.custom_css.encode("utf-8")
            ).hexdigest()
        else:
            self.custom_css_hash = ""
        super().save(*args, **kwargs)

    @classmethod
    def default_sidebar_modules(cls, bundles_enabled: bool = True) -> list[dict]:
        return [
            {"key": cls.SIDEBAR_MODULE_SUMMARY, "enabled": True},
            {"key": cls.SIDEBAR_MODULE_BUNDLES, "enabled": bundles_enabled},
            {"key": cls.SIDEBAR_MODULE_DOMAINS, "enabled": True},
            {"key": cls.SIDEBAR_MODULE_TAGS, "enabled": True},
            {"key": cls.SIDEBAR_MODULE_COLORS, "enabled": True},
        ]

    @classmethod
    def normalize_sidebar_modules(
        cls, sidebar_modules: list | None, bundles_enabled: bool = True
    ) -> list[dict]:
        if not isinstance(sidebar_modules, list) or len(sidebar_modules) == 0:
            return cls.default_sidebar_modules(bundles_enabled)

        normalized = []
        seen = set()
        defaults = {
            item["key"]: item["enabled"]
            for item in cls.default_sidebar_modules(bundles_enabled)
        }

        for item in sidebar_modules:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in defaults or key in seen:
                continue
            normalized.append(
                {
                    "key": key,
                    "enabled": bool(item.get("enabled", defaults[key])),
                }
            )
            seen.add(key)

        for key, enabled in defaults.items():
            if key not in seen:
                normalized.append({"key": key, "enabled": enabled})

        return normalized

    def get_sidebar_modules(self) -> list[dict]:
        return self.normalize_sidebar_modules(
            self.sidebar_modules,
            bundles_enabled=not self.hide_bundles,
        )

    def get_sidebar_module_items(self) -> list[dict]:
        return [
            {
                **item,
                "label": self.SIDEBAR_MODULE_LABELS[item["key"]],
            }
            for item in self.get_sidebar_modules()
        ]

    @classmethod
    def default_highlights_sidebar_modules(cls) -> list[dict]:
        return [
            {"key": cls.SIDEBAR_MODULE_COLORS, "enabled": True},
            {"key": cls.SIDEBAR_MODULE_DOMAINS, "enabled": True},
            {"key": cls.SIDEBAR_MODULE_TAGS, "enabled": True},
        ]

    @staticmethod
    def _normalize_module_list(
        raw: list | None, defaults: dict[str, bool]
    ) -> list[dict]:
        """Normalize a module list: validate keys, fill missing defaults, deduplicate."""
        if not isinstance(raw, list) or len(raw) == 0:
            return [{"key": k, "enabled": v} for k, v in defaults.items()]

        normalized = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in defaults or key in seen:
                continue
            normalized.append(
                {"key": key, "enabled": bool(item.get("enabled", defaults[key]))}
            )
            seen.add(key)

        for key, enabled in defaults.items():
            if key not in seen:
                normalized.append({"key": key, "enabled": enabled})

        return normalized

    def get_highlights_sidebar_modules(self) -> list[dict]:
        defaults = {
            item["key"]: item["enabled"]
            for item in self.default_highlights_sidebar_modules()
        }
        return self._normalize_module_list(self.highlights_sidebar_modules, defaults)

    def get_highlights_sidebar_module_items(self) -> list[dict]:
        return [
            {
                **item,
                "label": self.SIDEBAR_MODULE_LABELS[item["key"]],
            }
            for item in self.get_highlights_sidebar_modules()
        ]

    @classmethod
    def default_bookmark_toolbar_modules(cls) -> list[dict]:
        return [{"key": key, "enabled": True} for key in cls.TOOLBAR_MODULE_KEYS]

    @classmethod
    def normalize_bookmark_toolbar_modules(
        cls, toolbar_modules: list | None
    ) -> list[dict]:
        if not isinstance(toolbar_modules, list) or len(toolbar_modules) == 0:
            return cls.default_bookmark_toolbar_modules()

        normalized = []
        seen = set()
        defaults = {key: True for key in cls.TOOLBAR_MODULE_KEYS}

        for item in toolbar_modules:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in defaults or key in seen:
                continue
            normalized.append(
                {
                    "key": key,
                    "enabled": bool(item.get("enabled", defaults[key])),
                }
            )
            seen.add(key)

        for key in cls.TOOLBAR_MODULE_KEYS:
            if key not in seen:
                normalized.append({"key": key, "enabled": defaults[key]})

        return normalized

    def get_bookmark_toolbar_modules(self) -> list[dict]:
        return self.normalize_bookmark_toolbar_modules(self.bookmark_toolbar_modules)

    def get_bookmark_toolbar_module_items(self) -> list[dict]:
        return [
            {
                **item,
                "label": self.TOOLBAR_MODULE_LABELS[item["key"]],
            }
            for item in self.get_bookmark_toolbar_modules()
        ]

    @classmethod
    def default_bookmark_actions(cls) -> list[dict]:
        # 高亮动作默认关闭
        defaults = {key: True for key in cls.ACTION_KEYS}
        defaults[cls.ACTION_HIGHLIGHT] = False
        return [{"key": key, "enabled": defaults[key]} for key in cls.ACTION_KEYS]

    @classmethod
    def normalize_bookmark_actions(cls, bookmark_actions: list | None) -> list[dict]:
        if not isinstance(bookmark_actions, list) or len(bookmark_actions) == 0:
            return cls.default_bookmark_actions()

        defaults = {key: True for key in cls.ACTION_KEYS}
        defaults[cls.ACTION_HIGHLIGHT] = False
        normalized = []
        seen = set()

        for item in bookmark_actions:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in defaults or key in seen:
                continue
            normalized.append(
                {
                    "key": key,
                    "enabled": bool(item.get("enabled", defaults[key])),
                }
            )
            seen.add(key)

        for key, enabled in defaults.items():
            if key not in seen:
                normalized.append({"key": key, "enabled": enabled})

        return normalized

    def get_bookmark_actions(self) -> list[dict]:
        if self.bookmark_actions:
            return self.normalize_bookmark_actions(self.bookmark_actions)
        # Fall back to legacy boolean fields
        return [
            {"key": key, "enabled": getattr(self, self.ACTION_FIELD_MAP[key], True)}
            for key in self.ACTION_FIELD_MAP
        ]

    def get_bookmark_action_items(self) -> list[dict]:
        return [
            {
                **item,
                "label": self.ACTION_LABELS[item["key"]],
            }
            for item in self.get_bookmark_actions()
        ]

    @property
    def health_max_age(self) -> int:
        """健康状态有效期（天）：优先用户自定义，缺省用全局默认。"""
        if self.health_max_age_days is not None:
            return self.health_max_age_days
        return getattr(settings, "LINK_HEALTH_MAX_AGE_DAYS", 15)

    @classmethod
    def default_bookmark_statuses(cls) -> list[dict]:
        return [{"key": key, "enabled": True} for key in cls.STATUS_KEYS]

    @classmethod
    def normalize_bookmark_statuses(cls, bookmark_statuses: list | None) -> list[dict]:
        if not isinstance(bookmark_statuses, list) or len(bookmark_statuses) == 0:
            return cls.default_bookmark_statuses()

        defaults = {key: True for key in cls.STATUS_KEYS}
        normalized = []
        seen = set()

        for item in bookmark_statuses:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in defaults or key in seen:
                continue
            normalized.append(
                {
                    "key": key,
                    "enabled": bool(item.get("enabled", defaults[key])),
                }
            )
            seen.add(key)

        for key, enabled in defaults.items():
            if key not in seen:
                normalized.append({"key": key, "enabled": enabled})

        return normalized

    def get_bookmark_statuses(self) -> list[dict]:
        return self.normalize_bookmark_statuses(self.bookmark_statuses)

    def get_bookmark_status_items(self) -> list[dict]:
        return [
            {
                **item,
                "label": self.STATUS_LABELS[item["key"]],
            }
            for item in self.get_bookmark_statuses()
        ]

    @classmethod
    def default_bookmark_quick_edits(cls) -> list[dict]:
        return [{"key": key, "enabled": True} for key in cls.QUICK_EDIT_KEYS]

    @classmethod
    def normalize_bookmark_quick_edits(
        cls, bookmark_quick_edits: list | None
    ) -> list[dict]:
        if not isinstance(bookmark_quick_edits, list) or len(bookmark_quick_edits) == 0:
            return cls.default_bookmark_quick_edits()

        defaults = {key: True for key in cls.QUICK_EDIT_KEYS}
        normalized = []
        seen = set()

        for item in bookmark_quick_edits:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key not in defaults or key in seen:
                continue
            normalized.append(
                {
                    "key": key,
                    "enabled": bool(item.get("enabled", defaults[key])),
                }
            )
            seen.add(key)

        for key, enabled in defaults.items():
            if key not in seen:
                normalized.append({"key": key, "enabled": enabled})

        return normalized

    def get_bookmark_quick_edits(self) -> list[dict]:
        return self.normalize_bookmark_quick_edits(self.bookmark_quick_edits)

    def get_bookmark_quick_edit_items(self) -> list[dict]:
        return [
            {
                **item,
                "label": self.QUICK_EDIT_LABELS[item["key"]],
            }
            for item in self.get_bookmark_quick_edits()
        ]


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = [
            "theme",
            "bookmark_date_display",
            "bookmark_date_route",
            "bookmark_description_display",
            "bookmark_description_max_lines",
            "bookmark_link_target",
            "web_archive_integration",
            "tag_search",
            "tag_grouping",
            "legacy_search",
            "enable_sharing",
            "enable_public_sharing",
            "enable_favicons",
            "enable_preview_images",
            "enable_preview_image_placeholders",
            "enable_automatic_html_snapshots",
            "display_url",
            "display_view_bookmark_action",
            "display_edit_bookmark_action",
            "display_archive_bookmark_action",
            "display_remove_bookmark_action",
            "display_read_bookmark_action",
            "bookmark_actions",
            "bookmark_statuses",
            "bookmark_quick_edits",
            "bookmark_quick_tags",
            "bookmark_toolbar_modules",
            "bookmark_toolbar_auto_hide",
            "bookmark_action_display_mode",
            "bookmark_status_display_mode",
            "bookmark_quick_edit_display_mode",
            "permanent_notes",
            "default_mark_unread",
            "default_mark_shared",
            "custom_css",
            "custom_domain_root",
            "auto_tagging_rules",
            "items_per_page",
            "highlights_per_page",
            "sticky_header_controls",
            "sticky_pagination",
            "sticky_side_panel",
            "show_sidebar",
            "hide_bundles",
            "enable_random_button",
            "random_mode",
            "random_target",
        ]


class UserProfileQuickSettingsForm(forms.ModelForm):
    SHARING_MODE_DISABLED = "disabled"
    SHARING_MODE_PRIVATE = "private"
    SHARING_MODE_PUBLIC = "public"
    SHARING_MODE_CHOICES = [
        (SHARING_MODE_DISABLED, _("Disabled")),
        (SHARING_MODE_PRIVATE, _("Private sharing")),
        (SHARING_MODE_PUBLIC, _("Public sharing")),
    ]

    show_sidebar = forms.BooleanField(required=False)
    show_highlights_sidebar = forms.BooleanField(required=False)
    enable_web_archive = forms.BooleanField(required=False)
    sharing_mode = forms.ChoiceField(choices=SHARING_MODE_CHOICES)
    sidebar_modules = forms.CharField()
    highlights_sidebar_modules = forms.CharField()
    bookmark_actions = forms.CharField()
    bookmark_statuses = forms.CharField()
    bookmark_quick_edits = forms.CharField()
    bookmark_quick_tags = forms.CharField(required=False)
    bookmark_toolbar_modules = forms.CharField()

    class Meta:
        model = UserProfile
        fields = [
            "theme",
            "bookmark_date_display",
            "bookmark_date_route",
            "bookmark_description_display",
            "bookmark_description_max_lines",
            "bookmark_link_target",
            "web_archive_integration",
            "tag_search",
            "tag_grouping",
            "legacy_search",
            "display_url",
            "bookmark_action_display_mode",
            "bookmark_status_display_mode",
            "bookmark_quick_edit_display_mode",
            "bookmark_toolbar_auto_hide",
            "permanent_notes",
            "default_mark_unread",
            "default_mark_shared",
            "enable_favicons",
            "enable_preview_images",
            "enable_preview_image_placeholders",
            "enable_automatic_html_snapshots",
            "items_per_page",
            "highlights_per_page",
            "highlights_sticky_header_controls",
            "highlights_sticky_pagination",
            "highlights_sticky_side_panel",
            "sticky_header_controls",
            "sticky_pagination",
            "sticky_side_panel",
            "enable_random_button",
            "random_mode",
            "random_target",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["show_sidebar"].initial = self.instance.show_sidebar
            self.fields[
                "show_highlights_sidebar"
            ].initial = self.instance.show_highlights_sidebar
            self.fields["enable_web_archive"].initial = (
                self.instance.web_archive_integration
                == self.instance.WEB_ARCHIVE_INTEGRATION_ENABLED
            )
            if self.instance.enable_public_sharing:
                self.fields["sharing_mode"].initial = self.SHARING_MODE_PUBLIC
            elif self.instance.enable_sharing:
                self.fields["sharing_mode"].initial = self.SHARING_MODE_PRIVATE
            else:
                self.fields["sharing_mode"].initial = self.SHARING_MODE_DISABLED
            self.fields["sidebar_modules"].initial = json.dumps(
                self.instance.get_sidebar_modules()
            )
            self.fields["highlights_sidebar_modules"].initial = json.dumps(
                self.instance.get_highlights_sidebar_modules()
            )
            self.fields["bookmark_actions"].initial = json.dumps(
                self.instance.get_bookmark_actions()
            )
            self.fields["bookmark_statuses"].initial = json.dumps(
                self.instance.get_bookmark_statuses()
            )
            self.fields["bookmark_quick_edits"].initial = json.dumps(
                self.instance.get_bookmark_quick_edits()
            )
            self.fields["bookmark_quick_tags"].initial = json.dumps(
                self.instance.get_bookmark_quick_tags()
            )
            self.fields["bookmark_toolbar_modules"].initial = json.dumps(
                self.instance.get_bookmark_toolbar_modules()
            )

    @property
    def sidebar_module_items(self) -> list[dict]:
        if self.is_bound:
            modules = self.data.get("sidebar_modules")
            try:
                parsed_modules = json.loads(modules) if modules else []
            except (TypeError, ValueError):
                parsed_modules = []
            normalized = UserProfile.normalize_sidebar_modules(
                parsed_modules,
                bundles_enabled=not self.instance.hide_bundles,
            )
            return [
                {
                    **item,
                    "label": UserProfile.SIDEBAR_MODULE_LABELS[item["key"]],
                }
                for item in normalized
            ]

        return self.instance.get_sidebar_module_items()

    @property
    def highlights_sidebar_module_items(self) -> list[dict]:
        if self.is_bound:
            modules = self.data.get("highlights_sidebar_modules")
            try:
                parsed = json.loads(modules) if modules else []
            except (TypeError, ValueError):
                parsed = []
            defaults = {
                item["key"]: item["enabled"]
                for item in UserProfile.default_highlights_sidebar_modules()
            }
            return [
                {**item, "label": UserProfile.SIDEBAR_MODULE_LABELS[item["key"]]}
                for item in UserProfile._normalize_module_list(parsed, defaults)
            ]

        return self.instance.get_highlights_sidebar_module_items()

    def clean_sidebar_modules(self):
        raw_value = self.cleaned_data["sidebar_modules"]
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(_("Invalid sidebar configuration.")) from None

        return UserProfile.normalize_sidebar_modules(
            parsed_value,
            bundles_enabled=not self.instance.hide_bundles,
        )

    def clean_highlights_sidebar_modules(self):
        raw_value = self.cleaned_data["highlights_sidebar_modules"]
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(_("Invalid sidebar configuration.")) from None
        defaults = {
            item["key"]: item["enabled"]
            for item in UserProfile.default_highlights_sidebar_modules()
        }
        return UserProfile._normalize_module_list(parsed_value, defaults)

    @property
    def bookmark_toolbar_module_items(self) -> list[dict]:
        if self.is_bound:
            modules = self.data.get("bookmark_toolbar_modules")
            try:
                parsed_modules = json.loads(modules) if modules else []
            except (TypeError, ValueError):
                parsed_modules = []
            normalized = UserProfile.normalize_bookmark_toolbar_modules(parsed_modules)
            return [
                {
                    **item,
                    "label": UserProfile.TOOLBAR_MODULE_LABELS[item["key"]],
                }
                for item in normalized
            ]

        return self.instance.get_bookmark_toolbar_module_items()

    def clean_bookmark_toolbar_modules(self):
        raw_value = self.cleaned_data["bookmark_toolbar_modules"]
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(_("Invalid toolbar configuration.")) from None

        return UserProfile.normalize_bookmark_toolbar_modules(parsed_value)

    @property
    def bookmark_action_items(self) -> list[dict]:
        if self.is_bound:
            actions = self.data.get("bookmark_actions")
            try:
                parsed_actions = json.loads(actions) if actions else []
            except (TypeError, ValueError):
                parsed_actions = []
            normalized = UserProfile.normalize_bookmark_actions(parsed_actions)
            return [
                {
                    **item,
                    "label": UserProfile.ACTION_LABELS[item["key"]],
                }
                for item in normalized
            ]

        return self.instance.get_bookmark_action_items()

    def clean_bookmark_actions(self):
        raw_value = self.cleaned_data["bookmark_actions"]
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(
                _("Invalid bookmark actions configuration.")
            ) from None

        return UserProfile.normalize_bookmark_actions(parsed_value)

    @property
    def bookmark_status_items(self) -> list[dict]:
        if self.is_bound:
            statuses = self.data.get("bookmark_statuses")
            try:
                parsed_statuses = json.loads(statuses) if statuses else []
            except (TypeError, ValueError):
                parsed_statuses = []
            normalized = UserProfile.normalize_bookmark_statuses(parsed_statuses)
            return [
                {
                    **item,
                    "label": UserProfile.STATUS_LABELS[item["key"]],
                }
                for item in normalized
            ]

        return self.instance.get_bookmark_status_items()

    def clean_bookmark_statuses(self):
        raw_value = self.cleaned_data["bookmark_statuses"]
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(
                _("Invalid bookmark status configuration.")
            ) from None

        return UserProfile.normalize_bookmark_statuses(parsed_value)

    @property
    def bookmark_quick_edit_items(self) -> list[dict]:
        if self.is_bound:
            quick_edits = self.data.get("bookmark_quick_edits")
            try:
                parsed_quick_edits = json.loads(quick_edits) if quick_edits else []
            except (TypeError, ValueError):
                parsed_quick_edits = []
            normalized = UserProfile.normalize_bookmark_quick_edits(parsed_quick_edits)
            return [
                {
                    **item,
                    "label": UserProfile.QUICK_EDIT_LABELS[item["key"]],
                }
                for item in normalized
            ]

        return self.instance.get_bookmark_quick_edit_items()

    def clean_bookmark_quick_edits(self):
        raw_value = self.cleaned_data["bookmark_quick_edits"]
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(
                _("Invalid bookmark quick edit configuration.")
            ) from None

        return UserProfile.normalize_bookmark_quick_edits(parsed_value)

    @property
    def bookmark_quick_tag_items(self) -> list[dict]:
        if self.is_bound:
            try:
                items = self.clean_bookmark_quick_tags()
            except Exception:
                return []
        else:
            items = self.instance.get_bookmark_quick_tags()
        # 为每个快捷标签加载图标 SVG 数据
        from bookmarks.services.icon_loader import load_quick_tags_icon

        for item in items:
            icon_name = item.get("icon_name")
            if icon_name:
                item["icon_data"] = load_quick_tags_icon(icon_name)
            else:
                item["icon_data"] = None
        return items

    def clean_bookmark_quick_tags(self):
        raw_value = self.cleaned_data.get("bookmark_quick_tags", "[]")
        if not raw_value:
            return []
        try:
            parsed_value = json.loads(raw_value)
        except (TypeError, ValueError):
            raise forms.ValidationError(
                _("Invalid bookmark quick tags configuration.")
            ) from None

        return UserProfile.normalize_bookmark_quick_tags(parsed_value)

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.show_sidebar = self.cleaned_data["show_sidebar"]
        profile.show_highlights_sidebar = self.cleaned_data.get(
            "show_highlights_sidebar", True
        )

        profile.web_archive_integration = (
            self.instance.WEB_ARCHIVE_INTEGRATION_ENABLED
            if self.cleaned_data["enable_web_archive"]
            else self.instance.WEB_ARCHIVE_INTEGRATION_DISABLED
        )

        sharing_mode = self.cleaned_data["sharing_mode"]
        profile.enable_sharing = sharing_mode != self.SHARING_MODE_DISABLED
        profile.enable_public_sharing = sharing_mode == self.SHARING_MODE_PUBLIC
        if sharing_mode == self.SHARING_MODE_DISABLED:
            profile.default_mark_shared = False

        profile.sidebar_modules = self.cleaned_data["sidebar_modules"]
        profile.highlights_sidebar_modules = self.cleaned_data.get(
            "highlights_sidebar_modules", []
        )
        profile.bookmark_toolbar_modules = self.cleaned_data["bookmark_toolbar_modules"]

        # Sync bookmark actions to JSON field and legacy boolean fields
        actions = self.cleaned_data["bookmark_actions"]
        profile.bookmark_actions = actions
        for action in actions:
            field_name = UserProfile.ACTION_FIELD_MAP.get(action["key"])
            if field_name:
                setattr(profile, field_name, action["enabled"])

        profile.bookmark_statuses = self.cleaned_data["bookmark_statuses"]
        profile.bookmark_quick_edits = self.cleaned_data["bookmark_quick_edits"]
        profile.bookmark_quick_tags = self.cleaned_data.get("bookmark_quick_tags", [])

        if commit:
            profile.save()

        return profile


class UserProfileHealthForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = [
            "health_max_age_days",
            "health_check_workers",
            "health_domain_interval",
        ]

    def clean_health_max_age_days(self):
        value = self.cleaned_data.get("health_max_age_days")
        if value is not None and value < 0:
            raise forms.ValidationError(_("Must be at least 0 days (0 = never expires)."))
        return value

    def clean_health_check_workers(self):
        value = self.cleaned_data.get("health_check_workers")
        if value is not None and value < 1:
            raise forms.ValidationError(_("Must be at least 1."))
        return value

    def clean_health_domain_interval(self):
        value = self.cleaned_data.get("health_domain_interval")
        if value is not None and value < 0:
            raise forms.ValidationError(_("Must not be negative."))
        return value


class UserProfileCustomCssForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["custom_css"]


class UserProfileAutoTaggingRulesForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["auto_tagging_rules"]


class UserProfileCustomDomainRootForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["custom_domain_root"]


class UserProfileHighlightCopyFormatForm(forms.ModelForm):
    DEFAULT_ITEM_FORMAT = "> ${highlight}\n\n${annotation}"
    DEFAULT_SEPARATOR = "\n\n---\n\n"

    item_format = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Item format"),
        strip=False,
    )
    separator = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label=_("Separator"),
        strip=False,
    )

    class Meta:
        model = UserProfile
        fields = ["highlight_copy_format", "highlight_copy_default_action"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fmt = (
            self.instance.highlight_copy_format
            if self.instance and self.instance.pk
            else {}
        )
        self.fields["item_format"].initial = fmt.get(
            "item_format", self.DEFAULT_ITEM_FORMAT
        )
        self.fields["separator"].initial = fmt.get("separator", self.DEFAULT_SEPARATOR)

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.highlight_copy_format = {
            "item_format": self.cleaned_data.get("item_format", ""),
            "separator": self.cleaned_data.get("separator", ""),
        }
        if commit:
            profile.save()
        return profile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.profile.save()


class Toast(models.Model):
    key = models.CharField(max_length=50)
    message = models.TextField()
    acknowledged = models.BooleanField(default=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE)

    I18N_MESSAGES = {
        "new_search_toast": _(
            "This version replaces the search engine with a new implementation that supports logical operators (and, or, not). If you run into any issues with the new search, you can switch back to the old one by enabling legacy search in the settings."
        ),
    }

    @property
    def display_message(self):
        return self.I18N_MESSAGES.get(self.key, self.message)


class FeedToken(models.Model):
    """
    Adapted from authtoken.models.Token
    """

    key = models.CharField(max_length=40, primary_key=True)
    user = models.OneToOneField(
        User,
        related_name="feed_token",
        on_delete=models.CASCADE,
    )
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    @classmethod
    def generate_key(cls):
        return binascii.hexlify(os.urandom(20)).decode()

    def __str__(self):
        return self.key


class ApiToken(models.Model):
    key = models.CharField(max_length=40, unique=True)
    user = models.ForeignKey(
        User,
        related_name="api_tokens",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=128, blank=False)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    @classmethod
    def generate_key(cls):
        return binascii.hexlify(os.urandom(20)).decode()

    def __str__(self):
        return f"{self.name} ({self.user.username})"


class GlobalSettings(models.Model):
    LANDING_PAGE_LOGIN = "login"
    LANDING_PAGE_SHARED_BOOKMARKS = "shared_bookmarks"
    LANDING_PAGE_CHOICES = [
        (LANDING_PAGE_LOGIN, _("Login page")),
        (LANDING_PAGE_SHARED_BOOKMARKS, _("Shared page")),
    ]

    landing_page = models.CharField(
        max_length=50,
        choices=LANDING_PAGE_CHOICES,
        blank=False,
        default=LANDING_PAGE_LOGIN,
    )
    guest_profile_user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    enable_link_prefetch = models.BooleanField(default=False, null=False)

    @classmethod
    def get(cls):
        instance = GlobalSettings.objects.first()
        if not instance:
            instance = GlobalSettings()
            instance.save()
        return instance

    def save(self, *args, **kwargs):
        if not self.pk and GlobalSettings.objects.exists():
            raise Exception("There is already one instance of GlobalSettings")
        return super().save(*args, **kwargs)


class GlobalSettingsForm(forms.ModelForm):
    class Meta:
        model = GlobalSettings
        fields = ["landing_page", "guest_profile_user", "enable_link_prefetch"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["guest_profile_user"].empty_label = _("Standard profile")


class CheckJob(models.Model):
    """书签健康检查任务（纯手动触发，支持中断与续跑）。

    状态机：pending → running → completed / failed
            running → interrupted（用户暂停或心跳超时）→ running（继续）/ cancelled（放弃）
    """

    STATE_PENDING = "pending"
    STATE_RUNNING = "running"
    STATE_INTERRUPTED = "interrupted"
    STATE_CANCELLED = "cancelled"
    STATE_COMPLETED = "completed"
    STATE_FAILED = "failed"

    STATE_CHOICES = [
        (STATE_PENDING, "Pending"),
        (STATE_RUNNING, "Running"),
        (STATE_INTERRUPTED, "Interrupted"),
        (STATE_CANCELLED, "Cancelled"),
        (STATE_COMPLETED, "Completed"),
        (STATE_FAILED, "Failed"),
    ]

    # scope: {"mode": "all" | "query" | "ids", "query": str, "ids": [int]}
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    scope = models.JSONField(default=dict, blank=True)
    state = models.CharField(
        max_length=32, default=STATE_PENDING, choices=STATE_CHOICES
    )
    total = models.IntegerField(default=0)
    done = models.IntegerField(default=0)
    heartbeat = models.DateTimeField(null=True, blank=True)
    stop_requested = models.BooleanField(default=False)
    # 暂停计时：paused_sec 为累计暂停秒数（resume 时累加）；paused_at 为
    # 当前暂停起点（暂停中非空）。任务进行时长 = now - started_at - paused_sec。
    paused_sec = models.IntegerField(default=0)
    paused_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_active(self) -> bool:
        return self.state in (self.STATE_PENDING, self.STATE_RUNNING)

    def __str__(self):
        return f"CheckJob #{self.pk} ({self.state}, {self.done}/{self.total})"
