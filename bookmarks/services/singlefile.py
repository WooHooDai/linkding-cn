import json
import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from contextlib import suppress

from django.conf import settings

from site_adapters.services.auth.credentials import get_implicit_manual_cookie
from site_adapters.services.auth.cookies import (
    copy_cookie_file_to_temp,
    generate_temp_cookies_file,
)
from site_adapters.services.execution_log import log_execution


class SingleFileError(Exception):
    pass


logger = logging.getLogger(__name__)


def _resolve_browser_path() -> str | None:
    """当 LD_BROWSER_ENGINE=cloakbrowser 时，解析 cloakbrowser 二进制路径。

    返回 None 表示让 SingleFile 按默认行为从 PATH 寻找 chromium。
    放在 required_options（最低优先级），用户可通过 LD_SINGLEFILE_OPTIONS 显式覆盖。
    """
    engine = getattr(settings, 'LD_BROWSER_ENGINE', 'chromium')
    if engine != 'cloakbrowser':
        return None
    try:
        from cloakbrowser import ensure_binary
        return ensure_binary()
    except ImportError:
        logger.warning(
            "LD_BROWSER_ENGINE=cloakbrowser but cloakbrowser package not installed. "
            "Install with: pip install cloakbrowser && python -m cloakbrowser install. "
            "Falling back to system chromium."
        )
        return None


def get_custom_options(config: dict):
    if config:
        custom_options = config.get("singlefile_args")
    else:
        logger.debug("No config provided")
        return []

    if not custom_options:
        logger.debug("No singlefile_args provided")
        return []

    args = []

    if isinstance(custom_options, dict):
        from site_adapters.services.config.validator import is_known_singlefile_arg
        for arg, value in custom_options.items():
            if not is_known_singlefile_arg(arg):
                logger.warning("Ignoring unknown SingleFile arg: %s", arg)
                continue
            if value is True:
                args.append(arg)
            elif value is False:
                continue
            elif value is None:
                continue
            elif isinstance(value, list):
                args.extend(f"{arg}={item}" for item in value)
            else:
                args.append(f"{arg}={value}")
    else:
        logger.error("singlefile_args must be a dict, got %s", type(custom_options).__name__)
        return []

    logger.debug("SingleFile custom args: %s", args)
    return args


def _as_list(value):
    if not value:
        return []
    return value if isinstance(value, list) else [value]


_BUILTIN_ENGINE_RE = re.compile(
    r'^[ \t]*(?:const|let)\s+builtin_engine\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(null))\s*;?',
    re.MULTILINE,
)


def read_builtin_engine(script_path: str) -> str | None:
    """Read the builtin_engine declaration from a snapshot JS script."""
    with open(script_path, encoding='utf-8') as f:
        source = f.read()
    match = _BUILTIN_ENGINE_RE.search(source)
    if not match:
        raise SingleFileError(
            f"Snapshot JS script must declare builtin_engine: {script_path}"
        )
    if match.group(3) is not None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def uses_builtin_engine(script_path: str, hook_name: str = '') -> bool:
    """Return whether a snapshot JS before/after hook runs inside SingleFile."""
    if not script_path.endswith('.js') or hook_name not in ('before', 'after'):
        return False
    engine = read_builtin_engine(script_path)
    if engine == 'singlefile':
        return True
    if engine in ('', None):
        return False
    raise SingleFileError(
        f"Unsupported builtin_engine value {engine!r} in {script_path}"
    )


_BROWSER_HOOK_BOILERPLATE = r"""
(() => {
  dispatchEvent(new CustomEvent("single-file-user-script-init"));

  const runHook = async (event) => {
    event.preventDefault();
    try {
      for (const hook of window.__linkdingHooks || []) {
        const fn = hook["before"];
        if (typeof fn === "function") {
          await fn(
            window.__linkding_snapshot_config.url,
            window.__linkding_snapshot_config.config
          );
        }
      }
      // Wait for cleanup (including wait_elements) if the cleanup script
      // registered an async cleanup function.
      if (typeof window.__linkdingCleanup === "function") {
        await window.__linkdingCleanup();
      }
    } finally {
      dispatchEvent(new CustomEvent("single-file-on-before-capture-response"));
    }
  };

  addEventListener(
    "single-file-on-before-capture-request",
    (event) => runHook(event)
  );
})();
"""


_WAIT_ELEMENTS_TIMEOUT_CAP = 30  # seconds
# Bounded default when neither wait_elements_timeout nor section timeout is set.
# Prevents a stray wait_elements selector from hanging the capture forever.
_DEFAULT_WAIT_ELEMENTS_TIMEOUT = 10  # seconds


def _resolve_wait_elements_timeout(config: dict) -> int:
    """Resolve wait_elements_timeout: explicit value, min(timeout, 30), or a bounded default.

    Resolution chain (first matching branch wins):
    1. Explicit ``wait_elements_timeout``: a positive int is used as-is; a
       non-positive value means "no wait timeout" → 0 (the browser script then
       waits unbounded by design).
    2. Section ``timeout``: positive → min(value, 30); non-positive → 0.
    3. Neither configured → the bounded default (10 s). Explicit 0 (from either
       source) stays unbounded; only the truly-unconfigured case is capped.
    """
    if not config:
        return 0
    explicit = config.get("wait_elements_timeout")
    if explicit is not None:
        try:
            val = int(explicit)
            return val if val > 0 else 0
        except (TypeError, ValueError):
            pass
    section_timeout = config.get("timeout")
    if section_timeout is not None:
        try:
            val = int(section_timeout)
            return min(val, _WAIT_ELEMENTS_TIMEOUT_CAP) if val > 0 else 0
        except (TypeError, ValueError):
            pass
    return _DEFAULT_WAIT_ELEMENTS_TIMEOUT


def _wrap_user_hook_script(source: str) -> str:
    checks = [
        "if (typeof before === 'function') "
        "window.__linkdingHooks.push({ before: before });"
    ]
    return "(() => {\n" + source + "\n" + "\n".join(checks) + "\n})();\n"


def _build_browser_script(config: dict, url: str = '') -> str | None:
    if not config:
        # No config at all — still enable default lazy image fix
        cleanup = {"keep": [], "remove": [], "lazy": True, "removeClasses": {}, "setStyles": {}, "waitElements": [], "waitElementsTimeout": 0}
    else:
        lazy = config.get("process_lazy_images")
        # process_lazy_images: true → default attrs; ["data-actualsrc", ...] → custom attrs
        # When not specified, default to True (always fix lazy images)
        if isinstance(lazy, list):
            lazy_config = lazy
        elif lazy is not None:
            lazy_config = bool(lazy)
        else:
            lazy_config = True  # Default: always fix lazy images
        cleanup = {
            "keep": _as_list(config.get("keep_elements")),
            "remove": _as_list(config.get("remove_elements")),
            "carousels": _as_list(config.get("process_carousels")),
            "lazy": lazy_config,
            "removeClasses": config.get("remove_classes") or {},
            "setStyles": config.get("set_styles") or {},
            "waitElements": _as_list(config.get("wait_elements")),
            "waitElementsTimeout": _resolve_wait_elements_timeout(config),
        }
    import site_adapters.services as _sa_services; vendor_path = os.path.join(os.path.dirname(_sa_services.__file__), 'engine', 'scripts', 'snapshot_browser_script.js')
    with open(vendor_path, encoding='utf-8') as f:
        script = f.read()

    parts = []
    before_paths = _as_list(config.get('_browser_before_scripts'))

    if before_paths:
        from site_adapters.services.engine.script_runner import _sanitize_config
        injected_url = url or config.get('_request_url') or config.get('_url') or ''
        parts.append(
            "window.__linkding_snapshot_config = "
            + json.dumps(
                {"url": injected_url, "config": _sanitize_config(config)},
                ensure_ascii=False,
            )
            + ";\n"
        )
        parts.append("window.__linkdingHooks = [];\n")
        for script_path in before_paths:
            with open(script_path, encoding='utf-8') as f:
                source = f.read()
            parts.append(_wrap_user_hook_script(source))
        parts.append(_BROWSER_HOOK_BOILERPLATE)

    preamble = "window.__linkding_cleanup_config = " + json.dumps(cleanup) + ";\n"
    parts.append(preamble + script)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write("".join(parts))
        return tmp.name


def _build_site_adapter_options(url: str, config: dict) -> tuple[list[str], list[str]]:
    if not config:
        return [], []
    options = []
    temp_files = []
    headers = config.get("headers") or {}
    for name, value in headers.items():
        if value is None or name.lower() == "cookie":
            continue
        if name.lower() == "user-agent":
            # 仅显式配置的 UA 注入快照浏览器；默认无 UA 时使用引擎自身 UA
            # （全局 UA 与运行引擎平台失配会被反爬站点拦截）。
            # 延迟导入避免 engine → singlefile → website_loader 循环导入。
            from bookmarks.services.website_loader import _has_explicit_user_agent

            if not _has_explicit_user_agent(config):
                continue
            options.append(f"--user-agent={value}")
        else:
            options.append(f"--http-header={name}: {value}")
    if config.get("proxy"):
        options.append(f"--http-proxy-server={config['proxy']}")
    user_cookie = config.get("_user_cookie") or config.get("user_cookie")
    cookie_file = None
    snapshot_scope = config.get("_effective_cookie_scope", "")
    if user_cookie:
        cookie_file = generate_temp_cookies_file(
            domain_key=config.get("_domain_key") or config.get("domain_key", ""),
            cookie_str=user_cookie, scope=snapshot_scope)
        if cookie_file:
            temp_files.append(cookie_file)
    # Implicit injection for sections without an auth config: only
    # user-provided (manual) cookies qualify. Auto cookies are config-driven
    # and must not bleed into sections that never declared auth, or stale
    # refresh leftovers would be forced onto every snapshot.
    if not cookie_file and (config.get("_domain_key") or config.get("domain_key")):
        domain_key = config.get("_domain_key") or config.get("domain_key")
        manual_cookie = get_implicit_manual_cookie(
            hostname=domain_key, section_scope=snapshot_scope)
        if manual_cookie:
            cookie_file = generate_temp_cookies_file(
                domain_key=domain_key, cookie_str=manual_cookie, scope=snapshot_scope)
            if cookie_file:
                temp_files.append(cookie_file)
    if cookie_file:
        options.append(f"--browser-cookies-file={cookie_file}")
    browser_script = _build_browser_script(config, url=url)
    if browser_script:
        options.append(f"--browser-script={browser_script}")
        temp_files.append(browser_script)
    return options, temp_files


def _strip_user_agent(options: list[str]) -> list[str]:
    """剔除框架级选项（env）中的 --user-agent。

    全局 UA 无法匹配运行环境引擎，注入会导致反爬站点拦截快照；
    UA 只允许来自适配器显式配置，或由浏览器使用引擎自身 UA。
    """
    out = []
    i = 0
    while i < len(options):
        opt = options[i]
        if opt.startswith("--user-agent"):
            # 支持 --user-agent=VALUE 与 --user-agent VALUE 两种形式
            if "=" not in opt:
                i += 1  # 跳过值 token
            i += 1
            continue
        out.append(opt)
        i += 1
    return out


_PROFILE_ARG_STRIPPED_WARNED = False


def _tail(text, limit: int = 2000) -> str:
    """取子进程输出的尾部若干字符——浏览器把真正的报错打在最后几行。"""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def _with_tail(message: str, output) -> str:
    output = _tail(output)
    return f"{message}: {output}" if output else message


def _describe_owner(path: str) -> tuple[int | None, str]:
    """返回目录 (属主 uid, 权限串)，用于"为什么不可写"的报错。"""
    try:
        stat = os.stat(path)
    except OSError:
        return None, "unknown"
    return stat.st_uid, oct(stat.st_mode & 0o777)


def resolve_profile_dir() -> str:
    """解析并校验 Chromium profile 目录；不可用即失败，不做降级。

    必须绝对路径：相对路径按 CWD 解析，会让不同用户命中同一目录、互相夺走属主，
    浏览器随即建不出 ProcessSingleton 而退出。
    """
    configured = getattr(settings, "LD_CHROMIUM_PROFILE_DIR", "")
    if not configured:
        raise SingleFileError(
            "LD_CHROMIUM_PROFILE_DIR is empty. Set it to an absolute, writable "
            "directory, e.g. /etc/linkding/data/chromium-profile."
        )
    if not os.path.isabs(configured):
        raise SingleFileError(
            f"LD_CHROMIUM_PROFILE_DIR must be an absolute path, got {configured!r}. "
            "A relative path is resolved against the process working directory, "
            "which lets processes running as different users share the same profile "
            "dir and take ownership of each other's files. "
            "Use e.g. /etc/linkding/data/chromium-profile."
        )
    try:
        os.makedirs(configured, mode=0o700, exist_ok=True)
    except OSError as error:
        raise SingleFileError(
            f"Cannot create LD_CHROMIUM_PROFILE_DIR {configured!r}: {error}. "
            "Point it at a writable directory, e.g. /etc/linkding/data/chromium-profile."
        ) from None
    if not os.access(configured, os.R_OK | os.W_OK | os.X_OK):
        uid = getattr(os, "getuid", lambda: 0)()
        owner_uid, mode = _describe_owner(configured)
        if owner_uid is not None and owner_uid != uid:
            hint = (
                "It is most likely owned by another user, e.g. created by a root "
                "shell inside the container. Fix the ownership or point the setting "
                "at a writable directory."
            )
        else:
            hint = (
                "Its mode does not grant read/write/traverse access to this user. "
                "Fix the permissions or point the setting at a writable directory."
            )
        raise SingleFileError(
            f"LD_CHROMIUM_PROFILE_DIR {configured!r} is not accessible by the current "
            f"user (uid={uid}); directory is owner uid={owner_uid} mode={mode}. {hint}"
        )
    return configured


def _strip_browser_profile_dir(options: list[str]) -> list[str]:
    """剔除 env 选项里的 --user-data-dir：profile 只由 resolve_profile_dir 决定。"""
    global _PROFILE_ARG_STRIPPED_WARNED

    out = []
    for opt in options:
        if opt.startswith("--browser-arg=--user-data-dir"):
            if not _PROFILE_ARG_STRIPPED_WARNED:
                _PROFILE_ARG_STRIPPED_WARNED = True
                logger.warning(
                    "Ignoring %s from LD_SINGLEFILE_*_OPTIONS; the profile dir is "
                    "managed by LD_CHROMIUM_PROFILE_DIR.",
                    opt,
                )
            continue
        out.append(opt)
    return out


def _terminate_process(process) -> str:
    """超时收尾：先 SIGTERM，必要时杀掉整个进程组，并回收残留输出。"""
    logger.error("Timeout expired while creating snapshot. Terminating process...")
    try:
        process.terminate()
        output, _ = process.communicate(timeout=20)
        return output or ""
    except subprocess.TimeoutExpired:
        # Kill the whole process group, which should also clean up any chromium
        # processes spawned by single-file
        logger.error("Timeout expired while terminating. Killing process group...")
        with suppress(OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with suppress(Exception):
            output, _ = process.communicate(timeout=5)
            return output or ""
    return ""


def create_snapshot(url: str, filepath: str, config: dict = None):
    singlefile_path = settings.LD_SINGLEFILE_PATH
    profile_dir = resolve_profile_dir()

    custom_options = get_custom_options(config)
    injected_options, temp_files = _build_site_adapter_options(url, config)
    global_options = _strip_browser_profile_dir(
        _strip_user_agent(shlex.split(settings.LD_SINGLEFILE_OPTIONS))
    )
    ublock_options = _strip_browser_profile_dir(
        _strip_user_agent(shlex.split(settings.LD_SINGLEFILE_UBLOCK_OPTIONS))
    )
    required_options = [
        # see the field `_builtin.snapshot.single_args` in `site_adapters/services/config/adapters/defaults/adapters.jsonc`
        f"--browser-arg=--user-data-dir={profile_dir}",
    ]
    # 自动解析 cloakbrowser 路径（最低优先级，允许显式覆盖）
    browser_path = _resolve_browser_path()
    if browser_path:
        required_options.append(f"--browser-executable-path={browser_path}")

    # Args that allow multiple values (not deduplicated by name)
    multi_value_arg_list = [
        "--browser-script",
        "--browser-stylesheet",
        "--browser-arg",
        "--browser-cookie",
        "--crawl-rewrite-rule",
        "--emulate-media-feature",
        "--http-header",
    ]

    def merge_option(target_options, merged_options):
        """Merge merged_options into target_options (lowest priority first).
        Higher-priority calls override same-name args from earlier calls.
        Multi-value args (e.g. --browser-arg) accumulate across levels."""
        for opt in merged_options:
            arg_name = opt.split("=", 1)[0]
            if arg_name in multi_value_arg_list:
                if opt not in target_options:
                    target_options.append(opt)
            else:
                # Find and replace existing same-name arg, or append
                for i, existing in enumerate(target_options):
                    if existing.split("=", 1)[0] == arg_name:
                        target_options[i] = opt
                        break
                else:
                    target_options.append(opt)

    # Process from lowest to highest priority; later appends override earlier ones
    result_options = []
    merge_option(result_options, required_options)
    merge_option(result_options, ublock_options)
    merge_option(result_options, global_options)
    merge_option(result_options, injected_options)
    merge_option(result_options, custom_options)

    # If before hook provided HTML, use it as the capture target
    before_html = config.get("_before_html_path") if config else None
    snapshot_url = before_html if before_html else (config.get("_request_url", url) if config else url)
    args = [singlefile_path] + result_options + [snapshot_url, filepath]

    logger.debug("SingleFile full args: %s", args)

    start = time.monotonic()
    process = None
    try:
        with suppress(OSError):
            os.remove(filepath)
        # Use start_new_session=True to create a new process group.
        # 捕获输出：浏览器退出的真实原因在 stderr，必须带进失败信息。
        process = subprocess.Popen(
            args,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        try:
            output, _ = process.communicate(
                timeout=(config.get("timeout") if config else None)
                or settings.LD_SINGLEFILE_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            output = _terminate_process(process)
            log_execution(
                url=snapshot_url,
                domain_key=(config or {}).get("_domain_key", ""),
                step="snapshot",
                cmd=args,
                returncode=-1,
                stderr=_with_tail("Timeout expired while creating snapshot", output),
                duration_ms=int((time.monotonic() - start) * 1000),
                config_snapshot=config,
            )
            raise SingleFileError("Timeout expired while creating snapshot") from None

        if not os.path.exists(filepath):
            raise SingleFileError(
                "Failed to create snapshot "
                f"(exit={process.returncode}): {_tail(output) or 'no output'}"
            )
        logger.debug("SingleFile output: %s", _tail(output, 500))
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=process.returncode if process and process.returncode is not None else 0,
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
    except OSError as error:
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=1,
            stderr=str(error),
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
        raise SingleFileError(f"Failed to start single-file: {error}") from None
    except SingleFileError as error:
        log_execution(
            url=snapshot_url,
            domain_key=(config or {}).get("_domain_key", ""),
            step="snapshot",
            cmd=args,
            returncode=process.returncode if process and process.returncode is not None else 1,
            stderr=str(error),
            duration_ms=int((time.monotonic() - start) * 1000),
            config_snapshot=config,
        )
        raise
    finally:
        for temp_file in temp_files:
            with suppress(OSError):
                os.unlink(temp_file)
