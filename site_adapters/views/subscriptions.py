"""
Adapter management — CRUD for _adapters in config.jsonc.
"""
import fnmatch
import json
import logging
import os
import shutil
import time
from urllib.parse import urljoin, urlparse

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from site_adapters.services.config.bootstrap import is_defaults_adapter
from site_adapters.views.helpers import (
    _adapter_cache_dir,
    _adapter_cache_name,
    _adapter_from_post,
    _adapter_index,
    _adapter_payload,
    _adapters_response,
    _get_adapters_dir,
    _get_adapters_list,
    _has_adapter_conflict,
    _invalidate_site_adapters_cache,
    _is_safe_subscription_name,
    _load_config,
    _save_adapters_list,
    site_adapters_required,
)

logger = logging.getLogger(__name__)


@site_adapters_required
@require_http_methods(["GET", "POST"])
def subscription_manage(request):
    """管理适配器列表。真源是 config.jsonc 的 _adapters。"""
    try:
        adapters = _get_adapters_list()
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return JsonResponse({'error': str(e)}, status=400)

    if request.method == 'GET':
        if request.GET.get('action') == 'detect_id':
            source = request.GET.get('source', '').strip()
            if not source:
                return JsonResponse({'error': 'source required'}, status=400)
            from site_adapters.services.subscriptions import (
                is_remote_source, _read_subscription_file, resolve_adapter_path,
                _download_jsonc, _normalize_source_to_file,
            )
            if is_remote_source(source):
                # 远程源：下载并提取 _meta
                try:
                    file_url = _normalize_source_to_file(source)
                    data, _ = _download_jsonc(file_url)
                    if data and isinstance(data.get('_meta'), dict):
                        meta = data['_meta']
                        return JsonResponse({'id': meta.get('id', ''), 'name': meta.get('name', '')})
                except Exception:
                    logger.warning('detect_id remote source failed: %s', source)
                return JsonResponse({'id': '', 'name': ''})
            # 本地源：读取本地文件提取 _meta
            try:
                file_path = resolve_adapter_path('', source)
                data = _read_subscription_file(file_path)
                if data and isinstance(data.get('_meta'), dict):
                    meta = data['_meta']
                    return JsonResponse({'id': meta.get('id', ''), 'name': meta.get('name', '')})
            except Exception:
                pass
            return JsonResponse({'id': '', 'name': ''})
        return _adapters_response()

    action_name = request.POST.get('action', '')

    try:
        if action_name == 'add':
            item = _adapter_from_post(request)

            # 远程源：下载提取 _meta 获得 id/name，然后立即缓存到本地
            source = item.get('source', '')
            from site_adapters.services.subscriptions import is_remote_source
            auto_detect_data = None
            auto_detect_meta = {}
            if is_remote_source(source) and (not item.get('id', '').strip() or not item.get('name', '').strip()):
                from site_adapters.services.subscriptions import _download_jsonc, _normalize_source_to_file
                download_error = None
                try:
                    file_url = _normalize_source_to_file(source)
                    auto_detect_data, auto_detect_meta = _download_jsonc(file_url)
                    if auto_detect_data and isinstance(auto_detect_data.get('_meta'), dict):
                        meta = auto_detect_data['_meta']
                        if not item.get('id', '').strip():
                            item['id'] = meta.get('id', '')
                        if not item.get('name', '').strip():
                            item['name'] = meta.get('name', '')
                except Exception as e:
                    logger.exception('Failed to download subscription for auto-detect: %s', source)
                    download_error = str(e)

                if download_error and not item.get('id', '').strip():
                    return JsonResponse({'error': 'Unable to auto-detect adapter id/name from URL. Please enter id and name manually. ' + download_error},
                                        status=400)

            # id 和 name 均为必填
            if not item.get('id', '').strip():
                return JsonResponse({'error': 'id is required'}, status=400)
            if not item.get('name', '').strip():
                return JsonResponse({'error': 'name is required'}, status=400)

            # 不保存空的 display_name
            if not item.get('display_name', '').strip():
                item.pop('display_name', None)
            if _has_adapter_conflict(adapters, item):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            adapters.append(item)
            _save_adapters_list(adapters)

            # 远程订阅源：立即缓存下载的数据
            if is_remote_source(source) and auto_detect_data is not None:
                from site_adapters.services.subscriptions import (
                    _write_adapter_file,
                    _content_fingerprint,
                    resolve_adapter_path,
                    _normalize_source_to_directory,
                    _update_meta_entry,
                    _resolve_includes,
                )
                adapters_root = _get_adapters_dir()
                file_path = resolve_adapter_path(item.get('name', ''), source, adapters_root, item.get('id', ''))
                base_url = _normalize_source_to_directory(source)

                if '_includes' in auto_detect_data:
                    try:
                        auto_detect_data = _resolve_includes(source, auto_detect_data, set())
                    except Exception as e:
                        logger.warning('Failed to resolve _includes for new subscription: %s', e)

                try:
                    write_result = _write_adapter_file(file_path, base_url, auto_detect_data)
                    failed_scripts = write_result.get('failed', [])
                    update_fields = {
                        'last_fetch': time.time(),
                        'content_hash': _content_fingerprint(auto_detect_data),
                        'fetch_status': 'partial' if failed_scripts else 'ok',
                        'script_failures': failed_scripts,
                    }
                    if auto_detect_meta.get('etag'):
                        update_fields['etag'] = auto_detect_meta['etag']
                    if auto_detect_meta.get('last_modified'):
                        update_fields['last_modified'] = auto_detect_meta['last_modified']
                    meta_block = auto_detect_data.get('_meta')
                    if isinstance(meta_block, dict):
                        if meta_block.get('version') is not None:
                            update_fields['version'] = meta_block['version']
                        check_url = meta_block.get('checkUpdateUrl')
                        if check_url and not check_url.startswith(('https://', 'http://')):
                            from site_adapters.services.subscriptions import _normalize_source_to_file
                            check_url = urljoin(_normalize_source_to_file(source), check_url)
                        if check_url:
                            update_fields['checkUpdateUrl'] = check_url
                    _update_meta_entry(base_url, **update_fields)
                    _invalidate_site_adapters_cache()
                except Exception as e:
                    logger.warning('Failed to cache new subscription immediately: %s', e)

        elif action_name == 'save':
            index = _adapter_index(request, adapters)
            if is_defaults_adapter(adapters[index]):
                return JsonResponse({'error': 'defaults adapter cannot be edited'}, status=403)
            item = _adapter_from_post(request)
            old = adapters[index]
            # 保留未提供的 id 和 name
            if not item.get('id', '').strip():
                item['id'] = old.get('id', '')
            if not item.get('name', '').strip():
                item['name'] = old.get('name', '')
            # display_name: 保留旧值（如果未提供），允许设为空（传空字符串表示清除）
            if 'display_name' not in item and old.get('display_name'):
                item['display_name'] = old['display_name']
            if not item.get('id', '').strip():
                return JsonResponse({'error': 'id is required'}, status=400)
            if not item.get('name', '').strip():
                return JsonResponse({'error': 'name is required'}, status=400)
            if _has_adapter_conflict(adapters, item, ignore_index=index):
                return JsonResponse({'error': 'adapter already exists'}, status=409)
            # 不保存空的 display_name
            if not item.get('display_name', '').strip():
                item.pop('display_name', None)
            adapters[index] = item
            _save_adapters_list(adapters)
            # 远程订阅源：确保缓存目录存在（source 变更时可能需要新建目录）
            source = item.get('source', '')
            from site_adapters.services.subscriptions import is_remote_source
            if is_remote_source(source):
                cache_dir = _adapter_cache_dir(item)
                if cache_dir:
                    os.makedirs(cache_dir, exist_ok=True)

        elif action_name == 'delete':
            index = _adapter_index(request, adapters)
            if is_defaults_adapter(adapters[index]):
                return JsonResponse({'error': 'defaults adapter cannot be deleted'}, status=403)
            adapter = adapters.pop(index)
            _save_adapters_list(adapters)
            # 仅远程适配器删除缓存目录，本地适配器保留文件夹
            source = adapter.get('source', '') if isinstance(adapter, dict) else ''
            from site_adapters.services.subscriptions import is_remote_source
            if is_remote_source(source):
                cache_dir = _adapter_cache_dir(adapter)
                adapters_root = _get_adapters_dir()
                try:
                    if os.path.commonpath([os.path.abspath(cache_dir), os.path.abspath(adapters_root)]) == os.path.abspath(adapters_root):
                        if os.path.isdir(cache_dir):
                            shutil.rmtree(cache_dir, ignore_errors=True)
                except (ValueError, OSError):
                    pass

        elif action_name == 'reorder':
            indices = request.POST.getlist('indices')
            seen = set()
            reordered = []
            for raw in indices:
                try:
                    idx = int(raw)
                except (ValueError, TypeError):
                    continue
                if 0 <= idx < len(adapters) and idx not in seen:
                    reordered.append(adapters[idx])
                    seen.add(idx)
            # Append any adapters not in the sent indices
            for i, ad in enumerate(adapters):
                if i not in seen:
                    reordered.append(ad)
            adapters = reordered
            _save_adapters_list(adapters)

        elif action_name == 'enable':
            index = _adapter_index(request, adapters)
            adapters[index]['enabled'] = True
            _save_adapters_list(adapters)

        elif action_name == 'disable':
            index = _adapter_index(request, adapters)
            adapters[index]['enabled'] = False
            _save_adapters_list(adapters)

        elif action_name == 'update':
            index = _adapter_index(request, adapters)
            adapter = adapters[index]
            name = adapter.get('name', '')
            source = adapter.get('source', '')
            if not source:
                return JsonResponse({'error': 'no source for adapter'}, status=400)
            from site_adapters.services.subscriptions import fetch_subscription, _read_subscription_file, resolve_adapter_path
            interval = adapter.get('update_interval', 86400) if isinstance(adapter, dict) else 86400
            path = fetch_subscription(source, name=name, adapter_id=adapter.get('id', ''), force=request.POST.get('force') == '1', update_interval=interval)
            if path:
                # Sync _meta.name from fetched file to config name
                data = _read_subscription_file(path)
                if data and isinstance(data.get('_meta'), dict) and data['_meta'].get('name'):
                    adapters[index]['name'] = data['_meta']['name']
                    _save_adapters_list(adapters)
                _invalidate_site_adapters_cache()
                response = _adapters_response()
                from site_adapters.services.subscriptions import (
                    _get_meta_entry,
                    _normalize_source_to_directory,
                )
                meta_entry = _get_meta_entry(_normalize_source_to_directory(source))
                failed_scripts = meta_entry.get('script_failures') or []
                if failed_scripts:
                    payload = json.loads(response.content.decode('utf-8'))
                    payload['warning'] = (
                        'Some scripts failed to download and will be retried on the next update: '
                        + ', '.join(failed_scripts[:5])
                    )
                    return JsonResponse(payload)
                return response
            return JsonResponse({'error': 'update failed, check logs'}, status=500)

        else:
            return JsonResponse({'error': f'unknown action: {action_name}'}, status=400)

    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    _invalidate_site_adapters_cache()
    return _adapters_response()

# ---------------------------------------------------------------------------
# 兼容旧 API：域名列表和 toggle
# ---------------------------------------------------------------------------

@site_adapters_required
def all_domains(request):
    """返回域名列表（元数据，不含 config）。
    支持 ?q= 参数进行服务端过滤。"""
    from site_adapters.views.helpers import build_domain_files_meta
    try:
        domain_files = build_domain_files_meta()
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    q = request.GET.get('q', '').strip().lower()
    if q:
        domain_files = [d for d in domain_files if q in d.get('domain_key', '').lower()]

    return JsonResponse({'domain_files': domain_files, 'total_count': len(domain_files)})


@site_adapters_required
@require_POST
def local_domain_toggle(request):
    """开关域名启用/禁用状态。写入 config.jsonc 的 _disabled_domains。"""
    from site_adapters.views.helpers import _toggle_domain_disabled
    domain_key = request.POST.get('domain', '')
    if not domain_key:
        return JsonResponse({'error': 'domain key required'}, status=400)
    enabled = request.POST.get('enabled', '1') == '1'
    _toggle_domain_disabled(domain_key, disabled=not enabled)
    return JsonResponse({'success': True, 'domain': domain_key, 'enabled': enabled})


@site_adapters_required
def subscription_domain_read(request):
    """读取订阅域名配置（兼容旧 API）。"""
    domain_key = request.GET.get('domain', '')
    sub_name = request.GET.get('sub', '')
    from site_adapters.services.subscriptions import _read_subscription_file

    adapters = _get_adapters_list()
    for adapter in adapters:
        if not isinstance(adapter, dict):
            continue
        if adapter.get('name') == sub_name:
            from site_adapters.services.subscriptions import resolve_adapter_path
            file_path = resolve_adapter_path(sub_name, adapter.get('source', ''), adapter_id=adapter.get('id', ''))
            data = _read_subscription_file(file_path)
            if data and isinstance(data.get('domains'), dict) and domain_key in data['domains']:
                return JsonResponse({'config': data['domains'][domain_key], 'domain': domain_key})
            break
    return JsonResponse({'error': 'not found'}, status=404)


@site_adapters_required
@require_POST
def subscription_domain_toggle(request):
    """开关订阅域名（兼容旧 API）。"""
    return JsonResponse({'success': True})
