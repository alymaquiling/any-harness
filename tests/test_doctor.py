import unittest
from unittest.mock import patch

from any_harness import doctor


class DoctorTest(unittest.TestCase):
    def _checks(self, report):
        return {check["name"]: check for check in report["checks"]}

    def test_default_missing_tools_are_optional_and_all_checks_are_reported(self):
        with patch.object(doctor.sys, "version_info", (3, 11, 0)), patch.object(
            doctor.shutil, "which", return_value=None
        ):
            report, code = doctor.run_doctor()

        checks = self._checks(report)
        self.assertEqual(code, 0)
        self.assertEqual(report["schema"], 1)
        self.assertEqual(
            list(checks),
            ["python", "git", "uv", "claude-code", "codex", "opencode", "cursor"],
        )
        self.assertEqual(checks["python"]["status"], "ok")
        self.assertEqual(checks["git"]["status"], "optional")
        self.assertEqual(checks["uv"]["status"], "optional")
        for harness in doctor.HARNESSES:
            self.assertEqual(checks[harness]["status"], "optional")

    def test_selected_missing_harness_is_required(self):
        def which(command):
            return "/mock/bin/claude" if command == "claude" else None

        with patch.object(doctor.sys, "version_info", (3, 11, 0)), patch.object(
            doctor.shutil, "which", side_effect=which
        ):
            report, code = doctor.run_doctor(["claude-code", "codex"])

        checks = self._checks(report)
        self.assertEqual(code, 1)
        self.assertEqual(checks["claude-code"]["status"], "ok")
        self.assertEqual(checks["codex"]["status"], "missing")
        self.assertEqual(checks["opencode"]["status"], "optional")
        self.assertEqual(checks["cursor"]["status"], "optional")

    def test_git_and_uv_stay_optional_with_selected_available_harness(self):
        def which(command):
            return "/mock/bin/claude" if command == "claude" else None

        with patch.object(doctor.shutil, "which", side_effect=which):
            report, code = doctor.run_doctor(["claude-code"])

        checks = self._checks(report)
        self.assertEqual(code, 0)
        self.assertEqual(checks["git"]["status"], "optional")
        self.assertEqual(checks["uv"]["status"], "optional")
        self.assertEqual(checks["claude-code"]["status"], "ok")

    def test_cursor_uses_agent_alias_when_cursor_agent_is_missing(self):
        def which(command):
            return "/mock/bin/agent" if command == "agent" else None

        with patch.object(doctor.shutil, "which", side_effect=which):
            report, code = doctor.run_doctor(["cursor"])

        cursor = self._checks(report)["cursor"]
        self.assertEqual(code, 0)
        self.assertEqual(cursor["status"], "ok")
        self.assertIn("agent", cursor["detail"])
        self.assertIn(
            "authentication and version compatibility were not checked",
            cursor["detail"],
        )

    def test_cursor_prefers_cursor_agent_when_both_aliases_are_available(self):
        def which(command):
            if command == "cursor-agent":
                return "/mock/bin/cursor-agent"
            if command == "agent":
                return "/mock/bin/agent"
            return None

        with patch.object(doctor.shutil, "which", side_effect=which):
            report, code = doctor.run_doctor(["cursor"])

        cursor = self._checks(report)["cursor"]
        self.assertEqual(code, 0)
        self.assertEqual(cursor["status"], "ok")
        self.assertIn("cursor-agent", cursor["detail"])

    def test_string_and_iterable_selections_are_accepted(self):
        with patch.object(doctor.shutil, "which", return_value="/mock/bin/tool"):
            string_report, string_code = doctor.run_doctor("codex")
            iterable_report, iterable_code = doctor.run_doctor(
                (harness for harness in ("codex", "codex"))
            )

        self.assertEqual(string_code, 0)
        self.assertEqual(iterable_code, 0)
        self.assertEqual(
            self._checks(string_report)["codex"]["status"],
            "ok",
        )
        self.assertEqual(
            self._checks(iterable_report)["codex"]["status"],
            "ok",
        )

    def test_invalid_selection_returns_diagnostic_instead_of_raising(self):
        with patch.object(doctor.shutil, "which", return_value=None):
            report, code = doctor.run_doctor(["unknown-harness"])

        checks = self._checks(report)
        self.assertEqual(code, 1)
        self.assertEqual(checks["harnesses"]["status"], "missing")
        self.assertIn("Supported harnesses", checks["harnesses"]["detail"])
        for harness in doctor.HARNESSES:
            self.assertIn(harness, checks)

    def test_non_iterable_selection_returns_diagnostic_instead_of_raising(self):
        with patch.object(doctor.shutil, "which", return_value=None):
            report, code = doctor.run_doctor(42)

        diagnostic = self._checks(report)["harnesses"]
        self.assertEqual(code, 1)
        self.assertEqual(diagnostic["status"], "missing")
        self.assertIn("iterable of harness names", diagnostic["detail"])

    def test_bytes_and_dict_selections_are_rejected_safely(self):
        with patch.object(doctor.shutil, "which", return_value=None):
            bytes_report, bytes_code = doctor.run_doctor(b"codex")
            dict_report, dict_code = doctor.run_doctor({"codex": True})

        self.assertEqual(bytes_code, 1)
        self.assertEqual(dict_code, 1)
        self.assertEqual(self._checks(bytes_report)["harnesses"]["status"], "missing")
        self.assertEqual(self._checks(dict_report)["harnesses"]["status"], "missing")

    def test_unsupported_python_is_required(self):
        with patch.object(doctor.sys, "version_info", (3, 8, 10)), patch.object(
            doctor.shutil, "which", return_value="/mock/bin/tool"
        ):
            report, code = doctor.run_doctor()

        python = self._checks(report)["python"]
        self.assertEqual(code, 1)
        self.assertEqual(python["status"], "missing")
        self.assertIn("Python 3.9 or newer is required", python["detail"])

    def test_python_3_9_is_supported(self):
        with patch.object(doctor.sys, "version_info", (3, 9, 0)), patch.object(
            doctor.shutil, "which", return_value="/mock/bin/tool"
        ):
            report, code = doctor.run_doctor()

        self.assertEqual(code, 0)
        self.assertEqual(self._checks(report)["python"]["status"], "ok")

    def test_missing_uv_on_macos_includes_homebrew_hint(self):
        with patch.object(doctor.sys, "platform", "darwin"), patch.object(
            doctor.shutil, "which", return_value=None
        ):
            report, code = doctor.run_doctor()

        uv = self._checks(report)["uv"]
        self.assertEqual(code, 0)
        self.assertEqual(uv["status"], "optional")
        self.assertIn("brew install uv", uv["detail"])

    def test_missing_uv_on_other_platforms_has_no_macos_hint(self):
        with patch.object(doctor.sys, "platform", "linux"), patch.object(
            doctor.shutil, "which", return_value=None
        ):
            report, code = doctor.run_doctor()

        uv = self._checks(report)["uv"]
        self.assertEqual(code, 0)
        self.assertNotIn("brew install uv", uv["detail"])

    def test_executable_presence_explains_scope_of_check(self):
        with patch.object(doctor.shutil, "which", return_value="/mock/bin/tool"):
            report, _ = doctor.run_doctor(["claude-code"])

        detail = self._checks(report)["claude-code"]["detail"]
        self.assertIn("authentication", detail)
        self.assertIn("version compatibility", detail)
        self.assertIn("not checked", detail)

    def test_detection_module_does_not_load_or_execute_subprocesses(self):
        self.assertNotIn("subprocess", doctor.__dict__)


if __name__ == "__main__":
    unittest.main()
