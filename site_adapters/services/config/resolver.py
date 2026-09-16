"""
Config resolver: provides unified config access for each service.

Config structure:
  {
    "auth": { ... },      # auth requirements (cookie + headers)
    "defaults": { ... },  # shared settings
    "metadata": { ... },   # metadata extraction
    "snapshot": { ... },   # HTML snapshot or raw XML/JSON capture
    "reader": { ... }      # reader mode
  }

Merge rule: defaults + section -> section overrides same-name fields.
HTTP sub-objects are merged: defaults.http + section.http -> section overrides.
Auth sub-objects are merged: top.auth + defaults.auth + section.auth -> section overrides.
"""

import json
import logging
import os

from bookmarks.utils import atomic_write
from site_adapters.services.auth.cookies import (
    COOKIE_DEFAULTS,
    merge_cookie,
)
from site_adapters.services.auth.credentials import (
    _merge_headers_block,
    _normalize_headers_block,
    get_best_basic_auth,
    get_best_cookie,
    get_best_header,
    get_best_headers,
    get_best_token,
)
from site_adapters.services.auth.oauth2 import (
    get_token_header,
    get_valid_token,
)
from site_adapters.services.auth.oauth2 import (
    refresh_token as _refresh_token,
)
from site_adapters.services.base import _get_base_dir
from site_adapters.services.config import (
    apply_request_url,
    apply_rewrite_url,
    deep_merge,
)
from site_adapters.services.config.loader import (
    load_builtin_config,
    load_domain_config,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

# Default lazy-load attribute names used when process_lazy_images is true.
# Kept in sync with DEFAULT_LAZY_ATTRS in snapshot_browser_script.js.
_DEFAULT_LAZY_ATTRS = [
    "data-src", "data-actualsrc", "data-original", "data-lazy-src",
    "data-original-src", "data-actual-image", "data-lazy", "data-defer-src",
]


def _merge_lazy_attrs(section_attrs: list) -> list:
    """Merge section-level lazy attrs with built-in defaults (union, deduplicated)."""
    merged = list(_DEFAULT_LAZY_ATTRS)
    for attr in section_attrs:
        if attr and attr not in merged:
            merged.append(attr)
    return merged


def _merge_dicts(base: dict, override: dict) -> dict:
    """Merge two dicts: override values replace base, None removes key."""
    result = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Auth merge
# ---------------------------------------------------------------------------

def _merge_auth(*auth_blocks: dict) -> dict:
    """
    Merge multiple auth blocks. Later blocks override earlier ones.
    cookie: deep-merge using merge_cookie()
    headers: merge dicts (later overrides same key)
    oauth2: replace as a whole (later overrides)
    basic_auth: replace as a whole (later overrides)
    """
    result = {}
    for block in auth_blocks:
        if not block:
            continue
        # Cookie
        if 'cookie' in block:
            result['cookie'] = merge_cookie(result.get('cookie', {}), block['cookie'])
        # Headers
        if 'headers' in block:
            result['headers'] = _merge_headers_block(
                result.get('headers', {}), block['headers'])
        # OAuth2 (was token)
        if 'oauth2' in block:
            result['oauth2'] = dict(block['oauth2'])
        # Basic Auth
        if 'basic_auth' in block:
            result['basic_auth'] = dict(block['basic_auth'])
    return result


# ---------------------------------------------------------------------------
# User toggle preferences
# ---------------------------------------------------------------------------

def _get_user_toggles_path(username: str) -> str:
    return os.path.join(
        _get_base_dir(), 'preferences', 'users', username, 'toggles.json'
    )


def get_user_preferences(username: str) -> dict:
    """Get all user toggle preferences keyed by domain."""
    path = _get_user_toggles_path(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_user_domain_preferences(username: str, domain_key: str) -> dict:
    """Get user toggle preferences for a specific domain."""
    prefs = get_user_preferences(username)
    domain_prefs = prefs.get(domain_key, {})
    return domain_prefs if isinstance(domain_prefs, dict) else {}


def save_user_preferences(
    username: str, domain_key: str, toggle_id: str, enabled: bool
):
    """Save a single user toggle preference."""
    prefs = get_user_preferences(username)
    if domain_key not in prefs:
        prefs[domain_key] = {}
    prefs[domain_key][toggle_id] = enabled
    path = _get_user_toggles_path(username)
    atomic_write(path, json.dumps(prefs, indent=2, ensure_ascii=False))


def list_domains_with_toggles(base_dir: str) -> list[dict]:
    """List all domains that declare toggles and their toggle definitions."""
    from site_adapters.services.config.loader import _cache
    all_config = _cache.load(base_dir)
    result = []
    for key, config in all_config.items():
        if key == 'defaults' or key.startswith('_'):
            continue
        if not isinstance(config, dict):
            continue
        toggles = config.get('snapshot', {}).get('toggles', {})
        if toggles and isinstance(toggles, dict):
            result.append({
                'domain': key,
                'toggles': toggles,
            })
    return result



def _apply_toggles(section_data: dict, full_config: dict, username: str) -> tuple[list, list]:
    """Apply user toggle preferences to remove_elements / keep_elements."""
    remove_elements = list(section_data.get('remove_elements') or [])
    keep_elements = list(section_data.get('keep_elements') or [])
    toggles = section_data.get('toggles', {})
    user_prefs = {}
    if toggles and username:
        domain_key = full_config.get('_domain_key', '')
        user_prefs = get_user_domain_preferences(username, domain_key)
    if toggles:
        for toggle_id, toggle_def in toggles.items():
            if not isinstance(toggle_def, dict):
                continue
            selector = toggle_def.get('selector', '')
            if not selector:
                continue
            default_keep = toggle_def.get('default', True)
            user_choice = user_prefs.get(toggle_id)
            should_keep = user_choice if user_choice is not None else default_keep
            if not should_keep:
                if selector not in remove_elements:
                    remove_elements.append(selector)
                if selector in keep_elements:
                    keep_elements.remove(selector)
            else:
                if selector in remove_elements:
                    remove_elements.remove(selector)
                if selector not in keep_elements:
                    keep_elements.append(selector)
    return remove_elements, keep_elements

# ---------------------------------------------------------------------------
# Section config builder
# ---------------------------------------------------------------------------

def _build_section_config(full_config: dict, section: str, base_dir: str, username: str = '') -> dict:
    """
    Build flat config for a section by merging defaults + section.

    Returns a dict with:
    - headers: HTTP headers dict
    - timeout, proxy: framework fields
    - auth: merged auth config dict (cookie + headers)
    - request_url; rewrite_url (metadata only)
    - section-specific fields
    - _request_url, _rewrite_url: resolved URLs
    - _domain_key, _raw: metadata
    - _scope: effective scope for this section's auth sub-types
    """
    default = full_config.get('defaults', {})
    section_data = full_config.get(section, {})

    # Merge: defaults + section (section overrides)
    merged = _merge_dicts(default, section_data)

    # HTTP: defaults.http + section.http
    default_http = default.get('http', {})
    section_http = section_data.get('http', {})
    merged_http = _merge_dicts(default_http, section_http)

    # Extract framework fields from merged
    timeout = merged.get('timeout')
    proxy = merged.get('proxy')

    # HTTP headers (all keys in http sub-object are headers)
    headers = {k: v for k, v in merged_http.items() if v is not None}

    # --- Auth: front-door check for auth: null ---
    section_auth_raw = section_data.get('auth') if isinstance(section_data, dict) else None
    auth_disabled = (section_auth_raw is None and
                     isinstance(section_data, dict) and 'auth' in section_data)

    # Effective cookie scope: '' for domain-level, section name for section-level
    effective_cookie_scope = ''

    # Domain-level auth baseline
    top_auth = full_config.get('auth', {})
    default_auth = default.get('auth', {})

    if auth_disabled:
        # auth: null → skip all auth for this section
        merged_auth = {}
        cookie_config = {}
        user_cookie_str = None
    else:
        # Normal merge: top.auth + defaults.auth + section.auth
        section_auth_val = section_auth_raw if isinstance(section_auth_raw, dict) else {}
        merged_auth = _merge_auth(top_auth, default_auth, section_auth_val)

        # Determine which sub-types are section-level (defined in section.auth)
        section_has_cookie = isinstance(section_auth_raw, dict) and 'cookie' in section_auth_raw
        section_has_headers = isinstance(section_auth_raw, dict) and 'headers' in section_auth_raw
        section_has_oauth2 = isinstance(section_auth_raw, dict) and 'oauth2' in section_auth_raw
        section_has_basic = isinstance(section_auth_raw, dict) and 'basic_auth' in section_auth_raw

        # Domain key (used for cookie file path derivation)
        domain_key = full_config.get('_domain_key', '')
        # Hostname from the original URL (used for credential lookups with DNS fallback)
        from urllib.parse import urlparse
        hostname = urlparse(full_config.get('_url', '')).hostname or domain_key

        # Cookie config from auth (deep-merge with defaults)
        cookie_config = {}
        merged_cookie = merged_auth.get('cookie', {})
        if merged_cookie and merged_cookie.get('enabled', True):
            cookie_config = merge_cookie(dict(COOKIE_DEFAULTS), dict(merged_cookie))

        # cookie and http Cookie header cannot coexist
        if cookie_config and 'Cookie' in headers:
            logger.warning("%s: auth.cookie and Cookie header coexist, Cookie header ignored", section)
            headers.pop('Cookie', None)

        # --- Cookie: scope-aware lookup ---
        user_cookie_str = None
        if cookie_config:
            effective_cookie_scope = section if section_has_cookie else ''
            user_cookie_str, _ = get_best_cookie(
                username=username, hostname=hostname, scope=effective_cookie_scope)

            # Cross-scope fallback: if section-level and no cookie found,
            # fall back to domain-level saved cookies. Only block the fallback
            # when the domain explicitly declares a different cookie type.
            if not user_cookie_str and section_has_cookie:
                domain_cookie_type = ''
                domain_cookie_cfg = _merge_auth(top_auth, default_auth).get('cookie', {})
                if domain_cookie_cfg and domain_cookie_cfg.get('enabled', True):
                    domain_cookie_type = domain_cookie_cfg.get('type', 'auto')
                section_cookie_type = cookie_config.get('type', 'auto')
                if not domain_cookie_type or domain_cookie_type == section_cookie_type:
                    user_cookie_str, _ = get_best_cookie(
                        username=username, hostname=hostname, scope='')

        # --- Headers: read all saved credentials + config defaults ---
        # Priority: existing http headers > saved user/shared credentials > config defaults
        if 'headers' in merged_auth:
            headers_norm = _normalize_headers_block(merged_auth['headers'])
            if headers_norm.get('enabled', True):
                effective_header_scope = section if section_has_headers else ''

                # Step 1: read ALL saved header credentials (not limited to declared names)
                all_saved, _ = get_best_headers(
                    username=username, hostname=hostname, scope=effective_header_scope)
                # Section-level: fall back to domain-level if nothing found
                if not all_saved and section_has_headers:
                    all_saved, _ = get_best_headers(
                        username=username, hostname=hostname, scope='')

                # Step 2: inject saved headers (don't overwrite existing http headers)
                for name, val in all_saved.items():
                    if name not in headers:
                        headers[name] = val

                # Step 3: for declared headers without saved credentials, use config default
                declared_values = headers_norm.get('values', {})
                for header_name, default_val in declared_values.items():
                    if not isinstance(default_val, str):
                        default_val = ''
                    if header_name not in headers and default_val:
                        headers[header_name] = default_val

        # --- OAuth2: no cross-scope fallback ---
        merged_oauth2 = merged_auth.get('oauth2', {})
        if merged_oauth2.get('enabled', True) and merged_oauth2.get('endpoint'):
            effective_oauth2_scope = section if section_has_oauth2 else ''
            if username:
                access_token = get_valid_token(merged_oauth2, username, hostname,
                                                scope=effective_oauth2_scope)
                if access_token:
                    oauth2_headers = get_token_header(merged_oauth2, access_token)
                    headers.update(oauth2_headers)
            else:
                best_rt, _ = get_best_token(username=username, hostname=hostname,
                                            scope=effective_oauth2_scope)
                if best_rt:
                    token_result = _refresh_token(merged_oauth2, best_rt)
                    if token_result:
                        oauth2_headers = get_token_header(merged_oauth2, token_result['access_token'])
                        headers.update(oauth2_headers)

        # --- Basic Auth: no cross-scope fallback ---
        merged_basic = merged_auth.get('basic_auth', {})
        if isinstance(merged_basic, dict) and merged_basic.get('enabled', True) and merged_basic:
            effective_basic_scope = section if section_has_basic else ''
            best_ba, _ = get_best_basic_auth(username=username, hostname=hostname,
                                             scope=effective_basic_scope)
            if best_ba:
                import base64
                credentials = f"{best_ba['username']}:{best_ba['password']}"
                encoded = base64.b64encode(credentials.encode()).decode()
                headers['Authorization'] = f'Basic {encoded}'

    # --- Build result ---
    result = {
        'headers': headers,
        'timeout': timeout,
        'proxy': proxy,
        "http_engine": merged.get("http_engine"),
        'auth': merged_auth,
        'cookie': cookie_config,
        '_user_cookie': user_cookie_str,
        '_scope': section,
        '_effective_cookie_scope': effective_cookie_scope,
        'request_url': merged.get('request_url'),
    }
    if section == 'metadata':
        result['rewrite_url'] = merged.get('rewrite_url')

    # Section-specific fields
    if section == 'metadata':
        if 'content_type' in section_data:
            result['content_type'] = section_data['content_type']
        if 'xmlns' in section_data:
            result['xmlns'] = section_data['xmlns']
        if 'select_title' in section_data:
            result['select_title'] = section_data['select_title']
        if 'select_description' in section_data:
            result['select_description'] = section_data['select_description']
        if 'select_image' in section_data:
            result['select_image'] = section_data['select_image']
        if 'rewrite_title' in section_data:
            result['rewrite_title'] = section_data['rewrite_title']
        if 'rewrite_description' in section_data:
            result['rewrite_description'] = section_data['rewrite_description']
        if 'rewrite_image' in section_data:
            result['rewrite_image'] = section_data['rewrite_image']
        result['scripts'] = section_data.get('scripts')
        result['load_full_page'] = section_data.get('load_full_page', True)
        if 'max_content_limit' in section_data:
            result['max_content_limit'] = section_data['max_content_limit']
        if 'use_browser' in section_data:
            result['use_browser'] = section_data['use_browser']

    elif section == 'snapshot':
        # 'enabled' only reads from the snapshot section (not defaults),
        # so a domain/route can opt-out of snapshots without affecting
        # metadata or reader. Defaults to True when absent.
        result['enabled'] = section_data.get('enabled', True)
        if 'content_type' in section_data:
            result['content_type'] = section_data['content_type']
        if 'process_lazy_images' in section_data:
            lazy = section_data['process_lazy_images']
            if isinstance(lazy, list):
                # Merge with built-in default attributes (union, deduplicated)
                result['process_lazy_images'] = _merge_lazy_attrs(lazy)
            else:
                result['process_lazy_images'] = lazy
        if 'process_carousels' in section_data:
            result['process_carousels'] = section_data['process_carousels']
        result['remove_classes'] = section_data.get('remove_classes')
        result['set_styles'] = section_data.get('set_styles')
        result['scripts'] = section_data.get('scripts')
        result['singlefile_args'] = section_data.get('singlefile_args', {})
        result['toggles'] = section_data.get('toggles', {})
        result['wait_elements'] = section_data.get('wait_elements')
        result['wait_elements_timeout'] = section_data.get('wait_elements_timeout')
        result['remove_elements'], result['keep_elements'] = _apply_toggles(section_data, full_config, username)

    elif section == 'reader':
        result['defuddle_args'] = section_data.get('defuddle_args', {})

    # URL processing
    url = full_config.get('_url', '')
    if url:
        request_url = apply_request_url(url, merged.get('request_url'))
        if request_url:
            result['_request_url'] = request_url
        if section == 'metadata':
            rewrite_url = apply_rewrite_url(url, merged.get('rewrite_url'))
            if rewrite_url:
                result['_rewrite_url'] = rewrite_url

    # Metadata
    result['_domain_key'] = full_config.get('_domain_key')
    result['_raw'] = full_config.get('_raw')
    result['_adapter'] = full_config.get('_adapter')
    result['_route_key'] = full_config.get('_route_key')

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_metadata_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    builtin = load_builtin_config(base_dir) or {}
    domain = load_domain_config(url, base_dir)
    config = deep_merge(builtin, domain) if domain else builtin
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'metadata', base_dir, username)


def get_snapshot_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    builtin = load_builtin_config(base_dir) or {}
    domain = load_domain_config(url, base_dir)
    config = deep_merge(builtin, domain) if domain else builtin
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'snapshot', base_dir, username)


def get_reader_config(url: str, username: str = '') -> dict | None:
    base_dir = _get_base_dir()
    if not base_dir or not os.path.isdir(base_dir):
        return None
    builtin = load_builtin_config(base_dir) or {}
    domain = load_domain_config(url, base_dir)
    config = deep_merge(builtin, domain) if domain else builtin
    if not config:
        return None
    config['_url'] = url
    return _build_section_config(config, 'reader', base_dir, username)
