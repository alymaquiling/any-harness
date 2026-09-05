"""Lifecycle and safety tests for multi-plugin repository builds."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from any_harness.build import build
from any_harness.model import Error, HARNESSES
from any_harness.multi import MARKER, build_all


class MultiBuildTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="any-harness-multi-tests-")
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "plugin-repository"
        self.repository.mkdir()
        self.output = self.root / "generated" / "all"

    def tearDown(self):
        self.temporary.cleanup()

    def make_plugin(self, relative, plugin_name, *, warning_agent=False):
        source = self.repository / relative
        source.mkdir(parents=True, exist_ok=True)
        (source / "plugin.yaml").write_text(
            f"""schema: 1
name: {plugin_name}
version: 1.0.0
description: Description for {plugin_name}
author:
  name: Test Author
""",
            encoding="utf-8",
        )
        skill = source / "skills" / "getting-started" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            f"""---
name: getting-started
description: Get started with {plugin_name}.
---

Use {plugin_name} to complete the requested workflow.
""",
            encoding="utf-8",
        )
        if warning_agent:
            agent = source / "agents" / "reviewer.md"
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_text(
                """---
name: reviewer
description: Review the requested change.
targets:
  claude-code:
    settings:
      hooks: {}
  codex:
    settings:
      mcpServers: {}
---

Review the requested change and report concrete findings.
""",
                encoding="utf-8",
            )
        return source

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_recursive_discovery_combines_sorted_marketplaces_and_isolates_projects(self):
        zeta = self.make_plugin("nested/first", "zeta", warning_agent=True)
        alpha = self.make_plugin("nested/deeper/second", "alpha")
        self.make_plugin(".hidden/ignored", "hidden-plugin")
        self.make_plugin("vendor/ignored", "vendor-plugin")
        self.make_plugin("node_modules/ignored", "node-plugin")
        self.make_plugin("build/ignored", "build-plugin")
        self.make_plugin("dist/ignored", "dist-plugin")
        native = alpha / "native" / "claude-code" / "plugin" / "native.txt"
        native.parent.mkdir(parents=True, exist_ok=True)
        native.write_bytes(b"native content\n")
        linked = self.repository / "linked-zeta"
        try:
            linked.symlink_to(zeta, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        notes = build_all(
            self.repository,
            self.output,
            HARNESSES,
            marketplace_name="team-marketplace",
        )

        marker = self.read_json(self.output / MARKER)
        self.assertEqual(marker["schema"], 1)
        self.assertEqual(marker["type"], "multi-plugin")
        self.assertEqual(marker["name"], "team-marketplace")
        self.assertEqual(marker["plugins"], ["alpha", "zeta"])
        self.assertEqual(marker["harnesses"], sorted(HARNESSES))
        self.assertTrue(notes)
        self.assertTrue(all("project/" not in note for note in notes))

        for harness in HARNESSES:
            harness_root = self.output / harness
            self.assertTrue((harness_root / "INSTALL.md").is_file())
            self.assertTrue((harness_root / "compatibility.json").is_file())
            self.assertFalse((harness_root / "project").exists())
            self.assertTrue(
                (
                    harness_root
                    / "projects"
                    / "alpha"
                    / {"claude-code": ".claude", "codex": ".agents", "cursor": ".cursor", "opencode": ".opencode"}[harness]
                    / "skills"
                    / "getting-started"
                    / "SKILL.md"
                ).is_file()
            )
            compatibility = self.read_json(harness_root / "compatibility.json")
            self.assertEqual([item["name"] for item in compatibility["plugins"]], ["alpha", "zeta"])
            self.assertEqual(compatibility["name"], "team-marketplace")

            if harness == "opencode":
                self.assertFalse((harness_root / "plugins").exists())
                self.assertFalse((harness_root / ".opencode-plugin").exists())
            else:
                self.assertTrue((harness_root / "plugins" / "alpha").is_dir())
                self.assertTrue((harness_root / "plugins" / "zeta").is_dir())
                native_output = harness_root / "plugins" / "alpha" / "native.txt"
                if harness == "claude-code":
                    self.assertEqual(native_output.read_bytes(), b"native content\n")
                else:
                    self.assertFalse(native_output.exists())
                marketplace_path = {
                    "claude-code": ".claude-plugin/marketplace.json",
                    "codex": ".agents/plugins/marketplace.json",
                    "cursor": ".cursor-plugin/marketplace.json",
                }[harness]
                marketplace = self.read_json(harness_root / marketplace_path)
                self.assertEqual(marketplace["name"], "team-marketplace")
                self.assertEqual([item["name"] for item in marketplace["plugins"]], ["alpha", "zeta"])
                if harness in {"claude-code", "cursor"}:
                    self.assertEqual(marketplace["owner"], {"name": "team-marketplace"})

        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertNotIn(str(self.repository), path.read_text(encoding="utf-8", errors="ignore"))
        for path in (
            self.output / "claude-code" / "plugins" / "zeta" / "README.md",
            self.output / "claude-code" / "projects" / "zeta" / "INSTALL.md",
            self.output / "claude-code" / "projects" / "zeta" / "compatibility.json",
        ):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("project/", content)
            self.assertIn("projects/zeta/", content)

    def test_explicit_root_plugin_stops_nested_discovery_and_allows_default_dist_output(self):
        source = self.make_plugin(".", "root-plugin")
        self.make_plugin("nested-plugin", "nested-plugin")
        output = source / "dist" / "all"

        build_all(source, output, ("claude-code",))
        marketplace = self.read_json(output / "claude-code" / ".claude-plugin" / "marketplace.json")
        self.assertEqual([item["name"] for item in marketplace["plugins"]], ["root-plugin"])
        build_all(source, output, ("claude-code",), check=True)

    def test_default_marketplace_name_is_sanitized_and_override_is_strict(self):
        repository = self.root / "Repository With Spaces"
        repository.mkdir()
        self.repository = repository
        self.make_plugin("plugin", "alpha")
        output = self.root / "default-name"

        build_all(repository, output, ("claude-code",))
        self.assertEqual(self.read_json(output / MARKER)["name"], "repository-with-spaces")

        with self.assertRaisesRegex(Error, "marketplace name"):
            build_all(repository, self.root / "invalid-name", ("claude-code",), marketplace_name="Bad Name")

    def test_duplicate_plugin_names_fail_with_both_sources_before_writes(self):
        first = self.make_plugin("one", "duplicate")
        second = self.make_plugin("two", "duplicate")

        with self.assertRaisesRegex(Error, "Duplicate plugin name 'duplicate'") as raised:
            build_all(self.repository, self.output, ("claude-code",))
        self.assertIn(str(first), str(raised.exception))
        self.assertIn(str(second), str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_open_code_native_extension_limitation_fails_without_writing(self):
        source = self.make_plugin("plugin", "open-code-plugin")
        extension = source / "native" / "opencode" / "plugin" / "extension.js"
        extension.parent.mkdir(parents=True, exist_ok=True)
        extension.write_text("export default {};\n", encoding="utf-8")

        with self.assertRaisesRegex(Error, "OpenCode native extensions"):
            build_all(self.repository, self.output, ("opencode",))
        self.assertFalse(self.output.exists())

    def test_stale_plugins_harnesses_and_directories_are_removed(self):
        self.make_plugin("one", "alpha")
        beta = self.make_plugin("two", "beta")
        build_all(self.repository, self.output, ("claude-code", "cursor"), marketplace_name="team")
        (self.output / "claude-code" / "stale-empty").mkdir()
        shutil.rmtree(beta)

        build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")

        self.assertFalse((self.output / "cursor").exists())
        self.assertFalse((self.output / "claude-code" / "plugins" / "beta").exists())
        self.assertFalse((self.output / "claude-code" / "projects" / "beta").exists())
        self.assertFalse((self.output / "claude-code" / "stale-empty").exists())
        marker = self.read_json(self.output / MARKER)
        self.assertEqual(marker["plugins"], ["alpha"])
        self.assertEqual(marker["harnesses"], ["claude-code"])

    def test_rejects_malformed_and_single_plugin_ownership_markers(self):
        self.make_plugin("plugin", "alpha")
        self.output.mkdir(parents=True)
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        (self.output / MARKER).write_text(
            json.dumps({"schema": 1, "type": "multi-plugin", "name": "team", "plugins": ["alpha"]}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Error, "Invalid build-all ownership marker"):
            build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

        shutil.rmtree(self.output)
        build(self.repository / "plugin", self.output, ("claude-code",))
        with self.assertRaisesRegex(Error, "unowned output directory|Invalid build-all ownership marker"):
            build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        self.assertTrue((self.output / ".any-harness-build.json").is_file())

    def test_check_detects_bytes_modes_and_extra_directories_without_writing(self):
        self.make_plugin("plugin", "alpha")
        build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        marker = self.output / MARKER
        marker_bytes = marker.read_bytes()
        self.assertEqual(build_all(self.repository, self.output, ("claude-code",), check=True, marketplace_name="team"), [])
        self.assertEqual(marker.read_bytes(), marker_bytes)

        generated = self.output / "claude-code" / "plugins" / "alpha" / "skills" / "getting-started" / "SKILL.md"
        generated.write_bytes(generated.read_bytes() + b"drift\n")
        with self.assertRaisesRegex(Error, "stale"):
            build_all(self.repository, self.output, ("claude-code",), check=True, marketplace_name="team")

        build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        (self.output / "claude-code" / "extra-empty").mkdir()
        with self.assertRaisesRegex(Error, "stale"):
            build_all(self.repository, self.output, ("claude-code",), check=True, marketplace_name="team")

        build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        generated.chmod(0o755)
        with self.assertRaisesRegex(Error, "stale"):
            build_all(self.repository, self.output, ("claude-code",), check=True, marketplace_name="team")

    def test_custom_in_repo_output_and_marker_directories_are_excluded_from_discovery(self):
        self.make_plugin("source", "alpha")
        generated = self.repository / "old-generated"
        generated.mkdir()
        (generated / MARKER).write_text("not a source manifest\n", encoding="utf-8")
        (generated / "nested" / "plugin.yaml").parent.mkdir(parents=True)
        (generated / "nested" / "plugin.yaml").write_text("not valid plugin source\n", encoding="utf-8")
        custom_output = self.repository / "artifacts" / "combined"

        build_all(self.repository, custom_output, ("claude-code",), marketplace_name="team")
        generated_source = custom_output / "nested" / "plugin.yaml"
        generated_source.parent.mkdir(parents=True)
        generated_source.write_text("not a source manifest\n", encoding="utf-8")
        build_all(self.repository, custom_output, ("claude-code",), marketplace_name="team")

        marketplace = self.read_json(custom_output / "claude-code" / ".claude-plugin" / "marketplace.json")
        self.assertEqual([item["name"] for item in marketplace["plugins"]], ["alpha"])
        self.assertFalse(generated_source.exists())

    def test_multi_output_cannot_be_replaced_by_single_build_with_same_name(self):
        source = self.make_plugin("source", "same-name")
        build_all(self.repository, self.output, ("claude-code",), marketplace_name="same-name")

        with self.assertRaisesRegex(Error, "unowned output directory"):
            build(source, self.output, ("claude-code",))
        self.assertTrue((self.output / MARKER).is_file())
        self.assertFalse((self.output / ".any-harness-build.json").exists())

    def test_no_sources_and_malformed_sources_preserve_existing_output(self):
        source = self.make_plugin("source", "alpha")
        build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        marker_bytes = (self.output / MARKER).read_bytes()
        marketplace_bytes = (
            self.output / "claude-code" / ".claude-plugin" / "marketplace.json"
        ).read_bytes()

        shutil.rmtree(source)
        with self.assertRaisesRegex(Error, "No plugin sources"):
            build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        self.assertEqual((self.output / MARKER).read_bytes(), marker_bytes)
        self.assertEqual(
            (self.output / "claude-code" / ".claude-plugin" / "marketplace.json").read_bytes(),
            marketplace_bytes,
        )

        malformed = self.repository / "malformed"
        malformed.mkdir()
        (malformed / "plugin.yaml").write_text("schema: 1\nname: malformed\n", encoding="utf-8")
        with self.assertRaises(Error):
            build_all(self.repository, self.output, ("claude-code",), marketplace_name="team")
        self.assertEqual((self.output / MARKER).read_bytes(), marker_bytes)
        self.assertEqual(
            (self.output / "claude-code" / ".claude-plugin" / "marketplace.json").read_bytes(),
            marketplace_bytes,
        )

    def test_rejects_symlink_and_source_overlapping_outputs(self):
        source = self.make_plugin("plugin", "alpha")
        symlink_target = self.root / "symlink-target"
        symlink_target.mkdir()
        symlink_output = self.root / "symlink-output"
        try:
            symlink_output.symlink_to(symlink_target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        with self.assertRaisesRegex(Error, "symlink"):
            build_all(self.repository, symlink_output, ("claude-code",))
        with self.assertRaisesRegex(Error, "source components|separate from plugin sources"):
            build_all(self.repository, source / "skills" / "generated", ("claude-code",))


if __name__ == "__main__":
    unittest.main()
