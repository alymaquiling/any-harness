import json
from pathlib import Path
import tempfile
import unittest

from any_harness.build import build, generate
from any_harness.cli import scaffold
from any_harness.install import install, source_folder
from any_harness.model import Error


class GuardrailsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        scaffold(self.source, "demo")

    def tearDown(self):
        self.temp.cleanup()

    def test_build_refuses_symlinked_parent(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.source / "link"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(Error, "parents may not be symlinks"):
            build(self.source, link / "output")
        self.assertEqual(list(outside.iterdir()), [])

    def test_build_requires_valid_ownership_before_replacing(self):
        output = self.root / "output"
        output.mkdir()
        sentinel = output / "valuable.txt"
        sentinel.write_text("keep me")
        marker = output / ".any-harness-build.json"
        for content in ({}, {"schema": 1, "name": "other", "harnesses": ["codex"]}):
            marker.write_text(json.dumps(content))
            with self.assertRaisesRegex(Error, "ownership marker"):
                build(self.source, output)
            self.assertEqual(sentinel.read_text(), "keep me")

    def test_check_detects_extra_empty_directory(self):
        output = self.root / "output"
        build(self.source, output)
        (output / "unexpected").mkdir()
        with self.assertRaisesRegex(Error, "stale"):
            build(self.source, output, check=True)

    def test_targets_cannot_be_reintroduced_through_native_settings(self):
        skill = self.source / "skills/getting-started/SKILL.md"
        skill.write_text("---\nname: getting-started\ndescription: Example\ntargets:\n  codex:\n    settings:\n      targets: {cursor: {enabled: false}}\n---\n\nInstructions\n")
        with self.assertRaisesRegex(Error, "managed by the generator"):
            generate(self.source)

    def test_os_temp_aliases_work_for_source_and_destination(self):
        # On macOS this may begin with /var rather than /private/var.
        logical_root = Path(self.temp.name)
        with source_folder(str(logical_root), subdir="source") as (folder, _):
            self.assertEqual(folder, self.source)
        install(self.source, "cursor", logical_root / "project")
        self.assertTrue((self.root / "project/.cursor/skills/getting-started/SKILL.md").is_file())

    def test_cursor_manifest_and_marketplace_use_restricted_fields(self):
        manifest = self.source / "plugin.yaml"
        manifest.write_text(manifest.read_text().replace("  name: Plugin author", "  name: Plugin author\n  url: https://example.com"))
        _, tree, _ = generate(self.source)
        plugin = json.loads(tree["cursor/plugins/demo/.cursor-plugin/plugin.json"][0])
        marketplace = json.loads(tree["cursor/.cursor-plugin/marketplace.json"][0])
        self.assertNotIn("url", plugin["author"])
        self.assertNotIn("url", marketplace["owner"])
        self.assertNotIn("version", marketplace["plugins"][0])


if __name__ == "__main__":
    unittest.main()
