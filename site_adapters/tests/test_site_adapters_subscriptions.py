import json
import os
import shutil
import tempfile
from unittest import mock

from django.test import TestCase, override_settings

from site_adapters.services.subscriptions import (
    fetch_subscription,
    validate_subscription_url,
)


class SiteAdaptersSubscriptionsTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir)
        self.settings_override.enable()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        from site_adapters.services.config.loader import _cache
        _cache.invalidate()
        self.settings_override.disable()
        shutil.rmtree(self.base_dir)

    def response(self, payload, headers=None):
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = json.dumps(payload)
        resp.headers = headers or {}
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def test_fetch_subscription_preserves_string_aliases(self):
        payload = {
            "_meta": {"name": "bundle", "version": 1},
            "domains": {
                "target.com": {"metadata": {"select_title": ["h1"]}},
                "alias.com": "target.com",
            },
        }

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload),
        ):
            file_path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        data = json.loads(open(file_path, encoding="utf-8").read())
        alias_config = data["domains"]["alias.com"]
        self.assertEqual(alias_config, "target.com")

    def test_fetch_subscription_accepts_legacy_file_url(self):
        """向后兼容：source 指向 adapters.jsonc 文件路径时也能正常工作。"""
        payload = {
            "domains": {"example.com": {"metadata": {"select_title": ["h1"]}}},
        }

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload),
        ):
            file_path = fetch_subscription(
                "https://example.test/bundle/adapters.jsonc",
                name="bundle", force=True,
            )

        self.assertTrue(os.path.exists(file_path))

    def test_fetch_subscription_writes_meta_json(self):
        """下载后应在 _meta.json 中记录运行时状态。"""
        payload = {"domains": {"example.com": {}}}
        headers = {"ETag": '"abc123"', "Last-Modified": "Mon, 11 Aug 2026 00:00:00 GMT"}

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload, headers=headers),
        ):
            fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True,
            )

        # 验证 _meta.json
        from site_adapters.services.subscriptions import _content_fingerprint, _get_meta_entry
        entry = _get_meta_entry("https://example.test/bundle/")
        self.assertIsNotNone(entry.get("last_fetch"))
        self.assertEqual(entry.get("etag"), '"abc123"')
        self.assertEqual(entry.get("content_hash"), _content_fingerprint(payload))

    def test_fetch_subscription_scripts_downloaded(self):
        """订阅源中包含 scripts 引用时，脚本应被下载到 scripts/ 目录。"""
        payload = {
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [
                            {"path": "cleanup.js", "hook": "before"},
                            {"path": "subdir/extract.py", "hook": "replace"},
                        ]
                    }
                }
            }
        }

        # 模拟脚本下载响应
        def mock_get(url, *args, **kwargs):
            if "adapters.jsonc" in url:
                return self.response(payload)
            elif "cleanup.js" in url:
                resp = mock.Mock()
                resp.status_code = 200
                resp.text = "// cleanup script content"
                resp.headers = {}
                resp.raise_for_status.return_value = None
                return resp
            elif "extract.py" in url:
                resp = mock.Mock()
                resp.status_code = 200
                resp.text = "# extract script content"
                resp.headers = {}
                resp.raise_for_status.return_value = None
                return resp
            return mock.Mock(status_code=404)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            file_path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True,
            )

        # 验证脚本文件已下载
        scripts_dir = os.path.join(os.path.dirname(file_path), "scripts")
        self.assertTrue(os.path.exists(os.path.join(scripts_dir, "cleanup.js")))
        self.assertTrue(os.path.exists(os.path.join(scripts_dir, "subdir", "extract.py")))

        # 验证 adapters.jsonc 保持原样（路径不改写）
        data = json.loads(open(file_path, encoding="utf-8").read())
        scripts = data["domains"]["example.com"]["snapshot"]["scripts"]
        self.assertEqual(scripts[0]["path"], "cleanup.js")

    def test_resolve_script_ref_https_url_rejected(self):
        """HTTPS URL 不应作为订阅源脚本路径。"""
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "https://cdn.example.com/scripts/clean.js",
            "https://base.test/bundle/",
        )
        self.assertIsNone(url)
        self.assertIsNone(name)

    def test_resolve_script_ref_relative_resolves_against_base(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "./scripts/a.js", "https://base.test/bundle/"
        )
        self.assertEqual(url, "https://base.test/bundle/scripts/a.js")
        self.assertEqual(name, "a.js")

    def test_resolve_script_ref_plain_name_infers_scripts_dir(self):
        """纯文件名推断在远端 scripts/ 目录。"""
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "cleanup.js", "https://base.test/bundle/"
        )
        self.assertEqual(url, "https://base.test/bundle/scripts/cleanup.js")
        self.assertEqual(name, "cleanup.js")

    def test_resolve_script_ref_dir_prefixed_name(self):
        """目录前缀名推断在远端 scripts/ 目录。"""
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "zhihu/extract.py", "https://base.test/bundle/"
        )
        self.assertEqual(url, "https://base.test/bundle/scripts/zhihu/extract.py")
        self.assertEqual(name, "zhihu/extract.py")

    def test_resolve_script_ref_http_rejected(self):
        from site_adapters.services.subscriptions import _resolve_script_ref
        url, name = _resolve_script_ref(
            "http://insecure.example.com/s.js",
            "https://base.test/bundle/",
        )
        self.assertIsNone(url)
        self.assertIsNone(name)

    def test_validate_https_url_rejects_private_ip(self):
        from site_adapters.services.subscriptions import _validate_https_url
        with self.assertRaises(ValueError):
            _validate_https_url("https://192.168.1.1/file.jsonc")

    def test_validate_https_url_accepts_public_url(self):
        from site_adapters.services.subscriptions import _validate_https_url
        parsed = _validate_https_url("https://cdn.example.com/file.jsonc")
        self.assertEqual(parsed.hostname, "cdn.example.com")

    def test_validate_subscription_url_rejects_private_hosts(self):
        with self.assertRaises(ValueError):
            validate_subscription_url("https://127.0.0.1/bundle.jsonc")

    def test_force_fetch_failure_returns_none(self):
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=Exception("boom"),
        ):
            self.assertIsNone(
                fetch_subscription(
                    "https://example.test/bundle/", name="bundle", force=True
                )
            )

    def test_is_safe_script_key_allows_subdirs(self):
        from site_adapters.services.subscriptions import _is_safe_script_key
        self.assertTrue(_is_safe_script_key("zhihu/extract.py"))
        self.assertTrue(_is_safe_script_key("cleanup.js"))

    def test_is_safe_script_key_rejects_dotdot(self):
        from site_adapters.services.subscriptions import _is_safe_script_key
        self.assertFalse(_is_safe_script_key("../escape.py"))
        self.assertFalse(_is_safe_script_key("foo/../bar.py"))

    def test_is_safe_script_key_rejects_dotfile(self):
        from site_adapters.services.subscriptions import _is_safe_script_key
        self.assertFalse(_is_safe_script_key(".hidden.py"))
        self.assertFalse(_is_safe_script_key("subdir/.hidden.py"))

    def test_collect_script_refs_scans_scripts_array(self):
        from site_adapters.services.subscriptions import _collect_script_refs
        data = {
            "domains": {
                "example.com": {
                    "metadata": {
                        "scripts": [
                            {"path": "before.py", "hook": "before"},
                            {"path": "after.py", "hook": "after"},
                        ]
                    },
                    "snapshot": {
                        "scripts": [
                            {"path": "replace.py", "hook": "replace"},
                        ]
                    },
                }
            }
        }
        refs = _collect_script_refs(data)
        self.assertIn("example.com", refs)
        self.assertEqual(set(refs["example.com"]), {"before.py", "after.py", "replace.py"})

    def test_collect_script_refs_ignores_old_script_field(self):
        """不应收集旧的 script 标量字段。"""
        from site_adapters.services.subscriptions import _collect_script_refs
        data = {
            "domains": {
                "example.com": {
                    "metadata": {
                        "script": "old_script.py",           # 旧格式，忽略
                        "scripts": [{"path": "new_script.py", "hook": "before"}],
                    }
                }
            }
        }
        refs = _collect_script_refs(data)
        self.assertEqual(refs["example.com"], ["new_script.py"])

    def test_collect_script_refs_descends_into_routes_and_defaults(self):
        """routes 与 defaults 中的脚本也必须被收集，否则不会被缓存。"""
        from site_adapters.services.subscriptions import _collect_script_refs
        data = {
            "defaults": {
                "snapshot": {"scripts": [{"path": "shared.js", "hook": "after"}]}
            },
            "domains": {
                "example.com": {
                    "defaults": {
                        "metadata": {"scripts": [{"path": "d.py", "hook": "before"}]}
                    },
                    "routes": {
                        "/article/": {
                            "metadata": {
                                "scripts": [{"path": "route.py", "hook": "replace"}]
                            },
                            "snapshot": {
                                "scripts": [{"path": "route_snap.js", "hook": "after"}]
                            },
                        }
                    },
                }
            },
        }
        refs = _collect_script_refs(data)
        self.assertEqual(refs["_defaults"], ["shared.js"])
        self.assertEqual(
            set(refs["example.com"]), {"d.py", "route.py", "route_snap.js"}
        )

    def test_fetch_downloads_route_scripts(self):
        """路由级脚本应被下载到 scripts/ 目录。"""
        payload = {
            "domains": {
                "example.com": {
                    "routes": {
                        "/article/": {
                            "metadata": {
                                "scripts": [{"path": "route.py", "hook": "replace"}]
                            }
                        }
                    }
                }
            }
        }

        def mock_get(url, *args, **kwargs):
            if url.endswith("route.py"):
                return self.script_response("# route")
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(path), "scripts", "route.py"))
        )

    def test_fetch_recovers_when_cache_file_deleted(self):
        """缓存文件丢失时，即使 _meta.json 中有旧指纹也应完整重下。"""
        payload = self.script_payload()

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                return self.script_response("// a")
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )
            # 模拟缓存目录被清空（_meta.json 仍保留旧 content_hash）
            shutil.rmtree(os.path.dirname(path))
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        self.assertTrue(os.path.exists(path))
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(path), "scripts", "a.js"))
        )

    def test_fetch_recovers_when_cache_deleted_and_server_304(self):
        """缓存丢失但服务端依据旧 ETag 返回 304 时，应无条件重取。"""
        payload = self.script_payload()
        state = {"body": 0}

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                return self.script_response("// a")
            state["body"] += 1
            if state["body"] == 2:
                return self.response_304()
            return self.response(payload, headers={"ETag": '"v1"'})

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )
            shutil.rmtree(os.path.dirname(path))
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        self.assertTrue(os.path.exists(path))
        self.assertTrue(
            os.path.exists(os.path.join(os.path.dirname(path), "scripts", "a.js"))
        )

    def test_cleanup_removes_unreferenced_scripts(self):
        """下载后应清理不再被引用的脚本文件。"""
        from site_adapters.services.subscriptions import _write_adapter_file

        temp_dir = os.path.join(self.base_dir, "adapters", "test-adapter.test")
        scripts_dir = os.path.join(temp_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)

        # 预先放一个旧脚本
        old_script = os.path.join(scripts_dir, "old_script.js")
        with open(old_script, "w") as f:
            f.write("// old")

        data = {
            "domains": {
                "example.com": {
                    "snapshot": {
                        "scripts": [{"path": "new_script.py", "hook": "before"}]
                    }
                }
            }
        }

        def mock_get(url, *args, **kwargs):
            resp = mock.Mock()
            resp.status_code = 200
            resp.text = "# new script"
            resp.headers = {}
            resp.raise_for_status.return_value = None
            return resp

        file_path = os.path.join(temp_dir, "adapters.jsonc")
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            _write_adapter_file(file_path, "https://example.test/bundle/", data)

        # 旧脚本应被清理
        self.assertFalse(os.path.exists(old_script))
        # 新脚本应存在
        self.assertTrue(os.path.exists(os.path.join(scripts_dir, "new_script.py")))

    def test_normalize_source_strips_adapter_filename(self):
        from site_adapters.services.subscriptions import _normalize_source_to_directory
        self.assertEqual(
            _normalize_source_to_directory("https://example.test/bundle/adapters.jsonc"),
            "https://example.test/bundle/",
        )
        self.assertEqual(
            _normalize_source_to_directory("https://example.test/bundle/"),
            "https://example.test/bundle/",
        )
        self.assertEqual(
            _normalize_source_to_directory("./defaults/adapters.jsonc"),
            "./defaults",
        )

    def response_304(self):
        resp = mock.Mock()
        resp.status_code = 304
        resp.headers = {}
        resp.raise_for_status.return_value = None
        return resp

    def test_content_hash_skips_rewrite_when_unchanged(self):
        """内容指纹未变时，不应重新写入缓存或下载脚本。"""
        payload = {
            "domains": {"example.com": {"metadata": {"select_title": ["h1"]}}},
        }
        from site_adapters.services.subscriptions import _write_adapter_file
        real_write = _write_adapter_file
        write_calls = []

        def counting_write(*args, **kwargs):
            write_calls.append(args)
            return real_write(*args, **kwargs)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            return_value=self.response(payload),
        ), mock.patch(
            "site_adapters.services.subscriptions._write_adapter_file",
            side_effect=counting_write,
        ):
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)
            self.assertEqual(len(write_calls), 1)
            write_calls.clear()
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertEqual(write_calls, [])

    def test_version_precheck_skips_body_when_unchanged(self):
        """checkUpdateUrl 返回相同版本时，不下载正文。"""
        payload = {
            "_meta": {
                "id": "bundle",
                "name": "bundle",
                "version": 1,
                "checkUpdateUrl": "https://example.test/bundle/check",
            },
            "domains": {"example.com": {}},
        }
        calls = []

        def mock_get(url, *args, **kwargs):
            calls.append(url)
            if url.endswith("/check"):
                return self.response({"id": "bundle", "version": 1})
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)
            calls.clear()
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertEqual(calls, ["https://example.test/bundle/check"])

    def test_version_precheck_triggers_body_when_changed(self):
        """checkUpdateUrl 返回新版本时，应继续下载正文。"""
        payload = {
            "_meta": {
                "id": "bundle",
                "name": "bundle",
                "version": 1,
                "checkUpdateUrl": "https://example.test/bundle/check",
            },
            "domains": {"example.com": {}},
        }
        calls = []

        def mock_get(url, *args, **kwargs):
            calls.append(url)
            if url.endswith("/check"):
                return self.response({"id": "bundle", "version": 2})
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)
            calls.clear()
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertIn("https://example.test/bundle/adapters.jsonc", calls)

    def test_304_syncs_new_version(self):
        """正文返回 304 但版本预检发现新版本时，应同步运行时 version。"""
        payload = {
            "_meta": {
                "id": "bundle",
                "name": "bundle",
                "version": 1,
                "checkUpdateUrl": "https://example.test/bundle/check",
            },
            "domains": {"example.com": {}},
        }
        calls = []
        body_count = {'n': 0}

        def mock_get(url, *args, **kwargs):
            calls.append(url)
            if url.endswith("/check"):
                return self.response({"id": "bundle", "version": 2})
            body_count['n'] += 1
            if body_count['n'] == 1:
                return self.response(payload)
            return self.response_304()

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)
            calls.clear()
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        from site_adapters.services.subscriptions import _get_meta_entry
        entry = _get_meta_entry("https://example.test/bundle/")
        self.assertEqual(entry.get("version"), "2")

    def test_version_precheck_failure_falls_back_to_body(self):
        """版本接口返回异常 payload 时，应降级为完整下载正文。"""
        payload = {
            "_meta": {
                "id": "bundle",
                "name": "bundle",
                "version": 1,
                "checkUpdateUrl": "https://example.test/bundle/check",
            },
            "domains": {"example.com": {}},
        }
        calls = []

        def mock_get(url, *args, **kwargs):
            calls.append(url)
            if url.endswith("/check"):
                return self.response({"unexpected": True})
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)
            calls.clear()
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertIn("https://example.test/bundle/adapters.jsonc", calls)

    def test_force_bypasses_interval_gate(self):
        """force 应跳过 update_interval 时间闸门并重新拉取。"""
        payload = {"domains": {"example.com": {}}}
        calls = []

        def mock_get(url, *args, **kwargs):
            calls.append(url)
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            fetch_subscription("https://example.test/bundle/", name="bundle")
            calls.clear()
            # 同 interval 内非 force 应跳过
            fetch_subscription("https://example.test/bundle/", name="bundle")
            self.assertEqual(calls, [])
            # force 应重新拉取
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)
            self.assertEqual(calls, ["https://example.test/bundle/adapters.jsonc"])

    # ------------------------------------------------------------------
    # 缓存自愈：脚本未下载完整时，后续更新应自动补齐
    # ------------------------------------------------------------------

    def script_response(self, text, status=200):
        resp = mock.Mock()
        resp.status_code = status
        resp.text = text
        resp.headers = {}
        resp.raise_for_status.return_value = None
        return resp

    def script_payload(self):
        return {
            "domains": {
                "example.com": {
                    "snapshot": {"scripts": [{"path": "a.js", "hook": "after"}]}
                }
            },
        }

    def test_repairs_missing_script_when_content_unchanged(self):
        """脚本本地丢失时，即使内容指纹未变也应重新下载。"""
        payload = self.script_payload()
        script_hits = []

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                script_hits.append(url)
                return self.script_response("// a")
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )
            script_path = os.path.join(os.path.dirname(path), "scripts", "a.js")
            self.assertTrue(os.path.exists(script_path))
            # 模拟下载中断导致脚本缓存不完整
            os.remove(script_path)
            script_hits.clear()
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertTrue(os.path.exists(script_path))
        self.assertEqual(script_hits, ["https://example.test/bundle/scripts/a.js"])

    def test_repairs_missing_script_on_304(self):
        """服务端 304 时仍需补齐缺失脚本。"""
        payload = {
            "_meta": {"id": "bundle", "name": "bundle", "version": 1},
            **self.script_payload(),
        }
        state = {"body": 0}

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                return self.script_response("// a")
            state["body"] += 1
            if state["body"] == 1:
                return self.response(payload)
            return self.response_304()

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )
            script_path = os.path.join(os.path.dirname(path), "scripts", "a.js")
            os.remove(script_path)
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertTrue(os.path.exists(script_path))

    def test_repairs_missing_script_when_version_unchanged(self):
        """版本预检判定未变时仍需补齐缺失脚本。"""
        payload = {
            "_meta": {
                "id": "bundle",
                "name": "bundle",
                "version": 1,
                "checkUpdateUrl": "https://example.test/bundle/check",
            },
            **self.script_payload(),
        }

        def mock_get(url, *args, **kwargs):
            if url.endswith("/check"):
                return self.response({"id": "bundle", "version": 1})
            if url.endswith("a.js"):
                return self.script_response("// a")
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )
            script_path = os.path.join(os.path.dirname(path), "scripts", "a.js")
            os.remove(script_path)
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        self.assertTrue(os.path.exists(script_path))

    def test_needs_fetch_ignores_interval_when_scripts_missing(self):
        """脚本缺失时，_needs_fetch 应忽略时间闸门触发补齐。"""
        from site_adapters.services.subscriptions import (
            _last_fetch_cache,
            _needs_fetch,
        )

        payload = self.script_payload()

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                return self.script_response("// a")
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        sub = {"source": "https://example.test/bundle/", "name": "bundle"}
        _last_fetch_cache.clear()
        self.assertFalse(_needs_fetch(sub))
        os.remove(os.path.join(os.path.dirname(path), "scripts", "a.js"))
        self.assertTrue(_needs_fetch(sub))

    def test_script_download_failure_marks_partial(self):
        """某个脚本下载失败时，应记录 partial 状态与失败脚本。"""
        from site_adapters.services.subscriptions import _get_meta_entry

        payload = self.script_payload()

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                resp = mock.Mock()
                resp.status_code = 500
                resp.headers = {}
                return resp
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ), mock.patch("site_adapters.services.subscriptions.time.sleep"):
            fetch_subscription("https://example.test/bundle/", name="bundle", force=True)

        entry = _get_meta_entry("https://example.test/bundle/")
        self.assertEqual(entry.get("fetch_status"), "partial")
        self.assertEqual(entry.get("script_failures"), ["a.js"])

    def test_script_download_retries_transient_failure(self):
        """瞬时 5xx 应重试，重试成功后缓存完整。"""
        payload = self.script_payload()
        state = {"n": 0}

        def mock_get(url, *args, **kwargs):
            if url.endswith("a.js"):
                state["n"] += 1
                if state["n"] == 1:
                    return self.script_response("", status=503)
                return self.script_response("// a")
            return self.response(payload)

        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ), mock.patch("site_adapters.services.subscriptions.time.sleep"):
            path = fetch_subscription(
                "https://example.test/bundle/", name="bundle", force=True
            )

        self.assertEqual(state["n"], 2)
        script_path = os.path.join(os.path.dirname(path), "scripts", "a.js")
        self.assertTrue(os.path.exists(script_path))

    def test_failed_script_download_preserves_existing_file(self):
        """脚本刷新失败时，不应删除磁盘上已有的旧脚本。"""
        from site_adapters.services.subscriptions import _write_adapter_file

        temp_dir = os.path.join(self.base_dir, "adapters", "x.y")
        scripts_dir = os.path.join(temp_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        existing = os.path.join(scripts_dir, "a.js")
        with open(existing, "w", encoding="utf-8") as f:
            f.write("// old")

        data = self.script_payload()

        def mock_get(url, *args, **kwargs):
            resp = mock.Mock()
            resp.status_code = 500
            resp.headers = {}
            return resp

        file_path = os.path.join(temp_dir, "adapters.jsonc")
        with mock.patch(
            "site_adapters.services.subscriptions.requests.get",
            side_effect=mock_get,
        ), mock.patch("site_adapters.services.subscriptions.time.sleep"):
            result = _write_adapter_file(
                file_path, "https://example.test/bundle/", data
            )

        self.assertTrue(os.path.exists(existing))
        with open(existing, encoding="utf-8") as f:
            self.assertEqual(f.read(), "// old")
        self.assertEqual(result["failed"], ["a.js"])
