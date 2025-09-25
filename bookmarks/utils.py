import importlib
import json
import logging
import os
import re
import unicodedata
import urllib.parse
import datetime
from typing import Optional
from pathlib import Path

from dateutil.relativedelta import relativedelta
from django.http import HttpResponseRedirect
from django.template.defaultfilters import pluralize
from django.utils import timezone, formats
from django.conf import settings

try:
    with open("version.txt", "r") as f:
        app_version = f.read().strip("\n")
except Exception as exc:
    logging.exception(exc)
    app_version = ""


def unique(elements, key):
    return list({key(element): element for element in elements}.values())


weekday_names = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}


def humanize_absolute_date(
    value: datetime.datetime, now: Optional[datetime.datetime] = None
):
    if not now:
        now = timezone.now()
    # Convert to local time zone first
    value_local = timezone.localtime(value)
    now_local = timezone.localtime(now)
    delta = relativedelta(now_local, value_local)
    yesterday = now_local - relativedelta(days=1)

    is_older_than_a_week = delta.years > 0 or delta.months > 0 or delta.weeks > 0 or delta.days > 0

    if is_older_than_a_week:
        return formats.date_format(value_local, "SHORT_DATE_FORMAT")
    elif value_local.day == now_local.day:
        return "今天"
    elif value_local.day == yesterday.day:
        return "昨天"
    else:
        return weekday_names[value_local.isoweekday()]


def humanize_relative_date(
    value: datetime.datetime, now: Optional[datetime.datetime] = None
):
    if not now:
        now = timezone.now()
    # Convert to local time zone first
    value_local = timezone.localtime(value)
    now_local = timezone.localtime(now)
    delta = relativedelta(now_local, value_local)
    is_current_week = value_local.isocalendar()[:2] == now_local.isocalendar()[:2]

    if delta.years > 0:
        return f"{delta.years} 年前"
    elif delta.months > 0:
        return f"{delta.months} 月前"
    elif delta.weeks > 0:
        return f"{delta.weeks} 周前"
    else:
        yesterday = now_local - relativedelta(days=1)
        if value_local.day == now_local.day:
            return "今天"
        elif value_local.day == yesterday.day:
            return "昨天"
        elif is_current_week:
            return weekday_names[value_local.isoweekday()]
        else:
            return "上" + weekday_names[value_local.isoweekday()]

def humanize_absolute_date_short(
    value: datetime.datetime, now: Optional[datetime.datetime] = None
):
    if not now:
        now = timezone.now()
    value_local = timezone.localtime(value)
    now_local = timezone.localtime(now)
    delta = relativedelta(now_local, value_local)
    yesterday = now_local - relativedelta(days=1)

    is_older_than_yesterday = delta.years > 0 or delta.months > 0 or delta.weeks > 0 or delta.days > 0

    if is_older_than_yesterday:
        if value_local.year == now_local.year:
            return f"{value_local.month}/{value_local.day}"
        else:
            return f"{value_local.year}/{value_local.month}/{value_local.day}"
    elif value_local.day == now_local.day:
        return "今天"
    elif value_local.day == yesterday.day:
        return "昨天"

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
        raise ValueError(f"{value} is not a valid timestamp")

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
    clean_url = urllib.parse.urlunparse((
        parsed_url.scheme, 
        parsed_url.netloc, 
        parsed_url.path, 
        '',  # 清空 params
        '',  # 清空 query (? 后的部分)
        ''   # 清空 fragment (# 后的部分)
    ))
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

def search_config_for_domain(domain, domain_map):
    config = None
    if domain in domain_map: # 直接命中
        config = domain_map[domain]
    if not config:
        for key in domain_map: # 解析命中（通用匹配符*）
            if key.startswith("*.") and domain.endswith(key[1:]):
                config =  domain_map[key]

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
            with open(path, "r", encoding="utf-8") as f:
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
    '''解析相对路径'''
    if isinstance(node, dict):
        for key, value in node.items():
            node[key] = _process_path(value, base_dir)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            node[i] = _process_path(item, base_dir)
    elif isinstance(node, str) and (node.startswith('./') or node.startswith('../')):
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
    '''解析相对日期字符串，获取数值、单位，用于前端搜索筛选项显示'''
    if not date_filter_relative_string:
        return None, None
    match = re.match(r'^last_(\d+)_(day|week|month|year)s?$', date_filter_relative_string)
    if match:
        value = match.group(1)
        unit = match.group(2) + 's'
        return value, unit
    return None, None