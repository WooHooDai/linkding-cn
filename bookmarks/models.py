import hashlib
import logging
import os
import re
import calendar
from functools import cached_property
from typing import List
from datetime import date, timedelta, datetime


import binascii
from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.http import QueryDict

from bookmarks.utils import unique, normalize_url
from bookmarks.validators import BookmarkURLValidator

logger = logging.getLogger(__name__)


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


def build_tag_string(tag_names: List[str], delimiter: str = ","):
    return delimiter.join(tag_names)


class Bookmark(models.Model):
    url = models.CharField(max_length=2048, validators=[BookmarkURLValidator()])
    url_normalized = models.CharField(max_length=2048, blank=True, db_index=True)
    title = models.CharField(max_length=512, blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    preview_image_remote_url = models.URLField(max_length=2048, blank=True)
    # Obsolete field, kept to not remove column when generating migrations
    website_title = models.CharField(max_length=512, blank=True, null=True)
    # Obsolete field, kept to not remove column when generating migrations
    website_description = models.TextField(blank=True, null=True)
    web_archive_snapshot_url = models.CharField(max_length=2048, blank=True)
    favicon_file = models.CharField(max_length=512, blank=True)
    preview_image_file = models.CharField(max_length=512, blank=True)
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


class BookmarkAsset(models.Model):
    TYPE_SNAPSHOT = "snapshot"
    TYPE_UPLOAD = "upload"

    CONTENT_TYPE_HTML = "text/html; charset=utf-8"

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

    @property
    def download_name(self):
        return (
            f"{self.display_name}.html"
            if self.asset_type == BookmarkAsset.TYPE_SNAPSHOT
            else self.display_name
        )

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
    show_count = models.BooleanField(default=True, verbose_name="显示书签数")
    is_folder = models.BooleanField(default=True)
    search_params = models.JSONField(default=dict, blank=True, verbose_name="搜索参数")

    def __str__(self):
        return self.name

    @property
    def search_object(self):
        """返回基于配置的BookmarkSearch对象"""
        params = self.search_params.copy()
        
        # 反序列化：开始日期、结束日期字符串转date对象
        for date_field in ['date_filter_start', 'date_filter_end']:
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

    FILTER_DATE_OFF = "off"
    FILTER_DATE_BY_ADDED = "added"
    FILTER_DATE_BY_MODIFIED = "modified"
    FILTER_DATE_BY_DELETED = "deleted"

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
    ]
    preferences = ["sort", "shared", "unread", "tagged", "date_filter_by", "date_filter_type", "date_filter_relative_string"]
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
            days_since_monday = today.weekday() # weekday() 返回 0-6，0 是周一，6 是周日
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
            m = re.match(r"last_(\d+)_(day|week|month|year)s?", date_filter_relative_string)
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
        date_filter_start = None,
        date_filter_end = None,
        preferences: dict = None,
        request: any = None,
    ):
        if not preferences:
            preferences = {}
        self.defaults = {**BookmarkSearch.defaults, **preferences}
        self.request = request

        # 合并参数：user参数 > bundle参数 > default参数
        user_params = {
            'q': q, 'user': user, 'bundle': bundle, 'sort': sort, 'shared': shared,
            'unread': unread, 'tagged': tagged, 'modified_since': modified_since, 'added_since': added_since,
            'deleted_since': deleted_since, 'date_filter_by': date_filter_by,
            'date_filter_type': date_filter_type, 'date_filter_relative_string': date_filter_relative_string,
            'date_filter_start': date_filter_start, 'date_filter_end': date_filter_end
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
        if (self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE and 
            self.date_filter_relative_string):
            start, _ = self.parse_relative_date_string(self.date_filter_relative_string)
            if start:
                return start
        return self.__dict__.get('date_filter_start')

    @property
    def date_filter_end(self):
        if (self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE and 
            self.date_filter_relative_string):
            _, end = self.parse_relative_date_string(self.date_filter_relative_string)
            if end:
                return end
        return self.__dict__.get('date_filter_end')

    @date_filter_start.setter
    def date_filter_start(self, value):
        self.__dict__['date_filter_start'] = value

    @date_filter_end.setter
    def date_filter_end(self, value):
        self.__dict__['date_filter_end'] = value

    def is_modified(self, param):
        value = self.__dict__[param]
        
        # 日期筛选类型为相对时，隐藏url参数中的开始日期、结束日期
        if (self.date_filter_type == self.FILTER_DATE_TYPE_RELATIVE and 
            param in ['date_filter_start', 'date_filter_end']):
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
                        if self.date_filter_start == bundle_start and self.date_filter_end == bundle_end:
                            continue

                if value is not None and value != "":
                    if value != bundle_value:  # 用户参数与Bundle参数不同时url包含该参数
                        if isinstance(value, models.Model):
                            query_params[param] = value.id
                        else:
                            query_params[param] = value
        else:
            # 没有Bundle时，使用原逻辑（只包含modified_params）
            for param in self.modified_params:
                value = self.__dict__[param]
                if isinstance(value, models.Model):
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
            value = query_dict.get(param)
            if value:
                initial_values[param] = value
        
        if bundle:
            search = bundle.search_object
            for param, value in initial_values.items(): #合并用户参数
                setattr(search, param, value)
            return search
        else:
            return BookmarkSearch(
                **initial_values, preferences=preferences, request=request
            )

class BookmarkSearchForm(forms.Form):
    SORT_CHOICES = [
        (BookmarkSearch.SORT_ADDED_ASC, "添加时间 ↑"),
        (BookmarkSearch.SORT_ADDED_DESC, "添加时间 ↓"),
        (BookmarkSearch.SORT_TITLE_ASC, "标题 ↑"),
        (BookmarkSearch.SORT_TITLE_DESC, "标题 ↓"),
        (BookmarkSearch.SORT_RANDOM, "随机"),
    ]
    FILTER_SHARED_CHOICES = [
        (BookmarkSearch.FILTER_SHARED_OFF, "关闭"),
        (BookmarkSearch.FILTER_SHARED_SHARED, "已分享"),
        (BookmarkSearch.FILTER_SHARED_UNSHARED, "未分享"),
    ]
    FILTER_UNREAD_CHOICES = [
        (BookmarkSearch.FILTER_UNREAD_OFF, "关闭"),
        (BookmarkSearch.FILTER_UNREAD_YES, "未读"),
        (BookmarkSearch.FILTER_UNREAD_NO, "已读"),
    ]
    FILTER_TAGGED_CHOICES = [
        (BookmarkSearch.FILTER_TAGGED_OFF, "关闭"),
        (BookmarkSearch.FILTER_TAGGED_TAGGED, "有标签"),
        (BookmarkSearch.FILTER_TAGGED_UNTAGGED, "无标签"),
    ]
    FILTER_DATE_BY_CHOICES = [
        (BookmarkSearch.FILTER_DATE_OFF, "关闭"),
        (BookmarkSearch.FILTER_DATE_BY_ADDED, "添加"),
        (BookmarkSearch.FILTER_DATE_BY_MODIFIED, "修改")
    ]
    FILTER_DATE_TYPE_CHOICES = [
        (BookmarkSearch.FILTER_DATE_TYPE_ABSOLUTE, "绝对"),
        (BookmarkSearch.FILTER_DATE_TYPE_RELATIVE, "相对")
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
    date_filter_by = forms.ChoiceField(choices=FILTER_DATE_BY_CHOICES, widget=forms.RadioSelect)
    date_filter_type = forms.ChoiceField(choices=FILTER_DATE_TYPE_CHOICES, widget=forms.RadioSelect)
    date_filter_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_filter_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_filter_relative_string = forms.CharField(required=False)

    def __init__(
        self,
        search: BookmarkSearch,
        editable_fields: List[str] = None,
        users: List[User] = None,
    ):
        super().__init__()
        editable_fields = editable_fields or []
        self.editable_fields = editable_fields

        # set choices for user field if users are provided
        if users:
            user_choices = [(user.username, user.username) for user in users]
            user_choices.insert(0, ("", "所有人"))
            self.fields["user"].choices = user_choices

        for param in search.params:
            # set initial values for modified params
            if param in ["date_filter_start", "date_filter_end"]:
                value = getattr(search, param)
                # date对象转为字符串，供 DateField 使用
                value = value.isoformat() if hasattr(value, 'isoformat') else value
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
    THEME_AUTO = "auto"
    THEME_LIGHT = "light"
    THEME_DARK = "dark"
    THEME_CHOICES = [
        (THEME_AUTO, "自动"),
        (THEME_LIGHT, "亮色"),
        (THEME_DARK, "暗色"),
    ]
    BOOKMARK_DATE_DISPLAY_RELATIVE = "relative"
    BOOKMARK_DATE_DISPLAY_ABSOLUTE = "absolute"
    BOOKMARK_DATE_DISPLAY_HIDDEN = "hidden"
    BOOKMARK_DATE_DISPLAY_CHOICES = [
        (BOOKMARK_DATE_DISPLAY_RELATIVE, "相对"),
        (BOOKMARK_DATE_DISPLAY_ABSOLUTE, "绝对"),
        (BOOKMARK_DATE_DISPLAY_HIDDEN, "隐藏"),
    ]
    BOOKMARK_DESCRIPTION_DISPLAY_INLINE = "inline"
    BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE = "separate"
    BOOKMARK_DESCRIPTION_DISPLAY_CHOICES = [
        (BOOKMARK_DESCRIPTION_DISPLAY_INLINE, "行内"),
        (BOOKMARK_DESCRIPTION_DISPLAY_SEPARATE, "分行"),
    ]
    BOOKMARK_LINK_TARGET_BLANK = "_blank"
    BOOKMARK_LINK_TARGET_SELF = "_self"
    BOOKMARK_LINK_TARGET_CHOICES = [
        (BOOKMARK_LINK_TARGET_BLANK, "新页面"),
        (BOOKMARK_LINK_TARGET_SELF, "当前页面"),
    ]
    WEB_ARCHIVE_INTEGRATION_DISABLED = "disabled"
    WEB_ARCHIVE_INTEGRATION_ENABLED = "enabled"
    WEB_ARCHIVE_INTEGRATION_CHOICES = [
        (WEB_ARCHIVE_INTEGRATION_DISABLED, "禁用"),
        (WEB_ARCHIVE_INTEGRATION_ENABLED, "启用"),
    ]
    TAG_SEARCH_STRICT = "strict"
    TAG_SEARCH_LAX = "lax"
    TAG_SEARCH_CHOICES = [
        (TAG_SEARCH_STRICT, "严格 Strict"),
        (TAG_SEARCH_LAX, "宽松 Lax"),
    ]
    TAG_GROUPING_ALPHABETICAL = "alphabetical"
    TAG_GROUPING_DISABLED = "disabled"
    TAG_GROUPING_CHOICES = [
        (TAG_GROUPING_ALPHABETICAL, "首字母"),
        (TAG_GROUPING_DISABLED, "禁用"),
    ]
    user = models.OneToOneField(User, related_name="profile", on_delete=models.CASCADE)
    theme = models.CharField(
        max_length=10, choices=THEME_CHOICES, blank=False, default=THEME_AUTO
    )
    bookmark_date_display = models.CharField(
        max_length=10,
        choices=BOOKMARK_DATE_DISPLAY_CHOICES,
        blank=False,
        default=BOOKMARK_DATE_DISPLAY_RELATIVE,
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
    enable_sharing = models.BooleanField(default=False, null=False)
    enable_public_sharing = models.BooleanField(default=False, null=False)
    enable_favicons = models.BooleanField(default=False, null=False)
    enable_preview_images = models.BooleanField(default=False, null=False)
    display_url = models.BooleanField(default=False, null=False)
    display_view_bookmark_action = models.BooleanField(default=True, null=False)
    display_edit_bookmark_action = models.BooleanField(default=True, null=False)
    display_archive_bookmark_action = models.BooleanField(default=True, null=False)
    display_remove_bookmark_action = models.BooleanField(default=True, null=False)
    permanent_notes = models.BooleanField(default=False, null=False)
    custom_css = models.TextField(blank=True, null=False)
    custom_css_hash = models.CharField(blank=True, null=False, max_length=32)
    custom_domain_root = models.TextField(blank=True, null=False, default="")
    auto_tagging_rules = models.TextField(blank=True, null=False)
    search_preferences = models.JSONField(default=dict, null=False)
    trash_search_preferences = models.JSONField(default=dict, null=False)
    enable_automatic_html_snapshots = models.BooleanField(default=True, null=False)
    default_mark_unread = models.BooleanField(default=False, null=False)
    default_mark_shared = models.BooleanField(default=False, null=False)
    items_per_page = models.IntegerField(
        null=False, default=30, validators=[MinValueValidator(10)]
    )
    sticky_header_controls = models.BooleanField(default=False, null=False)
    sticky_pagination = models.BooleanField(default=False, null=False)
    sticky_side_panel = models.BooleanField(default=False, null=False)
    collapse_side_panel = models.BooleanField(default=False, null=False)
    hide_bundles = models.BooleanField(default=False, null=False)

    def save(self, *args, **kwargs):
        if self.custom_css:
            self.custom_css_hash = hashlib.md5(
                self.custom_css.encode("utf-8")
            ).hexdigest()
        else:
            self.custom_css_hash = ""
        super().save(*args, **kwargs)


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = [
            "theme",
            "bookmark_date_display",
            "bookmark_description_display",
            "bookmark_description_max_lines",
            "bookmark_link_target",
            "web_archive_integration",
            "tag_search",
            "tag_grouping",
            "enable_sharing",
            "enable_public_sharing",
            "enable_favicons",
            "enable_preview_images",
            "enable_automatic_html_snapshots",
            "display_url",
            "display_view_bookmark_action",
            "display_edit_bookmark_action",
            "display_archive_bookmark_action",
            "display_remove_bookmark_action",
            "permanent_notes",
            "default_mark_unread",
            "default_mark_shared",
            "custom_css",
            "custom_domain_root",
            "auto_tagging_rules",
            "items_per_page",
            "sticky_header_controls",
            "sticky_pagination",
            "sticky_side_panel",
            "collapse_side_panel",
            "hide_bundles",
        ]


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


class GlobalSettings(models.Model):
    LANDING_PAGE_LOGIN = "login"
    LANDING_PAGE_SHARED_BOOKMARKS = "shared_bookmarks"
    LANDING_PAGE_CHOICES = [
        (LANDING_PAGE_LOGIN, "登录页"),
        (LANDING_PAGE_SHARED_BOOKMARKS, "分享页"),
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
        return super(GlobalSettings, self).save(*args, **kwargs)


class GlobalSettingsForm(forms.ModelForm):
    class Meta:
        model = GlobalSettings
        fields = ["landing_page", "guest_profile_user", "enable_link_prefetch"]

    def __init__(self, *args, **kwargs):
        super(GlobalSettingsForm, self).__init__(*args, **kwargs)
        self.fields["guest_profile_user"].empty_label = "标准用户资料"
