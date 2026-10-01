"""
Cookie 凭据管理（用户 / 共享加密存储）

Cookie 只存在于加密凭据系统中（用户 > 共享），不再使用 per-domain 持久文件。
refresh_cookie_declarative 使用系统临时文件作为 Node 子进程 I/O 载体，用完即清理。
冷却期：内存 dict（不持久化，60 秒）
验证：auth.cookie.verify.http_head_probe (L1) + content_check (L2, regex)
刷新：auth.cookie.refresh + 冷却期 → 写回用户或共享凭据
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
import time

from bookmarks.utils import atomic_write
from site_adapters.services.execution_log import log_execution

from publicsuffixlist import PublicSuffixList

from site_adapters.services.auth.credentials import (
    get_shared_cookie, _mark_cookie_expired,
    _resolve_credential_domain, _resolve_shared_credential_domain,
)

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 60  # 60 秒冷却期
MAX_COOLDOWN_ENTRIES = 1000

# 内存冷却期（不持久化）
_cooldowns: dict[str, float] = {}

# ---------------------------------------------------------------------------
# 声明式 cookie 默认值
# ---------------------------------------------------------------------------
COOKIE_DEFAULTS = {
    "enabled": True,
    "type": "auto",
    "refresh": {
        "url": "",
        "wait_cookie": "",
        "timeout": 30,          # 秒
        "interval": 14400,      # 秒
    },
    "verify": {
        "http_head_probe": {
            "enabled": True,
            "url": "",
            "timeout": 5,       # 秒
            "invalid_status": [401, 403],
            "invalid_location_patterns": [],
            "set_cookie_cleared": True,
        },
        "content_check": {
            "enabled": True,
            "url": "",
            "check_selectors": ["title", "body"],
            "valid_patterns": [],
            "valid_selectors": [],
            "invalid_patterns": [],
            "invalid_selectors": [],
        },
    },
}


def merge_cookie(base: dict, override: dict) -> dict:
    """Deep-merge two cookie config dicts recursively.

    Scalars and lists are replaced; dicts are merged recursively.
    None values in override are ignored (use enabled: false to disable).
    """
    if not base:
        return dict(override) if override else {}
    if not override:
        return dict(base)
    result = dict(base)
    for key, value in override.items():
        if value is None:
            continue
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_cookie(result[key], value)
        else:
            result[key] = value
    return result


# cookie 文件路径不再使用；Cookie 只存在于加密凭据系统（用户 / 共享）中

def _load_cookie_data(path: str) -> list | dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _cookie_data_to_string(data) -> str | None:
    """Convert Playwright cookie list to header string."""
    if isinstance(data, list):
        pairs = []
        for item in data:
            if isinstance(item, dict) and item.get("name") and "value" in item:
                pairs.append(f"{item['name']}={item['value']}")
        return "; ".join(pairs) if pairs else None
    if isinstance(data, str):
        return data
    return None


def _filter_cookies_for_domain(cookies: list, domain_key: str) -> list:
    """Keep only cookies that apply to the adapter's target domain.

    Browser contexts are shared across sites, so a full ``context.cookies()``
    dump can contain credentials for unrelated domains. Persist only cookies
    whose domain matches the target host or its wildcard parent.
    """
    if not cookies or not domain_key:
        return cookies or []

    host = domain_key.removeprefix('*.').lower()
    wildcard = domain_key.startswith('*.')
    filtered = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        raw_domain = cookie.get('domain')
        if not isinstance(raw_domain, str):
            continue
        cookie_domain = raw_domain.lstrip('.').lower()
        if wildcard:
            if cookie_domain == host or cookie_domain.endswith('.' + host):
                filtered.append(cookie)
        elif cookie_domain == host or host.endswith('.' + cookie_domain):
            filtered.append(cookie)
    return filtered


def _save_cookie_data(path: str, data):
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))



def _filter_expired(cookies: list) -> list:
    """剔除已过期的 cookie 条目 (L0 过期检查)。

    Playwright cookie 格式中使用 expires 字段（Unix 时间戳，秒）。
    没有 expires 字段的视为 session cookie，保留。
    """
    if not cookies:
        return cookies
    now = time.time()
    result = []
    expired = 0
    for c in cookies:
        exp = c.get('expires')
        if exp is not None and isinstance(exp, (int, float)) and exp <= now:
            expired += 1
            continue
        result.append(c)
    if expired:
        logger.debug('Filtered %d expired cookies', expired)
    return result


def _derive_cookie_domain(domain_key: str) -> str:
    """从 domain key 推导 cookie 的 domain 字段。

    用户粘贴的 cookie 通常设在父域名上（如 .zhihu.com），
    而非精确的子域名（如 www.zhihu.com）。
    使用 publicsuffixlist 精确推导公共后缀，回退到取最后两级。
    """
    host = domain_key.removeprefix('*.')
    try:
        psl = PublicSuffixList()
        registrable = psl.privatesuffix(host)
        if registrable:
            return f'.{registrable}'
    except Exception:
        logger.debug('publicsuffixlist failed for %s, falling back', domain_key)
    # 回退：取最后两级
    parts = host.split('.')
    parent = '.'.join(parts[-2:]) if len(parts) >= 2 else host
    return f'.{parent}'



def cookie_string_to_playwright_list(cookie_str: str, domain_key: str) -> list[dict]:
    """Convert a 'name=value; name2=value2' cookie string to Playwright format list.

    Shared utility to avoid duplicating this conversion in cookies,
    credentials, and the metadata browser loader.

    Supports optional domain= key in the pasted string::

        z_c0=abc; d_c0=def; domain=.example.com
    """
    cookies_list = []
    # 提取显式 domain= 字段
    explicit_domain = None
    remaining = cookie_str
    import re as _re
    m = _re.search(r'(?:^|;\s*)domain=([^;]+)', cookie_str, _re.IGNORECASE)
    if m:
        explicit_domain = m.group(1).strip()
        remaining = _re.sub(r'(?:^|;\s*)domain=[^;]+;?\s*', '', cookie_str, flags=_re.IGNORECASE).strip()
    cookie_domain = explicit_domain or _derive_cookie_domain(domain_key)
    for pair in remaining.split(';'):
        pair = pair.strip()
        if '=' in pair:
            name, value = pair.split('=', 1)
            cookies_list.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": cookie_domain,
                "path": "/",
            })
    return cookies_list


def load_cookie_file(path: str) -> str | None:
    """加载 cookie 文件（仅用于临时文件读取，不再作为持久存储）。
    
    仅供 refresh_cookie_declarative 内部使用，不应在外部调用。
    """
    data = _load_cookie_data(path)
    if not data:
        return None
    if isinstance(data, list):
        data = _filter_expired(data)
        if not data:
            return None
    return _cookie_data_to_string(data)



# ---------------------------------------------------------------------------
# Cooldown (in-memory)
# ---------------------------------------------------------------------------

def _is_in_cooldown(domain_key: str) -> bool:
    return time.monotonic() < _cooldowns.get(domain_key, 0)


def _evict_stale_cooldowns():
    """Remove expired cooldown entries to prevent unbounded memory growth."""
    now = time.monotonic()
    expired = [k for k, v in _cooldowns.items() if v <= now]
    for k in expired:
        _cooldowns.pop(k, None)


def _set_cooldown(domain_key: str):
    # Periodically evict stale entries to prevent unbounded growth
    if len(_cooldowns) >= MAX_COOLDOWN_ENTRIES:
        _evict_stale_cooldowns()
    _cooldowns[domain_key] = time.monotonic() + COOLDOWN_SECONDS


def _clear_cooldown(domain_key: str):
    _cooldowns.pop(domain_key, None)


# ---------------------------------------------------------------------------
# 域名级刷新互斥（防批量并发各自启动浏览器）
# ---------------------------------------------------------------------------

_domain_refresh_locks: dict[str, threading.Lock] = {}
_domain_refresh_locks_guard = threading.Lock()
# 每域最近一次浏览器刷新完成的时间戳（进程内，仅用于并发复用判断）
_last_refresh_time: dict[str, float] = {}
# 复用窗口：该域在此窗口内刚完成过刷新 → 等待锁的线程直接复用结果，
# 不再启动浏览器；超出窗口则视为需要重新刷新（如旧 cookie 失效的 force_refresh）
REFRESH_REUSE_WINDOW_SEC = 300


def _domain_refresh_lock(domain_key: str) -> threading.Lock:
    """按域名返回刷新互斥锁（惰性创建，进程内共享）。

    批量健康检查 / 多线程 pipeline 同时命中 cookie 失效时，
    同一域名只允许一个线程启动浏览器刷新，其余线程在锁内
    复用刷新结果，避免 N 个并发各开一个浏览器。
    """
    with _domain_refresh_locks_guard:
        lock = _domain_refresh_locks.get(domain_key)
        if lock is None:
            lock = threading.Lock()
            _domain_refresh_locks[domain_key] = lock
        return lock


# ---------------------------------------------------------------------------
# Verification (declarative)
# ---------------------------------------------------------------------------

def _verify_cookie_l1_probe(verify_config: dict, url: str,
                              cookie_str: str, domain_key: str,
                              timeout: int = 5) -> dict:
    """L1 HTTP HEAD 探针：HEAD 请求检测 Cookie 有效性。

    检查项：
    - 响应状态码（是否在 invalid_status 列表中，如 401/403）
    - Location 头（是否重定向到 login / signin 等页面）
    - Set-Cookie 头（是否被服务端清空/覆盖）

    返回 {"valid": bool, "reason": str}。
    网络错误时返回 {"valid": True, "reason": "http_head_probe_error"}，不阻塞后续 L2 检查。
    """
    probe_cfg = verify_config.get('http_head_probe', {})
    if not probe_cfg or not probe_cfg.get('enabled', True):
        return {"valid": True, "reason": "http_head_probe_disabled"}

    invalid_status = probe_cfg.get('invalid_status', [401, 403])
    import re as _l1_re
    raw_patterns = probe_cfg.get('invalid_location_patterns', [])
    invalid_location = [_l1_re.compile(p, _l1_re.IGNORECASE) for p in raw_patterns]
    check_set_cookie = probe_cfg.get('set_cookie_cleared', True)
    probe_timeout = probe_cfg.get('timeout', timeout)

    if not invalid_status and not invalid_location and not check_set_cookie:
        return {"valid": True, "reason": "no_http_head_probe_checks"}

    try:
        import requests as _requests
        headers = {'Cookie': cookie_str} if cookie_str else {}
        resp = _requests.head(
            url,
            headers=headers,
            allow_redirects=False,
            timeout=probe_timeout,
        )
    except Exception as e:
        logger.debug("L1 http_head_probe network error for %s: %s", domain_key, e)
        return {"valid": True, "reason": "http_head_probe_error"}

    # 1. 检查状态码
    if resp.status_code in invalid_status:
        return {"valid": False,
                "reason": f"L1: invalid status {resp.status_code}"}

    # 2. 检查 Location 重定向目标
    if invalid_location and 'Location' in resp.headers:
        location = resp.headers['Location']
        matched = next((p for p in invalid_location if p.search(location)), None)
        if matched:
            return {"valid": False,
                    "reason": f'L1: redirect to "{matched}"'}

    # 3. 检查 Set-Cookie 是否在清空已有 cookie
    if check_set_cookie and 'Set-Cookie' in resp.headers:
        set_cookie_val = resp.headers['Set-Cookie']
        if _is_cookie_being_cleared(set_cookie_val, cookie_str):
            return {"valid": False,
                    "reason": "L1: Set-Cookie clearing detected"}

    logger.debug("L1 http_head_probe passed for %s (status=%d)", domain_key, resp.status_code)
    return {"valid": True, "reason": f"L1: status={resp.status_code}"}


def _is_cookie_being_cleared(set_cookie_header: str, current_cookie_str: str) -> bool:
    """检测 Set-Cookie 响应头是否在清空/过期当前使用的 cookie。

    判断标准：
    - Set-Cookie 设置为空值或 deleted
    - Set-Cookie 的 Max-Age=0 或 expires 为过去时间
    - 且 cookie name 与当前使用的 cookie 中的某个 name 匹配
    """
    if not current_cookie_str:
        return False

    current_names = set()
    for pair in current_cookie_str.split(';'):
        pair = pair.strip()
        if '=' in pair:
            current_names.add(pair.split('=', 1)[0].strip().lower())

    # 解析 Set-Cookie（可能有多个，用逗号分隔）
    set_cookie_lower = set_cookie_header.lower()
    for name in current_names:
        if name in set_cookie_lower:
            # 检查是否在清空
            if f'{name}=' in set_cookie_lower:
                # 提取该 cookie 的值部分
                import re as _re
                pattern = _re.compile(
                    rf'{_re.escape(name)}=([^;]*)', _re.IGNORECASE
                )
                match = pattern.search(set_cookie_header)
                if match:
                    value = match.group(1).strip()
                    if value == '' or value.lower() == 'deleted':
                        return True
                if 'max-age=0' in set_cookie_lower:
                    return True
    return False


def verify_cookie_declarative(verify_config: dict, context: dict) -> dict:
    """
    声明式 cookie 验证。
    verify_config: content_check sub-config {check_selectors, invalid_patterns, invalid_selectors}
    context: {url, title, body_preview, html_path}
    返回: {valid: bool, reason: str}
    """
    # Support both new nested (content_check) and old flat format
    cc = verify_config.get("content_check", verify_config)
    check_selectors = cc.get("check_selectors", cc.get("check", ["title", "body"]))
    valid_patterns = cc.get("valid_patterns", [])
    valid_selectors = cc.get("valid_selectors", cc.get("valid_selector", []))
    invalid_patterns = cc.get("invalid_patterns", [])
    invalid_selectors = cc.get("invalid_selectors", cc.get("invalid_selector", []))

    import re as _cc_re

    # ---- 正向验证：命中 → 有效（短路） ----

    # 1a. 正向 CSS 选择器
    if valid_selectors and context.get("html_path"):
        try:
            from bs4 import BeautifulSoup
            with open(context["html_path"], encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            for sel in valid_selectors:
                if soup.select_one(sel):
                    return {"valid": True,
                            "reason": f'valid selector matched: "{sel}"'}
        except Exception:
            pass

    # 1b. 正向文本模式（title / body）
    if valid_patterns:
        try:
            vpatterns = [_cc_re.compile(p, _cc_re.IGNORECASE) for p in valid_patterns]
        except _cc_re.error as e:
            logger.warning("Invalid regex in valid_patterns: %s", e)
        else:
            text_parts = []
            if "title" in check_selectors and context.get("title"):
                text_parts.append(context["title"])
            if "body" in check_selectors:
                text_parts.append(context.get("body_preview", ""))
            combined = " ".join(text_parts)
            matched = next((p for p in vpatterns if p.search(combined)), None)
            if matched:
                return {"valid": True,
                        "reason": f'valid pattern matched: "{matched.pattern}"'}

    # ---- 反向验证：命中 → 失效 ----

    # 2a. 反向 CSS 选择器
    if invalid_selectors and context.get("html_path"):
        try:
            from bs4 import BeautifulSoup
            with open(context["html_path"], encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            for sel in invalid_selectors:
                if soup.select_one(sel):
                    return {"valid": False,
                            "reason": f'invalid selector matched: "{sel}"'}
        except Exception:
            pass

    # 2b. 反向文本模式
    if not invalid_patterns:
        return {"valid": True, "reason": "no invalid patterns configured"}

    try:
        ipatterns = [_cc_re.compile(p, _cc_re.IGNORECASE) for p in invalid_patterns]
    except _cc_re.error as e:
        logger.warning("Invalid regex in invalid_patterns: %s", e)
        return {"valid": True, "reason": "invalid regex config"}

    if "title" in check_selectors and context.get("title"):
        title_text = context["title"]
        matched = next((p for p in ipatterns if p.search(title_text)), None)
        if matched:
            return {"valid": False,
                    "reason": f'title matches "{matched.pattern}"'}

    if "body" in check_selectors:
        text = context.get("body_preview", "")
        if not text and context.get("html_path"):
            try:
                with open(context["html_path"], encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                pass
        matched = next((p for p in ipatterns if p.search(text)), None)
        if matched:
            return {"valid": False,
                    "reason": f'body matches "{matched.pattern}"'}

    return {"valid": True, "reason": "ok"}


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def _should_refresh_cookie(cookie_config: dict) -> bool:
    """Return whether browser-based cookie refresh is allowed.

    ``auto`` cookies may always refresh anonymously. ``login`` cookies must
    opt in explicitly with ``refresh.url`` or ``refresh.wait_cookie``;
    otherwise the default empty refresh block must not overwrite a
    user-supplied login session with anonymous cookies.
    """
    refresh = cookie_config.get('refresh') or {}
    if cookie_config.get('type', 'auto') == 'login':
        return bool(refresh.get('url') or refresh.get('wait_cookie'))
    return bool(refresh)


def refresh_cookie_declarative(refresh_config: dict, url: str,
                                domain_key: str) -> list | None:
    """
    声明式 cookie 刷新。使用内置 refresh_cookies.js。
    通过临时文件与 Node 子进程交换数据，返回 Playwright 格式 cookie 列表。

    refresh_config: {url, wait_cookie, timeout}
    内置冷却期机制：失败后 60 秒内不再尝试。
    返回 cookie 列表或 None。
    """
    script_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'engine', 'scripts', 'refresh_cookies.js',
    )
    if not os.path.exists(script_path):
        logger.error("Built-in refresh script not found: %s", script_path)
        return None

    if _is_in_cooldown(domain_key):
        logger.info("Cookie refresh in cooldown, skipping: %s", domain_key)
        return None

    from django.conf import settings as django_settings
    # 优先使用配置的路径，其次尝试系统发现
    chromium_path = getattr(django_settings, 'LD_BROWSER_CHROMIUM_PATH', '') or os.getenv('CHROMIUM_PATH', '')
    if not chromium_path:
        try:
            from site_adapters.services.engine.browser_provider import _find_chromium_path
            chromium_path = _find_chromium_path()
        except Exception:
            pass
    refresh_url = refresh_config.get('url') or url
    raw_wait = refresh_config.get('wait_cookie', '')
    wait_cookie = raw_wait if isinstance(raw_wait, list) else ([raw_wait] if raw_wait else [])
    timeout = int(refresh_config.get('timeout', 30) * 1000)  # s → ms for Playwright
    license_key = getattr(django_settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_KEY', '')

    # Resolve persistent profile directory (optional).
    # "default" → {BASE_DIR}/chromium-profile (same path SingleFile uses).
    # Relative paths resolve against BASE_DIR; absolute paths used as-is.
    raw_profile = refresh_config.get('user_data_dir', '')
    user_data_dir = ''
    if raw_profile:
        if raw_profile == 'default':
            base_dir = getattr(django_settings, 'BASE_DIR', '')
            user_data_dir = os.path.join(base_dir, 'chromium-profile') if base_dir else ''
        elif os.path.isabs(raw_profile):
            user_data_dir = raw_profile
        else:
            base_dir = getattr(django_settings, 'BASE_DIR', '')
            user_data_dir = os.path.join(base_dir, raw_profile) if base_dir else raw_profile

    # 使用临时文件作为 Node 子进程的 I/O 载体
    tmp_path = None
    try:
        tmp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
        tmp_path = tmp_file.name
        json.dump([], tmp_file)
        tmp_file.close()

        start = time.monotonic()
        input_data = {
            'url': refresh_url,
            'cookie_file': tmp_path,
            'outputPath': tmp_path,
            'wait_cookie': wait_cookie,
            'waitCookie': wait_cookie,
            'chromium_path': chromium_path,
            'timeout': timeout,
            'licenseKey': license_key,
            'user_data_dir': user_data_dir,
        }
        cmd = ['node', script_path]
        subprocess_timeout = max(timeout / 1000 + 30, 60)
        result = subprocess.run(
            cmd,
            input=json.dumps(input_data),
            capture_output=True, text=True, timeout=subprocess_timeout,
            env={**os.environ, 'LD_BROWSER_ENGINE': getattr(django_settings, 'LD_BROWSER_ENGINE', 'cloakbrowser'),
                 'CLOAKBROWSER_LICENSE_KEY': license_key},
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        log_execution(
            url=url,
            domain_key=domain_key,
            step='cookie_refresh',
            cmd=cmd,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            config_snapshot={'wait_cookie': raw_wait},
        )

        if result.returncode == 0:
            _clear_cooldown(domain_key)
            data = _load_cookie_data(tmp_path)
            if data and isinstance(data, list):
                data = _filter_expired(data)
                data = _filter_cookies_for_domain(data, domain_key)
            logger.info("Cookie refresh succeeded: %s (%d cookies)", domain_key, len(data) if data else 0)
            return data if data else []
        else:
            _set_cooldown(domain_key)
            logger.error("Cookie refresh failed: %s: %s", domain_key, result.stderr[:200])
            return None
    except Exception as e:
        _set_cooldown(domain_key)
        logger.error("Cookie refresh error: %s: %s", domain_key, e)
        log_execution(
            url=url,
            domain_key=domain_key,
            step='cookie_refresh',
            cmd=['node', script_path],
            returncode=1,
            stderr=str(e),
        )
        return None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Verify + refresh flow (declarative)
# ---------------------------------------------------------------------------

def _mark_login_cookie_expired(source: str | None, username: str,
                                 domain_key: str, scope: str):
    """Mark a login-type cookie as expired in credential metadata.

    Only applies to login-type cookies (auto cookies auto-refresh, so they
    should never reach this state). Writes cookie_status='invalid' and
    expired_at timestamp so the cookie won't be used for future requests
    until the user re-saves it.

    Resolves the actual stored domain (may differ from domain_key due to
    wildcard/DNS fallback) so the metadata key matches the credential file.
    """
    # source is only set when a cookie was actually loaded; skip if None
    if not source:
        return
    if source == 'user' and username:
        cred_source = username
        matched = _resolve_credential_domain(username, domain_key)
    else:
        cred_source = 'shared'
        matched = _resolve_shared_credential_domain(domain_key)
    if not matched:
        matched = domain_key
    try:
        _mark_cookie_expired(cred_source=cred_source, domain=matched, scope=scope)
        logger.info("Cookie marked as expired for %s (source=%s, scope=%s)",
                    matched, cred_source, scope or 'domain')
    except Exception as e:
        logger.error("Failed to mark cookie expired for %s: %s", domain_key, e)


def verify_and_refresh(*, cookie_config: dict, url: str, domain_key: str,
                       verify_context: dict, username: str = '',
                       scope: str = '', force_refresh: bool = False) -> str | None:
    """
    完整的 cookie 验证 + 刷新流程。

    凭据优先级：用户凭据 > 共享凭据（在同一 scope 内）。
    刷新后写回对应的凭据存储（用户或共享），使用正确的 scope 文件。
    cookie_config: 完整的 cookie 配置块（已合并）
    scope: effective scope ('' for domain-level, section name for section-level)
    返回 cookie 字符串（可能为 None）。

    force_refresh: 即使已有 cookie，也先执行一次浏览器刷新（用于 replace
    hook 返回空结果、现有 cookie 可能失效的场景）。
    """
    from site_adapters.services.auth.credentials import (
        get_best_cookie, save_user_cookie, save_shared_cookie,
        get_user_cookie, get_shared_cookie,
    )
    from urllib.parse import urlparse

    # Credentials are stored per concrete host (DNS fallback resolves wildcard
    # directories), so lookup with the request hostname instead of the
    # wildcard domain key which would otherwise never match.
    lookup_hostname = (urlparse(url).hostname or domain_key).lower()

    # 判断凭据来源
    cookie_str = None
    source = None  # 'user' | 'shared' | None

    if username:
        cookie_str, status = get_user_cookie(
            username=username, hostname=lookup_hostname, scope=scope)
        if cookie_str and status == 'ok':
            source = 'user'

    if not cookie_str:
        cookie_str, status = get_shared_cookie(
            hostname=lookup_hostname, scope=scope)
        if cookie_str and status == 'ok':
            source = 'shared'

    def _save_cookies(cookie_list: list):
        """将 cookie 列表保存到对应的凭据存储（使用正确的 scope）。"""
        if not cookie_list:
            return
        cookie_str_save = _cookie_data_to_string(cookie_list)
        if not cookie_str_save:
            return
        if source == 'user' and username:
            save_user_cookie(username=username, domain=domain_key,
                             cookie_str=cookie_str_save, scope=scope, source='auto')
        else:
            save_shared_cookie(domain=domain_key, cookie_str=cookie_str_save,
                               scope=scope, source='auto')

    def _peek_cookie() -> str | None:
        """锁内复查当前 scope 凭据（等待锁期间其他线程可能已刷新）。"""
        if source == 'user' and username:
            c, _ = get_user_cookie(username=username, hostname=lookup_hostname, scope=scope)
            if c:
                return c
        c, _ = get_shared_cookie(hostname=lookup_hostname, scope=scope)
        return c

    def _refresh_locked() -> str | None:
        """域名锁内刷新：并发只启动一次浏览器，其余线程复用刷新结果。

        复用仅发生在该域刚完成过浏览器刷新（窗口期内）——等待锁期间
        其他线程刚刷新的结果可直接使用；旧 cookie 不满足复用条件，
        force_refresh 等场景仍会真正启动浏览器换新。
        """
        key = domain_key or lookup_hostname
        with _domain_refresh_lock(key):
            now = time.monotonic()
            existing = _peek_cookie()
            if existing and (now - _last_refresh_time.get(key, 0)) < REFRESH_REUSE_WINDOW_SEC:
                return existing
            data = refresh_cookie_declarative(cookie_config['refresh'], url, domain_key)
            if not data:
                return None
            try:
                _save_cookies(data)
            except Exception as e:
                logger.error("Failed to save cookies to credentials for %s: %s", domain_key, e)
                return None
            _last_refresh_time[key] = now
            return _cookie_data_to_string(data)

    # Force refresh: replace hooks may return empty metadata even though a
    # cookie exists (e.g. the server rejected a just-acquired token).
    if force_refresh and _should_refresh_cookie(cookie_config):
        logger.info("Forcing cookie refresh for %s (scope=%s)",
                    domain_key, scope or 'domain')
        return _refresh_locked()

    # 没有 cookie 且有 refresh 配置 → 尝试刷新
    if not cookie_str and _should_refresh_cookie(cookie_config):
        logger.info("No cookie for %s (%s, scope=%s), attempting browser refresh",
                     domain_key, source or 'auto', scope or 'domain')
        refreshed = _refresh_locked()
        if refreshed:
            cookie_str = refreshed
            if not source:
                source = 'shared'
            return refreshed
        else:
            logger.warning("Cookie refresh failed for %s, no cookies acquired", domain_key)

    verify_cfg = cookie_config.get('verify', {})
    content_check_cfg = verify_cfg.get('content_check', {})
    invalid_patterns = content_check_cfg.get('invalid_patterns', content_check_cfg.get('invalid_selectors', []))

    # L0 已过（有 cookie 字符串），进入 L1 探针
    if cookie_str:
        probe_cfg = verify_cfg.get('http_head_probe', {})
        if probe_cfg and probe_cfg.get('enabled', True):
            l1_result = _verify_cookie_l1_probe(
                verify_cfg, url, cookie_str, domain_key)
            if not l1_result.get('valid'):
                logger.info("Cookie invalid (L1): %s: %s",
                            domain_key, l1_result.get("reason"))
                # L1 判定失效 → 如果配置了 refresh 就直接刷新，不再走 L2
                if _should_refresh_cookie(cookie_config):
                    logger.info("L1 invalid for %s, attempting browser refresh",
                                domain_key)
                    refreshed = _refresh_locked()
                    if refreshed:
                        logger.info("Cookie refreshed after L1 for %s (scope=%s)",
                                    domain_key, scope or 'domain')
                        return refreshed
                    else:
                        logger.warning("Cookie refresh (after L1) failed for %s",
                                       domain_key)
                        _mark_login_cookie_expired(source, username, domain_key, scope)
                else:
                    _mark_login_cookie_expired(source, username, domain_key, scope)
                return None

    # L2 content_check: skip if disabled or no checks configured
    if content_check_cfg.get('enabled', True) is False:
        logger.debug("L2 content_check disabled for %s", domain_key)
        return cookie_str

    invalid_selectors = content_check_cfg.get('invalid_selectors',
                          content_check_cfg.get('invalid_selector', []))
    valid_patterns = content_check_cfg.get('valid_patterns', [])
    valid_selectors = content_check_cfg.get('valid_selectors',
                        content_check_cfg.get('valid_selector', []))
    if not valid_patterns and not valid_selectors and not invalid_patterns and not invalid_selectors:
        return cookie_str

    # L2 页面内容验证
    verify_context.setdefault('domain_key', domain_key)
    result = verify_cookie_declarative(verify_cfg, verify_context)
    if result.get('valid'):
        return cookie_str

    logger.info("Cookie invalid: %s: %s", domain_key, result.get("reason"))

    # Refresh
    if _should_refresh_cookie(cookie_config):
        logger.info("Cookie invalid for %s, attempting browser refresh", domain_key)
        refreshed = _refresh_locked()
        if refreshed:
            logger.info("Cookie refreshed after L2 for %s (scope=%s)",
                        domain_key, scope or 'domain')
            return refreshed
        else:
            logger.warning("Cookie refresh (after verification) failed for %s", domain_key)
            _mark_login_cookie_expired(source, username, domain_key, scope)
    else:
        _mark_login_cookie_expired(source, username, domain_key, scope)

    return None


def copy_cookie_data_to_temp(data) -> str | None:
    """Copy Playwright cookie data (list or dict) directly to a temp file.

    Bypasses the string round-trip so original domain / path / sameSite
    metadata is preserved exactly as stored.
    """
    if not data:
        return None
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as tmp:
        json.dump(data, tmp)
        return tmp.name


def generate_temp_cookies_file(*, domain_key: str, cookie_str: str = None,
                               scope: str = '') -> str | None:
    """Generate a temporary Playwright cookies file.

    When a raw cookie string is given (e.g. user-pasted), it is converted
    to Playwright format.  When cookie_str is None, attempts to load from
    shared credentials in the given scope.

    Caller must delete the returned path after use.
    """
    if cookie_str is None:
        shared, status = get_shared_cookie(hostname=domain_key, scope=scope)
        if shared and status == 'ok':
            cookies_list = cookie_string_to_playwright_list(shared, domain_key)
            return copy_cookie_data_to_temp(cookies_list)
        return None
    # User-provided raw string → convert to Playwright format
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain_key)
    return copy_cookie_data_to_temp(cookies_list)

def copy_cookie_file_to_temp(path: str) -> str | None:
    """Copy a stored Playwright cookie file directly to a temp file."""
    data = _load_cookie_data(path)
    return copy_cookie_data_to_temp(data)
