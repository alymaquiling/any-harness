"""Regressions for native companion wiring and target-specific projections."""

import json
from pathlib import Path
import re
import tempfile
import unittest

import tomli
import yaml

from any_harness.build import generate
from any_harness.install import install
from any_harness.model import Error, load


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-native-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.write_source()

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, manifest_extra="", agent_targets=""):
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "plugin.yaml").write_text(
            """schema: 1
name: demo-plugin
version: 1.0.0
description: A test plugin
author:
  name: Test Author
"""
            + manifest_extra,
            encoding="utf-8",
        )
        skill = self.source / "skills" / "review" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            "---\nname: review\ndescription: Review changes\n---\n\nReview changes.\n",
            encoding="utf-8",
        )
        agent = self.source / "agents" / "reviewer.md"
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(
            "---\nname: reviewer\ndescription: Review changes\n"
            + agent_targets
            + "---\n\nReview changes.\n",
            encoding="utf-8",
        )

    @staticmethod
    def frontmatter(data):
        text = data.decode("utf-8")
        match = re.match(r"\A---\n(.*?)\n---", text, re.S)
        if match is None:
            raise AssertionError("generated Markdown has no frontmatter")
        return yaml.safe_load(match.group(1))

    def test_codex_manifest_wires_only_existing_companion_files(self):
        _plugin, tree, _notes = generate(self.source, ("codex",))
        manifest_path = "codex/plugins/demo-plugin/.codex-plugin/plugin.json"
        manifest = json.loads(tree[manifest_path][0])
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)

        companion = self.source / "native" / "codex" / "plugin"
        companion.mkdir(parents=True)
        (companion / ".mcp.json").write_text(
            '{"mcpServers":{"demo":{"command":"demo-server"}}}\n',
            encoding="utf-8",
        )
        _plugin, tree, notes = generate(self.source, ("codex",))
        manifest = json.loads(tree[manifest_path][0])
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertNotIn("apps", manifest)
        self.assertIn("codex/plugins/demo-plugin/.mcp.json", tree)
        self.assertTrue(any("mcpServers" in note for note in notes))

        (companion / ".app.json").write_text(
            '{"apps":{"demo":{"id":"demo"}}}\n', encoding="utf-8"
        )
        _plugin, tree, notes = generate(self.source, ("codex",))
        manifest = json.loads(tree[manifest_path][0])
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertEqual(manifest["apps"], "./.app.json")
        self.assertIn("codex/plugins/demo-plugin/.app.json", tree)
        self.assertTrue(any("apps" in note for note in notes))

    def test_claude_plugin_agent_drops_unsupported_fields_but_project_agent_keeps_them(self):
        self.write_source(
            agent_targets="""targets:
  claude-code:
    settings:
      hooks:
        - event: Stop
      mcpServers:
        demo:
          command: demo-server
      permissionMode: dontAsk
"""
        )
        _plugin, tree, notes = generate(self.source, ("claude-code",))
        package = self.frontmatter(
            tree["claude-code/plugins/demo-plugin/agents/reviewer.md"][0]
        )
        project = self.frontmatter(
            tree["claude-code/project/.claude/agents/reviewer.md"][0]
        )

        for key in ("hooks", "mcpServers", "permissionMode"):
            self.assertNotIn(key, package)
            self.assertIn(key, project)
        self.assertEqual(project["permissionMode"], "dontAsk")
        self.assertTrue(
            any(
                "reviewer" in note
                and "hooks" in note
                and "mcpServers" in note
                and "permissionMode" in note
                and "direct installation" in note
                for note in notes
            )
        )

    def test_repository_and_homepage_require_absolute_http_urls(self):
        for field, value in (
            ("repository", "github.com/example/plugin"),
            ("repository", "ftp://example.com/plugin"),
            ("repository", "https:///missing-host"),
            ("homepage", "/docs/plugin"),
            ("homepage", "mailto:team@example.com"),
        ):
            with self.subTest(field=field, value=value):
                self.write_source(f"{field}: {value}\n")
                with self.assertRaisesRegex(
                    Error, rf"{field} must be an absolute HTTP\(S\) URL"
                ):
                    load(self.source)

        for field in ("repository", "homepage"):
            for scheme in ("http", "https"):
                with self.subTest(field=field, scheme=scheme):
                    self.write_source(f"{field}: {scheme}://example.com/plugin\n")
                    load(self.source)

    def test_non_string_repository_is_rejected_before_url_parsing(self):
        self.write_source("repository: {nested: value}\n")
        with self.assertRaisesRegex(Error, "plugin.yaml: repository: expected a nonempty string"):
            load(self.source)

    def test_opencode_native_project_plugin_is_preserved_by_render_and_install(self):
        plugin = self.source / "native" / "opencode" / "project" / ".opencode" / "plugins" / "example.ts"
        plugin.parent.mkdir(parents=True)
        plugin.write_text(
            "export default async ({ project }) => ({ project })\n",
            encoding="utf-8",
        )

        _plugin, tree, _notes = generate(self.source, ("opencode",))
        relative = "opencode/project/.opencode/plugins/example.ts"
        expected = plugin.read_bytes()
        self.assertIn(relative, tree)
        self.assertEqual(tree[relative][0], expected)

        destination = self.root / "installed"
        actions, _notes = install(self.source, "opencode", destination)
        installed = destination / ".opencode" / "plugins" / "example.ts"
        self.assertIn(("write", ".opencode/plugins/example.ts"), actions)
        self.assertEqual(installed.read_bytes(), expected)

    def test_model_and_reasoning_settings_survive_each_native_projection(self):
        self.write_source(
            agent_targets="""targets:
  claude-code:
    settings:
      model: sonnet
      effort: xhigh
  codex:
    settings:
      model: gpt-5.6
      model_reasoning_effort: ultra
  opencode:
    settings:
      model: openai/gpt-5
      reasoningEffort: high
      # Synthetic nested provider option to verify passthrough; consult the
      # provider's documentation for a valid thinkingConfig shape.
      thinkingConfig:
        enabled: true
  cursor:
    settings:
      model: cursor-reviewer[effort=medium]
"""
        )
        harnesses = ("claude-code", "codex", "opencode", "cursor")
        _plugin, tree, _notes = generate(self.source, harnesses)

        generated = {}
        for harness in harnesses:
            path = f"{harness}/project/"
            agent_path = next(
                key
                for key in tree
                if key.startswith(path) and key.endswith("/agents/reviewer.md")
                or key.startswith(path) and key.endswith("/agents/reviewer.toml")
            )
            if agent_path.endswith(".toml"):
                generated[harness] = tomli.loads(tree[agent_path][0].decode("utf-8"))
            else:
                generated[harness] = self.frontmatter(tree[agent_path][0])

        self.assertEqual(
            {key: generated["claude-code"][key] for key in ("model", "effort")},
            {"model": "sonnet", "effort": "xhigh"},
        )
        self.assertEqual(
            {
                key: generated["codex"][key]
                for key in ("model", "model_reasoning_effort")
            },
            {"model": "gpt-5.6", "model_reasoning_effort": "ultra"},
        )
        self.assertEqual(
            {
                key: generated["opencode"][key]
                for key in ("model", "reasoningEffort", "thinkingConfig")
            },
            {
                "model": "openai/gpt-5",
                "reasoningEffort": "high",
                "thinkingConfig": {"enabled": True},
            },
        )
        self.assertEqual(
            generated["cursor"]["model"], "cursor-reviewer[effort=medium]"
        )

        # Distinct native keys and model IDs must remain scoped to their
        # target; a generated tree may contain all four adapters at once.
        for harness, forbidden in {
            "claude-code": ("model_reasoning_effort", "reasoningEffort", "thinkingConfig", "cursor-reviewer"),
            "codex": ("effort: xhigh", "reasoningEffort", "thinkingConfig", "cursor-reviewer"),
            "opencode": ("model_reasoning_effort", "effort: xhigh", "cursor-reviewer"),
            "cursor": ("model_reasoning_effort", "reasoningEffort", "thinkingConfig", "model: sonnet"),
        }.items():
            target_text = b"\n".join(
                data
                for path, (data, _mode) in tree.items()
                if path.startswith(f"{harness}/")
            ).decode("utf-8")
            for value in forbidden:
                self.assertNotIn(value, target_text, f"{value} leaked into {harness}")

        installed = {}
        for harness in harnesses:
            destination = self.root / f"installed-{harness}"
            actions, _notes = install(self.source, harness, destination)
            self.assertTrue(actions)
            project_agent = next(destination.rglob("reviewer.toml"), None)
            if project_agent is not None:
                installed[harness] = tomli.loads(project_agent.read_text(encoding="utf-8"))
            else:
                project_agent = next(destination.rglob("reviewer.md"))
                installed[harness] = self.frontmatter(project_agent.read_bytes())

        self.assertEqual(installed, generated)


if __name__ == "__main__":
    unittest.main()
