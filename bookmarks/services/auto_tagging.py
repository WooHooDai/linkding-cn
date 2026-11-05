import logging
import re
from urllib.parse import parse_qs, urlparse

import idna

logger = logging.getLogger(__name__)


def get_tags_for_url(rules: str, url: str) -> list[str]:
    """规则是用户手写的，写坏一行只该少打几个标签，不该让整个请求失败。"""
    if not rules or not url:
        return []
    try:
        tag_names = get_tags(rules, url)
    except Exception:
        logger.exception("Failed to auto-tag bookmark. url=%s", url)
        return []
    # 标签名大小写不敏感（Tag 用 name__iexact 复用），只留先出现的那份
    names = []
    seen = set()
    for tag_name in sorted(tag_names):
        if tag_name.lower() not in seen:
            seen.add(tag_name.lower())
            names.append(tag_name)
    return names


def get_tags(script: str, url: str):
    parsed_url = urlparse(url.lower())
    result = set()

    if not parsed_url.hostname:
        return result

    for line in script.split("\n"):
        line = line.strip()

        # Skip empty lines or lines that start with a comment
        if not line or line.startswith("#"):
            continue

        # Remove trailing comment - only if # is preceded by whitespace
        comment_match = re.search(r"\s+#", line)
        if comment_match:
            line = line[: comment_match.start()]

        # Ignore lines that don't contain a URL and a tag
        parts = line.split()
        if len(parts) < 2:
            continue

        # to parse a host name from the pattern URL, ensure it has a scheme
        # 只有 URL 条件按小写比对，标签名保留规则里写的大小写
        pattern_url = ("//" + re.sub("^https?://", "", parts[0])).lower()
        parsed_pattern = urlparse(pattern_url)

        if not _domains_matches(parsed_pattern.hostname, parsed_url.hostname):
            continue

        if parsed_pattern.path and not _path_matches(
            parsed_pattern.path, parsed_url.path
        ):
            continue

        if parsed_pattern.query and not _qs_matches(
            parsed_pattern.query, parsed_url.query
        ):
            continue

        if parsed_pattern.fragment and not _fragment_matches(
            parsed_pattern.fragment, parsed_url.fragment
        ):
            continue

        for tag in parts[1:]:
            result.add(tag)

    return result


def _path_matches(expected_path: str, actual_path: str) -> bool:
    return actual_path.startswith(expected_path)


def _domains_matches(expected_domain: str, actual_domain: str) -> bool:
    expected_domain = idna.encode(expected_domain)
    actual_domain = idna.encode(actual_domain)

    return actual_domain.endswith(expected_domain)


def _qs_matches(expected_qs: str, actual_qs: str) -> bool:
    expected_qs = parse_qs(expected_qs, keep_blank_values=True)
    actual_qs = parse_qs(actual_qs, keep_blank_values=True)

    for key in expected_qs:
        if key not in actual_qs:
            return False
        for value in expected_qs[key]:
            if value != "" and value not in actual_qs[key]:
                return False

    return True


def _fragment_matches(expected_fragment: str, actual_fragment: str) -> bool:
    return actual_fragment.startswith(expected_fragment)
