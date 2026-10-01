"""
浏览器引擎提供者 — 运行期按 LD_BROWSER_ENGINE 选择引擎

CloakBrowser 二进制由构建期下载并固化；Chromium 模式会在运行期发现
可执行路径。运行期根据配置启动对应引擎。

Public API:
    get_browser_config()  → dict with engine, binary_path, etc.
    launch_browser()      → Playwright Browser instance
"""

import logging
import os
import shutil

from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def get_browser_config() -> dict:
    """返回当前浏览器引擎配置（供 JS 脚本等外部调用方参考）。"""
    engine = getattr(settings, 'LD_BROWSER_ENGINE', 'cloakbrowser')
    return {
        'engine': engine,
        'license_type': getattr(settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_TYPE', 'free'),
        'license_key': getattr(settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_KEY', ''),
        'chromium_path': getattr(settings, 'LD_BROWSER_CHROMIUM_PATH', ''),
    }


# ---------------------------------------------------------------------------
# Chromium 路径发现
# ---------------------------------------------------------------------------

def _find_chromium_path() -> str:
    """查找系统 chromium 可执行路径。"""
    cfg_path = getattr(settings, 'LD_BROWSER_CHROMIUM_PATH', '')
    if cfg_path:
        return cfg_path

    env_path = os.environ.get('CHROMIUM_PATH', '')
    if env_path:
        return env_path

    for path in [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    for binary in ['chromium', 'chromium-browser', 'google-chrome']:
        found = shutil.which(binary)
        if found:
            return found

    raise FileNotFoundError(
        'Chromium not found. Install chromium or set LD_BROWSER_CHROMIUM_PATH.'
    )


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def launch_browser(headless: bool = True, **kwargs):
    """启动浏览器实例（CloakBrowser 或 Playwright+Chromium）。"""
    engine = getattr(settings, 'LD_BROWSER_ENGINE', 'cloakbrowser')

    if engine == 'cloakbrowser':
        return _launch_cloakbrowser(headless=headless, **kwargs)
    elif engine == 'chromium':
        return _launch_chromium(headless=headless, **kwargs)
    else:
        raise ValueError(f'Unknown LD_BROWSER_ENGINE: {engine!r}')


def _launch_cloakbrowser(headless: bool = True, **kwargs):
    from cloakbrowser import launch
    license_key = getattr(settings, 'LD_BROWSER_CLOAKBROWSER_LICENSE_KEY', '')
    launch_kwargs = dict(headless=headless)
    if license_key:
        launch_kwargs['license_key'] = license_key
    launch_kwargs.update(kwargs)
    return launch(**launch_kwargs)


def _launch_chromium(headless: bool = True, **kwargs):
    from playwright.sync_api import sync_playwright
    from contextlib import suppress
    exec_path = _find_chromium_path()
    pw = sync_playwright().start()
    launch_args = ['--no-sandbox', '--disable-blink-features=AutomationControlled']
    extra_args = kwargs.pop('args', [])
    launch_args.extend(extra_args)
    try:
        browser = pw.chromium.launch(
            headless=headless,
            executable_path=exec_path,
            args=launch_args,
            **kwargs,
        )
    except Exception:
        # launch() failed after pw already started: stop the Playwright driver
        # here, otherwise its asyncio loop leaks into the calling thread and
        # later Django DB access raises SynchronousOnlyOperation.
        with suppress(Exception):
            pw.stop()
        raise
    browser.__playwright__ = pw
    # Wrap browser.close() so stopping the Playwright event loop is automatic.
    # Without pw.stop() the asyncio loop leaks into the calling thread, and
    # Django's async-unsafe guard then raises SynchronousOnlyOperation on
    # every subsequent DB access (e.g. session lookups) on that pooled thread.
    _original_close = browser.close
    def _close_and_stop_playwright(*a, **kw):
        try:
            _original_close(*a, **kw)
        finally:
            with suppress(Exception):
                pw.stop()
    browser.close = _close_and_stop_playwright
    return browser
