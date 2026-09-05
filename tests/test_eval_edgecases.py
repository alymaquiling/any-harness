"""Independent regression tests for native evaluation edge cases.

These tests use executable local stubs and never call a model or a vendor
service.  They intentionally exercise contracts at the runner boundary so a
native adapter can be replaced without changing what these checks prove.
"""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

import yaml

from any_harness import eval as evaluation
from any_harness import eval_adapters


class EvalEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-eval-edge-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "plugin"
        fixture = self.source / "evals" / "fixtures" / "basic"
        (fixture / "emptydir").mkdir(parents=True)
        (self.source / "skills" / "review").mkdir(parents=True)
        (self.source / "plugin.yaml").write_text(
            "schema: 1\n"
            "name: edge-plugin\n"
            "version: 1.0.0\n"
            "description: Edge case test plugin\n"
            "author:\n"
            "  name: Eval Tests\n",
            encoding="utf-8",
        )
        (self.source / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review the fixture\n---\n\nReview it.\n",
            encoding="utf-8",
        )
        (fixture / "input.txt").write_text("unchanged\n", encoding="utf-8")
        (fixture / "README.txt").write_text("fixture\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_cases(self, cases, variants=None, harnesses=("codex",)):
        document = {"schema": 1, "harnesses": list(harnesses), "cases": list(cases)}
        if variants is not None:
            document["variants"] = list(variants)
        path = self.source / "evals" / "cases.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    @staticmethod
    def case(case_id, assertions=None, *, constraints=None, prompt="Inspect the fixture.", component=None, mode="automatic", invocation="optional"):
        result = {
            "id": case_id,
            "fixture": "fixtures/basic",
            "prompt": prompt,
            "component": component or {"kind": "plugin", "name": "edge-plugin"},
            "mode": mode,
            "invocation": invocation,
            "assertions": list(assertions or []),
        }
        if constraints:
            result["constraints"] = list(constraints)
        return result

    @staticmethod
    def executable(path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def fake_adapter(self, script, inspection=None, *, capabilities=None, env=None, unsupported=None):
        capabilities = capabilities or {
            harness: {"executable": sys.executable, "model": True, "effort": True}
            for harness in evaluation.HARNESSES
        }
        env_template = dict(env or {})

        class FakeAdapter:
            prepare_calls = 0

            @staticmethod
            def capabilities():
                return capabilities

            @staticmethod
            def prepare(harness, workspace, config_dir, settings, config_mode, component, mode, prompt):
                FakeAdapter.prepare_calls += 1
                invocation_env = dict(env_template)
                env_remove = list(invocation_env.pop("_env_remove", []))
                result = {
                    "argv": [sys.executable, "-c", script],
                    "env": invocation_env,
                    "notes": ["edge-case native stub"],
                    "unsupported": unsupported,
                }
                if env_remove:
                    result["env_remove"] = env_remove
                return result

            @staticmethod
            def inspect_output(harness, stdout, stderr, component):
                if inspection is not None:
                    return inspection(harness, stdout, stderr, component)
                return {
                    "output": stdout,
                    "invocation": "observed",
                    "completed": True,
                    "tool_trace_complete": True,
                    "tool_calls": [],
                    "actions": [],
                }

        return FakeAdapter

    def run_suite(self, adapter, output_name="result", *, config_mode="isolated", timeout=5, check_timeout=30, harnesses=("codex",)):
        output = self.root / output_name
        with patch.object(evaluation, "_adapter_module", return_value=adapter):
            return evaluation.run_suite(
                self.source,
                output,
                harnesses=list(harnesses),
                config_mode=config_mode,
                timeout=timeout,
                check_timeout=check_timeout,
            )

    def test_all_deterministic_assertions_and_structured_constraints(self):
        script = (
            "from pathlib import Path; "
            "Path('result.txt').write_text('created\\n', encoding='utf-8'); "
            "print('native output')"
        )
        check = self.executable(
            self.source / "evals" / "checks" / "created.sh",
            "#!/bin/sh\nset -eu\ntest -f result.txt\ngrep -F created result.txt >/dev/null\n",
        )
        assertions = [
            {"type": "output_contains", "value": "native output"},
            {"type": "output_not_contains", "value": "forbidden"},
            {"type": "file_exists", "path": "result.txt"},
            {"type": "file_contains", "path": "result.txt", "value": "created"},
            {"type": "file_not_exists", "path": "missing.txt"},
            {"type": "unchanged", "path": "input.txt"},
            {"type": "check", "script": "checks/created.sh"},
        ]
        adapter = self.fake_adapter(
            script,
            inspection=lambda _h, stdout, _e, _c: {
                "output": stdout,
                "invocation": "observed",
                "completed": True,
                "tool_trace_complete": True,
                "tool_calls": [{"name": "Read"}],
                "actions": [{"name": "Read"}],
            },
        )
        self.write_cases(
            [
                self.case(
                    "deterministic",
                    assertions,
                    constraints=[
                        {"type": "action_forbidden", "action": "Write"},
                        {"type": "tool_not_used", "name": "Write"},
                    ],
                )
            ]
        )
        result = self.run_suite(adapter)
        self.assertEqual(result["runs"][0]["status"], "pass")
        self.assertTrue(all(item["passed"] is True for item in result["runs"][0]["assertions"]))

    def test_deleted_empty_directory_unchanged_and_check_failure_timeout_are_not_passes(self):
        script = "from pathlib import Path; Path('emptydir').rmdir(); print('done')"
        self.executable(self.source / "evals" / "checks" / "fail.sh", "#!/bin/sh\nexit 7\n")
        self.executable(
            self.source / "evals" / "checks" / "timeout.sh",
            "#!/bin/sh\nsleep 2\n",
        )
        self.write_cases(
            [
                self.case(
                    "edge-assertions",
                    [
                        {"type": "unchanged", "path": "emptydir"},
                        {"type": "check", "script": "checks/fail.sh"},
                        {"type": "check", "script": "checks/timeout.sh"},
                    ],
                )
            ]
        )
        result = self.run_suite(self.fake_adapter(script), check_timeout=0.5)
        run = result["runs"][0]
        self.assertEqual(run["status"], "failure")
        details = {item["type"]: item for item in run["assertions"]}
        self.assertFalse(details["unchanged"]["passed"])
        self.assertFalse(details["check"]["passed"])
        # There are two check assertions; the timeout is the one with a reason.
        timed = [
            item
            for item in run["assertions"]
            if item["type"] == "check" and item.get("reason") == "check timed out"
        ]
        self.assertEqual(len(timed), 1)
        self.assertIsNone(timed[0]["passed"])

    def test_unknown_invocation_cannot_hide_failed_outcome(self):
        adapter = self.fake_adapter(
            "print('expected output')",
            inspection=lambda _h, _stdout, _stderr, _c: {
                "output": "expected output",
                "invocation": "unknown",
                "completed": True,
                "tool_trace_complete": False,
            },
        )
        self.write_cases(
            [
                self.case(
                    "failed-file-assertion",
                    [{"type": "output_contains", "value": "expected output"}, {"type": "file_exists", "path": "never-created"}],
                    invocation="required",
                )
            ]
        )
        result = self.run_suite(adapter)
        self.assertEqual(result["runs"][0]["status"], "failure")
        self.assertEqual(evaluation.ci_exit_code(result), 1)

    def test_malformed_or_incomplete_native_stream_is_inconclusive(self):
        payloads = (
            "not-json\n",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}}) + "\n",
        )
        for index, payload in enumerate(payloads):
            with self.subTest(payload=payload):
                adapter = self.fake_adapter(
                    "print(" + repr(payload) + ", end='')",
                    inspection=lambda _h, stdout, stderr, component: eval_adapters.inspect_output(
                        "codex", stdout, stderr, component
                    ),
                )
                self.write_cases(
                    [
                        self.case(
                            "incomplete-stream",
                            [{"type": "output_contains", "value": "partial" if index else "not-json"}],
                            component={"kind": "skill", "name": "review"},
                            invocation="required",
                        )
                    ]
                )
                result = self.run_suite(adapter, output_name="incomplete-%d" % index)
                run = result["runs"][0]
                self.assertEqual(run["status"], "inconclusive")
                self.assertEqual(run["invocation"], "unknown")
                self.assertFalse(run.get("tool_trace_complete"))

    def test_unsupported_prepare_never_launches_native_process(self):
        marker = self.root / "launched"
        script = "from pathlib import Path; Path(%r).write_text('bad', encoding='utf-8')" % str(marker)
        adapter = self.fake_adapter(script, unsupported="component unsupported")
        self.write_cases([self.case("unsupported", [])])
        result = self.run_suite(adapter)
        run = result["runs"][0]
        self.assertEqual(run["status"], "skipped")
        self.assertIn("component unsupported", run["reason"])
        self.assertEqual(adapter.prepare_calls, 1)
        self.assertFalse(marker.exists())
        self.assertIsNone(run["exit_status"])

    def test_env_remove_is_honored_and_inherit_records_home_fingerprint(self):
        env_adapter = self.fake_adapter(
            "import os; print('removed' if 'SHOULD_REMOVE' not in os.environ else 'present')",
            inspection=lambda _h, stdout, _stderr, _c: {
                "output": stdout,
                "invocation": "observed",
                "completed": True,
                "tool_trace_complete": True,
            },
            env={"SHOULD_REMOVE": "present", "_env_remove": ["SHOULD_REMOVE"]},
        )
        self.write_cases([self.case("env-remove", [{"type": "output_contains", "value": "removed"}])])
        with patch.dict(os.environ, {"SHOULD_REMOVE": "ambient"}, clear=False):
            result = self.run_suite(env_adapter, output_name="env-remove")
        self.assertEqual(result["runs"][0]["status"], "pass")
        self.assertIn("removed", result["runs"][0]["normalized_output"])

        user_home = self.root / "user-home"
        (user_home / ".codex").mkdir(parents=True)
        (user_home / ".codex" / "config.toml").write_text('model = "test-model"\n', encoding="utf-8")
        self.write_cases([self.case("inherit-fingerprint", [{"type": "output_contains", "value": "removed"}])])
        with patch.dict(os.environ, {"HOME": str(user_home)}, clear=False):
            os.environ.pop("CODEX_HOME", None)
            result = self.run_suite(env_adapter, output_name="inherit", config_mode="inherit")
        run = result["runs"][0]
        self.assertTrue(run["config"]["inherited_environment"])
        self.assertIn("HOME", run["config"]["environment_keys"])
        fingerprint = run["config_fingerprint"]
        self.assertIsNotNone(fingerprint["hash"])
        self.assertFalse(fingerprint["complete"])
        self.assertTrue(any("config.toml" in name for name in fingerprint["files"]))

    def test_opencode_v2_is_skipped_before_native_launch(self):
        marker = self.root / "opencode-launched"
        executable = self.executable(
            self.root / "bin" / "opencode",
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then printf 'opencode 2.1.0\\n'; exit 0; fi\n"
            "printf launched > %s\n" % str(marker),
        )
        capabilities = {"opencode": {"executable": str(executable), "supported_major": 1}}
        adapter = self.fake_adapter("print('native')", capabilities=capabilities)
        self.write_cases([self.case("opencode-v2", [])], harnesses=("opencode",))
        result = self.run_suite(adapter, harnesses=("opencode",))
        run = result["runs"][0]
        self.assertEqual(run["status"], "skipped")
        self.assertIn("major", run["reason"].lower())
        self.assertFalse(marker.exists())

    def test_unknown_native_result_status_is_inconclusive_and_exit_error_fails(self):
        unknown = self.fake_adapter(
            "print('okay')",
            inspection=lambda _h, _stdout, _stderr, _c: {
                "output": "okay",
                "invocation": "unknown",
                "status": "mystery",
                "completed": False,
                "tool_trace_complete": False,
            },
        )
        self.write_cases([self.case("unknown-status", [{"type": "output_contains", "value": "okay"}])])
        result = self.run_suite(unknown, output_name="unknown-status")
        self.assertEqual(result["runs"][0]["status"], "inconclusive")

        error = self.fake_adapter(
            "import sys; print('okay'); sys.exit(9)",
            inspection=lambda _h, _stdout, _stderr, _c: {
                "output": "okay",
                "invocation": "observed",
                "status": "success",
                "completed": True,
                "tool_trace_complete": True,
            },
        )
        self.write_cases([self.case("exit-error", [{"type": "output_contains", "value": "okay"}])])
        result = self.run_suite(error, output_name="exit-error")
        self.assertEqual(result["runs"][0]["status"], "failure")
        self.assertEqual(result["runs"][0]["exit_status"], 9)

    def test_variant_hashes_differ_without_mutating_source(self):
        variant_source = self.source / "evals" / "variant-source"
        (variant_source / "skills" / "review").mkdir(parents=True)
        (variant_source / "plugin.yaml").write_text(
            "schema: 1\nname: edge-variant\nversion: 2.0.0\n"
            "description: Variant\nauthor:\n  name: Eval Tests\n",
            encoding="utf-8",
        )
        (variant_source / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Variant review\n---\n\nVariant.\n",
            encoding="utf-8",
        )
        self.write_cases(
            [self.case("variants", [{"type": "output_contains", "value": "ok"}], component={"kind": "skill", "name": "review"})],
            variants=[
                {"id": "base"},
                {"id": "without-review", "exclude": ["skill:review"]},
                {"id": "alternate-source", "source": "variant-source"},
            ],
        )
        before = evaluation.hash_tree(self.source, exclude_dirs=("evals", ".git"))
        adapter = self.fake_adapter(
            "print('ok')",
            inspection=lambda _h, _stdout, _stderr, _c: {
                "output": "ok",
                "invocation": "observed",
                "completed": True,
                "tool_trace_complete": True,
            },
        )
        result = self.run_suite(adapter, output_name="variants")
        runs = {run["variant"]: run for run in result["runs"]}
        self.assertNotEqual(runs["base"]["generated_hash"], runs["without-review"]["generated_hash"])
        self.assertNotEqual(runs["base"]["variant_source_hash"], runs["alternate-source"]["variant_source_hash"])
        self.assertEqual(runs["base"]["variant_source_hash"], runs["without-review"]["variant_source_hash"])
        self.assertEqual(before, evaluation.hash_tree(self.source, exclude_dirs=("evals", ".git")))

    def test_timeout_kills_child_process_group(self):
        marker = self.root / "late-child"
        child = "import time; time.sleep(1); open(%r, 'w').write('late')" % str(marker)
        script = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', %r]); "
            "time.sleep(30)"
        ) % child
        adapter = self.fake_adapter(
            script,
            inspection=lambda _h, _stdout, _stderr, _c: {"output": "", "invocation": "unknown"},
        )
        self.write_cases([self.case("child-timeout", [])])
        result = self.run_suite(adapter, output_name="child-timeout", timeout=0.1)
        self.assertEqual(result["runs"][0]["status"], "timeout")
        time.sleep(1.2)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
