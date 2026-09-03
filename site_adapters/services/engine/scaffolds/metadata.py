"""
Metadata hook scripts for site-adapters (Python / JavaScript).

Metadata scripts are always executed outside the browser. Python scripts may
modify `result` in-place; JavaScript scripts must return the modified result
dict because they run in a separate Node process.

Each script defines one or more hook functions. The function name is the hook
value from the adapter configuration. Define only the hooks you need; the
framework calls each script for the hooks listed in your adapter config.

Hook execution order:
    before scripts -> [replace script OR built-in engine] -> after scripts

--------------------------------------------------------------------------------
Config keys available in every hook
--------------------------------------------------------------------------------

  General:
    headers            dict[str,str]  HTTP request headers
    timeout            int|None       HTTP timeout in seconds
    proxy              str|None       HTTP proxy URL
    request_url        str|None       Resolved request URL (from request_url pattern)
    rewrite_url        str|None       Resolved rewrite URL
    auth               dict           Merged auth config
    cookie             dict           Cookie configuration
    user_cookie        str|None       Best available cookie string

  Metadata-specific:
    select_title       list[str]      CSS selectors for title
    select_description list[str]      CSS selectors for description
    select_image       list[str]      CSS selectors for preview image; supports ::attr(name)
    rewrite_title      list|None      [pattern, replacement] regex for title
    rewrite_description list|None
    rewrite_image      list|None
    load_full_page     bool           Whether to load full page HTML
    scripts            list[dict]     Script hooks configuration

--------------------------------------------------------------------------------
Return value conventions
--------------------------------------------------------------------------------

  before: return a partial config dict or None. The framework merges returned
          keys into the config before the built-in engine runs.

  replace: return a dict with any of: title, description, image, url.

  after: modify `result` in-place and return None.
"""


def before(url: str, config: dict) -> dict | None:
    """
    Hook: before

    Executes before the main metadata pipeline. Return a partial config dict
    to affect the built-in engine; return None when no config change is needed.

    Supported return keys:
        request_url, user_cookie, headers, timeout, proxy

    Example - acquire a login cookie before the request:
        import requests

        resp = requests.post(
            "https://example.com/login",
            json={"user": "...", "pass": "..."},
        )
        return {
            "user_cookie": "; ".join(
                f"{k}={v}" for k, v in resp.cookies.items()
            )
        }

    Example - set a custom request URL and header:
        return {
            "request_url": "https://api.example.com/v2/articles",
            "headers": {"X-Trace": "metadata-hook"},
        }
    """
    return None


def replace(url: str, config: dict) -> dict:
    """
    Hook: replace

    Completely replaces the built-in metadata engine. The framework makes
    no HTTP request. You are responsible for fetching and parsing the page.

    Returns a dict with any of: title, description, image, url (all optional).

    Example - use the built-in parser with custom HTTP logic:
        import requests
        from site_adapters.services.engine import parse_metadata

        resp = requests.get(
            url,
            headers=config.get("headers", {}),
            timeout=config.get("timeout", 10),
        )
        result = parse_metadata(resp.text, url, config)
        result["title"] = result["title"].replace(" - Suffix", "")
        return result

    Example - full custom parsing:
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(url, headers=config.get("headers", {}))
        soup = BeautifulSoup(resp.text, "html.parser")
        return {
            "title": soup.select_one("h1").text if soup.select_one("h1") else None,
            "description": None,
            "image": None,
            "url": url,
        }
    """
    return {"title": None, "description": None, "image": None, "url": url}


def after(result: dict, url: str, config: dict) -> None:
    """
    Hook: after

    Executes after metadata extraction (by built-in engine or replace script).
    Modify `result` in-place.

    result keys: title, description, image, url (all str|None)

    Example - strip site name from titles:
        result["title"] = (
            result["title"]
            .replace(" - Example", "")
            .replace(" | Example", "")
        )

    Example - fall back to a fixed image:
        if not result["image"]:
            result["image"] = "https://example.com/default.png"
    """
    pass
