import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from any_harness import eval as evaluation
from any_harness.cli import main


class FakeAdapter:
    """A native-looking executable used to test runner orchestration only."""

    @staticmethod
    def capabilities():
        return {harness: {"executable": sys.executable, "model": True, "effort": True} for harness in evaluation.HARNESSES}

    @staticmethod
    def prepare(harness, workspace, config_dir, settings, config_mode, component, mode, prompt):
        script = (
            "import json, pathlib, sys; "
            "p=pathlib.Path('result.txt'); p.write_text('native-result\\n', encoding='utf-8'); "
            "print(json.dumps({'type':'result','result':'native result','completed':True}))"
        )
        return {
            "argv": [sys.executable, "-c", script, prompt],
            "env": {"FAKE_HARNESS": harness},
            "notes": ["fake native CLI"],
            "unsupported": None,
        }

    @staticmethod
    def inspect_output(harness, stdout, stderr, component):
        return {
            "output": "native result",
            "invocation": "observed",
            "observed_model": "fake-model",
            "observed_effort": "medium",
            "completed": True,
            "tool_calls": [{"name": component.get("name", "plugin")}],
            "tool_trace_complete": True,
        }


class ErrorAdapter(FakeAdapter):
    @staticmethod
    def inspect_output(harness, stdout, stderr, component):
        return {"output": "", "invocation": "unknown", "error": "authentication failed", "completed": False}


class UnknownAdapter(FakeAdapter):
    @staticmethod
    def inspect_output(harness, stdout, stderr, component):
        return {"output": "native result", "invocation": "unknown", "completed": True}


class UnsupportedAdapter(FakeAdapter):
    calls = 0

    @staticmethod
    def prepare(harness, workspace, config_dir, settings, config_mode, component, mode, prompt):
        UnsupportedAdapter.calls += 1
        return {"argv": [], "env": {}, "unsupported": "native component unsupported"}


class MutatingAdapter(FakeAdapter):
    @staticmethod
    def prepare(harness, workspace, config_dir, settings, config_mode, component, mode, prompt):
        script = "import pathlib; pathlib.Path('input.txt').unlink(); print('done')"
        return {"argv": [sys.executable, "-c", script, prompt], "env": {}}


class EnvRemoveAdapter(FakeAdapter):
    @staticmethod
    def prepare(harness, workspace, config_dir, settings, config_mode, component, mode, prompt):
        script = "import os; print('removed' if 'SHOULD_REMOVE' not in os.environ else 'present')"
        return {"argv": [sys.executable, "-c", script, prompt], "env": {"SHOULD_REMOVE": "yes"}, "env_remove": ["SHOULD_REMOVE"]}

    @staticmethod
    def inspect_output(harness, stdout, stderr, component):
        return {"output": stdout, "invocation": "observed", "completed": True}


class EvalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-eval-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "plugin"
        (self.source / "skills" / "review" / "assets").mkdir(parents=True)
        (self.source / "agents").mkdir()
        (self.source / "evals" / "fixtures" / "basic").mkdir(parents=True)
        (self.source / "evals" / "checks").mkdir()
        (self.source / "plugin.yaml").write_text(
            "schema: 1\nname: demo-plugin\nversion: 1.0.0\ndescription: Demo\nauthor:\n  name: Tester\n",
            encoding="utf-8",
        )
        (self.source / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review files\n---\n\nReview the requested files.\n",
            encoding="utf-8",
        )
        (self.source / "agents" / "reviewer.md").write_text(
            "---\nname: reviewer\ndescription: Review changes\n---\n\nFind correctness bugs.\n",
            encoding="utf-8",
        )
        (self.source / "evals" / "fixtures" / "basic" / "input.txt").write_text("fixture\n", encoding="utf-8")
        check = self.source / "evals" / "checks" / "result.sh"
        check.write_text("#!/bin/sh\ntest -f result.txt\n", encoding="utf-8")
        check.chmod(check.stat().st_mode | stat.S_IXUSR)
        (self.source / "evals" / "cases.yaml").write_text(
            textwrap.dedent(
                """
                schema: 1
                harnesses: [codex]
                cases:
                  - id: native-output
                    fixture: fixtures/basic
                    prompt: Return the native result.
                    component: {kind: skill, name: review}
                    mode: explicit
                    invocation: required
                    assertions:
                      - {type: output_contains, value: native result}
                      - {type: file_exists, path: result.txt}
                      - {type: file_contains, path: result.txt, value: native-result}
                      - {type: file_not_exists, path: missing.txt}
                      - {type: output_not_contains, value: forbidden-output}
                      - {type: unchanged, path: input.txt}
                      - {type: check, script: checks/result.sh}
                    constraints:
                      - {type: tool_not_used, name: forbidden-tool}
                variants:
                  - id: base
                  - id: without-agent
                    exclude: [agent:reviewer]
                """
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_validate_and_preview_are_json_safe(self):
        suite = evaluation.load_suite(self.source)
        self.assertEqual(len(suite["cases"]), 1)
        preview = evaluation.preview_suite(self.source, harnesses=["codex"], repeat=2)
        self.assertEqual(len(preview["runs"]), 4)
        self.assertIn("argv", preview["runs"][0]["prepared"])
        json.dumps(preview)

    def test_run_stages_native_files_and_cleans_workspace(self):
        out = self.root / "results"
        with patch.object(evaluation, "_adapter_module", return_value=FakeAdapter):
            result = evaluation.run_suite(self.source, out, harnesses=["codex"], repeat=2, jobs=2, timeout=10)
        self.assertEqual(evaluation.ci_exit_code(result), 0)
        self.assertTrue((out / "result.json").is_file())
        self.assertTrue((out / "report.md").is_file())
        self.assertEqual(len(result["runs"]), 4)
        self.assertTrue(all(run["status"] == "pass" for run in result["runs"]))
        self.assertTrue(all(Path(run["artifact_path"]).is_dir() for run in result["runs"]))
        self.assertTrue(all((Path(run["artifact_path"]) / "result.txt").is_file() for run in result["runs"]))
        self.assertEqual(len(result["variability"]), 2)

    def test_timeout_and_runner_error_are_distinct(self):
        class SlowAdapter(FakeAdapter):
            @staticmethod
            def prepare(harness, workspace, config_dir, settings, config_mode, component, mode, prompt):
                return {"argv": [sys.executable, "-c", "import time; time.sleep(30)", prompt], "env": {}}

        out = self.root / "timeout"
        with patch.object(evaluation, "_adapter_module", return_value=SlowAdapter):
            result = evaluation.run_suite(self.source, out, harnesses=["codex"], timeout=0.1)
        self.assertEqual(result["runs"][0]["status"], "timeout")
        self.assertEqual(evaluation.ci_exit_code(result), 2)

        error_out = self.root / "error"
        with patch.object(evaluation, "_adapter_module", return_value=ErrorAdapter):
            error_result = evaluation.run_suite(self.source, error_out, harnesses=["codex"], timeout=10)
        self.assertEqual(error_result["runs"][0]["status"], "runner_error")
        self.assertEqual(evaluation.ci_exit_code(error_result), 2)

    def test_unknown_invocation_does_not_hide_behavior_or_vacuous_pass(self):
        out = self.root / "unknown"
        with patch.object(evaluation, "_adapter_module", return_value=UnknownAdapter):
            result = evaluation.run_suite(self.source, out, harnesses=["codex"], case_ids=["native-output"])
        # The output assertions are deterministic, but required invocation
        # evidence remains unknown and therefore cannot pass.
        self.assertEqual(result["runs"][0]["status"], "inconclusive")

        no_assertions = self.source / "evals" / "cases.yaml"
        original = no_assertions.read_text(encoding="utf-8")
        empty_text = re.sub(r"    assertions:\n(?:      - .*\n)+", "    assertions: []\n", original)
        empty_text = re.sub(r"    constraints:\n(?:      - .*\n)+", "    constraints: []\n", empty_text)
        no_assertions.write_text(empty_text, encoding="utf-8")
        try:
            with patch.object(evaluation, "_adapter_module", return_value=UnknownAdapter):
                empty = evaluation.run_suite(self.source, self.root / "empty", harnesses=["codex"], case_ids=["native-output"])
            self.assertEqual(empty["runs"][0]["status"], "inconclusive")
        finally:
            no_assertions.write_text(original, encoding="utf-8")

    def test_unsupported_is_skipped_without_launch_and_env_removals_apply(self):
        UnsupportedAdapter.calls = 0
        with patch.object(evaluation, "_adapter_module", return_value=UnsupportedAdapter):
            skipped = evaluation.run_suite(self.source, self.root / "unsupported", harnesses=["codex"], case_ids=["native-output"])
        self.assertEqual(skipped["runs"][0]["status"], "skipped")
        self.assertEqual(evaluation.ci_exit_code(skipped), 3)
        self.assertEqual(UnsupportedAdapter.calls, 2)  # one call per base/without-agent variant

        with tempfile.TemporaryDirectory(prefix="any-harness-env-remove-") as temp:
            temp_root = Path(temp)
            workspace = temp_root / "workspace"
            config = temp_root / "config"
            workspace.mkdir()
            config.mkdir()
            stdout_path = temp_root / "stdout"
            stderr_path = temp_root / "stderr"
            with patch.dict(os.environ, {"SHOULD_REMOVE": "inherited"}, clear=False):
                removed = evaluation._invoke(
                    EnvRemoveAdapter,
                    "codex",
                    workspace,
                    config,
                    {},
                    {"kind": "skill", "name": "review"},
                    "explicit",
                    "return the result",
                    "isolated",
                    stdout_path,
                    stderr_path,
                    10,
                    sys.executable,
                )
            self.assertIn("removed", removed["normalized_output"])

    def test_native_plugin_extras_are_explicitly_skipped(self):
        native = self.source / "native" / "codex" / "plugin"
        native.mkdir(parents=True)
        (native / ".mcp.json").write_text("{}\n", encoding="utf-8")

        class ShouldNotLaunch(FakeAdapter):
            calls = 0

            @staticmethod
            def prepare(*args, **kwargs):
                ShouldNotLaunch.calls += 1
                raise AssertionError("plugin package extras must be skipped before native launch")

        with patch.object(evaluation, "_adapter_module", return_value=ShouldNotLaunch):
            result = evaluation.run_suite(self.source, self.root / "native-extra", harnesses=["codex"], case_ids=["native-output"])
        self.assertEqual(result["runs"][0]["status"], "skipped")
        self.assertEqual(ShouldNotLaunch.calls, 0)
        self.assertTrue(result["runs"][0]["unsupported_features"])

    def test_unchanged_directory_and_check_failure_are_not_false_passes(self):
        # Mutating a file nested under an asserted directory must be visible
        # even when the directory itself still exists.
        cases = self.source / "evals" / "cases.yaml"
        original = cases.read_text(encoding="utf-8")
        cases.write_text(original.replace("path: input.txt", "path: ."), encoding="utf-8")
        try:
            with patch.object(evaluation, "_adapter_module", return_value=MutatingAdapter):
                result = evaluation.run_suite(self.source, self.root / "mutating", harnesses=["codex"], case_ids=["native-output"])
            self.assertEqual(result["runs"][0]["status"], "failure")
        finally:
            cases.write_text(original, encoding="utf-8")

        check = self.source / "evals" / "checks" / "result.sh"
        check_original = check.read_text(encoding="utf-8")
        check.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        try:
            with patch.object(evaluation, "_adapter_module", return_value=FakeAdapter):
                result = evaluation.run_suite(self.source, self.root / "check-fail", harnesses=["codex"], case_ids=["native-output"])
            self.assertEqual(result["runs"][0]["status"], "failure")
        finally:
            check.write_text(check_original, encoding="utf-8")

    def test_strict_validation_rejects_paths_unknown_fields_and_fixture_leaks(self):
        cases = self.source / "evals" / "cases.yaml"
        original = cases.read_text(encoding="utf-8")
        cases.write_text(original.replace("schema: 1", "schema: 1\nunknown: true"), encoding="utf-8")
        with self.assertRaises(evaluation.EvalError):
            evaluation.load_suite(self.source)
        cases.write_text(original.replace("fixtures/basic", "../fixtures/basic"), encoding="utf-8")
        with self.assertRaises(evaluation.EvalError):
            evaluation.load_suite(self.source)
        cases.write_text(original.replace("fixtures/basic", "."), encoding="utf-8")
        with self.assertRaises(evaluation.EvalError):
            evaluation.load_suite(self.source)

    def test_cli_run_and_report_return_behavior_code(self):
        out = self.root / "cli-results"
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(evaluation, "_adapter_module", return_value=FakeAdapter), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["eval", "run", str(self.source), "--harness", "codex", "--out", str(out)])
        self.assertEqual(code, 0, stderr.getvalue())
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["exit_code"], 0)
        stdout.seek(0)
        stdout.truncate(0)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            report_code = main(["eval", "report", str(out / "result.json")])
        self.assertEqual(report_code, 0)
        self.assertTrue((out / "report.md").is_file())

    def test_report_cannot_follow_output_alias_to_result_json(self):
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps({"schema": 1, "runs": [{"status": "pass"}]}), encoding="utf-8")
        alias = self.root / "alias.json"
        alias.symlink_to(result_path)
        with self.assertRaises(evaluation.EvalError):
            evaluation.report_result(result_path, alias)


if __name__ == "__main__":
    unittest.main()
