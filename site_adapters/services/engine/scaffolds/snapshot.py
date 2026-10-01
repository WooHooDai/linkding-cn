"""
Snapshot hook scripts for site-adapters (Python external mode).

Python snapshot scripts always run outside SingleFile. Use them for
pre-fetching, cookie/header/request URL handling, custom HTML generation, or
final file rewriting.

For DOM-level hooks that should run inside the SingleFile browser, use the
JavaScript scaffold `snapshot.js`. This Python scaffold is for external hooks
only.

Each script defines one or more hook functions. The function name is the hook
value from the adapter configuration. Define only the hooks you need.

Hook execution order:
    before scripts -> [replace script OR SingleFile engine] -> after scripts

--------------------------------------------------------------------------------
Timeout & resource model  (read before holding heavy resources)
--------------------------------------------------------------------------------

Python hooks run in a daemon worker thread bounded by `timeout`. On timeout the
runner stops waiting but CANNOT interrupt the thread — it keeps running in the
background (JS hooks run as a subprocess and are hard-killed on timeout; Python
hooks are not). Do NOT let a hook hold heavy resources across the timeout: a
browser/Playwright instance, a DB connection, or a long-running subprocess will
leak (orphaned event loops / open handles survive the timeout). Acquire such
resources, do bounded work, and release them in a `finally` block, or raise the
hook's `timeout`.

--------------------------------------------------------------------------------
Config keys available in every hook
--------------------------------------------------------------------------------

  General:
    headers            dict[str,str]  HTTP request headers
    timeout            int|None       Timeout in seconds
    proxy              str|None       HTTP proxy URL
    request_url        str|None       Resolved request URL
    auth               dict           Merged auth config
    cookie             dict           Cookie configuration
    user_cookie        str|None       Best available cookie string

  Snapshot-specific:
    keep_elements      list[str]      CSS selectors to keep
    remove_elements    list[str]      CSS selectors to remove
    process_lazy_images bool|list[str]
    remove_classes     dict           CSS classes to remove
    set_styles         dict           Inline styles to set
    singlefile_args    dict           SingleFile CLI args
    toggles            dict           User-toggleable controls
    scripts            list[dict]     Script hooks configuration

--------------------------------------------------------------------------------
Project helpers commonly useful in custom Python hooks
--------------------------------------------------------------------------------

  # Browser engine selected by project settings (Chromium / CloakBrowser):
  # from site_adapters.services.engine.browser_provider import launch_browser, get_browser_config

  # Cookie conversion for Playwright contexts:
  # from site_adapters.services.auth.cookies import cookie_string_to_playwright_list

  # Stored credentials, with user-first/shared fallback helpers:
  # from site_adapters.services.auth.credentials import get_best_cookie, get_user_cookie, get_shared_cookie
  # from site_adapters.services.auth.credentials import get_best_header, get_best_token, get_best_basic_auth

  # OAuth2 access token cache/refresh:
  # from site_adapters.services.auth.oauth2 import get_valid_token

  # Resolve merged adapter configs for another URL/section:
  # from site_adapters.services.config.resolver import get_metadata_config, get_snapshot_config, get_reader_config

  # Delegate back to built-in engines from a replace hook:
  # from site_adapters.services.engine import create_snapshot, parse_metadata, run_script

--------------------------------------------------------------------------------
File handling
--------------------------------------------------------------------------------

  before: return an HTML string to feed to SingleFile, or None to let
          SingleFile fetch the URL normally.

  replace: write a complete HTML file to output_path.

  after: read and modify output_path in-place.

  The framework creates and cleans up temp files automatically.
"""


def before(url: str, config: dict) -> str | None:
    """
    Hook: before

    Executes before SingleFile. Return HTML to feed to SingleFile instead of
    re-fetching the URL; return None to let SingleFile fetch the URL normally.

    Example - expand collapsed content in the configured project browser:
        from site_adapters.services.engine.browser_provider import launch_browser

        browser = launch_browser(headless=True)
        playwright = getattr(browser, "__playwright__", None)
        try:
            page = browser.new_page()
            page.goto(config.get("request_url") or url, wait_until="networkidle")
            for btn in page.query_selector_all(".read-more-btn"):
                try:
                    btn.click()
                    page.wait_for_timeout(300)
                except Exception:
                    pass
            html = page.content()
        finally:
            browser.close()
            if playwright:
                playwright.stop()
        return html

    Example - return a fixed HTML document:
        return "<html><body><h1>Captured</h1></body></html>"
    """
    return None


def replace(url: str, config: dict, output_path: str) -> None:
    """
    Hook: replace

    Completely replaces the SingleFile engine. Write a complete,
    self-contained HTML file to `output_path`.

    Example - capture with the configured project browser:
        from site_adapters.services.engine.browser_provider import launch_browser

        browser = launch_browser(headless=True)
        playwright = getattr(browser, "__playwright__", None)
        try:
            page = browser.new_page()
            page.goto(config.get("request_url") or url, wait_until="networkidle")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            html = page.content()
        finally:
            browser.close()
            if playwright:
                playwright.stop()

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    Example - custom work then delegate to SingleFile:
        from site_adapters.services.engine import create_snapshot

        # ... do custom preprocessing ...
        create_snapshot(url, output_path, config)
    """
    pass


def after(output_path: str, config: dict) -> None:
    """
    Hook: after

    Executes after the snapshot HTML file is written. Modify the file
    at `output_path` in-place.

    Example - inject dark mode CSS:
        with open(output_path, "r+", encoding="utf-8") as f:
            html = f.read()
            html = html.replace(
                "</head>",
                "<style>body{background:#111;color:#eee}</style></head>",
            )
            f.seek(0)
            f.write(html)
            f.truncate()

    Example - rewrite CDN image URLs:
        import re

        with open(output_path, "r+", encoding="utf-8") as f:
            html = f.read()
            html = re.sub(
                r"https://cdn\\.example\\.com/",
                "https://example.com/",
                html,
            )
            f.seek(0)
            f.write(html)
            f.truncate()
    """
    pass
