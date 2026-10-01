"""
凭据加密模块

使用 Fernet (AES-128-CBC + HMAC) 对用户凭据进行加密存储。
密钥自动生成并持久化到 data/credentials.key。
"""

import hashlib
import logging
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None
_fernet_mtime: float = 0
_fingerprint: str | None = None
_fingerprint_mtime: float = 0
_lock = threading.Lock()


def _get_key_path() -> str:
    from django.conf import settings
    return os.path.join(settings.BASE_DIR, 'data', 'credentials.key')


def _get_fernet() -> Fernet:
    global _fernet, _fernet_mtime
    key_path = _get_key_path()
    # Detect external key rotation: rebuild if the key file's mtime changed
    # since we last cached the Fernet instance. Without this, a process that
    # had already cached the old key would keep failing to decrypt new creds.
    try:
        current_mtime = os.path.getmtime(key_path)
    except OSError:
        current_mtime = 0

    # Fast path: key file unchanged → reuse cached fernet
    if _fernet is not None and _fernet_mtime == current_mtime:
        return _fernet

    with _lock:
        # double-check after acquiring lock
        if _fernet is not None and _fernet_mtime == current_mtime:
            return _fernet

        if os.path.exists(key_path):
            with open(key_path, encoding='utf-8') as f:
                key = f.read().strip()
            new_mtime = current_mtime
        else:
            key = Fernet.generate_key().decode()
            from bookmarks.utils import atomic_write
            atomic_write(key_path, key)
            os.chmod(key_path, 0o600)
            logger.info("Generated new credentials encryption key")
            try:
                new_mtime = os.path.getmtime(key_path)
            except OSError:
                new_mtime = current_mtime

        _fernet = Fernet(key.encode())
        _fernet_mtime = new_mtime
        return _fernet


def encrypt_value(plaintext: str) -> str:
    """加密明文字符串，返回 Fernet 密文。"""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str | None:
    """
    解密 Fernet 密文。
    成功返回明文，失败返回 None（密钥不匹配或数据损坏）。
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None
    except Exception:
        return None


def is_encrypted(value: str) -> bool:
    """判断值是否是 Fernet 密文格式（以 gAAAAA 开头的 base64）。"""
    return isinstance(value, str) and value.startswith('gAAAAA')


def decrypt_or_plaintext(value: str) -> str:
    """
    尝试解密；如果不是密文格式，视为旧版明文直接返回。
    用于惰性迁移。
    """
    if not value:
        return value
    if not is_encrypted(value):
        return value
    result = decrypt_value(value)
    return result if result is not None else value


def get_key_fingerprint() -> str:
    """Return SHA-256 fingerprint of the current encryption key.

    Re-reads the key file if its mtime has changed since the last read.
    """
    global _fingerprint, _fingerprint_mtime
    key_path = _get_key_path()
    try:
        current_mtime = os.path.getmtime(key_path)
    except OSError:
        current_mtime = 0

    # Fast path: mtime unchanged → return cached fingerprint
    if _fingerprint is not None and _fingerprint_mtime == current_mtime:
        return _fingerprint

    with _lock:
        # double-check after acquiring lock
        if _fingerprint is not None and _fingerprint_mtime == current_mtime:
            return _fingerprint
        with open(key_path, encoding='utf-8') as f:
            key = f.read().strip()
        _fingerprint = hashlib.sha256(key.encode()).hexdigest()
        _fingerprint_mtime = current_mtime
        return _fingerprint
