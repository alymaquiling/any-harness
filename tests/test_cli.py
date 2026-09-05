import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from any_harness.cli import main


class CLITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(map(str, args)))
        self.assertEqual(code, 0, stderr.getvalue())
        return stdout.getvalue()

    def test_documented_lifecycle_all_harnesses(self):
        self.run_cli("init", self.source, "--name", "test-plugin")
        self.run_cli("add", "agent", "reviewer", "--source", self.source, "--description", "Review changes")
        self.run_cli("validate", self.source)
        self.run_cli("build", self.source, "--out", self.root / "output")
        self.run_cli("build", self.source, "--out", self.root / "output", "--check")
        for harness in ("claude-code", "codex", "cursor", "opencode"):
            project = self.root / harness
            self.run_cli("install", self.source, "--harness", harness, "--project", project, "--dry-run")
            self.assertFalse(project.exists())
            self.run_cli("install", self.source, "--harness", harness, "--project", project)
            self.run_cli("uninstall", "test-plugin", "--harness", harness, "--project", project)

    def test_init_infers_name_and_build_validates(self):
        self.run_cli("init", self.source)
        self.assertIn("name: source", (self.source / "plugin.yaml").read_text())
        self.run_cli("build", self.source, "--out", self.root / "output")

    def test_init_infers_current_folder_name(self):
        self.source.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(self.source)
            self.run_cli("init", ".")
        finally:
            os.chdir(previous)
        self.assertIn("name: source", (self.source / "plugin.yaml").read_text())

    def test_invalid_inferred_name_does_not_create_destination(self):
        destination = self.root / "Invalid Name"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["init", str(destination)]), 1)
        self.assertFalse(destination.exists())
        self.run_cli("init", destination, "--name", "valid-name")

    def test_bundled_authoring_installs_to_each_native_scope(self):
        for harness in ("claude-code", "codex", "cursor", "opencode"):
            project = self.root / harness
            self.run_cli("authoring", "--harness", harness, "--project", project)
            skills = sorted(p.parent.name for p in project.rglob("SKILL.md"))
            self.assertEqual(skills, ["add-client-harness", "create-eval-suite", "create-portable-agent", "create-portable-plugin", "create-portable-skill"])

    def test_opencode_global_honors_xdg_and_uninstalls(self):
        config = self.root / "config"
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config)}):
            self.run_cli("authoring", "--harness", "opencode", "--global")
            self.assertTrue((config / "opencode/skills/create-portable-skill/SKILL.md").is_file())
            self.run_cli("uninstall", "authoring", "--harness", "opencode", "--global")
            self.assertFalse(list(config.rglob("SKILL.md")))


if __name__ == "__main__":
    unittest.main()
