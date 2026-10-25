import gzip
import logging
import os
import shutil

import requests
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone
from django.utils.translation import gettext as _

from bookmarks.models import Bookmark, BookmarkAsset
from bookmarks.services import snapshot_processor
from bookmarks.services.website_loader import (
    build_request_cookies,
    build_request_headers,
    detect_content_type,
    is_pdf_content_type,
    normalize_content_type,
)
from site_adapters.services.config.resolver import get_snapshot_config

MAX_ASSET_FILENAME_LENGTH = 192

logger = logging.getLogger(__name__)


class PdfTooLargeError(Exception):
    pass


def _save_bookmark_updates(bookmark: Bookmark, update_fields: list[str]):
    bookmark.save(update_fields=update_fields)


def _asset_row_exists(asset: BookmarkAsset) -> bool:
    """抓取期间资产可能被并发删除（如批量「删除所有快照」），此时应丢弃结果，
    避免 asset.save() 把已删除的记录重新插入。"""
    return asset.pk is not None and BookmarkAsset.objects.filter(pk=asset.pk).exists()


def _format_asset_timestamp(value) -> str:
    return timezone.localtime(value).strftime("%Y/%m/%d")


def create_snapshot_asset(bookmark: Bookmark) -> BookmarkAsset:
    asset = BookmarkAsset(
        bookmark=bookmark,
        asset_type=BookmarkAsset.TYPE_SNAPSHOT,
        date_created=timezone.now(),
        content_type="",
        display_name=_("New snapshot"),
        status=BookmarkAsset.STATUS_PENDING,
    )
    return asset


def create_snapshot(asset: BookmarkAsset, username: str = ''):
    try:
        url = asset.bookmark.url
        username = username or (asset.bookmark.owner.username if asset.bookmark.owner else '')
        config = get_snapshot_config(url, username=username)
        declared = normalize_content_type((config or {}).get("content_type"))

        if declared == "json":
            _create_data_snapshot(asset, username=username, content_type="json")
            return
        elif declared == "xml":
            _create_data_snapshot(asset, username=username, content_type="xml")
            return
        elif declared == "html":
            _create_html_snapshot(asset, username=username)
            return

        detected = detect_content_type(url, config)
        if is_pdf_content_type(detected):
            _create_pdf_snapshot(asset)
        else:
            format_type = normalize_content_type(detected)
            if format_type in ("json", "xml"):
                _create_data_snapshot(asset, username=username, content_type=format_type)
            else:
                _create_html_snapshot(asset, username=username)
    except Exception:
        asset.status = BookmarkAsset.STATUS_FAILURE
        asset.save()
        raise

def _create_html_snapshot(asset: BookmarkAsset, username: str = ''):
    # Create snapshot into temporary file
    temp_filename = _generate_asset_filename(asset, asset.bookmark.url, "tmp")
    temp_filepath = os.path.join(settings.LD_ASSET_FOLDER, temp_filename)
    try:
        snapshot_processor.create_snapshot(asset.bookmark.url, temp_filepath, username=username)

        # Store as gzip in asset folder
        filename = _generate_asset_filename(asset, asset.bookmark.url, "html.gz")
        filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)
        with open(temp_filepath, "rb") as temp_file, gzip.open(filepath, "wb") as gz_file:
            shutil.copyfileobj(temp_file, gz_file)

        # Remove temporary file
        os.remove(temp_filepath)

        timestamp = _format_asset_timestamp(asset.date_created)

        if not _asset_row_exists(asset):
            logger.info("Snapshot asset deleted during capture, discarding. id=%s", asset.pk)
            os.remove(filepath)
            return

        asset.status = BookmarkAsset.STATUS_COMPLETE
        asset.content_type = BookmarkAsset.CONTENT_TYPE_HTML
        asset.display_name = _("HTML snapshot from %(timestamp)s") % {
            "timestamp": timestamp
        }
        asset.file = filename
        asset.gzip = True
        asset.save()

        asset.bookmark.latest_snapshot = asset
        _save_bookmark_updates(asset.bookmark, ["latest_snapshot"])
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)


def _create_pdf_snapshot(asset: BookmarkAsset):
    url = asset.bookmark.url
    request_config = get_snapshot_config(url)
    max_size = settings.LD_SNAPSHOT_PDF_MAX_SIZE

    temp_filename = _generate_asset_filename(asset, url, "tmp")
    temp_filepath = os.path.join(settings.LD_ASSET_FOLDER, temp_filename)

    request_timeout = request_config.get("timeout", 60) if request_config else 60
    request_kwargs = {
        "cookies": build_request_cookies(request_config),
        "headers": build_request_headers(request_config),
        "stream": True,
        "timeout": request_timeout,
    }
    proxies = request_config.get("proxy") if request_config else None
    if proxies:
        request_kwargs["proxies"] = proxies

    try:
        with requests.get(url, **request_kwargs) as response:
            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_size:
                raise PdfTooLargeError(
                    f"PDF size ({content_length} bytes) exceeds limit ({max_size} bytes)"
                )

            downloaded_size = 0
            with open(temp_filepath, "wb") as temp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue

                    downloaded_size += len(chunk)
                    if downloaded_size > max_size:
                        raise PdfTooLargeError(
                            f"PDF size exceeds limit ({max_size} bytes)"
                        )
                    temp_file.write(chunk)

        filename = _generate_asset_filename(asset, url, "pdf.gz")
        filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)
        with (
            open(temp_filepath, "rb") as temp_file,
            gzip.open(filepath, "wb") as gz_file,
        ):
            shutil.copyfileobj(temp_file, gz_file)

        timestamp = _format_asset_timestamp(asset.date_created)

        if not _asset_row_exists(asset):
            logger.info("Snapshot asset deleted during capture, discarding. id=%s", asset.pk)
            os.remove(filepath)
            return

        asset.status = BookmarkAsset.STATUS_COMPLETE
        asset.content_type = BookmarkAsset.CONTENT_TYPE_PDF
        asset.display_name = _("PDF download from %(timestamp)s") % {
            "timestamp": timestamp
        }
        asset.file = filename
        asset.gzip = True
        asset.save()

        asset.bookmark.latest_snapshot = asset
        _save_bookmark_updates(asset.bookmark, ["latest_snapshot"])
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)


def _create_data_snapshot(
    asset: BookmarkAsset,
    username: str,
    content_type: str,
):
    url = asset.bookmark.url
    extension = "json" if content_type == "json" else "xml"
    mime_type = (
        BookmarkAsset.CONTENT_TYPE_JSON
        if content_type == "json"
        else BookmarkAsset.CONTENT_TYPE_XML
    )

    temp_filename = _generate_asset_filename(asset, url, "tmp")
    temp_filepath = os.path.join(settings.LD_ASSET_FOLDER, temp_filename)
    try:
        snapshot_processor.create_snapshot(
            url,
            temp_filepath,
            username=username,
            content_type=content_type,
        )

        filename = _generate_asset_filename(asset, url, f"{extension}.gz")
        filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)
        with open(temp_filepath, "rb") as temp_file, gzip.open(filepath, "wb") as gz_file:
            shutil.copyfileobj(temp_file, gz_file)
        os.remove(temp_filepath)

        timestamp = _format_asset_timestamp(asset.date_created)
        label = "JSON" if content_type == "json" else "XML"

        if not _asset_row_exists(asset):
            logger.info("Snapshot asset deleted during capture, discarding. id=%s", asset.pk)
            os.remove(filepath)
            return

        asset.status = BookmarkAsset.STATUS_COMPLETE
        asset.content_type = mime_type
        asset.display_name = _("%(label)s snapshot from %(timestamp)s") % {
            "label": label,
            "timestamp": timestamp,
        }
        asset.file = filename
        asset.gzip = True
        asset.save()

        asset.bookmark.latest_snapshot = asset
        _save_bookmark_updates(asset.bookmark, ["latest_snapshot"])
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)


def upload_snapshot(bookmark: Bookmark, html: bytes):
    asset = create_snapshot_asset(bookmark)
    filename = _generate_asset_filename(asset, asset.bookmark.url, "html.gz")
    filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)

    with gzip.open(filepath, "wb") as gz_file:
        gz_file.write(html)

    # Only save the asset if the file was written successfully
    timestamp = _format_asset_timestamp(asset.date_created)

    asset.status = BookmarkAsset.STATUS_COMPLETE
    asset.content_type = BookmarkAsset.CONTENT_TYPE_HTML
    asset.display_name = _("HTML snapshot from %(timestamp)s") % {
        "timestamp": timestamp
    }
    asset.file = filename
    asset.gzip = True
    asset.save()

    asset.bookmark.latest_snapshot = asset
    _save_bookmark_updates(asset.bookmark, ["latest_snapshot"])

    return asset


def upload_asset(bookmark: Bookmark, upload_file: UploadedFile):
    try:
        asset = BookmarkAsset(
            bookmark=bookmark,
            asset_type=BookmarkAsset.TYPE_UPLOAD,
            date_created=timezone.now(),
            content_type=upload_file.content_type,
            display_name=upload_file.name,
            status=BookmarkAsset.STATUS_COMPLETE,
            gzip=False,
        )
        name, extension = os.path.splitext(upload_file.name)

        # automatically gzip the file if it is not already gzipped
        if upload_file.content_type != "application/gzip":
            filename = _generate_asset_filename(
                asset, name, extension.lstrip(".") + ".gz"
            )
            filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)
            with gzip.open(filepath, "wb", compresslevel=9) as f:
                for chunk in upload_file.chunks():
                    f.write(chunk)
            asset.gzip = True
            asset.file = filename
            asset.file_size = os.path.getsize(filepath)
        else:
            filename = _generate_asset_filename(asset, name, extension.lstrip("."))
            filepath = os.path.join(settings.LD_ASSET_FOLDER, filename)
            with open(filepath, "wb") as f:
                for chunk in upload_file.chunks():
                    f.write(chunk)
            asset.file = filename
            asset.file_size = upload_file.size

        asset.save()

        logger.info(
            "Successfully uploaded asset file. bookmark=%s file=%s",
            bookmark, upload_file.name,
        )
        return asset
    except Exception as e:
        logger.error(
            "Failed to upload asset file. bookmark=%s file=%s",
            bookmark, upload_file.name,
            exc_info=e,
        )
        raise


def remove_asset(asset: BookmarkAsset):
    # If this asset is the latest_snapshot for a bookmark, try to find the next most recent snapshot
    bookmark = asset.bookmark
    update_fields = []
    if bookmark and bookmark.latest_snapshot == asset:
        latest = (
            BookmarkAsset.objects.filter(
                bookmark=bookmark,
                asset_type=BookmarkAsset.TYPE_SNAPSHOT,
                status=BookmarkAsset.STATUS_COMPLETE,
            )
            .exclude(pk=asset.pk)
            .order_by("-date_created")
            .first()
        )

        bookmark.latest_snapshot = latest
        update_fields.append("latest_snapshot")

    asset.delete()
    if bookmark and update_fields:
        _save_bookmark_updates(bookmark, update_fields)


def rename_asset(asset: BookmarkAsset, new_display_name: str):
    if new_display_name.strip() == "":
        return
    asset.display_name = new_display_name.strip()
    asset.save()

    logger.info(
        "Successfully renamed asset. asset_id=%s new_name=%s",
        asset.id, new_display_name,
    )


def _generate_asset_filename(
    asset: BookmarkAsset, filename: str, extension: str
) -> str:
    def sanitize_char(char):
        if char.isalnum() or char in ("-", "_", "."):
            return char
        else:
            return "_"

    formatted_datetime = timezone.localtime(asset.date_created).strftime("%Y-%m-%d_%H%M%S")
    sanitized_filename = "".join(sanitize_char(char) for char in filename)

    # Calculate the length of fixed parts of the final filename
    non_filename_length = len(f"{asset.asset_type}_{formatted_datetime}_.{extension}")
    # Calculate the maximum length for the dynamic part of the filename
    max_filename_length = MAX_ASSET_FILENAME_LENGTH - non_filename_length
    # Truncate the filename if necessary
    sanitized_filename = sanitized_filename[:max_filename_length]

    return f"{asset.asset_type}_{formatted_datetime}_{sanitized_filename}.{extension}"
