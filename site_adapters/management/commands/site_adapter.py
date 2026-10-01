import json
import os
import shutil
import tempfile
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bookmarks.services.website_loader import (
    load_website_metadata,
    normalize_content_type,
)
from site_adapters.services.auth.cookies import (
    verify_and_refresh,
)
from site_adapters.services.auth.credentials import get_shared_cookie
from site_adapters.services.config import parse_jsonc
from site_adapters.services.config.loader import show_config
from site_adapters.services.config.resolver import (
    get_metadata_config,
    get_reader_config,
    get_snapshot_config,
)
from site_adapters.services.config.validator import classify_field, validate_config
from site_adapters.services.subscriptions import (
    fetch_subscription,
)


_SENSITIVE_HEADER_NAMES = {
    "cookie",
    "authorization",
    "proxy-authorization",
    "set-cookie",
}


def _redact_config_for_output(obj):
    """递归脱敏解析后的配置，避免会话 cookie/凭据明文进入 stdout。

    保持键集合不变，仅把敏感值替换为 "<redacted>"：
    - `_user_cookie` 键（resolver 注入的原始 cookie 串）
    - `headers` 块中的 Cookie/Authorization 等敏感头值
    """
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            if key == "_user_cookie":
                redacted[key] = "<redacted>" if value else value
                continue
            if key == "headers" and isinstance(value, dict):
                redacted_headers = {}
                for hname, hval in value.items():
                    if isinstance(hname, str) and hname.lower() in _SENSITIVE_HEADER_NAMES:
                        redacted_headers[hname] = "<redacted>"
                    else:
                        redacted_headers[hname] = hval
                redacted[key] = redacted_headers
                continue
            redacted[key] = _redact_config_for_output(value)
        return redacted
    if isinstance(obj, list):
        return [_redact_config_for_output(item) for item in obj]
    return obj


class Command(BaseCommand):
    help = "Manage and test site adapter configuration"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="cmd", required=True)

        validate = sub.add_parser("validate")
        validate.add_argument("base_dir", nargs="?", default=None)
        validate.add_argument("--file", default="")

        show = sub.add_parser("show-config")
        show.add_argument("url")
        show.add_argument("--dir", default=None)

        metadata = sub.add_parser("metadata")
        metadata.add_argument("url")

        cookie = sub.add_parser("cookie")
        cookie.add_argument("url")
        cookie.add_argument("--section", choices=("metadata", "snapshot", "reader"), default="metadata")

        pipeline = sub.add_parser("pipeline")
        pipeline.add_argument("url")
        pipeline.add_argument("--output", "-o", default=None)
        pipeline.add_argument("--skip-snapshot", action="store_true")

        subscription = sub.add_parser("validate-subscription")
        subscription.add_argument("source")

        prefetch = sub.add_parser("prefetch-subscriptions")
        prefetch.add_argument("--force", action="store_true")

        from_us = sub.add_parser("from-userscript")
        from_us.add_argument("source")
    def handle(self, *args, **opts):
        return getattr(self, f"handle_{opts['cmd'].replace('-', '_')}")(opts)

    def handle_validate(self, opts):
        base_dir = opts["base_dir"] or settings.LD_SITE_ADAPTERS_DIR
        issues = validate_config(base_dir, domain_filename=opts["file"])
        if not issues:
            self.stdout.write(self.style.SUCCESS("site adapters ok"))
            return
        for issue in issues:
            level = issue['level'] if isinstance(issue, dict) else 'error'
            style = self.style.ERROR if level == 'error' else self.style.WARNING
            msg = issue['message'] if isinstance(issue, dict) else str(issue)
            self.stdout.write(style(msg))
        if any((issue.get('level', 'error') if isinstance(issue, dict) else 'error') == 'error'
               for issue in issues):
            raise CommandError("site adapters validation failed")

    def handle_show_config(self, opts):
        result = show_config(opts["url"], opts["dir"] or settings.LD_SITE_ADAPTERS_DIR)
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))

    def handle_metadata(self, opts):
        try:
            config = get_metadata_config(opts["url"])
            metadata = load_website_metadata(opts["url"], ignore_cache=True)
        except (requests.RequestException, OSError) as exc:
            # 网络异常只打印简洁信息，完整 traceback 留作日志，不污染 stdout/stderr
            raise CommandError(f"metadata fetch failed: {type(exc).__name__}: {exc}") from None
        self.stdout.write(json.dumps({
            "config": _redact_config_for_output(config),
            "metadata": metadata.to_dict(),
        }, indent=2, ensure_ascii=False, default=str))

    def handle_cookie(self, opts):
        section = opts["section"]
        if section == "metadata":
            config = get_metadata_config(opts["url"])
        elif section == "reader":
            config = get_reader_config(opts["url"])
        else:
            config = get_snapshot_config(opts["url"])
        if not config:
            self.stdout.write(self.style.ERROR("no matching domain config"))
            return
        domain_key = config.get("_domain_key", "")
        cookie_config = config.get("cookie", {})
        before = config.get("_user_cookie") or get_shared_cookie(hostname=domain_key, scope=opts["section"])[0]
        after = before
        if cookie_config:
            after = verify_and_refresh(
                cookie_config=cookie_config,
                url=opts["url"],
                domain_key=domain_key,
                verify_context={"url": opts["url"], "status": 0, "title": "", "body_preview": ""},
                scope=opts["section"],
            )
        self.stdout.write(json.dumps({
            "domain": domain_key,
            "has_cookie": bool(config.get("_user_cookie") or get_shared_cookie(hostname=domain_key, scope=opts["section"])[0]),
            "refreshed": bool(after and after != before),
        }, indent=2, ensure_ascii=False))

    def handle_pipeline(self, opts):
        from bookmarks.services import reader_processor
        from bookmarks.services.snapshot_processor import create_snapshot

        url = opts["url"]
        try:
            snapshot_config = get_snapshot_config(url)
            result = {
                "metadata_config": get_metadata_config(url),
                "snapshot_config": snapshot_config,
                "reader_config": get_reader_config(url),
                "metadata": load_website_metadata(url, ignore_cache=True).to_dict(),
            }
        except (requests.RequestException, OSError) as exc:
            raise CommandError(f"pipeline fetch failed: {type(exc).__name__}: {exc}") from None
        tmp_dir = None
        snapshot_path = opts["output"]
        try:
            if not opts["skip_snapshot"]:
                snapshot_extension = (
                    normalize_content_type((snapshot_config or {}).get("content_type"))
                    or "html"
                )
                if not snapshot_path:
                    tmp_dir = tempfile.mkdtemp()
                    snapshot_path = os.path.join(
                        tmp_dir, f"snapshot.{snapshot_extension}"
                    )
                create_snapshot(url, snapshot_path)
                result["snapshot"] = {"path": snapshot_path, "size": os.path.getsize(snapshot_path)}
                with open(snapshot_path, encoding="utf-8") as f:
                    raw_content = f.read()
                if snapshot_extension in ("json", "xml"):
                    result["reader"] = reader_processor.parse_content(
                        raw_content, snapshot_extension, url=url
                    )
                else:
                    result["reader"] = reader_processor.parse_html(
                        raw_content, url=url
                    )
            else:
                result["reader"] = reader_processor.parse_url(url)
            self.stdout.write(json.dumps(_redact_config_for_output(result), indent=2, ensure_ascii=False, default=str))
        except (requests.RequestException, OSError) as exc:
            # 网络异常只打印简洁信息，完整 traceback 留作日志
            raise CommandError(f"pipeline fetch failed: {type(exc).__name__}: {exc}") from None
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def handle_validate_subscription(self, opts):
        try:
            data, root = self._load_subscription(opts["source"])
        except Exception as exc:
            raise CommandError(f"load failed: {exc}") from exc
        issues = self._validate_subscription_data(data, root)
        if not issues:
            self.stdout.write(self.style.SUCCESS(f"subscription ok, {len(data.get('domains', {}))} domains"))
            return
        for issue in issues:
            level = issue['level'] if isinstance(issue, dict) else 'error'
            style = self.style.ERROR if level == 'error' else self.style.WARNING
            msg = issue['message'] if isinstance(issue, dict) else str(issue)
            self.stdout.write(style(msg))
        if any((issue.get('level', 'error') if isinstance(issue, dict) else 'error') == 'error'
               for issue in issues):
            raise CommandError("subscription validation failed")

    def handle_prefetch_subscriptions(self, opts):
        """Download remote subscriptions that are missing or due for refresh."""
        from site_adapters.services.config.bootstrap import ensure_base_dirs
        from site_adapters.services.subscriptions import (
            fetch_all_subscriptions,
            fetch_subscription,
        )
        from site_adapters.views.helpers import _get_adapters_list

        ensure_base_dirs()
        adapters = _get_adapters_list()
        remote_adapters = [
            adapter
            for adapter in adapters
            if isinstance(adapter, dict)
            and adapter.get("enabled") is not False
            and str(adapter.get("source", "")).startswith("https://")
        ]
        paths = []

        if opts["force"]:
            for adapter in remote_adapters:
                source = adapter.get("source", "")
                path = fetch_subscription(
                    source,
                    name=adapter.get("name", ""),
                    adapter_id=adapter.get("id", ""),
                    force=True,
                    update_interval=adapter.get("update_interval", 86400),
                )
                if path:
                    paths.append(path)
        else:
            paths = fetch_all_subscriptions(remote_adapters)

        if paths:
            self.stdout.write(self.style.SUCCESS(f"prefetched {len(paths)} subscription(s)"))
        else:
            self.stdout.write(self.style.SUCCESS("no subscriptions needed prefetching"))

    def _load_subscription(self, source: str):
        if os.path.isdir(source):
            return self._load_subscription_dir(source)
        if source.startswith("https://"):
            root = fetch_subscription(source, force=True)
            if not root:
                raise ValueError("subscription fetch failed")
            # fetch_subscription 返回缓存的 adapters.jsonc 文件路径，
            # _load_subscription_dir 接收的是目录（内部拼接 adapters.jsonc），
            # 这里取其所在目录，否则会拼出 adapters.jsonc/adapters.jsonc。
            return self._load_subscription_dir(os.path.dirname(root))
        if source.startswith("http://"):
            raise ValueError("subscription url must use HTTPS")
        with open(source, encoding="utf-8") as f:
            data = parse_jsonc(f.read())
        if not isinstance(data, dict):
            raise ValueError("subscription top-level must be an object")
        if not isinstance(data.get("domains"), dict):
            data = {
                **data,
                "domains": {
                    key: value for key, value in data.items()
                    if key not in ("*", "scripts", "domains") and not key.startswith("_")
                },
            }
        return data, os.path.dirname(os.path.abspath(source))

    def _load_subscription_dir(self, path: str):
        root = os.path.abspath(path)
        # 尝试读取 adapters.jsonc
        sub_file = os.path.join(root, "adapters.jsonc")
        if os.path.exists(sub_file):
            with open(sub_file, encoding="utf-8") as f:
                data = parse_jsonc(f.read())
            if isinstance(data, dict) and isinstance(data.get("domains"), dict):
                scripts_dir = os.path.join(root, "scripts")
                if os.path.isdir(scripts_dir):
                    data["_available_scripts"] = os.listdir(scripts_dir)
                return data, root
        # 回退：旧 subscription.jsonc
        old_sub = os.path.join(root, "subscription.jsonc")
        if os.path.exists(old_sub):
            with open(old_sub, encoding="utf-8") as f:
                data = parse_jsonc(f.read())
            if isinstance(data, dict) and isinstance(data.get("domains"), dict):
                return data, root
        # 回退：旧目录格式
        data = {"domains": {}}
        global_path = os.path.join(root, "global.jsonc")
        if os.path.exists(global_path):
            with open(global_path, encoding="utf-8") as f:
                global_data = parse_jsonc(f.read())
            data["*"] = global_data.get("*", {}) if isinstance(global_data, dict) else {}
        domains_dir = os.path.join(root, "domains")
        if os.path.isdir(domains_dir):
            for fname in sorted(os.listdir(domains_dir)):
                if not (fname.endswith(".jsonc") or fname.endswith(".json")):
                    continue
                domain_key = fname.rsplit(".", 1)[0]
                fpath = os.path.join(domains_dir, fname)
                with open(fpath, encoding="utf-8") as f:
                    data["domains"][domain_key] = parse_jsonc(f.read())
        return data, root

    def _validate_subscription_data(self, data: dict, root: str):
        from site_adapters.services.config.validator import _issue
        issues = []
        domains = data.get("domains", {})
        if not isinstance(domains, dict):
            return [_issue('error', 'domains_not_object', "domains must be an object")]
        for domain_key, value in domains.items():
            if "/" in domain_key or "\\" in domain_key or ".." in domain_key:
                issues.append(_issue('error', 'invalid_domain_key', f"invalid domain key: {domain_key}", path=domain_key))
                continue
            if isinstance(value, str):
                continue
            if not isinstance(value, dict):
                issues.append(_issue('error', 'domain_not_object', f"{domain_key} must be an object", path=domain_key))
                continue
            if value.get("type") == "alias":
                if not value.get("target"):
                    issues.append(_issue('error', 'alias_missing_target', f"{domain_key} alias missing target", path=domain_key))
                continue
            for section in ("metadata", "snapshot", "reader"):
                sec = value.get(section, {})
                if not sec:
                    continue
                if not isinstance(sec, dict):
                    issues.append(_issue('error', 'section_not_object', f"{domain_key}.{section} must be an object", path=f"{domain_key}.{section}"))
                    continue
                for field, field_value in sec.items():
                    if classify_field(section, field) == "unknown":
                        issues.append(_issue('warning', 'unknown_field', f"{domain_key}.{section}.{field} is unknown", path=f"{domain_key}.{section}.{field}"))
                    if field == "script" or field.endswith("_script"):
                        self._check_subscription_script(issues, root, domain_key, section, field, field_value)
        return issues

    def _check_subscription_script(self, issues, root, domain_key, section, field, value):
        from site_adapters.services.config.validator import _issue
        if not value:
            return
        if not isinstance(value, str):
            issues.append(_issue('error', 'script_field_not_string', f"{domain_key}.{section}.{field} must be a string path or URL", path=f"{domain_key}.{section}.{field}"))
            return
        # URL 引用：校验 URL 合法性；外链脚本不会被订阅镜像下载，给出提示
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                issues.append(_issue('error', 'script_url_invalid', f"{domain_key}.{section}.{field} invalid script URL: {value}", path=f"{domain_key}.{section}.{field}"))
            else:
                issues.append(_issue('warning', 'external_script_not_mirrored', f"{domain_key}.{section}.{field} external script URL is not mirrored by subscriptions; ship it under scripts/ and reference it relatively", path=f"{domain_key}.{section}.{field}"))
            return
        # 旧版标量 script 字段（已废弃）：订阅镜像只收集 scripts 数组，标量不会被
        # 下载，给出明确的升级提示，而不是按本地路径是否存在误导性报错。
        issues.append(_issue('warning', 'deprecated_scalar_script', f"{domain_key}.{section}.{field} legacy scalar script is deprecated and will NOT be mirrored; migrate to scripts: [{{\"path\": \"{value}\", \"hook\": \"after\"}}]", path=f"{domain_key}.{section}.{field}"))


    def handle_from_userscript(self, opts):
        """Generate a site adapter config from a Tampermonkey userscript."""
        import re

        source = opts["source"]

        if os.path.isfile(source):
            with open(source, encoding="utf-8") as f:
                content = f.read()
        else:
            self.stdout.write(self.style.ERROR(f"File not found: {source}"))
            return

        # Parse UserScript metadata block
        block_match = re.search(
            r"//\s*==UserScript==\s*\n(.*?)//\s*==/UserScript==",
            content, re.DOTALL,
        )
        if not block_match:
            self.stdout.write(self.style.ERROR("No UserScript metadata block found"))
            return

        block = block_match.group(1)
        matches = re.findall(r"//\s*@match\s+(.+)", block)
        grants = re.findall(r"//\s*@grant\s+(.+)", block)
        name_match = re.search(r"//\s*@name\s+(.+)", block)
        name = name_match.group(1).strip() if name_match else "unknown"

        if not matches:
            self.stdout.write(self.style.ERROR("No @match found in userscript"))
            return

        # Convert @match patterns to domain keys
        domains = []
        for pattern in matches:
            pattern = pattern.strip()
            # *://*.example.com/* -> *.example.com
            m = re.match(r"\*://(?:\*\.)?([^/]+?)(?:/.*)?$", pattern)
            if m:
                domains.append(m.group(1))
            else:
                # https://example.com/path -> example.com
                m2 = re.match(r"https?://([^/]+)", pattern)
                if m2:
                    domains.append(m2.group(1))

        if not domains:
            self.stdout.write(self.style.ERROR("Could not extract domains from @match"))
            return

        # Determine if script needs GM_xmlhttpRequest (suggests auth needed)
        needs_auth = "GM_xmlhttpRequest" in grants or "GM_xmlhttpRequest" in content

        # Generate config
        for domain in domains:
            config = {"metadata": {}, "snapshot": {}, "reader": {}}
            if needs_auth:
                config["auth"] = {
                    "cookie": {"type": "login"},
                }

            self.stdout.write(self.style.SUCCESS(f"\nGenerated config for {domain} (from {name}):"))
            self.stdout.write(json.dumps({domain: config}, indent=2, ensure_ascii=False))

        # Check for DOM selectors in the script
        selector_patterns = re.findall(
            r"""(?:querySelector|querySelectorAll|getElementById|getElementsByClassName)\s*\(\s*['"]([^'"]+)['"]""",
            content,
        )
        if selector_patterns:
            self.stdout.write(f"\nDetected selectors in script (may help configure metadata/snapshot):")
            for sel in set(selector_patterns[:10]):
                self.stdout.write(f"  - {sel}")
