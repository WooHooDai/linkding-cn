import datetime
import importlib
import json
import logging
import os
import re
import unicodedata
import urllib.parse
from pathlib import Path

import tldextract
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

try:
    with open("version.txt") as f:
        app_version = f.read().strip("\n")
except Exception as exc:
    logging.exception(exc)
    app_version = ""


def unique(elements, key):
    return list({key(element): element for element in elements}.values())


def _weekday_name(isoweekday: int) -> str:
    names = {
        1: _("Monday"),
        2: _("Tuesday"),
        3: _("Wednesday"),
        4: _("Thursday"),
        5: _("Friday"),
        6: _("Saturday"),
        7: _("Sunday"),
    }
    return names[isoweekday]


def _localize_datetime(value: datetime.datetime) -> datetime.datetime:
    if timezone.is_aware(value):
        value = timezone.localtime(value)
        return value.replace(tzinfo=None)
    return value


def humanize_absolute_date(
    value: datetime.datetime, now: datetime.datetime | None = None
):
    if not now:
        now = timezone.now()
    value_local = _localize_datetime(value)
    now_local = _localize_datetime(now)
    delta = relativedelta(now_local, value_local)
    yesterday = now_local - relativedelta(days=1)

    is_older_than_a_week = delta.years > 0 or delta.months > 0 or delta.weeks > 0

    if is_older_than_a_week:
        return formats.date_format(value_local, "SHORT_DATE_FORMAT")
    elif value_local.date() == now_local.date():
        return _("Today")
    elif value_local.date() == yesterday.date():
        return _("Yesterday")
    else:
        return _weekday_name(value_local.isoweekday())


def humanize_relative_date(
    value: datetime.datetime, now: datetime.datetime | None = None
):
    if not now:
        now = timezone.now()
    value_local = _localize_datetime(value)
    now_local = _localize_datetime(now)
    delta = relativedelta(now_local, value_local)

    if delta.years > 0:
        return ngettext("%(count)s year ago", "%(count)s years ago", delta.years) % {
            "count": delta.years
        }
    elif delta.months > 0:
        return ngettext("%(count)s month ago", "%(count)s months ago", delta.months) % {
            "count": delta.months
        }
    elif delta.weeks > 0:
        return ngettext("%(count)s week ago", "%(count)s weeks ago", delta.weeks) % {
            "count": delta.weeks
        }
    else:
        yesterday = now_local - relativedelta(days=1)
        if value_local.date() == now_local.date():
            return _("Today")
        elif value_local.date() == yesterday.date():
            return _("Yesterday")
        else:
            return _weekday_name(value_local.isoweekday())


def humanize_absolute_date_short(
    value: datetime.datetime, now: datetime.datetime | None = None
):
    if not now:
        now = timezone.now()
    value_local = _localize_datetime(value)
    now_local = _localize_datetime(now)
    delta = relativedelta(now_local, value_local)
    yesterday = now_local - relativedelta(days=1)

    is_older_than_yesterday = (
        delta.years > 0 or delta.months > 0 or delta.weeks > 0 or delta.days > 0
    )

    if is_older_than_yesterday:
        return formats.date_format(value_local, "SHORT_DATE_FORMAT")
    elif value_local.date() == now_local.date():
        return _("Today")
    elif value_local.date() == yesterday.date():
        return _("Yesterday")
    return formats.date_format(value_local, "SHORT_DATE_FORMAT")


def parse_timestamp(value: str):
    """
    Parses a string timestamp into a datetime value
    First tries to parse the timestamp as milliseconds.
    If that fails with an error indicating that the timestamp exceeds the maximum,
    it tries to parse the timestamp as microseconds, and then as nanoseconds
    :param value:
    :return:
    """
    try:
        timestamp = int(value)
    except ValueError:
        raise ValueError(f"{value} is not a valid timestamp") from None

    try:
        return datetime.datetime.fromtimestamp(timestamp, datetime.UTC)
    except (OverflowError, ValueError, OSError):
        pass

    # Value exceeds the max. allowed timestamp
    # Try parsing as microseconds
    try:
        return datetime.datetime.fromtimestamp(timestamp / 1000, datetime.UTC)
    except (OverflowError, ValueError, OSError):
        pass

    # Value exceeds the max. allowed timestamp
    # Try parsing as nanoseconds
    try:
        return datetime.datetime.fromtimestamp(timestamp / 1000000, datetime.UTC)
    except (OverflowError, ValueError, OSError):
        pass

    # Timestamp is out of range
    raise ValueError(f"{value} exceeds maximum value for a timestamp")


def get_clean_url(url: str) -> str:
    # 清除 url 中所有参数
    parsed_url = urllib.parse.urlparse(url)
    clean_url = urllib.parse.urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            "",  # 清空 params
            "",  # 清空 query (? 后的部分)
            "",  # 清空 fragment (# 后的部分)
        )
    )
    return clean_url


def get_safe_return_url(return_url: str, fallback_url: str):
    # Use fallback if URL is none or URL is not on same domain
    if not return_url or not re.match(r"^/[a-z]+", return_url):
        return fallback_url
    return return_url


def redirect_with_query(request, redirect_url):
    query_string = urllib.parse.urlencode(request.GET)
    if query_string:
        redirect_url += "?" + query_string

    return HttpResponseRedirect(redirect_url)


def generate_username(email, claims):
    # taken from mozilla-django-oidc docs :)
    # Using Python 3 and Django 1.11+, usernames can contain alphanumeric
    # (ascii and unicode), _, @, +, . and - characters. So we normalize
    # it and slice at 150 characters.
    if settings.OIDC_USERNAME_CLAIM in claims and claims[settings.OIDC_USERNAME_CLAIM]:
        username = claims[settings.OIDC_USERNAME_CLAIM]
    else:
        username = email
    return unicodedata.normalize("NFKC", username)[:150]


def get_domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc


_registrable_domain_extractor = tldextract.TLDExtract(suffix_list_urls=None)


def get_registrable_domain(url: str) -> str:
    hostname = urllib.parse.urlparse(url).hostname or ""
    if not hostname:
        return ""

    extracted = _registrable_domain_extractor(hostname)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}".lower()

    if extracted.domain:
        return extracted.domain.lower()

    return hostname.lower()


def search_config_for_domain(url, settings_path, settings_cache=None):
    config = None

    if os.path.exists(settings_path):
        domain_map = load_settings(settings_path, settings_cache)
        if domain_map == "__JSON_ERROR__":
            logging.error(f"【错误】配置文件解析失败：{settings_path}")
            return config
    else:
        logging.error(f"【错误】配置文件路径不存在：{settings_path}")
        return config

    domain = get_domain(url)
    if domain in domain_map:  # 直接命中
        config = domain_map[domain]
    if not config:
        for key in domain_map:  # 解析命中（通用匹配符*）
            if key.startswith("*.") and domain.endswith(key[1:]):
                config = domain_map[key]

    # 域名别名（配置复用）：将另一个域名的配置作为当前域名的配置
    visited = {domain}
    while isinstance(config, str):
        alias = config
        if alias in visited:
            break
        visited.add(alias)
        config = domain_map.get(alias)

    return config


def load_settings(path, cache):
    base_dir = Path(path).resolve().parent
    cache = {} if cache is None else cache
    try:
        mtime = os.path.getmtime(path)
    except (OSError, FileNotFoundError):
        cache["cache"] = None
        cache["mtime"] = None
        return cache["cache"]
    cache_settings = cache.get("cache")
    cache_mtime = cache.get("mtime")
    if cache_settings is None or cache_mtime != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                config_data = json.load(f)
                cache["cache"] = _process_path(config_data, base_dir)
            cache["mtime"] = mtime
        except json.JSONDecodeError:
            cache["cache"] = "__JSON_ERROR__"
            cache["mtime"] = mtime
        except (OSError, FileNotFoundError):
            cache["cache"] = None
            cache["mtime"] = None
    return cache.get("cache")


def _process_path(node, base_dir):
    """解析相对路径"""
    if isinstance(node, dict):
        for key, value in node.items():
            node[key] = _process_path(value, base_dir)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            node[i] = _process_path(item, base_dir)
    elif isinstance(node, str) and (node.startswith("./") or node.startswith("../")):
        # 如果是字符串且以 ./ 或 ../ 开头，就解析它
        # (base_dir / node) 将路径拼接起来
        # .resolve() 将其转换为绝对路径，并处理 ".." 等情况
        return str((base_dir / node).resolve())

    return node


def load_module(path, cache):
    cache = {} if cache is None else cache
    try:
        mtime = os.path.getmtime(path)
    except (OSError, FileNotFoundError):
        return None
    spec = cache.get(path)
    if spec is None or spec[1] != mtime:
        spec = importlib.util.spec_from_file_location("custom_module", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cache[path] = (module, mtime)
    return cache[path][0]


def parse_relative_date_string(date_filter_relative_string):
    """解析相对日期字符串，获取数值、单位，用于前端搜索筛选项显示"""
    if not date_filter_relative_string:
        return None, None
    match = re.match(
        r"^last_(\d+)_(day|week|month|year)s?$", date_filter_relative_string
    )
    if match:
        value = match.group(1)
        unit = match.group(2) + "s"
        return value, unit
    return None, None


def normalize_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""

    url = url.strip()
    if not url:
        return ""

    try:
        parsed = urllib.parse.urlparse(url)

        # Normalize the scheme to lowercase
        scheme = parsed.scheme.lower()

        # Normalize the netloc (domain) to lowercase
        netloc = parsed.hostname.lower() if parsed.hostname else ""
        if parsed.port:
            netloc += f":{parsed.port}"
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth += f":{parsed.password}"
            netloc = f"{auth}@{netloc}"

        # Remove trailing slashes from all paths
        path = parsed.path.rstrip("/") if parsed.path else ""

        # Sort query parameters alphabetically
        query = ""
        if parsed.query:
            query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            query_params.sort(key=lambda x: (x[0], x[1]))
            query = urllib.parse.urlencode(query_params, quote_via=urllib.parse.quote)

        # Keep fragment as-is
        fragment = parsed.fragment

        # Reconstruct the normalized URL
        return urllib.parse.urlunparse(
            (scheme, netloc, path, parsed.params, query, fragment)
        )

    except (ValueError, AttributeError):
        return url


def extract_hostname(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return ""

    if not parsed.hostname:
        return ""

    return parsed.hostname.rstrip(".").lower()


def parse_domain_roots(custom_domain_root: str) -> list[str]:
    roots = []
    if not custom_domain_root:
        return roots

    for line in custom_domain_root.splitlines():
        line = line.strip()
        if "://" not in line:
            line = f"https://{line}"
        hostname = extract_hostname(line)
        if hostname:
            roots.append(hostname)

    # Preserve user-defined order while removing duplicates case-insensitively.
    return list(dict.fromkeys(roots))


def get_matching_domain_roots(hostname: str, domain_roots: list[str]) -> list[str]:
    hostname = hostname.lower()
    matches = []

    for root in domain_roots:
        if hostname == root or hostname.endswith(f".{root}"):
            matches.append(root)

    return sorted(matches, key=lambda value: (value.count("."), value))


def build_domain_filter_value(hostname: str, include_subdomains: bool = False) -> str:
    hostname = hostname.lower()
    if not include_subdomains:
        return hostname
    return f"{hostname} | .{hostname}"


def canonicalize_domain_filter_value(value: str) -> str:
    if not value:
        return ""

    parts = [part.strip().lower() for part in value.split("|")]
    parts = [part for part in parts if part]
    if not parts:
        return ""

    if len(parts) == 1:
        return parts[0]

    exact_parts = [part for part in parts if not part.startswith(".")]
    subdomain_parts = [part for part in parts if part.startswith(".")]
    ordered_parts = sorted(exact_parts) + sorted(subdomain_parts)
    return " | ".join(ordered_parts)


def get_sidebar_domain_filter_value(url: str, custom_domain_root: str = "") -> str:
    hostname = extract_hostname(url)
    if not hostname:
        return ""

    domain_roots = parse_domain_roots(custom_domain_root)
    matching_roots = get_matching_domain_roots(hostname, domain_roots)
    if not matching_roots:
        return build_domain_filter_value(hostname)

    return build_domain_filter_value(matching_roots[-1], include_subdomains=True)
