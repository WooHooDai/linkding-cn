"""
订阅机制

将远程订阅源目录镜像到本地：下载 adapters.jsonc 和 scripts/ 中的所有引用脚本。
- 远程 HTTPS 目录 → 下载并缓存
- 本地目录 → 直接读取（不缓存副本）
- _includes 递归展开
- 条件请求（ETag / Last-Modified）
- 运行时状态存储于 _meta.json（不污染缓存的 adapters.jsonc）
"""

import hashlib
import ipaddress
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests

from bookmarks.utils import atomic_write
from site_adapters.services.base import _get_adapters_dir, _get_base_dir
from site_adapters.services.config import deep_merge, parse_jsonc

logger = logging.getLogger(__name__)

_ADAPTER_FILE = 'adapters.jsonc'
_OLD_SUB_FILE = 'subscription.jsonc'

_last_fetch_cache: dict[tuple[str, str], tuple[float, float]] = {}

_SCRIPT_DOWNLOAD_WORKERS = 8
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_BACKOFF = 0.5


# ---------------------------------------------------------------------------
# _meta.json — 订阅源运行时状态
# ---------------------------------------------------------------------------

def _get_meta_path() -> str:
    return os.path.join(_get_adapters_dir(), '_meta.json')


def _load_meta() -> dict:
    path = _get_meta_path()
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_meta(meta: dict):
    atomic_write(_get_meta_path(), json.dumps(meta, indent=2, ensure_ascii=False))


def _get_meta_entry(url: str) -> dict:
    """获取指定订阅源的运行时状态条目。"""
    meta = _load_meta()
    entry = meta.get(url)
    if isinstance(entry, dict):
        return entry
    return {}


def _update_meta_entry(url: str, **fields):
    """更新指定订阅源的运行时状态。"""
    meta = _load_meta()
    entry = meta.get(url, {})
    if not isinstance(entry, dict):
        entry = {}
    entry.update(fields)
    meta[url] = entry
    _save_meta(meta)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _get_adapters_dir_path() -> str:
    return _get_adapters_dir()


def _url_to_name(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _safe_name(name: str) -> str:
    if not name or not name.strip():
        return ''
    if '/' in name or '\\' in name or '..' in name:
        return ''
    if name.startswith('.'):
        return ''
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', name):
        return ''
    return name


def _sub_name(url: str, name: str = '') -> str:
    return _safe_name(name) or _url_to_name(url)


def _is_safe_script_key(key: str) -> bool:
    """验证脚本存储键是否安全（允许子目录，拒绝 .. 和隐藏文件）。"""
    if not key or key.startswith('/') or key.endswith('/') or '//' in key:
        return False
    parts = key.split('/')
    for part in parts:
        if not part or part == '..' or part.startswith('.'):
            return False
    return True


def _resolve_script_ref(script_ref: str, base_url: str) -> tuple[str | None, str | None]:
    """解析脚本引用，返回 (下载 URL, 本地存储键)。

    订阅源视为自包含目录，所有脚本路径相对于 base_url 解析。
    不支持外部 HTTPS URL —— 发布者应将脚本放入 scripts/ 目录。
    """
    if script_ref.startswith('https://') or script_ref.startswith('http://'):
        logger.warning('External script URLs not supported in subscriptions: %s', script_ref)
        return None, None

    # 计算本地存储键和对应的下载 URL
    if script_ref.startswith('./'):
        local_key = script_ref[2:]  # 去掉 ./
        # 去掉 scripts/ 前缀，因为本地存储时 scripts_dir 已包含该层
        if local_key.startswith('scripts/'):
            local_key = local_key[len('scripts/'):]
        download_url = urljoin(base_url, script_ref)
    elif script_ref.startswith('../'):
        download_url = urljoin(base_url, script_ref)
        parts = urlparse(download_url).path.strip('/').split('/')
        if 'scripts' in parts:
            idx = parts.index('scripts')
            local_key = '/'.join(parts[idx + 1:])
        else:
            local_key = parts[-1] if parts else ''
    else:
        # 纯文件名或目录前缀名：远端在 scripts/ 下
        local_key = script_ref
        download_url = urljoin(base_url, 'scripts/' + script_ref)

    return download_url, local_key


def _validate_https_url(url: str, resolve_dns: bool = False):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URL must be HTTPS with a hostname: %s" % url)
    hostname = parsed.hostname

    def _check_ip(addr_str: str):
        try:
            addr = ipaddress.ip_address(addr_str)
        except ValueError:
            return
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ValueError("URL cannot target private/loopback: %s" % addr_str)

    _check_ip(hostname)
    if hostname and hostname[0].isdigit():
        _check_ip(hostname)

    if resolve_dns:
        import socket
        try:
            infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
            for family, _, _, _, sockaddr in infos:
                resolved = ipaddress.ip_address(sockaddr[0])
                if resolved.is_private or resolved.is_loopback or resolved.is_link_local:
                    raise ValueError(
                        "URL resolves to private/loopback address: %s -> %s" % (hostname, resolved)
                    )
        except socket.gaierror as exc:
            raise ValueError("Cannot resolve hostname: %s: %s" % (hostname, exc)) from exc

    return parsed


def validate_subscription_url(url: str):
    return _validate_https_url(url)


def _validate_download_url(url: str):
    return _validate_https_url(url)


def is_remote_source(source: str) -> bool:
    """判断 source 是否为远程 URL。"""
    if not source:
        return False
    return source.startswith('https://') or source.startswith('http://')


def _normalize_source_to_directory(source: str) -> str:
    """将 source 规范化为目录路径（去掉末尾的 adapters.jsonc 文件名）。

    远程 URL 统一保证末尾斜杠，确保 urljoin 解析相对路径时不会丢失末段。
    """
    if source.endswith('/' + _ADAPTER_FILE):
        source = source[:-len('/' + _ADAPTER_FILE)]
    elif source.endswith(_ADAPTER_FILE):
        source = source[:-len(_ADAPTER_FILE)]
    # 远程 URL 统一补末尾斜杠
    if is_remote_source(source) and not source.endswith('/'):
        source = source + '/'
    return source


def _normalize_source_to_file(source: str) -> str:
    """将 source 规范化为可下载的 adapters.jsonc 文件 URL/路径。

    目录形式的 source 会自动拼上 adapters.jsonc；已带文件名的原样返回。
    与 fetch_subscription 的下载路径补全逻辑保持一致。
    """
    directory = _normalize_source_to_directory(source)
    if is_remote_source(source):
        if source.endswith('.jsonc'):
            return source
        return directory.rstrip('/') + '/' + _ADAPTER_FILE
    return os.path.join(directory, _ADAPTER_FILE)


def resolve_adapter_path(name: str, source: str, adapters_dir: str | None = None,
                           adapter_id: str = '') -> str:
    """解析适配器文件路径。

    source 是订阅源目录（包含 adapters.jsonc）的路径，兼容旧格式（包含文件名）。
    - HTTPS URL → adapters/<id>.<name>/adapters.jsonc（本地缓存路径）
    - 本地路径 → <source>/adapters.jsonc
    """
    if adapters_dir is None:
        adapters_dir = _get_adapters_dir_path()

    source = _normalize_source_to_directory(source)

    if is_remote_source(source):
        from site_adapters.services.base import _adapter_dir as _base_adapter_dir
        dir_name = _base_adapter_dir({'id': adapter_id, 'name': name})
        return os.path.join(adapters_dir, dir_name, _ADAPTER_FILE)
    # 本地目录路径：拼接 adapters.jsonc
    if os.path.isabs(source):
        return os.path.join(source, _ADAPTER_FILE)
    return os.path.normpath(os.path.join(adapters_dir, source, _ADAPTER_FILE))


def _adapter_dir(entry: dict) -> str:
    """从适配器条目计算目录名。"""
    from site_adapters.services.base import _adapter_dir as _base_adapter_dir
    return _base_adapter_dir(entry)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int, headers: dict | None = None):
    """带重试的 GET，仅对网络异常与 5xx/429 重试。

    304 与 4xx 等确定性结果直接返回，重试不会改变结果。
    重试耗尽后抛出最后一个异常，由调用方降级处理。
    """
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = requests.HTTPError(
                    f"HTTP {resp.status_code}: {url}", response=resp
                )
            else:
                return resp
        if attempt < _HTTP_RETRY_ATTEMPTS - 1:
            time.sleep(_HTTP_RETRY_BACKOFF * (2 ** attempt))
    if last_exc is not None:
        raise last_exc
    raise requests.RequestException(f"request failed: {url}")


def _download_jsonc(url: str, etag: str = '', last_modified: str = '') -> tuple[dict | None, dict]:
    headers = {}
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified

    resp = _http_get(url, timeout=30, headers=headers)
    if resp.status_code == 304:
        logger.info("Subscription not modified (304): %s", url)
        return None, {}

    resp.raise_for_status()
    _MAX_SUBSCRIPTION_SIZE = 10 * 1024 * 1024
    content_length = resp.headers.get('Content-Length')
    if content_length:
        try:
            if int(content_length) > _MAX_SUBSCRIPTION_SIZE:
                raise ValueError("Subscription too large (%s bytes, max %d)" % (content_length, _MAX_SUBSCRIPTION_SIZE))
        except (ValueError, TypeError):
            pass
    content = resp.text
    if len(content) > _MAX_SUBSCRIPTION_SIZE:
        raise ValueError("Subscription too large (%d bytes, max %d)" % (len(content), _MAX_SUBSCRIPTION_SIZE))

    data = parse_jsonc(content)
    if not isinstance(data, dict):
        raise ValueError("订阅顶层必须是对象")

    response_meta = {}
    if 'ETag' in resp.headers:
        response_meta['etag'] = resp.headers['ETag']
    if 'Last-Modified' in resp.headers:
        response_meta['last_modified'] = resp.headers['Last-Modified']

    return data, response_meta


def _domain_map(data: dict) -> dict:
    if isinstance(data.get('domains'), dict):
        return data['domains']
    return {
        key: value for key, value in data.items()
        if key not in ('defaults', '_builtin', 'domains') and not key.startswith('_')
    }


def _normalize_domain_config(value):
    if isinstance(value, str):
        return {"type": "alias", "target": value}
    return value


def _materialize_domains(data: dict) -> dict:
    defaults = data.get('defaults', {})
    domains = {}
    for domain_key, config in _domain_map(data).items():
        config = _normalize_domain_config(config)
        if isinstance(config, dict) and config.get('type') != 'alias' and defaults:
            config = deep_merge(defaults, config)
        domains[domain_key] = config
    return domains


_MAX_INCLUDES_DEPTH = 10

def _resolve_includes(url: str, data: dict, seen: set[str], _depth: int = 0) -> dict:
    if url in seen:
        raise ValueError("Subscription _includes cycle: %s" % url)
    if _depth >= _MAX_INCLUDES_DEPTH:
        raise ValueError("Subscription _includes too deep (max %d): %s" % (_MAX_INCLUDES_DEPTH, url))
    seen.add(url)

    includes = data.get('_includes', [])
    if isinstance(includes, str):
        includes = [includes]

    merged_domains = {}

    for include_url in reversed(includes or []):
        include_url = urljoin(url, include_url)
        try:
            validate_subscription_url(include_url)
        except ValueError as exc:
            logger.warning('Skipping unsafe include URL: %s: %s', include_url, exc)
            continue
        include_data, _ = _download_jsonc(include_url)
        if include_data is None:
            continue
        include_data = _resolve_includes(include_url, include_data, seen, _depth + 1)
        merged_domains.update(_materialize_domains(include_data))

    merged_domains.update(_domain_map(data))

    result = dict(data)
    result['domains'] = merged_domains
    result.pop('_includes', None)
    seen.remove(url)
    return result


# ---------------------------------------------------------------------------
# 脚本收集与缓存
# ---------------------------------------------------------------------------

def _iter_script_paths(node):
    """递归收集任意层级 scripts 数组中的 path 字段。

    运行时 _resolve_all_paths 会将任意层级（metadata / snapshot / reader /
    defaults / routes）中的 scripts[].path 解析为适配器目录下的路径，
    因此镜像时必须扫描同样的范围，否则部分脚本不会被缓存。
    """
    if isinstance(node, dict):
        scripts = node.get('scripts')
        if isinstance(scripts, list):
            for entry in scripts:
                if isinstance(entry, dict):
                    path = entry.get('path', '')
                    if isinstance(path, str) and path:
                        yield path
        for key, value in node.items():
            if key == 'scripts':
                continue
            yield from _iter_script_paths(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_script_paths(item)


def _unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _collect_script_refs(data: dict) -> dict[str, list[str]]:
    """收集所有域的脚本引用，按域名分组。

    只扫描 scripts 数组中的 path 字段（不兼容旧的 script 标量）。
    递归覆盖域名级 defaults / routes 等所有嵌套层级。
    """
    refs: dict[str, list[str]] = {}
    adapter_defaults = data.get('defaults')
    if isinstance(adapter_defaults, dict):
        default_refs = _unique(_iter_script_paths(adapter_defaults))
        if default_refs:
            refs['_defaults'] = default_refs
    for domain_key, domain_config in _domain_map(data).items():
        if not isinstance(domain_config, dict):
            continue
        paths = _unique(_iter_script_paths(domain_config))
        if paths:
            refs[domain_key] = paths
    return refs


def _content_fingerprint(data: dict) -> str:
    """对解析后的规范化数据计算稳定指纹，用于跨次下载的变化检测。

    与 _write_adapter_file 的序列化方式一致（sort_keys + indent=2），
    因此指纹同时可用于本地缓存字节比对。
    """
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _all_script_refs(data: dict) -> list[str]:
    """展平所有脚本引用（保持首次出现的顺序）。"""
    refs: list[str] = []
    for domain_refs in _collect_script_refs(data).values():
        refs.extend(domain_refs)
    return refs


def _local_script_keys(data: dict, base_url: str = '') -> list[str]:
    """将脚本引用解析为本地存储键（去重、过滤不安全键）。"""
    keys: list[str] = []
    seen: set[str] = set()
    for script_ref in _all_script_refs(data):
        _download_url, local_key = _resolve_script_ref(
            script_ref, base_url or 'https://invalid.local/'
        )
        if not local_key or not _is_safe_script_key(local_key) or local_key in seen:
            continue
        seen.add(local_key)
        keys.append(local_key)
    return keys


def _missing_scripts(file_path: str, base_url: str = '') -> list[str]:
    """检查本地缓存中缺失或为空的脚本文件（纯本地检查，不发起网络请求）。

    是订阅缓存自愈的基础：即使 adapters.jsonc 未变化、服务端返回 304，
    只要脚本缓存不完整，更新链路也会强制重新下载缺失的脚本。
    """
    data = _read_subscription_file(file_path)
    if not data:
        return []
    scripts_dir = os.path.join(os.path.dirname(file_path), 'scripts')
    missing = []
    for local_key in _local_script_keys(data, base_url):
        target_path = os.path.join(scripts_dir, local_key)
        if not os.path.isfile(target_path) or os.path.getsize(target_path) == 0:
            missing.append(local_key)
    return missing


def _write_adapter_file(file_path: str, url: str, data: dict,
                        only_keys: set[str] | None = None) -> dict:
    """将订阅镜像到本地 adapters/<adapter>/。不做任何内容改写。

    adapters.jsonc 保持远端原样（不注入 _meta，不改写脚本路径）。
    scripts/ 目录镜像远端结构。

    only_keys 非空时只下载这些本地键对应的脚本（用于修复不完整缓存），
    且跳过“清理未引用脚本”，避免误删本次未参与修复的文件。

    Returns:
        {'referenced': set[str], 'failed': list[str]}，failed 为本轮下载失败的脚本键。
    """
    sub_dir = os.path.dirname(file_path)
    os.makedirs(sub_dir, exist_ok=True)

    # 下载脚本到 scripts/ 目录（镜像远端结构）
    domain_refs = _collect_script_refs(data)
    if domain_refs:
        scripts_dir = os.path.join(sub_dir, 'scripts')
        # 收集唯一脚本引用 (local_key → download_url)，去重避免重复下载
        unique_scripts: dict[str, str] = {}
        for _domain_key, refs in domain_refs.items():
            for script_ref in refs:
                download_url, local_key = _resolve_script_ref(script_ref, url)
                if not download_url or not local_key:
                    continue
                if not _is_safe_script_key(local_key):
                    logger.warning('Unsafe script key: %s', local_key)
                    continue
                if only_keys is not None and local_key not in only_keys:
                    continue
                if local_key not in unique_scripts:
                    unique_scripts[local_key] = download_url

        # 并行下载所有唯一脚本
        def _download_one(l_key: str, dl_url: str) -> tuple[str, str | None]:
            try:
                _validate_download_url(dl_url)
                resp = _http_get(dl_url, timeout=30)
                resp.raise_for_status()
                content = resp.text
                # 有 Content-Length 且未经压缩时，校验字节数，捕获静默截断
                content_length = resp.headers.get('Content-Length')
                if content_length and not resp.headers.get('Content-Encoding'):
                    try:
                        expected = int(content_length)
                    except (TypeError, ValueError):
                        expected = None
                    if expected is not None and len(resp.content) != expected:
                        raise ValueError(
                            f'Script truncated: got {len(resp.content)} of {expected} bytes'
                        )
                return l_key, content
            except Exception as e:
                logger.warning('Failed to download script %s: %s', dl_url, e)
                return l_key, None

        downloaded: dict[str, str | None] = {}
        if unique_scripts:
            with ThreadPoolExecutor(max_workers=_SCRIPT_DOWNLOAD_WORKERS) as pool:
                futures = {pool.submit(_download_one, k, v): k for k, v in unique_scripts.items()}
                for fut in as_completed(futures):
                    l_key, content = fut.result()
                    downloaded[l_key] = content

        # 写入下载成功的脚本（哈希比对，仅在变化时写入）
        referenced_paths: set[str] = set()
        failed_paths: list[str] = []
        for local_key, content in downloaded.items():
            if content is None:
                # 下载失败：保留磁盘上已有的旧文件（若有），避免网络抖动误删可用脚本
                failed_paths.append(local_key)
                referenced_paths.add(local_key)
                continue
            target_path = os.path.join(scripts_dir, local_key)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            new_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            old_hash = ''
            if os.path.exists(target_path):
                try:
                    with open(target_path, encoding='utf-8') as f:
                        old_hash = hashlib.sha256(f.read().encode('utf-8')).hexdigest()
                except OSError:
                    pass
            if new_hash != old_hash:
                atomic_write(target_path, content)
                logger.info('Script updated: %s', local_key)
            referenced_paths.add(local_key)

        # 清理不再被引用的脚本（仅完整镜像时执行）
        if only_keys is None and os.path.isdir(scripts_dir):
            for root, dirs, files in os.walk(scripts_dir, topdown=False):
                for name in files:
                    if name.startswith('.'):
                        continue
                    abs_path = os.path.join(root, name)
                    rel_path = os.path.relpath(abs_path, scripts_dir)
                    if rel_path not in referenced_paths:
                        try:
                            os.remove(abs_path)
                            logger.info('Removed unused script: %s', rel_path)
                        except OSError:
                            pass
                for name in dirs:
                    dir_path = os.path.join(root, name)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                    except OSError:
                        pass
    else:
        failed_paths = []
        referenced_paths = set()

    # 原样写入 adapters.jsonc（不注入 _meta，不改写路径）
    # 使用与 _content_fingerprint 一致的规范化序列化，保证字节可比。
    content_str = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2)
    atomic_write(file_path, content_str)

    return {'referenced': referenced_paths, 'failed': failed_paths}


# ---------------------------------------------------------------------------
# 文件读取
# ---------------------------------------------------------------------------

def _read_subscription_file(file_path: str) -> dict | None:
    """读取适配器文件。支持 adapters.jsonc 和旧 subscription.jsonc。"""
    if not os.path.exists(file_path):
        old_path = os.path.join(os.path.dirname(file_path), _OLD_SUB_FILE)
        if os.path.exists(old_path):
            file_path = old_path
        else:
            return None
    try:
        with open(file_path, encoding='utf-8') as f:
            return parse_jsonc(f.read())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read adapter file: %s: %s", file_path, e)
        return None


def list_cached_domains_from_file(file_path: str) -> list[str]:
    data = _read_subscription_file(file_path)
    if not data or not isinstance(data.get('domains'), dict):
        return []
    return sorted(data['domains'].keys())


def _get_adapter_cache_path(name: str, adapter_id: str) -> str:
    from site_adapters.services.base import _adapter_dir as _base_adapter_dir
    dir_name = _base_adapter_dir({'id': adapter_id, 'name': name})
    return os.path.join(_get_adapters_dir_path(), dir_name, _ADAPTER_FILE)


def is_allowed_script_path(script_path: str, base_dir: str) -> bool:
    abs_path = os.path.realpath(os.path.abspath(script_path))
    abs_base = os.path.realpath(os.path.abspath(base_dir))
    try:
        return os.path.commonpath([abs_path, abs_base]) == abs_base
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 单个订阅下载
# ---------------------------------------------------------------------------

def _fetch_remote_version(check_update_url: str, adapter_id: str = '') -> str | None:
    """请求发布者提供的轻量版本接口，返回 version 字符串。

    任何失败（URL 非法、请求异常、响应格式错误、id 不匹配）都返回 None，
    由调用方降级为完整拉取，避免轻量检查成为更新链路的硬依赖。
    """
    try:
        validate_subscription_url(check_update_url)
    except ValueError as exc:
        logger.warning('Invalid checkUpdateUrl: %s: %s', check_update_url, exc)
        return None
    try:
        resp = requests.get(check_update_url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.warning('Version check failed: %s: %s', check_update_url, exc)
        return None
    if not isinstance(payload, dict) or payload.get('version') is None:
        logger.warning('Version check returned unexpected payload: %s', check_update_url)
        return None
    remote_id = payload.get('id')
    if adapter_id and remote_id is not None and str(remote_id) != str(adapter_id):
        logger.warning('Version check id mismatch: %s != %s', remote_id, adapter_id)
        return None
    return str(payload['version'])


def _repair_missing_scripts(file_path: str, base_url: str) -> list[str]:
    """仅重新下载本地缓存中缺失的脚本，返回下载失败的脚本键。

    用于“未变化”短路路径（版本未变 / 304 / 内容指纹未变）下的自愈：
    此时 adapters.jsonc 无需更新，只需补齐缺失的 scripts/ 文件。
    """
    only_keys = set(_missing_scripts(file_path, base_url))
    if not only_keys:
        return []
    data = _read_subscription_file(file_path)
    if not data:
        return []
    result = _write_adapter_file(file_path, base_url, data, only_keys=only_keys)
    failed = result.get('failed', [])
    if failed:
        logger.error('Script repair incomplete (%s): %s', base_url, ', '.join(failed))
    else:
        logger.info('Script repair completed (%s): %d script(s)', base_url, len(only_keys))
    return failed


def fetch_subscription(url: str, name: str = '', adapter_id: str = '', force: bool = False,
                       update_interval: int = 86400) -> str | None:
    """下载远程订阅源并镜像到本地。

    url 可以是目录路径（自动拼 adapters.jsonc），也可以是直接的 .jsonc 文件 URL。
    状态信息存储在 _meta.json 中，不写入缓存的 adapters.jsonc。

    更新判定顺序（从廉到贵）：
      1. update_interval 时间闸门（force 跳过）
      2. checkUpdateUrl 版本预检（发布者提供时）
      3. ETag / Last-Modified 条件请求（304 即未变）
      4. content_hash 内容指纹（服务端无验证器时兜底，内容未变则跳过脚本刷新）

    Returns:
        缓存文件路径；失败时若旧缓存存在也返回该路径；完全失败返回 None。
    """
    # 构建下载 URL、基准 URL、_meta.json key
    meta_key = _normalize_source_to_directory(url)

    base_url = meta_key
    file_url = _normalize_source_to_file(url)

    # 本地路径：直接返回
    if not is_remote_source(url):
        local_file = resolve_adapter_path(name, base_url) if name else file_url
        if os.path.exists(local_file):
            return local_file
        logger.error("Local adapter file not found: %s", local_file)
        return None

    sub_name = _sub_name(meta_key, name)
    file_path = _get_adapter_cache_path(sub_name, adapter_id or name)

    try:
        validate_subscription_url(file_url)
    except ValueError as exc:
        logger.error(str(exc))
        return None

    if update_interval == 0:
        return file_path

    # 从 _meta.json 读取运行时状态
    meta_entry = _get_meta_entry(meta_key)

    # 本地完整性检查（无网络）：脚本缺失时跳过所有“未变化”短路，进入自愈流程
    missing_scripts = _missing_scripts(file_path, base_url)
    needs_repair = bool(missing_scripts)
    # 缓存的 adapters.jsonc 本身丢失时，必须无条件重取（旧内容的 ETag/指纹都不可信）
    cache_missing = not os.path.exists(file_path)
    if needs_repair:
        logger.warning(
            "Subscription cache incomplete: %s missing %d script(s): %s",
            meta_key, len(missing_scripts), ', '.join(missing_scripts[:10]),
        )
    if cache_missing:
        logger.warning("Subscription cache file missing: %s", file_path)

    if not force and not needs_repair and not cache_missing:
        last_fetch = meta_entry.get('last_fetch')
        if last_fetch and time.time() - last_fetch < update_interval:
            return file_path

    try:
        # 版本预检：发布者在 _meta 中声明 checkUpdateUrl 时使用。
        check_update_url = meta_entry.get('checkUpdateUrl', '')
        if check_update_url and not check_update_url.startswith(('https://', 'http://')):
            check_update_url = urljoin(file_url, check_update_url)
        remote_version = None
        stored_version = meta_entry.get('version')
        if check_update_url:
            remote_version = _fetch_remote_version(check_update_url, adapter_id=adapter_id)
            if remote_version is not None:
                if (stored_version is not None and remote_version == str(stored_version)
                        and not cache_missing):
                    update_fields = {'last_fetch': time.time()}
                    if needs_repair:
                        failed = _repair_missing_scripts(file_path, meta_key)
                        update_fields['script_failures'] = failed
                        update_fields['fetch_status'] = 'partial' if failed else 'ok'
                    _update_meta_entry(meta_key, **update_fields)
                    _last_fetch_cache[(meta_key, name)] = (time.time(), update_interval)
                    logger.info("Subscription version unchanged: %s (%s)", meta_key, remote_version)
                    return file_path
                logger.info("Subscription version changed: %s -> %s", stored_version, remote_version)

        logger.info("Fetching subscription: %s", file_url)

        etag = meta_entry.get('etag', '')
        last_modified = meta_entry.get('last_modified', '')

        data, response_meta = _download_jsonc(file_url, etag=etag, last_modified=last_modified)

        if data is None and cache_missing:
            # 缓存文件已丢失，但服务端依据旧验证器返回 304：无条件重取一次
            logger.warning(
                "Cache file missing but got 304, refetching unconditionally: %s", meta_key
            )
            data, response_meta = _download_jsonc(file_url)

        if data is None:
            if cache_missing and not os.path.exists(file_path):
                logger.error(
                    "Cache missing and remote returned 304, cannot rebuild: %s", meta_key
                )
                return None
            update_fields = {'last_fetch': time.time()}
            if remote_version is not None:
                # 版本预检发现了新版本，但正文 304：同步版本号，不重下脚本。
                update_fields['version'] = remote_version
                logger.warning("Subscription 304 but version changed: %s (%s)", meta_key, remote_version)
            if needs_repair:
                # 304 只代表 adapters.jsonc 未变，脚本缓存仍需补齐
                failed = _repair_missing_scripts(file_path, meta_key)
                update_fields['script_failures'] = failed
                update_fields['fetch_status'] = 'partial' if failed else 'ok'
            _update_meta_entry(meta_key, **update_fields)
            _last_fetch_cache[(meta_key, name)] = (time.time(), update_interval)
            logger.info("Subscription unchanged: %s", meta_key)
            return file_path

        if '_includes' in data:
            data = _resolve_includes(file_url, data, set())

        update_fields = {
            'last_fetch': time.time(),
            'content_hash': _content_fingerprint(data),
        }
        if response_meta.get('etag'):
            update_fields['etag'] = response_meta['etag']
        if response_meta.get('last_modified'):
            update_fields['last_modified'] = response_meta['last_modified']

        # 持久化发布者元信息，供后续版本预检与展示使用
        meta_block = data.get('_meta')
        if isinstance(meta_block, dict):
            if meta_block.get('version') is not None:
                update_fields['version'] = meta_block['version']
            check_url = meta_block.get('checkUpdateUrl')
            if check_url and not check_url.startswith(('https://', 'http://')):
                check_url = urljoin(file_url, check_url)
            if check_url:
                update_fields['checkUpdateUrl'] = check_url
            else:
                # 发布者移除了版本接口时，清理旧的运行时值，避免继续请求过期 URL
                update_fields['checkUpdateUrl'] = ''

        if update_fields['content_hash'] != meta_entry.get('content_hash') or cache_missing:
            result = _write_adapter_file(file_path, meta_key, data)
            failed = result.get('failed', [])
            update_fields['script_failures'] = failed
            update_fields['fetch_status'] = 'partial' if failed else 'ok'
            logger.info("Subscription updated: %s", meta_key)
        else:
            logger.info("Subscription content unchanged (hash): %s", meta_key)
            if needs_repair:
                # 内容未变时不会重写缓存，但缺失脚本必须补齐
                failed = _repair_missing_scripts(file_path, meta_key)
                update_fields['script_failures'] = failed
                update_fields['fetch_status'] = 'partial' if failed else 'ok'

        _update_meta_entry(meta_key, **update_fields)

        cache_key = (meta_key, name)
        _last_fetch_cache[cache_key] = (time.time(), update_interval)

        return file_path
    except Exception as e:
        logger.error("Subscription fetch failed: %s: %s", meta_key, e)
        _update_meta_entry(meta_key, last_fetch=time.time(), fetch_status='error')
        return file_path if os.path.exists(file_path) else None

def _needs_fetch(sub: dict) -> bool:
    source = sub.get('source', '')
    if not source:
        return False
    if sub.get('enabled') is False:
        return False
    if not is_remote_source(source):
        return False

    now = time.time()
    name = sub.get('name', '')
    interval = sub.get('update_interval', 86400)
    if interval == 0:
        return False
    cache_key = (source, name)

    adapter_id = sub.get('id', '')
    sub_file = _get_adapter_cache_path(_sub_name(source, name), adapter_id or name)
    if not os.path.exists(sub_file):
        return True

    # 本地完整性优先于时间闸门：脚本缺失时立即重试，直到补齐
    if _missing_scripts(sub_file, _normalize_source_to_directory(source)):
        return True

    cached = _last_fetch_cache.get(cache_key)
    if cached is not None:
        cached_fetch, cached_interval = cached
        if cached_interval == interval and now - cached_fetch < interval:
            return False

    # 从 _meta.json 获取 last_fetch（key 需规范化为目录 URL）
    meta_key = _normalize_source_to_directory(source)
    meta_entry = _get_meta_entry(meta_key)
    last_fetch = meta_entry.get('last_fetch')
    if last_fetch is None:
        return True
    _last_fetch_cache[cache_key] = (last_fetch, interval)
    return now - last_fetch >= interval


def fetch_all_subscriptions(subscriptions: list[dict]) -> list[str]:
    if not any(_needs_fetch(sub) for sub in subscriptions if isinstance(sub, dict)):
        return []

    paths = []
    for sub in subscriptions:
        if sub.get('enabled') is False:
            continue
        source = sub.get('source')
        if not source:
            continue
        name = sub.get('name', '')
        interval = sub.get('update_interval', 86400) if isinstance(sub, dict) else 86400

        path = fetch_subscription(source, name=name, adapter_id=sub.get('id', ''), update_interval=interval)
        if path:
            paths.append(path)

    return paths
