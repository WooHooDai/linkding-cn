import os
import subprocess
import tempfile
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from bookmarks.services import singlefile


class SingleFileServiceTestCase(TestCase):
    def setUp(self):
        self.temp_html_filepath = None

    def tearDown(self):
        if self.temp_html_filepath and os.path.exists(self.temp_html_filepath):
            os.remove(self.temp_html_filepath)

    def create_test_file(self, *args, **kwargs):
        self.temp_html_filepath = tempfile.mkstemp(suffix=".tmp")[1]

    def test_create_snapshot_failure(self):
        # subprocess fails - which it probably doesn't as single-file doesn't return exit codes
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = subprocess.CalledProcessError(1, "command")

            with self.assertRaises(singlefile.SingleFileError):
                singlefile.create_snapshot("http://example.com", "nonexistentfile.tmp")

        # so also check that it raises error if output file isn't created
        with (
            mock.patch("subprocess.Popen"),
            self.assertRaises(singlefile.SingleFileError),
        ):
            singlefile.create_snapshot("http://example.com", "nonexistentfile.tmp")

    def test_create_snapshot_empty_options(self):
        mock_process = mock.Mock()
        mock_process.wait.return_value = 0
        self.create_test_file()

        with mock.patch("subprocess.Popen") as mock_popen:
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            called_args = mock_popen.call_args.args[0]
            self.assertEqual(called_args[0], "single-file")
            self.assertEqual(called_args[-2], "http://example.com")
            self.assertEqual(called_args[-1], self.temp_html_filepath)
            self.assertIn(
                "--browser-arg=--disable-blink-features=AutomationControlled",
                called_args,
            )
            self.assertIn(f"--user-agent={settings.LD_DEFAULT_USER_AGENT}", called_args)
            self.assertEqual(
                called_args.count("--browser-arg=--headless=new"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--user-data-dir=chromium-profile"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--no-sandbox"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--load-extension=uBOLite.chromium.mv3"),
                1,
            )

    @override_settings(
        LD_SINGLEFILE_OPTIONS='--some-option "some value" --another-option "another value" --third-option="third value"'
    )
    def test_create_snapshot_custom_options(self):
        mock_process = mock.Mock()
        mock_process.wait.return_value = 0
        self.create_test_file()

        with mock.patch("subprocess.Popen") as mock_popen:
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            called_args = mock_popen.call_args.args[0]
            self.assertEqual(called_args[0], "single-file")
            self.assertEqual(called_args[-2], "http://example.com")
            self.assertEqual(called_args[-1], self.temp_html_filepath)
            self.assertIn("--some-option", called_args)
            self.assertIn("some value", called_args)
            self.assertIn("--another-option", called_args)
            self.assertIn("another value", called_args)
            self.assertIn("--third-option=third value", called_args)
            self.assertIn(
                "--browser-arg=--disable-blink-features=AutomationControlled",
                called_args,
            )
            self.assertIn(f"--user-agent={settings.LD_DEFAULT_USER_AGENT}", called_args)

    def test_create_snapshot_default_timeout_setting(self):
        mock_process = mock.Mock()
        mock_process.wait.return_value = 0
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            mock_process.wait.assert_called_with(timeout=120)

    @override_settings(LD_SINGLEFILE_TIMEOUT_SEC=180)
    def test_create_snapshot_custom_timeout_setting(self):
        mock_process = mock.Mock()
        mock_process.wait.return_value = 0
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            mock_process.wait.assert_called_with(timeout=180)
