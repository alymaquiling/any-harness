"""Focused validation and generated-path collision regressions."""

from pathlib import Path
import tempfile
import unittest

from any_harness.build import generate, put
from any_harness.cli import add, scaffold
from any_harness.model import Error


class AuthoringValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-validation-")
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_scaffold_rejects_invalid_description_before_creating_destination(self):
        for index, description in enumerate(("", "   ", "x" * 1025)):
            destination = self.root / f"plugin-{index}"
            with self.assertRaises(Error):
                scaffold(destination, "demo", description=description)
            self.assertFalse(destination.exists())

    def test_scaffold_rejects_empty_author_before_creating_destination(self):
        destination = self.root / "plugin"
        with self.assertRaisesRegex(Error, "author name: expected a nonempty string"):
            scaffold(destination, "demo", author=" ")
        self.assertFalse(destination.exists())

    def test_add_rejects_invalid_description_without_mutating_source(self):
        source = self.root / "source"
        scaffold(source, "demo")
        before = sorted(
            path.relative_to(source).as_posix() for path in source.rglob("*")
        )
        for description in ("", "x" * 1025):
            with self.assertRaises(Error):
                add(source, "skill", "new-skill", description)
            after = sorted(
                path.relative_to(source).as_posix() for path in source.rglob("*")
            )
            self.assertEqual(after, before)
            self.assertFalse((source / "skills" / "new-skill").exists())

    def test_put_rejects_file_directory_ancestor_collisions(self):
        for first, second in (
            (
                "project/.claude/skills/review",
                "project/.claude/skills/review/SKILL.md",
            ),
            (
                "project/.claude/skills/review/SKILL.md",
                "project/.claude/skills/review",
            ),
        ):
            tree = {}
            put(tree, first, b"first")
            with self.assertRaisesRegex(Error, "Generated file collision"):
                put(tree, second, b"second")
            self.assertEqual(tree, {first: (b"first", 0o644)})

    def test_validate_rejects_native_file_that_occupies_generated_directory(self):
        source = self.root / "source"
        skill = source / "skills" / "review"
        native = (
            source
            / "native"
            / "claude-code"
            / "project"
            / ".claude"
            / "skills"
        )
        skill.mkdir(parents=True)
        native.mkdir(parents=True)
        (source / "plugin.yaml").write_text(
            """schema: 1
name: demo
version: 1.0.0
description: A test plugin
author:
  name: Test Author
""",
            encoding="utf-8",
        )
        (skill / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review changes\n---\n\nReview.\n",
            encoding="utf-8",
        )
        (native / "review").write_text("native file\n", encoding="utf-8")

        with self.assertRaisesRegex(Error, "Generated file collision"):
            generate(source, ("claude-code",))


if __name__ == "__main__":
    unittest.main()
