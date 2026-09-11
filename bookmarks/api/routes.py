import gzip
import logging
import time
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, OperationalError, models, transaction
from django.http import Http404, StreamingHttpResponse
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter, SimpleRouter
from rest_framework.views import APIView

from bookmarks import queries
from bookmarks.api.serializers import (
    AnnotationSerializer,
    BookmarkAssetSerializer,
    BookmarkBundleSerializer,
    BookmarkSerializer,
    ReadingProgressSerializer,
    TagSerializer,
    UserProfileSerializer,
)
from bookmarks.models import (
    Annotation,
    Bookmark,
    BookmarkAsset,
    BookmarkBundle,
    BookmarkSearch,
    ReadingProgress,
    Tag,
    User,
)
from bookmarks.services import (
    assets,
    auto_tagging,
    bookmarks,
    bundles,
    tasks,
    website_loader,
)
from bookmarks.type_defs import HttpRequest
from bookmarks.utils import normalize_url
from bookmarks.views import access


def _resolve_asset_file_path(asset: BookmarkAsset) -> Path:
    base_dir = Path(settings.LD_ASSET_FOLDER).resolve()
    candidate_path = Path(asset.file)

    # Prevent absolute-path reads and path traversal outside LD_ASSET_FOLDER.
    if candidate_path.is_absolute():
        raise Http404("Asset file does not exist")

    resolved_path = (base_dir / candidate_path).resolve()
    if not resolved_path.is_file() or base_dir not in resolved_path.parents:
        raise Http404("Asset file does not exist")

    return resolved_path

logger = logging.getLogger(__name__)


class BookmarkViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    request: HttpRequest
    serializer_class = BookmarkSerializer

    def get_permissions(self):
        # Allow unauthenticated access to shared bookmarks.
        # The shared action should still filter bookmarks so that
        # unauthenticated users only see bookmarks from users that have public
        # sharing explicitly enabled
        if self.action == "shared":
            return [AllowAny()]

        # Otherwise use default permissions which should require authentication
        return super().get_permissions()

    def get_queryset(self):
        # Provide filtered queryset for list actions
        user = self.request.user
        search = BookmarkSearch.from_request(self.request, self.request.GET)
        if self.action == "list":
            return queries.query_bookmarks(user, user.profile, search)
        elif self.action == "archived":
            return queries.query_archived_bookmarks(user, user.profile, search)
        elif self.action == "shared":
            user = User.objects.filter(username=search.user).first()
            public_only = not self.request.user.is_authenticated
            return queries.query_shared_bookmarks(
                user, self.request.user_profile, search, public_only
            )

        # For single entity actions return user owned bookmarks
        return Bookmark.objects.all().filter(owner=user)

    def get_serializer_context(self):
        disable_scraping = "disable_scraping" in self.request.GET
        disable_html_snapshot = "disable_html_snapshot" in self.request.GET
        prefer_async_metadata = self.request.GET.get(
            "prefer_async_metadata", False
        ) in ["true"]
        return {
            "request": self.request,
            "user": self.request.user,
            "disable_scraping": disable_scraping,
            "disable_html_snapshot": disable_html_snapshot,
            "prefer_async_metadata": prefer_async_metadata,
        }

    @action(methods=["get"], detail=False)
    def archived(self, request: HttpRequest):
        return self.list(request)

    @action(methods=["get"], detail=False)
    def shared(self, request: HttpRequest):
        return self.list(request)

    @action(methods=["post"], detail=True)
    def archive(self, request: HttpRequest, pk):
        bookmark = self.get_object()
        bookmarks.archive_bookmark(bookmark)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=["post"], detail=True)
    def unarchive(self, request: HttpRequest, pk):
        bookmark = self.get_object()
        bookmarks.unarchive_bookmark(bookmark)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=["post"], detail=True, url_path="refresh-preview-image")
    def refresh_preview_image(self, request: HttpRequest, pk):
        bookmark = self.get_object()
        tasks.load_preview_image(request.user, bookmark, force=True)
        bookmark.refresh_from_db()
        return Response(self.get_serializer(bookmark).data)

    @action(methods=["post"], detail=True)
    def trash(self, request, pk):
        bookmark = self.get_object()
        bookmarks.trash_bookmark(bookmark)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=["post"], detail=True)
    def restore(self, request, pk):
        bookmark = self.get_object()
        bookmarks.restore_bookmark(bookmark)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=["get"], detail=False)
    def check(self, request: HttpRequest):
        url = request.GET.get("url")
        ignore_cache = request.GET.get("ignore_cache", False) in ["true"]
        # 客户端 bookmarklet 会把浏览器捕获的元数据随请求发出，由服务器应用
        # rewrite_* 规则后返回（不重新抓取网页）。
        from_client = request.GET.get("from_client", False) == "1"
        client_title = request.GET.get("title")
        client_description = request.GET.get("description")

        bookmark = Bookmark.query_existing(request.user, url).first()

        # URL 可能会被自定义脚本改变
        # 当被改变时，进行二次检查
        normalized_url = normalize_url(url)
        if from_client:
            metadata = website_loader.rewrite_website_metadata(
                url,
                title=client_title,
                description=client_description,
                username=request.user.username,
            )
        else:
            try:
                metadata = website_loader.load_website_metadata(
                    url, ignore_cache=ignore_cache, username=request.user.username
                )
            except website_loader.RetryableMetadataError as exc:
                logger.warning(
                    f"Retryable metadata failure during bookmark check. url={url}",
                    exc_info=exc,
                )
                metadata = website_loader.WebsiteMetadata(
                    url=url,
                    title=None,
                    description=None,
                    preview_image=None,
                )
        if (
            not bookmark
            and metadata.url
            and normalize_url(metadata.url) != normalized_url
        ):
            normalized_metadata_url = normalize_url(metadata.url)
            bookmark = Bookmark.query_existing(
                request.user, normalized_metadata_url
            ).first()

        existing_bookmark_data = (
            self.get_serializer(bookmark).data if bookmark else None
        )

        # Return tags that would be automatically applied to the bookmark
        profile = request.user.profile
        auto_tags = []
        if profile.auto_tagging_rules:
            try:
                auto_tags = auto_tagging.get_tags(profile.auto_tagging_rules, url)
            except Exception as e:
                logger.error(
                    f"Failed to auto-tag bookmark. url={url}",
                    exc_info=e,
                )

        return Response(
            {
                "bookmark": existing_bookmark_data,
                "metadata": metadata.to_dict(),
                "auto_tags": auto_tags,
            },
            status=status.HTTP_200_OK,
        )

    @action(methods=["post"], detail=False)
    def singlefile(self, request: HttpRequest):
        if settings.LD_DISABLE_ASSET_UPLOAD:
            return Response(
                {"error": "Asset upload is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )
        url = request.POST.get("url")
        file = request.FILES.get("file")

        if not url or not file:
            return Response(
                {"error": "Both 'url' and 'file' parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        bookmark = Bookmark.query_existing(request.user, url).first()

        if not bookmark:
            bookmark = Bookmark(url=url)
            bookmark = bookmarks.create_bookmark(
                bookmark, "", request.user, disable_html_snapshot=True
            )
            try:
                bookmarks.enhance_with_website_metadata(bookmark)
            except website_loader.RetryableMetadataError:
                tasks.schedule_metadata_enrichment(bookmark)

        assets.upload_snapshot(bookmark, file.read())

        return Response(
            {"message": "Snapshot uploaded successfully."},
            status=status.HTTP_201_CREATED,
        )

    @action(methods=["get"], detail=True, url_path="notes_html")
    def notes_html(self, request: HttpRequest, pk):
        """返回书签笔记的渲染后 HTML"""
        bookmark = self.get_object()
        if not bookmark.notes:
            return Response({"html": ""})

        from bookmarks.templatetags.shared import render_markdown
        html = render_markdown({}, bookmark.notes)
        return Response({"html": str(html)})

class BookmarkAssetViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    request: HttpRequest
    serializer_class = BookmarkAssetSerializer

    def update(self, request, *args, **kwargs):
        allowed_fields = {"display_name"}
        unknown_fields = set(request.data.keys()) - allowed_fields
        if unknown_fields:
            return Response(
                {
                    "detail": "Only display_name can be updated.",
                    "invalid_fields": sorted(unknown_fields),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return super().update(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        # limit access to assets to the owner of the bookmark for now
        bookmark = access.bookmark_write(self.request, self.kwargs["bookmark_id"])
        return BookmarkAsset.objects.filter(
            bookmark_id=bookmark.id, bookmark__owner=user
        )

    def get_serializer_context(self):
        return {"user": self.request.user}

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request: HttpRequest, bookmark_id, pk):
        asset = self.get_object()
        try:
            file_path = _resolve_asset_file_path(asset)
            content_type = asset.content_type
            file_stream = (
                gzip.GzipFile(file_path, mode="rb")
                if asset.gzip
                else file_path.open("rb")
            )
            response = StreamingHttpResponse(file_stream, content_type=content_type)
            response["Content-Disposition"] = (
                f'attachment; filename="{asset.download_name}"'
            )
            return response
        except (FileNotFoundError, Http404):
            raise Http404("Asset file does not exist") from None
        except Exception as e:
            logger.error(
                f"Failed to download asset. bookmark_id={bookmark_id}, asset_id={pk}",
                exc_info=e,
            )
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(methods=["post"], detail=False)
    def upload(self, request: HttpRequest, bookmark_id):
        if settings.LD_DISABLE_ASSET_UPLOAD:
            return Response(
                {"error": "Asset upload is disabled."},
                status=status.HTTP_403_FORBIDDEN,
            )
        bookmark = access.bookmark_write(request, bookmark_id)

        upload_file = request.FILES.get("file")
        if not upload_file:
            return Response(
                {"error": "No file provided."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            asset = assets.upload_asset(bookmark, upload_file)
            serializer = self.get_serializer(asset)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(
                f"Failed to upload asset file. bookmark_id={bookmark_id}, file={upload_file.name}",
                exc_info=e,
            )
            return Response(
                {"error": "Failed to upload asset."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def perform_destroy(self, instance):
        assets.remove_asset(instance)


class TagViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
):
    request: HttpRequest
    serializer_class = TagSerializer

    def get_queryset(self):
        user = self.request.user
        return (
            Tag.objects.all()
            .filter(owner=user)
            .annotate(
                bookmark_count=models.Count(
                    "bookmark", filter=models.Q(bookmark__is_deleted=False)
                )
            )
            .order_by("-bookmark_count", "name")
        )

    def get_serializer_context(self):
        return {"user": self.request.user}


class UserViewSet(viewsets.GenericViewSet):
    @action(methods=["get"], detail=False)
    def profile(self, request: HttpRequest):
        return Response(UserProfileSerializer(request.user.profile).data)

    @action(methods=["patch"], detail=False, url_path="profile/reader-settings")
    def reader_settings(self, request: HttpRequest):
        profile = request.user.profile
        settings = request.data
        if not isinstance(settings, dict):
            return Response({"error": "Expected a JSON object"}, status=400)
        profile.reader_settings = {**profile.reader_settings, **settings}
        profile.save(update_fields=["reader_settings"])
        return Response(profile.reader_settings)


class BookmarkBundleViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    request: HttpRequest
    serializer_class = BookmarkBundleSerializer

    def get_queryset(self):
        user = self.request.user
        return BookmarkBundle.objects.filter(owner=user).order_by("order")

    def get_serializer_context(self):
        return {"user": self.request.user}

    def perform_destroy(self, instance):
        bundles.delete_bundle(instance)


# DRF routers do not support nested view sets such as /bookmarks/<id>/assets/<id>/
# Instead create separate routers for each view set and manually register them in urls.py
# The default router is only used to allow reversing a URL for the API root
default_router = DefaultRouter()

bookmark_router = SimpleRouter()
bookmark_router.register("", BookmarkViewSet, basename="bookmark")


class AnnotationViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    request: HttpRequest
    serializer_class = AnnotationSerializer

    def get_queryset(self):
        user = self.request.user
        bookmark_id = self.kwargs.get("bookmark_id")
        if bookmark_id:
            bookmark = access.bookmark_read(self.request, bookmark_id)
            return Annotation.objects.filter(
                bookmark=bookmark, bookmark__owner=user
            )
        return Annotation.objects.filter(bookmark__owner=user)

    def get_serializer_context(self):
        context = {"request": self.request, "user": self.request.user}
        if self.action == "create":
            bookmark_id = self.kwargs.get("bookmark_id")
            if bookmark_id:
                context["bookmark"] = access.bookmark_write(self.request, bookmark_id)
        return context

    def perform_create(self, serializer):
        bookmark = serializer.context.get("bookmark")
        if bookmark is None:
            bookmark_id = self.kwargs.get("bookmark_id")
            bookmark = access.bookmark_write(self.request, bookmark_id)
        serializer.save(bookmark=bookmark)

    def perform_destroy(self, instance):
        instance.delete()


bookmark_annotation_router = SimpleRouter()
bookmark_annotation_router.register(
    "", AnnotationViewSet, basename="bookmark_annotation"
)

annotation_router = SimpleRouter()
annotation_router.register("", AnnotationViewSet, basename="annotation")


class ReadingProgressView(APIView):
    """阅读进度 API：GET 返回当前进度（无记录时返回默认值），PATCH 保存进度（支持冲突检测）。"""

    request: HttpRequest

    def _get_bookmark(self, bookmark_id):
        return access.bookmark_read(self.request, bookmark_id)

    def _get_progress_for_update(self, bookmark):
        """获取或新建 ReadingProgress 实例，返回 (instance, created)。
        不使用 select_for_update()：SQLite 不支持行级锁，PostgreSQL 上 base_date_modified 已足够防冲突。"""
        progress = ReadingProgress.objects.filter(
            user=self.request.user,
            bookmark=bookmark,
        ).first()
        if progress is not None:
            return progress, False

        return (
            ReadingProgress(user=self.request.user, bookmark=bookmark),
            True,
        )

    def _is_stale_update(self, progress, created, base_date_modified):
        """乐观锁冲突检测。
        - 新建记录 → 不冲突
        - 客户端未提供 base_date_modified → last-write-wins，不冲突
        - 客户端提供了过期的 base_date_modified → 冲突（409）
        由 _save_reading_progress 中的 has_base_date_modified 守卫调用，
        确保未发送 base_date_modified 的请求不会触发 409。"""
        if created or not progress.date_modified:
            return False
        # 客户端未提供 base_date_modified → last-write-wins
        if not base_date_modified:
            return False

        if isinstance(base_date_modified, str):
            base_date_modified = parse_datetime(base_date_modified)
        if not base_date_modified:
            return False

        return progress.date_modified > base_date_modified

    def get(self, request: HttpRequest, bookmark_id: int):
        """获取阅读进度。无记录时返回全部默认值，避免客户端 404 处理。"""
        bookmark = self._get_bookmark(bookmark_id)
        progress = ReadingProgress.objects.filter(
            user=request.user,
            bookmark=bookmark,
        ).first()
        if progress is None:
            return Response(
                {
                    "id": None,
                    "bookmark": bookmark.id,
                    "article_asset": None,
                    "text_position_start": None,
                    "text_quote_exact": "",
                    "text_quote_prefix": "",
                    "text_quote_suffix": "",
                    "element_selector": None,
                    "progress": 0,
                    "scroll_top": 0,
                    "scroll_height": 0,
                    "client_width": 0,
                    "client_height": 0,
                    "date_created": None,
                    "date_modified": None,
                }
            )
        serializer = ReadingProgressSerializer(
            progress,
            context={"request": request, "user": request.user, "bookmark": bookmark},
        )
        return Response(serializer.data)

    def patch(self, request: HttpRequest, bookmark_id: int):
        """保存阅读进度。支持 base_date_modified 冲突检测（409 = 过期更新）。"""
        bookmark = self._get_bookmark(bookmark_id)
        # sendBeacon 发送 form-urlencoded，需 dict() 化
        if hasattr(request.data, "dict"):
            data = request.data.dict()
        else:
            data = request.data.copy()

        has_base_date_modified = "base_date_modified" in data
        base_date_modified = data.get("base_date_modified")
        if base_date_modified == "":
            base_date_modified = None
            data["base_date_modified"] = None

        max_retries = 3
        for attempt in range(max_retries):
            try:
                return self._save_reading_progress(bookmark, data, has_base_date_modified, base_date_modified)
            except (IntegrityError, OperationalError):
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt))  # 0.1s, 0.2s, 0.4s
                else:
                    raise

    def _save_reading_progress(self, bookmark, data, has_base_date_modified, base_date_modified):
        with transaction.atomic():
            progress, created = self._get_progress_for_update(bookmark)
            # 冲突检测：服务端记录比客户端基准更新 → 拒绝本次写入
            if has_base_date_modified and self._is_stale_update(
                progress, created, base_date_modified
            ):
                return Response(
                    {
                        "detail": _("Reading progress has been updated elsewhere."),
                        "date_modified": progress.date_modified,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            serializer = ReadingProgressSerializer(
                progress,
                data=data,
                partial=True,
                context={"request": self.request, "user": self.request.user, "bookmark": bookmark},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(user=self.request.user, bookmark=bookmark)
            return Response(serializer.data)

    def put(self, request: HttpRequest, bookmark_id: int):
        return self.patch(request, bookmark_id)

    def post(self, request: HttpRequest, bookmark_id: int):
        """sendBeacon 使用 POST，委托给 patch 处理。"""
        return self.patch(request, bookmark_id)

tag_router = SimpleRouter()
tag_router.register("", TagViewSet, basename="tag")

user_router = SimpleRouter()
user_router.register("", UserViewSet, basename="user")

bundle_router = SimpleRouter()
bundle_router.register("", BookmarkBundleViewSet, basename="bundle")

bookmark_asset_router = SimpleRouter()
bookmark_asset_router.register("", BookmarkAssetViewSet, basename="bookmark_asset")


@api_view(["POST"])
def render_markdown_api(request):
    """接受 Markdown 文本，返回渲染后的 HTML（需要登录）"""
    markdown_text = request.data.get("markdown", "")
    if not markdown_text:
        return Response({"html": ""})
    from bookmarks.templatetags.shared import render_markdown

    html = render_markdown({}, markdown_text)
    return Response({"html": str(html)})
