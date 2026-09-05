"""Unit tests for native command construction and structured event parsing."""

import json
from pathlib import Path
import tempfile
import unittest

from any_harness.eval_adapters import capabilities, inspect_output, prepare


class EvalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-adapter-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.config = self.root / "config"
        self.workspace.mkdir()
        self.config.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def call(self, harness, settings=None, component=None, mode="automatic", config_mode="isolated", prompt="Review this"):
        return prepare(
            harness,
            self.workspace,
            self.config,
            settings or {},
            config_mode,
            component or {"kind": "plugin"},
            mode,
            prompt,
        )

    def test_capabilities_expose_all_targets_and_differences(self):
        all_capabilities = capabilities()
        self.assertEqual(set(all_capabilities), {"claude-code", "codex", "opencode", "cursor"})
        self.assertTrue(all_capabilities["claude-code"]["agents"]["explicit"])
        self.assertFalse(all_capabilities["codex"]["agents"]["explicit"])
        self.assertFalse(all_capabilities["cursor"]["effort"])
        self.assertEqual(capabilities("codex")["cli"], "codex")
        with self.assertRaises(ValueError):
            capabilities("missing")

    def test_claude_explicit_skill_preserves_native_flags_and_isolated_config(self):
        result = self.call(
            "claude-code",
            {"model": "sonnet", "effort": "high", "permission_mode": "plan"},
            {"kind": "skill", "name": "review"},
            mode="explicit",
        )
        self.assertIsNone(result["unsupported"])
        self.assertEqual(result["env"]["CLAUDE_CONFIG_DIR"], str(self.config))
        self.assertEqual(result["env"]["HOME"], str(self.config / "home"))
        self.assertIn("--model", result["argv"])
        self.assertIn("--effort", result["argv"])
        self.assertIn("--permission-mode", result["argv"])
        self.assertIn("--no-session-persistence", result["argv"])
        self.assertEqual(result["argv"][-1], "/review Review this")

    def test_codex_explicit_skill_uses_exec_json_ephemeral_and_native_reasoning(self):
        result = self.call(
            "codex",
            {"model": "gpt-5.6", "model_reasoning_effort": "high", "sandbox": "workspace-write"},
            {"kind": "skill", "name": "review"},
            mode="explicit",
        )
        self.assertIsNone(result["unsupported"])
        self.assertEqual(result["env"]["CODEX_HOME"], str(self.config))
        self.assertEqual(result["env"]["XDG_CONFIG_HOME"], str(self.config / "xdg-config"))
        self.assertIn("--json", result["argv"])
        self.assertIn("--ephemeral", result["argv"])
        self.assertIn("--sandbox", result["argv"])
        config_only = self.call("codex", {"ignore_user_config": True})
        self.assertIn("--ignore-user-config", config_only["argv"])
        self.assertIn('model_reasoning_effort="high"', result["argv"])
        self.assertTrue(result["argv"][-1].startswith("$review "))

    def test_opencode_agent_selector_and_cursor_force_are_explicit(self):
        opencode = self.call(
            "opencode",
            {"model": "openai/gpt-5", "variant": "high"},
            {"kind": "agent", "name": "reviewer"},
            mode="explicit",
        )
        self.assertIsNone(opencode["unsupported"])
        self.assertIn("--agent", opencode["argv"])
        self.assertIn("reviewer", opencode["argv"])
        self.assertEqual(opencode["env"]["OPENCODE_CONFIG_DIR"], str(self.config))

        cursor = self.call(
            "cursor",
            {"model": "gpt-5", "force": True},
            {"kind": "skill", "name": "review"},
            mode="explicit",
        )
        self.assertIn("unsupported native setting(s): force", cursor["unsupported"])
        self.assertNotIn("--force", cursor["argv"])
        self.assertIn("--model", cursor["argv"])
        self.assertEqual(cursor["env"]["CURSOR_CONFIG_DIR"], str(self.config))
        self.assertTrue(any("CURSOR_CONFIG_DIR" in note for note in cursor["notes"]))

    def test_unsupported_settings_and_agent_selectors_are_visible(self):
        result = self.call("cursor", {"effort": "high"})
        self.assertIn("unsupported native setting(s): effort", result["unsupported"])
        result = self.call("codex", {}, {"kind": "agent", "name": "reviewer"}, mode="explicit")
        self.assertIsNone(result["unsupported"])
        self.assertTrue(any("no documented headless agent selector" in note for note in result["notes"]))
        self.assertIn("Use agent 'reviewer'", result["argv"][-1])

        cursor_agent = self.call("cursor", {}, {"kind": "agent", "name": "reviewer"}, mode="explicit")
        self.assertIsNone(cursor_agent["unsupported"])
        self.assertTrue(cursor_agent["argv"][-1].startswith("/reviewer "))

        unsafe = self.call("codex", {"sandbox": "danger-full-access"})
        self.assertIn("danger-full-access", unsafe["unsupported"])
        invalid = self.call("opencode", {"thinking": "yes"})
        self.assertIn("thinking must be boolean", invalid["unsupported"])
        invalid_effort = self.call("codex", {"effort": "not-a-level"})
        self.assertIn("reasoning effort is unsupported", invalid_effort["unsupported"])
        conflict = self.call("codex", {"effort": "high", "model_reasoning_effort": "high"})
        self.assertIn("conflicting native setting aliases", conflict["unsupported"])

    def test_plugin_evaluation_is_project_native_and_does_not_install(self):
        result = self.call("claude-code", {}, {"kind": "plugin", "name": "demo"})
        self.assertIsNone(result["unsupported"])
        self.assertTrue(any("marketplace/cache installation" in note for note in result["notes"]))
        self.assertNotIn("--plugin-dir", result["argv"])

        opencode = self.call("opencode", {}, {"kind": "plugin", "name": "demo"})
        self.assertEqual(
            opencode.get("env_remove"),
            ["OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG", "OPENCODE_PERMISSION"],
        )

    def test_claude_structured_events_prove_skill_invocation_and_capture_metrics(self):
        stdout = "\n".join(
            json.dumps(event)
            for event in (
                {"type": "system", "subtype": "init", "model": "sonnet", "effort": "high"},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": "Skill", "input": {"skill": "review"}},
                            {"type": "text", "text": "A finding"},
                        ]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "Checked files.",
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                    "total_cost_usd": 0.12,
                },
            )
        )
        result = inspect_output("claude-code", stdout, "", {"kind": "skill", "name": "review"})
        self.assertEqual(result["invocation"], "observed")
        self.assertEqual(result["actions"], result["tool_calls"])
        self.assertTrue(result["tool_trace_complete"])
        self.assertEqual(result["observed_model"], "sonnet")
        self.assertEqual(result["observed_effort"], "high")
        self.assertEqual(result["tokens"]["input_tokens"], 10)
        self.assertEqual(result["cost"], 0.12)
        # The terminal result is authoritative and may replace streamed
        # assistant deltas.
        self.assertEqual(result["output"], "Checked files.")
        self.assertEqual(result["status"], "success")

    def test_codex_structured_agent_message_is_output_but_not_skill_evidence(self):
        stdout = "\n".join(
            json.dumps(event)
            for event in (
                {"type": "thread.started", "thread_id": "t"},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Result"}},
                {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}},
            )
        )
        result = inspect_output("codex", stdout, "", {"kind": "skill", "name": "review"})
        self.assertEqual(result["output"], "Result")
        self.assertEqual(result["invocation"], "unknown")
        self.assertTrue(result["completed"])
        self.assertTrue(result["tool_trace_complete"])
        self.assertEqual(result["tokens"]["output_tokens"], 2)

    def test_structured_tool_events_are_recorded_for_opencode_and_cursor(self):
        opencode = inspect_output(
            "opencode",
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "skill",
                        "state": {"status": "completed", "input": {"name": "review"}},
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "step_finish",
                    "model": "openai/gpt-5",
                    "variant": "high",
                    "part": {"tokens": {"input": 4, "output": 2}, "cost": 0.02},
                }
            ),
            "",
            {"kind": "skill", "name": "review"},
        )
        self.assertEqual(opencode["invocation"], "observed")
        self.assertEqual(opencode["observed_model"], "openai/gpt-5")
        self.assertEqual(opencode["observed_effort"], "high")
        self.assertEqual(opencode["tokens"]["input"], 4)
        self.assertEqual(opencode["cost"], 0.02)
        cursor = inspect_output(
            "cursor",
            json.dumps({"type": "system", "subtype": "init", "model": "gpt-5"})
            + "\n"
            + json.dumps({"type": "tool_call", "name": "Skill", "input": {"skill": "review"}})
            + "\n"
            + json.dumps({"type": "result", "subtype": "success", "result": "done"}),
            "",
            {"kind": "skill", "name": "review"},
        )
        self.assertEqual(cursor["invocation"], "observed")
        self.assertEqual(cursor["observed_model"], "gpt-5")
        self.assertEqual(cursor["status"], "success")

    def test_assistant_claims_do_not_prove_invocation_and_errors_are_preserved(self):
        claim = inspect_output(
            "claude-code",
            json.dumps({"type": "result", "subtype": "success", "result": "I used /review."}),
            "",
            {"kind": "skill", "name": "review"},
        )
        self.assertEqual(claim["invocation"], "unknown")
        self.assertEqual(claim["tool_calls"], [])
        failed = inspect_output(
            "codex",
            json.dumps({"type": "turn.failed", "error": {"message": "not authenticated"}}),
            "",
            {"kind": "plugin", "name": "demo"},
        )
        self.assertEqual(failed["status"], "failure")
        self.assertIn("not authenticated", failed["error"])
        self.assertFalse(failed["completed"])

    def test_plain_output_is_retained_but_remains_inconclusive(self):
        result = inspect_output("cursor", "plain result\n", "", {"kind": "skill", "name": "review"})
        self.assertEqual(result["output"], "plain result")
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["invocation"], "unknown")
        self.assertFalse(result["tool_trace_complete"])

    def test_inherit_mode_accepts_missing_config_dir_and_does_not_claim_isolation(self):
        result = prepare(
            "codex",
            self.workspace,
            None,
            {},
            "inherit",
            {"kind": "plugin", "name": "demo"},
            "automatic",
            "Review this",
        )
        self.assertIsNone(result["unsupported"])
        self.assertNotIn("CODEX_HOME", result["env"])
        self.assertTrue(any("normal CODEX_HOME" in note for note in result["notes"]))

    def test_nested_untrusted_json_and_prefix_names_do_not_prove_invocation(self):
        # The object below is a tool argument in a Read call.  It must not be
        # treated as a second native Skill call, and "review-extra" must not
        # match the requested component "review".
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {
                                "untrusted_document": {
                                    "type": "tool_use",
                                    "name": "Skill",
                                    "input": {"skill": "review-extra", "description": "review"},
                                }
                            },
                        }
                    ]
                },
            }
        )
        result = inspect_output("claude-code", stdout, "", {"kind": "skill", "name": "review"})
        self.assertEqual(result["invocation"], "unknown")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "Read")

    def test_malformed_structured_stream_cannot_establish_invocation(self):
        stdout = json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "review"}}]}}
        ) + "\nnot-json"
        result = inspect_output("claude-code", stdout, "", {"kind": "skill", "name": "review"})
        self.assertEqual(result["invocation"], "unknown")
        self.assertFalse(result["tool_trace_complete"])


if __name__ == "__main__":
    unittest.main()
