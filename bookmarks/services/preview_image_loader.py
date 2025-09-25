import logging
import mimetypes
import os.path
import hashlib
import shutil
from pathlib import Path

import requests
from django.conf import settings
from bookmarks.models import Bookmark
from bookmarks.services import website_loader
from bookmarks.utils import get_clean_url

logger = logging.getLogger(__name__)


def _ensure_preview_folder():
    Path(settings.LD_PREVIEW_FOLDER).mkdir(parents=True, exist_ok=True)

def _ensure_temp_preview_folder():
    _ensure_preview_folder()
    Path(settings.LD_PREVIEW_FOLDER).joinpath("tmp").mkdir(parents=True, exist_ok=True)

def _get_permanent_image_path(file_name: str) -> Path:
    _ensure_preview_folder()
    return Path(settings.LD_PREVIEW_FOLDER) / file_name

def _get_temporary_image_path(file_name: str) -> Path:
    _ensure_temp_preview_folder()
    return Path(settings.LD_PREVIEW_FOLDER) / "tmp" / file_name


def _url_to_filename(url: str) -> str:
    url = get_clean_url(url)
    return hashlib.md5(url.encode()).hexdigest()


def _download_and_save_image(image_url: str, referer_url: str = None) -> str | None:
    headers = { "Referer": referer_url if referer_url else image_url }
    try:
        with requests.get(image_url, headers=headers, stream=True) as response:
            if response.status_code < 200 or response.status_code >= 300:
                logger.debug(
                    f"Bad response status code for preview image: {image_url} status_code={response.status_code}"
                )
                return None

            if "Content-Length" not in response.headers:
                logger.debug(f"Empty Content-Length for preview image: {image_url}")
                return None

            content_length = int(response.headers["Content-Length"])
            if content_length > settings.LD_PREVIEW_MAX_SIZE:
                logger.debug(
                    f"Content-Length exceeds LD_PREVIEW_MAX_SIZE: {image_url} length={content_length}"
                )
                return None

            if "Content-Type" not in response.headers:
                logger.debug(f"Empty Content-Type for preview image: {image_url}")
                return None

            content_type = response.headers["Content-Type"].split(";", 1)[0]
            file_extension = mimetypes.guess_extension(content_type)
            logger.debug(f"File extension for preview image: {file_extension}")

            if file_extension not in settings.LD_PREVIEW_ALLOWED_EXTENSIONS:
                logger.debug(
                    f"Unsupported Content-Type for preview image: {image_url} content_type={content_type}"
                )
                return None

            image_file_name = f"{_url_to_filename(image_url)}{file_extension}"
            image_file_path = _get_temporary_image_path(image_file_name)

            logger.debug(f"Downloading image: {image_url}")

            with open(image_file_path, "wb") as file:
                downloaded = 0
                for chunk in response.iter_content(chunk_size=8192):
                    downloaded += len(chunk)
                    if downloaded > content_length:
                        logger.debug(
                            f"Content-Length mismatch for image: {image_url} length={content_length} downloaded={downloaded}"
                        )
                        file.close()
                        image_file_path.unlink()
                        return None
                    file.write(chunk)

            logger.debug(f"Downloaded preview image to temporary path: {image_file_path}")
            return image_file_name
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download preview image: {image_url}", exc_info=e)
        return None


def load_temporary_preview_image(image_url: str) -> str | None:
    _ensure_temp_preview_folder()

    image_file_name_without_ext = _url_to_filename(image_url)
    
    # 检查预览图临时文件夹是否已有下载完成的预览图
    existing_file_path = None
    for ext in settings.LD_PREVIEW_ALLOWED_EXTENSIONS:
        potential_path = _get_temporary_image_path(image_file_name_without_ext + ext)
        if potential_path.exists():
            existing_file_path = potential_path
            break

    if existing_file_path:
        logger.debug(f"Reusing existing temporary preview image: {existing_file_path}")
        return existing_file_path

    # 没有则重新下载
    image_file_name = _download_and_save_image(image_url)
    
    if image_file_name:
        image_file_path = _get_temporary_image_path(image_file_name)
        logger.debug(f"Saved new temporary preview image as: {image_file_path}")
        return image_file_name
    return None


def load_preview_image(url: str, bookmark: Bookmark) -> str | None:
    _ensure_preview_folder()
    _ensure_temp_preview_folder()

    image_url = (
        bookmark.preview_image_remote_url
        if bookmark and bookmark.preview_image_remote_url
        else None
    )

    # 如无预览图链接，尝试获取
    if not image_url:
        logger.debug("No remote preview image URL, trying to load website metadata.")
        metadata = website_loader.load_website_metadata(url)
        if not metadata.preview_image:
            logger.debug(f"Could not find preview image in metadata: {url}")
            return None
        image_url = metadata.preview_image


    image_file_name_without_ext = _url_to_filename(image_url)

    temporary_file_path = None
    for ext in settings.LD_PREVIEW_ALLOWED_EXTENSIONS:
        potential_path = _get_temporary_image_path(image_file_name_without_ext + ext)
        if potential_path.exists():
            temporary_file_path = potential_path # 优先使用缓存
            break
    if not temporary_file_path:
        temporary_file_name = _download_and_save_image(image_url, referer_url=url) # 没有缓存再下载
        if temporary_file_name: 
            temporary_file_path = _get_temporary_image_path(temporary_file_name)
    
    if temporary_file_path:
        permanent_file_name = Path(temporary_file_path).name
        permanent_file_path = _get_permanent_image_path(permanent_file_name)
        shutil.move(temporary_file_path, permanent_file_path)
        logger.info(f"Saved new permanent preview image as: {permanent_file_path}")
        return permanent_file_name

    return None