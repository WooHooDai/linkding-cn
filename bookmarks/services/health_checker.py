"""书签健康检查引擎。

通用层（无适配器即可工作）：
- HEAD 优先 → GET 兜底（有界读取）
- 状态码 / 重定向 / 超时判定
- 按域名限流（复用 website_loader 的限流机制）
- 内容级失效判定（选择器 / 关键词，适配器可配置）

适配层：`get_health_config(url)` 返回域名级配置，覆盖默认行为。
"""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.utils import timezone

from bookmarks.models import (
    HEALTH_STATUS_BLOCKED,
    HEALTH_STATUS_MISSING,
    HEALTH_STATUS_DEAD,
    HEALTH_STATUS_OK,
    HEALTH_STATUS_FAILED,
    Bookmark,
    CheckJob,
)
from bookmarks.services.website_loader import (
    _record_domain_request,
    _wait_for_domain,
    build_request_cookies,
    build_request_headers,
)
from bookmarks.utils import get_registrable_domain
from site_adapters.services.config.resolver import get_health_config

logger = logging.getLogger(__name__)

# 当前任务覆盖配置（由 run_job 设置；子进程内单任务，线程共享只读）：
# - workers：profile 级并发覆盖全局 settings.LD_HEALTH_CHECK_WORKERS
# - domain_interval：profile 级每域名限流间隔覆盖全局
_ACTIVE_JOB_OVERRIDES = {"workers": None, "domain_interval": None}


def _active_domain_interval() -> float | None:
    return _ACTIVE_JOB_OVERRIDES.get("domain_interval")

# 默认配置（与 _builtin.health 保持一致，作为 resolver 失效时的兜底）
# 内部统一使用归一化后的扁平键（resolver 已把嵌套 http_head_probe / content_check 展开）。
DEFAULT_ACCEPT_STATUS = [200]
DEFAULT_BLOCKED_STATUS = [401, 403, 407, 429]
DEFAULT_CHECK_CONTENT = True
DEFAULT_CHECK_SELECTORS = ["title", "body"]
DEFAULT_MAX_CONTENT_BYTES = 1024 * 1024
# body_text 截断：纯文本正则扫描上限（防止超长文章拖慢 re.search）
_DEFAULT_BODY_TEXT_LIMIT = 65536

# HEAD 返回这些状态码时不可信（站点可能仅支持 GET），需落入 GET
_HEAD_UNRELIABLE_STATUS = {405}

# 静态 HTML 兜底判定词：即使没有域名适配器，也能识别常见软 404。
# 语义与 cookie.verify 的 invalid_patterns 一致（正则子串匹配，普通词直接可用）。
_BUILTIN_DEAD_KEYWORDS = (
    "404 not found",
    "page not found",
    "not found",
    "页面不存在",
    "页面已不存在",
    "内容不存在",
    "文章不存在",
    "该页面已被删除",
    "此页面已删除",
    "内容已删除",
    "文章已删除",
    "已被删除或不存在",
    "您访问的页面不存在",
    "你所访问的页面不存在",
    "页面不存在或已删除",
)


class HealthCheckError(Exception):
    """健康检查过程中不可恢复的任务级错误。"""


# ---------------------------------------------------------------------------
# 单条检查
# ---------------------------------------------------------------------------


def _default_health_config() -> dict:
    """默认健康检查配置（归一化扁平结构）。"""
    return {
        # 总开关：false = 该域名不做健康检查
        "health_enabled": True,
        # L1 探测
        "probe_enabled": True,
        "probe_timeout": 3,
        "accept_status": list(DEFAULT_ACCEPT_STATUS),
        "blocked_status": list(DEFAULT_BLOCKED_STATUS),
        # 登录墙路径词：带路径锚定避免子串误伤（如 "auth" 会命中 author_share=1）
        "blocked_location_patterns": ["/login", "/signin", "/auth", "/passport", "/accounts"],
        # L2 内容检查
        "check_content": DEFAULT_CHECK_CONTENT,
        "check_selectors": list(DEFAULT_CHECK_SELECTORS),
        "valid_selectors": [],
        "valid_patterns": [],
        "invalid_selectors": [
            "h1.error",
            ".not-found",
            "[class*=404]",
            "[id*=not-found]",
        ],
        "invalid_patterns": list(_BUILTIN_DEAD_KEYWORDS),
        # health 特有 / 通用
        "max_content_limit": DEFAULT_MAX_CONTENT_BYTES,
        "content_timeout": 30,
        "headers": {},
        "proxy": None,
        "_request_url": None,
        "_adapter": None,
        "_domain_key": "",
    }


def _normalize_config(raw: dict | None) -> dict:
    """把任意输入（resolver 输出 / 直接传入的嵌套结构）归一化为内部扁平结构。

    支持两种输入形态：
    - 嵌套：http_head_probe.* / content_check.*（对齐 cookie.verify 的分层）
    - 扁平：resolver 展开后的键（probe_enabled / accept_status / check_content / valid_* / invalid_*）
    """
    raw = raw or {}
    out = _default_health_config()
    for key in (
        "health_enabled",
        "max_content_limit",
        "content_timeout",
        "headers",
        "proxy",
        "reason",
        "_request_url",
        "_adapter",
        "_domain_key",
    ):
        if raw.get(key) is not None:
            out[key] = raw[key]

    # L1：嵌套优先，resolver 扁平输出回退
    probe = raw.get("http_head_probe") or {}
    out["probe_enabled"] = probe.get("enabled", out["probe_enabled"])
    out["probe_timeout"] = probe.get("timeout", out["probe_timeout"])
    if "accept_status" in probe:
        out["accept_status"] = probe["accept_status"]
    elif raw.get("accept_status") is not None:
        out["accept_status"] = raw["accept_status"]
    if "blocked_status" in probe:
        out["blocked_status"] = probe["blocked_status"]
    elif raw.get("blocked_status") is not None:
        out["blocked_status"] = raw["blocked_status"]
    if "blocked_location_patterns" in probe:
        out["blocked_location_patterns"] = probe["blocked_location_patterns"]
    elif raw.get("blocked_location_patterns") is not None:
        out["blocked_location_patterns"] = raw["blocked_location_patterns"]
    if raw.get("probe_enabled") is not None:
        out["probe_enabled"] = raw["probe_enabled"]
    if raw.get("probe_timeout") is not None:
        out["probe_timeout"] = raw["probe_timeout"]

    # L2：嵌套优先，resolver 扁平输出回退
    content = raw.get("content_check") or {}
    out["check_content"] = content.get("enabled", out["check_content"])
    if "timeout" in content:
        out["content_timeout"] = content["timeout"]
    elif raw.get("content_timeout") is not None:
        out["content_timeout"] = raw["content_timeout"]
    if "check_selectors" in content:
        out["check_selectors"] = content["check_selectors"]
    elif raw.get("check_selectors") is not None:
        out["check_selectors"] = raw["check_selectors"]
    if "valid_selectors" in content:
        out["valid_selectors"] = content["valid_selectors"]
    elif raw.get("valid_selectors") is not None:
        out["valid_selectors"] = raw["valid_selectors"]
    if "valid_patterns" in content:
        out["valid_patterns"] = content["valid_patterns"]
    elif raw.get("valid_patterns") is not None:
        out["valid_patterns"] = raw["valid_patterns"]
    if "invalid_selectors" in content:
        out["invalid_selectors"] = content["invalid_selectors"]
    elif raw.get("invalid_selectors") is not None:
        out["invalid_selectors"] = raw["invalid_selectors"]
    if "invalid_patterns" in content:
        out["invalid_patterns"] = content["invalid_patterns"]
    elif raw.get("invalid_patterns") is not None:
        out["invalid_patterns"] = raw["invalid_patterns"]
    if raw.get("check_content") is not None:
        out["check_content"] = raw["check_content"]

    return out


def _resolve_config(url: str, config: dict | None, username: str = "") -> dict:
    if config is not None:
        return _normalize_config(config)
    try:
        resolved = get_health_config(url, username=username)
    except Exception as exc:
        logger.warning("Failed to resolve health config for %s: %s", url, exc)
        resolved = None
    if not resolved:
        return _default_health_config()
    return _normalize_config(resolved)


def _request_kwargs(config: dict, timeout: float | None = None) -> dict:
    kwargs = {
        "allow_redirects": True,
        "headers": build_request_headers(config),
        "cookies": build_request_cookies(config),
        "timeout": timeout if timeout is not None else config.get("content_timeout", 30),
    }
    proxy = config.get("proxy")
    if proxy:
        kwargs["proxies"] = proxy
    return kwargs


def _head_probe(url: str, config: dict, timeout: float):
    """HEAD 探测。返回 (status_code, final_url, duration_ms)；网络异常或状态不可信返回 None。

    请求目标优先取适配器解析出的 `_request_url`（对应通用 `request_url` 改写）。
    timeout 由调用方按总预算分配传入。
    """
    start = time.monotonic()
    try:
        kwargs = _request_kwargs(config, timeout=timeout)
        probe_url = config.get("_request_url") or url
        response = requests.head(probe_url, **{**kwargs, "stream": True})
        status = response.status_code
        duration_ms = int((time.monotonic() - start) * 1000)
        if status in _HEAD_UNRELIABLE_STATUS:
            return None
        return status, response.url or probe_url, duration_ms
    except requests.RequestException as exc:
        logger.debug("HEAD probe failed for %s: %s", url, exc)
        return None


def _get_probe(url: str, config: dict, need_content: bool, timeout: float):
    """GET 探测（有界读取）。返回 (status_code, final_url, content, duration_ms)。

    - 非 accept_status（如 4xx/5xx）时完全不读 body，状态码已定性。
    - timeout 由调用方按总预算剩余分配传入。
    - 网络异常时返回 (None, None, None, None)；重定向循环向上抛出 TooManyRedirects。
    """
    start = time.monotonic()
    max_bytes = config.get("max_content_limit", DEFAULT_MAX_CONTENT_BYTES)
    target = config.get("_request_url") or url
    accept = set(config.get("accept_status", DEFAULT_ACCEPT_STATUS))
    try:
        with requests.get(
            target, stream=True, **_request_kwargs(config, timeout=timeout)
        ) as response:
            status = response.status_code
            final_url = response.url or target
            # 只有 2xx 成功页才需要读 body 扫内容信号；
            # 4xx/5xx 的状态码已定性，body 无价值，完全不读
            if need_content and status in accept:
                read_limit = max_bytes
                size = 0
                chunks = []
                for chunk in response.iter_content(chunk_size=32 * 1024):
                    size += len(chunk)
                    chunks.append(chunk)
                    if size >= read_limit:
                        break
                content = b"".join(chunks)[:read_limit]
            else:
                content = b""
            duration_ms = int((time.monotonic() - start) * 1000)
            return status, final_url, content, duration_ms
    except requests.exceptions.TooManyRedirects:
        raise
    except requests.RequestException as exc:
        logger.debug("GET probe failed for %s: %s", url, exc)
        return None, None, None, None


def _fetch(url: str, config: dict):
    """执行一次检查请求，返回 (status_code, final_url, content, probes)。

    `probes` 记录各分段（HEAD / GET）的状态、终址与耗时，供测试看板
    与书签详情展示；未执行的分段为 None。

    超时：L1 HEAD 用 `probe_timeout`（默认 5s），L2 GET 用 `content_timeout`
    （默认 30s），两者独立。

    - probe_enabled=False：跳过 HEAD，直接 GET（GET 拿满总预算）。
    - check_content=False：不取正文，仅按状态码判定。
    - 两者都 False 时由调用方（check_url）短路为 skipped，不会走到这里。
    """
    check_content = config.get("check_content", DEFAULT_CHECK_CONTENT)
    probe_enabled = config.get("probe_enabled", True)
    accept = set(config.get("accept_status", DEFAULT_ACCEPT_STATUS))
    blocked = set(config.get("blocked_status", DEFAULT_BLOCKED_STATUS))
    probe_timeout = config.get("probe_timeout", 5)
    content_timeout = config.get("content_timeout", 30)

    domain = get_registrable_domain(url)
    if domain:
        interval = _active_domain_interval()
        if interval is not None:
            _wait_for_domain_with_interval(domain, interval)
        else:
            _wait_for_domain(domain)

    probes = {"head": None, "get": None}
    fetch_start = time.monotonic()

    # HEAD 优先（可禁用）：快速判定 dead/blocked，避免下载正文
    if probe_enabled:
        head = _head_probe(url, config, timeout=probe_timeout)
        if head is not None:
            head_status, head_final_url, head_duration = head
            probes["head"] = {
                "status": head_status,
                "url": head_final_url,
                "duration_ms": head_duration,
            }
            if head_status >= 400:
                # HEAD 返回 4xx/5xx 不可直接采信：部分站点/CDN 对 HEAD 请求特殊处理
                # （如小红书短链 xhslink.cn 恒返回 404，但 GET 正常 302→200）。
                # 落入 GET 复核，以 GET 结果为准。
                pass
            elif head_status not in accept and head_status not in blocked:
                # 非 2xx 且非 blocked（如 3xx 终点）且无需正文 → 直接返回
                if not check_content:
                    if domain:
                        _record_domain_request(domain)
                    return head_status, head_final_url, None, probes
                # 需要正文：继续 GET
            elif not check_content:
                if domain:
                    _record_domain_request(domain)
                return head_status, head_final_url, None, probes
            # 需要正文：继续 GET

    # GET 探针使用 L2 独立超时
    get_status, get_final_url, content, get_duration = _get_probe(
        url, config, need_content=check_content, timeout=content_timeout
    )
    if get_status is not None:
        probes["get"] = {
            "status": get_status,
            "url": get_final_url,
            "duration_ms": get_duration,
        }
    if domain:
        _record_domain_request(domain)
    return get_status, get_final_url, content, probes


_profile_domain_last_request: dict[str, float] = {}
_profile_domain_rate_lock = threading.Lock()


def _wait_for_domain_with_interval(domain: str, interval: float):
    """按 profile 自定义间隔限流（独立于全局 metadata 限流器）。"""
    if interval <= 0:
        return
    import time as _time

    wait = 0.0
    with _profile_domain_rate_lock:
        now = _time.monotonic()
        last = _profile_domain_last_request.get(domain, 0)
        wait = interval - (now - last)
        if wait > 0:
            _profile_domain_last_request[domain] = last + interval
        else:
            _profile_domain_last_request[domain] = now
    if wait > 0:
        _time.sleep(wait)


def _decode_text(content: bytes | None) -> str:
    if not content:
        return ""
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _content_signals(html: str, config: dict) -> list[dict]:
    """内容级判定（对齐 cookie.verify 的 content_check 语义）。

    返回命中的信号列表；未命中返回 []。
    - check_selectors 控制扫描 title / body
    - valid_selectors / valid_patterns：正向命中（短路）→ 信号 type 为 valid_*
    - invalid_selectors / invalid_patterns：反向命中 → 信号 type 为 invalid_*
    - invalid_patterns 为正则（re.search 子串匹配，普通关键词直接可用）
    """
    check_parts = set(config.get("check_selectors") or DEFAULT_CHECK_SELECTORS)
    valid_selectors = config.get("valid_selectors") or []
    valid_patterns = config.get("valid_patterns") or []
    invalid_selectors = config.get("invalid_selectors") or []
    invalid_patterns = config.get("invalid_patterns") or []
    signals: list[dict] = []

    if not (valid_selectors or valid_patterns or invalid_selectors or invalid_patterns):
        return signals

    text = html or ""
    if not text.strip():
        return signals

    soup = None
    title = ""
    body_text = ""
    try:
        soup = BeautifulSoup(text, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        body = soup.body or soup
        body_text = " ".join(body.get_text(" ", strip=True).split())[:_DEFAULT_BODY_TEXT_LIMIT]
    except Exception as exc:
        logger.debug("Content parse failed for signals: %s", exc)

    def _compile(patterns):
        compiled = []
        for p in patterns:
            try:
                compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as exc:
                logger.debug("Invalid pattern %r: %s", p, exc)
        return compiled

    title_text = title if "title" in check_parts else ""
    body_text = body_text if "body" in check_parts else ""
    combined = " ".join([title_text, body_text]).strip()

    # 正向：命中即有效（短路）
    if soup is not None:
        for selector in valid_selectors:
            try:
                if soup.select_one(selector) is not None:
                    signals.append({"type": "valid_selector", "value": selector})
                    return signals
            except Exception as exc:
                logger.debug("Selector %s failed: %s", selector, exc)
    for pattern in _compile(valid_patterns):
        if pattern.search(combined):
            signals.append({"type": "valid_pattern", "value": pattern.pattern})
            return signals

    # 反向：命中即失效
    if soup is not None:
        for selector in invalid_selectors:
            try:
                if soup.select_one(selector) is not None:
                    signals.append({"type": "invalid_selector", "value": selector})
            except Exception as exc:
                logger.debug("Selector %s failed: %s", selector, exc)
    for pattern in _compile(invalid_patterns):
        if pattern.search(combined):
            signals.append({"type": "invalid_pattern", "value": pattern.pattern})
            break
    return signals


def check_url(url: str, config: dict | None = None, username: str = "") -> dict:
    """检查单个 URL 的可达性与内容有效性，返回判定结果字典。

    `username` 用于解析该用户保存的站点 cookie/headers（settings/adapters
    中保存的用户级凭据）；不传时仅使用共享凭据。

    返回的 `status` 为五类判定之一；若该域名的适配器设置 `health.enabled=false`，
    返回 `skipped=True` 且 `status=None`，调用方应把书签标记为未检查（unknown）。
    """
    resolved_config = _resolve_config(url, config, username)
    result = _check_url(url, resolved_config)
    _apply_custom_reason(result, resolved_config)
    return result


def _apply_custom_reason(result: dict, resolved_config: dict) -> None:
    """适配器自定义判定说明：配置了 `reason` 时覆盖引擎默认文案。

    - 仅对非 ok 判定生效（ok 不显示说明）。
    - `reason` 支持两种形态：
      * 字符串：全局兜底文案（等价于对象形态的 `default`）
      * 对象：按 状态 → HTTP 码 分层配置
        ```jsonc
        {
          "default": "无法访问（HTTP {http_status}）",
          "blocked": { "default": "被站点拦截", "403": "被反爬拦截" },
          "dead": "页面已失效"
        }
        ```
      命中优先级：`reason.<status>.<http_code>` > `reason.<status>.default` > `reason.default` > 引擎默认。
    - 支持 `{http_status}` 占位符替换为实际 HTTP 状态码。
    """
    custom = resolved_config.get("reason")
    if not custom or not result.get("status") or result["status"] == HEALTH_STATUS_OK:
        return
    http_status = result.get("http_status")
    text = _resolve_reason_text(custom, result["status"], http_status)
    if text is None:
        return  # 无匹配配置 → 保留引擎默认文案
    result["reason"] = text.replace(
        "{http_status}", str(http_status) if http_status else ""
    )


def _resolve_reason_text(reason_cfg, status: str, http_status) -> str | None:
    """三级解析自定义说明：status.http_code > status.default > default。"""
    if isinstance(reason_cfg, str):
        return reason_cfg
    if not isinstance(reason_cfg, dict):
        return None
    status_cfg = reason_cfg.get(status)
    if isinstance(status_cfg, dict):
        if http_status is not None:
            specific = status_cfg.get(str(http_status))
            if isinstance(specific, str) and specific:
                return specific
        default = status_cfg.get("default")
        if isinstance(default, str) and default:
            return default
    elif isinstance(status_cfg, str) and status_cfg:
        return status_cfg
    default = reason_cfg.get("default")
    return default if isinstance(default, str) and default else None


def _check_url(url: str, resolved_config: dict) -> dict:
    start = time.monotonic()
    accept = set(resolved_config.get("accept_status", DEFAULT_ACCEPT_STATUS))
    blocked = set(resolved_config.get("blocked_status", DEFAULT_BLOCKED_STATUS))

    result = {
        "url": url,
        "status": None,
        "http_status": None,
        "reason": "",
        "duration_ms": 0,
        "redirect_url": None,
        "adapter": resolved_config.get("_adapter"),
        "content_signals": [],
    }

    # 总开关关闭 → 该域名不做健康检查（书签保持未检查，显示 unknown）
    if not resolved_config.get("health_enabled", True):
        result.update(
            {
                "skipped": True,
                "reason": "health check disabled for domain",
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        )
        return result

    try:
        status, final_url, content, probes = _fetch(url, resolved_config)
    except requests.exceptions.TooManyRedirects:
        result.update(
            {
                "status": HEALTH_STATUS_DEAD,
                "reason": "redirect loop",
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        )
        return result
    except requests.RequestException as exc:
        result.update(
            {
                "status": HEALTH_STATUS_FAILED,
                "reason": f"request failed: {exc}",
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        )
        return result

    result["http_status"] = status
    result["probes"] = probes
    if final_url and final_url != url:
        result["redirect_url"] = final_url
    result["duration_ms"] = int((time.monotonic() - start) * 1000)

    if status is None:
        result.update({"status": HEALTH_STATUS_FAILED, "reason": "request failed"})
        return result

    # 重定向 URL 命中 blocked_location_patterns → 视为被墙/跳登录（blocked）
    location_patterns = resolved_config.get("blocked_location_patterns") or []
    if final_url and location_patterns:
        for pattern in location_patterns:
            try:
                if re.search(pattern, final_url, re.IGNORECASE):
                    result.update(
                        {
                            "status": HEALTH_STATUS_BLOCKED,
                            "reason": f"redirected to blocked location: {pattern}",
                        }
                    )
                    return result
            except re.error as exc:
                logger.debug("Invalid location pattern %r: %s", pattern, exc)

    if status in accept:
        verdict = HEALTH_STATUS_OK
        reason = ""
        signals: list[dict] = []
        if resolved_config.get("check_content", DEFAULT_CHECK_CONTENT):
            html = _decode_text(content)
            signals = _content_signals(html, resolved_config)
            # 正向信号（valid_*）短路 → ok；否则反向信号（invalid_*）→ content_dead
            if any(s["type"].startswith("valid_") for s in signals):
                verdict = HEALTH_STATUS_OK
                reason = "valid content matched"
            elif signals:
                verdict = HEALTH_STATUS_MISSING
                reason = "content signals matched"
        result.update(
            {
                "status": verdict,
                "reason": reason,
                "content_signals": signals,
            }
        )
        return result

    if status in blocked:
        result.update({"status": HEALTH_STATUS_BLOCKED, "reason": f"HTTP {status}"})
        return result

    if 400 <= status < 600:
        result.update({"status": HEALTH_STATUS_DEAD, "reason": f"HTTP {status}"})
        return result

    result.update(
        {"status": HEALTH_STATUS_FAILED, "reason": f"unexpected status {status}"}
    )
    return result


def _build_details(result: dict, job_id: int | None) -> dict:
    details = dict(result)
    details.pop("url", None)
    details["checked_at"] = timezone.now().isoformat()
    if job_id is not None:
        details["job_id"] = job_id
    return details


def persist_bookmark_result(
    bookmark: Bookmark, result: dict, job_id: int | None = None
) -> None:
    """将单条检查结果写回书签（由主线程串行调用，避免 SQLite 写锁竞争）。

    - 正常判定：health_status 写五类之一，details 存证据。
    - skipped（该域名禁用了 L1+L2）：health_status 置 NULL（显示 unknown），
      details 保留 skipped 标记与 job_id（断点续跑仍生效）。
    """
    details = _build_details(result, job_id)
    Bookmark.objects.filter(pk=bookmark.pk).update(
        health_status=result.get("status"), health_details=details
    )


def check_bookmark(
    bookmark: Bookmark,
    job_id: int | None = None,
    write: bool = True,
    username: str = "",
) -> dict:
    """检查单条书签。write=True 时直接写回（供命令单条模式使用）。"""
    result = check_url(bookmark.url, username=username or bookmark.owner.username)
    if write:
        persist_bookmark_result(bookmark, result, job_id)
    return result


# ---------------------------------------------------------------------------
# 任务（CheckJob）执行
# ---------------------------------------------------------------------------


def resolve_job_bookmarks(job: CheckJob):
    """返回 job scope 对应的书签 QuerySet（属主隔离 + 仅活动书签）。

    - mode=all / query：包含**归档书签**（排在非归档之后），排除软删除。
    - mode=ids：按指定 id，排除软删除。
    """
    profile = getattr(job.owner, "profile", None)
    mode = (job.scope or {}).get("mode", "all")

    from bookmarks.models import BookmarkSearch
    from bookmarks.queries import _base_bookmarks_query

    if mode == "ids":
        ids = (job.scope or {}).get("ids") or []
        return (
            Bookmark.objects.filter(owner=job.owner, is_deleted=False)
            .filter(id__in=ids)
            .order_by("id")
        )
    if mode == "query":
        query = (job.scope or {}).get("query") or ""
        search = BookmarkSearch(q=query)
    elif mode == "bundle":
        from bookmarks.models import BookmarkBundle

        bundle_id = (job.scope or {}).get("bundle_id")
        bundle = BookmarkBundle.objects.filter(
            owner=job.owner, pk=bundle_id
        ).first()
        if not bundle:
            return Bookmark.objects.none()
        search = bundle.search_object
    else:  # mode == "all"
        search = BookmarkSearch()
    # 归档书签也检查，但排在后；软删除（回收站）不检查
    return (
        _base_bookmarks_query(job.owner, profile, search)
        .filter(is_deleted=False)
        .order_by("is_archived", "id")
    )


def _touch_heartbeat(job: CheckJob) -> None:
    CheckJob.objects.filter(pk=job.pk).update(heartbeat=timezone.now())


def run_job(job: CheckJob) -> None:
    """执行一个 CheckJob（由 health_check 命令在子进程中调用）。

    - 幂等续跑：跳过 health_details.job_id == 当前 job 的书签。
    - 中断：检查 stop_requested，为真时干净退出并置 interrupted。
    - 崩溃恢复：心跳由页面/状态接口根据超时判断进程已死。
    """
    if job.state not in (CheckJob.STATE_PENDING, CheckJob.STATE_INTERRUPTED):
        raise HealthCheckError(f"Job {job.pk} is in state {job.state}, cannot start")

    # 启动前已被暂停（pending 时用户点了暂停）→ 直接置为 interrupted
    if CheckJob.objects.filter(pk=job.pk, stop_requested=True).exists():
        logger.info("CheckJob %s was paused before start, interrupting", job.pk)
        CheckJob.objects.filter(pk=job.pk).update(
            state=CheckJob.STATE_INTERRUPTED,
            paused_at=timezone.now(),
            finished_at=timezone.now(),
        )
        return

    CheckJob.objects.filter(pk=job.pk).update(
        state=CheckJob.STATE_RUNNING,
        # 仅首次启动时记录 started_at；暂停续跑不清零，保留进行时长
        started_at=(
            timezone.now()
            if CheckJob.objects.filter(pk=job.pk, started_at__isnull=True).exists()
            else job.started_at
        ),
        finished_at=None,
        stop_requested=False,
    )
    job.refresh_from_db()

    bookmarks = list(resolve_job_bookmarks(job))
    total = len(bookmarks)
    done = 0
    job_id = job.pk
    heartbeat_every = max(1, settings.LD_HEALTH_CHECK_HEARTBEAT_SEC)
    profile = getattr(job.owner, "profile", None)
    _ACTIVE_JOB_OVERRIDES["workers"] = (
        profile.health_check_workers if profile else None
    )
    _ACTIVE_JOB_OVERRIDES["domain_interval"] = (
        profile.health_domain_interval if profile else None
    )
    workers = max(
        1, _ACTIVE_JOB_OVERRIDES["workers"] or settings.LD_HEALTH_CHECK_WORKERS
    )

    # 断点：跳过已由本 job 检查过的书签
    pending = [b for b in bookmarks if (b.health_details or {}).get("job_id") != job_id]
    skipped = total - len(pending)

    CheckJob.objects.filter(pk=job_id).update(total=total, done=skipped)
    job.refresh_from_db()

    logger.info(
        "CheckJob %s started: total=%s, skipped=%s, workers=%s",
        job_id,
        total,
        skipped,
        workers,
    )

    last_heartbeat = time.monotonic()
    _touch_heartbeat(job)

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            # 工作线程只做网络请求与判定（无 DB 写），写库统一由主线程串行完成，
            # 避免 SQLite 等单写者数据库在多线程下出现 "database table is locked"。
            futures = {
                executor.submit(
                    check_bookmark, b, job_id, False, job.owner.username
                ): b
                for b in pending
            }
            for future in as_completed(futures):
                bookmark = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.exception(
                        "Health check failed for bookmark %s (%s): %s",
                        bookmark.pk,
                        bookmark.url,
                        exc,
                    )
                    # 引擎异常等同于未完成检查：不写回任何状态（书签保持原值/NULL，
                    # 与"未检查"一致），仅记录日志；任务级失败由 CheckJob 状态呈现
                    result = None
                if result is not None:
                    # 主线程串行写库；写库异常视为任务级故障（向上冒泡 → job failed）
                    persist_bookmark_result(bookmark, result, job_id)
                done += 1
                CheckJob.objects.filter(pk=job_id).update(done=skipped + done)

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_every:
                    _touch_heartbeat(job)
                    last_heartbeat = now

                if CheckJob.objects.filter(pk=job_id, stop_requested=True).exists():
                    job_row = (
                        CheckJob.objects.filter(pk=job_id)
                        .only("state", "stop_requested")
                        .first()
                    )
                    if (
                        job_row is not None
                        and job_row.state == CheckJob.STATE_CANCELLED
                    ):
                        # 用户选择放弃：保持 cancelled，仅落完成时间
                        logger.info("CheckJob %s cancelled by user", job_id)
                        CheckJob.objects.filter(pk=job_id).update(
                            finished_at=timezone.now()
                        )
                    else:
                        logger.info("CheckJob %s stop requested, interrupting", job_id)
                        CheckJob.objects.filter(pk=job_id).update(
                            state=CheckJob.STATE_INTERRUPTED,
                            paused_at=timezone.now(),
                            finished_at=timezone.now(),
                        )
                    return

        CheckJob.objects.filter(pk=job_id).update(
            state=CheckJob.STATE_COMPLETED,
            done=total,
            finished_at=timezone.now(),
        )
        logger.info("CheckJob %s completed (%s bookmarks)", job_id, total)
    except Exception as exc:
        logger.exception("CheckJob %s failed", job_id)
        CheckJob.objects.filter(pk=job_id).update(
            state=CheckJob.STATE_FAILED,
            error=str(exc)[:2000],
            finished_at=timezone.now(),
        )


def is_heartbeat_stale(job: CheckJob, now=None) -> bool:
    """心跳是否超时（worker 进程可能已退出）。"""
    if not job.heartbeat:
        return False
    now = now or timezone.now()
    timeout = settings.LD_HEALTH_CHECK_HEARTBEAT_TIMEOUT_SEC
    return job.heartbeat < now - timedelta(seconds=timeout)
