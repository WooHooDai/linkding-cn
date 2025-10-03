from django import forms
from django.forms.utils import ErrorList
from django.utils import timezone

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
from bookmarks.services.bookmarks import create_bookmark, update_bookmark
from bookmarks.type_defs import HttpRequest
from bookmarks.utils import normalize_url
from bookmarks.validators import BookmarkURLValidator


class CustomErrorList(ErrorList):
    template_name = "shared/error_list.html"


class BookmarkForm(forms.ModelForm):
    # Use URLField for URL
    url = forms.CharField(validators=[BookmarkURLValidator()])
    tag_string = forms.CharField(required=False)
    # Do not require title and description as they may be empty
    title = forms.CharField(max_length=512, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea())
    unread = forms.BooleanField(required=False)
    shared = forms.BooleanField(required=False)
    # Hidden field that determines whether to close window/tab after saving the bookmark
    auto_close = forms.CharField(required=False)
    favicon_file = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = Bookmark
        fields = [
            "url",
            "tag_string",
            "title",
            "description",
            "notes",
            "preview_image_remote_url",
            "unread",
            "shared",
            "auto_close",
            "favicon_file",
        ]

    def __init__(self, request: HttpRequest, instance: Bookmark = None):
        self.request = request

        initial = None
        if instance is None and request.method == "GET":
            initial = {
                "url": request.GET.get("url"),
                "title": request.GET.get("title"),
                "description": request.GET.get("description"),
                "notes": request.GET.get("notes"),
                "tag_string": request.GET.get("tags"),
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
        bookmark = super().save(commit=False)
        if self.instance.pk:
            return update_bookmark(bookmark, tag_string, self.request.user)
        else:
            return create_bookmark(bookmark, tag_string, self.request.user)

    def clean_url(self):
        # When creating a bookmark, the service logic prevents duplicate URLs by
        # updating the existing bookmark instead, which is also communicated in
        # the form's UI. When editing a bookmark, there is no assumption that
        # it would update a different bookmark if the URL is a duplicate, so
        # raise a validation error in that case.
        url = self.cleaned_data["url"]
        if self.instance.pk:
            normalized_url = normalize_url(url)
            is_duplicate = (
                Bookmark.objects.filter(
                    owner=self.instance.owner, url_normalized=normalized_url
                )
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if is_duplicate:
                raise forms.ValidationError("A bookmark with this URL already exists.")

        return url


def convert_tag_string(tag_string: str):
    # Tag strings coming from inputs are space-separated, however services.bookmarks functions expect comma-separated
    # strings
    return tag_string.replace(" ", ",")


class BookmarkBundleForm(forms.ModelForm):
    # 添加Search筛选项字段
    sort = forms.ChoiceField(choices=BookmarkSearchForm.SORT_CHOICES, label="排序", required=False)
    shared = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_SHARED_CHOICES, widget=forms.RadioSelect, label="分享筛选", required=False)
    unread = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_UNREAD_CHOICES, widget=forms.RadioSelect, label="未读筛选", required=False)
    tagged = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_TAGGED_CHOICES, widget=forms.RadioSelect, label="标签筛选", required=False)
    date_filter_by = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_DATE_BY_CHOICES, widget=forms.RadioSelect, label="日期筛选", required=False)
    date_filter_type = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_DATE_TYPE_CHOICES, widget=forms.RadioSelect, label="日期筛选方式", required=False)
    date_filter_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="开始日期")
    date_filter_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="结束日期")
    date_filter_relative_string = forms.CharField(required=False, label="相对日期字符串")
    
    class Meta:
        model = BookmarkBundle
        fields = [
            "name", "search", "any_tags", "all_tags", "excluded_tags", 
            "show_count", "is_folder", "sort", "shared", "unread", "tagged",
            "date_filter_by", "date_filter_type", "date_filter_start", 
            "date_filter_end", "date_filter_relative_string"
        ]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 优先使用非空search_params
        params = getattr(self.instance, 'search_params', None)
        if params:
            for field_name, value in params.items():
                if field_name in self.fields:
                    self.fields[field_name].initial = value
            return
        
        # 否则，使用默认值
        defaults = BookmarkSearch.defaults
        for field_name in ['sort', 'shared', 'unread', 'tagged', 'date_filter_by', 'date_filter_type']:
            if field_name in self.fields:
                self.fields[field_name].initial = defaults.get(field_name)
    
    def save(self, commit=True):
        instance = super().save(commit=False)

        search_params = {}
        search_field_names = [
            'sort', 'shared', 'unread', 'tagged', 'date_filter_by', 'date_filter_type',
            'date_filter_start', 'date_filter_end', 'date_filter_relative_string'
        ]
        
        for field_name in search_field_names:
            if field_name in self.cleaned_data:
                value = self.cleaned_data[field_name]
                if value is not None and value != '':
                    if field_name in ['date_filter_start', 'date_filter_end'] and value:
                        search_params[field_name] = value.isoformat()
                    else:
                        search_params[field_name] = value
                elif field_name in ['date_filter_by', 'date_filter_type']: # 日期筛选项若为空，使用默认值
                    search_params[field_name] = value or BookmarkSearch.defaults.get(field_name)
        
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
            raise forms.ValidationError(f'Tag "{name}" already exists.')

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
                "Please enter only one tag name for the target tag."
            )

        target_tag_name = target_tag_names[0]

        try:
            target_tag = Tag.objects.get(name__iexact=target_tag_name, owner=self.user)
        except Tag.DoesNotExist:
            raise forms.ValidationError(f'Tag "{target_tag_name}" does not exist.')

        return target_tag

    def clean_merge_tags(self):
        merge_tags_string = self.cleaned_data.get("merge_tags", "")

        merge_tag_names = parse_tag_string(merge_tags_string, " ")
        if not merge_tag_names:
            raise forms.ValidationError("Please enter at least one tag to merge.")

        merge_tags = []
        for tag_name in merge_tag_names:
            try:
                tag = Tag.objects.get(name__iexact=tag_name, owner=self.user)
                merge_tags.append(tag)
            except Tag.DoesNotExist:
                raise forms.ValidationError(f'Tag "{tag_name}" does not exist.')

        target_tag = self.cleaned_data.get("target_tag")
        if target_tag and target_tag in merge_tags:
            raise forms.ValidationError(
                "The target tag cannot be selected for merging."
            )

        return merge_tags
