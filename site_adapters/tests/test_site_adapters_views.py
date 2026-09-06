import os
import re
import shutil
import tempfile
import json
from unittest import mock

from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from bookmarks.models import User
from site_adapters.services.config import parse_jsonc
from site_adapters.services.config.loader import _cache


class SiteAdaptersViewsTestCase(TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.settings_override = override_settings(LD_SITE_ADAPTERS_DIR=self.base_dir)
        self.settings_override.enable()
        self.addCleanup(self.cleanup)

        user = User.objects.create_user("site-adapter-user", password="password", is_superuser=True)
        self.client.force_login(user)

    def cleanup(self):
        _cache.invalidate()
        self.settings_override.disable()
        shutil.rmtree(self.base_dir)

    def _write_jsonc(self, relpath, data):
        path = os.path.join(self.base_dir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def _setup_two_adapters(self, defaults_first=False):
        adapters = [
            {"id": "custom", "name": "custom", "source": "./custom/adapters.jsonc", "enabled": True},
            {
                "id": "defaults",
                "name": "defaults",
                "source": "./defaults/adapters.jsonc",
                "update_interval": 86400,
                "enabled": True,
            },
        ]
        if defaults_first:
            adapters = [adapters[1], adapters[0]]
        self._write_jsonc("adapters/config.jsonc", {"_adapters": adapters})
        self._write_jsonc(
            "adapters/custom/adapters.jsonc",
            {"_meta": {"id": "custom", "name": "custom"}, "domains": {}},
        )

    def test_site_adapters_requires_superuser(self):
        user = User.objects.create_user("site-adapter-nonstaff", password="password")
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 403)

    def test_site_adapters_allows_superuser(self):
        user = User.objects.create_user("site-adapter-superuser", password="password", is_superuser=True)
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 200)

    def test_site_adapters_requires_active_staff(self):
        user = User.objects.create_user("site-adapter-inactive-superuser", password="password", is_superuser=True, is_active=False)
        self.client.force_login(user)

        response = self.client.get(reverse("linkding:settings.site_adapters"))

        self.assertEqual(response.status_code, 302)

    def test_site_adapters_page_renders(self):
        response = self.client.get(reverse("linkding:settings.site_adapters"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "site-adapters.css")

    def test_site_adapters_page_passes_cookie_type_to_credentials_ui(self):
        self._write_jsonc(
            "adapters/config.jsonc",
            {
                "_adapters": [
                    {
                        "id": "custom",
                        "name": "custom",
                        "source": "./custom/adapters.jsonc",
                        "enabled": True,
                    }
                ]
            },
        )
        self._write_jsonc(
            "adapters/custom/adapters.jsonc",
            {
                "_meta": {"id": "custom", "name": "custom"},
                "domains": {
                    "example.com": {
                        "auth": {"cookie": {"type": "login"}}
                    }
                },
            },
        )
        _cache.invalidate()

        response = self.client.get(reverse("linkding:settings.site_adapters"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        match = re.search(r"window\.__ld_auth_domains = (\[.*?\]);", html, re.S)
        self.assertIsNotNone(match)
        domains = json.loads(match.group(1))
        self.assertEqual(domains[0]["d"], "example.com")
        self.assertEqual(domains[0]["ct"], "login")

    def test_action_update_forwards_force_flag(self):
        """update 动作应将 force 参数原样传给 fetch_subscription。"""
        self._write_jsonc(
            "adapters/config.jsonc",
            {
                "_adapters": [
                    {"id": "custom", "name": "custom", "source": "https://example.test/bundle/", "enabled": True}
                ]
            },
        )
        url = reverse("linkding:settings.site_adapters.subscription_manage")

        with mock.patch(
            "site_adapters.services.subscriptions.fetch_subscription",
            return_value=None,
        ) as fetch_mock:
            self.client.post(url, {"action": "update", "index": "0"})
            self.assertIs(fetch_mock.call_args.kwargs.get("force"), False)
            fetch_mock.reset_mock()
            self.client.post(url, {"action": "update", "index": "0", "force": "1"})
            self.assertIs(fetch_mock.call_args.kwargs.get("force"), True)

    def test_remote_subscription_cache_detected_when_id_differs_from_name(self):
        """远程缓存目录为 {id}.{name}，展示读取路径必须一致，否则误报缺失/0域名。"""
        from site_adapters.services.subscriptions import fetch_subscription

        self._write_jsonc(
            "adapters/config.jsonc",
            {
                "_adapters": [
                    {"id": "custom", "name": "bundle", "source": "https://example.test/bundle/", "enabled": True}
                ]
            },
        )
        payload = {
            "_meta": {"id": "custom", "name": "bundle"},
            "domains": {"example.com": {"metadata": {"select_title": ["h1"]}}},
        }
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = json.dumps(payload)
        resp.headers = {}
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload

        with mock.patch("site_adapters.services.subscriptions.requests.get", return_value=resp):
            fetch_subscription(
                "https://example.test/bundle/",
                name="bundle",
                adapter_id="custom",
                force=True,
            )

        response = self.client.get(reverse("linkding:settings.site_adapters.subscription_manage"))
        self.assertEqual(response.status_code, 200)
        adapter = next(a for a in response.json()["adapters"] if a["id"] == "custom")
        self.assertIs(adapter["cached"], True)
        self.assertFalse(adapter["cache_missing"])
        self.assertEqual(adapter["domain_count"], 1)


    def test_domain_crud_rejects_unsafe_inputs_and_invalid_json(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "domains", "example.com.jsonc"), "w", encoding="utf-8") as f:
            f.write("{}")

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_create"),
            {"domain_key": "../outside"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_save"),
            {"filename": "../outside.jsonc", "content": "{}"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_save"),
            {"filename": "example.com.jsonc", "content": '{"metadata": '},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_rename"),
            {"old_filename": "example.com.jsonc", "new_domain": "../outside"},
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.domain_delete"),
            {"filename": "../outside.jsonc"},
        )
        self.assertEqual(response.status_code, 400)


    def test_action_config_returns_merged_config_without_network(self):
        import json as _json
        os.makedirs(os.path.join(self.base_dir, "adapters", "defaults"))
        with open(os.path.join(self.base_dir, "adapters", "config.jsonc"), "w", encoding="utf-8") as f:
            _json.dump({
                "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
            }, f)
        with open(os.path.join(self.base_dir, "adapters", "defaults", "adapters.jsonc"), "w", encoding="utf-8") as f:
            _json.dump({
                "defaults": {"http": {"timeout": 9}},
                "domains": {"example.com": {"http": {"timeout": 1}, "metadata": {"select_title": ["h1"]}}}
            }, f)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.action"),
            {"action": "test", "test_type": "config", "url": "https://example.com/post"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["domain_key"], "example.com")
        self.assertEqual(result["merged"]["http"]["timeout"], 1)
        # Show config response includes structured issues from validator
        self.assertIn("issues", response.json())
        self.assertIsInstance(response.json()["issues"], list)

    def test_action_config_reports_duplicate_key(self):
        """Show config response includes duplicate key warnings."""
        os.makedirs(os.path.join(self.base_dir, "adapters", "defaults"))
        with open(os.path.join(self.base_dir, "adapters", "config.jsonc"), "w", encoding="utf-8") as f:
            json.dump({
                "_adapters": [{"id": "defaults", "name": "defaults", "source": "./defaults/adapters.jsonc"}]
            }, f)
        # Raw text with duplicate domain key
        with open(os.path.join(self.base_dir, "adapters", "defaults", "adapters.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"domains": {"example.com": {"metadata": {"select_title": ["h1"]}}, '
                    '"example.com": {"metadata": {"select_title": ["h2"]}}}}')

        response = self.client.post(
            reverse("linkding:settings.site_adapters.action"),
            {"action": "test", "test_type": "config", "url": "https://example.com/post"},
        )

        self.assertEqual(response.status_code, 200)
        issues = response.json()["issues"]
        dup_issues = [i for i in issues if i.get("code") == "duplicate_key"]
        self.assertEqual(len(dup_issues), 1)
        self.assertEqual(dup_issues[0]["level"], "warning")
        self.assertIn("example.com", dup_issues[0]["message"])

    def test_action_test_returns_json_error_when_test_fails(self):
        os.makedirs(os.path.join(self.base_dir, "domains"))
        with open(os.path.join(self.base_dir, "domains", "example.com.jsonc"), "w", encoding="utf-8") as f:
            f.write('{"metadata": {"select_title": ["h1"]}}')

        with mock.patch(
            "site_adapters.views.testing.load_website_metadata_for_test",
            side_effect=RuntimeError("blocked"),
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "metadata", "url": "https://example.com/post"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "metadata")
        self.assertEqual(response.json()["error"], "blocked")

    def test_action_metadata_returns_non_retryable_error_message(self):
        from bookmarks.services.website_loader import WebsiteMetadata

        config = {"_domain_key": "example.com", "_request_url": "https://example.com/post"}
        metadata = WebsiteMetadata("https://example.com/post", None, None, None)
        sources = {"error": "Non-retryable metadata response: 403"}
        with (
            mock.patch(
                "site_adapters.views.testing.get_metadata_config",
                return_value=config,
            ),
            mock.patch(
                "site_adapters.views.testing.show_config",
                return_value={},
            ),
            mock.patch(
                "site_adapters.views.testing.load_website_metadata_for_test",
                return_value=(metadata, sources, config),
            ),
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "metadata", "url": "https://example.com/post"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["metadata_error"], "Non-retryable metadata response: 403")
        self.assertTrue(data["failed"])

    def test_metadata_credential_sources_includes_section_cookie(self):
        """Section-level metadata.auth.cookie shows up in credential_sources."""
        from site_adapters.services.auth.credentials import save_shared_cookie
        from site_adapters.views.testing import _snapshot_credential_state

        config = {
            "_domain_key": "*.xiaoheihe.cn",
            "_effective_cookie_scope": "metadata",
            "auth": {
                "cookie": {"type": "auto", "enabled": True},
            },
        }
        save_shared_cookie(domain="*.xiaoheihe.cn", cookie_str="x_xhh_tokenid=abc",
                           scope="metadata")

        state = _snapshot_credential_state(
            config, "site-adapter-user", "api.xiaoheihe.cn")

        self.assertIn("cookie", state)
        self.assertEqual(state["cookie"]["source"], "shared")

    def test_credential_test_uses_snapshot_cookie_scripts_and_refreshes_status(self):
        cookie_config = {"type": "anon"}
        meta_config = {"_domain_key": "example.com", "cookie": cookie_config}

        def mock_verify_and_refresh(*, cookie_config, url, domain_key, verify_context, username="", scope=""):
            from site_adapters.services.auth.credentials import save_shared_cookie
            save_shared_cookie(domain="example.com", cookie_str="session=abc", scope=scope)
            return "session=abc"

        auth_req = {"cookie": True, "headers": [], "token": False, "cookie_type": "anon"}
        with mock.patch("site_adapters.views.testing.get_metadata_config", return_value=meta_config), \
             mock.patch("site_adapters.views.testing.get_snapshot_config", return_value={}), \
             mock.patch("site_adapters.services.auth.credentials.get_auth_requirements_for_domain", return_value=auth_req), \
             mock.patch("site_adapters.views.testing.verify_and_refresh", side_effect=mock_verify_and_refresh):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "credential", "url": "https://example.com/post"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["cookie"]["has_value"])
        self.assertTrue(data["cookie"]["status"] in ("refreshed", "acquired"))
        self.assertEqual(data["cookie"]["preview"], "session=abc")

    def test_view_snapshot_rejects_path_outside_test_assets(self):
        response = self.client.get(
            reverse("linkding:settings.site_adapters.view_snapshot"),
            {"path": "../global.jsonc"},
        )

        self.assertEqual(response.status_code, 404)

    def test_view_snapshot_allows_html_interactions_in_an_opaque_origin(self):
        for extension, body, policy in (
            ("html", "<button>展开章节</button>", "sandbox allow-scripts"),
            ("json", '{"title":"文档"}', "sandbox"),
            ("xml", "<title>文档</title>", "sandbox"),
        ):
            with self.subTest(extension=extension):
                filename = f"snapshot_test.{extension}"
                with open(os.path.join(self.base_dir, filename), "w", encoding="utf-8") as f:
                    f.write(body)
                with mock.patch("site_adapters.views.snapshot.TEST_ASSETS_DIR", self.base_dir):
                    response = self.client.get(
                        reverse("linkding:settings.site_adapters.view_snapshot"),
                        {"file": filename},
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Security-Policy"], policy)
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                self.assertEqual(b"".join(response.streaming_content).decode(), body)

    def test_reader_test_includes_reader_view(self):
        def fake_create_snapshot(url, out_path, username=""):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("<html><body><article><p>Hello</p></article></body></html>")

        with (
            mock.patch("site_adapters.views.testing.get_reader_config", return_value={}),
            mock.patch("site_adapters.views.testing.show_config", return_value={}),
            mock.patch("site_adapters.views.testing.TEST_ASSETS_DIR", self.base_dir),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot",
                side_effect=fake_create_snapshot,
            ),
            mock.patch(
                "bookmarks.services.reader_processor.parse_html",
                return_value={
                    "title": "Example",
                    "wordCount": 123,
                    "content": "<article><p>Hello</p></article>",
                },
            ),
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "reader", "url": "https://example.com/post"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertIn("reader_view", result)
        self.assertTrue(result["reader_view"].startswith("/admin/site-adapters/view-reader?file="))
        metadata_files = [
            f
            for f in os.listdir(self.base_dir)
            if f.startswith("article_") and f.endswith(".json")
        ]
        self.assertEqual(len(metadata_files), 1)
        with open(os.path.join(self.base_dir, metadata_files[0]), encoding="utf-8") as f:
            metadata = json.load(f)
        self.assertEqual(metadata["original_url"], "https://example.com/post")
        self.assertTrue(metadata["snapshot_url"].startswith("/admin/site-adapters/view-snapshot?file="))

    def test_reader_test_uses_xml_snapshot_extension(self):
        def fake_create_snapshot(url, out_path, username=""):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("<feed><title>XML</title></feed>")

        with (
            mock.patch("site_adapters.views.testing.get_reader_config", return_value={}),
            mock.patch(
                "site_adapters.views.testing.get_snapshot_config",
                return_value={"content_type": "xml"},
            ),
            mock.patch("site_adapters.views.testing.show_config", return_value={}),
            mock.patch("site_adapters.views.testing.TEST_ASSETS_DIR", self.base_dir),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot",
                side_effect=fake_create_snapshot,
            ),
            mock.patch(
                "bookmarks.services.reader_processor.parse_content",
                return_value={
                    "title": "XML",
                    "wordCount": 1,
                    "content": "<article><p>XML</p></article>",
                },
            ) as mock_parse_content,
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "reader", "url": "https://example.com/feed.xml"},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["snapshot_view_url"].endswith(".xml"))
        mock_parse_content.assert_called_once_with(
            "<feed><title>XML</title></feed>",
            "xml",
            url="https://example.com/feed.xml",
            username="",
        )

    def test_view_reader_renders_preview_page(self):
        with mock.patch("site_adapters.views.snapshot.TEST_ASSETS_DIR", self.base_dir):
            os.makedirs(self.base_dir, exist_ok=True)
            with open(os.path.join(self.base_dir, "article_test.html"), "w", encoding="utf-8") as f:
                f.write("<article><p>Preview content</p></article>")
            with open(os.path.join(self.base_dir, "article_test.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "title": "Preview",
                        "word_count": 42,
                        "original_url": "https://example.com/original",
                        "snapshot_url": "/admin/site-adapters/view-snapshot?file=snapshot_test.html",
                    },
                    f,
                )

            response = self.client.get(
                reverse("linkding:settings.site_adapters.view_reader"),
                {"file": "article_test.html"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.context["reader_preview_data"], dict)
        self.assertContains(response, "Preview content")
        self.assertContains(response, "reader.js")
        self.assertContains(response, "snapshot_test.html")

    def test_snapshot_test_returns_disabled_when_adapter_disables_snapshot(self):
        """When adapter config has snapshot.enabled=false, the test panel
        should return disabled=True without creating a snapshot."""
        snap_config = {"enabled": False, "content_type": "html", "_domain_key": "example.com"}

        with (
            mock.patch(
                "site_adapters.views.testing.get_snapshot_config",
                return_value=snap_config,
            ),
            mock.patch("site_adapters.views.testing.show_config", return_value={}),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot"
            ) as mock_create,
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "snapshot", "url": "https://example.com/video/123"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["disabled"])
        self.assertNotIn("result", data)
        mock_create.assert_not_called()

    def test_snapshot_test_creates_snapshot_when_enabled(self):
        """When adapter config has snapshot.enabled=true (or absent),
        the test panel should create a snapshot normally."""
        snap_config = {"enabled": True, "content_type": "html", "_domain_key": "example.com"}

        def fake_create_snapshot(url, out_path, username=""):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Snapshot</body></html>")

        with (
            mock.patch(
                "site_adapters.views.testing.get_snapshot_config",
                return_value=snap_config,
            ),
            mock.patch("site_adapters.views.testing.show_config", return_value={}),
            mock.patch("site_adapters.views.testing.TEST_ASSETS_DIR", self.base_dir),
            mock.patch(
                "bookmarks.services.snapshot_processor.create_snapshot",
                side_effect=fake_create_snapshot,
            ),
        ):
            response = self.client.post(
                reverse("linkding:settings.site_adapters.action"),
                {"action": "test", "test_type": "snapshot", "url": "https://example.com/page"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data.get("disabled", False))
        self.assertIn("result", data)
        self.assertTrue(data["result"]["view_url"].endswith(".html"))

    def test_subscription_list_preserves_order_and_initializes_defaults(self):
        self._setup_two_adapters(defaults_first=False)

        response = self.client.get(
            reverse("linkding:settings.site_adapters.subscription_manage")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()["adapters"]],
            ["custom", "defaults"],
        )
        self.assertTrue(
            os.path.exists(
                os.path.join(self.base_dir, "adapters", "defaults", "adapters.jsonc")
            )
        )

    def test_reorder_does_not_force_defaults_first(self):
        self._setup_two_adapters(defaults_first=False)

        response = self.client.post(
            reverse("linkding:settings.site_adapters.subscription_manage"),
            {"action": "reorder", "indices": [0, 1]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()["adapters"]],
            ["custom", "defaults"],
        )

    def test_defaults_adapter_cannot_be_edited_or_deleted(self):
        self._setup_two_adapters(defaults_first=True)
        url = reverse("linkding:settings.site_adapters.subscription_manage")

        edit_response = self.client.post(
            url, {"action": "save", "index": "0", "source": "./defaults/adapters.jsonc"}
        )
        delete_response = self.client.post(
            url, {"action": "delete", "index": "0"}
        )

        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)

    def test_adapter_with_defaults_id_and_other_name_is_not_protected(self):
        self._write_jsonc(
            "adapters/config.jsonc",
            {
                "_adapters": [
                    {
                        "id": "defaults",
                        "name": "other",
                        "source": "./other/adapters.jsonc",
                        "enabled": True,
                    },
                    {
                        "id": "defaults",
                        "name": "defaults",
                        "source": "./defaults/adapters.jsonc",
                        "enabled": True,
                    },
                ]
            },
        )
        url = reverse("linkding:settings.site_adapters.subscription_manage")

        response = self.client.post(url, {"action": "delete", "index": "0"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in response.json()["adapters"]],
            ["defaults"],
        )

    def _make_preview_response(
        self,
        status_code=200,
        content_type="image/png",
        content_length="4",
        chunks=(b"test",),
        headers=None,
    ):
        response = mock.Mock(status_code=status_code)
        response.headers = headers or {
            "Content-Type": content_type,
            "Content-Length": content_length,
        }
        response.iter_content.return_value = chunks
        return response

    def test_preview_image_proxy_streams_image(self):
        preview_response = self._make_preview_response()

        with mock.patch(
            "site_adapters.views.preview.requests.get",
            return_value=preview_response,
        ) as mock_get:
            response = self.client.get(
                reverse("linkding:settings.site_adapters.preview_image"),
                {"url": "https://example.com/preview.png"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(b"".join(response.streaming_content), b"test")
        mock_get.assert_called_once()

    def test_preview_image_proxy_requires_superuser(self):
        user = User.objects.create_user("preview-nonstaff", password="password")
        self.client.force_login(user)

        response = self.client.get(
            reverse("linkding:settings.site_adapters.preview_image"),
            {"url": "https://example.com/preview.png"},
        )

        self.assertEqual(response.status_code, 403)

    def test_preview_image_proxy_rejects_invalid_url(self):
        response = self.client.get(
            reverse("linkding:settings.site_adapters.preview_image"),
            {"url": "file:///etc/passwd"},
        )

        self.assertEqual(response.status_code, 400)

    def test_preview_image_proxy_rejects_non_image_content(self):
        preview_response = self._make_preview_response(content_type="text/html")

        with mock.patch(
            "site_adapters.views.preview.requests.get",
            return_value=preview_response,
        ):
            response = self.client.get(
                reverse("linkding:settings.site_adapters.preview_image"),
                {"url": "https://example.com/preview"},
            )

        self.assertEqual(response.status_code, 400)
        preview_response.close.assert_called_once()

    def test_preview_image_proxy_rejects_oversized_image(self):
        preview_response = self._make_preview_response(
            content_length=str(settings.LD_PREVIEW_MAX_SIZE + 1)
        )

        with mock.patch(
            "site_adapters.views.preview.requests.get",
            return_value=preview_response,
        ):
            response = self.client.get(
                reverse("linkding:settings.site_adapters.preview_image"),
                {"url": "https://example.com/preview.png"},
            )

        self.assertEqual(response.status_code, 400)
        preview_response.close.assert_called_once()

    def test_preview_image_proxy_follows_redirect(self):
        redirect_response = mock.Mock(status_code=302)
        redirect_response.headers = {"Location": "https://cdn.example.com/preview.png"}
        redirect_response.iter_content.return_value = []
        preview_response = self._make_preview_response()

        with mock.patch(
            "site_adapters.views.preview.requests.get",
            side_effect=[redirect_response, preview_response],
        ) as mock_get:
            response = self.client.get(
                reverse("linkding:settings.site_adapters.preview_image"),
                {"url": "https://example.com/preview"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"test")
        self.assertEqual(mock_get.call_count, 2)
