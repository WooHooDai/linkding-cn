import json
import os
import tempfile
from unittest import mock

from django.test import TestCase

from bookmarks.services.snapshot_processor import (
    SnapshotRejectedError,
    _reject_by_health,
    _run_snapshot,
    create_snapshot,
)

CHECK_URL = "bookmarks.services.health_checker.check_url"
URL = "https://example.com/item"


class SnapshotProcessorTestCase(TestCase):
    def _run_raw_snapshot(self, content_type: str, content: str):
        with tempfile.NamedTemporaryFile("w", suffix=".tmp", delete=False) as tmp:
            output_path = tmp.name
        self.addCleanup(lambda: os.path.exists(output_path) and os.unlink(output_path))

        config = {"content_type": content_type}
        with mock.patch(
            "bookmarks.services.snapshot_processor.website_loader.load_page",
            return_value=content,
        ) as mock_load:
            _run_snapshot(URL, output_path, config)

        mock_load.assert_called_once_with(
            URL,
            config,
            load_full_page=True,
        )
        with open(output_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), content)

    def test_json_snapshot_saves_raw_response(self):
        body = json.dumps({"title": "JSON snapshot"})
        self._run_raw_snapshot("json", body)

    def test_xml_snapshot_saves_raw_response(self):
        body = "<?xml version=\"1.0\"?><feed><entry><title>XML snapshot</title></entry></feed>"
        self._run_raw_snapshot("xml", body)


class SnapshotPreflightHealthTestCase(TestCase):
    reason = "分享票据已失效，请在 App 重新复制链接"

    def test_blocked_rejects_with_health_reason(self):
        with mock.patch(CHECK_URL, return_value={
            "status": "blocked", "reason": self.reason,
        }):
            with self.assertRaises(SnapshotRejectedError) as ctx:
                _reject_by_health(URL, {"precheck_health": True}, "alice")
        self.assertEqual(ctx.exception.status, "blocked")
        self.assertEqual(ctx.exception.reason, self.reason)
        self.assertIn(self.reason, str(ctx.exception))

    def test_dead_and_missing_reject(self):
        for status in ("dead", "missing"):
            with self.subTest(status=status), mock.patch(
                CHECK_URL, return_value={"status": status, "reason": self.reason}
            ):
                with self.assertRaises(SnapshotRejectedError):
                    _reject_by_health(URL, {"precheck_health": True})

    def test_ok_skipped_failed_and_unknown_allow(self):
        for status in ("ok", None, "failed", "unknown"):
            with self.subTest(status=status), mock.patch(
                CHECK_URL, return_value={"status": status, "reason": ""}
            ):
                _reject_by_health(URL, {"precheck_health": True})

    def test_probe_failure_does_not_block(self):
        with mock.patch(CHECK_URL, side_effect=OSError("network down")):
            _reject_by_health(URL, {"precheck_health": True})

    def test_disabled_does_not_call_health(self):
        with mock.patch(CHECK_URL) as check:
            _reject_by_health(URL, {})
        check.assert_not_called()

    def test_create_snapshot_runs_preflight_with_username(self):
        config = {"precheck_health": True}
        with mock.patch(
            "bookmarks.services.snapshot_processor.get_snapshot_config",
            return_value=config,
        ), mock.patch(
            "bookmarks.services.snapshot_processor._run_snapshot"
        ), mock.patch(
            "bookmarks.services.snapshot_processor._verify_snapshot_cookie",
            return_value=False,
        ), mock.patch(
            CHECK_URL, return_value={"status": "ok", "reason": ""}
        ) as check:
            create_snapshot(URL, os.path.join(tempfile.gettempdir(), "unused.html"),
                            username="alice")
        check.assert_called_once_with(URL, username="alice")
