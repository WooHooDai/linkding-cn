import logging
import re

from bookmarks.services.structured_reader import (
    html_xpath_to_html,
    json_path_to_html,
    json_to_html,
    xml_to_html,
    xml_xpath_to_html,
)
from site_adapters.services.config.resolver import get_reader_config

logger = logging.getLogger(__name__)


def _extract_defuddle_options(config: dict) -> dict:
    """Extract defuddle options from config."""
    from site_adapters.services.config.validator import is_known_defuddle_param
    args = config.get("defuddle_args", {})
    return {k: v for k, v in args.items() if is_known_defuddle_param(k)}


def _selector_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _is_xpath_selector(selector: str) -> bool:
    value = selector.strip()
    if value.startswith(("/", "(", "./", ".//")):
        return True
    if re.match(
        r"^(?:string|boolean|number|concat|normalize-space|substring|contains)\s*\(",
        value,
    ):
        return True
    return bool(
        re.match(
            r"^(?:ancestor|ancestor-or-self|attribute|child|descendant|"
            r"descendant-or-self|following|following-sibling|namespace|parent|"
            r"preceding|preceding-sibling|self)::",
            value,
        )
    )


def _is_json_path_selector(selector: str) -> bool:
    value = selector.strip()
    if value.startswith("$"):
        return True
    if re.match(r"^\[\s*(?:\d+|['\"])", value):
        return True
    return bool(
        re.match(r"^[A-Za-z_][\w-]*\s*\[\s*(?:\d+|['\"])", value)
    )


def _resolve_html_content_selector(
    html_content: str, options: dict
) -> tuple[str, dict]:
    """Resolve XPath content selectors before handing HTML to defuddle."""
    selector_value = options.get("contentSelector")
    if not selector_value:
        return html_content, options

    if isinstance(selector_value, str):
        if not _is_xpath_selector(selector_value):
            return html_content, options
        selected = html_xpath_to_html(html_content, [selector_value])
        if selected:
            return selected, {**options, "contentSelector": "article"}
        resolved = dict(options)
        resolved.pop("contentSelector", None)
        return html_content, resolved

    selectors = _selector_list(selector_value)
    xpath_selectors = [
        selector for selector in selectors if _is_xpath_selector(selector)
    ]
    if not xpath_selectors:
        return html_content, options

    selected = html_xpath_to_html(html_content, xpath_selectors)
    if selected:
        return selected, {**options, "contentSelector": "article"}

    css_selectors = [
        selector for selector in selectors if not _is_xpath_selector(selector)
    ]
    if css_selectors:
        content_selector = (
            css_selectors[0] if len(css_selectors) == 1 else css_selectors
        )
        return html_content, {**options, "contentSelector": content_selector}

    resolved = dict(options)
    resolved.pop("contentSelector", None)
    return html_content, resolved


def _resolve_structured_content_selector(
    content: str, content_type: str, options: dict
) -> tuple[str | None, dict]:
    """Resolve JSONPath/XPath content selectors against raw JSON/XML."""
    selector_value = options.get("contentSelector")
    if not selector_value:
        return None, options

    if isinstance(selector_value, str):
        selectors = [selector_value]
    else:
        selectors = _selector_list(selector_value)

    if content_type == "json":
        path_selectors = [
            selector
            for selector in selectors
            if _is_json_path_selector(selector)
        ]
    elif content_type == "xml":
        path_selectors = [
            selector for selector in selectors if _is_xpath_selector(selector)
        ]
    else:
        path_selectors = []

    if not path_selectors:
        return None, options

    if content_type == "json":
        selected = json_path_to_html(content, path_selectors)
    else:
        selected = xml_xpath_to_html(content, path_selectors)
    if selected:
        return selected, {**options, "contentSelector": "article"}

    css_selectors = [
        selector for selector in selectors if selector not in path_selectors
    ]
    if css_selectors:
        content_selector = (
            css_selectors[0] if len(css_selectors) == 1 else css_selectors
        )
        return None, {**options, "contentSelector": content_selector}

    resolved = dict(options)
    resolved.pop("contentSelector", None)
    return None, resolved


def _normalize_html_for_reader(html_content: str) -> str:
    """Unwrap serialized shadow DOM templates for reader extraction.

    The original snapshot is not modified; this function returns a temporary
    normalized copy that Defuddle can parse as ordinary HTML.
    """
    if not html_content:
        return html_content

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    for template in soup.find_all("template", attrs={"shadowrootmode": True}):
        for child in list(template.contents):
            template.insert_before(child)
        template.decompose()
    return str(soup)


def _collect_carousels(html_content: str) -> list[str]:
    """Extract processed carousels from normalized snapshot HTML."""
    if not html_content or "ld-carousel" not in html_content:
        return []
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    return [
        str(figure)
        for figure in soup.find_all("figure", attrs={"aria-label": "ld-carousel"})
    ]


def _carousel_is_before_text(html_content: str, carousel: str) -> bool:
    """Return whether the given carousel fragment appears before the first paragraph in the source.

    Each carousel is located by its own serialized fragment, so multiple
    carousels on one page are classified independently instead of all being
    bucketed by the first carousel's position.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    paragraph = next(
        (
            item
            for item in soup.find_all("p")
            if len(item.get_text(" ", strip=True)) >= 80
        ),
        None,
    ) or soup.find("p")
    if not carousel or not paragraph:
        return False

    serialized = str(soup)
    carousel_index = serialized.find(carousel)
    paragraph_index = serialized.find(str(paragraph))
    return (
        carousel_index >= 0
        and paragraph_index >= 0
        and carousel_index < paragraph_index
    )


def _restore_missing_carousels(
    html_content: str, extracted_content: str, carousels: list[str]
) -> str:
    """Restore processed carousels that site-specific defuddle extractors omit."""
    if not carousels or "ld-carousel" in extracted_content:
        return extracted_content

    before = [
        carousel
        for carousel in carousels
        if _carousel_is_before_text(html_content, carousel)
    ]
    after = [
        carousel for carousel in carousels if carousel not in before
    ]

    if before:
        fragment = "".join(before)
        article_match = re.search(r"<article\b[^>]*>", extracted_content, re.I)
        if article_match:
            extracted_content = (
                extracted_content[: article_match.end()]
                + fragment
                + extracted_content[article_match.end() :]
            )
        else:
            extracted_content = fragment + extracted_content

    if after:
        fragment = "".join(after)
        article_close = re.search(r"</article>", extracted_content, re.I)
        if article_close:
            extracted_content = (
                extracted_content[: article_close.start()]
                + fragment
                + extracted_content[article_close.start() :]
            )
        else:
            extracted_content = extracted_content + fragment

    return extracted_content


def parse_html(html_content: str, url: str = "", username: str = "") -> dict:
    """
    Extract content from HTML.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle
    """
    config = get_reader_config(url, username=username)

    defuddle_opts = _extract_defuddle_options(config) if config else {}

    return _parse_html(
        html_content,
        url=url,
        username=username,
        defuddle_opts=defuddle_opts,
    )


def _parse_html(
    html_content: str,
    url: str,
    username: str,
    defuddle_opts: dict,
) -> dict:
    from bookmarks.services import defuddle

    html_content = _normalize_html_for_reader(html_content)
    html_content, defuddle_opts = _resolve_html_content_selector(
        html_content, defuddle_opts
    )
    carousels = _collect_carousels(html_content)

    result = defuddle.parse_html(
        html_content, url=url, options=defuddle_opts or None
    )
    if result.get("content"):
        result["content"] = _restore_missing_carousels(
            html_content, result["content"], carousels
        )
    return result


def parse_url(url: str, username: str = "") -> dict:
    """
    Extract content from URL.

    Dispatch logic:
    1. defuddle_args defined -> defuddle with options
    2. No config -> default defuddle

    抓取复用 loader 通路（http_engine / auto cookie / 403 自愈）后交给
    defuddle 解析 HTML，避免 node 直连被 TLS 指纹风控站点拦截（如知乎）。
    """
    from bookmarks.services import defuddle, website_loader

    config = get_reader_config(url, username=username)

    defuddle_opts = _extract_defuddle_options(config) if config else {}

    try:
        html = website_loader.fetch_page_html(url, config, username=username)
    except Exception as exc:
        raise defuddle.DefuddleError(f"Failed to fetch page: {exc}") from exc

    return _parse_html(
        html,
        url=url,
        username=username,
        defuddle_opts=defuddle_opts,
    )


def parse_content(
    content: str,
    content_type: str,
    url: str = "",
    username: str = "",
) -> dict:
    """Extract article content from an HTML, XML, or JSON snapshot.

    Defuddle itself only accepts HTML documents, so XML/JSON snapshots are
    converted to a semantic HTML document before being passed to defuddle.
    """
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    config = get_reader_config(url, username=username)
    defuddle_opts = _extract_defuddle_options(config) if config else {}

    if normalized_type in (
        "xml",
        "application/xml",
        "text/xml",
        "application/rss+xml",
        "application/atom+xml",
    ):
        try:
            selected, defuddle_opts = _resolve_structured_content_selector(
                content, "xml", defuddle_opts
            )
            content = selected or xml_to_html(content)
        except Exception:
            # Malformed XML must not bubble up to the task layer (which would
            # abort the reader task and delete article assets). Fall through to
            # lenient HTML extraction on the raw snapshot instead.
            logger.warning(
                "Failed to parse XML snapshot, falling back to HTML extraction"
            )
    elif normalized_type in ("json", "application/json"):
        try:
            selected, defuddle_opts = _resolve_structured_content_selector(
                content, "json", defuddle_opts
            )
            content = selected or json_to_html(content)
        except Exception:
            # Malformed JSON: degrade to plain HTML extraction rather than
            # failing the whole reader task.
            logger.warning(
                "Failed to parse JSON snapshot, falling back to HTML extraction"
            )
    return _parse_html(
        content,
        url=url,
        username=username,
        defuddle_opts=defuddle_opts,
    )
