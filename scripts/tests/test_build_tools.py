import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "image_size", ROOT / "scripts/verify-docker-image.py"
)
image_size = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_size)


class BuildToolsTest(unittest.TestCase):
    def test_growth_thresholds(self):
        for before, after, expected in [
            (600_000_000, 520_000_000, "pass"),
            (600_000_000, 609_000_000, "warning"),
            (600_000_000, 619_000_000, "fail"),
            (30_000_000, 35_000_000, "pass"),
            (30_000_000, 40_000_001, "fail"),
        ]:
            with self.subTest(before=before, after=after):
                self.assertEqual(image_size.growth_status(before, after), expected)

    def test_oci_counts_one_architecture_and_excludes_attestations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def blob(data):
                content = json.dumps(data).encode()
                digest = hashlib.sha256(content).hexdigest()
                target = root / "blobs/sha256" / digest
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                return "sha256:" + digest

            config = blob(
                {
                    "os": "linux",
                    "architecture": "amd64",
                    "config": {},
                    "history": [
                        {"created_by": "COPY app"},
                        {"empty_layer": True},
                        {"created_by": "RUN build"},
                    ],
                }
            )
            manifest = blob(
                {"config": {"digest": config}, "layers": [{"size": 17}, {"size": 23}]}
            )
            (root / "index.json").write_text(
                json.dumps(
                    {
                        "manifests": [
                            {
                                "platform": {
                                    "os": "unknown",
                                    "architecture": "unknown",
                                },
                                "digest": "attestation-not-read",
                            },
                            {
                                "platform": {"os": "linux", "architecture": "amd64"},
                                "digest": manifest,
                            },
                        ]
                    }
                )
            )
            self.assertEqual(
                image_size.read_image(str(root), "linux/amd64")["compressed_bytes"], 40
            )
            output = root / "result.json"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify-docker-image.py"),
                    "size",
                    str(root),
                    "--platform",
                    "linux/amd64",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                json.loads(output.read_text())["current"]["compressed_bytes"], 40
            )
            with self.assertRaises(ValueError):
                image_size.read_image(str(root), "linux/arm64")

    def test_build_script_preserves_variants_and_requires_explicit_push(self):
        with tempfile.TemporaryDirectory() as directory:
            docker = Path(directory) / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\nimport json,sys\n"
                "print('arm64' if sys.argv[1]=='version' else json.dumps(sys.argv[1:]))\n"
            )
            docker.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"])
            cases = [
                ([], 0),
                (["--base"], 0),
                (["--base", "--alpine"], 0),
                (["--no-mirror", "--base"], 0),
                (["--alpine"], 2),
                (["--platform", "linux/amd64,linux/arm64"], 2),
                (
                    [
                        "--push",
                        "--repository",
                        "example/app",
                        "--repository",
                        "ghcr.io/example/app",
                    ],
                    0,
                ),
            ]
            for args, expected in cases:
                with self.subTest(args=args):
                    result = subprocess.run(
                        ["bash", str(ROOT / "scripts/build-docker.sh"), *args],
                        env=env,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected == 0:
                        command = json.loads(result.stdout)
                        self.assertEqual("--push" in command, "--push" in args)
                        self.assertEqual(
                            "--no-cache-filter" in command, "--base" not in args
                        )
                        if "--base" not in args:
                            self.assertEqual(
                                command[command.index("--no-cache-filter") + 1],
                                "ublock-build",
                            )
                        if "--base" in args:
                            self.assertEqual(
                                command[command.index("--target") + 1], "linkding"
                            )
                        if "--alpine" in args:
                            self.assertNotIn("woohoodai/linkding-cn:latest", command)
                            self.assertIn(
                                "woohoodai/linkding-cn:latest-base-alpine", command
                            )


if __name__ == "__main__":
    unittest.main()
