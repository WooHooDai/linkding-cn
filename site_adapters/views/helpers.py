"""
Shared helpers for site adapter views.
"""
import json
import logging
import os
import re
import threading
from functools import wraps

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from bookmarks.utils import atomic_write
from site_adapters.services.config import (
    parse_jsonc,
    load_jsonc_file,
)
from site_adapters.services.config.jsonc import (
    update_key as _replace_top_level_jsonc_value,
)
from site_adapters.services.config.validator import (
    get_defuddle_params_set,
    get_singlefile_args_set,
)
from site_adapters.services.config.bootstrap import (
    _get_defaults_source_path as _service_get_defaults_source_path,
    ensure_base_dirs as _service_ensure_base_dirs,
    ensure_defaults_adapter as _service_ensure_defaults_adapter,
)

logger = logging.getLogger(__name__)

TEST_ASSETS_DIR = os.path.join(os.path.dirname(django_settings.LD_ASSET_FOLDER), 'site_adapters', 'test_assets')
from site_adapters.services.base import _get_adapters_dir, _get_base_dir


def _ensure_base_dirs():
    adapters_dir = _service_ensure_base_dirs()
    _cleanup_stale_adapters(adapters_dir)


def _cleanup_stale_adapters(adapters_dir: str):
    """清理磁盘上已被删除的适配器条目。

    - 本地适配器：源文件不存在则从 config.jsonc 删除条目
    - 远程适配器：缓存文件不存在则保留条目（可重新下载）
    - defaults：文件不存在则由 _ensure_defaults_adapter 重建，此处不处理
    """
    from site_adapters.services.subscriptions import is_remote_source, resolve_adapter_path
    config_path = os.path.join(adapters_dir, 'config.jsonc')
    if not os.path.exists(config_path):
        return
    try:
        data, text = _load_config()
    except Exception:
        return
    adapters = data.get('_adapters', [])
    if not isinstance(adapters, list):
        return
    cleaned = []
    changed = False
    for item in adapters:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        source = item.get('source', '')
        if not source:
            continue
        if is_remote_source(source):
            # 远程适配器：始终保留
            cleaned.append(item)
            continue
        # 本地适配器：检查文件是否存在
        try:
            file_path = resolve_adapter_path(item.get('name', ''), source, adapters_dir)
            if os.path.exists(file_path):
                cleaned.append(item)
            else:
                logger.info('Removing stale local adapter: %s (%s)', item.get('name', ''), source)
                changed = True
        except Exception:
            cleaned.append(item)
    if changed:
        _save_adapters_list(cleaned)

def _get_defaults_source_path() -> str:
    """Return the path to the source defaults adapter template."""
    return _service_get_defaults_source_path()


def _ensure_defaults_adapter(adapters_dir: str, sync: bool = True):
    """Ensure the runtime defaults adapter exists and is synced with the source template."""
    return _service_ensure_defaults_adapter(
        adapters_dir,
        source_path=_get_defaults_source_path(),
        sync=sync,
    )


def site_adapters_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_superuser):
            raise PermissionDenied()
        return view_func(request, *args, **kwargs)

    return login_required(wrapped)


def _invalidate_site_adapters_cache():
    from site_adapters.services.config.loader import _cache
    _cache.invalidate()


def _is_safe_subscription_name(name: str) -> bool:
    if not name:
        return True
    if name.startswith('.'):
        return False
    if '/' in name or '\\' in name or '..' in name:
        return False
    return bool(re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name))








# ---------------------------------------------------------------------------
# Config (config.jsonc) management
# ---------------------------------------------------------------------------

# 进程内全局锁：config.jsonc 是"读整文件→内存改→整文件覆写"，atomic_write 只防
# 写一半，不防并发 POST 之间的丢更新。add/save/reorder/enable/disable 与
# local_domain_toggle 并发时用此锁串行化整个读-改-写临界区。
_config_lock = threading.RLock()


def _config_path() -> str:
    return os.path.join(_get_adapters_dir(), 'config.jsonc')


def _load_config() -> tuple[dict, str]:
    path = _config_path()
    if not os.path.exists(path):
        return {}, ''
    with open(path, encoding='utf-8') as f:
        text = f.read()
    if not text.strip():
        return {}, text
    data = parse_jsonc(text)
    if not isinstance(data, dict):
        raise ValueError('config.jsonc must be an object')
    return data, text


def _save_adapters_list(adapters: list[dict]):
    with _config_lock:
        path = _config_path()
        text = ''
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                text = f.read()
        new_text = _replace_top_level_jsonc_value(text, '_adapters', adapters)
        atomic_write(path, new_text)
        _invalidate_site_adapters_cache()






def _get_adapters_list() -> list:
    # 自愈入口：创建运行时目录并清理磁盘上已被删除的本地适配器条目。
    # 管理页加载（subscription_manage / all_domains）与后台订阅同步都会经过此函数，
    # 故在此接入，无需额外启动钩子。
    _ensure_base_dirs()
    # Self-heal the defaults adapter before exposing or operating on the list.
    _ensure_defaults_adapter(_get_adapters_dir(), sync=False)
    data, _ = _load_config()
    adapters = data.get("_adapters", [])
    if not isinstance(adapters, list):
        return []
    # 去重：id+name 均相同时视为重复，保留最先出现的
    seen_keys = set()
    deduped = []
    for item in adapters:
        if not isinstance(item, dict):
            deduped.append(item)
            continue
        key = (item.get('id', ''), item.get('name', ''))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(item)
    return deduped


def _get_disabled_domains() -> set[str]:
    """Read _disabled_domains from config.jsonc. Returns a set of domain keys."""
    data, _ = _load_config()
    disabled = data.get('_disabled_domains', [])
    if not isinstance(disabled, list):
        return set()
    return set(disabled)


def _toggle_domain_disabled(domain_key: str, disabled: bool):
    """Add or remove domain_key from _disabled_domains in config.jsonc."""
    with _config_lock:
        current = _get_disabled_domains()
        if disabled:
            current.add(domain_key)
        else:
            current.discard(domain_key)
        path = _config_path()
        text = ''
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                text = f.read()
        new_text = _replace_top_level_jsonc_value(text, '_disabled_domains', sorted(current))
        atomic_write(path, new_text)
        _invalidate_site_adapters_cache()



# Test helpers
def _sanitize_url_for_filename(url: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in url)[:120]


def _extract_cleanup_stats(html_path: str) -> dict:
    try:
        with open(html_path, encoding='utf-8') as f:
            head = f.read(20000)
    except OSError:
        return {}
    marker = 'linkding-cleanup-stats'
    idx = head.find(marker)
    if idx < 0:
        return {}
    for quote in ['"', "'"]:
        content_marker = f'content={quote}'
        content_idx = head.find(content_marker, idx)
        if content_idx >= 0:
            start = content_idx + len(content_marker)
            end = head.find(quote, start)
            if end >= 0:
                try:
                    return json.loads(head[start:end].replace('&quot;', '"'))
                except json.JSONDecodeError:
                    pass
    return {}


# Schema helpers
def _adapter_from_post(request) -> dict:
    source = request.POST.get('source', '').strip()
    name = request.POST.get('name', '').strip()
    adapter_id = request.POST.get('id', '').strip()
    interval_raw = request.POST.get('update_interval', '').strip()

    display_name = request.POST.get('display_name', '').strip()

    from site_adapters.services.subscriptions import is_remote_source, validate_subscription_url

    # id 和 name 优先从请求获取，其次从本地源文件 _meta 自动检测
    # 远程源允许缺失（由调用方在下载后补全）
    if not adapter_id or not name:
        if not is_remote_source(source):
            from site_adapters.services.subscriptions import resolve_adapter_path, _read_subscription_file
            try:
                file_path = resolve_adapter_path(name or '', source)
                data = _read_subscription_file(file_path)
                if data and isinstance(data.get('_meta'), dict):
                    meta = data['_meta']
                    if not adapter_id and meta.get('id'):
                        adapter_id = meta['id']
                    if not name and meta.get('name'):
                        name = meta['name']
            except Exception:
                pass
        if not adapter_id and not is_remote_source(source):
            raise ValueError('id is required')
        if not name and not is_remote_source(source):
            raise ValueError('name is required')
    if not source:
        raise ValueError('source is required')

    if is_remote_source(source):
        validate_subscription_url(source)

    if not _is_safe_subscription_name(adapter_id):
        raise ValueError('invalid adapter id')
    if not _is_safe_subscription_name(name):
        raise ValueError('invalid adapter name')

    if not is_remote_source(source):
        from site_adapters.services.subscriptions import resolve_adapter_path
        full = resolve_adapter_path(name, source)
        if not os.path.exists(full):
            raise ValueError(f'local adapter file not found: {source} (resolved: {full})')

    try:
        update_interval = int(interval_raw or 86400)
    except ValueError as exc:
        raise ValueError('update_interval must be an integer') from exc
    if update_interval <= 0:
        raise ValueError('update_interval must be positive')

    item = {'name': name, 'update_interval': update_interval}
    item['id'] = adapter_id
    item['source'] = source
    item['display_name'] = display_name
    return item


def _adapter_cache_info(adapter: dict) -> dict:
    if not isinstance(adapter, dict):
        return {'cached': False, 'domain_count': 0}

    from site_adapters.services.subscriptions import (
        _get_meta_entry,
        _read_subscription_file,
        is_remote_source,
        list_cached_domains_from_file,
        resolve_adapter_path,
    )
    from site_adapters.services.base import _adapter_dir
    from site_adapters.services.base import _get_adapters_dir
    
    name = adapter.get('name', '')
    source = adapter.get('source', '')
    dir_name = _adapter_dir(adapter)
    adapters_root = _get_adapters_dir()
    # 远程缓存目录是 {id}.{name}（与写入路径一致），读取时必须带上 id
    file_path = resolve_adapter_path(name, source, adapters_root, adapter.get('id', '')) if name else os.path.join(adapters_root, dir_name, 'adapters.jsonc')
    cached = os.path.exists(file_path)
    cached_domains = list_cached_domains_from_file(file_path) if cached else []
    is_remote = is_remote_source(source)
    info = {
        'name': name,
        'cached': cached,
        'cache_missing': is_remote and not cached,
        'domain_count': len(cached_domains),
        'domains': cached_domains,
    }
    # 从 _meta.json 读取运行时状态（key 需规范化为目录 URL）
    remote_runtime_version = None
    if is_remote:
        from site_adapters.services.subscriptions import _normalize_source_to_directory
        meta_key = _normalize_source_to_directory(source)
        meta_entry = _get_meta_entry(meta_key)
        if meta_entry:
            info.update({
                'last_fetch': meta_entry.get('last_fetch'),
                'fetch_status': meta_entry.get('fetch_status', 'ok'),
            })
            if meta_entry.get('version') is not None:
                remote_runtime_version = meta_entry['version']
    # 从缓存的 adapters.jsonc 读取发布者元信息
    if cached:
        try:
            sub_data = _read_subscription_file(file_path)
            if sub_data and isinstance(sub_data.get('_meta'), dict):
                meta = sub_data['_meta']
                info.update({
                    'version': meta.get('version', ''),
                    'changelog': meta.get('changelog', ''),
                    'source_name': meta.get('name', ''),
                    'description': meta.get('description', ''),
                })
        except (json.JSONDecodeError, OSError):
            pass
    # 远程订阅的 version 以运行时状态为准（版本预检/304 同步会更新它）
    if remote_runtime_version is not None:
        info['version'] = remote_runtime_version
    return info


def _adapter_payload(index: int, adapter) -> dict:
    item = dict(adapter) if isinstance(adapter, dict) else {'name': str(adapter)}
    item.setdefault('id', '')
    item.setdefault('source', '')
    item.setdefault('name', '')
    item.setdefault('update_interval', 86400)
    item.setdefault('enabled', True)
    item.setdefault('description', '')
    item['index'] = index
    from site_adapters.services.base import _adapter_dir
    item['dir'] = _adapter_dir(item)
    info = _adapter_cache_info(item)
    item.update(info)
    return item


def _adapters_response() -> JsonResponse:
    adapters = _get_adapters_list()
    visible = [a for a in adapters if isinstance(a, dict)]
    payload = [_adapter_payload(i, a) for i, a in enumerate(visible)]
    return JsonResponse({'adapters': payload})


def _adapter_index(request, adapters: list) -> int:
    try:
        index = int(request.POST.get('index', ''))
    except ValueError as exc:
        raise ValueError('invalid adapter index') from exc
    if index < 0 or index >= len(adapters):
        raise ValueError('invalid adapter index')
    return index


def _adapter_cache_name(adapter: dict) -> str:
    from site_adapters.services.subscriptions import _sub_name
    source = adapter.get('source', '') if isinstance(adapter, dict) else ''
    name = adapter.get('name', '') if isinstance(adapter, dict) else ''
    return _sub_name(source or name, name) if isinstance(adapter, dict) else ''


def _adapter_cache_dir(adapter: dict) -> str:
    from site_adapters.services.base import _adapter_dir, _get_adapters_dir
    if not isinstance(adapter, dict):
        return ''
    dir_name = _adapter_dir(adapter)
    return os.path.join(_get_adapters_dir(), dir_name)


def _has_adapter_conflict(adapters: list, item: dict, ignore_index: int | None = None) -> bool:
    """检查适配器是否与已有列表冲突。

    仅按 id+name 组合判断重复，不检查 source 是否相同。
    """
    new_id = item.get('id', '')
    new_name = item.get('name', '')
    for i, a in enumerate(adapters):
        if i == ignore_index or not isinstance(a, dict):
            continue
        # id+name 均相等时视为冲突
        if a.get('id') == new_id and a.get('name') == new_name:
            return True
    return False




def build_domain_files_meta() -> list[dict]:
    """Lightweight variant: returns domain list WITHOUT config data.
    Includes 'sections' key listing config top-level keys for tag display."""
    full = build_domain_files()
    result = []
    for d in full:
        item = {k: v for k, v in d.items() if k != 'config'}
        config = d.get('config') or {}
        item['sections'] = sorted(k for k in config.keys() if isinstance(config.get(k), dict))
        result.append(item)
    return result

def build_domain_files() -> list[dict]:
    """Build the full domain list from all enabled adapters, including config,
    disabled state, and shadow info. Reusable by page view and API."""
    from site_adapters.services.auth.credentials import get_shared_cookie
    from site_adapters.services.config import load_jsonc_file
    from site_adapters.services.subscriptions import resolve_adapter_path
    import os

    adapters_list = _get_adapters_list()
    adapters_dir = _get_adapters_dir()
    disabled_domains = _get_disabled_domains()
    domain_files = []
    seen_domains: dict[str, str] = {}

    for adapter in adapters_list:
        if not isinstance(adapter, dict):
            continue
        if adapter.get('enabled') is False:
            continue
        name = adapter.get('name', '')
        source = adapter.get('source', '')
        file_path = resolve_adapter_path(name, source, adapters_dir, adapter_id=adapter.get('id', ''))

        if not file_path or not os.path.exists(file_path):
            continue

        try:
            data = load_jsonc_file(file_path)
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(data, dict):
            continue

        domains = data.get('domains', {})
        if not isinstance(domains, dict):
            domains = {}

        for domain_key, domain_config in domains.items():
            is_alias = isinstance(domain_config, dict) and domain_config.get('type') == 'alias'
            target = domain_config.get('target', '') if is_alias else ''
            # Check for cookies across all scopes (domain + section-level)
            has_cookie = False
            shared_domain, _ = get_shared_cookie(hostname=domain_key)
            if shared_domain:
                has_cookie = True
            if not has_cookie:
                for sec in ('metadata', 'snapshot', 'reader'):
                    shared_sec, _ = get_shared_cookie(hostname=domain_key, scope=sec)
                    if shared_sec:
                        has_cookie = True
                        break
            # Determine which sections need cookies
            section_cookie_needs = {}
            if not is_alias and isinstance(domain_config, dict):
                domain_auth = domain_config.get('auth', {})
                if isinstance(domain_auth, dict) and domain_auth.get('cookie'):
                    section_cookie_needs[''] = True
                for sec in ('metadata', 'snapshot', 'reader'):
                    sec_data = domain_config.get(sec, {})
                    if isinstance(sec_data, dict):
                        sec_auth = sec_data.get('auth', {})
                        if isinstance(sec_auth, dict) and sec_auth.get('cookie'):
                            section_cookie_needs[sec] = True
            requires_cookie = bool(section_cookie_needs)
            disabled = domain_key in disabled_domains

            if domain_key in seen_domains:
                domain_files.append({
                    'domain_key': domain_key,
                    'adapter': name,
                    'is_alias': is_alias,
                    'target': target,
                    'has_cookie': has_cookie,
                    'requires_cookie': requires_cookie,
                    'section_cookie_needs': section_cookie_needs,
                    'disabled': disabled,
                    'shadowed': True,
                    'shadowed_by': seen_domains[domain_key],
                    'config': domain_config,
                })
                continue

            seen_domains[domain_key] = name
            domain_files.append({
                'domain_key': domain_key,
                'adapter': name,
                'is_alias': is_alias,
                'target': target,
                'has_cookie': has_cookie,
                'requires_cookie': requires_cookie,
                'section_cookie_needs': section_cookie_needs,
                'disabled': disabled,
                'shadowed': False,
                'shadowed_by': '',
                'config': domain_config,
            })

    return domain_files


def _adapter_dir(adapter: dict) -> str:
    """Get the directory name for an adapter."""
    from site_adapters.services.base import _adapter_dir as _base_adapter_dir
    return _base_adapter_dir(adapter)
