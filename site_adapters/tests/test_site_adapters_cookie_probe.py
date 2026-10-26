"""L1 cookie 探针的 method / url 覆盖行为。"""
from unittest import mock

from django.test import TestCase

from site_adapters.services.auth.cookies import _verify_cookie_l1_probe


class L1ProbeMethodTests(TestCase):
    def _probe(self, probe_cfg, cookie_str="ck=1", url="https://a.example/page"):
        verify_cfg = {"http_head_probe": {"enabled": True, **probe_cfg}}
        with mock.patch(
            "bookmarks.services.website_loader._open_http_stream"
        ) as fetch:
            fetch.return_value.status_code = 200
            result = _verify_cookie_l1_probe(
                verify_config=verify_cfg, url=url,
                cookie_str=cookie_str, domain_key="a.example",
            )
        return result, fetch

    def test_default_method_is_head_and_uses_request_url(self):
        _, fetch = self._probe({})
        self.assertEqual(fetch.call_args.args[0], "https://a.example/page")
        self.assertEqual(fetch.call_args.kwargs.get("method"), "HEAD")

    def test_get_method_and_static_url_override(self):
        cfg = {
            "method": "GET",
            "url": "https://a.example/api/check",
            "invalid_status": [401, 403],
        }
        result, fetch = self._probe(cfg)
        self.assertTrue(result["valid"])
        self.assertEqual(fetch.call_args.args[0], "https://a.example/api/check")
        self.assertEqual(fetch.call_args.kwargs.get("method"), "GET")

    def test_get_probe_invalid_status_triggers_invalid(self):
        cfg = {
            "method": "GET",
            "url": "https://a.example/api/check",
            "invalid_status": [401, 403],
        }
        with mock.patch(
            "bookmarks.services.website_loader._open_http_stream"
        ) as fetch:
            fetch.return_value.status_code = 403
            result = _verify_cookie_l1_probe(
                verify_config={"http_head_probe": {"enabled": True, **cfg}},
                url="https://a.example/page",
                cookie_str="ck=1", domain_key="a.example",
            )
        self.assertFalse(result["valid"])
        self.assertIn("403", result["reason"])
