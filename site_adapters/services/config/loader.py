"""
Loader — adapter config loading + merging + per-source caching

Directory structure:
  data/site_adapters/
    adapters/
      config.jsonc            # _adapters list: declares all adapters and their priority
      defaults/
        adapters.jsonc         # built-in defaults + user-managed domains
        scripts/
      fivefilters/
        adapters.jsonc
        scripts/
      ...

config.jsonc format:
{
  "_adapters": [
    {"id": "my-publisher", "name": "my-adapter", "source": "https://...", "update_interval": 86400, "enabled": true},
    {"id": "local", "name": "custom", "source": "./path/to/adapters.jsonc"}
  ]
}

- id: publisher unique identifier (required), adapter directory named {id}.{name}
- name: adapter name (required), used for UI display and as part of directory name
- source: file source (required, https:// remote URL / local path)
- update_interval: remote source update interval (seconds), default 86400
- enabled: whether the adapter is enabled, default true

Merge priority:
  _adapters array order = priority (first is highest)
  The defaults adapter participates like any other adapter. Its `_builtin`
  baseline is applied separately at the lowest priority by the resolver.
"""

import copy
import fnmatch
import json
import logging
import os
import threading
import time
import re
from pathlib import Path
from urllib.parse import urlparse

from site_adapters.services.base import _get_adapters_dir
from site_adapters.services.config import (
    _resolve_all_paths,
    deep_merge,
    load_jsonc_file,
)
from site_adapters.services.config.bootstrap import ensure_defaults_adapter
from site_adapters.services.subscriptions import (
    _read_subscription_file,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Adapter file names
# ---------------------------------------------------------------------------

_ADAPTER_FILE = 'adapters.jsonc'
_CONFIG_FILE = 'config.jsonc'
_DEFAULTS_CACHE_KEY = 'adapter:defaults:defaults'


# ---------------------------------------------------------------------------
# Per-source cache
# ---------------------------------------------------------------------------

class SourceCache:
    """Cache domain configs per source, detecting changes via mtime."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sources: dict[str, tuple[tuple, dict]] = {}
        self._merged: dict | None = None
        self._base_dir: str = ''
        self._adapter_order: list[str] = []
        self._defaults_cache_key: str | None = None
        self._last_check: float = 0

    def _path_signature(self, path: str) -> tuple:
        if os.path.isfile(path):
            try:
                st = os.stat(path)
                return (path, st.st_mtime_ns, st.st_size)
            except OSError:
                return (path, 0, 0)
        if not os.path.isdir(path):
            return (path, 0)
        sig = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in sorted(files):
                if name.startswith('.'):
                    continue
                fpath = os.path.join(root, name)
                try:
                    st = os.stat(fpath)
                    sig.append((os.path.relpath(fpath, path), st.st_mtime_ns, st.st_size))
                except OSError:
                    pass
        return tuple(sig)

    def _resolve_adapter_path(self, entry: dict, adapters_dir: str) -> str:
        """Resolve the adapter file path.

        entry: {"id": "...", "name": "...", "source": "..."}
        source is the subscription directory (containing adapters.jsonc) or a direct file URL.

        - HTTPS URL → adapters/{id}.{name}/adapters.jsonc (cache target)
        - Absolute path → <source>/adapters.jsonc
        - Relative path → resolve relative to adapters/, append adapters.jsonc
        """
        from site_adapters.services.base import _adapter_dir
        from site_adapters.services.subscriptions import _normalize_source_to_directory
        name = entry.get('name', '') if isinstance(entry, dict) else ''
        source = entry.get('source', '') if isinstance(entry, dict) else ''
        dir_name = _adapter_dir(entry) if isinstance(entry, dict) else name

        if source.startswith('https://') or source.startswith('http://'):
            return os.path.join(adapters_dir, dir_name, _ADAPTER_FILE)
        # 本地目录路径：规范化并拼接 adapters.jsonc
        source = _normalize_source_to_directory(source)
        if os.path.isabs(source):
            return os.path.join(source, _ADAPTER_FILE)
        return os.path.normpath(os.path.join(adapters_dir, source, _ADAPTER_FILE))

    def _load_adapter_file(self, file_path: str) -> dict:
        """Load a single adapter file, returning {"*": ..., "domains": {...}}.

        Resolution logic:
        1. If the given path is a file → use it directly
        2. If the given path is a directory → try adapters.jsonc inside
        3. If the parent directory exists → try adapters.jsonc / <dirname>.jsonc
        4. Otherwise → return empty dict (e.g. remote adapter not yet fetched)
        """
        if os.path.isfile(file_path):
            pass  # file exists, use directly
        elif os.path.isdir(file_path):
            candidates = [
                os.path.join(file_path, 'adapters.jsonc'),
                os.path.join(file_path, os.path.basename(file_path) + '.jsonc'),
            ]
            file_path = None
            for c in candidates:
                if os.path.isfile(c):
                    file_path = c
                    break
            if file_path is None:
                logger.warning("Adapter file not found in directory: %s", file_path if file_path else candidates[0])
                return {'defaults': {}, '_builtin': {}, 'domains': {}}
        elif os.path.isdir(os.path.dirname(file_path)):
            dir_path = os.path.dirname(file_path)
            candidates = [
                os.path.join(dir_path, 'adapters.jsonc'),
                os.path.join(dir_path, os.path.basename(dir_path) + '.jsonc'),
            ]
            file_path = None
            for c in candidates:
                if os.path.isfile(c):
                    file_path = c
                    break
            if file_path is None:
                logger.warning("Adapter file not found in: %s", dir_path)
                return {'defaults': {}, '_builtin': {}, 'domains': {}}
        else:
            logger.debug("Adapter path not found, skipping: %s", file_path)
            return {'defaults': {}, '_builtin': {}, 'domains': {}}
        try:
            data = load_jsonc_file(file_path)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to parse adapter file: %s: %s", file_path, e)
            return {'defaults': {}, '_builtin': {}, 'domains': {}}
        if not isinstance(data, dict):
            logger.error("Adapter file top-level must be an object: %s", file_path)
            return {'defaults': {}, '_builtin': {}, 'domains': {}}

        # extract defaults, _builtin, and domains
        glob_defaults = data.get('defaults', {})
        if not isinstance(glob_defaults, dict):
            glob_defaults = {}
        global_defaults = data.get('_builtin', {})
        if not isinstance(global_defaults, dict):
            global_defaults = {}

        domains_raw = data.get('domains', {})
        if isinstance(domains_raw, dict):
            domains = dict(domains_raw)
        else:
            # compat: when no "domains" key, treat all non-meta keys as domains
            domains = {
                k: v for k, v in data.items()
                if k not in ('defaults', '_builtin', 'domains', '_meta') and not k.startswith('_')
            }

        # resolve script relative paths
        file_dir = str(Path(file_path).resolve().parent)
        domains = {
            k: _resolve_all_paths(v, file_dir) if isinstance(v, dict) else v
            for k, v in domains.items()
        }

        meta = data.get('_meta', {}) if isinstance(data, dict) else {}
        return {'defaults': glob_defaults, '_builtin': global_defaults, 'domains': domains, '_meta': meta}

    _CHECK_INTERVAL = 5.0

    def load(self, base_dir: str) -> dict:
        """Load and merge all adapters, returning the full merged config."""
        now = time.monotonic()
        with self._lock:
            if self._base_dir and self._base_dir != base_dir:
                self._sources.clear()
                self._merged = None
                self._adapter_order = []
                self._defaults_cache_key = None
            self._base_dir = base_dir
            if self._merged is not None and (now - self._last_check) < self._CHECK_INTERVAL:
                return self._merged
            self._last_check = now

        adapters_dir = os.path.join(base_dir, 'adapters')
        ensure_defaults_adapter(adapters_dir, sync=False)
        config_path = os.path.join(adapters_dir, _CONFIG_FILE)

        changed = False

        # 1. Read config.jsonc
        config_sig = self._path_signature(config_path)
        if self._sources.get('__config__', ((), {}))[0] != config_sig:
            try:
                config_data = load_jsonc_file(config_path) if os.path.exists(config_path) else {}
            except (json.JSONDecodeError, OSError):
                config_data = {}
            self._sources['__config__'] = (config_sig, config_data)
            changed = True
        config_data = self._sources.get('__config__', ((), {}))[1]

        adapters_list = config_data.get('_adapters', [])
        if not isinstance(adapters_list, list):
            adapters_list = []

        # dedup: same id+name is considered duplicate, keep the first occurrence
        seen_keys = set()
        deduped = []
        for item in adapters_list:
            if not isinstance(item, dict):
                deduped.append(item)
                continue
            key = (item.get('id', ''), item.get('name', ''))
            if key in seen_keys:
                logger.warning('Duplicate adapter id+name skipped: %s', key)
                continue
            seen_keys.add(key)
            deduped.append(item)
        adapters_list = deduped

        # filter enabled adapters
        enabled_adapters = []
        for item in adapters_list:
            if not isinstance(item, dict):
                continue
            if item.get('enabled') is False:
                continue
            name = item.get('name', '')

            enabled_adapters.append(item)

        # 2. Load each adapter
        new_order = []
        for item in enabled_adapters:
            name = item.get('name', '')
            if not name:
                continue
            file_path = self._resolve_adapter_path(item, adapters_dir)

            adapter_id = item.get('id', '')
            cache_key = f'adapter:{adapter_id}:{name}'
            new_order.append(cache_key)

            sig = self._path_signature(file_path)
            if self._sources.get(cache_key, (0,))[0] != sig:
                adapter_data = self._load_adapter_file(file_path)
                # apply exclude filtering
                exclude = item.get('exclude', [])
                if exclude and adapter_data.get('domains'):
                    adapter_data['domains'] = {
                        k: v for k, v in adapter_data['domains'].items()
                        if not any(fnmatch.fnmatch(k, pat) for pat in exclude)
                    }
                self._sources[cache_key] = (sig, adapter_data)
                changed = True

        # clean up removed adapters
        old_keys = [k for k in self._sources if k.startswith('adapter:')]
        for key in old_keys:
            if key not in new_order:
                self._sources.pop(key, None)
                changed = True

        if self._adapter_order != new_order:
            self._adapter_order = new_order
            changed = True
        self._defaults_cache_key = next(
            (key for key in new_order if key == _DEFAULTS_CACHE_KEY),
            None,
        )

        if changed or self._merged is None:
            with self._lock:
                self._merged = self._merge_all()

        with self._lock:
            return self._merged

    def _merge_all(self) -> dict:
        """Merge all adapters by priority.

        Rules:
        1. _adapters order = priority (first is highest)
        2. Within the same adapter: domain config overrides "*" ("*" is the internal baseline)
        3. Across adapters: earlier overrides later
        4. The defaults adapter's `_builtin` acts as the fallback config and is
           applied separately by load_builtin_config at the lowest priority.
        """
        merged_domains = {}

        # Build adapter entry lookup: cache_key -> {id, name, description, source, local_path}
        adapters_dir = os.path.join(self._base_dir, 'adapters')
        adapter_entries = {}
        adapters_list = (
            self._sources.get('__config__', ((), {}))[1].get('_adapters', [])
            if isinstance(self._sources.get('__config__', ((), {}))[1].get('_adapters'), list)
            else []
        )
        for item in adapters_list:
            if not isinstance(item, dict):
                continue
            if item.get('enabled') is False:
                continue
            name = item.get('name', '')
            adapter_id = item.get('id', '')
            if not name:
                continue
            cache_key = f'adapter:{adapter_id}:{name}'
            source = item.get('source', '')

            from site_adapters.services.base import _adapter_dir
            from site_adapters.services.subscriptions import resolve_adapter_path
            local_path = resolve_adapter_path(name, source, adapters_dir, adapter_id=adapter_id) if source else os.path.join(adapters_dir, _adapter_dir(item), 'adapters.jsonc')

            adapter_data = self._sources.get(cache_key, (0, {'_meta': {}}))[1]
            meta = adapter_data.get('_meta', {})
            description = meta.get('description', '') if isinstance(meta, dict) else ''

            adapter_entries[cache_key] = {
                'id': adapter_id,
                'name': name,
                'description': description,
                'source': source,
                'local_path': local_path,
            }

        _adapter_map = {}
        _raw_domain_configs = {}

        # merge from back to front (earlier overrides later)
        for cache_key in reversed(self._adapter_order):
            adapter_data = self._sources.get(cache_key, (0, {'defaults': {}, '_builtin': {}, 'domains': {}}))[1]
            glob_defaults = adapter_data.get('defaults', {})
            domains = adapter_data.get('domains', {})

            for domain_key, domain_config in domains.items():
                # Preserve pre-merge domain config for raw display
                if isinstance(domain_config, dict):
                    _raw_domain_configs[domain_key] = copy.deepcopy(domain_config)
                if not isinstance(domain_config, dict):
                    merged_domains[domain_key] = domain_config
                # within the same adapter: domain overrides "*"
                elif glob_defaults:
                    merged_domains[domain_key] = deep_merge(
                        copy.deepcopy(glob_defaults),
                        domain_config,
                    )
                else:
                    merged_domains[domain_key] = copy.deepcopy(domain_config)
                # Record source adapter for this domain_key
                if cache_key in adapter_entries:
                    _adapter_map[domain_key] = adapter_entries[cache_key]

        return {
            '_adapters': [
                item for item in (
                    self._sources.get('__config__', ((), {}))[1].get('_adapters', [])
                    if isinstance(self._sources.get('__config__', ((), {}))[1].get('_adapters'), list)
                    else []
                )
            ],
            '_defaults_cache_key': self._defaults_cache_key,
            '_adapter_map': _adapter_map,
            '_raw_domain_configs': _raw_domain_configs,
            **merged_domains,
        }

    def invalidate(self):
        with self._lock:
            self._sources.clear()
            self._merged = None
            self._adapter_order = []
            self._defaults_cache_key = None


_cache = SourceCache()


# ---------------------------------------------------------------------------
# _disabled_domains — read from config.jsonc top level (independent of adapter merge logic)
# ---------------------------------------------------------------------------

def _get_disabled_domains() -> set[str]:
    """Read the _disabled_domains list from the top level of config.jsonc."""
    adapters_dir = os.path.join(_cache._base_dir, 'adapters') if _cache._base_dir else _get_adapters_dir()
    config_path = os.path.join(adapters_dir, _CONFIG_FILE)
    try:
        if os.path.exists(config_path):
            data = load_jsonc_file(config_path)
            if isinstance(data, dict):
                disabled = data.get('_disabled_domains', [])
                if isinstance(disabled, list):
                    return set(disabled)
    except (json.JSONDecodeError, OSError):
        pass
    return set()


# ---------------------------------------------------------------------------
# Domain matching
# ---------------------------------------------------------------------------

def _get_domain(url: str) -> str:
    return urlparse(url).hostname or ""


def match_domain(url: str, domain_map: dict) -> tuple[str | None, dict | None]:
    domain = _get_domain(url)
    if not domain:
        return None, None
    if domain in domain_map:
        config = _resolve_alias(domain_map[domain], domain_map)
        if config is not None:
            return domain, config
    wildcard_keys = sorted(
        [k for k in domain_map if k.startswith('*.')],
        key=lambda k: k.count('.'),
        reverse=True,
    )
    for key in wildcard_keys:
        if domain.endswith(key[1:]):
            config = _resolve_alias(domain_map[key], domain_map)
            if config is not None:
                return key, config
    return None, None


def _resolve_alias(config, domain_map: dict, visited: set | None = None, _depth: int = 0) -> dict | None:
    _MAX_ALIAS_DEPTH = 10
    if not isinstance(config, dict):
        return config
    if config.get('type') != 'alias':
        return config
    target = config.get('target')
    if not target:
        return None
    if visited is None:
        visited = set()
    if target in visited:
        logger.warning("Domain alias cycle detected: %s", target)
        return None
    if _depth >= _MAX_ALIAS_DEPTH:
        logger.warning("Domain alias chain too deep (max %d): %s", _MAX_ALIAS_DEPTH, target)
        return None
    visited.add(target)
    target_config = domain_map.get(target)
    if target_config is None:
        logger.warning("Domain alias target not found: %s", target)
        return None
    return _resolve_alias(target_config, domain_map, visited, _depth + 1)


# ---------------------------------------------------------------------------
# Core: load domain config
# ---------------------------------------------------------------------------

def _apply_route_matching(url: str, config: dict) -> tuple[dict, str | None]:
    """Apply route matching to a domain config.

    Extracts 'routes' from config, matches URL path against patterns
    in document order, deep-merges the first matching route config.

    Returns (config_with_routes_removed_and_route_applied, matched_pattern).
    If no route matches, returns (config_without_routes, None).
    """
    routes = config.get('routes')
    # Remove routes key regardless of whether a route matched
    config = {k: v for k, v in config.items() if k != 'routes'}
    if not routes or not isinstance(routes, dict):
        return config, None
    path = urlparse(url).path or '/'
    for pattern, route_config in routes.items():
        if not isinstance(route_config, dict):
            continue
        try:
            if re.search(pattern, path):
                config = deep_merge(config, route_config)
                return config, pattern
        except re.error:
            logger.warning("Invalid route pattern: %s", pattern)
            continue
    return config, None


def load_domain_config(url: str, base_dir: str) -> dict | None:
    """
    Load the domain config for a URL.

    Domain configs are pre-merged with their adapter's defaults in _merge_all.
    Adapter priority is determined by declaration order (first is highest).

    Returns:
    {
        'auth': {...},
        'defaults': {...},
        'metadata': {...},
        'snapshot': {...},
        'reader': {...},
        '_domain_key': '...',
        '_raw': {...},
    }
    """
    all_config = _cache.load(base_dir)

    domain_key, domain_config = match_domain(url, all_config)
    if domain_config is None:
        return None

    # check if domain is disabled in config.jsonc
    disabled_domains = _get_disabled_domains()
    if domain_key in disabled_domains:
        return None

    result = copy.deepcopy(domain_config)
    # Apply route matching
    result, route_key = _apply_route_matching(url, result)
    result['_domain_key'] = domain_key
    result['_route_key'] = route_key
    result['_raw'] = copy.deepcopy(all_config.get('_raw_domain_configs', {}).get(domain_key, domain_config))
    adapter_map = all_config.get('_adapter_map', {})
    adapter = adapter_map.get(domain_key)
    if adapter:
        result['_adapter'] = adapter
    else:
        logger.warning("Matched domain '%s' but no adapter entry in _adapter_map", domain_key)
    return result


# ---------------------------------------------------------------------------
# Show config
# ---------------------------------------------------------------------------

def show_config(url: str, base_dir: str) -> dict:
    all_config = _cache.load(base_dir)

    domain_key, domain_config = match_domain(url, all_config)
    if domain_config is None:
        return {'matched': False, 'error': f'No matching domain config: {url}', 'domain': _get_domain(url)}

    # check if domain is disabled in config.jsonc
    disabled_domains = _get_disabled_domains()
    if domain_key in disabled_domains:
        return {'matched': False, 'domain_key': domain_key, 'error': f'Domain is disabled: {domain_key}', 'domain': _get_domain(url)}

    adapter_map = all_config.get('_adapter_map', {})
    adapter = adapter_map.get(domain_key)
    raw_config = all_config.get('_raw_domain_configs', {}).get(domain_key, domain_config)
    # Apply route matching to merged config
    merged_config = copy.deepcopy(domain_config)
    merged_config, route_key = _apply_route_matching(url, merged_config)
    # 展示前合并 builtin 系统基线，口径与 resolver.get_*_config 的
    # deep_merge(builtin, domain) 一致；否则 show-config 只显示用户/域配置，
    # 用户看不到从 builtin 继承的默认键。
    builtin = load_builtin_config(base_dir) or {}
    if builtin:
        merged_config = deep_merge(builtin, merged_config)
    result = {
        'url': url,
        'domain': _get_domain(url),
        'domain_key': domain_key,
        'matched': True,
        'adapter': adapter,
        'route_key': route_key,
        'raw_config': raw_config,
        'merged': merged_config,
    }
    return result


# Path to the source defaults adapter template (bundled in the Python package).
# Used only by bootstrap.ensure_defaults_adapter at startup to seed / update the runtime copy.
_DEFAULTS_SOURCE_DIR = os.path.join(os.path.dirname(__file__), 'adapters', 'defaults')
_DEFAULTS_SOURCE_FILE = os.path.join(_DEFAULTS_SOURCE_DIR, 'adapters.jsonc')


def _read_builtin_source() -> dict:
    """Read the _builtin block from the source template file.

    Only used during startup sync; runtime reads go through the data/ copy.
    """
    try:
        data = load_jsonc_file(_DEFAULTS_SOURCE_FILE)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error('Failed to read builtin source file: %s: %s', _DEFAULTS_SOURCE_FILE, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    builtin = data.get('_builtin', {})
    return builtin if isinstance(builtin, dict) else {}


def _read_runtime_defaults(base_dir: str) -> dict | None:
    """Read the runtime defaults adapter file from data/."""
    runtime_path = os.path.join(base_dir, 'adapters', 'defaults', 'adapters.jsonc')
    if not os.path.exists(runtime_path):
        return None
    try:
        data = load_jsonc_file(runtime_path)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# load_builtin_config cache
# ---------------------------------------------------------------------------

# runtime defaults 适配器路径 -> ((mtime_ns, size), merged_builtin_or_None)
# 该函数在每个域名请求的 metadata/snapshot/reader/health 解析路径上都会被调用，
# 此前每次都重读磁盘文件；此处加模块级缓存，文件 mtime/size 变化时自动失效重载。
_builtin_config_cache: dict[str, tuple] = {}


def _runtime_defaults_signature(runtime_path: str) -> tuple:
    try:
        st = os.stat(runtime_path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def load_builtin_config(base_dir: str) -> dict | None:
    """Return the merged system-wide baseline config.

    Reads _builtin and _builtin_overrides from the runtime defaults adapter
    (data/site_adapters/adapters/defaults/adapters.jsonc).  The _builtin
    block in that file is kept in sync with the source template at startup
    via AppConfig.ready().

    Results are cached per runtime file path; the cache is invalidated when the
    file's mtime or size changes. The returned dict is a deep copy of the
    cached merged baseline, so callers may mutate it freely.
    """
    ensure_defaults_adapter(os.path.join(base_dir, 'adapters'), sync=False)
    runtime_path = os.path.join(base_dir, 'adapters', 'defaults', 'adapters.jsonc')
    sig = _runtime_defaults_signature(runtime_path)
    cached = _builtin_config_cache.get(runtime_path)
    if cached is not None and cached[0] == sig:
        cached_value = cached[1]
        return copy.deepcopy(cached_value) if cached_value is not None else None
    runtime = _read_runtime_defaults(base_dir)
    if not runtime:
        _builtin_config_cache[runtime_path] = (sig, None)
        return None
    builtin = runtime.get('_builtin', {})
    if not isinstance(builtin, dict) or not builtin:
        _builtin_config_cache[runtime_path] = (sig, None)
        return None
    overrides = runtime.get('_builtin_overrides', {})
    if isinstance(overrides, dict) and overrides:
        result = deep_merge(copy.deepcopy(builtin), overrides)
    else:
        result = copy.deepcopy(builtin)
    _builtin_config_cache[runtime_path] = (sig, result)
    return copy.deepcopy(result)
