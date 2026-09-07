#!/usr/bin/env python3
"""Verify Docker images: test runtime behavior or compare compressed OCI size."""

import argparse
import base64
import http.server
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path


def read_image(source, platform):
    """Read a registry reference, OCI archive, or layout without pulling layers."""
    with ExitStack() as stack:
        path = Path(source)
        descriptor = None
        if path.is_file():
            archive = stack.enter_context(tarfile.open(path))

            def read(name):
                return json.load(archive.extractfile(name))

            def blob(digest):
                return read("blobs/" + digest.replace(":", "/"))

            top = read("index.json")
        elif path.is_dir():

            def read(name):
                return json.loads((path / name).read_text())

            def blob(digest):
                return read("blobs/" + digest.replace(":", "/"))

            top = read("index.json")
        else:

            def remote(ref):
                return json.loads(
                    subprocess.check_output(
                        ["docker", "buildx", "imagetools", "inspect", "--raw", ref],
                        text=True,
                    )
                )

            def blob(digest):
                return remote(source.split("@")[0] + "@" + digest)

            top = remote(source)

        manifest = top
        while "manifests" in manifest:
            candidates = [
                item
                for item in manifest["manifests"]
                if "/".join(
                    item.get("platform", {}).get(k, "") for k in ("os", "architecture")
                )
                == platform
            ]
            # Some single-platform OCI exporters omit the outer platform field.
            if not candidates and len(manifest["manifests"]) == 1:
                candidates = manifest["manifests"]
            if len(candidates) != 1:
                raise ValueError(f"{source}: expected exactly one image for {platform}")
            descriptor = candidates[0]
            manifest = blob(descriptor["digest"])

        if path.exists():
            config = blob(manifest["config"]["digest"])
        else:
            reference = (
                source.split("@")[0] + "@" + descriptor["digest"]
                if descriptor
                else source
            )
            config = json.loads(
                subprocess.check_output(
                    [
                        "docker",
                        "buildx",
                        "imagetools",
                        "inspect",
                        "--format",
                        "{{json .Image}}",
                        reference,
                    ],
                    text=True,
                )
            )
        actual = config["os"] + "/" + config["architecture"]
        if actual != platform:
            raise ValueError(f"{source}: requested {platform}, got {actual}")
        history = [h for h in config.get("history", []) if not h.get("empty_layer")]
        if len(history) != len(manifest["layers"]):
            raise ValueError("Image history and layer count differ")
        labels = config.get("config", {}).get("Labels") or {}
        return {
            "source": source,
            "platform": actual,
            "manifest_digest": descriptor["digest"] if descriptor else None,
            "config_digest": manifest["config"]["digest"],
            "compressed_bytes": sum(layer["size"] for layer in manifest["layers"]),
            "version": labels.get("org.opencontainers.image.version"),
            "revision": labels.get("org.opencontainers.image.revision"),
            "variant": labels.get("io.github.woohoodai.linkding.variant"),
            "layers": [
                {**layer, "command": hist.get("created_by", "")}
                for layer, hist in zip(manifest["layers"], history, strict=True)
            ],
        }


def growth_status(before, after):
    growth = after - before
    if growth > max(10_000_000, before * 0.03):
        return "fail"
    if growth > max(5_000_000, before * 0.01):
        return "warning"
    return "pass"


def check_size(args):
    current = read_image(args.image, args.platform)
    result = {"current": current, "status": "pass"}
    changed_layers = current["layers"]
    if args.baseline:
        before = read_image(args.baseline, args.platform)
        if (
            before["variant"]
            and current["variant"]
            and before["variant"] != current["variant"]
        ):
            raise ValueError("Cannot compare different image variants")
        result.update(
            baseline=before,
            delta_bytes=current["compressed_bytes"] - before["compressed_bytes"],
            status=growth_status(
                before["compressed_bytes"], current["compressed_bytes"]
            ),
        )
        old_digests = {layer["digest"] for layer in before["layers"]}
        changed_layers = [
            layer for layer in current["layers"] if layer["digest"] not in old_digests
        ]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"{args.platform}: {current['compressed_bytes'] / 1e6:.2f} MB compressed")
    if "delta_bytes" in result:
        print(f"Change: {result['delta_bytes'] / 1e6:+.2f} MB ({result['status']})")
    print("Largest new/changed layers:" if args.baseline else "Largest layers:")
    for layer in sorted(changed_layers, key=lambda item: item["size"], reverse=True)[
        :5
    ]:
        print(f"  {layer['size'] / 1e6:7.2f} MB  {layer['command'][:120]}")
    if result["status"] == "fail":
        raise SystemExit(
            "Image growth exceeds max(10 MB, 3%); inspect the layers before release"
        )


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *args], text=True, **kwargs).strip()


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (subprocess.CalledProcessError, OSError):
            pass
        time.sleep(1)
    raise TimeoutError("Container did not become ready")


def test_image(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    name = "linkding-image-test-" + uuid.uuid4().hex[:10]
    network = name + "-net"
    postgres = name + "-postgres"
    containers = []
    network_created = False
    try:
        docker("network", "create", "--internal", network)
        network_created = True
        env = [
            "-e",
            "LD_SUPERUSER_NAME=image-audit",
            "-e",
            "LD_SUPERUSER_PASSWORD=" + uuid.uuid4().hex,
            "-e",
            "LD_SERVER_PORT=19090",
            "-e",
            "LD_SERVER_HOST=0.0.0.0",
            "-e",
            "LD_CONTEXT_PATH=" + args.context_path,
        ]
        if args.postgres:
            docker(
                "run",
                "-d",
                "--pull=never",
                "--name",
                postgres,
                "--network",
                network,
                "--tmpfs",
                "/var/lib/postgresql/data",
                "-e",
                "POSTGRES_DB=linkding",
                "-e",
                "POSTGRES_USER=linkding",
                "-e",
                "POSTGRES_PASSWORD=image-test-only",
                args.postgres,
            )
            containers.append(postgres)
            wait_for(
                lambda: (
                    "accepting connections"
                    in docker(
                        "exec",
                        postgres,
                        "pg_isready",
                        "-U",
                        "linkding",
                        stderr=subprocess.DEVNULL,
                    )
                )
            )
            env += [
                "-e",
                "LD_DB_ENGINE=postgres",
                "-e",
                "LD_DB_HOST=" + postgres,
                "-e",
                "LD_DB_PASSWORD=image-test-only",
            ]
        # Seed an empty subscription list so tests never contact external websites.
        bootstrap = (
            "mkdir -p data/site_adapters; "
            "printf '%s' '{\"_adapters\":[]}' > data/site_adapters/config.jsonc; "
            "exec ./bootstrap.sh"
        )
        docker(
            "run",
            "-d",
            "--pull=never",
            "--platform",
            args.platform,
            "--name",
            name,
            "--network",
            network,
            "--shm-size=256m",
            *env,
            "--entrypoint",
            "/bin/sh",
            args.image,
            "-c",
            bootstrap,
        )
        containers.append(name)
        base_url = "http://127.0.0.1:19090/" + args.context_path

        def health():
            docker(
                "exec",
                name,
                "curl",
                "-fsS",
                "--max-time",
                "3",
                base_url + "health",
                stderr=subprocess.DEVNULL,
            )
            return True

        wait_for(health)
        wait_for(
            lambda: (
                "RUNNING"
                in docker(
                    "exec", name, "supervisorctl", "-c", "supervisord.conf", "status"
                )
            )
        )
        smoke = Path(__file__).read_text()
        inner_args = ["test", "--inside-container"]
        if args.base:
            inner_args.append("--base")
        if args.strict:
            inner_args.append("--strict")
        output = docker(
            "exec",
            "-i",
            "-u",
            "www-data",
            name,
            "python",
            "-",
            *inner_args,
            input=smoke,
        )
        result = json.loads(output.splitlines()[-1])
        result.update(
            image=args.image,
            platform=args.platform,
            context_path=args.context_path,
            server_port=19090,
            http_assets=True,
            background_worker=True,
        )
        # Execute the healthcheck configured by the image, including its environment substitutions.
        healthcheck = json.loads(docker("inspect", name))[0]["Config"]["Healthcheck"][
            "Test"
        ]
        assert healthcheck[0] == "CMD-SHELL"
        docker("exec", name, "/bin/sh", "-c", healthcheck[1])
        result["configured_healthcheck"] = True
        (args.output_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        if not args.base:
            docker(
                "cp",
                name + ":/tmp/image-smoke.png",
                str(args.output_dir / "browser.png"),
            )
            docker(
                "cp",
                name + ":/tmp/image-ui-smoke.png",
                str(args.output_dir / "application.png"),
            )
        inventory = subprocess.run(
            ["docker", "exec", name, "cat", "/usr/share/linkding/os-packages.tsv"],
            text=True,
            capture_output=True,
        )
        if inventory.returncode == 0:
            (args.output_dir / "os-packages.tsv").write_text(inventory.stdout)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "python_packages"},
                ensure_ascii=False,
            )
        )
    finally:
        if name in containers:
            logs = subprocess.run(
                ["docker", "logs", name], text=True, capture_output=True
            )
            (args.output_dir / "container.log").write_text(logs.stdout + logs.stderr)
        for container in reversed(containers):
            subprocess.run(
                ["docker", "rm", "-fv", container],
                stdout=subprocess.DEVNULL,
                check=False,
            )
        if network_created:
            subprocess.run(
                ["docker", "network", "rm", network],
                stdout=subprocess.DEVNULL,
                check=False,
            )


def run_in_container(args):
    def run(command, data=None, timeout=60):
        result = subprocess.run(
            command, input=data, text=True, capture_output=True, timeout=timeout
        )
        assert result.returncode == 0, (command, result.stdout, result.stderr)
        return result.stdout

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmarks.settings.prod")
    import django

    django.setup()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.test import Client
    from django.urls import reverse
    from django.utils.translation import gettext, override

    result = {
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "node": run(["node", "--version"]).strip(),
        "python_packages": sorted(
            (d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
        ),
        "checks": [],
    }
    node_arch = run(["node", "-p", "process.arch"]).strip()
    assert node_arch == {"aarch64": "arm64", "x86_64": "x64"}[platform.machine()]
    import psycopg

    assert psycopg.pq.__impl__ == "c"
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
        if connection.vendor == "sqlite":
            assert settings.USE_SQLITE_ICU_EXTENSION
            cursor.execute("SELECT lower('Ä'), 'Ä' LIKE 'ä'")
            assert cursor.fetchone() == ("ä", 1)
            cursor.execute("SELECT '中文' COLLATE ICU")
            assert cursor.fetchone() == ("中文",)
    result["database"] = connection.vendor
    result["checks"].append("database-and-icu")

    with override("zh-hans"):
        assert gettext("Login") == "登录"
    client = Client()
    client.cookies["ld_language"] = "zh-hans"
    assert "登录" in client.get(reverse("login")).content.decode()
    client.force_login(get_user_model().objects.get(username="image-audit"))
    for name in (
        "linkding:bookmarks.index",
        "linkding:settings.adapters",
        "linkding:settings.site_adapters",
    ):
        assert client.get(reverse(name)).status_code == 200, name
    assert (
        client.get(
            reverse("linkding:bookmarks.index"), {"sort": "title_asc"}
        ).status_code
        == 200
    )
    result["checks"].append("translations-and-authenticated-pages")

    base_url = (
        "http://127.0.0.1:"
        + os.environ["LD_SERVER_PORT"]
        + "/"
        + os.environ.get("LD_CONTEXT_PATH", "")
    )
    for asset in (
        "bundle.js",
        "theme-light.css",
        "theme-dark.css",
        "site-adapters.js",
        "adapters.js",
        "site-adapters.css",
    ):
        with urllib.request.urlopen(
            base_url + "static/" + asset, timeout=10
        ) as response:
            assert response.status == 200 and len(response.read()) > 100, asset
    with urllib.request.urlopen(base_url + "login/", timeout=10) as response:
        assert response.status == 200
    result["checks"].append("http-and-static-assets")

    run(
        [
            "node",
            "-e",
            """const a=require('node:assert/strict');
    a.throws(()=>require.resolve('esbuild'));
    a.throws(()=>require.resolve('postcss'));
    a.equal(require('linkedom').parseHTML('<p>ok</p>').document.querySelector('p').textContent,'ok');
    require('playwright-core');""",
        ]
    )
    if args.strict:
        assert shutil.which("npm") is None
        assert shutil.which("gcc") is None
        assert shutil.which("msgfmt") is None
        assert not Path("/usr/include/node").exists()
        assert not Path("bookmarks/tests").exists()
        assert not Path("bookmarks/frontend").exists()
        assert not Path("site_adapters/styles").exists()
    result["checks"].append("runtime-dependencies")

    article = (
        "<p>这是镜像优化后的中文正文测试，用于验证阅读提取、字体显示以及完整网页快照。</p>"
        * 12
    )
    html = (
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>镜像回归测试</title>
    <style>body{font-family:sans-serif}#canvas{border:1px solid}</style></head><body>
    <article><h1>镜像回归测试</h1>"""
        + article
        + """</article><div class="ad">remove-me</div>
    <svg width="50" height="50"><circle cx="25" cy="25" r="20" fill="green"/></svg>
    <img id="pixel" src="/pixel.png"><canvas id="canvas" width="50" height="50"></canvas>
    <script>let c=document.querySelector('canvas').getContext('2d');c.fillStyle='red';c.fillRect(0,0,50,50);</script>
    </body></html>"""
    )
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII="
    )

    class FixtureHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            data = pixel if self.path == "/pixel.png" else html.encode()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "image/png"
                if self.path == "/pixel.png"
                else "text/html; charset=utf-8",
            )
            self.send_header("Set-Cookie", "image_smoke=ok; Path=/; SameSite=Lax")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    with tempfile.TemporaryDirectory(prefix="linkding-image-") as folder:
        folder = Path(folder)
        source = folder / "article.html"
        source.write_text(html)
        parsed = json.loads(
            run(
                ["node", "site_adapters/services/engine/scripts/defuddle_parse.js"],
                json.dumps(
                    {
                        "htmlPath": str(source),
                        "url": "https://example.invalid/article",
                        "options": {"contentSelector": "article"},
                    }
                ),
            )
        )
        assert "中文正文" in parsed["content"]
        result["checks"].append("defuddle-reader")
        if args.base:
            assert not settings.LD_ENABLE_SNAPSHOTS
            assert importlib.util.find_spec("playwright") is None
            assert shutil.which("chromium") is None
        if not args.base:
            import playwright

            assert not (Path(playwright.__file__).parent / "driver/node").exists()
            assert os.environ["PLAYWRIGHT_NODEJS_PATH"] == shutil.which("node")
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/"
            try:
                from site_adapters.services.engine.browser_provider import (
                    launch_browser,
                )

                browser = launch_browser()
                page = browser.new_page()
                page.goto(url, wait_until="networkidle")
                assert page.title() == "镜像回归测试"
                assert page.locator("#pixel").evaluate(
                    "(img)=>img.complete && img.naturalWidth===1"
                )
                assert (
                    page.locator("#canvas").evaluate(
                        "(c)=>c.getContext('2d').getImageData(0,0,1,1).data[0]"
                    )
                    == 255
                )
                page.screenshot(path="/tmp/image-smoke.png", full_page=True)
                result["chromium"] = browser.version
                if args.strict:
                    result["chinese_font"] = run(["fc-match", ":lang=zh-cn"]).strip()
                    assert (
                        "Zen Hei" in result["chinese_font"]
                        or "Noto" in result["chinese_font"]
                    )
                page.context.add_cookies(
                    [
                        {"name": name, "value": cookie.value, "url": base_url}
                        for name, cookie in client.cookies.items()
                    ]
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for route in ("bookmarks", "settings/adapters", "admin/site-adapters"):
                    response = page.goto(base_url + route)
                    assert response.status == 200
                    page.locator("#main-heading").wait_for(state="visible")
                page.locator("#btn-add-subscription").click()
                page.locator("#sub-modal-overlay").wait_for(state="visible")
                page.screenshot(path="/tmp/image-ui-smoke.png", full_page=True)
                assert not errors, errors
                result["checks"].append("authenticated-ui-and-adapter-javascript")
                browser.close()
                result["checks"].append("python-browser-images-canvas-screenshot")

                snapshot = folder / "snapshot.html"
                run(
                    ["node", "site_adapters/services/engine/scripts/snapshot.js"],
                    json.dumps(
                        {
                            "url": url,
                            "outputPath": str(snapshot),
                            "cleanup": {"remove": [".ad"]},
                        }
                    ),
                )
                assert (
                    "中文正文" in snapshot.read_text()
                    and "remove-me" not in snapshot.read_text()
                )
                hook = folder / "after.js"
                hook.write_text(
                    "function after(){document.querySelector('article').setAttribute('data-audit','passed')}"
                )
                run(
                    [
                        "node",
                        "site_adapters/services/engine/scripts/snapshot_browser_after.js",
                    ],
                    json.dumps(
                        {
                            "scriptPath": str(hook),
                            "url": url,
                            "config": {},
                            "outputPath": str(snapshot),
                        }
                    ),
                )
                assert 'data-audit="passed"' in snapshot.read_text()
                result["checks"].append("js-snapshot-and-after-hook")

                cookies = folder / "cookies.json"
                run(
                    [
                        "node",
                        "site_adapters/services/engine/scripts/refresh_cookies.js",
                    ],
                    json.dumps(
                        {
                            "url": url,
                            "cookie_file": str(cookies),
                            "wait_cookie": "image_smoke",
                            "timeout": 15000,
                        }
                    ),
                )
                assert any(
                    c["name"] == "image_smoke" and c["value"] == "ok"
                    for c in json.loads(cookies.read_text())
                )
                result["checks"].append("cookie-refresh")

                from bookmarks.services.singlefile import create_snapshot

                single = folder / "single.html"
                create_snapshot(
                    url,
                    str(single),
                    {
                        "singlefile_args": {
                            "--browser-executable-path": "/usr/bin/chromium"
                        },
                    },
                )
                assert "中文正文" in single.read_text()
                assert "data:image/" in single.read_text()
                result["checks"].append("singlefile-snapshot")

                from playwright.sync_api import sync_playwright

                extension = str(Path("uBOLite.chromium.mv3").resolve())
                assert os.access(extension, os.R_OK) and os.access(
                    "chromium-profile", os.W_OK
                )
                with sync_playwright() as pw:
                    context = pw.chromium.launch_persistent_context(
                        str(folder / "extension-profile"),
                        executable_path="/usr/bin/chromium",
                        headless=True,
                        args=[
                            "--no-sandbox",
                            f"--disable-extensions-except={extension}",
                            f"--load-extension={extension}",
                        ],
                    )
                    worker = (
                        context.service_workers[0]
                        if context.service_workers
                        else context.wait_for_event("serviceworker", timeout=20000)
                    )
                    manifest = worker.evaluate("chrome.runtime.getManifest()")
                    assert "uBlock" in manifest["name"]
                    result["ublock"] = manifest["version"]
                    context.close()
                result["checks"].append("ublock-load-as-www-data")
            finally:
                server.shutdown()
                server.server_close()

    Path("/tmp/image-smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    test = commands.add_parser(
        "test", help="Boot disposable containers and run image regressions"
    )
    test.add_argument("image", nargs="?")
    test.add_argument("--platform", default="linux/amd64")
    test.add_argument("--base", action="store_true")
    test.add_argument("--strict", action="store_true")
    test.add_argument(
        "--postgres", help="Local PostgreSQL image, for example postgres:16-alpine"
    )
    test.add_argument("--context-path", default="audit/")
    test.add_argument("--output-dir", type=Path, default=Path("tmp/image-test"))
    test.add_argument("--inside-container", action="store_true", help=argparse.SUPPRESS)
    size = commands.add_parser(
        "size", help="Compare compressed layers without starting a container"
    )
    size.add_argument("image", help="Registry reference, OCI archive, or OCI layout")
    size.add_argument("--platform", default="linux/amd64")
    size.add_argument("--baseline")
    size.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "size":
        check_size(args)
    elif args.inside_container:
        run_in_container(args)
    elif not args.image:
        test.error("image is required")
    else:
        test_image(args)


if __name__ == "__main__":
    main()
