import logging
import mimetypes
import os
import os.path
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings

max_file_age = 60 * 60 * 24  # 1 day

logger = logging.getLogger(__name__)

# register mime type for .ico files, which is not included in the default
# mimetypes of the Docker image
mimetypes.add_type("image/x-icon", ".ico")


@dataclass(frozen=True)
class CachedFavicon:
    filename: str
    is_stale: bool


def _ensure_favicon_folder():
    Path(settings.LD_FAVICON_FOLDER).mkdir(parents=True, exist_ok=True)


def _url_to_filename(url: str) -> str:
    return re.sub(r"\W+", "_", url)


def _get_url_parameters(url: str) -> dict:
    parsed_uri = urlparse(url)
    return {
        # https://example.com/foo?bar -> https://example.com
        "url": f"{parsed_uri.scheme}://{parsed_uri.hostname}",
        # https://example.com/foo?bar -> example.com
        "domain": parsed_uri.hostname,
    }


def _get_favicon_path(favicon_file: str) -> Path:
    return Path(os.path.join(settings.LD_FAVICON_FOLDER, favicon_file))


def _find_cached_favicon(favicon_name: str, include_stale: bool) -> CachedFavicon | None:
    favicon_folder = Path(settings.LD_FAVICON_FOLDER)
    if not favicon_folder.exists():
        return None

    for filename in os.listdir(settings.LD_FAVICON_FOLDER):
        file_base_name, _ = os.path.splitext(filename)
        if file_base_name != favicon_name:
            continue

        favicon_path = _get_favicon_path(filename)
        if not favicon_path.exists():
            continue

        is_stale = _is_stale(favicon_path)
        if is_stale and not include_stale:
            return None
        return CachedFavicon(filename=filename, is_stale=is_stale)
    return None


def get_cached_favicon(url: str, include_stale: bool = True) -> CachedFavicon | None:
    url_parameters = _get_url_parameters(url)
    favicon_name = _url_to_filename(url_parameters["url"])
    return _find_cached_favicon(favicon_name, include_stale)


def _remove_existing_favicon_variants(
    favicon_name: str, keep_filename: str | None = None
):
    favicon_folder = Path(settings.LD_FAVICON_FOLDER)
    if not favicon_folder.exists():
        return

    for filename in os.listdir(settings.LD_FAVICON_FOLDER):
        file_base_name, _ = os.path.splitext(filename)
        if file_base_name != favicon_name or filename == keep_filename:
            continue

        favicon_path = _get_favicon_path(filename)
        if favicon_path.exists():
            favicon_path.unlink()


def _is_stale(path: Path) -> bool:
    stat = path.stat()
    file_age = time.time() - stat.st_mtime
    return file_age >= max_file_age


def _load_or_refresh_favicon(
    url: str, timeout: int = 10, force_refresh: bool = False
) -> str:
    url_parameters = _get_url_parameters(url)

    # Create favicon folder if not exists
    _ensure_favicon_folder()
    # Use scheme+hostname as favicon filename to reuse icon for all pages on the same domain
    favicon_name = _url_to_filename(url_parameters["url"])

    if not force_refresh:
        cached_favicon = _find_cached_favicon(favicon_name, include_stale=False)
        if cached_favicon:
            return cached_favicon.filename

    favicon_url = settings.LD_FAVICON_PROVIDER.format(**url_parameters)
    logger.debug(f"Loading favicon from: {favicon_url}")
    with requests.get(favicon_url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        content_type = response.headers["Content-Type"]
        file_extension = mimetypes.guess_extension(content_type) or ".png"
        favicon_file = f"{favicon_name}{file_extension}"
        favicon_path = _get_favicon_path(favicon_file)
        with open(favicon_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

    if force_refresh:
        _remove_existing_favicon_variants(favicon_name, keep_filename=favicon_file)

    logger.debug(f"Saved favicon as: {favicon_path}")
    return favicon_file


def load_favicon(url: str, timeout: int = 10) -> str:
    try:
        return _load_or_refresh_favicon(url, timeout=timeout, force_refresh=False)
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to load favicon for {url}: {e}")
        return ""
    except Exception as e:
        logger.error(f"An unexpected error occurred during favicon load for {url}: {e}")
        return ""


def refresh_favicon(url: str, timeout: int = 10) -> str:
    try:
        return _load_or_refresh_favicon(url, timeout=timeout, force_refresh=True)
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to refresh favicon for {url}: {e}")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during favicon refresh for {url}: {e}")
        raise


def is_favicon_file_exists(url: str) -> bool:
    return get_cached_favicon(url, include_stale=True) is not None
