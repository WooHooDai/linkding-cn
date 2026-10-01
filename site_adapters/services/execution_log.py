"""
执行记录

记录快照生成、元数据脚本、Cookie 验证/刷新的执行日志。
文件：data/site_adapters/logs/execution-YYYY-MM-DD.jsonl
按天轮转，默认保留 30 天，可通过 LD_SITE_ADAPTERS_LOG_RETENTION_DAYS 环境变量自定义。

收集模式：使用 collect_executions() context manager 捕获范围内所有执行日志。
"""

import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

SECRET_KEYS = {
    "authorization", "cookie", "cookies", "cookie_file", "set-cookie",
    "token", "access_token", "refresh_token", "id_token",
    "password", "passwd", "sessionid", "csrftoken", "x-csrf-token",
}
# 键名长/复合的放前面，避免被短名（如 token）截断。值在引号/逗号/分号/花括号处
# 截止，以覆盖脚本 stdout 直接输出的裸 "sessionid=abc; csrftoken=xyz" 与
# JSON 形式的 `"sessionid":"abc"`（原先要求先出现 cookie:/authorization: 才命中）。
SECRET_RE = re.compile(
    r"""(?i)(authorization|set-cookie|cookie_file|cookies|cookie|"""
    r"""access_token|refresh_token|id_token|token|"""
    r"""password|passwd|sessionid|csrftoken|x-csrf-token)"""
    r"""(["']?\s*[:=]\s*["']?)[^"',;}]+"""
)

# Lock for serializing JSONL file writes (prevents line tearing)
_write_lock = threading.Lock()

# Context variable for execution collection (thread/task-safe)
_collector_var: ContextVar[list | None] = ContextVar('_collector', default=None)


@contextmanager
def collect_executions():
    """收集此范围内所有 log_execution 的条目。

    Usage:
        with collect_executions() as entries:
            do_something()
        # entries now contains all log entries from the block
    """
    entries = []
    token = _collector_var.set(entries)
    try:
        yield entries
    finally:
        _collector_var.reset(token)



def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _get_log_dir() -> str:
    from django.conf import settings
    return os.path.join(settings.LD_SITE_ADAPTERS_DIR, 'logs')


def _get_log_path() -> str:
    return os.path.join(
        _get_log_dir(),
        f'execution-{datetime.now(UTC).strftime("%Y-%m-%d")}.jsonl',
    )


def _retention_days() -> int:
    from django.conf import settings
    return int(getattr(settings, 'LD_SITE_ADAPTERS_LOG_RETENTION_DAYS', 30))


_last_cleanup: float = 0

def _cleanup_old_logs():
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < 3600:  # at most once per hour
        return
    _last_cleanup = now
    log_dir = _get_log_dir()
    if not os.path.isdir(log_dir):
        return
    cutoff = datetime.now(UTC) - timedelta(days=_retention_days())
    for name in os.listdir(log_dir):
        if not name.startswith('execution-') or not name.endswith('.jsonl'):
            continue
        try:
            date_str = name[len('execution-'):-len('.jsonl')]
            file_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=UTC)
            if file_date < cutoff:
                os.remove(os.path.join(log_dir, name))
        except (ValueError, OSError):
            pass


CMD_SECRET_PREFIXES = ('--browser-cookies-file=',)


def _redact_cmd_args(args: list) -> list:
    """Redact sensitive cmd args (e.g. --browser-cookies-file=/tmp/...)."""
    result = []
    for arg in args:
        if isinstance(arg, str) and any(arg.startswith(p) for p in CMD_SECRET_PREFIXES):
            result.append(arg.split('=', 1)[0] + '=[redacted]')
        else:
            result.append(arg)
    return result


def _redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).lower() in SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_RE.sub(r'\1\2[redacted]', value)
    return value


def log_execution(
    url: str,
    domain_key: str,
    step: str,
    cmd: list = None,
    stdin: str = None,
    returncode: int = 0,
    stdout: str = '',
    stderr: str = '',
    duration_ms: int = 0,
    config_snapshot: dict = None,
):
    """记录一次执行。

    step: "snapshot" | "metadata_script" | "cookie_refresh" | "cookie_verify"
    """
    entry = {
        'timestamp': datetime.now(UTC).isoformat(),
        'url': url,
        'domain_key': domain_key,
        'step': step,
        'returncode': returncode,
        'duration_ms': duration_ms,
    }
    if cmd:
        entry['cmd'] = _redact_cmd_args(cmd)
    if stdin:
        entry['stdin'] = _redact(stdin)[:2000]
    if stdout and not step.startswith('cookie_'):
        entry['stdout'] = _redact(stdout)[:1000]
    if stderr:
        entry['stderr'] = _redact(stderr)[:1000]
    if config_snapshot:
        entry['config_snapshot'] = _redact(config_snapshot)

    # Append to collector if active
    collector = _collector_var.get()
    if collector is not None:
        collector.append({k: v for k, v in entry.items() if k not in ('stdout', 'stderr')})

    # Write to JSONL file (serialized to prevent line tearing)
    try:
        with _write_lock:
            log_path = _get_log_path()
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(_dumps(entry) + '\n')
            _cleanup_old_logs()
    except OSError as e:
        logger.error("Failed to write execution log: %s", e)
