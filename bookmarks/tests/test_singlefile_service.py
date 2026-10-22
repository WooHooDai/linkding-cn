import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from bookmarks.services import singlefile


class SingleFileServiceTestCase(TestCase):
    def setUp(self):
        self.temp_html_filepath = None
        # 进程级"只警告一次"的开关，测试间需要复位以免相互影响
        singlefile._PROFILE_ARG_STRIPPED_WARNED = False

    def tearDown(self):
        if self.temp_html_filepath and os.path.exists(self.temp_html_filepath):
            os.remove(self.temp_html_filepath)

    def create_test_file(self, *args, **kwargs):
        self.temp_html_filepath = tempfile.mkstemp(suffix=".tmp")[1]

    def successful_singlefile_process(self, output=""):
        process = mock.Mock()
        process.returncode = 0

        def _communicate(timeout=None):
            open(self.temp_html_filepath, "w").close()
            return output, None

        process.communicate.side_effect = _communicate
        return process

    def temp_profile_dir(self):
        profile_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, profile_dir, True)
        return profile_dir

    def test_create_snapshot_failure(self):
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_popen.side_effect = OSError("missing binary")

            with self.assertRaises(singlefile.SingleFileError):
                singlefile.create_snapshot("http://example.com", "nonexistentfile.tmp")

        # so also check that it raises error if output file isn't created
        mock_process = mock.Mock()
        mock_process.communicate.return_value = ("", None)
        with (
            mock.patch("subprocess.Popen", return_value=mock_process),
            self.assertRaises(singlefile.SingleFileError),
        ):
            singlefile.create_snapshot("http://example.com", "nonexistentfile.tmp")

    def test_create_snapshot_does_not_accept_stale_output_file(self):
        self.create_test_file()
        mock_process = mock.Mock()
        mock_process.communicate.return_value = ("", None)

        with (
            mock.patch("subprocess.Popen", return_value=mock_process),
            self.assertRaises(singlefile.SingleFileError),
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

    def test_create_snapshot_accepts_nonzero_exit_code_with_fresh_output_file(self):
        self.create_test_file()
        mock_process = self.successful_singlefile_process()
        mock_process.returncode = 1

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

    def test_create_snapshot_error_includes_browser_output(self):
        """失败信息必须带上子进程输出，否则只剩一句无信息量的框架自述。"""
        self.create_test_file()
        mock_process = mock.Mock()
        mock_process.returncode = 255
        mock_process.communicate.return_value = (
            "The browser exited unexpectedly\n",
            None,
        )

        with (
            mock.patch("subprocess.Popen", return_value=mock_process),
            self.assertRaises(singlefile.SingleFileError) as ctx,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

        self.assertIn("The browser exited unexpectedly", str(ctx.exception))
        self.assertIn("exit=255", str(ctx.exception))

    def test_create_snapshot_empty_options(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()
        profile_dir = self.temp_profile_dir()

        with (
            self.settings(LD_CHROMIUM_PROFILE_DIR=profile_dir),
            mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            called_args = mock_popen.call_args.args[0]
            self.assertEqual(called_args[0], "single-file")
            self.assertEqual(called_args[-2], "http://example.com")
            self.assertEqual(called_args[-1], self.temp_html_filepath)
            self.assertEqual(
                called_args.count("--browser-arg=--headless=new"),
                1,
            )
            self.assertEqual(
                called_args.count(f"--browser-arg=--user-data-dir={profile_dir}"),
                1,
            )
            self.assertEqual(
                called_args.count("--browser-arg=--no-sandbox"),
                1,
            )
            self.assertEqual(
                called_args.count(
                    "--browser-arg=--load-extension="
                    + os.path.join(settings.BASE_DIR, "uBOLite.chromium.mv3")
                ),
                1,
            )

    def test_relative_profile_dir_is_rejected(self):
        """相对路径会按 CWD 解析（正是故障成因），必须硬失败而不是降级。"""
        self.create_test_file()

        with (
            self.settings(LD_CHROMIUM_PROFILE_DIR="chromium-profile"),
            mock.patch("subprocess.Popen") as mock_popen,
            self.assertRaises(singlefile.SingleFileError) as ctx,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

        self.assertIn("absolute path", str(ctx.exception))
        self.assertIn("/etc/linkding/data/chromium-profile", str(ctx.exception))
        mock_popen.assert_not_called()

    def test_uncreatable_profile_dir_is_rejected(self):
        """目录建不出来（不可作为目录/无权限/只读）时要报出具体原因。"""
        self.create_test_file()

        # /dev/null/... 无法作为目录创建（NotADirectoryError）
        with (
            self.settings(LD_CHROMIUM_PROFILE_DIR="/dev/null/linkding-profile"),
            mock.patch("subprocess.Popen") as mock_popen,
            self.assertRaises(singlefile.SingleFileError) as ctx,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

        self.assertIn("Cannot create LD_CHROMIUM_PROFILE_DIR", str(ctx.exception))
        mock_popen.assert_not_called()

    def test_profile_dir_owned_by_another_user_is_rejected(self):
        """目录存在但被别的用户占住时，报错要带上 uid 与属主/权限。"""
        self.create_test_file()
        profile_dir = self.temp_profile_dir()

        with (
            self.settings(LD_CHROMIUM_PROFILE_DIR=profile_dir),
            mock.patch(
                "bookmarks.services.singlefile.os.access", return_value=False
            ),
            mock.patch(
                "bookmarks.services.singlefile._describe_owner",
                return_value=(0, "0o700"),
            ),
            mock.patch("subprocess.Popen") as mock_popen,
            self.assertRaises(singlefile.SingleFileError) as ctx,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

        message = str(ctx.exception)
        self.assertIn("not accessible", message)
        self.assertIn("owner uid=0 mode=0o700", message)
        self.assertIn("owned by another user", message)
        mock_popen.assert_not_called()

    def test_profile_dir_without_write_permission_is_rejected(self):
        """属主是自己但缺 w/x 位时，提示要指向权限而不是"别的用户"。"""
        self.create_test_file()
        profile_dir = self.temp_profile_dir()

        with (
            self.settings(LD_CHROMIUM_PROFILE_DIR=profile_dir),
            mock.patch(
                "bookmarks.services.singlefile.os.access", return_value=False
            ),
            mock.patch("subprocess.Popen") as mock_popen,
            self.assertRaises(singlefile.SingleFileError) as ctx,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

        message = str(ctx.exception)
        self.assertIn("mode does not grant", message)
        mock_popen.assert_not_called()

    @override_settings(
        LD_SINGLEFILE_UBLOCK_OPTIONS="--browser-arg=--headless=new "
        "--browser-arg=--user-data-dir=chromium-profile"
    )
    def test_env_user_data_dir_is_ignored(self):
        """环境变量里的相对 profile 路径必须被剔除，否则重新引入跨用户权限冲突。"""
        mock_process = self.successful_singlefile_process()
        self.create_test_file()
        profile_dir = self.temp_profile_dir()

        with (
            self.settings(LD_CHROMIUM_PROFILE_DIR=profile_dir),
            mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen,
        ):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

        profile_args = [
            arg
            for arg in mock_popen.call_args.args[0]
            if arg.startswith("--browser-arg=--user-data-dir")
        ]
        self.assertEqual(
            profile_args, [f"--browser-arg=--user-data-dir={profile_dir}"]
        )

    @override_settings(
        LD_SINGLEFILE_OPTIONS='--some-option "some value" --another-option "another value" --third-option="third value"'
    )
    def test_create_snapshot_custom_options(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
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

    def test_snapshot_processor_default_config_includes_migrated_args(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        from site_adapters.services.config.loader import _cache as adapter_cache

        adapter_cache.invalidate()
        try:
            with mock.patch(
                "bookmarks.services.singlefile.subprocess.Popen",
                return_value=mock_process,
            ) as mock_popen:
                from bookmarks.services.snapshot_processor import create_snapshot

                create_snapshot("http://example.com", self.temp_html_filepath)

                called_args = mock_popen.call_args.args[0]
                self.assertIn(
                    "--browser-arg=--disable-blink-features=AutomationControlled",
                    called_args,
                )
                # 全局 UA 不再注入快照：只有适配器显式配置的 UA 才允许出现
                self.assertFalse(
                    any(arg.startswith("--user-agent") for arg in called_args)
                )
                self.assertIn("--block-fonts", called_args)
        finally:
            adapter_cache.invalidate()

    def test_create_snapshot_default_timeout_setting(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            mock_process.communicate.assert_called_with(timeout=120)

    @override_settings(LD_SINGLEFILE_TIMEOUT_SEC=180)
    def test_create_snapshot_custom_timeout_setting(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process):
            singlefile.create_snapshot("http://example.com", self.temp_html_filepath)

            mock_process.communicate.assert_called_with(timeout=180)

    def test_custom_options_type_aware_bool_true(self):
        """Boolean True values should produce flag-only args."""
        result = singlefile.get_custom_options({"singlefile_args": {"--remove-hidden-elements": True}})
        self.assertIn("--remove-hidden-elements", result)

    def test_custom_options_type_aware_bool_false(self):
        """Boolean False values should be skipped."""
        result = singlefile.get_custom_options({"singlefile_args": {"--remove-hidden-elements": False}})
        self.assertNotIn("--remove-hidden-elements", result)

    def test_custom_options_type_aware_list(self):
        """List values should be expanded."""
        result = singlefile.get_custom_options({"singlefile_args": {"--http-header": ["X-A: 1", "X-B: 2"]}})
        self.assertIn("--http-header=X-A: 1", result)
        self.assertIn("--http-header=X-B: 2", result)

    def test_custom_options_type_aware_string(self):
        """String values should be key=value."""
        result = singlefile.get_custom_options({"singlefile_args": {"--user-agent": "CustomAgent"}})
        self.assertIn("--user-agent=CustomAgent", result)

    def test_singlefile_args_support_bool_and_numbers(self):
        self.assertEqual(
            singlefile.get_custom_options({
                "singlefile_args": {
                    "--remove-hidden-elements": True,
                    "--browser-wait-delay": 2000,
                    "--remove-frames": False,
                }
            }),
            ["--remove-hidden-elements", "--browser-wait-delay=2000"],
        )

    def test_create_snapshot_injects_request_url_headers_and_browser_script(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        config = {
            "_request_url": "https://fetch.example.com",
            "headers": {"User-Agent": "UA", "Accept-Language": "zh-CN"},
            "keep_elements": [".article"],
        }

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot("https://original.example.com", self.temp_html_filepath, config)

        called_args = mock_popen.call_args.args[0]
        self.assertEqual(called_args[-2], "https://fetch.example.com")
        self.assertIn("--user-agent=UA", called_args)
        self.assertIn("--http-header=Accept-Language: zh-CN", called_args)
        self.assertTrue(any(arg.startswith("--browser-script=") for arg in called_args))

    def test_create_snapshot_passes_browser_script_for_lazy_image_fix(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot(
                "https://example.com",
                self.temp_html_filepath,
                {"script": "/tmp/custom_snapshot.js"},
            )

        # Browser script is always passed for default lazy image fix
        self.assertTrue(any("--browser-script=" in arg for arg in mock_popen.call_args.args[0]))

    def test_create_snapshot_ignores_missing_cookie_config_file(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot(
                "https://example.com",
                self.temp_html_filepath,
                {"cookie": {"file": "/tmp/does-not-exist.json"}},
            )

        self.assertFalse(any(arg.startswith("--browser-cookies-file=") for arg in mock_popen.call_args.args[0]))

    def test_generated_browser_script_reads_vendor_file(self):
        script_path = singlefile._build_browser_script({
            "keep_elements": [".article"],
            "process_carousels": ["faceplate-carousel"],
        })
        self.addCleanup(lambda: os.path.exists(script_path) and os.remove(script_path))

        with open(script_path, encoding="utf-8") as f:
            script = f.read()

        self.assertIn("window.__linkding_cleanup_config", script)
        self.assertIn("single-file-on-before-capture-request", script)
        self.assertIn('"keep": [".article"]', script)
        self.assertIn("shadowRoot", script)
        self.assertIn("queryAll", script)
        self.assertIn("protectedNodes", script)
        self.assertIn('"carousels": ["faceplate-carousel"]', script)
        self.assertIn("ld-carousel", script)

    def _write_js(self, content):
        fd, path = tempfile.mkstemp(suffix=".js")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def _run_browser_script(self, script_path, html):
        repo_root = Path(__file__).resolve().parents[2]
        node_harness = r"""
const fs = require("fs");
const { parseHTML } = require(process.argv[3]);
const dom = parseHTML(process.argv[1]);
global.window = dom.window;
global.document = dom.document;
global.Node = dom.window.Node;
global.CustomEvent = dom.window.CustomEvent;
global.dispatchEvent = dom.window.dispatchEvent.bind(dom.window);
global.addEventListener = dom.window.addEventListener.bind(dom.window);
global.getComputedStyle = () => ({ whiteSpace: "normal" });
global.MutationObserver = class { observe(){} disconnect(){} };
eval(fs.readFileSync(process.argv[2], "utf8"));

(async () => {
  // If the cleanup script registered __linkdingCleanup (standalone mode),
  // call it directly. Otherwise dispatch the capture request event which
  // triggers the boilerplate's async handler.
  if (typeof window.__linkdingCleanup === "function") {
    await window.__linkdingCleanup();
  } else {
    window.dispatchEvent(new window.CustomEvent("single-file-on-before-capture-request"));
    // Wait for the async response event (standalone mode dispatches it)
    await new Promise(resolve => {
      addEventListener("single-file-on-before-capture-response", resolve, { once: true });
      window.dispatchEvent(new window.CustomEvent("single-file-on-before-capture-request"));
    });
  }
  const meta = document.querySelector("meta[name=linkding-cleanup-stats]");
  console.log(JSON.stringify({
    wrap: document.querySelectorAll(".interaction_bar__wrap").length,
    indicator: document.querySelectorAll(".swiper_indicator_wrp_pc").length,
    stats: meta && meta.getAttribute("content")
  }));
})();
"""
        result = subprocess.run(
            [
                "node",
                "-e",
                node_harness,
                html,
                script_path,
                str(repo_root / "node_modules" / "linkedom"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_remove_elements_works_without_keep_elements(self):
        script_path = singlefile._build_browser_script({
            "remove_elements": [".interaction_bar__wrap", ".swiper_indicator_wrp_pc"],
        })
        self.addCleanup(lambda: os.path.exists(script_path) and os.remove(script_path))

        result = self._run_browser_script(
            script_path,
            "<html><body>"
            '<div class="interaction_bar__wrap">x</div>'
            '<div class="swiper_indicator_wrp_pc">y</div>'
            "</body></html>",
        )

        self.assertEqual(result["wrap"], 0)
        self.assertEqual(result["indicator"], 0)
        self.assertIn('"removed":2', result["stats"])

    def test_read_builtin_engine_supports_singlefile_empty_and_null(self):
        singlefile_path = self._write_js('const builtin_engine = "singlefile";\n')
        empty_path = self._write_js("const builtin_engine = '';\n")
        null_path = self._write_js("const builtin_engine = null;\n")

        self.assertEqual(singlefile.read_builtin_engine(singlefile_path), "singlefile")
        self.assertEqual(singlefile.read_builtin_engine(empty_path), "")
        self.assertIsNone(singlefile.read_builtin_engine(null_path))
        self.assertTrue(singlefile.uses_builtin_engine(singlefile_path, "before"))
        self.assertFalse(singlefile.uses_builtin_engine(empty_path, "before"))

    def test_read_builtin_engine_rejects_unknown_value(self):
        path = self._write_js('const builtin_engine = "monolith";\n')

        with self.assertRaises(singlefile.SingleFileError):
            singlefile.uses_builtin_engine(path, "before")

    def test_build_browser_script_embeds_user_before_hook(self):
        before_path = self._write_js(
            'const builtin_engine = "singlefile";\n'
            "async function before(url, config) {\n"
            "  document.querySelectorAll('.collapsed').forEach((el) => el.classList.remove('collapsed'));\n"
            "}\n"
        )
        script_path = singlefile._build_browser_script(
            {
                "_browser_before_scripts": [before_path],
                "keep_elements": [".article"],
            },
            url="https://example.com",
        )
        self.addCleanup(lambda: os.path.exists(script_path) and os.remove(script_path))

        with open(script_path, encoding="utf-8") as f:
            script = f.read()

        self.assertIn("window.__linkdingHooks", script)
        self.assertIn("single-file-user-script-init", script)
        self.assertIn("single-file-on-before-capture-request", script)
        self.assertIn("single-file-on-before-capture-response", script)
        self.assertNotIn("single-file-on-after-capture-request", script)
        self.assertIn("el.classList.remove", script)
        self.assertIn('"url": "https://example.com"', script)
        self.assertLess(
            script.index("el.classList.remove"),
            script.index("single-file-user-script-init"),
        )
        self.assertLess(
            script.index("single-file-user-script-init"),
            script.index("window.__linkding_cleanup_config"),
        )

    def test_custom_options_passes_browser_script(self):
        result = singlefile.get_custom_options({
            "singlefile_args": {
                "--browser-script": "/tmp/custom.js",
                "--remove-hidden-elements": True,
            }
        })

        self.assertIn("--browser-script=/tmp/custom.js", result)
        self.assertIn("--remove-hidden-elements", result)

    def test_create_snapshot_passes_framework_and_custom_browser_scripts(self):
        mock_process = self.successful_singlefile_process()
        self.create_test_file()

        with mock.patch("subprocess.Popen", return_value=mock_process) as mock_popen:
            singlefile.create_snapshot(
                "https://example.com",
                self.temp_html_filepath,
                {"singlefile_args": {"--browser-script": "/tmp/raw-singlefile.js"}},
            )

        browser_scripts = [
            arg for arg in mock_popen.call_args.args[0]
            if arg.startswith("--browser-script=")
        ]
        self.assertEqual(len(browser_scripts), 2)
        self.assertIn("--browser-script=/tmp/raw-singlefile.js", browser_scripts)
