"""Stdlib tests for the shared model and deterministic target builders."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

import tomli
import yaml

from any_harness.build import build, generate
from any_harness.model import Error, HARNESSES, load


class BuildTestCase(unittest.TestCase):
    """Create small, self-contained source plugins for each test."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="any-harness-tests-")
        # macOS exposes /var as a symlink to /private/var; canonicalize the
        # fixture root so source/output safety checks exercise only paths in
        # the fixture tree.
        self.root = Path(self.tempdir.name).resolve()
        self.source = self.root / "source"

    def tearDown(self):
        self.tempdir.cleanup()

    def add_file(self, relative, content=b"", mode=None):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        if mode is not None:
            path.chmod(mode)
        return path

    def add_plugin(
        self,
        *,
        skill_body="Review the current changes.",
        skill_targets=None,
        agent_body="Inspect the repository and report findings.",
        agent_targets=None,
        include_agent=True,
        include_resources=True,
        native=True,
    ):
        self.add_file(
            "plugin.yaml",
            """schema: 1
name: demo-plugin
version: 1.2.3
description: A test plugin
author:
  name: Test Author
license: MIT
repository: https://example.test/demo-plugin
keywords:
  - testing
  - adapters
""",
        )

        skill_targets = skill_targets or {
            "claude-code": {"settings": {"claude_option": "only-claude"}},
            "codex": {
                "settings": {"codex_option": "only-codex"},
                "openai": {"display_name": "Demo review", "short_description": "Review code"},
            },
            "opencode": {"settings": {"opencode_option": "only-opencode"}},
            "cursor": {"settings": {"cursor_option": "only-cursor"}},
        }
        self.add_file(
            "skills/review/SKILL.md",
            """---
name: review
description: Review code for bugs and regressions.
license: MIT
compatibility: Requires a Git repository.
metadata:
  source: test
targets:
"""
            + self.indent_yaml(skill_targets)
            + "---\n\n"
            + skill_body
            + "\n",
        )

        if include_resources:
            self.add_file("skills/review/reference.md", "Additional review guidance.\n")
            self.add_file("skills/review/scripts/check.sh", "#!/bin/sh\nprintf checked\\n\n", mode=0o751)
            self.add_file("skills/review/assets/example.txt", "example asset\n", mode=0o640)

        if include_agent:
            agent_targets = agent_targets or {
                "claude-code": {"settings": {"claude_agent_option": "only-claude-agent"}},
                "codex": {"settings": {"model": "gpt-5", "codex_agent_option": "only-codex-agent"}},
                "opencode": {"settings": {"model": "openai/gpt-5", "opencode_agent_option": "only-opencode-agent"}},
                "cursor": {"settings": {"cursor_agent_option": "only-cursor-agent"}},
            }
            self.add_file(
                "agents/reviewer.md",
                """---
name: reviewer
description: Inspect changes and report actionable findings.
targets:
"""
                + self.indent_yaml(agent_targets)
                + "---\n\n"
                + agent_body
                + "\n",
            )

        if native:
            markers = {
                "claude-code": "native claude content\n",
                "codex": "native codex content\n",
                "opencode": "native opencode content\n",
                "cursor": "native cursor content\n",
            }
            project_roots = {
                "claude-code": ".claude",
                "codex": ".codex",
                "opencode": ".opencode",
                "cursor": ".cursor",
            }
            for harness, marker in markers.items():
                # OpenCode's executable plugin area is intentionally rejected
                # by the renderer; its native project extensions live under
                # .opencode/ instead.
                if harness != "opencode":
                    self.add_file(f"native/{harness}/plugin/{harness}-plugin.txt", marker)
                self.add_file(
                    f"native/{harness}/project/{project_roots[harness]}/{harness}-project.txt",
                    marker,
                )
        return self.source

    @staticmethod
    def indent_yaml(value):
        text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
        return "".join("  " + line if line.strip() else line for line in text.splitlines(True))

    def snapshot(self, root):
        root = Path(root)
        result = {}
        if not root.exists():
            return result
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                result[path.relative_to(root).as_posix()] = ("symlink", os.readlink(path))
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = (
                    path.read_bytes(),
                    stat.S_IMODE(path.stat().st_mode),
                )
        return result

    @staticmethod
    def tree_text(tree):
        return b"\n".join(data for data, _mode in tree.values())


class ModelValidationTests(BuildTestCase):
    def test_duplicate_yaml_keys_are_rejected_in_plugin_and_frontmatter(self):
        self.add_file(
            "plugin.yaml",
            """schema: 1
name: demo-plugin
name: duplicate-plugin
version: 1.0.0
description: Test
author:
  name: Author
""",
        )
        with self.assertRaisesRegex(Error, "Duplicate YAML key: name"):
            load(self.source)

        self.add_plugin(include_agent=False, include_resources=False, native=False)
        skill = self.source / "skills/review/SKILL.md"
        skill.write_text(
            """---
name: review
description: First description
description: Duplicate description
---

Body
""",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Error, "Duplicate YAML key: description"):
            load(self.source)

    def test_unsafe_or_unknown_frontmatter_is_rejected_without_execution(self):
        marker = self.root / "should-not-exist"
        self.add_plugin(include_agent=False, include_resources=False, native=False)
        skill = self.source / "skills/review/SKILL.md"
        skill.write_text(
            f"""---
name: review
description: Safe description
unsafe_field: !!python/object/apply:os.system [\"touch {marker}\"]
---

Body
""",
            encoding="utf-8",
        )
        with self.assertRaises(Error):
            load(self.source)
        self.assertFalse(marker.exists(), "SafeLoader must not execute YAML tags")

    def test_target_settings_cannot_override_generator_owned_fields(self):
        self.add_plugin(include_agent=False, include_resources=False, native=False)
        skill = self.source / "skills/review/SKILL.md"
        skill.write_text(
            """---
name: review
description: Review code
targets:
  claude-code:
    settings:
      name: attacker-controlled-name
---

Body
""",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Error, "managed by the generator"):
            load(self.source)


class RenderingTests(BuildTestCase):
    def test_each_target_gets_native_output_without_other_target_configuration(self):
        self.add_plugin()
        native_markers = {
            "claude-code": "native claude content",
            "codex": "native codex content",
            "opencode": "native opencode content",
            "cursor": "native cursor content",
        }
        target_markers = {
            "claude-code": ["only-claude", "only-claude-agent"],
            "codex": ["only-codex", "only-codex-agent"],
            "opencode": ["only-opencode", "only-opencode-agent"],
            "cursor": ["only-cursor", "only-cursor-agent"],
        }
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                _plugin, tree, _notes = generate(self.source, [harness])
                text = self.tree_text(tree).decode("utf-8")
                self.assertNotIn("targets:", text)
                self.assertIn(native_markers[harness], text)
                for other, marker in native_markers.items():
                    if other != harness:
                        self.assertNotIn(marker, text)
                for marker in target_markers[harness]:
                    self.assertIn(marker, text)
                for other, markers in target_markers.items():
                    if other != harness:
                        for marker in markers:
                            self.assertNotIn(marker, text)

                # Target settings are rendered in both the package and
                # project component for that target.
                self.assertTrue(any(path.endswith("skills/review/SKILL.md") for path in tree))

    def test_target_output_has_expected_native_layouts(self):
        self.add_plugin()
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                _plugin, tree, _notes = generate(self.source, [harness])
                paths = set(tree)
                if harness == "opencode":
                    self.assertIn(f"{harness}/project/.opencode/skills/review/SKILL.md", paths)
                    self.assertIn(f"{harness}/project/.opencode/agents/reviewer.md", paths)
                    self.assertNotIn(f"{harness}/plugins/demo-plugin/skills/review/SKILL.md", paths)
                else:
                    self.assertIn(f"{harness}/plugins/demo-plugin/skills/review/SKILL.md", paths)
                    marker = {
                        "claude-code": ".claude-plugin",
                        "codex": ".codex-plugin",
                        "cursor": ".cursor-plugin",
                    }[harness]
                    self.assertIn(f"{harness}/plugins/demo-plugin/{marker}/plugin.json", paths)
                if harness == "claude-code":
                    self.assertIn(f"{harness}/project/.claude/skills/review/SKILL.md", paths)
                    self.assertIn(f"{harness}/plugins/demo-plugin/agents/reviewer.md", paths)
                elif harness == "codex":
                    self.assertIn(f"{harness}/project/.codex/agents/reviewer.toml", paths)
                    self.assertIn(f"{harness}/project/.agents/skills/review/SKILL.md", paths)
                    self.assertIn(f"{harness}/project/.agents/skills/review/agents/openai.yaml", paths)
                elif harness == "cursor":
                    self.assertIn(f"{harness}/project/.cursor/skills/review/SKILL.md", paths)
                    self.assertIn(f"{harness}/plugins/demo-plugin/agents/reviewer.md", paths)

    def test_codex_agent_is_valid_toml_with_developer_instructions(self):
        self.add_plugin()
        _plugin, tree, _notes = generate(self.source, ["codex"])
        raw = tree["codex/project/.codex/agents/reviewer.toml"][0]
        parsed = tomli.loads(raw.decode("utf-8"))
        self.assertEqual(parsed["name"], "reviewer")
        self.assertEqual(parsed["description"], "Inspect changes and report actionable findings.")
        self.assertEqual(parsed["model"], "gpt-5")
        self.assertEqual(parsed["codex_agent_option"], "only-codex-agent")
        self.assertEqual(parsed["developer_instructions"], "Inspect the repository and report findings.\n")
        self.assertNotIn("targets", parsed)

    def test_skill_resources_preserve_executable_bit_and_regular_mode(self):
        self.add_plugin(include_agent=False, native=False)
        _plugin, tree, _notes = generate(self.source, ["claude-code"])
        executable = tree["claude-code/plugins/demo-plugin/skills/review/scripts/check.sh"]
        regular = tree["claude-code/plugins/demo-plugin/skills/review/assets/example.txt"]
        self.assertEqual(executable[1], 0o755)
        self.assertEqual(regular[1], 0o644)

        output = self.root / "built"
        build(self.source, output, ["claude-code"])
        self.assertEqual(
            stat.S_IMODE(
                (output / "claude-code/plugins/demo-plugin/skills/review/scripts/check.sh").stat().st_mode
            ),
            0o755,
        )
        self.assertEqual(
            stat.S_IMODE(
                (output / "claude-code/plugins/demo-plugin/skills/review/assets/example.txt").stat().st_mode
            ),
            0o644,
        )


class BuildLifecycleTests(BuildTestCase):
    def test_build_is_deterministic_checkable_and_removes_stale_files(self):
        self.add_plugin()
        output = self.root / "dist"
        build(self.source, output, ["claude-code", "codex"])
        first = self.snapshot(output)
        self.assertTrue(first)

        # A second build produces byte-for-byte and mode-for-mode identical
        # output, and check mode accepts the committed result.
        build(self.source, output, ["claude-code", "codex"])
        self.assertEqual(first, self.snapshot(output))
        build(self.source, output, ["claude-code", "codex"], check=True)

        stale = output / "stale.txt"
        stale.write_text("stale\n", encoding="utf-8")
        with self.assertRaisesRegex(Error, "missing or stale"):
            build(self.source, output, ["claude-code", "codex"], check=True)
        build(self.source, output, ["claude-code", "codex"])
        self.assertFalse(stale.exists())
        self.assertEqual(first, self.snapshot(output))

        # Source changes make check mode fail until a rebuild, then the new
        # body is present in all relevant generated components.
        skill = self.source / "skills/review/SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace(
                "Review the current changes.", "Review the newly changed files."
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Error, "missing or stale"):
            build(self.source, output, ["claude-code", "codex"], check=True)
        build(self.source, output, ["claude-code", "codex"])
        self.assertIn(
            "newly changed files",
            (output / "claude-code/plugins/demo-plugin/skills/review/SKILL.md").read_text(
                encoding="utf-8"
            ),
        )

    def test_unowned_output_is_refused_and_owned_output_is_replaced(self):
        self.add_plugin(include_agent=False, native=False)
        output = self.root / "dist"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("user data\n", encoding="utf-8")
        with self.assertRaisesRegex(Error, "unowned output directory"):
            build(self.source, output, ["claude-code"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user data\n")

        marker = output / ".any-harness-build.json"
        marker.write_text(
            json.dumps({"schema": 1, "name": "demo-plugin", "harnesses": ["claude-code"]})
            + "\n",
            encoding="utf-8",
        )
        build(self.source, output, ["claude-code"])
        self.assertFalse(sentinel.exists())
        self.assertTrue((output / "claude-code/INSTALL.md").exists())

    def test_symlinked_output_marker_is_not_accepted_as_ownership(self):
        self.add_plugin(include_agent=False, native=False)
        output = self.root / "dist"
        output.mkdir()
        target = self.root / "marker-target"
        target.write_text("not an ownership marker\n", encoding="utf-8")
        (output / ".any-harness-build.json").symlink_to(target)
        with self.assertRaisesRegex(Error, "unowned output directory"):
            build(self.source, output, ["claude-code"])
        self.assertTrue(target.exists())

    def test_source_and_component_paths_are_protected(self):
        self.add_plugin(include_agent=False, native=False)
        before = self.snapshot(self.source)

        for output in (
            self.source,
            self.source / "skills",
            self.source / "agents",
            self.source / "native",
            self.source.parent,
        ):
            with self.subTest(output=output):
                with self.assertRaises(Error):
                    build(self.source, output, ["claude-code"])
                self.assertEqual(before, self.snapshot(self.source))

        # A symlink destination must never be replaced, even when it points to
        # an otherwise valid directory outside the source tree.
        real_output = self.root / "real-output"
        real_output.mkdir()
        link_output = self.root / "linked-output"
        link_output.symlink_to(real_output, target_is_directory=True)
        with self.assertRaisesRegex(Error, "separate from source"):
            build(self.source, link_output, ["claude-code"])
        self.assertEqual(before, self.snapshot(self.source))

    def test_output_inside_source_root_is_supported_without_mutating_source(self):
        self.add_plugin(include_agent=False, native=False)
        before = self.snapshot(self.source)
        output = self.source / "generated"
        build(self.source, output, ["claude-code"])
        self.assertTrue((output / "claude-code/INSTALL.md").is_file())
        after = self.snapshot(self.source)
        for relative, entry in before.items():
            self.assertEqual(after[relative], entry)


if __name__ == "__main__":
    unittest.main()
