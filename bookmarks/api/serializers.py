import json
from functools import lru_cache

from django.db.models import prefetch_related_objects
from django.templatetags.static import static
from django.utils.translation import gettext_lazy as _
from pypinyin import Style, pinyin
from rest_framework import serializers
from rest_framework.serializers import ListSerializer

from bookmarks.models import (
    Annotation,
    Bookmark,
    BookmarkAsset,
    BookmarkBundle,
    ReadingProgress,
    Tag,
    UserProfile,
    build_tag_string,
)
from bookmarks.services import bookmarks, bundles, tasks, website_loader
from bookmarks.services.tags import get_or_create_tag
from bookmarks.services.wayback import generate_fallback_webarchive_url
from bookmarks.utils import app_version, extract_url
from bookmarks.validators import BookmarkURLValidator


class TagListField(serializers.ListField):
    child = serializers.CharField()


class BookmarkListSerializer(ListSerializer):
    def to_representation(self, data):
        # Prefetch nested relations to avoid n+1 queries
        prefetch_related_objects(data, "tags")

        return super().to_representation(data)


class EmtpyField(serializers.ReadOnlyField):
    def to_representation(self, value):
        return None


class BookmarkBundleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookmarkBundle
        fields = [
            "id",
            "name",
            "search",
            "any_tags",
            "all_tags",
            "excluded_tags",
            "order",
            "date_created",
            "date_modified",
            "search_params",
        ]
        read_only_fields = [
            "id",
            "date_created",
            "date_modified",
        ]

    def create(self, validated_data):
        bundle = BookmarkBundle(**validated_data)
        bundle.order = validated_data.get("order", None)
        return bundles.create_bundle(bundle, self.context["user"])


class BookmarkSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bookmark
        fields = [
            "id",
            "url",
            "title",
            "description",
            "notes",
            "preview_image_remote_url",
            "web_archive_snapshot_url",
            "favicon_url",
            "preview_image_url",
            "is_deleted",
            "is_archived",
            "unread",
            "shared",
            "tag_names",
            "date_added",
            "date_modified",
            "date_deleted",
            "website_title",
            "website_description",
        ]
        read_only_fields = [
            "web_archive_snapshot_url",
            "favicon_url",
            "preview_image_url",
            "is_deleted",
            "tag_names",
            "date_deleted",
            "website_title",
            "website_description",
        ]
        list_serializer_class = BookmarkListSerializer

    # Override model field to remove BookmarkURLValidator — validation runs after extract_url in validate_url()
    url = serializers.CharField(max_length=2048)
    # Custom tag_names field to allow passing a list of tag names to create/update
    tag_names = TagListField(required=False)
    # Custom fields to generate URLs for favicon, preview image, and web archive snapshot
    favicon_url = serializers.SerializerMethodField()
    preview_image_remote_url = serializers.URLField(required=False, allow_null=True)
    preview_image_url = serializers.SerializerMethodField()
    web_archive_snapshot_url = serializers.SerializerMethodField()
    # Add dummy website title and description fields for backwards compatibility but keep them empty
    website_title = EmtpyField()
    website_description = EmtpyField()
    date_added = serializers.DateTimeField(required=False)
    date_modified = serializers.DateTimeField(required=False)

    def get_favicon_url(self, obj: Bookmark):
        from bookmarks.utils import extract_hostname
        from django.urls import reverse
        hostname = extract_hostname(obj.url)
        if not hostname:
            return None
        # 使用预加载的 FaviconLookup 避免 N+1 查询
        favicon_lookup = self.context.get("_favicon_lookup")
        if favicon_lookup is None:
            from bookmarks.views.contexts import FaviconLookup
            from bookmarks.utils import parse_domain_roots
            request = self.context.get("request")
            domain_config = parse_domain_roots(
                request.user.profile.custom_domain_root if request and request.user.is_authenticated else ""
            )
            favicon_lookup = FaviconLookup(domain_config)
            self.context["_favicon_lookup"] = favicon_lookup
        domain = favicon_lookup.get_domain(hostname)
        request = self.context.get("request")
        favicon_path = reverse("linkding:favicon_image", kwargs={"domain": domain})
        return request.build_absolute_uri(favicon_path)

    def get_preview_image_url(self, obj: Bookmark):
        if not obj.preview_image_file:
            return None
        request = self.context.get("request")
        preview_image_file_path = static(obj.preview_image_file)
        preview_image_url = request.build_absolute_uri(preview_image_file_path)
        return preview_image_url

    def get_web_archive_snapshot_url(self, obj: Bookmark):
        if obj.web_archive_snapshot_url:
            return obj.web_archive_snapshot_url

        return generate_fallback_webarchive_url(obj.url, obj.date_added)

    def create(self, validated_data):
        tag_names = validated_data.pop("tag_names", [])
        tag_string = build_tag_string(tag_names)
        # Apply user's default_mark_unread if unread not explicitly set
        if "unread" not in self.initial_data:
            user = self.context["user"]
            if hasattr(user, "profile"):
                validated_data.setdefault("unread", user.profile.default_mark_unread)
        bookmark = Bookmark(**validated_data)

        disable_scraping = self.context.get("disable_scraping", False)
        disable_html_snapshot = self.context.get("disable_html_snapshot", False)
        prefer_async_metadata = self.context.get("prefer_async_metadata", False)

        saved_bookmark = bookmarks.create_bookmark(
            bookmark,
            tag_string,
            self.context["user"],
            disable_html_snapshot=disable_html_snapshot,
            schedule_metadata_enrichment=prefer_async_metadata and not disable_scraping,
        )
        # Unless scraping is explicitly disabled, enhance bookmark with website
        # metadata to preserve backwards compatibility with clients that expect
        # title and description to be populated automatically when left empty
        if not disable_scraping and not prefer_async_metadata:
            try:
                bookmarks.enhance_with_website_metadata(saved_bookmark)
            except website_loader.RetryableMetadataError:
                tasks.schedule_metadata_enrichment(saved_bookmark)
        return saved_bookmark

    def update(self, instance: Bookmark, validated_data):
        tag_names = validated_data.pop("tag_names", instance.tag_names)
        tag_string = build_tag_string(tag_names)

        for field_name, field in self.fields.items():
            if not field.read_only and field_name in validated_data:
                setattr(instance, field_name, validated_data[field_name])

        return bookmarks.update_bookmark(instance, tag_string, self.context["user"])

    def validate_url(self, value):
        value = extract_url(value)
        BookmarkURLValidator()(value)
        return value

    def validate(self, attrs):
        # When creating a bookmark, the service logic prevents duplicate URLs by
        # updating the existing bookmark instead. When editing a bookmark,
        # there is no assumption that it would update a different bookmark if
        # the URL is a duplicate, so raise a validation error in that case.
        if self.instance and "url" in attrs:
            is_duplicate = (
                Bookmark.objects.filter(owner=self.instance.owner, url=attrs["url"])
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if is_duplicate:
                raise serializers.ValidationError(
                    {"url": "A bookmark with this URL already exists."}
                )

        return attrs


class BookmarkAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookmarkAsset
        fields = [
            "id",
            "bookmark",
            "date_created",
            "file",
            "file_size",
            "asset_type",
            "content_type",
            "display_name",
            "status",
        ]
        read_only_fields = [
            "id",
            "bookmark",
            "date_created",
            "file",
            "file_size",
            "asset_type",
            "content_type",
            "status",
        ]


@lru_cache(maxsize=None)
def _pinyin_full(name):
    """全拼，无声调，小写。如 '最爱' → 'zuiai'"""
    return "".join(p[0] for p in pinyin(name, style=Style.NORMAL)).lower()


@lru_cache(maxsize=None)
def _pinyin_first(name):
    """首字母，小写。如 '最爱' → 'za'"""
    return "".join(p[0] for p in pinyin(name, style=Style.FIRST_LETTER)).lower()


class TagSerializer(serializers.ModelSerializer):
    bookmark_count = serializers.IntegerField(read_only=True, default=0)
    pinyin_full = serializers.SerializerMethodField()
    pinyin_first = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = [
            "id",
            "name",
            "date_added",
            "bookmark_count",
            "pinyin_full",
            "pinyin_first",
        ]
        read_only_fields = ["date_added", "bookmark_count"]

    def get_pinyin_full(self, obj):
        return _pinyin_full(obj.name)

    def get_pinyin_first(self, obj):
        return _pinyin_first(obj.name)

    def create(self, validated_data):
        return get_or_create_tag(validated_data["name"], self.context["user"])


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "theme",
            "bookmark_date_display",
            "bookmark_link_target",
            "web_archive_integration",
            "tag_search",
            "enable_sharing",
            "enable_public_sharing",
            "enable_favicons",
            "display_url",
            "permanent_notes",
            "search_preferences",
            "reader_settings",
            "highlight_copy_format",
            "highlight_copy_default_action",
            "version",
        ]

    version = serializers.ReadOnlyField(default=app_version)


class AnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Annotation
        fields = [
            "id",
            "bookmark",
            "article_asset",
            "selector",
            "selected_text",
            "color",
            "note_content",
            "date_created",
            "date_modified",
        ]
        read_only_fields = ["id", "bookmark", "date_created", "date_modified"]

    def validate(self, attrs):
        bookmark = attrs.get("bookmark")
        if bookmark is None:
            bookmark = self.context.get("bookmark")
        if bookmark is None and self.instance is not None:
            bookmark = self.instance.bookmark

        article_asset = attrs.get("article_asset")
        if article_asset is None and self.instance is not None:
            article_asset = self.instance.article_asset

        if not bookmark:
            return attrs

        if article_asset is None:
            if self.instance is None:
                raise serializers.ValidationError(
                    {
                        "article_asset": _("Article asset is required."),
                    }
                )
            return attrs

        if article_asset.bookmark_id != bookmark.id:
            raise serializers.ValidationError(
                {
                    "article_asset": _(
                        "Article asset must belong to the same bookmark as the annotation."
                    )
                }
            )

        if article_asset.asset_type != BookmarkAsset.TYPE_ARTICLE:
            raise serializers.ValidationError(
                {
                    "article_asset": _("Article asset must have type 'article'."),
                }
            )

        return attrs


class ReadingProgressSerializer(serializers.ModelSerializer):
    # 冲突检测：客户端提交上次保存的 date_modified，服务端校验是否过期
    base_date_modified = serializers.DateTimeField(
        required=False,
        allow_null=True,
        write_only=True,
    )

    class Meta:
        model = ReadingProgress
        fields = [
            "id",
            "bookmark",
            "article_asset",
            "text_position_start",
            "text_quote_exact",
            "text_quote_prefix",
            "text_quote_suffix",
            "element_selector",
            "progress",
            "scroll_top",
            "scroll_height",
            "client_width",
            "client_height",
            "date_created",
            "date_modified",
            "base_date_modified",
        ]
        read_only_fields = ["id", "bookmark", "date_created", "date_modified"]

    def to_internal_value(self, data):
        # sendBeacon posts form data where empty nullable fields arrive as
        # empty strings instead of None / omitted.
        if "text_position_start" in data and data["text_position_start"] == "":
            data = {**data, "text_position_start": None}
        if "element_selector" in data:
            val = data["element_selector"]
            if val == "":
                data = {**data, "element_selector": None}
            elif isinstance(val, str):
                try:
                    data = {**data, "element_selector": json.loads(val)}
                except (ValueError, TypeError):
                    pass
        return super().to_internal_value(data)

    def validate(self, attrs):
        bookmark = self.context.get("bookmark")
        article_asset = attrs.get("article_asset")
        if article_asset is None and self.instance is not None:
            article_asset = self.instance.article_asset

        if bookmark and article_asset and article_asset.bookmark_id != bookmark.id:
            raise serializers.ValidationError(
                {
                    "article_asset": _(
                        "Article asset must belong to the same bookmark."
                    )
                }
            )

        if article_asset and article_asset.asset_type != BookmarkAsset.TYPE_ARTICLE:
            raise serializers.ValidationError(
                {"article_asset": _("Article asset must have type 'article'.")}
            )

        progress = attrs.get("progress")
        if progress is not None:
            attrs["progress"] = min(1, max(0, progress))

        return attrs

    def create(self, validated_data):
        validated_data.pop("base_date_modified", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("base_date_modified", None)
        return super().update(instance, validated_data)
