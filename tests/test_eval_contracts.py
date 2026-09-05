import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from any_harness.cli import main


class EvalCliContractTest(unittest.TestCase):
    """Exercise the public eval CLI with a deterministic native CLI stub.

    The stub emits the shape of a Codex ``exec --json`` stream but never calls
    a model. These checks therefore cover runner contracts separately from any
    live harness behavior.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-eval-contract-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "plugin"
        fixture = self.source / "evals" / "fixtures" / "basic"
        fixture.mkdir(parents=True)
        (self.source / "plugin.yaml").write_text(
            "schema: 1\n"
            "name: smoke-plugin\n"
            "version: 1.0.0\n"
            "description: A deterministic eval contract fixture\n"
            "author:\n"
            "  name: Test\n",
            encoding="utf-8",
        )
        skill = self.source / "skills" / "smoke"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: smoke\n"
            "description: Report the fixture contents\n"
            "---\n\n"
            "Report the fixture contents without editing files.\n",
            encoding="utf-8",
        )
        (fixture / "README.txt").write_text("fixture file\n", encoding="utf-8")
        checks = self.source / "evals" / "checks"
        checks.mkdir()
        check = checks / "workspace.sh"
        check.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "test -d \"$ANY_HARNESS_EVAL_WORKSPACE\"\n"
            "test -f \"$ANY_HARNESS_EVAL_WORKSPACE/README.txt\"\n"
            "test -f README.txt\n"
            "test ! -e cases.yaml\n",
            encoding="utf-8",
        )
        check.chmod(check.stat().st_mode | stat.S_IXUSR)
        (self.source / "evals" / "cases.yaml").write_text(
            "schema: 1\n"
            "harnesses: [codex, opencode]\n"
            "cases:\n"
            "  - id: native-stub\n"
            "    fixture: fixtures/basic\n"
            "    prompt: Find the correctness issue in this fixture.\n"
            "    component:\n"
            "      kind: plugin\n"
            "      name: smoke-plugin\n"
            "    mode: automatic\n"
            "    invocation: optional\n"
            "    assertions:\n"
            "      - type: output_contains\n"
            "        value: zero denominator\n"
            "      - type: unchanged\n"
            "        path: .\n"
            "      - type: check\n"
            "        script: checks/workspace.sh\n",
            encoding="utf-8",
        )
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake_codex = bin_dir / "codex"
        fake_codex.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"stub\"}'\n"
            "printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"zero denominator should be tested\"}}'\n"
            "printf '%s\\n' '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":1,\"output_tokens\":2}}'\n",
            encoding="utf-8",
        )
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
        self.path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_validate_preview_run_and_report_use_native_stub(self):
        with patch.dict(os.environ, {"PATH": self.path}, clear=False):
            code, output, error = self.invoke("eval", "validate", self.source)
            self.assertEqual(code, 0, error)
            self.assertEqual(json.loads(output)["cases"], 1)

            code, output, error = self.invoke("eval", "preview", self.source, "--harness", "codex")
            self.assertEqual(code, 0, error)
            preview = json.loads(output)
            self.assertEqual(preview["runs"][0]["case"], "native-stub")
            self.assertIn("codex", preview["executables"])

            result_dir = self.root / "result"
            code, output, error = self.invoke(
                "eval",
                "run",
                self.source,
                "--harness",
                "codex",
                "--out",
                result_dir,
                "--timeout",
                "5",
            )
            self.assertEqual(code, 0, error)
            result = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["runs"][0]["status"], "pass")
            self.assertTrue(any(item["type"] == "check" and item["passed"] for item in result["runs"][0]["assertions"]))
            self.assertTrue((result_dir / "artifacts").is_dir())

            code, output, error = self.invoke("eval", "report", result_dir / "result.json")
            self.assertEqual(code, 0, error)
            self.assertTrue((result_dir / "report.md").is_file())
            self.assertIn("native-stub", (result_dir / "report.md").read_text(encoding="utf-8"))

    def test_missing_native_harness_is_inconclusive_and_has_result(self):
        with patch.dict(os.environ, {"PATH": str(self.root / "bin")}, clear=False):
            result_dir = self.root / "missing"
            code, output, error = self.invoke(
                "eval",
                "run",
                self.source,
                "--harness",
                "opencode",
                "--out",
                result_dir,
            )
            self.assertEqual(code, 3, error)
            result = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "inconclusive")
            self.assertEqual(result["runs"][0]["status"], "skipped")
            self.assertIn("executable unavailable", result["runs"][0]["reason"])

    def test_report_rejects_malformed_result_json(self):
        malformed = self.root / "malformed.json"
        malformed.write_text("{}\n", encoding="utf-8")
        code, output, error = self.invoke("eval", "report", malformed)
        self.assertEqual(code, 2)
        self.assertIn("Invalid result schema", error)

    def test_external_review_grader_is_separate_and_immutable(self):
        result = self.root / "review-result.json"
        result.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "runs": [
                        {
                            "id": "run-1",
                            "case": "agent-finds-regression",
                            "harness": "codex",
                            "variant": "base",
                            "repeat": 1,
                            "status": "pass",
                            "observed": {"invocation": "unknown"},
                            "artifact_path": "artifacts/run-1/workspace",
                            "stdout_path": "artifacts/run-1/stdout.txt",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = result.read_bytes()
        rubric = Path(__file__).resolve().parents[1] / "examples" / "review-kit" / "tools" / "review-rubric.json"
        grader = rubric.with_name("grade-result.py")
        output = self.root / "review" / "model-grader-v1.json"
        completed = subprocess.run(
            [sys.executable, str(grader), str(result), "--rubric", str(rubric), "--output", str(output)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result.read_bytes(), before)
        artifact = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(artifact["reviews"][0]["review_status"], "pending")
        self.assertEqual(len(artifact["rubric_sha256"]), 64)

        rejected = subprocess.run(
            [sys.executable, str(grader), str(result), "--rubric", str(rubric), "--output", str(result)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(result.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
