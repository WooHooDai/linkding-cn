
"""
Script execution engine.

Public API:
    from site_adapters.services.engine import run_script
    from site_adapters.services.engine import parse_metadata
    from site_adapters.services.engine import create_snapshot
    from site_adapters.services.engine.browser_provider import launch_browser, get_browser_config
"""

from site_adapters.services.engine.script_runner import run_script


def parse_metadata(content: str, url: str, config: dict) -> dict:
    """Built-in metadata parser exposed for use in replace scripts.

    Args:
        content: HTML, XML, or JSON response string to parse.
        url: Page URL (for relative image resolution).
        config: Merged config dict (user-facing keys: content_type, select_*, etc.)

    Returns:
        dict with title, description, author, image, url (any can be None).
    """
    from bookmarks.services.website_loader import _parse_metadata_from_content

    title, description, preview_image, author, _ = _parse_metadata_from_content(
        content, url, config, include_sources=True
    )
    return {
        "title": title,
        "description": description,
        "author": author,
        "image": preview_image,
        "url": url,
    }


from bookmarks.services.singlefile import create_snapshot

__all__ = ["run_script", "parse_metadata", "create_snapshot"]
