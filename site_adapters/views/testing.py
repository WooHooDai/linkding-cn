"""
Test panel: config/metadata/snapshot/reader/credential/pipeline tests + validation.
"""
import json
import logging
import os
from urllib.parse import urlparse

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from bookmarks.services.website_loader import (
    load_website_metadata_for_test,
    normalize_content_type,
)
from bookmarks.utils import is_safe_domain_key
from site_adapters.services.auth.cookies import (
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import save_shared_cookie
from site_adapters.services.config.loader import _cache, match_domain, show_config
from site_adapters.services.config.resolver import (
    get_health_config,
    get_metadata_config,
    get_reader_config,
    get_snapshot_config,
)
from site_adapters.services.config.validator import validate_config
from site_adapters.services.execution_log import collect_executions
from site_adapters.views.helpers import (
    TEST_ASSETS_DIR,
    _get_base_dir,
    _resolve_domain_path,
    _sanitize_url_for_filename,
    site_adapters_required,
)

logger = logging.getLogger(__name__)

def _timestamp() -> str:
    """返回当前时间戳字符串，遵循 TIME_ZONE 设置。"""
    from zoneinfo import ZoneInfo
    tz_name = getattr(settings, 'TIME_ZONE', 'UTC')
    tz = ZoneInfo(tz_name)
    return timezone.now().astimezone(tz).strftime('%Y%m%d%H%M%S')

@site_adapters_required
@require_POST
def action(request):
    """处理测试请求。"""
    act = request.POST.get('action', '')
    if act == 'test':
        try:
            return _handle_test(request)
        except Exception as exc:
            logger.exception("Site adapter test failed")
            return JsonResponse({
                'type': request.POST.get('test_type', 'test'),
                'error': str(exc),
            })
    elif act == 'validate':
        return _handle_validate(request)
    elif act == 'clean_test_files':
        return _handle_clean_test_files()
    return JsonResponse({'error': 'unknown action'}, status=400)


def _handle_validate(request) -> JsonResponse:
    base_dir = _get_base_dir()
    filename = request.POST.get('filename', '')
    if filename:
        try:
            _resolve_domain_path(filename)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
    issues = validate_config(base_dir, domain_filename=filename)
    return JsonResponse({'issues': issues})


# ---------------------------------------------------------------------------
# Test/verification (metadata, snapshot, reader, credential, pipeline)
# ---------------------------------------------------------------------------

def _test_response(data, entries=None, **kwargs):
    """包装测试响应，附加 collected execution entries."""
    if entries:
        data['executions'] = entries
    return JsonResponse(data, **kwargs)


def _handle_test(request) -> JsonResponse:
    url = request.POST.get('url', '').strip()
    test_type = request.POST.get('test_type', 'config')

    if not url:
        return JsonResponse({'error': 'URL required'}, status=400)

    base_dir = _get_base_dir()
    username = request.POST.get('test_username', '').strip()

    handlers = {
        'config': _test_config,
        'metadata': _test_metadata,
        'snapshot': _test_snapshot,
        'reader': _test_reader,
        'credential': _test_credential,
        'health': _test_health,
        'pipeline': _test_pipeline,
    }
    handler = handlers.get(test_type)
    if not handler:
        return _test_response({'error': f'Unknown test type: {test_type}'}, status=400)

    with collect_executions() as entries:
        return handler(url, base_dir, username, entries)


def _test_config(url, base_dir, username, entries):
    result = show_config(url, base_dir)
    issues = validate_config(base_dir)
    return _test_response({'type': 'config', 'result': result, 'issues': issues}, entries=entries)


def _make_default_metadata_config():
    """为无匹配域名的场景构建内置引擎默认参数。"""
    return {
        '_engine': 'built-in',
        'timeout': 10,
        'chunk_size': 50 * 1024,
        'proxy': None,
        'headers': {},
        'script': None,
        'select_title': None,
        'select_description': None,
        'select_image': None,
        'request_url': None,
        'rewrite_url': None,
    }


def _make_default_snapshot_config():
    """为无匹配域名的场景构建内置快照引擎默认参数。"""
    from django.conf import settings as django_settings
    return {
        '_engine': 'built-in (SingleFile)',
        'script': None,
        'content_type': 'html',
        'process_lazy_images': None,
        'remove_classes': None,
        'set_styles': None,
        'singlefile_args': {},
        'singlefile_path': getattr(django_settings, 'LD_SINGLEFILE_PATH', 'single-file'),
        'singlefile_timeout': getattr(django_settings, 'LD_SINGLEFILE_TIMEOUT_SEC', 60),
        'user_agent': getattr(django_settings, 'LD_DEFAULT_USER_AGENT', ''),
        'headers': {},
        'proxy': None,
        'request_url': None,
    }


def _make_default_reader_config():
    """为无匹配域名的场景构建内置阅读器引擎默认参数。"""
    return {
        '_engine': 'built-in (defuddle)',
        'script': None,
        'defuddle_args': {},
    }


# ---------------------------------------------------------------------------
# Credential source tracking (before/after diff)
# ---------------------------------------------------------------------------

def _snapshot_credential_state(config: dict, username: str, hostname: str) -> dict:
    """Capture current credential values before a test operation.

    Returns a dict with cookie/headers/token keys, each containing the
    best available value and its source (user/shared/http_header/none).
    Only captures credential types that are configured for the domain.
    """
    from site_adapters.services.auth.credentials import (
        _extract_auth_block,
        get_user_cookie, get_shared_cookie, get_best_cookie,
        get_user_headers, get_shared_headers, get_best_header, get_best_headers,
        get_user_token, get_shared_token, get_best_token,
        get_auth_requirements_for_domain,
    )

    state = {}
    scope = config.get('_effective_cookie_scope', '') if config else ''
    # Use the resolved section auth first so section-level credentials (e.g.
    # metadata.auth.cookie) are shown instead of only domain-level auth.
    auth_req = _extract_auth_block((config or {}).get('auth') or {})
    if not auth_req.get('cookie') and not auth_req.get('headers_active') and not auth_req.get('oauth2'):
        auth_req = get_auth_requirements_for_domain(hostname)

    # Cookie
    if auth_req.get('cookie'):
        best, _ = get_best_cookie(username=username, hostname=hostname, scope=scope)
        user_val, _ = get_user_cookie(username=username, hostname=hostname, scope=scope) if username else (None, '')
        shared_val, _ = get_shared_cookie(hostname=hostname, scope=scope)
        http_val = (config.get('headers', {}) or {}).get('Cookie') if config else None
        source = 'none'
        if user_val:
            source = 'user'
        elif shared_val:
            source = 'shared'
        elif http_val:
            source = 'http_header'
        state['cookie'] = {'value': best, 'source': source}

    # Headers: read all saved credentials (not limited to declared names)
    if auth_req.get('headers_active'):
        declared_names = auth_req.get('headers', [])
        all_saved, _ = get_best_headers(username=username, hostname=hostname, scope=scope)
        all_names = sorted(set(declared_names) | set(all_saved.keys()))
        header_state = {}
        for h in all_names:
            best = all_saved.get(h)
            user_h, _ = get_user_header(username=username, hostname=hostname, header_name=h, scope=scope) if username else (None, '')
            shared_h, _ = get_shared_header(hostname=hostname, header_name=h, scope=scope)
            source = 'none'
            if user_h:
                source = 'user'
            elif shared_h:
                source = 'shared'
            header_state[h] = {'value': best, 'source': source}
        state['headers'] = header_state

    # Token
    if auth_req.get('oauth2'):
        best, _ = get_best_token(username=username, hostname=hostname, scope=scope)
        user_val, _ = get_user_token(username=username, hostname=hostname, scope=scope) if username else (None, '')
        shared_val, _ = get_shared_token(hostname=hostname, scope=scope)
        source = 'none'
        if user_val:
            source = 'user'
        elif shared_val:
            source = 'shared'
        state['oauth2'] = {'value': best, 'source': source}

    return state


def _build_credential_entry(cred_type: str, entry, before_entry) -> dict:
    """Build a single credential source + status entry.

    Handles both scalar (cookie/token) and dict (headers) values.
    """
    if cred_type == 'headers':
        # entry is dict of {header_name: {value, source}}
        sources = {v.get('source', 'none') for v in entry.values()} if entry else set()
        if 'user' in sources:
            source = 'user'
        elif 'shared' in sources:
            source = 'shared'
        else:
            source = 'none'

        has_before = bool(before_entry) if before_entry else False
        has_after = bool(entry)
        if not has_before and has_after:
            status = 'acquired'
        elif has_before and has_after:
            changed = False
            if before_entry:
                for k, v in entry.items():
                    bv = before_entry.get(k, {})
                    if v.get('value') != bv.get('value'):
                        changed = True
                        break
            status = 'refreshed' if changed else 'existing'
        else:
            status = 'none'

        return {'source': source, 'status': status}

    # Scalar: cookie or token
    source = entry.get('source', 'none') if entry else 'none'
    has_before = bool(before_entry and before_entry.get('value')) if before_entry else False
    has_after = bool(entry and entry.get('value'))

    if not has_before and has_after:
        status = 'acquired'
    elif has_before and has_after:
        changed = entry.get('value') != before_entry.get('value')
        status = 'refreshed' if changed else 'existing'
    else:
        status = 'none'

    return {'source': source, 'status': status}


def _compute_credential_sources(config: dict, username: str, hostname: str,
                                before: dict) -> dict:
    """Compare before/after credential state to produce source + status.

    Returns {cred_type: {source: str, status: str}} for each configured type.
    status values: 'existing' | 'refreshed' | 'acquired' | 'none'
    """
    after = _snapshot_credential_state(config, username, hostname)
    result = {}
    for cred_type in ('cookie', 'headers', 'oauth2'):
        before_entry = before.get(cred_type)
        after_entry = after.get(cred_type)
        if not before_entry and not after_entry:
            continue
        entry = after_entry or before_entry
        result[cred_type] = _build_credential_entry(cred_type, entry, before_entry)
    return result


# ---------------------------------------------------------------------------
# Test handlers
# ---------------------------------------------------------------------------

def _extract_match_info(config):
    """从 config 中提取匹配信息。"""
    if not config:
        return {'matched': False, 'domain_key': None, 'adapter': None, 'route_key': None}
    return {
        'matched': True,
        'domain_key': config.get('_domain_key'),
        'adapter': config.get('_adapter'),
        'route_key': config.get('_route_key'),
    }


def _test_health(url, base_dir, username, entries):
    """Health check 测试：展示该 URL 命中的 health 适配配置 + 实际判定结果。

    调用引擎的 check_url（不写库），返回状态/HTTP 码/判定说明/耗时等，
    与书签列表中的结论一致；同时给出归一化后的 health 配置（L1/L2 开关、
    超时、重定向上限、UA、cookie/headers 生效情况）供排查。
    """
    from bookmarks.services.health_checker import check_url
    from bookmarks.services.website_loader import build_request_cookies

    hostname = urlparse(url).hostname or ''
    config = get_health_config(url, username=username)
    show_cfg = show_config(url, base_dir)
    match_info = _extract_match_info(config)

    # 归一化配置中是否会带用户/shared cookie
    cookie_sources = {}
    if config:
        try:
            cookie_val = build_request_cookies(config)
            cookie_sources['carrying_cookie'] = bool(cookie_val)
        except Exception:
            cookie_sources['carrying_cookie'] = False

    try:
        result = check_url(url, username=username)
        check_error = None
    except Exception as exc:
        result = {}
        check_error = str(exc)[:500]

    HEALTH_STATUS_CHOICES_MAP = {
        'ok': 'Ok', 'dead': 'Dead', 'failed': 'Failed',
        'blocked': 'Blocked', 'missing': 'Missing',
    }
    status = result.get('status')
    return _test_response({
        'type': 'health',
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'route_key': match_info['route_key'],
        'original_url': url,
        'request_url': (config or {}).get('_request_url', url) if config else url,
        'config': config,
        # 对齐 metadata：有域名匹配时展示合并配置；无匹配（builtin 兜底）时
        # 展示引擎实际生效的归一化配置，便于排查判定依据
        'merged_config': show_cfg.get('merged') if show_cfg.get('matched') else config,
        'cookie': cookie_sources,
        'result': {
            'status': status,
            'status_display': HEALTH_STATUS_CHOICES_MAP.get(status, 'Unknown') if status else None,
            'http_status': result.get('http_status'),
            'reason': result.get('reason'),
            'redirect_url': result.get('redirect_url'),
            'duration_ms': result.get('duration_ms'),
            'skipped': result.get('skipped'),
            'probes': result.get('probes'),
            'content_signals': result.get('content_signals'),
            'error': check_error,
        },
        'raw_config': show_cfg.get('raw_config'),
        'help': {
            'hostname': hostname,
            'username': username or '(none, shared credentials only)',
        },
    }, entries=entries)


def _test_metadata(url, base_dir, username, entries):
    hostname = urlparse(url).hostname or ''
    config = get_metadata_config(url, username=username)
    before = _snapshot_credential_state(config, username, hostname) if config else {}

    show_cfg = show_config(url, base_dir)
    no_match = False
    if not config:
        no_match = True
    metadata, sources, config = load_website_metadata_for_test(url, username=username)
    metadata_error = None
    if isinstance(sources, dict):
        metadata_error = sources.pop('error', None)
    match_info = _extract_match_info(config)

    credential_sources = _compute_credential_sources(config, username, hostname, before)

    result = {
        'type': 'metadata',
        'no_match': no_match,
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'route_key': match_info['route_key'],
        'config': config,
        'raw_config': show_cfg.get('raw_config'),
        'merged_config': show_cfg.get('merged'),
        'original_url': url,
        'request_url': config.get('_request_url', url) if config else url,
        'result': metadata.to_dict(),
        'sources': sources,
        'credential_sources': credential_sources,
    }
    if no_match:
        result['default_config'] = _make_default_metadata_config()
    if metadata_error:
        result['metadata_error'] = metadata_error
        result['failed'] = True
    return _test_response(result, entries=entries)


def _test_snapshot(url, base_dir, username, entries):
    hostname = urlparse(url).hostname or ''
    config = get_snapshot_config(url, username=username)
    before = _snapshot_credential_state(config, username, hostname) if config else {}

    show_cfg = show_config(url, base_dir)
    no_match = False
    if not config:
        no_match = True
    match_info = _extract_match_info(config)

    # Check if snapshots are disabled by adapter config (snapshot.enabled: false)
    snapshot_disabled = config is not None and config.get('enabled', True) is False

    if snapshot_disabled:
        result = {
            'type': 'snapshot',
            'no_match': no_match,
            'matched': match_info['matched'],
            'domain_key': match_info['domain_key'],
            'adapter': match_info['adapter'],
            'route_key': match_info['route_key'],
            'config': config,
            'raw_config': show_cfg.get('raw_config'),
            'merged_config': show_cfg.get('merged'),
            'original_url': url,
            'request_url': config.get('_request_url', url) if config else url,
            'credential_sources': _compute_credential_sources(config, username, hostname, before),
            'disabled': True,
        }
        return _test_response(result, entries=entries)

    from bookmarks.services.snapshot_processor import create_snapshot
    os.makedirs(TEST_ASSETS_DIR, exist_ok=True)
    snapshot_extension = normalize_content_type((config or {}).get('content_type')) or 'html'
    filename = (
        'snapshot_'
        + _timestamp()
        + '_'
        + _sanitize_url_for_filename(url)
        + '.'
        + snapshot_extension
    )
    out_path = os.path.join(TEST_ASSETS_DIR, filename)
    create_snapshot(url, out_path, username=username)

    credential_sources = _compute_credential_sources(config, username, hostname, before)

    result = {
        'type': 'snapshot',
        'no_match': no_match,
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'route_key': match_info['route_key'],
        'config': config,
        'raw_config': show_cfg.get('raw_config'),
        'merged_config': show_cfg.get('merged'),
        'original_url': url,
        'request_url': config.get('_request_url', url) if config else url,
        'credential_sources': credential_sources,
        'result': {
            'file': filename,
            'size': os.path.getsize(out_path),
            'view_url': f'/admin/site-adapters/view-snapshot?file={filename}',
        },
    }
    if no_match:
        result['default_config'] = _make_default_snapshot_config()
    return _test_response(result, entries=entries)


def _write_reader_test_asset(
    reader_html: str,
    url: str,
    metadata: dict | None = None,
    original_url: str = "",
    snapshot_view_url: str = "",
) -> tuple[str, str]:
    """Write a reader preview file plus companion metadata used by the preview page."""
    metadata = metadata or {}
    reader_filename = 'article_' + _timestamp() + '_' + _sanitize_url_for_filename(url) + '.html'
    reader_path = os.path.join(TEST_ASSETS_DIR, reader_filename)
    with open(reader_path, 'w', encoding='utf-8') as f:
        f.write(reader_html)

    reader_meta_path = os.path.splitext(reader_path)[0] + '.json'
    with open(reader_meta_path, 'w', encoding='utf-8') as f:
        json.dump({
            'title': metadata.get('title', ''),
            'word_count': metadata.get('wordCount', 0),
            'original_url': original_url or url,
            'snapshot_url': snapshot_view_url,
        }, f, ensure_ascii=False)

    return reader_filename, reader_path


def _test_reader(url, base_dir, username, entries):
    hostname = urlparse(url).hostname or ''
    config = get_reader_config(url, username=username)
    snapshot_config = get_snapshot_config(url, username=username)
    before = _snapshot_credential_state(config, username, hostname) if config else {}

    show_cfg = show_config(url, base_dir)
    no_match = False
    if not config:
        no_match = True
    from bookmarks.services import reader_processor
    from bookmarks.services.snapshot_processor import create_snapshot
    os.makedirs(TEST_ASSETS_DIR, exist_ok=True)
    snapshot_extension = (
        normalize_content_type((snapshot_config or {}).get('content_type')) or 'html'
    )
    snap_filename = (
        'snapshot_'
        + _timestamp()
        + '_'
        + _sanitize_url_for_filename(url)
        + '.'
        + snapshot_extension
    )
    snap_path = os.path.join(TEST_ASSETS_DIR, snap_filename)

    # Determine reader mode: useAsync=true bypasses snapshot, useAsync=false needs snapshot HTML
    defuddle_args = (config or {}).get('defuddle_args', {})
    use_async = defuddle_args.get('useAsync', False) is True
    snapshot_disabled = (
        snapshot_config is not None
        and snapshot_config.get('enabled', True) is False
    )

    reader_notice = None
    if use_async:
        reader_notice = 'async'
        # useAsync=true: defuddle fetches directly, no snapshot needed
        result = reader_processor.parse_url(url, username=username)
        snap_path_final = None
        snapshot_view_url = ''
    else:
        # useAsync=false: need snapshot HTML
        create_snapshot(url, snap_path, username=username)
        with open(snap_path, encoding='utf-8') as f:
            raw_content = f.read()
        if snapshot_extension in ('json', 'xml'):
            result = reader_processor.parse_content(
                raw_content, snapshot_extension, url=url, username=username
            )
        else:
            result = reader_processor.parse_html(
                raw_content, url=url, username=username
            )
        snap_path_final = snap_path
        snapshot_view_url = f'/admin/site-adapters/view-snapshot?file={snap_filename}'
        if snapshot_disabled:
            reader_notice = 'temp_snapshot'

    reader_html = result.get('content', '')
    reader_filename, reader_path = _write_reader_test_asset(
        reader_html, url, result, url, snapshot_view_url
    )

    credential_sources = _compute_credential_sources(config, username, hostname, before)

    match_info = _extract_match_info(config)
    result_data = {
        'type': 'reader',
        'no_match': no_match,
        'matched': match_info['matched'],
        'domain_key': match_info['domain_key'],
        'adapter': match_info['adapter'],
        'route_key': match_info['route_key'],
        'config': config,
        'raw_config': show_cfg.get('raw_config'),
        'merged_config': show_cfg.get('merged'),
        'original_url': url,
        'request_url': config.get('_request_url', url) if config else url,
        'credential_sources': credential_sources,
        'result': {
            'title': result.get('title', ''),
            'word_count': result.get('wordCount', 0),
            'reader_view': f'/admin/site-adapters/view-reader?file={reader_filename}',
            'html_size': os.path.getsize(reader_path),
            'view_url': f'/admin/site-adapters/view-snapshot?file={reader_filename}',
            'snapshot_size': os.path.getsize(snap_path_final) if snap_path_final and os.path.exists(snap_path_final) else 0,
            'snapshot_view_url': snapshot_view_url,
        },
        'defuddle_args': config.get('defuddle_args') if config else None,
        'reader_notice': reader_notice,
    }
    if no_match:
        result_data['default_config'] = _make_default_reader_config()
    return _test_response(result_data, entries=entries)


def _test_credential(url, base_dir, username, entries):
    """Test and display credential state for a URL (cookie + headers + token)."""
    from site_adapters.services.auth.credentials import (
        get_best_cookie, get_shared_cookie, get_user_cookie,
        get_best_header, get_user_header, get_shared_header,
        get_best_token, get_user_token, get_shared_token,
        get_auth_requirements_for_domain,
    )
    hostname = urlparse(url).hostname or ''
    if not hostname:
        return _test_response({'type': 'credential', 'error': 'Unable to parse hostname from URL'}, entries=entries)

    auth_req = get_auth_requirements_for_domain(hostname)

    # Get config for domain_key display and refresh
    metadata_config = get_metadata_config(url, username=username) or {}
    domain_key = metadata_config.get('_domain_key', hostname) if metadata_config else hostname
    config = metadata_config
    test_scope = config.get('_effective_cookie_scope', '') if config else ''

    # --- Cookie ---
    cookie_info = None
    if auth_req.get('cookie'):
        cookie_type = auth_req.get('cookie_type', 'auto')

        # Before state
        before_user, _ = get_user_cookie(username=username, hostname=hostname, scope=test_scope) if username else (None, '')
        before_shared, _ = get_shared_cookie(hostname=hostname, scope=test_scope)
        before_best, _ = get_best_cookie(username=username, hostname=hostname, scope=test_scope)

        has_user_before = bool(before_user)
        has_shared_before = bool(before_shared)

        # Refresh
        cookie_config = config.get('cookie', {}) if config else {}
        before = before_best
        after = verify_and_refresh(cookie_config=cookie_config, url=url, domain_key=domain_key,
                                   verify_context={'url': url, 'status': 0, 'title': '', 'body_preview': ''},
                                   username=username, scope=test_scope) if cookie_config else None
        refreshed = bool(after and (not before or after != before))

        # After state
        after_user, after_user_status = get_user_cookie(username=username, hostname=hostname, scope=test_scope) if username else (None, '')
        after_shared, after_shared_status = get_shared_cookie(hostname=hostname, scope=test_scope)
        after_best, after_best_status = get_best_cookie(username=username, hostname=hostname, scope=test_scope)

        has_cookie = bool(after_best)
        cookie_preview = after_best[:50] + '...' if after_best and len(after_best) > 50 else (after_best or '')

        # Source
        if after_user:
            cookie_source = 'user'
        elif after_shared:
            cookie_source = 'shared'
        elif after_best:
            cookie_source = 'http_header'
        else:
            cookie_source = 'none'

        # Check if cookie was marked as expired/invalid
        is_invalid = after_best_status == 'invalid' or after_user_status == 'invalid' or after_shared_status == 'invalid'

        # Read expired_at from metadata
        expired_at = ''
        if is_invalid:
            from site_adapters.services.auth.credentials import _get_cookie_expired_meta, _resolve_credential_domain, _resolve_shared_credential_domain
            if after_user_status == 'invalid' and username:
                matched = _resolve_credential_domain(username, hostname)
                if matched:
                    expired_at = _get_cookie_expired_meta(cred_source=username, domain=matched, scope=test_scope).get('expired_at', '')
            elif after_shared_status == 'invalid':
                matched = _resolve_shared_credential_domain(hostname)
                if matched:
                    expired_at = _get_cookie_expired_meta(cred_source='shared', domain=matched, scope=test_scope).get('expired_at', '')

        # Status
        had_any_before = has_user_before or has_shared_before
        if is_invalid:
            cookie_status = 'invalid'
        elif refreshed:
            cookie_status = 'acquired' if not had_any_before else 'refreshed'
        else:
            cookie_status = 'existing' if has_cookie else 'none'

        cookie_info = {
            'has_value': has_cookie,
            'has_user': bool(after_user),
            'has_shared': bool(after_shared),
            'preview': cookie_preview,
            'source': cookie_source,
            'status': cookie_status,
            'cookie_type': cookie_type,
            'expired_at': expired_at,
        }

    # --- Headers: read all saved credentials (not limited to declared names) ---
    headers_info = None
    if auth_req.get('headers_active'):
        declared_names = auth_req.get('headers', [])
        all_saved, _ = get_best_headers(username=username, hostname=hostname, scope=test_scope)
        all_names = sorted(set(declared_names) | set(all_saved.keys()))
        header_list = []
        for h in all_names:
            best = all_saved.get(h)
            user_h, _ = get_user_header(username=username, hostname=hostname, header_name=h, scope=test_scope) if username else (None, '')
            shared_h, _ = get_shared_header(hostname=hostname, header_name=h, scope=test_scope)
            source = 'none'
            if user_h:
                source = 'user'
            elif shared_h:
                source = 'shared'
            header_list.append({
                'name': h,
                'has_value': bool(best),
                'source': source,
                'declared': h in declared_names,
            })
        h_sources = {h['source'] for h in header_list}
        if 'user' in h_sources:
            h_source = 'user'
        elif 'shared' in h_sources:
            h_source = 'shared'
        else:
            h_source = 'none'
        headers_info = {
            'headers': header_list,
            'source': h_source,
            'status': 'existing' if h_source != 'none' else 'none',
        }

    # --- Token ---
    token_info = None
    if auth_req.get('oauth2'):
        best, _ = get_best_token(username=username, hostname=hostname, scope=test_scope)
        user_val, _ = get_user_token(username=username, hostname=hostname, scope=test_scope) if username else (None, '')
        shared_val, _ = get_shared_token(hostname=hostname, scope=test_scope)
        source = 'none'
        if user_val:
            source = 'user'
        elif shared_val:
            source = 'shared'
        token_info = {
            'has_value': bool(best),
            'source': source,
            'status': 'existing' if source != 'none' else 'none',
        }

    result = {
        'type': 'credential',
        'hostname': hostname,
        'domain_key': domain_key,
    }
    if cookie_info:
        result['cookie'] = cookie_info
    if headers_info:
        result['headers'] = headers_info
    if token_info:
        result['oauth2'] = token_info

    return _test_response(result, entries=entries)


def _test_pipeline(url, base_dir, username, entries):
    from bookmarks.services import reader_processor
    from bookmarks.services.snapshot_processor import create_snapshot
    hostname = urlparse(url).hostname or ''
    config_result = show_config(url, base_dir)
    meta_config = get_metadata_config(url, username=username)
    before_meta = _snapshot_credential_state(meta_config, username, hostname) if meta_config else {}
    snap_config = get_snapshot_config(url, username=username)
    before_snap = _snapshot_credential_state(snap_config, username, hostname) if snap_config else {}
    reader_config = get_reader_config(url, username=username)
    before_reader = _snapshot_credential_state(reader_config, username, hostname) if reader_config else {}

    metadata, sources, _ = load_website_metadata_for_test(url, username=username)
    metadata_error = None
    if isinstance(sources, dict):
        metadata_error = sources.pop('error', None)

    credential_sources_meta = _compute_credential_sources(meta_config, username, hostname, before_meta)

    # Check if snapshots are disabled by adapter config (snapshot.enabled: false)
    snapshot_disabled = snap_config is not None and snap_config.get('enabled', True) is False

    # Determine reader mode: useAsync=true bypasses snapshot, useAsync=false needs snapshot HTML
    defuddle_args = (reader_config or {}).get('defuddle_args', {})
    use_async = defuddle_args.get('useAsync', False) is True

    os.makedirs(TEST_ASSETS_DIR, exist_ok=True)
    snapshot_extension = (
        normalize_content_type((snap_config or {}).get('content_type')) or 'html'
    )
    snap_filename = (
        'snapshot_'
        + _timestamp()
        + '_'
        + _sanitize_url_for_filename(url)
        + '.'
        + snapshot_extension
    )
    snap_path = os.path.join(TEST_ASSETS_DIR, snap_filename)

    reader_notice = None
    if use_async:
        # useAsync=true: defuddle fetches directly, no snapshot needed
        reader_notice = 'async'
        try:
            article = reader_processor.parse_url(url, username=username)
        except Exception:
            article = {'title': '', 'content': '', 'wordCount': 0}
        snapshot_view_url = ''
        reader_filename, reader_path = _write_reader_test_asset(
            article.get('content', ''), url, article, url, ''
        )
    elif snapshot_disabled:
        # useAsync=false + snapshot.enabled=false → 临时快照
        reader_notice = 'temp_snapshot'
        create_snapshot(url, snap_path, username=username)
        with open(snap_path, encoding='utf-8') as f:
            raw_content = f.read()
        if snapshot_extension in ('json', 'xml'):
            article = reader_processor.parse_content(
                raw_content, snapshot_extension, url=url, username=username
            )
        else:
            article = reader_processor.parse_html(
                raw_content, url=url, username=username
            )
        snapshot_view_url = f'/admin/site-adapters/view-snapshot?file={snap_filename}'
        reader_filename, reader_path = _write_reader_test_asset(
            article.get('content', ''), url, article, url, snapshot_view_url
        )
    else:
        create_snapshot(url, snap_path, username=username)
        with open(snap_path, encoding='utf-8') as f:
            raw_content = f.read()
        if snapshot_extension in ('json', 'xml'):
            article = reader_processor.parse_content(
                raw_content, snapshot_extension, url=url, username=username
            )
        else:
            article = reader_processor.parse_html(
                raw_content, url=url, username=username
            )
        reader_html = article.get('content', '')
        snapshot_view_url = f'/admin/site-adapters/view-snapshot?file={snap_filename}'
        reader_filename, reader_path = _write_reader_test_asset(
            reader_html, url, article, url, snapshot_view_url
        )
    metadata_no_match = not meta_config
    snapshot_no_match = not snap_config
    reader_no_match = not reader_config
    md_match = _extract_match_info(meta_config)

    credential_sources_snap = _compute_credential_sources(snap_config, username, hostname, before_snap)
    credential_sources_reader = _compute_credential_sources(reader_config, username, hostname, before_reader)

    snap_match = _extract_match_info(snap_config)
    rd_match = _extract_match_info(reader_config)
    result = {
        'type': 'pipeline',
        'config': config_result,
        'metadata': {
            'config': meta_config,
            'raw_config': config_result.get('raw_config'),
            'merged_config': config_result.get('merged'),
            'matched': md_match['matched'],
            'domain_key': md_match['domain_key'],
            'adapter': md_match['adapter'],
            'route_key': md_match['route_key'],
            'credential_sources': credential_sources_meta,
            'no_match': metadata_no_match,
            'original_url': url,
            'request_url': meta_config.get('_request_url', url) if meta_config else url,
            'result': metadata.to_dict(),
            'sources': sources,
            'metadata_error': metadata_error,
        },
        'snapshot': {
            'config': snap_config,
            'raw_config': config_result.get('raw_config'),
            'merged_config': config_result.get('merged'),
            'matched': snap_match['matched'],
            'domain_key': snap_match['domain_key'],
            'credential_sources': credential_sources_snap,
            'adapter': snap_match['adapter'],
            'route_key': snap_match['route_key'],
            'no_match': snapshot_no_match,
            'disabled': snapshot_disabled or use_async,
            'original_url': url,
            'request_url': snap_config.get('_request_url', url) if snap_config else url,
            'result': {
                'file': snap_filename if not snapshot_disabled and not use_async else '',
                'size': os.path.getsize(snap_path) if os.path.exists(snap_path) and not use_async else 0,
                'view_url': f'/admin/site-adapters/view-snapshot?file={snap_filename}' if not snapshot_disabled and not use_async else '',
            },
        },
        'reader': {
            'config': reader_config,
            'raw_config': config_result.get('raw_config'),
            'merged_config': config_result.get('merged'),
            'matched': rd_match['matched'],
            'domain_key': rd_match['domain_key'],
            'credential_sources': credential_sources_reader,
            'adapter': rd_match['adapter'],
            'route_key': rd_match['route_key'],
            'no_match': reader_no_match,
            'original_url': url,
            'request_url': reader_config.get('_request_url', url) if reader_config else url,
            'result': {
                'title': article.get('title', ''),
                'word_count': article.get('wordCount', 0),
                'reader_view': f'/admin/site-adapters/view-reader?file={reader_filename}',
                'html_size': os.path.getsize(reader_path),
                'view_url': f'/admin/site-adapters/view-snapshot?file={reader_filename}',
                'snapshot_size': os.path.getsize(snap_path) if os.path.exists(snap_path) and not use_async else 0,
                'snapshot_view_url': snapshot_view_url,
            },
            'defuddle_args': reader_config.get('defuddle_args') if reader_config else None,
            'reader_notice': reader_notice,
        },
    }
    if metadata_no_match:
        result['metadata']['default_config'] = _make_default_metadata_config()
    if snapshot_no_match:
        result['snapshot']['default_config'] = _make_default_snapshot_config()
    if reader_no_match:
        result['reader']['default_config'] = _make_default_reader_config()
    if metadata_error:
        result['metadata_error'] = metadata_error
        result['failed'] = True
    return _test_response(result, entries=entries)


def _handle_clean_test_files() -> JsonResponse:
    test_dir = TEST_ASSETS_DIR
    if not os.path.isdir(test_dir):
        return JsonResponse({'success': True, 'deleted': 0})
    count = 0
    for f in os.listdir(test_dir):
        fpath = os.path.join(test_dir, f)
        if os.path.isfile(fpath):
            os.remove(fpath)
            count += 1
    return JsonResponse({'success': True, 'deleted': count})


# ---------------------------------------------------------------------------
# Cookie 操作
# ---------------------------------------------------------------------------

@site_adapters_required
@require_POST
def save_cookie(request):
    """保存 cookie。"""
    domain_key = request.POST.get('domain_key', '')
    cookie_str = request.POST.get('cookie', '')
    if not domain_key:
        return JsonResponse({'error': 'domain_key required'}, status=400)
    if not is_safe_domain_key(domain_key):
        return JsonResponse({'error': 'invalid domain key'}, status=400)
    save_shared_cookie(domain=domain_key, cookie_str=cookie_str)
    return JsonResponse({'success': True})
