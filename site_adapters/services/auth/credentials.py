"""
User credential management

Storage: credentials/users/{username}/{domain}/cookie.json (encrypted)
         credentials/users/{username}/{domain}/header.json (encrypted)
Metadata: credentials/encryption.meta (key fingerprint + domain index)

Section-level scope support:
  cookie.json              — domain-level (scope='')
  cookie_snapshot.json     — snapshot section (scope='snapshot')
  cookie_metadata.json     — metadata section (scope='metadata')
  cookie_reader.json       — reader section (scope='reader')
  Same pattern for header, token (oauth2), basic_auth, token_cache.

Credential resolution model (simplified):
  1. _build_section_config determines the effective scope per auth sub-type.
  2. Within a single scope, user > shared (2-level lookup).
  3. Cross-scope fallback (cookie only) is an explicit sequential step,
     gated by cookie type matching.
"""

import json
import logging
import os
import re
import threading
import time

from bookmarks.utils import atomic_write
from site_adapters.services.auth.crypto import (
    decrypt_or_plaintext,
    encrypt_value,
    get_key_fingerprint,
    is_encrypted,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scope constants
# ---------------------------------------------------------------------------

VALID_SCOPES = ('', 'metadata', 'snapshot', 'reader', 'health')

# Sections that can carry their own auth config.
_AUTH_SECTIONS = ('metadata', 'snapshot', 'reader', 'health')


def _validate_scope(scope: str) -> str:
    """Validate and return the scope, defaulting to '' (domain-level)."""
    if scope is None:
        return ''
    if scope not in VALID_SCOPES:
        raise ValueError(f'Invalid scope: {scope!r}; must be one of {VALID_SCOPES}')
    return scope


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Credential type → base filename (without .json suffix and without scope).
_CRED_FILENAMES = {
    'cookie': 'cookie',
    'header': 'header',
    'oauth2': 'token',       # OAuth2 refresh tokens stored as token.json
    'token': 'token',        # legacy alias
    'basic_auth': 'basic_auth',
    'token_cache': 'token_cache',
}


def _scoped_filename(*, cred_type: str, scope: str = '') -> str:
    """Build a credential filename with optional scope suffix.

    Examples:
      cred_type='cookie', scope=''           → 'cookie.json'
      cred_type='cookie', scope='snapshot'   → 'cookie_snapshot.json'
    """
    scope = _validate_scope(scope)
    base = _CRED_FILENAMES.get(cred_type, cred_type)
    if scope:
        return f'{base}_{scope}.json'
    return f'{base}.json'


def _get_credentials_dir() -> str:
    from site_adapters.services.base import _get_base_dir
    return os.path.join(_get_base_dir(), 'credentials')


# ── Shared credential path helpers ──

def _shared_credential_dir(domain: str) -> str:
    return os.path.join(_get_credentials_dir(), 'shared', domain)


def _shared_credential_path(*, domain: str, cred_type: str, scope: str = '') -> str:
    """Build a shared credential file path with scope support."""
    filename = _scoped_filename(cred_type=cred_type, scope=scope)
    return os.path.join(_shared_credential_dir(domain), filename)


# ── User credential path helpers ──

def _credential_path(*, username: str, domain: str, filename: str) -> str:
    """Build a credential file path without domain matching."""
    return os.path.join(_get_credentials_dir(), 'users', username, domain, filename)


def _resolve_domain_in_dir(base_dir: str, hostname: str) -> str | None:
    """Find the best matching domain directory in a given base directory.

    Multi-level DNS fallback: tries the full hostname first, then strips
    one subdomain at a time. Checks exact match, then wildcard forward
    (stored ``*.example.com`` matching candidate ``www.example.com``).

    Only returns directories that contain at least one credential file.
    """
    if not os.path.isdir(base_dir):
        return None

    stored = []
    for entry in os.listdir(base_dir):
        entry_path = os.path.join(base_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        try:
            if any(os.path.isfile(os.path.join(entry_path, f)) for f in os.listdir(entry_path)):
                stored.append(entry)
        except OSError:
            continue

    if not stored:
        return None

    bare_hostname = hostname[2:] if hostname.startswith('*.') else hostname
    parts = bare_hostname.split('.')
    candidates = []
    for i in range(len(parts) - 1):
        candidates.append('.'.join(parts[i:]))

    for candidate in candidates:
        if candidate in stored:
            return candidate

        best_wildcard = None
        best_depth = -1
        for entry in stored:
            if entry.startswith('*.'):
                suffix = entry[1:]
                if candidate.endswith(suffix):
                    depth = entry.count('.')
                    if depth > best_depth:
                        best_wildcard = entry
                        best_depth = depth
        if best_wildcard:
            return best_wildcard

    return None


def _resolve_credential_domain(username: str, hostname: str) -> str | None:
    """Resolve domain for user credentials (thin wrapper)."""
    return _resolve_domain_in_dir(
        os.path.join(_get_credentials_dir(), 'users', username), hostname
    )


def _resolve_shared_credential_domain(hostname: str) -> str | None:
    """Resolve domain for shared credentials (thin wrapper)."""
    return _resolve_domain_in_dir(
        os.path.join(_get_credentials_dir(), 'shared'), hostname
    )


def _get_cookie_expired_meta(*, cred_source: str, domain: str, scope: str = '') -> dict:
    """Read cookie expiry metadata from encryption.meta.

    cred_source: 'shared' or the username.
    Returns the meta entry dict (may be empty).
    """
    meta = _load_meta()
    key = _meta_key(cred_type='cookies', username=cred_source, domain=domain, scope=scope)
    return meta['credentials'].get(key, {})


def _mark_cookie_expired(*, cred_source: str, domain: str, scope: str = ''):
    """Mark a cookie as expired (invalid) in metadata."""
    _update_meta_entry(cred_type='cookies', username=cred_source, domain=domain,
                       scope=scope, cookie_status='invalid', expired_at=_now_iso())


def _clear_cookie_expired(*, cred_source: str, domain: str, scope: str = ''):
    """Clear cookie expiry metadata (e.g. after user re-saves cookie)."""
    with _meta_lock:
        meta = _load_meta()
        key = _meta_key(cred_type='cookies', username=cred_source, domain=domain, scope=scope)
        entry = meta['credentials'].get(key, {})
        entry.pop('cookie_status', None)
        entry.pop('expired_at', None)
        meta['credentials'][key] = entry
        _save_meta(meta)


def _resolve_credential_path(*, username: str, hostname: str, filename: str) -> str:
    """Build a credential file path with domain matching for reads."""
    matched = _resolve_credential_domain(username, hostname)
    effective_domain = matched if matched else hostname
    return _credential_path(username=username, domain=effective_domain, filename=filename)


def _get_meta_path() -> str:
    return os.path.join(_get_credentials_dir(), 'encryption.meta')


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

# Process-wide lock guarding load-modify-save cycles on encryption.meta.
# Prevents concurrent writers from clobbering each other's entries (e.g.
# losing an invalid/cookie_status mark or a source provenance tag).
_meta_lock = threading.RLock()


def _load_meta() -> dict:
    path = _get_meta_path()
    if not os.path.exists(path):
        return {'key_fingerprint': '', 'credentials': {}}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {'key_fingerprint': '', 'credentials': {}}
        data.setdefault('key_fingerprint', '')
        data.setdefault('credentials', {})
        return data
    except json.JSONDecodeError:
        # A single corrupt meta file must not take down every credential read
        # path. Preserve the evidence by moving the bad file aside, then start
        # fresh so callers degrade gracefully instead of crashing.
        backup = f'{path}.corrupt-{int(time.time())}'
        try:
            os.rename(path, backup)
            logger.warning("Corrupt encryption.meta renamed to %s", backup)
        except OSError as rename_err:
            logger.error("Failed to rename corrupt meta %s: %s", path, rename_err)
        return {'key_fingerprint': '', 'credentials': {}}
    except OSError:
        return {'key_fingerprint': '', 'credentials': {}}


def _save_meta(meta: dict):
    atomic_write(_get_meta_path(), json.dumps(meta, indent=2, ensure_ascii=False))


def _meta_key(*, cred_type: str, username: str, domain: str, scope: str = '') -> str:
    """Build the encryption metadata key with scope."""
    scope = _validate_scope(scope)
    return f'{cred_type}:{username}:{domain}:{scope}'


def _lookup_meta_domain_variant(meta: dict, *, cred_type: str, username: str,
                                domain: str, scope: str = '') -> dict:
    """Find a meta entry whose domain is a wildcard/suffix variant of `domain`.

    Used when the exact meta_key (built from the on-disk directory name)
    misses: the entry may have been written under the request hostname or a
    wildcard directory (e.g. file lives in `www.example.com/` but the
    cookie_status='invalid' mark was stored under `*.example.com`). Without
    this fallback the listing would silently drop that status.
    """
    key_prefix = f'{cred_type}:{username}:'
    key_suffix = f':{scope}'
    for key, entry in meta.get('credentials', {}).items():
        if not key.startswith(key_prefix) or not key.endswith(key_suffix):
            continue
        stored_domain = key[len(key_prefix):-len(key_suffix)] if len(key_suffix) else key[len(key_prefix):]
        if stored_domain == domain:
            continue
        if stored_domain.startswith('*.'):
            if domain.endswith(stored_domain[1:]):
                return entry
        elif domain.endswith('.' + stored_domain):
            return entry
    return {}


def _update_meta_entry(*, cred_type: str, username: str, domain: str,
                       scope: str = '', **fields):
    with _meta_lock:
        meta = _load_meta()
        # keep fingerprint current
        meta['key_fingerprint'] = get_key_fingerprint()
        key = _meta_key(cred_type=cred_type, username=username, domain=domain, scope=scope)
        entry = meta['credentials'].get(key, {})
        entry.update(fields)
        meta['credentials'][key] = entry
        _save_meta(meta)


def check_key_fingerprint() -> bool:
    """检查存储的 fingerprint 与当前密钥是否匹配。匹配返回 True。"""
    meta = _load_meta()
    stored = meta.get('key_fingerprint', '')
    if not stored:
        return True  # first use, no fingerprint
    return stored == get_key_fingerprint()


# ---------------------------------------------------------------------------
# File I/O (encryption layer)
# ---------------------------------------------------------------------------

def _read_encrypted_file(path: str) -> tuple[str | None, str]:
    """
    Read encrypted file. Returns (decrypted_content, status).
    Status: 'ok' | 'key_changed' | 'not_found' | 'error'
    """
    if not os.path.exists(path):
        return None, 'not_found'
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.read()
        if not raw.strip():
            return None, 'not_found'
        if not is_encrypted(raw):
            # legacy plaintext
            return raw, 'ok'
        result = decrypt_or_plaintext(raw)
        if result == raw and is_encrypted(raw):
            # decrypt failed, key changed
            return None, 'key_changed'
        return result, 'ok'
    except Exception:
        return None, 'error'


def _write_encrypted_file(path: str, content: str):
    """加密并写入文件（原子写入：先写临时文件再 rename）。"""
    encrypted = encrypt_value(content)
    atomic_write(path, encrypted)


def _remove_file(path: str):
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# Cookie credentials
# ---------------------------------------------------------------------------

def get_user_cookie(*, username: str, hostname: str, scope: str = '',
                    source: str | None = None) -> tuple[str | None, str]:
    """
    Get the user's cookie credential for a hostname in a given scope.

    Uses DNS multi-level fallback: "www.zhihu.com" -> "zhihu.com".

    Returns (cookie_string | None, status).
    Status: 'ok' | 'key_changed' | 'not_found' | 'error' | 'invalid'

    source: optional provenance filter. 'auto' only matches framework
    refresh-generated cookies; 'manual' matches user-provided cookies
    (legacy 'paste' entries are treated as manual). None means no filter.
    """
    filename = _scoped_filename(cred_type='cookie', scope=scope)
    path = _resolve_credential_path(username=username, hostname=hostname, filename=filename)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    # Check if cookie was previously marked as expired/invalid
    matched = _resolve_credential_domain(username, hostname)
    if matched:
        meta_entry = _get_cookie_expired_meta(cred_source=username, domain=matched, scope=scope)
        if meta_entry.get('cookie_status') == 'invalid':
            return None, 'invalid'
        if source is not None and not _source_matches(meta_entry.get('source'), source):
            return None, 'not_found'
    cookie_str = _json_cookie_to_header_string(content)
    return cookie_str, status


def save_user_cookie(*, username: str, domain: str, cookie_str: str,
                     scope: str = '', exact: bool = True, source: str = 'manual'):
    """Save the user's cookie credential (encrypted storage).

    By default uses exact domain without fallback matching to avoid
    accidentally overwriting a different domain's credentials.

    source: provenance marker — 'manual' (user-provided) or 'auto'
    (framework refresh). Defaults to 'manual'.
    """
    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain)
    cookie_content = json.dumps(cookies_list, ensure_ascii=False)
    filename = _scoped_filename(cred_type='cookie', scope=scope)
    if exact:
        effective_domain = domain
    else:
        # exact=False resolves the file into the eTLD+1 / wildcard directory;
        # key the meta entry by that same directory name so the cookie_status
        # written later by the refresh path lines up with what listing scans.
        matched = _resolve_credential_domain(username, domain)
        effective_domain = matched if matched else domain
    path = _credential_path(username=username, domain=effective_domain, filename=filename)
    _write_encrypted_file(path, cookie_content)
    _update_meta_entry(cred_type='cookies', username=username, domain=effective_domain,
                       scope=scope, updated_at=_now_iso(), source=source)
    _clear_cookie_expired(cred_source=username, domain=effective_domain, scope=scope)


def delete_user_cookie(*, username: str, domain: str, scope: str = ''):
    """Delete the user's cookie credential."""
    filename = _scoped_filename(cred_type='cookie', scope=scope)
    matched = _resolve_credential_domain(username, domain)
    effective_domain = matched if matched else domain
    path = _credential_path(username=username, domain=effective_domain, filename=filename)
    _remove_file(path)
    with _meta_lock:
        meta = _load_meta()
        meta['credentials'].pop(
            _meta_key(cred_type='cookies', username=username, domain=effective_domain, scope=scope), None)
        _save_meta(meta)


# ---------------------------------------------------------------------------
# Header credentials
# ---------------------------------------------------------------------------

def get_user_header(*, username: str, hostname: str, header_name: str,
                    scope: str = '') -> tuple[str | None, str]:
    """
    Get a specific header credential for a hostname.
    Returns (header_value | None, status).
    """
    filename = _scoped_filename(cred_type='header', scope=scope)
    path = _resolve_credential_path(username=username, hostname=hostname, filename=filename)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        headers = json.loads(content)
        return headers.get(header_name), status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def get_user_headers(*, username: str, hostname: str, scope: str = '') -> tuple[dict, str]:
    """
    Get all header credentials for a hostname.
    Returns (headers dict, status).
    """
    filename = _scoped_filename(cred_type='header', scope=scope)
    path = _resolve_credential_path(username=username, hostname=hostname, filename=filename)
    content, status = _read_encrypted_file(path)
    if content is None:
        return {}, status
    try:
        headers = json.loads(content)
        return headers if isinstance(headers, dict) else {}, status
    except (json.JSONDecodeError, AttributeError):
        return {}, 'error'


def save_user_header(*, username: str, domain: str, header_name: str, value: str,
                      scope: str = '', exact: bool = True):
    """Save a header credential (encrypted storage)."""
    filename = _scoped_filename(cred_type='header', scope=scope)
    if exact:
        path = _credential_path(username=username, domain=domain, filename=filename)
    else:
        path = _resolve_credential_path(username=username, hostname=domain, filename=filename)
    # read existing headers (may have other headers)
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
        if not isinstance(headers, dict):
            headers = {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers[header_name] = value
    content = json.dumps(headers, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry(cred_type='headers', username=username, domain=domain,
                       scope=scope, updated_at=_now_iso(), source='paste')


def delete_user_header(*, username: str, domain: str, header_name: str = '',
                        scope: str = ''):
    """Delete a header credential. Empty header_name deletes the entire file."""
    filename = _scoped_filename(cred_type='header', scope=scope)
    path = _resolve_credential_path(username=username, hostname=domain, filename=filename)
    if not header_name:
        _remove_file(path)
        with _meta_lock:
            meta = _load_meta()
            meta['credentials'].pop(
                _meta_key(cred_type='headers', username=username, domain=domain, scope=scope), None)
            _save_meta(meta)
        return
    # delete single header
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers.pop(header_name, None)
    if headers:
        _write_encrypted_file(path, json.dumps(headers, ensure_ascii=False))
    else:
        _remove_file(path)
        with _meta_lock:
            meta = _load_meta()
            meta['credentials'].pop(
                _meta_key(cred_type='headers', username=username, domain=domain, scope=scope), None)
            _save_meta(meta)


# ---------------------------------------------------------------------------
# Token (OAuth2) credentials
# ---------------------------------------------------------------------------

def get_user_token(*, username: str, hostname: str, scope: str = '') -> tuple[str | None, str]:
    """Get the user's refresh_token for a hostname."""
    filename = _scoped_filename(cred_type='oauth2', scope=scope)
    path = _resolve_credential_path(username=username, hostname=hostname, filename=filename)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data.get('refresh_token', content) if isinstance(data, dict) else content, status
    except (json.JSONDecodeError, AttributeError):
        return content, status


def save_user_token(*, username: str, domain: str, refresh_token: str,
                    scope: str = '', exact: bool = True):
    """Save the user's refresh_token (encrypted storage)."""
    filename = _scoped_filename(cred_type='oauth2', scope=scope)
    if exact:
        path = _credential_path(username=username, domain=domain, filename=filename)
    else:
        path = _resolve_credential_path(username=username, hostname=domain, filename=filename)
    token_content = json.dumps({'refresh_token': refresh_token}, ensure_ascii=False)
    _write_encrypted_file(path, token_content)
    _update_meta_entry(cred_type='tokens', username=username, domain=domain,
                       scope=scope, updated_at=_now_iso(), source='paste')


def delete_user_token(*, username: str, domain: str, scope: str = ''):
    """Delete the user's token credential."""
    filename = _scoped_filename(cred_type='oauth2', scope=scope)
    path = _resolve_credential_path(username=username, hostname=domain, filename=filename)
    _remove_file(path)
    cache_filename = _scoped_filename(cred_type='token_cache', scope=scope)
    cache_path = _resolve_credential_path(username=username, hostname=domain, filename=cache_filename)
    _remove_file(cache_path)
    with _meta_lock:
        meta = _load_meta()
        meta['credentials'].pop(
            _meta_key(cred_type='tokens', username=username, domain=domain, scope=scope), None)
        _save_meta(meta)


# ---------------------------------------------------------------------------
# Basic Auth credentials
# ---------------------------------------------------------------------------

def get_user_basic_auth(*, username: str, hostname: str,
                        scope: str = '') -> tuple[dict | None, str]:
    """Get the user's basic auth credential for a hostname.

    Returns ({'username': ..., 'password': ...} | None, status).
    """
    filename = _scoped_filename(cred_type='basic_auth', scope=scope)
    path = _resolve_credential_path(username=username, hostname=hostname, filename=filename)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None, status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def save_user_basic_auth(*, username: str, domain: str, username_val: str,
                         password_val: str, scope: str = '', exact: bool = True):
    """Save the user's basic auth credential (encrypted storage)."""
    filename = _scoped_filename(cred_type='basic_auth', scope=scope)
    if exact:
        path = _credential_path(username=username, domain=domain, filename=filename)
    else:
        path = _resolve_credential_path(username=username, hostname=domain, filename=filename)
    content = json.dumps({'username': username_val, 'password': password_val}, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry(cred_type='basic_auth', username=username, domain=domain,
                       scope=scope, updated_at=_now_iso(), source='paste')


def delete_user_basic_auth(*, username: str, domain: str, scope: str = ''):
    """Delete the user's basic auth credential."""
    filename = _scoped_filename(cred_type='basic_auth', scope=scope)
    path = _resolve_credential_path(username=username, hostname=domain, filename=filename)
    _remove_file(path)
    with _meta_lock:
        meta = _load_meta()
        meta['credentials'].pop(
            _meta_key(cred_type='basic_auth', username=username, domain=domain, scope=scope), None)
        _save_meta(meta)


# ---------------------------------------------------------------------------
# Shared credentials
# ---------------------------------------------------------------------------

# ── Shared Cookie ──

def _source_matches(stored_source: str | None, requested: str) -> bool:
    """Whether a stored credential's provenance matches a requested filter.

    Legacy entries were saved with source='paste' before provenance was
    tracked; they are treated as user-provided (manual).
    """
    if not stored_source:
        # Entries without any recorded source are treated as manual.
        return requested == 'manual'
    if requested == 'manual':
        return stored_source in ('manual', 'paste')
    return stored_source == requested


def get_shared_cookie(*, hostname: str, scope: str = '',
                      source: str | None = None) -> tuple[str | None, str]:
    """Get the shared cookie credential for a hostname.

    Uses DNS multi-level fallback. Returns (cookie_string | None, status).
    Status: 'ok' | 'key_changed' | 'not_found' | 'error' | 'invalid'

    source: optional provenance filter — 'auto' matches framework
    refresh-generated cookies, 'manual' matches user-provided cookies
    (legacy 'paste' treated as manual). None means no filter.
    """
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    # Check if cookie was previously marked as expired/invalid
    meta_entry = _get_cookie_expired_meta(cred_source='shared', domain=matched, scope=scope)
    if meta_entry.get('cookie_status') == 'invalid':
        return None, 'invalid'
    if source is not None and not _source_matches(meta_entry.get('source'), source):
        return None, 'not_found'
    path = _shared_credential_path(domain=matched, cred_type='cookie', scope=scope)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    cookie_str = _json_cookie_to_header_string(content)
    return cookie_str, status


def get_implicit_manual_cookie(*, hostname: str, section_scope: str = '') -> str | None:
    """Implicit cookie lookup for sections without an auth config.

    Only user-provided (manual) cookies qualify — auto cookies are driven
    by config and must not bleed into unconfigured sections. Lookup order:
    section-scoped manual cookie first, then domain-level manual cookie.
    """
    for scope in (section_scope, ''):
        cookie_str, _ = get_shared_cookie(hostname=hostname, scope=scope, source='manual')
        if cookie_str:
            return cookie_str
    return None


def save_shared_cookie(*, domain: str, cookie_str: str, scope: str = '',
                       source: str = 'manual'):
    """Save a shared cookie credential (encrypted storage).

    source: provenance marker — 'manual' (user-provided) or 'auto'
    (framework refresh). Defaults to 'manual'.
    """
    from site_adapters.services.auth.cookies import cookie_string_to_playwright_list
    cookies_list = cookie_string_to_playwright_list(cookie_str, domain)
    cookie_content = json.dumps(cookies_list, ensure_ascii=False)
    path = _shared_credential_path(domain=domain, cred_type='cookie', scope=scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_encrypted_file(path, cookie_content)
    _update_meta_entry(cred_type='cookies', username='shared', domain=domain,
                       scope=scope, updated_at=_now_iso(), source=source)
    _clear_cookie_expired(cred_source='shared', domain=domain, scope=scope)


def delete_shared_cookie(*, domain: str, scope: str = ''):
    """Delete a shared cookie credential."""
    path = _shared_credential_path(domain=domain, cred_type='cookie', scope=scope)
    _remove_file(path)
    with _meta_lock:
        meta = _load_meta()
        meta['credentials'].pop(
            _meta_key(cred_type='cookies', username='shared', domain=domain, scope=scope), None)
        _save_meta(meta)


# ── Shared Header ──

def get_shared_header(*, hostname: str, header_name: str,
                      scope: str = '') -> tuple[str | None, str]:
    """Get a specific shared header credential for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    path = _shared_credential_path(domain=matched, cred_type='header', scope=scope)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        headers = json.loads(content)
        return headers.get(header_name), status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def get_shared_headers(*, hostname: str, scope: str = '') -> tuple[dict, str]:
    """Get all shared header credentials for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return {}, 'not_found'
    path = _shared_credential_path(domain=matched, cred_type='header', scope=scope)
    content, status = _read_encrypted_file(path)
    if content is None:
        return {}, status
    try:
        headers = json.loads(content)
        return headers if isinstance(headers, dict) else {}, status
    except (json.JSONDecodeError, AttributeError):
        return {}, 'error'


def save_shared_header(*, domain: str, header_name: str, value: str,
                       scope: str = ''):
    """Save a shared header credential (encrypted storage)."""
    path = _shared_credential_path(domain=domain, cred_type='header', scope=scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
        if not isinstance(headers, dict):
            headers = {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers[header_name] = value
    content = json.dumps(headers, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry(cred_type='headers', username='shared', domain=domain,
                       scope=scope, updated_at=_now_iso(), source='paste')


def delete_shared_header(*, domain: str, header_name: str = '', scope: str = ''):
    """Delete a shared header credential. Empty header_name deletes the entire file."""
    path = _shared_credential_path(domain=domain, cred_type='header', scope=scope)
    if not header_name:
        _remove_file(path)
        with _meta_lock:
            meta = _load_meta()
            meta['credentials'].pop(
                _meta_key(cred_type='headers', username='shared', domain=domain, scope=scope), None)
            _save_meta(meta)
        return
    existing, _ = _read_encrypted_file(path)
    try:
        headers = json.loads(existing) if existing else {}
    except (json.JSONDecodeError, AttributeError):
        headers = {}
    headers.pop(header_name, None)
    if headers:
        _write_encrypted_file(path, json.dumps(headers, ensure_ascii=False))
    else:
        _remove_file(path)
        with _meta_lock:
            meta = _load_meta()
            meta['credentials'].pop(
                _meta_key(cred_type='headers', username='shared', domain=domain, scope=scope), None)
            _save_meta(meta)


# ── Shared Token ──

def get_shared_token(*, hostname: str, scope: str = '') -> tuple[str | None, str]:
    """Get the shared refresh_token for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    path = _shared_credential_path(domain=matched, cred_type='oauth2', scope=scope)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data.get('refresh_token', content) if isinstance(data, dict) else content, status
    except (json.JSONDecodeError, AttributeError):
        return content, status


def save_shared_token(*, domain: str, refresh_token: str, scope: str = ''):
    """Save a shared refresh_token (encrypted storage)."""
    path = _shared_credential_path(domain=domain, cred_type='oauth2', scope=scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token_content = json.dumps({'refresh_token': refresh_token}, ensure_ascii=False)
    _write_encrypted_file(path, token_content)
    _update_meta_entry(cred_type='tokens', username='shared', domain=domain,
                       scope=scope, updated_at=_now_iso(), source='paste')


def delete_shared_token(*, domain: str, scope: str = ''):
    """Delete a shared token credential."""
    path = _shared_credential_path(domain=domain, cred_type='oauth2', scope=scope)
    _remove_file(path)
    cache_path = _shared_credential_path(domain=domain, cred_type='token_cache', scope=scope)
    _remove_file(cache_path)
    with _meta_lock:
        meta = _load_meta()
        meta['credentials'].pop(
            _meta_key(cred_type='tokens', username='shared', domain=domain, scope=scope), None)
        _save_meta(meta)


# ── Shared Basic Auth ──

def get_shared_basic_auth(*, hostname: str, scope: str = '') -> tuple[dict | None, str]:
    """Get the shared basic auth credential for a hostname."""
    matched = _resolve_shared_credential_domain(hostname)
    if not matched:
        return None, 'not_found'
    path = _shared_credential_path(domain=matched, cred_type='basic_auth', scope=scope)
    content, status = _read_encrypted_file(path)
    if content is None:
        return None, status
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None, status
    except (json.JSONDecodeError, AttributeError):
        return None, 'error'


def save_shared_basic_auth(*, domain: str, username_val: str, password_val: str,
                           scope: str = ''):
    """Save a shared basic auth credential (encrypted storage)."""
    path = _shared_credential_path(domain=domain, cred_type='basic_auth', scope=scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = json.dumps({'username': username_val, 'password': password_val}, ensure_ascii=False)
    _write_encrypted_file(path, content)
    _update_meta_entry(cred_type='basic_auth', username='shared', domain=domain,
                       scope=scope, updated_at=_now_iso(), source='paste')


def delete_shared_basic_auth(*, domain: str, scope: str = ''):
    """Delete a shared basic auth credential."""
    path = _shared_credential_path(domain=domain, cred_type='basic_auth', scope=scope)
    _remove_file(path)
    with _meta_lock:
        meta = _load_meta()
        meta['credentials'].pop(
            _meta_key(cred_type='basic_auth', username='shared', domain=domain, scope=scope), None)
        _save_meta(meta)


# ---------------------------------------------------------------------------
# Unified best-credential resolution (single scope, user > shared)
# ---------------------------------------------------------------------------

def get_best_cookie(*, username: str, hostname: str,
                    scope: str = '') -> tuple[str | None, str]:
    """Get the best available cookie within a single scope: user first, shared fallback."""
    if username:
        result, status = get_user_cookie(username=username, hostname=hostname, scope=scope)
        if result:
            return result, status
    return get_shared_cookie(hostname=hostname, scope=scope)


def get_best_header(*, username: str, hostname: str, header_name: str,
                    scope: str = '') -> tuple[str | None, str]:
    """Get the best available header within a single scope: user first, shared fallback."""
    if username:
        result, status = get_user_header(username=username, hostname=hostname,
                                         header_name=header_name, scope=scope)
        if result:
            return result, status
    return get_shared_header(hostname=hostname, header_name=header_name, scope=scope)


def get_best_token(*, username: str, hostname: str,
                   scope: str = '') -> tuple[str | None, str]:
    """Get the best available token within a single scope: user first, shared fallback."""
    if username:
        result, status = get_user_token(username=username, hostname=hostname, scope=scope)
        if result:
            return result, status
    return get_shared_token(hostname=hostname, scope=scope)


def get_best_basic_auth(*, username: str, hostname: str,
                        scope: str = '') -> tuple[dict | None, str]:
    """Get the best available basic auth within a single scope: user first, shared fallback."""
    if username:
        result, status = get_user_basic_auth(username=username, hostname=hostname, scope=scope)
        if result:
            return result, status
    return get_shared_basic_auth(hostname=hostname, scope=scope)




def get_best_headers(*, username: str, hostname: str,
                     scope: str = '') -> tuple[dict, str]:
    """Get all available headers within a single scope: user first, shared fallback.

    Returns (merged dict of all saved headers, status).
    """
    result = {}
    if username:
        user_hdrs, status = get_user_headers(username=username, hostname=hostname, scope=scope)
        if user_hdrs:
            result.update(user_hdrs)
    shared_hdrs, _ = get_shared_headers(hostname=hostname, scope=scope)
    for k, v in shared_hdrs.items():
        if k not in result:
            result[k] = v
    return result, 'ok' if result else 'not_found'


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

# Regex to parse scope from filenames: cookie.json, cookie_snapshot.json, etc.
_SCOPE_FILE_RE = re.compile(r'^(cookie|header|token|basic_auth|token_cache)(?:_({}))?\.json$'.format(
    '|'.join(s for s in VALID_SCOPES if s)
))


def _parse_scoped_filename(filename: str) -> tuple[str | None, str | None]:
    """Parse a credential filename into (cred_type, scope).

    Returns (None, None) if the file doesn't match a known credential pattern.
    """
    m = _SCOPE_FILE_RE.match(filename)
    if not m:
        return None, None
    raw_type = m.group(1)
    scope = m.group(2) or ''
    # Normalize 'token' → 'oauth2' for display consistency
    if raw_type == 'token':
        cred_type = 'oauth2'
    else:
        cred_type = raw_type
    return cred_type, scope


def _list_credentials_in_dir(base_dir: str, meta_prefix: str,
                                  include_values: bool = False) -> list[dict]:
    """List all credentials in a directory, optionally with decrypted values.

    Scans for all scope variants (cookie.json, cookie_snapshot.json, etc.).
    """
    result = []
    meta = _load_meta()
    fingerprint_ok = check_key_fingerprint()

    if not os.path.isdir(base_dir):
        return result

    for domain in sorted(os.listdir(base_dir)):
        domain_dir = os.path.join(base_dir, domain)
        if not os.path.isdir(domain_dir):
            continue

        for filename in sorted(os.listdir(domain_dir)):
            cred_type, scope = _parse_scoped_filename(filename)
            if cred_type is None:
                continue
            # Skip token_cache files in listing (they are internal cache, not credentials)
            if filename.startswith('token_cache'):
                continue

            path = os.path.join(domain_dir, filename)
            if not os.path.isfile(path):
                continue

            meta_key = _meta_key(cred_type=cred_type + 's', username=meta_prefix,
                                 domain=domain, scope=scope)
            m = meta['credentials'].get(meta_key)
            if not m:
                # Exact key miss: recover a wildcard/hostname variant entry so
                # a cookie_status='invalid' mark stored under a different but
                # compatible domain still surfaces in the listing.
                m = _lookup_meta_domain_variant(
                    meta, cred_type=cred_type + 's', username=meta_prefix,
                    domain=domain, scope=scope) or {}
            content, status = _read_encrypted_file(path)
            if not fingerprint_ok and status == 'ok':
                status = 'key_changed'

            entry = {
                'domain': domain,
                'type': cred_type,
                'scope': scope,
                'status': status,
                'updated_at': m.get('updated_at', ''),
                'cookie_status': m.get('cookie_status', ''),
                'expired_at': m.get('expired_at', ''),
            }

            if include_values and content and status == 'ok':
                if cred_type == 'cookie':
                    entry['cookie'] = _json_cookie_to_header_string(content) or ''
                elif cred_type == 'header':
                    try:
                        hdrs = json.loads(content)
                        if isinstance(hdrs, dict):
                            entry['header_names'] = list(hdrs.keys())
                            entry['header_values'] = hdrs
                    except (json.JSONDecodeError, AttributeError):
                        entry['header_names'] = []
                        entry['header_values'] = {}
                elif cred_type == 'oauth2':
                    try:
                        token_data = json.loads(content)
                        entry['token'] = token_data.get('refresh_token', '') if isinstance(token_data, dict) else ''
                    except (json.JSONDecodeError, AttributeError):
                        entry['token'] = ''
                elif cred_type == 'basic_auth':
                    try:
                        ba_data = json.loads(content)
                        entry['basic_auth_username'] = ba_data.get('username', '') if isinstance(ba_data, dict) else ''
                        entry['basic_auth_password'] = ba_data.get('password', '') if isinstance(ba_data, dict) else ''
                    except (json.JSONDecodeError, AttributeError):
                        entry['basic_auth_username'] = ''
                        entry['basic_auth_password'] = ''

            result.append(entry)

    return result


def list_shared_credentials(include_values: bool = False) -> list[dict]:
    """List all shared credentials."""
    return _list_credentials_in_dir(
        os.path.join(_get_credentials_dir(), 'shared'), 'shared',
        include_values=include_values,
    )


def list_user_credentials(username: str) -> list[dict]:
    """列出用户的所有凭据（含解密后的值）。"""
    return _list_credentials_in_dir(
        os.path.join(_get_credentials_dir(), 'users', username),
        username,
        include_values=True,
    )


# ---------------------------------------------------------------------------
# Token cache (OAuth2 access_token persistence)
# ---------------------------------------------------------------------------

def load_user_token_cache(*, username: str, hostname: str,
                          scope: str = '') -> dict | None:
    """Load token cache (access_token + expires_at) for a hostname.

    Reads encrypted storage; transparently migrates legacy plaintext
    cache files to encrypted on first successful read.
    """
    filename = _scoped_filename(cred_type='token_cache', scope=scope)
    path = _resolve_credential_path(username=username, hostname=hostname, filename=filename)
    if not os.path.exists(path):
        return None
    content, status = _read_encrypted_file(path)
    if content is None or status == 'key_changed':
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    # Migrate legacy plaintext token_cache to encrypted storage.
    try:
        with open(path, encoding='utf-8') as f:
            if not is_encrypted(f.read()):
                _write_encrypted_file(path, json.dumps(data, ensure_ascii=False))
    except OSError:
        pass
    return data


def save_user_token_cache(*, username: str, domain: str, cache_data: dict,
                          scope: str = ''):
    """Save token cache for a domain.

    Uses exact domain (no fallback) because the cache is always written
    alongside the token itself in the same domain directory.
    """
    filename = _scoped_filename(cred_type='token_cache', scope=scope)
    matched = _resolve_credential_domain(username, domain)
    effective_domain = matched if matched else domain
    path = _credential_path(username=username, domain=effective_domain, filename=filename)
    _write_encrypted_file(path, json.dumps(cache_data, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Headers block normalization
# ---------------------------------------------------------------------------

# Reserved keys in a headers block that are not header names.
_HEADERS_RESERVED_KEYS = ('enabled', 'help', 'values')


def _normalize_headers_block(headers_cfg) -> dict:
    """Normalize a headers config block to {enabled, help, values}.

    Two input forms are supported:
    - Structured: {'enabled': bool, 'help': str, 'values': {name: val}}
    - Flat (legacy): {'name': val, 'enabled': bool?, 'help': str?}

    In flat form, 'enabled'/'help'/'values' are treated as reserved keys,
    not header names. To declare a header literally named 'enabled' or
    'help', use the structured form with a 'values' sub-key.
    """
    if not isinstance(headers_cfg, dict):
        return {'enabled': True, 'help': '', 'values': {}}

    if 'values' in headers_cfg:
        # Structured form
        enabled = headers_cfg.get('enabled', True)
        help_text = headers_cfg.get('help', '')
        values = dict(headers_cfg.get('values', {}))
        if not isinstance(values, dict):
            values = {}
        return {'enabled': enabled, 'help': help_text, 'values': values}

    # Flat form: separate reserved keys from header name→value pairs
    enabled = headers_cfg.get('enabled', True)
    help_text = headers_cfg.get('help', '')
    values = {}
    for k, v in headers_cfg.items():
        if k in _HEADERS_RESERVED_KEYS:
            continue
        values[k] = v
    return {'enabled': enabled, 'help': help_text, 'values': values}


def _merge_headers_block(*blocks) -> dict:
    """Merge multiple normalized headers blocks.

    enabled: False wins (any layer disabling → disabled).
    help: last non-empty wins.
    values: later overrides same key.
    """
    result_enabled = True
    result_help = ''
    result_values: dict[str, str] = {}
    for block in blocks:
        if not block:
            continue
        norm = _normalize_headers_block(block)
        if not norm.get('enabled', True):
            result_enabled = False
        if norm.get('help'):
            result_help = norm['help']
        result_values.update(norm.get('values', {}))
    return {'enabled': result_enabled, 'help': result_help, 'values': result_values}


# ---------------------------------------------------------------------------
# Auth requirements
# ---------------------------------------------------------------------------

def _extract_auth_block(auth: dict) -> dict:
    """Extract the effective auth requirement fields from a merged auth block."""
    cookie_cfg = auth.get('cookie', {})
    headers_cfg = auth.get('headers', {})
    oauth2_cfg = auth.get('oauth2', {})
    basic_cfg = auth.get('basic_auth', {})

    has_cookie = bool(cookie_cfg) and (
        cookie_cfg.get('enabled', True) if isinstance(cookie_cfg, dict) else True
    )
    # headers: key presence + enabled flag (empty values dict is OK)
    headers_norm = _normalize_headers_block(headers_cfg)
    has_headers = 'headers' in auth and headers_norm.get('enabled', True)
    header_names = sorted(headers_norm.get('values', {}).keys())
    has_oauth2 = bool(oauth2_cfg) and (
        oauth2_cfg.get('enabled', True) if isinstance(oauth2_cfg, dict) else True
    ) and bool(oauth2_cfg.get('endpoint'))
    has_basic_auth = bool(basic_cfg) and (
        basic_cfg.get('enabled', True) if isinstance(basic_cfg, dict) else True
    )
    cookie_type = cookie_cfg.get('type', 'auto') if isinstance(cookie_cfg, dict) else 'auto'

    return {
        'cookie': has_cookie,
        'cookie_type': cookie_type if has_cookie else '',
        'headers': header_names if has_headers else [],
        'headers_active': has_headers,
        'oauth2': has_oauth2,
        'token': has_oauth2,  # backward compat
        'basic_auth': has_basic_auth,
        'cookie_help': cookie_cfg.get('help', '') if isinstance(cookie_cfg, dict) else '',
        'headers_help': headers_norm.get('help', ''),
        'oauth2_help': oauth2_cfg.get('help', '') if isinstance(oauth2_cfg, dict) else '',
        'basic_help': basic_cfg.get('help', '') if isinstance(basic_cfg, dict) else '',
    }


def _build_section_auth_requirements(*, section_data: dict, domain_auth: dict) -> dict:
    """Build auth requirements for a single section.

    Determines which sub-types are section-level vs inherited vs disabled.
    """
    section_auth_raw = section_data.get('auth') if isinstance(section_data, dict) else None

    # auth: null → disabled
    if section_auth_raw is None and isinstance(section_data, dict) and 'auth' in section_data:
        return {
            'cookie': False, 'cookie_type': '', 'headers': [], 'headers_active': False,
            'oauth2': False, 'token': False, 'basic_auth': False,
            'cookie_help': '', 'headers_help': '', 'oauth2_help': '', 'basic_help': '',
            'source': 'disabled',
        }

    if not isinstance(section_auth_raw, dict) or not section_auth_raw:
        # No section auth → fully inherited from domain
        result = _extract_auth_block(domain_auth)
        result['source'] = 'inherited'
        return result

    # Section has its own auth block — merge and determine per-subtype source
    from site_adapters.services.auth.cookies import merge_cookie
    merged = {}
    # Cookie
    if 'cookie' in section_auth_raw:
        merged['cookie'] = merge_cookie(domain_auth.get('cookie', {}), section_auth_raw['cookie'])
    else:
        merged['cookie'] = domain_auth.get('cookie', {})
    # Headers
    if 'headers' in section_auth_raw:
        merged['headers'] = _merge_headers_block(
            domain_auth.get('headers', {}), section_auth_raw['headers'])
    else:
        merged['headers'] = domain_auth.get('headers', {})
    # OAuth2
    if 'oauth2' in section_auth_raw:
        merged['oauth2'] = dict(section_auth_raw['oauth2'])
    else:
        merged['oauth2'] = domain_auth.get('oauth2', {})
    # Basic Auth
    if 'basic_auth' in section_auth_raw:
        merged['basic_auth'] = dict(section_auth_raw['basic_auth'])
    else:
        merged['basic_auth'] = domain_auth.get('basic_auth', {})

    result = _extract_auth_block(merged)
    result['source'] = 'section'
    return result


def get_auth_requirements_for_domain(hostname: str, base_dir: str = '') -> dict:
    """
    Query auth requirements for a hostname (local + subscription).

    Returns the domain-level baseline auth requirements (flat, for backward compat).
    Use get_auth_requirements_for_domain_key for the full section-aware structure.
    """
    if not base_dir:
        from site_adapters.services.base import _get_base_dir
        base_dir = _get_base_dir()

    from site_adapters.services.config.loader import load_domain_config
    url = f'https://{hostname}'
    config = load_domain_config(url, base_dir)
    if not config:
        return {'cookie': False, 'headers': [], 'headers_active': False, 'token': False, 'cookie_type': '',
                'oauth2': False, 'basic_auth': False,
                'cookie_help': '', 'headers_help': '', 'oauth2_help': '', 'basic_help': ''}

    auth = config.get('auth', {})
    return _extract_auth_block(auth)


def get_auth_requirements_for_domain_key(domain_key: str, base_dir: str = '') -> dict:
    """Query auth requirements for a resolved domain key.

    Returns:
    {
        'domain': { ... },              # domain-level baseline
        'sections': {
            'metadata': { ... },        # effective merged auth per section
            'snapshot': { ... },
            'reader': { ... },
        }
    }

    Each block contains: cookie, cookie_type, headers, oauth2, basic_auth,
    cookie_help, headers_help, oauth2_help, basic_help, source.
    source is 'section' (section defines its own auth), 'inherited'
    (inherits domain-level), or 'disabled' (auth: null).
    """
    if not base_dir:
        from site_adapters.services.base import _get_base_dir
        base_dir = _get_base_dir()

    empty_block = {
        'cookie': False, 'cookie_type': '', 'headers': [], 'headers_active': False,
        'oauth2': False, 'token': False, 'basic_auth': False,
        'cookie_help': '', 'headers_help': '', 'oauth2_help': '', 'basic_help': '',
    }
    if not domain_key:
        return {'domain': dict(empty_block), 'sections': {}}

    from site_adapters.services.config import deep_merge
    from site_adapters.services.config.loader import _cache, _resolve_alias

    all_config = _cache.load(base_dir)
    defaults = all_config.get('defaults', {})
    raw_config = all_config.get(domain_key)
    if raw_config is None:
        return {'domain': dict(empty_block), 'sections': {}}

    resolved = _resolve_alias(raw_config, all_config) if isinstance(raw_config, dict) else raw_config
    if not isinstance(resolved, dict):
        return {'domain': dict(empty_block), 'sections': {}}

    merged = deep_merge(resolved, defaults) if defaults else resolved

    # Domain-level baseline auth = top.auth + defaults.auth merged
    from site_adapters.services.config.resolver import _merge_auth
    top_auth = merged.get('auth', {})
    default_auth = defaults.get('auth', {})
    domain_auth = _merge_auth(top_auth, default_auth)

    domain_block = _extract_auth_block(domain_auth)
    domain_block['source'] = 'domain'

    sections = {}
    for section in _AUTH_SECTIONS:
        section_data = merged.get(section, {})
        sections[section] = _build_section_auth_requirements(
            section_data=section_data, domain_auth=domain_auth)

    return {'domain': domain_block, 'sections': sections}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _json_cookie_to_header_string(data_str: str) -> str | None:
    """Convert Playwright cookie JSON to header string."""
    from site_adapters.services.auth.cookies import _cookie_data_to_string as _convert
    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        return data_str if isinstance(data_str, str) else None
    return _convert(data)


def _now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
