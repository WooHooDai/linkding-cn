import os
import shutil
import tempfile
import time

from django.test import TestCase, override_settings

from site_adapters.services.auth.oauth2 import _resolve_json_path
from site_adapters.services.config import parse_jsonc
from site_adapters.services.config.loader import (
    _cache,
    load_domain_config,
)
from site_adapters.services.config.resolver import (
    get_metadata_config,
    get_snapshot_config,
)


class SiteAdaptersEngineTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(self.cleanup)

    def setup_adapter(self, domain, config_dict, default_config=None):
        """Create a minimal adapter structure with one domain."""
        import json
        adapter = {"domains": {domain: config_dict}}
        if default_config:
            adapter["defaults"] = default_config
        self.write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
        }))
        self.write("adapters/defaults/adapters.jsonc", json.dumps(adapter))

    def setup_adapter_multi(self, domains_dict, default_config=None):
        """Create a minimal adapter structure with multiple domains."""
        import json
        adapter = {"domains": domains_dict}
        if default_config:
            adapter["defaults"] = default_config
        self.write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
        }))
        self.write("adapters/defaults/adapters.jsonc", json.dumps(adapter))

    def cleanup(self):
        _cache.invalidate()
        shutil.rmtree(self.base_dir)

    def write(self, relpath, content):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_parse_jsonc_keeps_urls_inside_strings(self):
        data = parse_jsonc('{"url": "https://example.com/a", "items": [1,], // comment\n}')

        self.assertEqual(data["url"], "https://example.com/a")
        self.assertEqual(data["items"], [1])

    def test_file_content_change_invalidates_cache(self):
        self.setup_adapter("example.com", {"http": {"timeout": 1}})

        self.assertEqual(load_domain_config("https://example.com", self.base_dir)["http"]["timeout"], 1)

        time.sleep(0.1)
        self.write("adapters/defaults/adapters.jsonc", '{"domains": {"example.com": {"http": {"timeout": 2}}}}')
        _cache._last_check = 0  # force re-check

        self.assertEqual(load_domain_config("https://example.com", self.base_dir)["http"]["timeout"], 2)

    def test_domain_config_overrides_adapter_defaults(self):
        self.setup_adapter("example.com", {"http": {"timeout": 1}},
                           default_config={"http": {"timeout": 9}})

        config = load_domain_config("https://example.com", self.base_dir)

        self.assertEqual(config["http"]["timeout"], 1)

    # test_subscriptions_follow_global_order removed: old _subscriptions format
    # is replaced by _adapters in config.jsonc. Subscription ordering is tested
    # via the adapter priority system.

    def test_alias_domain_resolves_target_config(self):
        self.setup_adapter_multi({
            "target.com": {"metadata": {"select_title": [".target"]}},
            "alias.com": {"type": "alias", "target": "target.com"},
        })

        config = load_domain_config("https://alias.com/post", self.base_dir)

        self.assertEqual(config["_domain_key"], "alias.com")
        self.assertEqual(config["metadata"]["select_title"], [".target"])

    def test_alias_loop_returns_no_config(self):
        self.setup_adapter_multi({
            "a.com": {"type": "alias", "target": "b.com"},
            "b.com": {"type": "alias", "target": "a.com"},
        })

        self.assertIsNone(load_domain_config("https://a.com/post", self.base_dir))

    def test_relative_paths_resolve_from_domain_file_directory(self):
        self.setup_adapter("example.com", {"metadata": {"script": "./metadata.js"}})

        config = load_domain_config("https://example.com/post", self.base_dir)

        expected = os.path.realpath(os.path.join(self.base_dir, "adapters", "defaults", "metadata.js"))
        self.assertEqual(config["metadata"]["script"], expected)

    def test_relative_non_script_strings_are_not_resolved(self):
        self.setup_adapter("example.com", {
            "metadata": {"select_title": ["./article"], "request_url": ["../post", "api"]}
        })

        config = load_domain_config("https://example.com/post", self.base_dir)

        self.assertEqual(config["metadata"]["select_title"], ["./article"])
        self.assertEqual(config["metadata"]["request_url"], ["../post", "api"])

    def test_resolve_json_path_supports_array_index(self):
        data = {"data": [{"token": "abc123"}]}

        self.assertEqual(_resolve_json_path(data, "data[0].token"), "abc123")

    def test_resolve_json_path_supports_standard_jsonpath(self):
        data = {"data": {"token": "abc123"}}

        self.assertEqual(_resolve_json_path(data, "$.data.token"), "abc123")

    def test_resolver_merges_http_and_handles_auth_config(self):
        self.setup_adapter("example.com", {
            "auth": {"cookie": {"type": "anon"}},
            "defaults": {
                "timeout": 5,
                "http": {"Cookie": "ignored", "X-Test": "domain"}
            },
            "metadata": {
                "select_title": ["h1"],
                "request_url": ["post/(\\d+)", "api/post/\\1"],
                "rewrite_url": ["post/(\\d+)", "article/\\1"],
                "http": {"X-Test": "section", "Accept": "text/html"}
            }
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_metadata_config("https://example.com/post/123")

        self.assertEqual(config["timeout"], 5)
        self.assertNotIn("Cookie", config["headers"])
        self.assertEqual(config["headers"]["X-Test"], "section")
        self.assertEqual(config["headers"]["Accept"], "text/html")
        self.assertEqual(config["_request_url"], "https://example.com/api/post/123")
        self.assertEqual(config["_rewrite_url"], "https://example.com/article/123")

    def test_metadata_resolver_includes_content_type(self):
        self.setup_adapter("example.com", {
            "metadata": {
                "content_type": "xml",
                "xmlns": {"atom": "http://www.w3.org/2005/Atom"},
                "select_title": ["//atom:title"],
            }
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_metadata_config("https://example.com/post")

        self.assertEqual(config["content_type"], "xml")
        self.assertEqual(config["xmlns"]["atom"], "http://www.w3.org/2005/Atom")

    def test_snapshot_resolver_includes_process_carousels(self):
        self.setup_adapter("example.com", {
            "snapshot": {"process_carousels": ["faceplate-carousel"]}
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/post")

        self.assertEqual(config["process_carousels"], ["faceplate-carousel"])

    def test_snapshot_resolver_includes_precheck_health(self):
        self.setup_adapter("example.com", {
            "snapshot": {"precheck_health": True}
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/post")

        self.assertIs(config["precheck_health"], True)

    def test_snapshot_resolver_includes_raw_xml_request_url(self):
        self.setup_adapter("www.reddit.com", {
            "snapshot": {
                "content_type": "xml",
                "request_url": [
                    "^(.*?)/?(?:\\?.*)?$",
                    "\\1/.rss",
                ],
                "http": {
                    "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8"
                },
            }
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://www.reddit.com/r/linkding/")

        self.assertEqual(config["content_type"], "xml")
        self.assertEqual(
            config["headers"]["Accept"],
            "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        self.assertEqual(config["_request_url"], "https://www.reddit.com/r/linkding/.rss")

    def test_snapshot_resolver_includes_content_type(self):
        self.setup_adapter("example.com", {
            "snapshot": {"content_type": "json"}
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/post")

        self.assertEqual(config["content_type"], "json")

    def test_snapshot_resolver_defaults_enabled_true(self):
        """When 'enabled' is not declared, it defaults to True."""
        self.setup_adapter("example.com", {
            "snapshot": {"content_type": "html"}
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/post")

        self.assertTrue(config["enabled"])

    def test_snapshot_resolver_reads_enabled_false(self):
        """Domain-level snapshot.enabled=false is reflected in the resolved config."""
        self.setup_adapter("example.com", {
            "snapshot": {"enabled": False, "content_type": "html"}
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/post")

        self.assertFalse(config["enabled"])

    def test_snapshot_resolver_route_overrides_enabled(self):
        """Route-level snapshot.enabled=true overrides domain-level enabled=false."""
        self.setup_adapter("example.com", {
            "snapshot": {"enabled": False, "content_type": "html"},
            "routes": {
                "/article/.*": {
                    "snapshot": {"enabled": True}
                }
            }
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            # Route matches → enabled should be True
            config_article = get_snapshot_config("https://example.com/article/123")
            self.assertTrue(config_article["enabled"])

            # No route match → inherits domain-level False
            config_other = get_snapshot_config("https://example.com/about")
            self.assertFalse(config_other["enabled"])

    def test_snapshot_resolver_route_inherits_enabled_false(self):
        """Route without 'enabled' inherits domain-level enabled=false."""
        self.setup_adapter("example.com", {
            "snapshot": {"enabled": False, "content_type": "html"},
            "routes": {
                "/search/.*": {
                    "snapshot": {"keep_elements": [".results"]}
                }
            }
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/search/test")
            self.assertFalse(config["enabled"])
            self.assertEqual(config["keep_elements"], [".results"])

    def test_snapshot_enabled_not_inherited_from_defaults(self):
        """The 'enabled' field is read from the snapshot section only,
        not from the defaults section."""
        self.setup_adapter("example.com", {
            "defaults": {"timeout": 10},
            "snapshot": {"content_type": "html"}
        })

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            config = get_snapshot_config("https://example.com/post")

        # Even though defaults has no 'enabled', snapshot defaults to True
        self.assertTrue(config["enabled"])

    def test_snapshot_builtin_overrides_disables_all_domains(self):
        """_builtin_overrides with snapshot.enabled=false propagates to all
        domains that don't explicitly set enabled=true."""
        import json
        adapter = {
            "_builtin": {"snapshot": {}},
            "_builtin_overrides": {"snapshot": {"enabled": False}},
            "domains": {
                "example.com": {
                    "snapshot": {"content_type": "html"}
                },
                "other.com": {
                    "snapshot": {"enabled": True, "content_type": "html"}
                },
            }
        }
        self.write("adapters/config.jsonc", json.dumps({
            "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
        }))
        self.write("adapters/defaults/adapters.jsonc", json.dumps(adapter))

        with override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir):
            # example.com doesn't set enabled → inherits builtin false
            config1 = get_snapshot_config("https://example.com/post")
            self.assertFalse(config1["enabled"])

            # other.com explicitly sets enabled=true → overrides builtin false
            config2 = get_snapshot_config("https://other.com/page")
            self.assertTrue(config2["enabled"])


class ExecutionLogTestCase(TestCase):
    def test_redact_cmd_args_masks_cookie_file(self):
        from site_adapters.services.execution_log import _redact_cmd_args
        args = ['single-file', '--browser-cookies-file=/tmp/secret.json', '--user-agent=UA']
        result = _redact_cmd_args(args)
        self.assertEqual(result[1], '--browser-cookies-file=[redacted]')
        self.assertEqual(result[2], '--user-agent=UA')

    def test_redact_cmd_args_leaves_normal_args_unchanged(self):
        from site_adapters.services.execution_log import _redact_cmd_args
        args = ['single-file', '--browser-script=/tmp/s.js', '--http-header=X: Y']
        result = _redact_cmd_args(args)
        self.assertEqual(result, args)


class ApplyTogglesTestCase(TestCase):
    def test_apply_toggles_returns_original_when_no_toggles(self):
        from site_adapters.services.config.resolver import _apply_toggles
        section = {"remove_elements": [".ad"], "keep_elements": [".article"]}
        remove, keep = _apply_toggles(section, {"_domain_key": "example.com"}, "user")
        self.assertEqual(remove, [".ad"])
        self.assertEqual(keep, [".article"])

    def test_apply_toggles_applies_default_false_without_username(self):
        from site_adapters.services.config.resolver import _apply_toggles
        section = {
            "toggles": {
                "comments": {
                    "selector": "span#content",
                    "default": False,
                }
            }
        }
        remove, keep = _apply_toggles(
            section, {"_domain_key": "example.com"}, ""
        )
        self.assertIn("span#content", remove)
        self.assertNotIn("span#content", keep)

    def test_apply_toggles_applies_default_true_without_username(self):
        from site_adapters.services.config.resolver import _apply_toggles
        section = {
            "toggles": {
                "comments": {
                    "selector": "span#content",
                    "default": True,
                }
            }
        }
        remove, keep = _apply_toggles(
            section, {"_domain_key": "example.com"}, ""
        )
        self.assertNotIn("span#content", remove)
        self.assertIn("span#content", keep)
