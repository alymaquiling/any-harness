"""End-to-end tests for installing a plugin from an HTTPS Git repository.

The server in this module intentionally implements only static ("dumb") Git
HTTP.  That exercises the same ``git clone`` subprocess used by the CLI while
keeping the test self-contained and independent of a Git hosting service.
"""

import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _run(command, *, cwd=None, env=None):
    """Run a test subprocess with useful output when it fails."""
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


class _QuietHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _HTTPSGitServer:
    """Serve a bare repository over trusted, loopback-only HTTPS."""

    def __init__(self, document_root, certificate, private_key):
        handler = functools.partial(
            _QuietHTTPRequestHandler,
            directory=str(Path(document_root)),
        )
        self.server = _ThreadingHTTPServer(("127.0.0.1", 0), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=str(certificate),
            keyfile=str(private_key),
        )
        self.server.socket = context.wrap_socket(
            self.server.socket,
            server_side=True,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="any-harness-git-http",
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address[:2]
        return f"https://{host}:{port}/repo.git"

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@unittest.skipUnless(
    shutil.which("git") and shutil.which("openssl"),
    "git and openssl are required for HTTPS Git source tests",
)
class GitInstallE2ETest(unittest.TestCase):
    """Exercise the public CLI, Git transport, receipt, update, and removal."""

    PLUGIN_NAME = "git-e2e"
    SKILL_NAME = "hello"
    AGENT_NAME = "reviewer"
    HARNESSES = ("claude-code", "codex", "opencode", "cursor")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-git-e2e-")
        self.workspace = Path(self.temp.name).resolve()
        self.source = self.workspace / "source"
        self.bare = self.workspace / "repo.git"
        self._write_plugin(version="1.0.0", body_suffix="initial")
        self._init_repository()
        self.certificate, self.private_key = self._make_certificate()

    def tearDown(self):
        self.temp.cleanup()

    def _write_plugin(self, *, version, body_suffix):
        skill = self.source / "packages" / self.PLUGIN_NAME / "skills" / self.SKILL_NAME
        agents = self.source / "packages" / self.PLUGIN_NAME / "agents"
        skill.mkdir(parents=True, exist_ok=True)
        agents.mkdir(parents=True, exist_ok=True)
        plugin = skill.parents[1]
        (plugin / "plugin.yaml").write_text(
            "\n".join(
                (
                    "schema: 1",
                    f"name: {self.PLUGIN_NAME}",
                    f"version: {version}",
                    "description: An HTTPS Git installation test plugin",
                    "author:",
                    "  name: Any Harness Test",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (skill / "SKILL.md").write_text(
            "\n".join(
                (
                    "---",
                    f"name: {self.SKILL_NAME}",
                    "description: A skill served by HTTPS Git",
                    "---",
                    "",
                    f"Run the {body_suffix} skill workflow.",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (skill / "references" / "guide.md").parent.mkdir(parents=True, exist_ok=True)
        (skill / "references" / "guide.md").write_text(
            f"Reference for the {body_suffix} revision.\n",
            encoding="utf-8",
        )
        (agents / f"{self.AGENT_NAME}.md").write_text(
            "\n".join(
                (
                    "---",
                    f"name: {self.AGENT_NAME}",
                    "description: An agent served by HTTPS Git",
                    "---",
                    "",
                    f"Review the {body_suffix} changes.",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def _git(self, *args, cwd=None):
        return _run(
            ["git", *map(str, args)],
            cwd=cwd,
            env=self._git_environment(),
        )

    def _git_environment(self):
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        return environment

    def _init_repository(self):
        self._git("init", "--quiet", self.source)
        self._git("-C", self.source, "config", "user.name", "Any Harness Test")
        self._git(
            "-C",
            self.source,
            "config",
            "user.email",
            "any-harness@example.invalid",
        )
        self._git("-C", self.source, "add", ".")
        self._git("-C", self.source, "commit", "--quiet", "-m", "initial")
        self.initial_commit = self._git(
            "-C", self.source, "rev-parse", "HEAD"
        ).stdout.strip()
        self.branch = self._git(
            "-C", self.source, "symbolic-ref", "--short", "HEAD"
        ).stdout.strip()
        self._git("-C", self.source, "tag", "v1.0.0")
        self._git("clone", "--bare", "--quiet", self.source, self.bare)
        self._git("--git-dir", self.bare, "update-server-info")

    def _make_certificate(self):
        config = self.workspace / "openssl.cnf"
        certificate = self.workspace / "localhost-cert.pem"
        private_key = self.workspace / "localhost-key.pem"
        config.write_text(
            "\n".join(
                (
                    "[req]",
                    "distinguished_name = req_distinguished_name",
                    "x509_extensions = v3_req",
                    "prompt = no",
                    "[req_distinguished_name]",
                    "CN = localhost",
                    "[v3_req]",
                    "subjectAltName = @alt_names",
                    "basicConstraints = critical,CA:TRUE",
                    "keyUsage = critical,keyCertSign,digitalSignature,keyEncipherment",
                    "[alt_names]",
                    "DNS.1 = localhost",
                    "IP.1 = 127.0.0.1",
                    "",
                )
            ),
            encoding="utf-8",
        )
        _run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-sha256",
                "-keyout",
                str(private_key),
                "-out",
                str(certificate),
                "-days",
                "1",
                "-config",
                str(config),
                "-extensions",
                "v3_req",
            ]
        )
        return certificate, private_key

    def _cli_environment(self):
        environment = self._git_environment()
        environment.update(
            {
                "GIT_SSL_CAINFO": str(self.certificate),
                "GIT_SSL_NO_VERIFY": "false",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        return environment

    def _run_cli(self, *args):
        result = _run(
            [sys.executable, "-m", "any_harness", *map(str, args)],
            cwd=REPOSITORY_ROOT,
            env=self._cli_environment(),
        )
        return result

    def _publish_update(self):
        self._write_plugin(version="1.1.0", body_suffix="updated")
        self._git("-C", self.source, "add", ".")
        self._git("-C", self.source, "commit", "--quiet", "-m", "updated")
        self.updated_commit = self._git(
            "-C", self.source, "rev-parse", "HEAD"
        ).stdout.strip()
        self._git(
            "-C",
            self.source,
            "push",
            "--quiet",
            str(self.bare),
            f"HEAD:refs/heads/{self.branch}",
            "--tags",
        )
        self._git("-C", self.source, "tag", "v1.1.0")
        self._git(
            "-C",
            self.source,
            "push",
            "--quiet",
            str(self.bare),
            "v1.1.0",
        )
        self._git("--git-dir", self.bare, "update-server-info")

    def _expected_paths(self, harness):
        project_root = {
            "claude-code": ".claude",
            "codex": ".agents",
            "opencode": ".opencode",
            "cursor": ".cursor",
        }[harness]
        paths = {
            f"{project_root}/skills/{self.SKILL_NAME}/SKILL.md",
            f"{project_root}/skills/{self.SKILL_NAME}/references/guide.md",
        }
        if harness == "codex":
            paths.add(f".codex/agents/{self.AGENT_NAME}.toml")
        else:
            paths.add(f"{project_root}/agents/{self.AGENT_NAME}.md")
        return paths

    def test_https_git_install_update_receipt_and_uninstall_all_harnesses(self):
        with _HTTPSGitServer(self.workspace, self.certificate, self.private_key) as url:
            git_source = "git+" + url
            for harness in self.HARNESSES:
                project = self.workspace / harness
                first = self._run_cli(
                    "install",
                    git_source,
                    "--ref",
                    "v1.0.0",
                    "--subdir",
                    f"packages/{self.PLUGIN_NAME}",
                    "--harness",
                    harness,
                    "--project",
                    project,
                )
                self.assertIn("Done:", first.stdout)
                expected = self._expected_paths(harness)
                for relative in expected:
                    self.assertTrue((project / relative).is_file(), relative)
                skill = project / next(
                    relative for relative in expected if relative.endswith("SKILL.md")
                )
                self.assertIn(b"initial skill workflow", skill.read_bytes())
                receipt_path = (
                    project
                    / ".any-harness"
                    / "installed"
                    / f"{harness}-{self.PLUGIN_NAME}.json"
                )
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual(receipt["source"]["commit"], self.initial_commit)
                self.assertEqual(
                    receipt["source"]["subdir"], f"packages/{self.PLUGIN_NAME}"
                )

            self._publish_update()
            for harness in self.HARNESSES:
                project = self.workspace / harness
                second = self._run_cli(
                    "install",
                    git_source,
                    "--ref",
                    "v1.1.0",
                    "--subdir",
                    f"packages/{self.PLUGIN_NAME}",
                    "--harness",
                    harness,
                    "--project",
                    project,
                )
                self.assertIn("Done:", second.stdout)
                expected = self._expected_paths(harness)
                skill = project / next(
                    relative for relative in expected if relative.endswith("SKILL.md")
                )
                self.assertIn(b"updated skill workflow", skill.read_bytes())
                receipt_path = (
                    project
                    / ".any-harness"
                    / "installed"
                    / f"{harness}-{self.PLUGIN_NAME}.json"
                )
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual(receipt["source"]["commit"], self.updated_commit)
                self.assertNotEqual(
                    receipt["source"]["commit"], self.initial_commit
                )

            for harness in self.HARNESSES:
                project = self.workspace / harness
                removed = self._run_cli(
                    "uninstall",
                    self.PLUGIN_NAME,
                    "--harness",
                    harness,
                    "--project",
                    project,
                )
                self.assertIn("Done:", removed.stdout)
                for relative in self._expected_paths(harness):
                    self.assertFalse((project / relative).exists(), relative)
                self.assertFalse(
                    (
                        project
                        / ".any-harness"
                        / "installed"
                        / f"{harness}-{self.PLUGIN_NAME}.json"
                    ).exists()
                )


if __name__ == "__main__":
    unittest.main()
