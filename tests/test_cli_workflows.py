"""Regression coverage for the Typer workflows and local CLI defaults."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import click

from any_harness.cli import main
from any_harness.config import find_config, load_config, save_config
from any_harness import eval as evaluation


class CliWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-cli-workflows-")
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    @contextlib.contextmanager
    def cwd(self, path):
        previous = Path.cwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(previous)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_setup_preflights_all_values_before_scaffolding(self):
        source = self.root / "new-plugin"
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke(
                "setup",
                source,
                "--no-input",
                "--harness",
                "codex",
                "--out",
                "",
            )
        self.assertEqual(code, 1)
        self.assertIn("out", stderr)
        self.assertFalse(source.exists())
        self.assertFalse((self.root / ".any-harness").exists())

    def test_setup_saves_defaults_used_by_build_and_explicit_values_win(self):
        source = self.root / "plugin"
        generated = self.root / "generated"
        project = self.root / "target"
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke(
                "setup",
                source,
                "--no-input",
                "--harness",
                "codex",
                "--project",
                project,
                "--out",
                generated,
            )
            self.assertEqual(code, 0, stderr)
            config = load_config(self.root / ".any-harness/config.yaml")
            self.assertEqual(config.source, "plugin")
            self.assertEqual(config.harnesses, ("codex",))
            self.assertEqual(config.project, "target")
            self.assertEqual(config.out, "generated")

            code, _stdout, stderr = self.invoke("build")
            self.assertEqual(code, 0, stderr)
            self.assertTrue((generated / "codex").is_dir())
            self.assertFalse((generated / "claude-code").exists())

            override = self.root / "override"
            code, _stdout, stderr = self.invoke(
                "build",
                source,
                "--out",
                override,
                "--harness",
                "cursor",
            )
            self.assertEqual(code, 0, stderr)
            self.assertTrue((override / "cursor").is_dir())
            self.assertFalse((override / "codex").exists())

    def test_saved_relative_paths_work_from_a_child_directory(self):
        source = self.root / "plugin"
        child = self.root / "child"
        output = self.root / "dist"
        child.mkdir()
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke(
                "setup",
                source,
                "--no-input",
                "--harness",
                "codex",
                "--out",
                output,
            )
            self.assertEqual(code, 0, stderr)
        with self.cwd(child):
            code, _stdout, stderr = self.invoke("build")
        self.assertEqual(code, 0, stderr)
        self.assertTrue((output / "codex").is_dir())

    def test_config_search_stops_at_plugin_boundary(self):
        repository = self.root / "repository"
        plugin = repository / "plugins" / "nested-plugin"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("placeholder\n", encoding="utf-8")
        save_config(repository, plugin, ("codex",), repository, repository / "parent-dist")
        with self.cwd(plugin):
            config = find_config()
        self.assertIsNone(config)

    def test_config_search_stops_at_nested_git_boundary(self):
        repository = self.root / "repository"
        nested = repository / "nested"
        (nested / "child").mkdir(parents=True)
        (nested / ".git").mkdir()
        save_config(repository, repository, ("codex",), repository, repository / "parent-dist")
        with self.cwd(nested / "child"):
            config = find_config()
        self.assertIsNone(config)

    def test_no_config_bypasses_malformed_local_config(self):
        source = self.root / "plugin"
        config_dir = self.root / ".any-harness"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("schema: 1\nsource: [", encoding="utf-8")
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke("init", source, "--name", "bypass-plugin")
            self.assertEqual(code, 0, stderr)
            code, _stdout, stderr = self.invoke(
                "--no-config",
                "build",
                source,
                "--out",
                self.root / "out",
                "--harness",
                "codex",
            )
        self.assertEqual(code, 0, stderr)

    def test_global_scope_wins_over_saved_project_and_project_global_conflicts(self):
        source = self.root / "plugin"
        configured_project = self.root / "configured-project"
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke("setup", source, "--no-input", "--harness", "codex", "--project", configured_project)
        self.assertEqual(code, 0, stderr)

        @contextlib.contextmanager
        def fake_source_folder(*_args, **_kwargs):
            yield source, {"path": str(source)}

        with patch("any_harness.cli.source_folder", fake_source_folder), patch("any_harness.cli.install", return_value=([], [])) as installer:
            with self.cwd(self.root):
                code, _stdout, stderr = self.invoke("install", source, "--global")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(installer.call_args.args[2], Path.home())

        with patch("any_harness.cli.source_folder", fake_source_folder), patch("any_harness.cli.install", return_value=([], [])):
            with self.cwd(self.root):
                code, _stdout, stderr = self.invoke(
                    "install",
                    source,
                    "--harness",
                    "codex",
                    "--project",
                    configured_project,
                    "--global",
                )
        self.assertEqual(code, 2)
        self.assertIn("mutually exclusive", stderr)

    def test_symlinked_config_file_is_rejected(self):
        config_dir = self.root / ".any-harness"
        config_dir.mkdir()
        target = self.root / "real-config.yaml"
        target.write_text("schema: 1\n", encoding="utf-8")
        config_path = config_dir / "config.yaml"
        try:
            config_path.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(ValueError):
            load_config(config_path)

    def test_invalid_setup_leaves_existing_config_unchanged(self):
        source = self.root / "plugin"
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke("setup", source, "--no-input", "--harness", "codex")
            self.assertEqual(code, 0, stderr)
        config_path = self.root / ".any-harness/config.yaml"
        before = config_path.read_bytes()
        second_source = self.root / "second-plugin"
        with self.cwd(self.root):
            code, _stdout, stderr = self.invoke(
                "setup",
                second_source,
                "--no-input",
                "--harness",
                "codex",
                "--out",
                "",
            )
        self.assertEqual(code, 1)
        self.assertEqual(config_path.read_bytes(), before)
        self.assertFalse(second_source.exists())

    def test_interactive_setup_accepts_prompted_values(self):
        source = self.root / "interactive"
        prompts = ["codex", ".", "dist", "interactive-plugin", "Interactive Author"]
        with patch("any_harness.cli.typer.prompt", side_effect=prompts):
            with self.cwd(self.root):
                code, _stdout, stderr = self.invoke("setup", source)
        self.assertEqual(code, 0, stderr)
        self.assertIn("name: interactive-plugin", (source / "plugin.yaml").read_text(encoding="utf-8"))
        self.assertEqual(load_config(self.root / ".any-harness/config.yaml").harnesses, ("codex",))

    def test_eval_preserves_suite_harness_default_without_cli_or_config_selection(self):
        with patch.object(evaluation, "preview_suite", return_value={"status": "preview"}) as preview:
            code, stdout, stderr = self.invoke("eval", "preview", self.root / "source")
        self.assertEqual(code, 0, stderr)
        self.assertIsNone(preview.call_args.kwargs["harnesses"])
        self.assertEqual(json.loads(stdout), {"status": "preview"})

    def test_eval_run_preserves_native_exit_code_and_json_output(self):
        result = {"status": "failed"}
        with patch.object(evaluation, "run_suite", return_value=result), patch.object(evaluation, "ci_exit_code", return_value=7):
            code, stdout, stderr = self.invoke("eval", "run", self.root / "source", "--out", self.root / "results")
        self.assertEqual(code, 7)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["exit_code"], 7)

    def test_setup_prompt_abort_returns_one_without_traceback_or_source(self):
        source = self.root / "cancelled"
        with patch("any_harness.cli.typer.prompt", side_effect=click.Abort()):
            code, _stdout, stderr = self.invoke("setup", source)
        self.assertEqual(code, 1)
        self.assertIn("Aborted.", stderr)
        self.assertFalse(source.exists())

    def test_doctor_json_returns_public_api_exit_code(self):
        with patch("any_harness.cli.run_doctor", return_value=({"checks": []}, 4)) as doctor:
            code, stdout, stderr = self.invoke("doctor", "--json")
        self.assertEqual(code, 4)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), {"checks": []})
        doctor.assert_called_once_with(None)

    def test_build_all_ignores_local_config(self):
        repository = self.root / "repository"
        save_config(self.root, repository, ("codex",), self.root, self.root / "configured-output")
        with patch("any_harness.cli.build_all_plugins", return_value=[]) as build_all:
            with self.cwd(self.root):
                code, _stdout, stderr = self.invoke(
                    "build-all",
                    repository,
                    "--out",
                    self.root / "combined",
                    "--harness",
                    "cursor",
                )
        self.assertEqual(code, 0, stderr)
        build_all.assert_called_once_with(
            str(repository),
            str(self.root / "combined"),
            ("cursor",),
            False,
            None,
        )


if __name__ == "__main__":
    unittest.main()
