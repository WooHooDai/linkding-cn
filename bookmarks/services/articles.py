import gzip
import logging
import os

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

from bookmarks.models import Bookmark, BookmarkAsset
from bookmarks.services.assets import (
    _format_asset_timestamp,
    _generate_asset_filename,
    _save_bookmark_updates,
)

logger = logging.getLogger(__name__)


def create_article_asset_pending(bookmark: Bookmark) -> BookmarkAsset:
    """Create a pending article asset (content will be filled by huey task)."""
    asset = BookmarkAsset(
        bookmark=bookmark,
        asset_type=BookmarkAsset.TYPE_ARTICLE,
        date_created=timezone.now(),
        content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        display_name=_("Processing article..."),
        status=BookmarkAsset.STATUS_PENDING,
    )
    asset.save()

    # Set latest_article so the read() view can detect pending tasks
    bookmark.latest_article = asset
    _save_bookmark_updates(bookmark, ["latest_article"])

    return asset


def save_article_content(asset: BookmarkAsset, html_content: str, title: str = ""):
    """Save defuddle-parsed HTML content to an existing pending asset."""
    filename = _generate_asset_filename(asset, asset.bookmark.url, "html.gz")
    filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)

    with gzip.open(filepath, "wb") as gz_file:
        gz_file.write(html_content.encode("utf-8"))

    timestamp = _format_asset_timestamp(asset.date_created)

    asset.status = BookmarkAsset.STATUS_COMPLETE
    asset.content_type = BookmarkAsset.CONTENT_TYPE_HTML
    asset.display_name = _("HTML article from %(timestamp)s") % {
        "timestamp": timestamp
    }
    asset.file = filename
    asset.gzip = True
    asset.save()

    # Update bookmark's latest_article
    bookmark = asset.bookmark
    bookmark.latest_article = asset
    _save_bookmark_updates(bookmark, ["latest_article"])


def create_article(bookmark: Bookmark, html_content: str, title: str = "") -> BookmarkAsset:
    """Save defuddle-parsed HTML as an article-type BookmarkAsset (synchronous).
    Updates bookmark.latest_article."""
    asset = BookmarkAsset(
        bookmark=bookmark,
        asset_type=BookmarkAsset.TYPE_ARTICLE,
        date_created=timezone.now(),
        content_type=BookmarkAsset.CONTENT_TYPE_HTML,
        display_name=title or _("Article"),
        status=BookmarkAsset.STATUS_PENDING,
    )
    asset.save()

    save_article_content(asset, html_content, title=title)
    return asset


def remove_article(asset: BookmarkAsset):
    """Delete article asset file. Updates bookmark.latest_article to next most recent."""
    bookmark = asset.bookmark
    update_fields = []

    if bookmark and bookmark.latest_article == asset:
        latest = (
            BookmarkAsset.objects.filter(
                bookmark=bookmark,
                asset_type=BookmarkAsset.TYPE_ARTICLE,
                status=BookmarkAsset.STATUS_COMPLETE,
            )
            .exclude(pk=asset.pk)
            .order_by("-date_created")
            .first()
        )
        bookmark.latest_article = latest
        update_fields.append("latest_article")

    asset.delete()
    if bookmark and update_fields:
        _save_bookmark_updates(bookmark, update_fields)


def get_article_content(asset: BookmarkAsset) -> str:
    """Read and decompress article HTML file content."""
    filepath = os.path.join(settings.LD_ASSET_FOLDER, asset.file)
    if asset.gzip:
        with gzip.open(filepath, "rb") as f:
            return f.read().decode("utf-8")
    else:
        with open(filepath, encoding="utf-8") as f:
            return f.read()
