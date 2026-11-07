import json
import logging
import os
import subprocess
from contextlib import suppress

from bookmarks.services import singlefile, website_loader
from site_adapters.services.auth.cookies import (
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import get_shared_cookie
from site_adapters.services.config import apply_request_url
from site_adapters.services.config.resolver import get_snapshot_config
from site_adapters.services.engine.script_runner import run_script, resolve_hook_timeout

logger = logging.getLogger(__name__)


class SnapshotRejectedError(singlefile.SingleFileError):
    """The snapshot was rejected with a user-facing reason."""

    def __init__(self, status: str, reason: str,
                 source: str = "preflight health check"):
        self.status = status
        self.reason = reason
        self.source = source
        super().__init__(
            f"snapshot rejected by {source} ({status}): {reason}"
        )


def _snapshot_format(config: dict | None) -> str:
    return website_loader.resolve_content_type(config, default="html")


def _apply_snapshot_before_result(url: str, config: dict, result: dict) -> None:
    """Apply the partial config returned by a snapshot before hook in place.

    Mirrors the metadata pipeline: user-facing keys such as ``request_url``
    and ``user_cookie`` are mapped to the internal keys the SingleFile engine
    actually reads.
    """
    from site_adapters.services.config.resolver import _merge_dicts

    merged = _merge_dicts(config, result)
    for key, value in result.items():
        if key == 'request_url':
            if isinstance(value, str) and value.startswith(('http://', 'https://')):
                merged['_request_url'] = value
            else:
                resolved = apply_request_url(url, value)
                if resolved:
                    merged['_request_url'] = resolved
        elif key == 'user_cookie' and value:
            merged['_user_cookie'] = value
    config.clear()
    config.update(merged)


def _run_snapshot(url: str, filepath: str, config: dict | None):
    if config:
        scripts = config.get("scripts")
        if scripts:
            return _run_snapshot_with_hooks(url, filepath, config, scripts)

        script_path = config.get("script")
        if script_path:
            if os.path.exists(script_path):
                return run_script(script_path, url=url, config=config, output_path=filepath)
            logger.error("Snapshot script not found: %s", script_path)
        if _snapshot_format(config) in ("xml", "json"):
            return _create_raw_snapshot(url, filepath, config)
        return _create_snapshot(url, filepath, config)
    return _create_snapshot(url, filepath, None)


def _create_raw_snapshot(url: str, filepath, config: dict):
    before_path = config.get("_before_content_path")
    if before_path and os.path.exists(before_path):
        with open(before_path, encoding="utf-8") as f:
            content = f.read()
    else:
        request_url = config.get("_request_url", url)
        content = website_loader.load_page(request_url, config, load_full_page=True)
    # A 200 with an empty body must not be persisted as a COMPLETE snapshot,
    # otherwise the dispatcher never retries. Align with the built-in engine
    # path which raises when no snapshot file is produced.
    if not (content or "").strip():
        raise singlefile.SingleFileError("Raw snapshot content is empty")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


def _run_snapshot_with_hooks(url: str, filepath: str, config: dict, scripts: list):
    """Execute snapshot pipeline with hook scripts.

    Order: external before hooks → [replace or SingleFile with browser hooks]
           → external after hooks
    """
    import tempfile
    raw_format = _snapshot_format(config)
    is_raw = raw_format in ("xml", "json")
    before_content_path = None
    before_temp_paths = []
    external_before = []
    external_after = []
    replace_scripts = []
    browser_before = []
    builtin_after = []

    for entry in scripts:
        script_path = entry.get('path', '')
        if not script_path or not os.path.exists(script_path):
            continue
        hook = entry.get('hook')
        if hook == 'replace':
            replace_scripts.append(entry)
        elif hook == 'before':
            if script_path.endswith('.js') and singlefile.uses_builtin_engine(script_path, 'before'):
                browser_before.append(script_path)
            else:
                external_before.append(entry)
        elif hook == 'after':
            if script_path.endswith('.js') and singlefile.uses_builtin_engine(script_path, 'after'):
                builtin_after.append(script_path)
            else:
                external_after.append(entry)

    # 1. Run external before hooks
    for entry in external_before:
        script_path = entry.get('path', '')
        hook_timeout = resolve_hook_timeout(entry, config)
        logger.debug("Running snapshot before hook: %s (timeout=%ds)", script_path, hook_timeout)
        result = run_script(script_path, hook_name='before', url=url,
                            config=dict(config), timeout=hook_timeout)
        if isinstance(result, str):
            suffix = raw_format if is_raw else "html"
            with tempfile.NamedTemporaryFile(
                "w", suffix=f".{suffix}", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(result)
            before_content_path = tmp.name
            before_temp_paths.append(tmp.name)
            logger.debug("Before hook returned content, saved to: %s", before_content_path)
        elif isinstance(result, dict):
            if result.get("error"):
                raise SnapshotRejectedError(
                    str(result.get("status") or "failed"),
                    str(result["error"]),
                    source="adapter hook",
                )
            # Before hook returned config overrides (e.g. dynamic User-Agent).
            # Deep-merge into config so SingleFile picks up the changes.
            _apply_snapshot_before_result(url, config, result)
            logger.debug("Before hook returned config overrides, merged: %s", result)

    # 2. Run replace hook or built-in engine
    try:
        if replace_scripts:
            if browser_before:
                logger.warning(
                    "Snapshot SingleFile browser hooks are ignored when a replace hook is present"
                )
            for entry_replace in replace_scripts:
                script_path = entry_replace.get('path', '')
                hook_timeout = resolve_hook_timeout(entry_replace, config)
                logger.debug("Running snapshot replace hook: %s (timeout=%ds)", script_path, hook_timeout)
                hook_result = run_script(script_path, hook_name='replace', url=url,
                                         config=dict(config), output_path=filepath,
                                         timeout=hook_timeout)
                # A replace hook may reject with a reason instead of leaving
                # the output empty; surface it through the same channel as
                # precheck_health so the admin panel can show it.
                if isinstance(hook_result, dict) and hook_result.get("error"):
                    raise SnapshotRejectedError(
                        str(hook_result.get("status") or "failed"),
                        str(hook_result["error"]),
                        source="adapter hook",
                    )
                break  # Only one replace allowed
            # run_script only logs on failure; verify the replace hook actually
            # produced a non-empty file, matching the built-in engine validation.
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                raise singlefile.SingleFileError(
                    "Replace hook did not produce a non-empty snapshot"
                )
        elif is_raw:
            config_copy = dict(config)
            if before_content_path:
                config_copy['_before_content_path'] = before_content_path
            _create_raw_snapshot(url, filepath, config_copy)
        else:
            # Built-in engine: SingleFile
            config_copy = dict(config)
            if browser_before:
                config_copy['_browser_before_scripts'] = browser_before
            if before_content_path:
                config_copy['_before_html_path'] = before_content_path
                _create_snapshot(url, filepath, config_copy)
            else:
                _create_snapshot(url, filepath, config_copy)

        # 3. Run built-in after hooks against the saved HTML
        for script_path in builtin_after:
            if is_raw:
                logger.warning(
                    "SingleFile built-in after hook skipped for raw %s snapshot: %s",
                    raw_format,
                    script_path,
                )
            else:
                _run_builtin_after_hook(script_path, url, filepath, config,
                                         resolve_hook_timeout({}, config))

        # 4. Run external after hooks
        for entry in external_after:
            script_path = entry.get('path', '')
            hook_timeout = resolve_hook_timeout(entry, config)
            logger.debug("Running snapshot after hook: %s (timeout=%ds)", script_path, hook_timeout)
            after_config = dict(config)
            after_config['_url'] = url
            run_script(script_path, hook_name='after', output_path=filepath,
                       config=after_config, timeout=hook_timeout)
    finally:
        # Cleanup every before-hook temp file. Only the last returned content
        # feeds the engine, but earlier temp files must not leak either.
        for path in before_temp_paths:
            with suppress(OSError):
                os.unlink(path)


def _run_builtin_after_hook(script_path: str, url: str, filepath: str,
                            config: dict, timeout: int = 30):
    """Run a SingleFile built-in after hook against the saved snapshot HTML."""
    import site_adapters.services as _sa_services
    from site_adapters.services.engine.script_runner import _sanitize_config

    runner = os.path.join(
        os.path.dirname(_sa_services.__file__),
        "engine",
        "scripts",
        "snapshot_browser_after.js",
    )
    payload = {
        "scriptPath": script_path,
        "url": url,
        "config": _sanitize_config(config),
        "outputPath": filepath,
    }
    try:
        result = subprocess.run(
            ["node", runner],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error(
                "Snapshot built-in after hook failed: %s stderr=%s",
                script_path,
                result.stderr[:500],
            )
    except subprocess.TimeoutExpired:
        logger.error("Snapshot built-in after hook timed out: %s", script_path)



def _verify_snapshot_cookie(url: str, filepath: str, config: dict) -> bool:
    cookie_config = config.get("cookie", {})
    if not cookie_config:
        return False
    domain_key = config.get("_domain_key")
    before = website_loader._cookie_string_from_config(config)
    after = verify_and_refresh(
        cookie_config=cookie_config,
        url=config.get("_request_url", url),
        domain_key=domain_key,
        verify_context={"url": config.get("_request_url", url), "html_path": filepath},
        scope=config.get('_effective_cookie_scope', ''),
    )
    if after and after != before:
        # 刷新成功后更新 _user_cookie 为新的 cookie 字符串
        config["_user_cookie"] = after
        return True
    return False


def _reject_by_health(url: str, config: dict | None, username: str = '') -> None:
    """Reuse the health section's verdict and reason before any browser starts.

    Health checks already follow redirects, inspect content and apply the
    adapter's ``reason`` text, so page signals stay in one place. A probe that
    fails (or health being disabled / ok) must never block a snapshot.
    """
    if not (config or {}).get("precheck_health"):
        return
    # Lazy import: health_checker pulls in the ORM, which the snapshot engine
    # does not otherwise need at module load time.
    from bookmarks.models import (
        HEALTH_STATUS_BLOCKED,
        HEALTH_STATUS_DEAD,
        HEALTH_STATUS_MISSING,
    )
    from bookmarks.services.health_checker import check_url

    try:
        result = check_url(url, username=username)
    except Exception as exc:  # noqa: BLE001 - a failed probe must not block snapshots
        logger.warning("preflight health check failed, continuing: %s", exc)
        return

    status = result.get("status")
    if status not in {
        HEALTH_STATUS_BLOCKED, HEALTH_STATUS_DEAD, HEALTH_STATUS_MISSING,
    }:
        return
    raise SnapshotRejectedError(status, result.get("reason") or status)


def create_snapshot(
    url: str,
    filepath: str,
    username: str = '',
    content_type: str | None = None,
):
    config = get_snapshot_config(url, username=username)
    if config and content_type:
        config["_response_content_type"] = content_type
    _reject_by_health(url, config, username)
    # Pre-flight: for auto-type cookie sites without cookies, acquire via browser first.
    try:
        cookie_config = config.get("cookie") if config else {}
        if cookie_config and cookie_config.get("type") == "auto":
            has_cookie = bool(
                config.get("_user_cookie") or
                (get_shared_cookie(hostname=config.get("_domain_key", ""), scope=config.get("_effective_cookie_scope", ""))[0] if config.get("_domain_key") else None)
            )
            if not has_cookie and cookie_config.get("refresh"):
                domain_key = config.get("_domain_key")
                logger.info("No cookie for auto site %s, acquiring via browser refresh", domain_key)
                new_cookie = verify_and_refresh(
                    cookie_config=cookie_config,
                    url=config.get("_request_url", url),
                    domain_key=domain_key,
                    verify_context={"url": config.get("_request_url", url), "title": "", "body_preview": ""},
                    scope=config.get('_effective_cookie_scope', ''),
                )
                if new_cookie:
                    config["_user_cookie"] = new_cookie
                    logger.info("Pre-flight acquired cookie for %s, proceeding with snapshot", domain_key)
                else:
                    logger.warning("Pre-flight failed to acquire cookie for %s", domain_key)
    except Exception as e:
        logger.error("Pre-flight cookie acquisition error for %s: %s", url, e)
    _run_snapshot(url, filepath, config)
    if config and _verify_snapshot_cookie(url, filepath, config):
        _run_snapshot(url, filepath, config)
    return None


def _create_snapshot(url: str, filepath, config: dict = None):
    return singlefile.create_snapshot(url, filepath, config)
