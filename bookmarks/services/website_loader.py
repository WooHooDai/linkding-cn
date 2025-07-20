import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urljoin

import importlib.util
import requests
from bs4 import BeautifulSoup
from bookmarks.utils import get_domain, load_module, search_config_for_domain, load_settings
from charset_normalizer import from_bytes
from django.conf import settings
from django.utils import timezone
from json.decoder import JSONDecodeError

logger = logging.getLogger(__name__)


@dataclass
class WebsiteMetadata:
    url: str
    title: str | None
    description: str | None
    preview_image: str | None

    def to_dict(self):
        return {
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "preview_image": self.preview_image,
        }




# 缓存规则设置与解析规则（function）
_settings_cache = None
_loaders_module_cache = {}  # {loader_path: (module, mtime)}

# 获取网站标题、描述、首图
# TODO: 目前一旦用户有自定义字段，就会失去缓存，暂时没考虑好传递config dict时的缓存方案
def load_website_metadata(url: str, ignore_cache: bool = False):
    settings_path = settings.LD_CUSTOM_WEBSITE_LOADER_SETTINGS
    domain = get_domain(url)
    config = None
    if os.path.exists(settings_path):
        domain_map = load_settings(settings_path,_settings_cache)
        if domain_map == "__JSON_ERROR__":
            return WebsiteMetadata(
                url=url,
                title="【错误】settings.json 文件有误",
                description="【错误】settings.json 解析失败，请检查文件格式",
                preview_image=None,
            )
        # 使用支持通配符的查找
        config = search_config_for_domain(domain, domain_map)
        if config:
            loader_file = config.get("loader")
            if loader_file:
                loader_path = os.path.join(os.path.dirname(settings_path), loader_file) if loader_file else None
                if loader_path and os.path.exists(loader_path):
                    module = load_module(loader_path, _loaders_module_cache)
                    func = getattr(module, "_load_website_metadata")
                    return func(url, config)
            elif config:
                return _load_website_metadata(url, config)
    if ignore_cache:
        return _load_website_metadata(url)
    return _load_website_metadata_cached(url)



# Caching metadata avoids scraping again when saving bookmarks, in case the
# metadata was already scraped to show preview values in the bookmark form
@lru_cache(maxsize=10)
def _load_website_metadata_cached(url: str):
    return _load_website_metadata(url)


def _load_website_metadata(url: str, config: dict = None):
    title = None
    description = None
    preview_image = None
    try:
        start = timezone.now()
        page_text = load_page(url, config)
        end = timezone.now()
        logger.debug(f"Load duration: {end - start}")

        start = timezone.now()
        soup = BeautifulSoup(page_text, "html.parser")

        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        else:
            title_tag = soup.find("meta", attrs={"property": "og:title"})
            title = (
                title_tag["content"].strip()
                if title_tag and title_tag["content"]
                else None
            )
        description_tag = soup.find("meta", attrs={"name": "description"})
        description = (
            description_tag["content"].strip()
            if description_tag and description_tag["content"]
            else None
        )

        if not description:
            description_tag = soup.find("meta", attrs={"property": "og:description"})
            description = (
                description_tag["content"].strip()
                if description_tag and description_tag["content"]
                else None
            )

        image_tag = (
            soup.find("meta", attrs={"property": "og:image"}) 
            or soup.find("meta", attrs={"name": "og:image"})
        )
        preview_image = image_tag["content"].strip() if image_tag else None
        if (
            preview_image
            and not preview_image.startswith("http://")
            and not preview_image.startswith("https://")
        ):
            preview_image = urljoin(url, preview_image)

        end = timezone.now()
        logger.debug(f"Parsing duration: {end - start}")
    finally:
        return WebsiteMetadata(
            url=url, title=title, description=description, preview_image=preview_image
        )


def load_page(url: str, config: dict = None):
    headers = build_request_headers(config)
    timeout = config.get("timeout", 10) if config else 10
    proxies = config.get("proxy") if config else None

    CHUNK_SIZE = config.get("chunk_size", 50*1024) if config else 50*1024
    MAX_CONTENT_LIMIT = config.get("max_content_limit", 5000*1024) if config else 5000*1024

    size = 0
    content = None
    iteration = 0
    # Use with to ensure request gets closed even if it's only read partially
    with requests.get(url, timeout=timeout, headers=headers, proxies=proxies, stream=True) as r:
        for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
            size += len(chunk)
            iteration = iteration + 1
            if content is None:
                content = chunk
            else:
                content = content + chunk

            logger.debug(f"Loaded chunk (iteration={iteration}, total={size / 1024})")

            # Stop reading if we have parsed end of head tag
            end_of_head = "</head>".encode("utf-8")
            if end_of_head in content:
                logger.debug(f"Found closing head tag after {size} bytes")
                content = content.split(end_of_head)[0] + end_of_head
                break
            # Stop reading if we exceed limit
            if size > MAX_CONTENT_LIMIT:
                logger.debug(f"Cancel reading document after {size} bytes")
                break
        if hasattr(r, "_content_consumed"):
            logger.debug(f"Request consumed: {r._content_consumed}")

    # Use charset_normalizer to determine encoding that best matches the response content
    # Several sites seem to specify the response encoding incorrectly, so we ignore it and use custom logic instead
    # This is different from Response.text which does respect the encoding specified in the response first,
    # before trying to determine one
    results = from_bytes(content or "")
    return str(results.best())


def build_request_headers(config: dict = None):
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml",
        "Accept-Encoding": "gzip, deflate",
        "Dnt": "1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": settings.LD_DEFAULT_USER_AGENT,
    }
    if config and config.get("headers"):
        headers.update(config["headers"])
    return headers
