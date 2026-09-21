import contextlib
import datetime
import importlib
import json
import logging
import os
import re
import tempfile
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from publicsuffixlist import PublicSuffixList
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

try:
    with open("version.txt") as f:
        app_version = f.read().strip()
except (OSError, FileNotFoundError):
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


def clean_query_params(params) -> str:
    """移除空值参数后编码，避免 URL 中出现 ?q= 这样的残留。
    接受 QueryDict 或普通 dict，返回编码后的 query string。
    QueryDict 的多值参数（如多个 health_status / tags）会全部保留，
    而不是只取最后一个值。
    """
    from django.http import QueryDict

    if isinstance(params, QueryDict):
        cleaned = QueryDict("", mutable=True)
        for key in params.keys():
            values = [v for v in params.getlist(key) if v not in (None, "")]
            if values:
                cleaned.setlist(key, values)
        return cleaned.urlencode()

    cleaned = QueryDict("", mutable=True)
    for key in params:
        value = params[key]
        if value not in (None, ""):
            cleaned[key] = value
    return cleaned.urlencode()


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


_psl = PublicSuffixList()


def get_registrable_domain(url: str) -> str:
    hostname = urllib.parse.urlparse(url).hostname or ""
    if not hostname:
        return ""

    registrable = _psl.privatesuffix(hostname)
    return registrable.lower() if registrable else hostname.lower()


    extracted = _registrable_domain_extractor(hostname)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}".lower()

    if extracted.domain:
        return extracted.domain.lower()

    return hostname.lower()


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
    """解析相对路径，校验结果不越出 base_dir。"""
    if isinstance(node, dict):
        for key, value in node.items():
            node[key] = _process_path(value, base_dir)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            node[i] = _process_path(item, base_dir)
    elif isinstance(node, str) and (node.startswith("./") or node.startswith("../")):
        resolved = (base_dir / node).resolve()
        # Ensure resolved path stays within base_dir
        try:
            if base_dir.resolve() not in resolved.parents and resolved != base_dir.resolve():
                return node  # reject traversal, return original string
        except (ValueError, OSError):
            return node
        return str(resolved)

    return node


def load_module(path, cache):
    cache = {} if cache is None else cache
    # Safety: only load .py files from the data directory
    abs_path = Path(path).resolve()
    data_dir = Path(settings.BASE_DIR, "data").resolve()
    if not str(abs_path).endswith(".py") or (data_dir not in abs_path.parents and abs_path.parent != data_dir):
        logging.getLogger(__name__).warning("Refusing to load module outside data/: %s", path)
        return None
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


_URL_RE = re.compile(r'https?://[^\s<>\]\)\"\']+')

def extract_url(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    match = _URL_RE.search(text)
    return match.group(0) if match else text


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


@dataclass
class DomainConfig:
    roots: list[str]  # 规范根域名列表（保持顺序，去重）
    aliases: dict[str, str]  # 别名 → 规范域名


def parse_domain_roots(custom_domain_root: str) -> DomainConfig:
    roots = []
    aliases = {}
    if not custom_domain_root:
        return DomainConfig(roots=roots, aliases=aliases)

    for line in custom_domain_root.splitlines():
        line = line.strip()
        if not line:
            continue

        if "->" in line:
            parts = [p.strip() for p in line.split("->", 1)]
            alias = extract_hostname("https://" + parts[0])
            target = extract_hostname("https://" + parts[1])
            if alias and target and alias != target:
                aliases[alias] = target
                _resolve_cycle(aliases, alias)
                if target not in roots:
                    roots.append(target)
        else:
            hostname = _extract_hostname_from_line(line)
            if hostname and hostname not in roots:
                roots.append(hostname)

    return DomainConfig(roots=roots, aliases=aliases)


def _extract_hostname_from_line(line: str) -> str:
    if "://" not in line:
        line = f"https://{line}"
    return extract_hostname(line)


def _resolve_cycle(aliases: dict[str, str], new_alias: str):
    """检测并打破别名链中的环。后出现的规则优先，移除环中最旧的规则。"""
    current = new_alias
    seen = {new_alias: 0}
    walk = [new_alias]

    while current in aliases:
        current = aliases[current]
        if current in seen:
            cycle_keys = set(walk[seen[current] :])
            for key in aliases:
                if key in cycle_keys and key != new_alias:
                    del aliases[key]
                    return
        seen[current] = len(walk)
        walk.append(current)


def get_matching_domain_roots(hostname: str, config: DomainConfig) -> list[str]:
    hostname = hostname.lower()

    # Step 1: root 匹配（现有逻辑）
    matches = []
    for root in config.roots:
        if hostname == root or hostname.endswith(f".{root}"):
            matches.append(root)

    if matches:
        matches.sort(key=lambda v: (v.count("."), v))

        # Step 2: 别名父链 — 从最宽泛的 matched root 出发，
        # 向上查找其自身或祖先域名是否在 aliases 中
        most_general = matches[0]
        parts = most_general.split(".")
        for i in range(len(parts)):
            candidate = ".".join(parts[i:])
            if candidate in config.aliases:
                parent = config.aliases[candidate]
                if parent not in matches:
                    matches.insert(0, parent)
                    seen = {parent}
                    while parent in config.aliases:
                        parent = config.aliases[parent]
                        if parent in seen or parent in matches:
                            break
                        seen.add(parent)
                        matches.insert(0, parent)
                break

        return matches

    # Step 3: 别名回退 — hostname 未匹配任何 root，
    # 检查是否匹配 alias（精确或子域名）
    for alias, target in config.aliases.items():
        if hostname == alias or hostname.endswith(f".{alias}"):
            matches = [target]
            seen = {target}
            while target in config.aliases:
                target = config.aliases[target]
                if target in seen or target in matches:
                    break
                seen.add(target)
                matches.insert(0, target)
            return matches

    return []


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


def get_alias_domains_for_root(root: str, config: DomainConfig) -> list[str]:
    """返回映射到指定 root 的所有别名域名（含 root 自身）。"""
    domains = [root]
    for alias, target in config.aliases.items():
        if target == root:
            domains.append(alias)
    return domains


def resolve_favicon_domain(
    hostname: str, config: DomainConfig | None = None, custom_domain_root: str = ""
) -> str:
    """将 hostname 按自定义域名规则归一化，用于 favicon 获取。

    优先使用预解析的 config；未提供时从 custom_domain_root 字符串解析。
    返回归一化后的域名；无匹配时返回原始 hostname。
    """
    if config is None:
        if not custom_domain_root:
            return hostname
        config = parse_domain_roots(custom_domain_root)
    matches = get_matching_domain_roots(hostname, config)
    return matches[0] if matches else hostname


def build_domain_filter_value_with_aliases(
    hostname: str,
    include_subdomains: bool,
    config: DomainConfig,
) -> str:
    hostname = hostname.lower()
    alias_domains = get_alias_domains_for_root(hostname, config)

    parts = []
    for domain in alias_domains:
        parts.append(domain)
        if include_subdomains:
            parts.append(f".{domain}")

    ordered = sorted(set(parts), key=lambda p: (p.startswith("."), p))
    return " | ".join(ordered)


def get_sidebar_domain_filter_value(url: str, custom_domain_root: str = "") -> str:
    hostname = extract_hostname(url)
    if not hostname:
        return ""

    config = parse_domain_roots(custom_domain_root)
    matching_roots = get_matching_domain_roots(hostname, config)

    if not matching_roots:
        return build_domain_filter_value(hostname)

    return build_domain_filter_value_with_aliases(
        matching_roots[-1], include_subdomains=True, config=config
    )


# SVG 净化：移除 XSS 向量，保留合法 SVG 元素
_DANGEROUS_TAGS = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|form|input|style|link|meta)\b[^>]*>",
    re.IGNORECASE,
)
_EVENT_ATTRS = re.compile(r"\bon\w+\s*=", re.IGNORECASE)
_JS_PROTOCOL = re.compile(r"javascript\s*:", re.IGNORECASE)


def sanitize_svg_body(svg_body: str) -> str:
    """清理 SVG body，移除 <script>、on* 事件处理器、javascript: 协议。"""
    if not isinstance(svg_body, str):
        return ""
    svg_body = _DANGEROUS_TAGS.sub("", svg_body)
    svg_body = _EVENT_ATTRS.sub("", svg_body)
    svg_body = _JS_PROTOCOL.sub("", svg_body)
    return svg_body


def atomic_write(path: str, content: str, encoding: str = 'utf-8'):
    """Write text to file atomically via tempfile + os.replace."""
    dir_name = os.path.dirname(path) or '.'
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=dir_name, suffix='.tmp', delete=False, encoding=encoding) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        raise


def is_safe_domain_key(domain_key: str) -> bool:
    """Check that a domain key is safe for use as a filename component.

    Allows wildcard prefix (e.g. *.example.com). Disallows path traversal,
    leading dots, and characters outside alphanumeric + hyphen + underscore + dot + star.
    """
    if not domain_key:
        return False
    if domain_key.startswith('.'):
        return False
    if '/' in domain_key or '\\' in domain_key or '..' in domain_key:
        return False
    return bool(re.match(r'^[A-Za-z0-9*][A-Za-z0-9._*-]*$', domain_key))
