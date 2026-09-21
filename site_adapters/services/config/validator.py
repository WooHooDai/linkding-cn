"""
Validator — 配置验证 + 字段分类

Issue 结构:
    {
        'level': 'error' | 'warning' | 'info',
        'code': str,       # 机器可读的 issue 类型
        'message': str,    # 人类可读描述
        'file': str,       # 文件路径 (可选)
        'adapter': str,    # adapter id.name (可选)
        'path': str,       # 配置路径，如 example.com.snapshot.scripts[0].path (可选)
    }
"""

import json
import logging
import os
import re

from site_adapters.services.config import (
    is_safe_script_path,
    load_jsonc_file,
    load_jsonc_file_with_warnings,
    parse_jsonc,
    _resolve_path,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Issue helper
# ---------------------------------------------------------------------------

def _issue(
    level: str,
    code: str,
    message: str,
    file: str | None = None,
    adapter: str | None = None,
    path: str | None = None,
) -> dict:
    """Build a structured issue dict."""
    d = {'level': level, 'code': code, 'message': message}
    if file:
        d['file'] = file
    if adapter:
        d['adapter'] = adapter
    if path:
        d['path'] = path
    return d


# ---------------------------------------------------------------------------
# 自动类型校验（基于 fields.py 的 type 字符串）
# ---------------------------------------------------------------------------

def _check_type(value, type_str: str) -> bool:
    """Check if *value* matches the type declared in fields.py.

    Returns True if valid, False if invalid.
    """
    if type_str == 'int':
        return isinstance(value, int) and not isinstance(value, bool)
    if type_str == 'bool':
        return isinstance(value, bool)
    if type_str == 'str':
        return isinstance(value, str)
    if type_str == 'str|null':
        return value is None or isinstance(value, str)
    if type_str == 'str|obj':
        # 字符串 = 全局兜底；对象 = 按状态/HTTP 码分层（值必须是字符串或对象）
        if isinstance(value, str):
            return True
        if not isinstance(value, dict):
            return False

        def _valid_text(v):
            return isinstance(v, str) and bool(v)

        def _valid_level(v):
            if _valid_text(v):
                return True
            if isinstance(v, dict):
                return all(_valid_text(k) and _valid_text(x) for k, x in v.items())
            return False

        return all(_valid_level(k) and _valid_level(v) for k, v in value.items()) if isinstance(value, dict) else False
    if type_str == 'str|array<str>':
        if isinstance(value, str):
            return True
        return isinstance(value, list) and all(isinstance(i, str) for i in value)
    if type_str == 'bool|array<str>':
        if isinstance(value, bool):
            return True
        return isinstance(value, list) and all(isinstance(i, str) for i in value)
    if type_str == 'array<str>':
        return isinstance(value, list) and all(isinstance(i, str) for i in value)
    if type_str == 'object<string, string>':
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        )
    if type_str == 'object':
        return isinstance(value, dict)
    if type_str == 'rewrite':
        if not isinstance(value, list):
            return False
        if len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], str):
            return True
        return all(
            isinstance(rule, list) and len(rule) == 2
            and isinstance(rule[0], str) and isinstance(rule[1], str)
            for rule in value
        )
    if type_str == 'auth':
        return value is None or isinstance(value, dict)
    if type_str == 'array<{path, hook}>':
        if not isinstance(value, list):
            return False
        return all(isinstance(item, dict) for item in value)
    # Enum types: "html"|"xml"|"json" or "auto"|"login" etc.
    if '|' in type_str and '<' not in type_str:
        import re
        options = re.findall(r'"([^"]+)"', type_str)
        if options:
            return value in options
    # Fallback: no type check available
    return True


def _auto_type_check(issues, label, section, key, value, file, adapter):
    """Auto-check field type against fields.py declaration."""
    field_def = ALL_SECTIONS.get(section, {}).get(key, {})
    type_str = field_def.get('type', '')
    if not type_str:
        return
    # Skip types handled by special validators
    if type_str in ('auth', 'array<{path, hook}>', 'rewrite'):
        return
    if not _check_type(value, type_str):
        issues.append(_issue(
            'error', 'type_mismatch',
            f"{label}.{section}.{key} must be {type_str}, got {type(value).__name__}",
            file=file, adapter=adapter, path=f"{label}.{section}.{key}",
        ))


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
_singlefile_args_set: set[str] | None = None
_defuddle_params_set: set[str] | None = None

# ---------------------------------------------------------------------------
# 各板块合法字段
# ---------------------------------------------------------------------------
from site_adapters.services.config.fields import (
    DEFAULT_FIELDS,
    DEFUDDLE_ARG_FIELDS,
    METADATA_FIELDS,
    SNAPSHOT_FIELDS,
    READER_FIELDS,
    HEALTH_FIELDS,
    ALL_SECTIONS,
    SINGLEFILE_ARG_NAMES,
)


def get_singlefile_args_set() -> set[str]:
    global _singlefile_args_set
    if _singlefile_args_set is None:
        _singlefile_args_set = set(SINGLEFILE_ARG_NAMES)
    return _singlefile_args_set


def get_defuddle_params_set() -> set[str]:
    global _defuddle_params_set
    if _defuddle_params_set is None:
        _defuddle_params_set = {
            key for key, info in DEFUDDLE_ARG_FIELDS.items()
            if not info.get("reserved")
        }
    return _defuddle_params_set


# ---------------------------------------------------------------------------
# 分类函数
# ---------------------------------------------------------------------------

_SECTION_FIELDS = {
    'defaults': set(DEFAULT_FIELDS.keys()),
    'metadata': set(METADATA_FIELDS.keys()),
    'snapshot': set(SNAPSHOT_FIELDS.keys()),
    'reader': set(READER_FIELDS.keys()),
    'health': set(HEALTH_FIELDS.keys()),
}

def classify_field(section: str, key: str) -> str:
    """
    Return field classification:
    - "field": known field for this section
    - "unknown": unrecognized field
    """
    fields = _SECTION_FIELDS.get(section, set())
    if key in fields:
        return "field"
    return "unknown"


def is_known_singlefile_arg(name: str) -> bool:
    return name in get_singlefile_args_set()


def is_known_defuddle_param(name: str) -> bool:
    return name in get_defuddle_params_set()


# ---------------------------------------------------------------------------
# 字段分离
# ---------------------------------------------------------------------------

def separate_http_fields(data: dict) -> tuple[dict, dict]:
    """Separate http section: framework fields vs HTTP headers."""
    framework = {"timeout", "proxy", "cookie"}
    app = {}
    headers = {}
    for key, value in data.items():
        if key in framework:
            app[key] = value
        else:
            headers[key] = value
    return app, headers


def validate_section_fields(section: str, data: dict) -> list[dict]:
    """Validate section fields, return issues. Unknown fields are discarded."""
    issues = []
    fields = _SECTION_FIELDS.get(section, set())
    for key in data:
        if key not in fields:
            issues.append(_issue('warning', 'unknown_field', f"{section}.{key} is unknown, discarded"))
    return issues


# ---------------------------------------------------------------------------
# 配置验证（从 engine.py 迁移）
# ---------------------------------------------------------------------------

def _is_safe_name(name: str) -> bool:
    return bool(name) and name == os.path.basename(name) and '/' not in name and '\\' not in name and '..' not in name


def _validate_subscriptions(issues: list[dict], adapters, file: str | None = None, adapters_dir: str | None = None):
    if adapters is None:
        return
    if not isinstance(adapters, list):
        issues.append(_issue('error', 'adapters_not_list', "_adapters must be an array", file=file))
        return
    from site_adapters.services.subscriptions import is_remote_source, resolve_adapter_path, _normalize_source_to_directory
    for index, adp in enumerate(adapters):
        label = f"_adapters[{index}]"
        if not isinstance(adp, dict):
            issues.append(_issue('error', 'adapter_entry_not_object', f"{label} must be an object", file=file, path=label))
            continue
        name = adp.get('name', '')
        if name and not _is_safe_name(name):
            issues.append(_issue('error', 'adapter_name_invalid', f"{label}.name is invalid", file=file, path=f"{label}.name"))
        source = adp.get('source', '')
        if source:
            if is_remote_source(source):
                from urllib.parse import urlparse
                parsed = urlparse(source)
                if parsed.scheme != 'https' or not parsed.netloc:
                    issues.append(_issue('error', 'adapter_source_not_https', f"{label}.source must be an HTTPS URL", file=file, path=f"{label}.source"))
            else:
                # Resolve relative source paths against adapters_dir, same as resolve_adapter_path
                resolved = resolve_adapter_path(name, source, adapters_dir, adapter_id=adp.get('id', '')) if adapters_dir else source
                if not os.path.exists(resolved):
                    issues.append(_issue('warning', 'adapter_source_not_found', f"{label}.source local file not found: {source}", file=file, path=f"{label}.source"))
        interval = adp.get('update_interval', 86400)
        if not isinstance(interval, int) or interval <= 0:
            issues.append(_issue('error', 'update_interval_invalid', f"{label}.update_interval must be a positive integer", file=file, path=f"{label}.update_interval"))


def _validate_cookie_block(issues: list[dict], label: str, cookie: dict, file_dir: str, file: str | None = None, adapter: str | None = None):
    """Validate a cookie config block."""
    if not isinstance(cookie, dict):
        issues.append(_issue('error', 'cookie_block_error', f"{label} must be an object", file=file, adapter=adapter, path=label))
        return
    valid_types = ("auto", "login")
    ctype = cookie.get("type", "auto")
    if ctype not in valid_types:
        issues.append(_issue('error', 'cookie_block_error', f"{label}.type must be one of {valid_types}, got '{ctype}'", file=file, adapter=adapter, path=f"{label}.type"))
    # verify
    verify = cookie.get("verify")
    if verify is not None:
        if not isinstance(verify, dict):
            issues.append(_issue('error', 'cookie_block_error', f"{label}.verify must be an object", file=file, adapter=adapter, path=f"{label}.verify"))
        else:
            check = verify.get("check")
            if check is not None:
                if not isinstance(check, list) or not all(isinstance(s, str) for s in check):
                    issues.append(_issue('error', 'cookie_block_error', f"{label}.verify.check must be a string array", file=file, adapter=adapter, path=f"{label}.verify.check"))
            invalid_pats = verify.get("content_check", {}).get("invalid_patterns", []) if isinstance(verify.get("content_check"), dict) else verify.get("invalid_patterns", [])
            if invalid_pats is not None:
                if not isinstance(invalid_pats, list) or not all(isinstance(s, str) for s in invalid_pats):
                    issues.append(_issue('error', 'cookie_block_error', f"{label}.verify.invalid_patterns must be a string array", file=file, adapter=adapter, path=f"{label}.verify.invalid_patterns"))
            valid_selector = verify.get("valid_selector")
            if valid_selector is not None and not isinstance(valid_selector, str):
                issues.append(_issue('error', 'cookie_block_error', f"{label}.verify.valid_selector must be a string", file=file, adapter=adapter, path=f"{label}.verify.valid_selector"))
            # warn about unknown keys
            for key in verify:
                if key not in ("check", "check_selectors", "invalid_patterns", "invalid_selectors", "valid_selector", "http_head_probe", "content_check", "probe"):
                    issues.append(_issue('warning', 'unknown_field', f"{label}.verify.{key} is unknown, will be ignored", file=file, adapter=adapter, path=f"{label}.verify.{key}"))
    # refresh
    refresh = cookie.get("refresh")
    if refresh is not None:
        if not isinstance(refresh, dict):
            issues.append(_issue('error', 'cookie_block_error', f"{label}.refresh must be an object", file=file, adapter=adapter, path=f"{label}.refresh"))
        else:
            for key in refresh:
                if key not in ("url", "wait_cookie", "timeout", "interval", "user_data_dir"):
                    issues.append(_issue('warning', 'unknown_field', f"{label}.refresh.{key} is unknown, will be ignored", file=file, adapter=adapter, path=f"{label}.refresh.{key}"))
    # refresh_interval
    refresh_block = cookie.get("refresh")
    ri = None
    if isinstance(refresh_block, dict):
        ri = refresh_block.get("interval", cookie.get("refresh_interval"))
    else:
        ri = cookie.get("refresh_interval")
    if ri is not None and (not isinstance(ri, (int, float)) or ri <= 0):
        issues.append(_issue('error', 'cookie_block_error', f"{label}.refresh.interval must be a positive number", file=file, adapter=adapter, path=f"{label}.refresh.interval"))
    # warn about unknown keys at cookie level
    for key in cookie:
        if key not in ("enabled", "type", "verify", "refresh", "help"):
            issues.append(_issue('warning', 'unknown_field', f"{label}.{key} is unknown, will be ignored", file=file, adapter=adapter, path=f"{label}.{key}"))


def _validate_auth_block(issues: list[dict], label: str, auth: dict, file_dir: str, file: str | None = None, adapter: str | None = None):
    """Validate an auth config block."""
    if not isinstance(auth, dict):
        issues.append(_issue('error', 'auth_block_error', f"{label} must be an object", file=file, adapter=adapter, path=label))
        return
    # Validate cookie sub-block
    cookie = auth.get('cookie')
    if cookie is not None:
        _validate_cookie_block(issues, f"{label}.cookie", cookie, file_dir, file=file, adapter=adapter)
    # Validate headers sub-block
    headers = auth.get('headers')
    if headers is not None:
        if not isinstance(headers, dict):
            issues.append(_issue('error', 'auth_block_error', f"{label}.headers must be an object", file=file, adapter=adapter, path=f"{label}.headers"))
        else:
            reserved = ('enabled', 'help', 'values')
            has_values = 'values' in headers
            if has_values:
                # Structured form: validate enabled, help, values
                if 'enabled' in headers and not isinstance(headers['enabled'], bool):
                    issues.append(_issue('error', 'auth_block_error', f"{label}.headers.enabled must be a boolean", file=file, adapter=adapter, path=f"{label}.headers.enabled"))
                if 'help' in headers and not isinstance(headers['help'], str):
                    issues.append(_issue('error', 'auth_block_error', f"{label}.headers.help must be a string", file=file, adapter=adapter, path=f"{label}.headers.help"))
                vals = headers.get('values')
                if vals is not None:
                    if not isinstance(vals, dict):
                        issues.append(_issue('error', 'auth_block_error', f"{label}.headers.values must be an object", file=file, adapter=adapter, path=f"{label}.headers.values"))
                    else:
                        for name, config in vals.items():
                            if not isinstance(name, str):
                                issues.append(_issue('error', 'auth_block_error', f"{label}.headers.values key must be a string", file=file, adapter=adapter, path=f"{label}.headers.values"))
                            if config is not None and not isinstance(config, str):
                                issues.append(_issue('error', 'auth_block_error', f"{label}.headers.values.{name} must be a string", file=file, adapter=adapter, path=f"{label}.headers.values.{name}"))
                # Warn about unknown keys outside values/enabled/help
                for key in headers:
                    if key not in reserved:
                        issues.append(_issue('warning', 'unknown_field', f"{label}.headers.{key} is unknown, will be ignored", file=file, adapter=adapter, path=f"{label}.headers.{key}"))
            else:
                # Flat form: enabled/help are reserved, rest are header names
                for name, config in headers.items():
                    if name in ('enabled', 'help'):
                        continue
                    if not isinstance(name, str):
                        issues.append(_issue('error', 'auth_block_error', f"{label}.headers key must be a string", file=file, adapter=adapter, path=f"{label}.headers"))
                    if config is not None and not isinstance(config, str):
                        issues.append(_issue('error', 'auth_block_error', f"{label}.headers.{name} must be a string", file=file, adapter=adapter, path=f"{label}.headers.{name}"))
    # Validate oauth2 sub-block
    oauth2 = auth.get("oauth2")
    if oauth2 is not None:
        prefix = f"{label}.oauth2"
        if not isinstance(oauth2, dict):
            issues.append(_issue('error', 'auth_block_error', f"{prefix} must be an object", file=file, adapter=adapter, path=prefix))
        else:
            endpoint = oauth2.get("endpoint")
            if not isinstance(endpoint, str) or not endpoint.strip():
                issues.append(_issue('error', 'auth_block_error', f"{prefix}.endpoint must be a non-empty string", file=file, adapter=adapter, path=f"{prefix}.endpoint"))
            for key in ("client_id", "client_secret", "grant_type", "format",
                         "access_token_path", "access_path",
                         "refresh_token_path", "refresh_path",
                         "expires_in_path", "expires_path",
                         "header", "header_format"):
                value = oauth2.get(key)
                if value is not None and not isinstance(value, str):
                    issues.append(_issue('error', 'auth_block_error', f"{prefix}.{key} must be a string", file=file, adapter=adapter, path=f"{prefix}.{key}"))
            extra_params = oauth2.get("extra_params")
            if extra_params is not None:
                if not isinstance(extra_params, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_params.items()):
                    issues.append(_issue('error', 'auth_block_error', f"{prefix}.extra_params must be a string map", file=file, adapter=adapter, path=f"{prefix}.extra_params"))
    # Validate basic_auth sub-block
    basic_auth = auth.get('basic_auth')
    if basic_auth is not None:
        if not isinstance(basic_auth, dict):
            issues.append(_issue('error', 'auth_block_error', f"{label}.basic_auth must be an object", file=file, adapter=adapter, path=f"{label}.basic_auth"))
    # Warn about unknown keys
    for key in auth:
        if key not in ('cookie', 'headers', 'oauth2', 'basic_auth'):
            issues.append(_issue('warning', 'unknown_field', f"{label}.{key} is unknown, will be ignored", file=file, adapter=adapter, path=f"{label}.{key}"))


def _validate_routes(issues: list[dict], label: str, routes, file_dir: str, file: str | None = None, adapter: str | None = None):
    """Validate a routes block."""
    if not isinstance(routes, dict):
        issues.append(_issue('error', 'routes_not_object',
            f"{label}.routes must be an object (pattern -> config)",
            file=file, adapter=adapter, path=f"{label}.routes"))
        return
    for pattern, route_config in routes.items():
        route_label = f'{label}.routes["{pattern}"]'
        # Validate regex
        try:
            re.compile(pattern)
        except re.error as e:
            issues.append(_issue('error', 'route_pattern_invalid',
                f"{route_label} invalid regex: {e}",
                file=file, adapter=adapter, path=route_label))
        # Validate route config structure
        if not isinstance(route_config, dict):
            issues.append(_issue('error', 'route_config_not_object',
                f"{route_label} must be an object",
                file=file, adapter=adapter, path=route_label))
            continue
        if route_config.get('type') == 'alias':
            issues.append(_issue('warning', 'route_alias_not_supported',
                f"{route_label}: alias type not supported in routes, will be ignored",
                file=file, adapter=adapter, path=route_label))
            continue
        # Recursively validate route config as domain config,
        # but disallow nested routes
        _validate_domain_config(issues, route_label, route_config, file_dir,
                                file=file, adapter=adapter, allow_routes=False)


def _validate_domain_config(issues: list[dict], label: str, data: dict, file_dir: str, file: str | None = None, adapter: str | None = None, allow_routes: bool = True):
    if not isinstance(data, dict):
        issues.append(_issue('error', 'domain_not_object', f"{label} top level must be an object", file=file, adapter=adapter, path=label))
        return
    if data.get('type') == 'alias':
        if not data.get('target'):
            issues.append(_issue('error', 'alias_missing_target', f"{label} alias missing target", file=file, adapter=adapter, path=label))
        return
    # Validate auth at top level (shared across all sections)
    top_auth = data.get('auth')
    if top_auth is not None:
        _validate_auth_block(issues, f"{label}.auth", top_auth, file_dir, file=file, adapter=adapter)

    # Validate defaults section
    default = data.get('defaults', {})
    if default:
        if not isinstance(default, dict):
            issues.append(_issue('error', 'section_not_object', f"{label}.defaults must be an object", file=file, adapter=adapter, path=f"{label}.defaults"))
        else:
            for key in default:
                if classify_field('defaults', key) == 'unknown':
                    issues.append(_issue('warning', 'unknown_field', f"{label}.defaults.{key} is unknown, will be ignored at runtime", file=file, adapter=adapter, path=f"{label}.defaults.{key}"))
                else:
                    _auto_type_check(issues, label, 'defaults', key, default[key], file, adapter)
            auth = default.get('auth')
            if auth is not None:
                _validate_auth_block(issues, f"{label}.defaults.auth", auth, file_dir, file=file, adapter=adapter)

    # Validate routes
    routes = data.get('routes')
    if routes is not None:
        if not allow_routes:
            issues.append(_issue('warning', 'routes_unexpected_location',
                f"{label}.routes is not supported here (only at domain level), will be ignored",
                file=file, adapter=adapter, path=f"{label}.routes"))
        else:
            _validate_routes(issues, label, routes, file_dir, file=file, adapter=adapter)

    # Validate sections
    for section in ('metadata', 'snapshot', 'reader', 'health'):
        sec = data.get(section, {})
        if not sec:
            continue
        if not isinstance(sec, dict):
            issues.append(_issue('error', 'section_not_object', f"{label}.{section} must be an object", file=file, adapter=adapter, path=f"{label}.{section}"))
            continue
        for key, value in sec.items():
            if classify_field(section, key) == 'unknown':
                issues.append(_issue('warning', 'unknown_field', f"{label}.{section}.{key} is unknown, will be ignored at runtime", file=file, adapter=adapter, path=f"{label}.{section}.{key}"))
            else:
                _auto_type_check(issues, label, section, key, value, file, adapter)
            if key == 'scripts':
                if not isinstance(value, list):
                    issues.append(_issue('error', 'scripts_not_array', f"{label}.{section}.scripts must be an array", file=file, adapter=adapter, path=f"{label}.{section}.scripts"))
                else:
                    replace_count = 0
                    seen_hooks = set()
                    for i, item in enumerate(value):
                        if not isinstance(item, dict):
                            issues.append(_issue('error', 'script_entry_not_object', f"{label}.{section}.scripts[{i}] must be an object", file=file, adapter=adapter, path=f"{label}.{section}.scripts[{i}]"))
                            continue
                        script_path = item.get('path', '')
                        hook_val = item.get('hook', '')
                        if hook_val not in ('before', 'after', 'replace'):
                            issues.append(_issue('error', 'script_hook_invalid', f"{label}.{section}.scripts[{i}].hook must be before/after/replace, got '{hook_val}'", file=file, adapter=adapter, path=f"{label}.{section}.scripts[{i}].hook"))
                        if hook_val == 'replace':
                            replace_count += 1
                        # Validate optional per-script timeout
                        script_timeout = item.get('timeout')
                        if script_timeout is not None:
                            if not isinstance(script_timeout, int) or isinstance(script_timeout, bool) or script_timeout <= 0:
                                issues.append(_issue('error', 'script_timeout_invalid', f"{label}.{section}.scripts[{i}].timeout must be a positive integer", file=file, adapter=adapter, path=f"{label}.{section}.scripts[{i}].timeout"))
                        if script_path:
                            if '/' not in script_path and not os.path.isabs(script_path):
                                script_path = os.path.join('scripts', script_path)
                            resolved = _resolve_path(script_path, file_dir) if not os.path.isabs(script_path) else script_path
                            if not os.path.exists(resolved):
                                issues.append(_issue('error', 'script_path_not_found', f"{label}.{section}.scripts[{i}].path not found: {item.get('path')}", file=file, adapter=adapter, path=f"{label}.{section}.scripts[{i}].path"))
                            elif not is_safe_script_path(resolved, file_dir):
                                issues.append(_issue('error', 'script_path_unsafe', f"{label}.{section}.scripts[{i}].path not allowed: {item.get('path')}", file=file, adapter=adapter, path=f"{label}.{section}.scripts[{i}].path"))
                    if replace_count > 1:
                        issues.append(_issue('error', 'replace_hook_duplicate', f"{label}.{section}.scripts has {replace_count} replace hooks, only 1 allowed", file=file, adapter=adapter, path=f"{label}.{section}.scripts"))
            if key.endswith('_script') or key == 'script':
                script_path = _resolve_path(value, file_dir) if isinstance(value, str) else ''
                if value and not isinstance(value, str):
                    issues.append(_issue('error', 'script_field_not_string', f"{label}.{section}.{key} must be a string path", file=file, adapter=adapter, path=f"{label}.{section}.{key}"))
                elif script_path and not os.path.exists(script_path):
                    issues.append(_issue('error', 'script_path_not_found', f"{label}.{section}.{key} script not found: {value}", file=file, adapter=adapter, path=f"{label}.{section}.{key}"))
                elif script_path and not is_safe_script_path(script_path, file_dir):
                    issues.append(_issue('error', 'script_path_unsafe', f"{label}.{section}.{key} script path not allowed: {value}", file=file, adapter=adapter, path=f"{label}.{section}.{key}"))
        auth = sec.get('auth')
        if auth is not None:
            _validate_auth_block(issues, f"{label}.{section}.auth", auth, file_dir, file=file, adapter=adapter)
        if section == 'metadata':
            xmlns = sec.get('xmlns')
            if xmlns is not None and (
                not isinstance(xmlns, dict)
                or not all(
                    isinstance(prefix, str)
                    and isinstance(uri, str)
                    and prefix
                    and uri
                    for prefix, uri in xmlns.items()
                )
            ):
                issues.append(
                    _issue('error', 'xmlns_invalid', f"{label}.metadata.xmlns must be a string-to-string prefix map", file=file, adapter=adapter, path=f"{label}.metadata.xmlns")
                )
            use_browser = sec.get('use_browser')
            if use_browser is not None:
                if not isinstance(use_browser, dict):
                    issues.append(_issue('error', 'use_browser_invalid', f"{label}.metadata.use_browser must be an object or null", file=file, adapter=adapter, path=f"{label}.metadata.use_browser"))
                else:
                    for sub_key, sub_val in use_browser.items():
                        if sub_key not in ('enabled', 'wait_until', 'wait_elements', 'timeout'):
                            issues.append(_issue('warning', 'unknown_field', f"{label}.metadata.use_browser.{sub_key} is unknown, will be ignored", file=file, adapter=adapter, path=f"{label}.metadata.use_browser.{sub_key}"))
                        elif sub_key == 'enabled' and not isinstance(sub_val, bool):
                            issues.append(_issue('error', 'use_browser_invalid', f"{label}.metadata.use_browser.enabled must be a boolean", file=file, adapter=adapter, path=f"{label}.metadata.use_browser.enabled"))
                        elif sub_key == 'wait_until' and sub_val not in ('domcontentloaded', 'load', 'networkidle'):
                            issues.append(_issue('error', 'use_browser_invalid', f"{label}.metadata.use_browser.wait_until must be one of domcontentloaded, load, networkidle", file=file, adapter=adapter, path=f"{label}.metadata.use_browser.wait_until"))
                        elif sub_key == 'wait_elements':
                            if isinstance(sub_val, str):
                                pass
                            elif isinstance(sub_val, list):
                                if not all(isinstance(s, str) for s in sub_val):
                                    issues.append(_issue('error', 'use_browser_invalid', f"{label}.metadata.use_browser.wait_elements must be a string or array of strings", file=file, adapter=adapter, path=f"{label}.metadata.use_browser.wait_elements"))
                            else:
                                issues.append(_issue('error', 'use_browser_invalid', f"{label}.metadata.use_browser.wait_elements must be a string or array of strings", file=file, adapter=adapter, path=f"{label}.metadata.use_browser.wait_elements"))
                        elif sub_key == 'timeout' and sub_val is not None:
                            if not isinstance(sub_val, int) or isinstance(sub_val, bool) or sub_val <= 0:
                                issues.append(_issue('error', 'use_browser_invalid', f"{label}.metadata.use_browser.timeout must be a positive integer or null", file=file, adapter=adapter, path=f"{label}.metadata.use_browser.timeout"))
        if section == 'snapshot':
            args = sec.get('singlefile_args', {})
            if args and not isinstance(args, dict):
                issues.append(_issue('error', 'section_not_object', f"{label}.snapshot.singlefile_args must be an object", file=file, adapter=adapter, path=f"{label}.snapshot.singlefile_args"))
            for arg in (args if isinstance(args, dict) else {}):
                if not is_known_singlefile_arg(arg):
                    issues.append(_issue('warning', 'singlefile_arg_unknown', f"{label}.snapshot.singlefile_args.{arg} unknown", file=file, adapter=adapter, path=f"{label}.snapshot.singlefile_args.{arg}"))
            scripts = sec.get('scripts')
            has_replace = (
                isinstance(scripts, list)
                and any(isinstance(item, dict) and item.get('hook') == 'replace' for item in scripts)
            )
            if has_replace:
                declarative_fields = [
                    'keep_elements', 'remove_elements', 'remove_classes',
                    'set_styles', 'singlefile_args', 'process_carousels',
                ]
                present = [
                    key for key in declarative_fields
                    if sec.get(key) not in (None, [], {})
                ]
                if present:
                    issues.append(
                        _issue('warning', 'replace_bypasses_declarative', f"{label}.{section}: replace script bypasses {', '.join(present)}", file=file, adapter=adapter, path=f"{label}.{section}")
                    )
        if section == 'reader':
            args = sec.get('defuddle_args', {})
            if args and not isinstance(args, dict):
                issues.append(_issue('error', 'section_not_object', f"{label}.reader.defuddle_args must be an object", file=file, adapter=adapter, path=f"{label}.reader.defuddle_args"))
            for arg in (args if isinstance(args, dict) else {}):
                if not is_known_defuddle_param(arg):
                    issues.append(_issue('warning', 'defuddle_arg_unknown', f"{label}.reader.defuddle_args.{arg} unknown", file=file, adapter=adapter, path=f"{label}.reader.defuddle_args.{arg}"))


def validate_config(base_dir: str, domain_filename: str = '') -> list[dict]:
    """Validate all adapter configs, returning a list of structured issue dicts."""
    issues = []
    if not os.path.isdir(base_dir):
        issues.append(_issue('error', 'dir_not_found', f"Directory not found: {base_dir}", path=base_dir))
        return issues

    adapters_dir = os.path.join(base_dir, 'adapters')

    if not domain_filename:
        config_path = os.path.join(adapters_dir, 'config.jsonc')
        if os.path.exists(config_path):
            try:
                config_data = load_jsonc_file(config_path)
                if not isinstance(config_data, dict):
                    issues.append(_issue('error', 'config_not_object', "config.jsonc top level must be an object", file=config_path))
                else:
                    _validate_subscriptions(issues, config_data.get('_adapters'), file=config_path, adapters_dir=adapters_dir)
            except json.JSONDecodeError as e:
                issues.append(_issue('error', 'config_parse_error', f"config.jsonc parse failed: {e}", file=config_path))

        if os.path.isdir(adapters_dir):
            adapters_list = []
            if os.path.exists(config_path):
                try:
                    cfg = load_jsonc_file(config_path)
                    if isinstance(cfg, dict):
                        adapters_list = cfg.get('_adapters', [])
                except Exception:
                    pass
            if not isinstance(adapters_list, list):
                adapters_list = []

            for item in adapters_list:
                if not isinstance(item, dict):
                    continue
                if item.get('enabled') is False:
                    continue
                name = item.get('name', '')
                source = item.get('source', '')
                adapter_id = item.get('id', '')
                adapter_label = f"{adapter_id}.{name}" if adapter_id else name
                if not source:
                    issues.append(_issue('warning', 'adapter_missing_source', f"Adapter missing source: {name}", path=name))
                    continue
                from site_adapters.services.subscriptions import resolve_adapter_path
                file_path = resolve_adapter_path(name, source, adapters_dir, adapter_id=adapter_id)

                if not os.path.exists(file_path):
                    issues.append(_issue('warning', 'adapter_file_not_found', f"Adapter file not found: {name}", file=file_path, adapter=adapter_label))
                    continue

                try:
                    data, dup_warnings = load_jsonc_file_with_warnings(file_path)
                    # Report duplicate keys
                    for w in dup_warnings:
                        issues.append(_issue(
                            'warning', 'duplicate_key',
                            f"Duplicate key '{w['key']}' appears {w['count']} times (last value used)",
                            file=file_path, adapter=adapter_label, path=w['key'],
                        ))
                    if not isinstance(data, dict):
                        issues.append(_issue('error', 'adapter_file_not_object', f"{name} top level must be an object", file=file_path, adapter=adapter_label))
                        continue
                    domains = data.get('domains', {})
                    if isinstance(domains, dict):
                        for domain_key, domain_config in domains.items():
                            label = f"{name}/{domain_key}"
                            _validate_domain_config(issues, label, domain_config, os.path.dirname(file_path), file=file_path, adapter=adapter_label)
                    glob_defaults = data.get('defaults')
                    global_defaults_data = data.get('_builtin')
                    if glob_defaults and isinstance(glob_defaults, dict):
                        _validate_domain_config(issues, f"{name}.defaults", glob_defaults, os.path.dirname(file_path), file=file_path, adapter=adapter_label, allow_routes=False)
                    if global_defaults_data and isinstance(global_defaults_data, dict):
                        _validate_domain_config(issues, f"{name}._builtin", global_defaults_data, os.path.dirname(file_path), file=file_path, adapter=adapter_label, allow_routes=False)
                except json.JSONDecodeError as e:
                    issues.append(_issue('error', 'config_parse_error', f"{name} parse failed: {e}", file=file_path, adapter=adapter_label))
    else:
        from site_adapters.services.subscriptions import _read_subscription_file, resolve_adapter_path
        adapters_list = []
        config_path = os.path.join(adapters_dir, 'config.jsonc')
        if os.path.exists(config_path):
            try:
                cfg = load_jsonc_file(config_path)
                if isinstance(cfg, dict):
                    adapters_list = cfg.get('_adapters', [])
            except Exception:
                pass
        if not isinstance(adapters_list, list):
            adapters_list = []

        found = False
        for item in adapters_list:
            if not isinstance(item, dict) or item.get('enabled') is False:
                continue
            source = item.get('source', '')
            if not source:
                continue
            file_path = resolve_adapter_path(item.get('name', ''), source, adapters_dir, item.get('id', ''))
            data = _read_subscription_file(file_path)
            if data and isinstance(data.get('domains'), dict) and domain_filename in data['domains']:
                _validate_domain_config(issues, domain_filename, data['domains'][domain_filename], os.path.dirname(file_path), file=file_path)
                found = True
                break
        if not found:
            issues.append(_issue('error', 'domain_not_found', f"Domain not found: {domain_filename}", path=domain_filename))

    return issues
