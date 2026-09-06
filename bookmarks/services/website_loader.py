import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes
from django.conf import settings
from django.utils import timezone
from elementpath import Selector as XPathSelector
from jsonpath_rfc9535 import find as jsonpath_find
from lxml import etree

from bookmarks.utils import get_registrable_domain
from site_adapters.services.auth.cookies import (
    cookie_string_to_playwright_list,
    verify_and_refresh,
)
from site_adapters.services.auth.cookies import _should_refresh_cookie
from site_adapters.services.auth.credentials import get_shared_cookie
from site_adapters.services.config import (
    apply_request_url,
    apply_rewrite,
    apply_rewrite_url,
)
from site_adapters.services.config.resolver import get_metadata_config
from site_adapters.services.engine.script_runner import run_script, resolve_hook_timeout
from site_adapters.services.execution_log import log_execution

logger = logging.getLogger(__name__)

# Per-domain rate limiter for metadata requests
_domain_last_request: dict[str, float] = {}

_JSON_LD_SKIP_TYPES = frozenset({"WebSite", "Organization", "BreadcrumbList"})

_domain_rate_lock = threading.Lock()
_DOMAIN_RATE_MAX_SIZE = 1000  # Prevent unbounded growth

_CONTENT_TYPE_ALIASES = {
    "html": "html",
    "application/xhtml+xml": "html",
    "json": "json",
    "application/json": "json",
    "xml": "xml",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/rss+xml": "xml",
    "application/atom+xml": "xml",
}

_DEFAULT_NAMESPACE_ALIASES = {
    "http://www.w3.org/2005/Atom": "atom",
    "http://purl.org/rss/1.0/": "rss",
}


class ContentTypeResolutionError(ValueError):
    """Raised when neither config nor response headers identify the format."""


def _wait_for_domain(domain: str):
    """Check per-domain rate limit and record the request atomically."""
    cooldown = settings.LD_METADATA_DOMAIN_COOLDOWN_SEC
    if cooldown <= 0:
        return
    wait = 0.0
    with _domain_rate_lock:
        # Prevent unbounded growth
        if len(_domain_last_request) >= _DOMAIN_RATE_MAX_SIZE:
            _domain_last_request.clear()
        now = time.monotonic()
        last = _domain_last_request.get(domain, 0)
        wait = cooldown - (now - last)
        if wait > 0:
            _domain_last_request[domain] = last + cooldown
        else:
            _domain_last_request[domain] = now
    if wait > 0:
        logger.debug('Rate limit: sleeping %.1fs for %s', wait, domain)
        time.sleep(wait)
    if wait > 0:
        logger.debug('Rate limit: sleeping %.1fs for %s', wait, domain)
        time.sleep(wait)



def _record_domain_request(domain: str):
    _domain_last_request[domain] = time.monotonic()

class RetryableMetadataError(Exception):
    def __init__(self, message="", status_code=None):
        super().__init__(message)
        self.status_code = status_code


class NonRetryableMetadataError(Exception):
    def __init__(self, message="", status_code=None):
        super().__init__(message)
        self.status_code = status_code


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



def _empty_metadata(url: str):
    return WebsiteMetadata(url=url, title=None, description=None, preview_image=None)


def _apply_metadata_before_result(url: str, config: dict, result) -> None:
    """Apply the partial config returned by a metadata before hook."""
    if not isinstance(result, dict):
        return
    for key, value in result.items():
        if key.startswith('_'):
            continue
        if key == 'request_url':
            config['request_url'] = value
            if isinstance(value, str) and value.startswith(('http://', 'https://')):
                config['_request_url'] = value
            else:
                resolved = apply_request_url(url, value)
                if resolved:
                    config['_request_url'] = resolved
        elif key == 'rewrite_url':
            config['rewrite_url'] = value
            resolved = apply_rewrite_url(url, value)
            if resolved:
                config['_rewrite_url'] = resolved
        elif key == 'user_cookie':
            config['_user_cookie'] = value
            config['user_cookie'] = value
        elif key == 'headers' and isinstance(value, dict):
            config.setdefault('headers', {}).update(value)
        else:
            config[key] = value


def _normalize_metadata_result(url: str, metadata, source: str):
    if isinstance(metadata, WebsiteMetadata):
        return metadata
    if isinstance(metadata, dict):
        return WebsiteMetadata(
            url=metadata.get('url') or url,
            title=metadata.get('title'),
            description=metadata.get('description'),
            preview_image=metadata.get('image') or metadata.get('preview_image'),
        )

    if metadata is None:
        logger.warning("Metadata loader returned no result. url=%s source=%s", url, source)
    else:
        logger.warning(
            "Metadata loader returned invalid result. url=%s source=%s type=%s",
            url, source, type(metadata).__name__,
        )

    return _empty_metadata(url)


def _metadata_dict_is_empty(result: dict) -> bool:
    """Return True when a replace-hook result carries no extractable fields."""
    return not any(
        result.get(key)
        for key in ("title", "description", "image", "preview_image")
    )


def _load_with_hooks(url: str, config: dict, scripts: list, username: str = '',
                     ignore_cache: bool = False) -> WebsiteMetadata:
    """Execute metadata pipeline with hook scripts.

    Order: before hooks → [replace or built-in engine] → after hooks
    """
    # Replace hooks issue their own HTTP requests, so acquire an auto cookie
    # before executing them when no credential is available yet.
    if config.get("cookie", {}).get("type") == "auto" and not _cookie_string_from_config(config):
        try:
            verify_and_refresh(
                cookie_config=config.get("cookie"),
                url=config.get("_request_url", url),
                domain_key=config.get("_domain_key"),
                verify_context={"url": url, "status": 0, "title": "", "body_preview": ""},
                username=username,
                scope=config.get("_effective_cookie_scope", ""),
            )
            # The refreshed credential is persisted by verify_and_refresh; a
            # rebuilt config makes it visible to the hook below.
            refreshed_config = get_metadata_config(url, username=username)
            if refreshed_config:
                config = refreshed_config
        except Exception:
            logger.exception(
                "Metadata preflight cookie refresh failed. url=%s", url
            )

    # 1. Run before hooks
    for entry in scripts:
        if entry.get('hook') != 'before':
            continue
        script_path = entry.get('path', '')
        if not script_path or not os.path.exists(script_path):
            continue
        logger.debug("Running metadata before hook: %s", script_path)
        hook_timeout = resolve_hook_timeout(entry, config)
        result = run_script(script_path, hook_name='before', url=url,
                            config=dict(config), timeout=hook_timeout)
        _apply_metadata_before_result(url, config, result)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("After before hook, config keys: %s", list(config.keys()))

    # 2. Run replace hook or built-in engine
    has_replace = any(e.get('hook') == 'replace' for e in scripts)

    if has_replace:
        for entry in scripts:
            if entry.get('hook') != 'replace':
                continue
            script_path = entry.get('path', '')
            if not script_path or not os.path.exists(script_path):
                continue
            logger.debug("Running metadata replace hook: %s", script_path)
            hook_timeout = resolve_hook_timeout(entry, config)
            result = run_script(script_path, hook_name='replace', url=url,
                                config=dict(config), timeout=hook_timeout)
            # Replace hooks own their HTTP requests, so an empty result with an
            # auto cookie can mean the saved token was rejected. Refresh once
            # and retry before giving up.
            if isinstance(result, dict) and _metadata_dict_is_empty(result):
                cookie_config = config.get("cookie") or {}
                if (
                    cookie_config.get("type") == "auto"
                    and cookie_config.get("refresh")
                    and _should_refresh_cookie(cookie_config)
                ):
                    logger.info(
                        "Replace hook returned empty metadata; forcing cookie refresh. url=%s",
                        url,
                    )
                    new_cookie = verify_and_refresh(
                        cookie_config=cookie_config,
                        url=config.get("_request_url", url),
                        domain_key=config.get("_domain_key"),
                        verify_context={"url": url, "status": 0, "title": "", "body_preview": ""},
                        username=username,
                        scope=config.get("_effective_cookie_scope", ""),
                        force_refresh=True,
                    )
                    if new_cookie:
                        config["_user_cookie"] = new_cookie
                        result = run_script(
                            script_path,
                            hook_name='replace',
                            url=url,
                            config=dict(config),
                            timeout=hook_timeout,
                        )
            if result is None:
                return _empty_metadata(url)
            if isinstance(result, dict):
                result['title'] = apply_rewrite(result.get('title'), config.get('rewrite_title'))
                result['description'] = apply_rewrite(result.get('description'), config.get('rewrite_description'))
                result['image'] = apply_rewrite(result.get('image'), config.get('rewrite_image'))
                metadata = _normalize_metadata_result(url, result, source=script_path)
            else:
                return _empty_metadata(url)
            break  # Only one replace allowed
    else:
        # Built-in engine: load page + parse
        if ignore_cache:
            metadata = _load_website_metadata(url, config, username=username)
        else:
            metadata = _load_website_metadata_config_cached(
                url, _metadata_config_cache_key(config), username=username
            )

    # 3. Run after hooks
    if metadata is None:
        metadata = _empty_metadata(url)

    result_dict = {
        'title': metadata.title,
        'description': metadata.description,
        'image': metadata.preview_image,
        'url': metadata.url,
    }

    for entry in scripts:
        if entry.get('hook') != 'after':
            continue
        script_path = entry.get('path', '')
        if not script_path or not os.path.exists(script_path):
            continue
        logger.debug("Running metadata after hook: %s", script_path)
        hook_timeout = resolve_hook_timeout(entry, config)
        after_result = run_script(
            script_path,
            hook_name='after',
            url=url,
            config=dict(config),
            result_dict=result_dict,
            timeout=hook_timeout,
        )
        if isinstance(after_result, dict):
            result_dict.update(after_result)

    return WebsiteMetadata(
        url=result_dict.get('url') or url,
        title=result_dict.get('title'),
        description=result_dict.get('description'),
        preview_image=result_dict.get('image'),
    )


def _metadata_config_cache_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


def load_website_metadata(url: str, ignore_cache: bool = False, username: str = ''):
    config = get_metadata_config(url, username=username)

    if config:
        scripts = config.get("scripts")
        if scripts:
            return _load_with_hooks(url, config, scripts, username=username, ignore_cache=ignore_cache)

        loader_file = config.get("script")
        if loader_file:
            loader_path = loader_file  # site_adapters engine resolved to absolute path
            if loader_path and os.path.exists(loader_path):
                load_full = config.get("load_full_page", True) if config else True
                body = load_page(url, config, load_full_page=load_full)
                if loader_path.endswith(".js"):
                    result = run_script(loader_path, url=url, config=config, html_content=body)
                    if result and isinstance(result, dict):
                        return WebsiteMetadata(
                            url=result.get('url') or url,
                            title=apply_rewrite(result.get('title'), config.get('rewrite_title')),
                            description=apply_rewrite(result.get('description'), config.get('rewrite_description')),
                            preview_image=apply_rewrite(
                                result.get('preview_image') or result.get('image'),
                                config.get('rewrite_image'),
                            ),
                        )
                    return _empty_metadata(url)
                result = run_script(loader_path, url=url, config=config, html_content=body)
                if result:
                    if isinstance(result, dict):
                        result['title'] = apply_rewrite(result.get('title'), config.get('rewrite_title'))
                        result['description'] = apply_rewrite(result.get('description'), config.get('rewrite_description'))
                        result['image'] = apply_rewrite(result.get('image'), config.get('rewrite_image'))
                    return _normalize_metadata_result(url, result, source=loader_path)
                return _empty_metadata(url)
        else:
            if ignore_cache:
                return _load_website_metadata(url, config, username=username)
            return _load_website_metadata_config_cached(
                url, _metadata_config_cache_key(config), username=username
            )

    if ignore_cache:
        result = _load_website_metadata(url, username=username)
    else:
        result = _load_website_metadata_cached(url)

    return result


def rewrite_website_metadata(
    url: str,
    title: str | None,
    description: str | None,
    username: str = '',
) -> WebsiteMetadata:
    """对客户端（浏览器）提供的元数据应用站点配置的 rewrite_* 规则。

    仅应用 rewrite_title / rewrite_description / rewrite_image 与 rewrite_url，
    不重新抓取网页。用于客户端 bookmarklet 流程：浏览器把捕获的元数据交给
    服务器，由服务器上的站点规则重写后返回。

    注意：预览图不参与客户端链路，始终由服务器重新抓取网页后按
    select_image / rewrite_image 规则处理。
    """
    config = get_metadata_config(url, username=username) or {}
    return WebsiteMetadata(
        url=config.get("_rewrite_url") or url,
        title=apply_rewrite(title, config.get("rewrite_title")),
        description=apply_rewrite(description, config.get("rewrite_description")),
        preview_image=None,
    )


def _config_cache_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)


# Caching metadata avoids scraping again when saving bookmarks
@lru_cache(maxsize=10)
def _load_website_metadata_cached(url: str, username: str = ''):
    return _load_website_metadata(url, username=username)


@lru_cache(maxsize=10)
def _load_website_metadata_config_cached(url: str, config_key: str, username: str = ''):
    return _load_website_metadata(url, json.loads(config_key), username=username)


_METADATA_MAX_RETRIES = 3
_METADATA_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt


def _load_website_metadata(url: str, config: dict = None, username: str = '', include_sources: bool = False):
    fetch_url = config.get("_request_url", url) if config else url
    load_full = config.get("load_full_page", True) if config else True
    page_text = None
    last_exc = None

    # Pre-flight: for auto-type cookie sites without cookies, acquire via browser first.
    try:
        cookie_config = config.get("cookie") if config else {}
        if cookie_config and cookie_config.get("type") == "auto":
            cookie_str = _cookie_string_from_config(config)
            if not cookie_str and cookie_config.get("refresh"):
                domain_key = config.get("_domain_key")
                logger.info("No cookie for auto site %s, acquiring via browser refresh", domain_key)
                new_cookie = verify_and_refresh(
                    cookie_config=cookie_config, url=fetch_url, domain_key=domain_key,
                    verify_context={"url": fetch_url, "status": 0, "title": "", "body_preview": ""},
                    username=username,
                    scope=config.get('_effective_cookie_scope', ''),
                )
                if new_cookie:
                    config["_user_cookie"] = new_cookie
                    logger.info("Pre-flight acquired cookie for %s, proceeding with request", domain_key)
                else:
                    logger.warning("Pre-flight failed to acquire cookie for %s", domain_key)
    except Exception as e:
        logger.error("Pre-flight cookie acquisition error for %s: %s", url, e)

    for attempt in range(_METADATA_MAX_RETRIES + 1):
        try:
            start = timezone.now()
            page_text = load_page(fetch_url, config, load_full_page=load_full)
            end = timezone.now()
            logger.debug("Load duration: %s", end - start)
            last_exc = None
            break
        except RetryableMetadataError as exc:
            last_exc = exc
            if attempt < _METADATA_MAX_RETRIES:
                delay = _METADATA_RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(
                    "Retryable error (attempt %d/%d), retrying in %.1fs. url=%s",
                    attempt + 1, _METADATA_MAX_RETRIES, delay, url,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "All %d retries exhausted. url=%s",
                    _METADATA_MAX_RETRIES, url,
                )
        except NonRetryableMetadataError as exc:
            logger.info("Metadata request failed without retry. url=%s", exc_info=exc)

            # If the HTTP status indicates an auth failure (e.g. 401/403),
            # attempt cookie refresh before giving up.  The L1
            # http_head_probe would normally detect this inside
            # verify_and_refresh, but load_page raises before that runs.
            cookie_config = config.get("cookie") if config else {}
            invalid_status = (
                cookie_config.get("verify", {})
                .get("http_head_probe", {})
                .get("invalid_status", [401, 403])
            ) if cookie_config else [401, 403]

            if (
                cookie_config
                and _should_refresh_cookie(cookie_config)
                and exc.status_code in invalid_status
                and attempt < _METADATA_MAX_RETRIES
            ):
                domain_key = config.get("_domain_key")
                logger.info(
                    "Non-retryable status %s for %s, attempting cookie refresh",
                    exc.status_code, domain_key,
                )
                new_cookie = verify_and_refresh(
                    cookie_config=cookie_config, url=fetch_url, domain_key=domain_key,
                    verify_context={
                        "url": fetch_url,
                        "status": exc.status_code or 0,
                        "title": "",
                        "body_preview": "",
                    },
                    username=username,
                    scope=config.get("_effective_cookie_scope", ""),
                )
                if new_cookie:
                    config["_user_cookie"] = new_cookie
                    logger.info(
                        "Cookie refreshed for %s after %s, retrying request",
                        domain_key, exc.status_code,
                    )
                    continue
                else:
                    logger.warning(
                        "Cookie refresh failed for %s after %s",
                        domain_key, exc.status_code,
                    )

            if include_sources:
                return _empty_metadata(url), {"error": str(exc)}
            return _empty_metadata(url)
        except Exception as exc:
            logger.error("Unexpected metadata request failure. url=%s", exc_info=exc)
            if include_sources:
                return _empty_metadata(url), {"error": str(exc)}
            return _empty_metadata(url)

    if last_exc is not None:
        logger.warning("All %d retries exhausted, returning empty metadata. url=%s", _METADATA_MAX_RETRIES, url)
        if include_sources:
            return _empty_metadata(url), {"error": str(last_exc)}
        return _empty_metadata(url)

    try:
        start = timezone.now()
        title, description, preview_image, sources = _parse_metadata_from_content(
            page_text, fetch_url, config, include_sources=True
        )

        cookie_config = config.get("cookie") if config else {}
        if cookie_config:
            domain_key = config.get("_domain_key")
            verify_context = {
                "url": fetch_url,
                "status": 200,
                "title": title or "",
                "body_preview": (page_text or "")[:2000],
            }
            before = _cookie_string_from_config(config)
            after = verify_and_refresh(cookie_config=cookie_config, url=fetch_url,
                                       domain_key=domain_key, verify_context=verify_context,
                                       username=username, scope=config.get('_effective_cookie_scope', ''))
            if after and after != before:
                retry_config = dict(config)
                # 刷新成功后更新 _user_cookie 为新的 cookie 字符串
                retry_config["_user_cookie"] = after
                page_text = load_page(fetch_url, retry_config, load_full_page=load_full)
                title, description, preview_image, sources = _parse_metadata_from_content(
                    page_text, fetch_url, retry_config, include_sources=True
                )

        end = timezone.now()
        logger.debug("Parsing duration: %s", end - start)
    except ContentTypeResolutionError:
        logger.error(
            "Unable to determine metadata content type for url=%s",
            url,
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.error("Unexpected metadata parsing failure. url=%s", url, exc_info=exc)
        if include_sources:
            return _empty_metadata(url), {"error": str(exc)}
        return _empty_metadata(url)

    if config:
        title = apply_rewrite(title, config.get('rewrite_title'))
        description = apply_rewrite(description, config.get('rewrite_description'))
        preview_image = apply_rewrite(preview_image, config.get('rewrite_image'))

    metadata = WebsiteMetadata(
        url=(config.get("_rewrite_url") if config else None) or url,
        title=title,
        description=description,
        preview_image=preview_image,
    )
    if include_sources:
        return metadata, sources
    return metadata


_JSON_SELECTOR_RE = re.compile(r'^(.*?)(?:::json\((.*)\))$', re.DOTALL)
_ATTR_SELECTOR_RE = re.compile(r'^(.*?)(?:::attr\(([^)]*)\))$', re.DOTALL)


def _split_json_selector(selector: str) -> tuple[str | None, str | None]:
    """Split a CSS selector with a trailing ``::json(path)`` pseudo-element.

    Returns ``(css_selector, json_path)``. ``json_path`` is ``None`` when the
    selector does not contain the ``::json()`` pseudo-element.
    """
    m = _JSON_SELECTOR_RE.match(selector.strip())
    if not m:
        return None, None
    css = m.group(1).strip()
    path = m.group(2).strip()
    return css, path


def _split_attr_selector(selector: str) -> tuple[str | None, str | None]:
    """Split a CSS selector with a trailing ``::attr(name)`` pseudo-element.

    Returns ``(css_selector, attr_name)``. ``attr_name`` is ``None`` when the
    selector does not contain the ``::attr()`` pseudo-element.
    """
    m = _ATTR_SELECTOR_RE.match(selector.strip())
    if not m:
        return None, None
    css = m.group(1).strip()
    attr_name = m.group(2).strip()
    if not attr_name:
        return None, None
    return css, attr_name


def _is_placeholder_image_src(value: str | None) -> bool:
    """Return True for image URLs that should not be used as previews."""
    if not value:
        return True
    trimmed = value.strip()
    if not trimmed or trimmed == "data:,":
        return True
    return (
        trimmed.startswith("blob:")
        or trimmed.startswith("about:")
        or trimmed == "#"
        or (
            trimmed.lower().startswith("data:image/")
            and ";base64," in trimmed.lower()
        )
    )


def _traverse_json_path(data, path: str):
    """Walk a dotted path like ``author.name`` or ``items[0].url`` inside JSON.

    Supports nested keys, array indices, and ``@``-prefixed JSON-LD keys.
    A ``*`` wildcard matches all elements of a dict or list, returning the
    first non-None descendant. Returns ``None`` when the path cannot resolve.
    """
    tokens = re.findall(r'[^.\[\]]+|\[\d+\]', path)
    current = data
    for i, token in enumerate(tokens):
        if current is None:
            return None
        if token.startswith('[') and token.endswith(']'):
            try:
                idx = int(token[1:-1])
            except ValueError:
                return None
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        elif token == '*':
            rest = '.'.join(tokens[i + 1:])
            if isinstance(current, list):
                for item in current:
                    result = _traverse_json_path(item, rest)
                    if result is not None:
                        return result
            elif isinstance(current, dict):
                for item in current.values():
                    result = _traverse_json_path(item, rest)
                    if result is not None:
                        return result
            return None
        else:
            if isinstance(current, dict):
                current = current.get(token)
            else:
                return None
    return current


def _extract_json_value_from_script(el, json_path: str) -> str | None:
    """Parse a <script> tag's JSON body and resolve ``json_path``.

    Handles both single objects and arrays of objects, and expands ``@graph``
    arrays, returning the first non-empty result. Complex return values
    (dict/list) are flattened via ``_json_value_to_string``.
    """
    raw = el.string
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    items = data if isinstance(data, list) else [data]
    expanded = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get('@graph'), list):
            expanded.extend(item['@graph'])
        else:
            expanded.append(item)
    items = expanded
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _traverse_json_path(item, json_path)
        result = _json_value_to_string(value)
        if result:
            return result
    return None


def _extract_json_ld(soup) -> dict:
    """Extract metadata from the first application/ld+json script tag.
    Returns dict with optional keys: title, description, image.
    """
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        # Normalise to a list of objects
        if isinstance(data, dict):
            items = [data] + (data.get("@graph") if isinstance(data.get("@graph"), list) else [])
        elif isinstance(data, list):
            items = data
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            # Skip non-content types
            item_type = item.get("@type", "")
            if isinstance(item_type, list):
                type_set = set(item_type)
            else:
                type_set = {item_type} if isinstance(item_type, str) else set()
            if type_set & _JSON_LD_SKIP_TYPES:
                continue
            result = {}
            # title
            title = item.get("headline") or item.get("name") or item.get("title")
            if title and isinstance(title, str):
                result["title"] = title.strip()
            # description
            desc = item.get("description")
            if desc and isinstance(desc, str):
                result["description"] = desc.strip()
            # image
            img = item.get("image") or item.get("thumbnailUrl")
            if img:
                if isinstance(img, str):
                    result["image"] = img.strip()
                elif isinstance(img, dict):
                    url_val = img.get("url")
                    if url_val and isinstance(url_val, str):
                        result["image"] = url_val.strip()
                elif isinstance(img, list) and img:
                    first = img[0]
                    if isinstance(first, str):
                        result["image"] = first.strip()
                    elif isinstance(first, dict):
                        url_val = first.get("url")
                        if url_val and isinstance(url_val, str):
                            result["image"] = url_val.strip()
            if result:
                return result
    return {}


def normalize_content_type(value) -> str | None:
    """Normalize a config value or HTTP Content-Type to html/json/xml."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().split(";", 1)[0].strip()
    return _CONTENT_TYPE_ALIASES.get(normalized)


def _selectors_from_config(config: dict | None) -> list[str]:
    if not config:
        return []
    selectors = []
    for key in ("select_title", "select_description", "select_image"):
        value = config.get(key)
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list):
            selectors.extend(
                item.strip()
                for item in value
                if isinstance(item, str) and item.strip()
            )
    return selectors


def _infer_content_type_from_selectors(config: dict | None) -> str | None:
    selectors = _selectors_from_config(config)
    if not selectors:
        return None
    first = selectors[0]
    if first.startswith("$") or first.startswith("["):
        return "json"
    if first.startswith("/"):
        return "xml"
    return "html"


def resolve_content_type(config: dict | None, default: str | None = None) -> str:
    """Resolve the extraction format using explicit, selector, and header signals."""
    explicit = normalize_content_type((config or {}).get("content_type"))
    if explicit:
        return explicit

    inferred = _infer_content_type_from_selectors(config)
    if inferred:
        return inferred

    response_type = normalize_content_type((config or {}).get("_response_content_type"))
    if response_type:
        return response_type

    if default is not None:
        return default
    raise ContentTypeResolutionError(
        "Could not determine response format. "
        "Set content_type, use a recognized selector syntax, or supply Content-Type."
    )


def _metadata_content_type(config: dict | None, default: str | None = None) -> str:
    return resolve_content_type(config, default=default)


def _empty_parse_sources():
    return {
        "title": {"value": None, "selector": None},
        "description": {"value": None, "selector": None},
        "preview_image": {"value": None, "selector": None},
    }


def _parse_metadata_from_content(
    content: str,
    url: str,
    config: dict | None = None,
    include_sources: bool = False,
):
    content_type = resolve_content_type(config)
    if content_type == "json":
        return _parse_metadata_from_json(content, url, config, include_sources)
    if content_type == "xml":
        return _parse_metadata_from_xml(content, url, config, include_sources)

    soup = BeautifulSoup(content, "html.parser")
    return _parse_metadata_from_soup(soup, url, config, include_sources)


def _parse_metadata_from_json(
    content: str,
    url: str,
    config: dict | None = None,
    include_sources: bool = False,
):
    sources = {}
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        if include_sources:
            return None, None, None, _empty_parse_sources()
        return None, None, None

    title, source = _extract_with_json_paths(
        data, (config or {}).get("select_title") or []
    )
    sources["title"] = {"value": title, "selector": source}

    description, source = _extract_with_json_paths(
        data, (config or {}).get("select_description") or []
    )
    sources["description"] = {"value": description, "selector": source}

    preview_image, source = _extract_with_json_paths(
        data, (config or {}).get("select_image") or []
    )
    if preview_image and not preview_image.startswith(("http://", "https://")):
        preview_image = urljoin(url, preview_image)
    sources["preview_image"] = {"value": preview_image, "selector": source}

    if include_sources:
        return title, description, preview_image, sources
    return title, description, preview_image


def _parse_metadata_from_xml(
    content: str,
    url: str,
    config: dict | None = None,
    include_sources: bool = False,
):
    sources = {}
    try:
        root = etree.fromstring(_strip_xml_declaration(content))
    except (etree.XMLSyntaxError, ValueError):
        if include_sources:
            return None, None, None, _empty_parse_sources()
        return None, None, None

    title, source = _extract_with_xpath(
        root, (config or {}).get("select_title") or [], "title", config
    )
    sources["title"] = {"value": title, "selector": source}

    description, source = _extract_with_xpath(
        root, (config or {}).get("select_description") or [], "description", config
    )
    sources["description"] = {"value": description, "selector": source}

    preview_image, source = _extract_with_xpath(
        root, (config or {}).get("select_image") or [], "image", config
    )
    if preview_image and not preview_image.startswith(("http://", "https://")):
        preview_image = urljoin(url, preview_image)
    sources["preview_image"] = {"value": preview_image, "selector": source}

    if include_sources:
        return title, description, preview_image, sources
    return title, description, preview_image


def _strip_xml_declaration(content: str) -> str:
    content = content.lstrip()
    if content.startswith("<?xml"):
        end = content.find("?>")
        if end != -1:
            content = content[end + 2:].lstrip()
    return content


def _parse_metadata_from_soup(
    soup,
    url: str,
    config: dict | None = None,
    include_sources: bool = False,
):
    sources = {}

    title_selectors = config.get("select_title") if config else None
    title, source = _extract_with_selector_source(
        soup, title_selectors or [], url, "title"
    )
    sources["title"] = {"value": title, "selector": source}

    desc_selectors = config.get("select_description") if config else None
    description, source = _extract_with_selector_source(
        soup, desc_selectors or [], url, "description"
    )
    sources["description"] = {"value": description, "selector": source}

    image_selectors = config.get("select_image") if config else None
    preview_image, source = _extract_with_selector_source(
        soup, image_selectors or [], url, "image"
    )
    sources["preview_image"] = {"value": preview_image, "selector": source}

    json_ld = _extract_json_ld(soup)
    if title is None:
        title = json_ld.get("title")
        if title:
            sources["title"] = {"value": title, "selector": "json-ld"}
    if description is None:
        description = json_ld.get("description")
        if description:
            sources["description"] = {"value": description, "selector": "json-ld"}
    if preview_image is None:
        preview_image = json_ld.get("image")
        if preview_image:
            sources["preview_image"] = {"value": preview_image, "selector": "json-ld"}

    if (
        preview_image
        and not preview_image.startswith("http://")
        and not preview_image.startswith("https://")
    ):
        preview_image = urljoin(url, preview_image)
    sources["preview_image"] = {
        "value": preview_image,
        "selector": sources["preview_image"]["selector"],
    }

    if include_sources:
        return title, description, preview_image, sources
    return title, description, preview_image


def _extract_with_selector_source(soup, selectors, url: str = "", field: str = ""):
    if isinstance(selectors, str):
        selectors = [selectors]
    for selector in selectors or []:
        if not selector or not selector.strip():
            continue
        css_selector, json_path = _split_json_selector(selector)
        if json_path is not None:
            try:
                el = soup.select_one(css_selector)
            except Exception:
                continue
            if not el:
                continue
            value = _extract_json_value_from_script(el, json_path)
        else:
            css_selector, attr_name = _split_attr_selector(selector)
            if attr_name is not None:
                try:
                    el = soup.select_one(css_selector)
                except Exception:
                    continue
                if not el:
                    continue
                value = el.get(attr_name)
            else:
                try:
                    el = soup.select_one(selector)
                except Exception:
                    continue
                if not el:
                    continue
                value = _extract_element_value(el, field)
        if value and field == "image" and _is_placeholder_image_src(value):
            value = None
        if value:
            value = urljoin(url, value.strip()) if field == "image" else value.strip()
            return value, selector
    return None, None


def _extract_element_value(el, field: str) -> str | None:
    if el.name == "meta":
        return el.get("content")
    if field == "image":
        return el.get("src") or el.get("href") or el.get("content") or el.get("url")

    value = el.get("content") or el.get_text(" ", strip=True)
    if field == "description" and value and el.get("type") == "html":
        return BeautifulSoup(value, "html.parser").get_text("\n", strip=True) or None
    return value


def _extract_with_json_paths(data, paths):
    if isinstance(paths, str):
        paths = [paths]
    for path in paths or []:
        if not path or not path.strip():
            continue
        try:
            normalized_path = path.strip()
            if normalized_path.startswith("["):
                normalized_path = "$" + normalized_path
            elif not normalized_path.startswith("$"):
                normalized_path = "$." + normalized_path
            nodes = jsonpath_find(normalized_path, data)
        except Exception:
            continue
        for node in nodes:
            value = _json_value_to_string(node.value)
            if value:
                return value, path
    return None, None


def _extract_with_xpath(root, expressions, field: str, config: dict | None = None):
    if isinstance(expressions, str):
        expressions = [expressions]
    configured_namespaces = (config or {}).get("xmlns") or {}
    if not isinstance(configured_namespaces, dict):
        configured_namespaces = {}
    namespaces = {}
    namespaces.update(configured_namespaces)
    for element in root.iter():
        for prefix, uri in (element.nsmap or {}).items():
            if not prefix:
                continue
            namespaces.setdefault(prefix, uri)
    default_uri = (root.nsmap or {}).get(None)
    default_alias = _DEFAULT_NAMESPACE_ALIASES.get(default_uri or "")
    if default_alias:
        namespaces.setdefault(default_alias, default_uri)
    namespaces = namespaces or None
    for expression in expressions or []:
        if not expression or not expression.strip():
            continue
        try:
            result = root.xpath(
                expression,
                namespaces=namespaces,
            )
        except (etree.XPathError, ValueError):
            result = None
        value = _xpath_result_to_string(result, field)
        if value:
            return value, expression
        if default_uri:
            value, _ = _select_with_default_namespace(
                root,
                expression,
                namespaces,
                field,
                default_uri,
            )
            if value:
                return value, expression
    return None, None


def _select_with_default_namespace(
    root,
    expression: str,
    namespaces: dict | None,
    field: str,
    default_namespace: str,
):
    try:
        selector = XPathSelector(
            expression,
            namespaces=namespaces,
            default_namespace=default_namespace,
        )
    except Exception:
        return None, None
    try:
        result = selector.select(root)
    except Exception:
        return None, None
    value = _xpath_result_to_string(result, field)
    return value, expression


def _xpath_result_to_string(result, field: str) -> str | None:
    if isinstance(result, list):
        for item in result:
            value = _xpath_result_to_string(item, field)
            if value:
                return value
        return None
    if isinstance(result, etree._Element):
        return _xpath_element_to_string(result, field)
    if isinstance(result, bool):
        return "true" if result else "false"
    if isinstance(result, (int, float)):
        return str(result)
    if isinstance(result, str):
        return result.strip() or None
    return None


def _xpath_element_to_string(el, field: str) -> str | None:
    if field == "image":
        for attr in ("src", "href", "content", "url"):
            value = el.get(attr)
            if value:
                return value

    value = "".join(el.itertext()).strip()
    if field == "description" and value and el.get("type") == "html":
        return BeautifulSoup(value, "html.parser").get_text("\n", strip=True) or None
    return value or None


def _json_value_to_string(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            result = _json_value_to_string(item)
            if result:
                return result
        return None
    if isinstance(value, dict):
        for key in ("url", "src", "href", "content", "text", "title", "name", "description"):
            result = _json_value_to_string(value.get(key))
            if result:
                return result
    return None


def load_website_metadata_for_test(url: str, username: str = ''):
    config = get_metadata_config(url, username=username)
    if config and config.get("scripts"):
        metadata = _load_with_hooks(url, config, config["scripts"], username=username)
        script_paths = [
            entry.get("path")
            for entry in config["scripts"]
            if entry.get("path")
        ]
        return metadata, {"scripts": script_paths}, config

    if config and config.get("script"):
        script_path = config["script"]
        load_full = config.get("load_full_page", True) if config else True
        try:
            body = load_page(config.get("_request_url", url), config, load_full_page=load_full)
        except (RetryableMetadataError, NonRetryableMetadataError) as exc:
            return _empty_metadata(url), {"error": str(exc)}, config
        result = run_script(script_path, url=url, config=config, html_content=body)
        if result and isinstance(result, dict):
            metadata = WebsiteMetadata(
                url=result.get('url') or url,
                title=apply_rewrite(result.get('title'), config.get('rewrite_title')),
                description=apply_rewrite(result.get('description'), config.get('rewrite_description')),
                preview_image=apply_rewrite(result.get('preview_image'), config.get('rewrite_image')),
            )
        else:
            metadata = _empty_metadata(url)
        return metadata, {"script": script_path}, config

    metadata, sources = _load_website_metadata(url, config, username, include_sources=True)
    return metadata, sources, config



_browser_semaphore = threading.Semaphore(2)


def _wait_for_browser_elements(page, wait_elements, timeout_ms: int) -> None:
    """Wait for configured selectors before returning browser content.

    Each entry supports "|" separated OR alternatives, matching the snapshot
    engine semantics. A timeout is non-fatal: the current DOM is kept so a
    JS-rendered page is never discarded in favor of a plain requests fallback.
    """
    for entry in wait_elements:
        if not entry:
            continue
        alternatives = [s.strip() for s in str(entry).split("|") if s.strip()]
        if not alternatives:
            continue
        try:
            page.wait_for_function(
                "alts => alts.some(sel => document.querySelector(sel))",
                arg=alternatives,
                timeout=timeout_ms,
            )
        except Exception as e:
            try:
                current_url = page.url
            except Exception:
                current_url = ""
            logger.warning(
                "Browser wait_elements not satisfied, continuing with current DOM. "
                "url=%s selectors=%s: %s",
                current_url,
                alternatives,
                e,
            )


def _load_page_via_browser(url: str, config: dict) -> str | None:
    """Load page HTML via a fresh ephemeral browser context.

    The context never shares the persistent ``chromium-profile`` used by
    snapshots or cookie refresh, so concurrent metadata loads cannot race on
    a profile lock. Already-resolved cookies are injected into the context to
    cover logged-in sites. Returns HTML string or None on failure.
    """
    browser_config = config.get('use_browser') or {}
    if not isinstance(browser_config, dict):
        return None

    enabled = browser_config.get('enabled', True)
    if not enabled:
        return None

    wait_until = browser_config.get('wait_until', 'networkidle')
    wait_elements = browser_config.get('wait_elements', '')
    if isinstance(wait_elements, str):
        wait_elements = [wait_elements] if wait_elements else []
    browser_timeout = browser_config.get('timeout') or config.get('timeout', 10)
    timeout_ms = int(browser_timeout * 1000)

    from site_adapters.services.engine.browser_provider import launch_browser

    if not _browser_semaphore.acquire(timeout=5):
        logger.warning("Browser load: max concurrent reached, falling back to requests. url=%s", url)
        return None

    browser = None
    try:
        browser = launch_browser(headless=True)
        context = browser.new_context()
        cookie_str = _cookie_string_from_config(config)
        if cookie_str:
            domain_key = config.get("_domain_key") or urlparse(url).hostname or ""
            cookies = cookie_string_to_playwright_list(cookie_str, domain_key)
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception as e:
                    logger.warning(
                        "Failed to inject browser cookies for metadata. url=%s: %s",
                        url,
                        e,
                    )
        page = context.new_page()
        page.goto(url, timeout=timeout_ms, wait_until=wait_until)

        # Wait for configured elements (OR alternatives via "|"). Timeouts are
        # non-fatal so the rendered DOM is still returned to the caller.
        _wait_for_browser_elements(page, wait_elements, timeout_ms)

        return page.content()
    except Exception as e:
        logger.warning("Browser load failed, falling back to requests. url=%s: %s", url, e)
        return None
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
            # Playwright sync API runs an asyncio event loop in this thread.
            # If pw.stop() is not called, the loop keeps running and Django's
            # async-unsafe guard raises SynchronousOnlyOperation for every
            # subsequent DB access on this (pooled) thread.
            pw = getattr(browser, "__playwright__", None)
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
        _browser_semaphore.release()


def load_page(url: str, config: dict = None, load_full_page: bool = False):
    # Browser path: use_browser is declared and not null
    browser_config = config.get('use_browser') if config else None
    if browser_config is not None:
        html = _load_page_via_browser(url, config)
        if html is not None:
            return html
        # Fall through to requests on failure (warning already logged)

    # Per-domain rate limiting
    domain = get_registrable_domain(url)
    if domain:
        _wait_for_domain(domain)

    headers = build_request_headers(config)
    cookies = build_request_cookies(config)
    timeout = config.get("timeout", 10) if config else 10
    proxies = config.get("proxy") if config else None

    # Build equivalent curl command for debugging
    curl_cmd = ['curl', '-sS', '-L', '--max-time', str(timeout)]
    for k, v in (headers or {}).items():
        curl_cmd += ['-H', f'{k}: {v}']
    for k, v in (cookies or {}).items():
        curl_cmd += ['-b', f'{k}={v}']
    curl_cmd.append(url)
    _page_start = time.monotonic()

    # Unit: KB
    CHUNK_SIZE = config.get("chunk_size", 50 * 1024) if config else 50 * 1024
    MAX_CONTENT_LIMIT = (
        config.get("max_content_limit", 5000 * 1024) if config else 5000 * 1024
    )

    size = 0
    content = None
    iteration = 0
    try:
        with requests.get(
            url,
            timeout=timeout,
            headers=headers,
            cookies=cookies,
            proxies=proxies,
            stream=True,
        ) as r:
            status_code = r.status_code
            if status_code == 429 or status_code >= 500:
                if domain:
                    _record_domain_request(domain)
                raise RetryableMetadataError(
                    f"Retryable metadata response: {status_code}", status_code
                )
            if status_code >= 400:
                if domain:
                    _record_domain_request(domain)
                raise NonRetryableMetadataError(
                    f"Non-retryable metadata response: {status_code}", status_code
                )

            if isinstance(config, dict):
                response_headers = getattr(r, "headers", {}) or {}
                response_content_type = response_headers.get("Content-Type")
                if response_content_type:
                    config["_response_content_type"] = response_content_type

            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                size += len(chunk)
                iteration = iteration + 1
                content = chunk if content is None else content + chunk

                logger.debug(
                    "Loaded chunk (iteration=%d, total=%.1fKB)", iteration, size / 1024
                )

                # Stop reading at </head> unless loading full page
                if not load_full_page:
                    end_of_head = b"</head>"
                    if end_of_head in content:
                        logger.debug("Found closing head tag after %d bytes", size)
                        content = content.split(end_of_head)[0] + end_of_head
                        break
                # Stop reading if we exceed limit
                if size > MAX_CONTENT_LIMIT:
                    logger.debug("Cancel reading document after %d bytes", size)
                    break
    except (RetryableMetadataError, NonRetryableMetadataError) as exc:
        duration_ms = int((time.monotonic() - _page_start) * 1000)
        log_execution(
            url=url,
            domain_key="",
            step="metadata",
            cmd=curl_cmd,
            returncode=exc.status_code if exc.status_code is not None else 1,
            stderr=str(exc)[:500],
            duration_ms=duration_ms,
        )
        raise
    except requests.exceptions.RequestException as exc:
        duration_ms = int((time.monotonic() - _page_start) * 1000)
        log_execution(url=url, domain_key="", step="metadata",
                      cmd=curl_cmd, returncode=1,
                      stderr=str(exc)[:500], duration_ms=duration_ms)
        if domain:
            _record_domain_request(domain)
        raise RetryableMetadataError(
            f"Retryable metadata request failure for {url}"
        ) from exc

    if not content:
        return ""

    # Use charset_normalizer to determine encoding that best matches the response content.
    # Several sites specify the response encoding incorrectly, so we ignore it and use
    # custom logic instead of Response.text which respects the declared encoding first.
    results = from_bytes(content or "")
    duration_ms = int((time.monotonic() - _page_start) * 1000)
    log_execution(url=url, domain_key="", step="metadata",
                  cmd=curl_cmd, returncode=0, duration_ms=duration_ms)
    if domain:
        _record_domain_request(domain)
    return str(results.best())




def get_request_config(url: str) -> dict | None:
    return get_metadata_config(url)


def detect_content_type(
    url: str, config: dict | None = None, timeout: int = 10
) -> str | None:
    request_config = config if config is not None else get_request_config(url)
    request_timeout = (
        request_config.get("timeout", timeout) if request_config else timeout
    )
    request_kwargs = {
        "allow_redirects": True,
        "cookies": build_request_cookies(request_config),
        "headers": build_request_headers(request_config),
        "timeout": request_timeout,
    }
    proxies = request_config.get("proxy") if request_config else None
    if proxies:
        request_kwargs["proxies"] = proxies

    try:
        response = requests.head(url, **request_kwargs)
        if response.status_code == 200:
            return (
                response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            )
    except requests.RequestException:
        pass

    try:
        with requests.get(url, stream=True, **request_kwargs) as response:
            if response.status_code == 200:
                return (
                    response.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
    except requests.RequestException:
        pass

    return None


def is_pdf_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type in ("application/pdf", "application/x-pdf")


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
        if config.get("headers", {}).get("Cookie"):
            headers.pop("Cookie", None)
    return headers


def build_request_cookies(config: dict = None) -> dict:
    cookies = {}
    cookies_str = _cookie_string_from_config(config)
    if cookies_str:
        try:
            simple_cookie = SimpleCookie()
            simple_cookie.load(cookies_str)
            cookies = {key: value.value for key, value in simple_cookie.items()}
        except Exception:
            logger.warning("Failed to parse cookies for config")
            return cookies
    return cookies


def _cookie_string_from_config(config: dict = None) -> str | None:
    if not config:
        return None
    # Priority: user credentials > shared credentials > Cookie header (http config)
    user_cookie = config.get("_user_cookie")
    if user_cookie:
        return user_cookie
    cookie_config = config.get("cookie")
    if isinstance(cookie_config, dict) and cookie_config.get("file"):
        from site_adapters.services.auth.cookies import load_cookie_file
        cookie = load_cookie_file(cookie_config["file"])
        if cookie:
            return cookie
    domain_key = config.get("_domain_key")
    scope = config.get("_effective_cookie_scope", "")
    if domain_key:
        shared, _ = get_shared_cookie(hostname=domain_key, scope=scope)
        if shared:
            return shared
    cookies_str = config.get("headers", {}).get("Cookie")
    if cookies_str:
        return cookies_str
    return None
