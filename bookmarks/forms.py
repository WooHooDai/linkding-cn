from django import forms
from django.forms.utils import ErrorList
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from bookmarks.models import (
    Bookmark,
    BookmarkBundle,
    BookmarkSearch,
    BookmarkSearchForm,
    Tag,
    build_tag_string,
    parse_tag_string,
    sanitize_tag_name,
)
from bookmarks.services import auto_tagging
from bookmarks.services.bookmarks import create_bookmark, update_bookmark
from bookmarks.type_defs import HttpRequest
from bookmarks.utils import extract_url
from bookmarks.validators import BookmarkURLValidator


class CustomErrorList(ErrorList):
    template_name = "shared/error_list.html"


def _merge_tag_string(tag_string: str, tag_names: list[str]) -> str:
    """按输入框的空格分隔语法拼接，与已有标签同名（忽略大小写）的不重复追加。"""
    names = (tag_string or "").split()
    seen = {name.lower() for name in names}
    for tag_name in tag_names:
        if tag_name.lower() not in seen:
            names.append(tag_name)
            seen.add(tag_name.lower())
    return " ".join(names)


class BookmarkForm(forms.ModelForm):
    # Use URLField for URL
    url = forms.CharField(validators=[BookmarkURLValidator()])
    tag_string = forms.CharField(required=False)
    # Do not require title and description as they may be empty
    title = forms.CharField(required=False)
    description = forms.CharField(required=False, widget=forms.Textarea())
    author = forms.CharField(required=False)
    unread = forms.BooleanField(required=False)
    shared = forms.BooleanField(required=False)
    # Hidden field that determines whether to close window/tab after saving the bookmark
    auto_close = forms.CharField(required=False, widget=forms.HiddenInput())
    # 表单里被取消勾选的规则标签，服务端据此跳过——API 传不了它，所以始终按规则补
    excluded_auto_tags = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = Bookmark
        fields = [
            "url",
            "tag_string",
            "title",
            "description",
            "author",
            "notes",
            "preview_image_remote_url",
            "unread",
            "shared",
            "auto_close",
        ]

    def __init__(self, request: HttpRequest, instance: Bookmark = None):
        self.request = request

        # 命中自动标签规则的标签随首屏一起填进标签框：等 check 接口抓完网页元数据
        # 再补，用户可能已经离开标签框，甚至已经提交
        url = request.GET.get("url")
        if request.method == "POST":
            url = request.POST.get("url")
        if not url and instance is not None:
            url = instance.url
        self.auto_tags = auto_tagging.get_tags_for_url(
            request.user_profile.auto_tagging_rules, url
        )
        # 已经收藏过的 URL 只展示规则标签、不预填：保存时会走更新分支，标签应当以
        # 书签原有标签为准，凭空多出来的规则标签会盖掉用户当初的选择
        self.auto_tags_applied = []
        if instance is None:
            already_bookmarked = bool(url) and Bookmark.query_existing(
                request.user, url
            ).exists()
            if not already_bookmarked:
                self.auto_tags_applied = self.auto_tags

        initial = None
        if instance is None and request.method == "GET":
            initial = {
                "url": request.GET.get("url"),
                "title": request.GET.get("title"),
                "description": request.GET.get("description"),
                "author": request.GET.get("author"),
                "notes": request.GET.get("notes"),
                "tag_string": _merge_tag_string(
                    request.GET.get("tags"), self.auto_tags_applied
                ),
                "auto_close": "auto_close" in request.GET,
                "unread": request.user_profile.default_mark_unread,
                "shared": request.user_profile.default_mark_shared,
            }
        if instance is not None and request.method == "GET":
            initial = {"tag_string": build_tag_string(instance.tag_names, " ")}
        data = request.POST if request.method == "POST" else None
        super().__init__(
            data, instance=instance, initial=initial, error_class=CustomErrorList
        )

        # 首屏就把按钮的选中态渲染对，免得先按下去再被脚本改回来
        tag_string = self["tag_string"].value() or ""
        present = {name.lower() for name in tag_string.split()}
        self.auto_tag_chips = [
            (tag, tag.lower() in present) for tag in self.auto_tags
        ]

    @property
    def is_auto_close(self):
        return self.data.get("auto_close", False) == "True" or self.initial.get(
            "auto_close", False
        )

    @property
    def has_notes(self):
        return self.initial.get("notes", None) or (
            self.instance and self.instance.notes
        )

    def save(self, commit=False):
        tag_string = convert_tag_string(self.data["tag_string"])
        excluded_auto_tags = self.data.get("excluded_auto_tags", "").split()
        bookmark = super().save(commit=False)
        if self.instance.pk:
            return update_bookmark(
                bookmark,
                tag_string,
                self.request.user,
                excluded_auto_tags=excluded_auto_tags,
            )
        else:
            return create_bookmark(
                bookmark,
                tag_string,
                self.request.user,
                schedule_metadata_enrichment=True,
                excluded_auto_tags=excluded_auto_tags,
            )

    def clean_url(self):
        # When creating a bookmark, the service logic prevents duplicate URLs by
        # updating the existing bookmark instead, which is also communicated in
        # the form's UI. When editing a bookmark, there is no assumption that
        # it would update a different bookmark if the URL is a duplicate, so
        # raise a validation error in that case.
        url = extract_url(self.cleaned_data["url"])
        self.cleaned_data["url"] = url
        if self.instance.pk:
            is_duplicate = (
                Bookmark.query_existing(self.instance.owner, url)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if is_duplicate:
                raise forms.ValidationError(
                    _("A bookmark with this URL already exists.")
                )

        return url


def convert_tag_string(tag_string: str):
    # Tag strings coming from inputs are space-separated, however services.bookmarks functions expect comma-separated
    # strings
    return tag_string.replace(" ", ",")


class BookmarkBundleForm(forms.ModelForm):
    sort = forms.ChoiceField(
        choices=BookmarkSearchForm.SORT_CHOICES, label=_("Sort"), required=False
    )
    shared = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_SHARED_CHOICES,
        widget=forms.RadioSelect,
        label=_("Shared filter"),
        required=False,
    )
    unread = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_UNREAD_CHOICES,
        widget=forms.RadioSelect,
        label=_("Unread filter"),
        required=False,
    )
    tagged = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_TAGGED_CHOICES,
        widget=forms.RadioSelect,
        label=_("Tagged filter"),
        required=False,
    )
    date_filter_by = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_DATE_BY_CHOICES,
        widget=forms.RadioSelect,
        label=_("Date filter"),
        required=False,
    )
    date_filter_type = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_DATE_TYPE_CHOICES,
        widget=forms.RadioSelect,
        label=_("Date filter type"),
        required=False,
    )
    date_filter_start = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("Start date"),
    )
    date_filter_end = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label=_("End date"),
    )
    date_filter_relative_string = forms.CharField(
        required=False, label=_("Relative date string")
    )
    html_snapshot = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_ASSET_CHOICES,
        widget=forms.RadioSelect,
        label=_("Snapshot"),
        required=False,
    )
    preview_image = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_ASSET_CHOICES,
        widget=forms.RadioSelect,
        label=_("Preview image"),
        required=False,
    )
    favicon = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_ASSET_CHOICES,
        widget=forms.RadioSelect,
        label=_("Favicon"),
        required=False,
    )
    highlight = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_HIGHLIGHT_CHOICES,
        widget=forms.RadioSelect,
        label=_("Highlight filter"),
        required=False,
    )
    annotation = forms.ChoiceField(
        choices=BookmarkSearchForm.FILTER_ANNOTATION_CHOICES,
        widget=forms.RadioSelect,
        label=_("Annotation filter"),
        required=False,
    )

    class Meta:
        model = BookmarkBundle
        fields = [
            "name",
            "search",
            "any_tags",
            "all_tags",
            "excluded_tags",
            "show_count",
            "is_folder",
            "sort",
            "shared",
            "unread",
            "tagged",
            "date_filter_by",
            "date_filter_type",
            "date_filter_start",
            "date_filter_end",
            "date_filter_relative_string",
            "html_snapshot",
            "preview_image",
            "favicon",
            "highlight",
            "annotation",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 优先使用非空search_params
        params = getattr(self.instance, "search_params", None)
        if params:
            for field_name, value in params.items():
                if field_name in self.fields:
                    self.fields[field_name].initial = value
            return

        # 否则，使用默认值
        defaults = BookmarkSearch.defaults
        for field_name in [
            "sort",
            "shared",
            "unread",
            "tagged",
            "date_filter_by",
            "date_filter_type",
            "html_snapshot",
            "preview_image",
            "favicon",
            "highlight",
            "annotation",
        ]:
            if field_name in self.fields:
                self.fields[field_name].initial = defaults.get(field_name)

    def save(self, commit=True):
        instance = super().save(commit=False)

        search_params = {}
        search_field_names = [
            "sort",
            "shared",
            "unread",
            "tagged",
            "date_filter_by",
            "date_filter_type",
            "date_filter_start",
            "date_filter_end",
            "date_filter_relative_string",
            "html_snapshot",
            "preview_image",
            "favicon",
            "highlight",
            "annotation",
        ]

        for field_name in search_field_names:
            if field_name in self.cleaned_data:
                value = self.cleaned_data[field_name]
                if value is not None and value != "":
                    if field_name in ["date_filter_start", "date_filter_end"] and value:
                        search_params[field_name] = value.isoformat()
                    else:
                        search_params[field_name] = value
                elif field_name in [
                    "date_filter_by",
                    "date_filter_type",
                ]:  # 日期筛选项若为空，使用默认值
                    search_params[field_name] = value or BookmarkSearch.defaults.get(
                        field_name
                    )

        instance.search_params = search_params

        if commit:
            instance.save()
        return instance


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name"]

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs, error_class=CustomErrorList)
        self.user = user

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()

        name = sanitize_tag_name(name)

        queryset = Tag.objects.filter(name__iexact=name, owner=self.user)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise forms.ValidationError(
                _('Tag "%(tag_name)s" already exists.') % {"tag_name": name}
            )

        return name

    def save(self, commit=True):
        tag = super().save(commit=False)
        if not self.instance.pk:
            tag.owner = self.user
            tag.date_added = timezone.now()
        else:
            tag.date_modified = timezone.now()
        if commit:
            tag.save()
        return tag


class TagMergeForm(forms.Form):
    target_tag = forms.CharField()
    merge_tags = forms.CharField()

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs, error_class=CustomErrorList)
        self.user = user

    def clean_target_tag(self):
        target_tag_name = self.cleaned_data.get("target_tag", "")

        target_tag_names = parse_tag_string(target_tag_name, " ")
        if len(target_tag_names) != 1:
            raise forms.ValidationError(
                _("Please enter only one tag name for the target tag.")
            )

        target_tag_name = target_tag_names[0]

        try:
            target_tag = Tag.objects.get(name__iexact=target_tag_name, owner=self.user)
        except Tag.DoesNotExist:
            raise forms.ValidationError(
                _('Tag "%(tag_name)s" does not exist.') % {"tag_name": target_tag_name}
            ) from None

        return target_tag

    def clean_merge_tags(self):
        merge_tags_string = self.cleaned_data.get("merge_tags", "")

        merge_tag_names = parse_tag_string(merge_tags_string, " ")
        if not merge_tag_names:
            raise forms.ValidationError(_("Please enter at least one tag to merge."))

        merge_tags = []
        for tag_name in merge_tag_names:
            try:
                tag = Tag.objects.get(name__iexact=tag_name, owner=self.user)
                merge_tags.append(tag)
            except Tag.DoesNotExist:
                raise forms.ValidationError(
                    _('Tag "%(tag_name)s" does not exist.') % {"tag_name": tag_name}
                ) from None

        target_tag = self.cleaned_data.get("target_tag")
        if target_tag and target_tag in merge_tags:
            raise forms.ValidationError(
                _("The target tag cannot be selected for merging.")
            )

        return merge_tags
