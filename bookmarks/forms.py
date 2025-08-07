from django import forms
from django.forms.utils import ErrorList

from bookmarks.models import Bookmark, build_tag_string, BookmarkBundle, BookmarkSearch, BookmarkSearchForm
from bookmarks.validators import BookmarkURLValidator
from bookmarks.type_defs import HttpRequest
from bookmarks.services.bookmarks import create_bookmark, update_bookmark


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
            is_duplicate = (
                Bookmark.objects.filter(owner=self.instance.owner, url=url)
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
    date_filter_by = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_DATE_BY_CHOICES, widget=forms.RadioSelect, label="日期筛选", required=False)
    date_filter_type = forms.ChoiceField(choices=BookmarkSearchForm.FILTER_DATE_TYPE_CHOICES, widget=forms.RadioSelect, label="日期筛选方式", required=False)
    date_filter_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="开始日期")
    date_filter_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}), label="结束日期")
    date_filter_relative_string = forms.CharField(required=False, label="相对日期字符串")
    
    class Meta:
        model = BookmarkBundle
        fields = [
            "name", "search", "any_tags", "all_tags", "excluded_tags", 
            "show_count", "is_folder", "sort", "shared", "unread", 
            "date_filter_by", "date_filter_type", "date_filter_start", 
            "date_filter_end", "date_filter_relative_string"
        ]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 为新创建的Bundle设置默认值
        if not self.instance or not self.instance.pk:
            defaults = BookmarkSearch.defaults
            self.fields['sort'].initial = defaults.get('sort')
            self.fields['shared'].initial = defaults.get('shared')
            self.fields['unread'].initial = defaults.get('unread')
            self.fields['date_filter_by'].initial = defaults.get('date_filter_by')
            self.fields['date_filter_type'].initial = defaults.get('date_filter_type')
        elif self.instance.search_params:
            # 为已存在的Bundle设置保存的值
            for field_name, value in self.instance.search_params.items():
                if field_name in self.fields:
                    self.fields[field_name].initial = value
    
    def save(self, commit=True):
        instance = super().save(commit=False)

        search_params = {}
        search_field_names = [
            'sort', 'shared', 'unread', 'date_filter_by', 'date_filter_type',
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
