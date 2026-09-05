import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import any_harness.install as install_module
from any_harness.install import apply, install, source_folder
from any_harness.model import Error


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="any-harness-test-")
        # macOS exposes /var as a symlink to /private/var.  Use the canonical
        # temp path so tests exercise paths inside the install root, not that
        # host-level compatibility symlink.
        self.workspace = Path(self.temp.name).resolve()
        self.source = self.workspace / "source"
        self.root = self.workspace / "project"
        self._write_source()

    def tearDown(self):
        self.temp.cleanup()

    def _write_source(
        self,
        name="demo",
        version="1.0.0",
        skill_body="Use the greeting workflow.",
        agent_body="Review the current changes.",
        include_resource=True,
        skill_name="greet",
        agent_name="reviewer",
    ):
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / "plugin.yaml").write_text(
            "\n".join(
                [
                    "schema: 1",
                    f"name: {name}",
                    f"version: {version}",
                    "description: A test plugin",
                    "author:",
                    "  name: Test Author",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        skill_dir = self.source / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    f"name: {skill_name}",
                    "description: A greeting skill",
                    "---",
                    "",
                    skill_body,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        if include_resource:
            references = skill_dir / "references"
            references.mkdir(parents=True, exist_ok=True)
            (references / "guide.md").write_text(
                "Use the reference while applying the skill.\n", encoding="utf-8"
            )
        agent_dir = self.source / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / f"{agent_name}.md").write_text(
            "\n".join(
                [
                    "---",
                    f"name: {agent_name}",
                    "description: A review agent",
                    "---",
                    "",
                    agent_body,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _receipt(self, name="demo"):
        return self.root / ".any-harness" / "installed" / f"opencode-{name}.json"

    def _snapshot(self, root=None):
        root = Path(root or self.root)
        if not root.exists():
            return {}
        result = {}
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            directory = Path(directory)
            for child_name in sorted(dirnames + filenames):
                child = directory / child_name
                relative = child.relative_to(root).as_posix()
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode):
                    result[relative] = ("symlink", os.readlink(child))
                elif stat.S_ISREG(info.st_mode):
                    result[relative] = (
                        "file",
                        child.read_bytes(),
                        stat.S_IMODE(info.st_mode),
                    )
                elif stat.S_ISDIR(info.st_mode):
                    result[relative] = ("directory",)
                else:
                    result[relative] = ("other", stat.S_IFMT(info.st_mode))
        return result

    def test_install_dry_run_reports_writes_without_mutating_root(self):
        actions, notes = install(self.source, "opencode", self.root, dry_run=True)

        self.assertTrue(actions)
        self.assertTrue(all(action == "write" for action, _ in actions))
        self.assertEqual(len(notes), 1)
        self.assertIn("native skills and agent directories", notes[0])
        self.assertFalse(self.root.exists())

    def test_initial_install_writes_native_opencode_files_and_receipt(self):
        actions, notes = install(self.source, "opencode", self.root)

        expected = {
            ".opencode/agents/reviewer.md",
            ".opencode/skills/greet/SKILL.md",
            ".opencode/skills/greet/references/guide.md",
        }
        self.assertEqual({relative for _, relative in actions}, expected)
        self.assertEqual(len(notes), 1)
        self.assertIn("native skills and agent directories", notes[0])
        for relative in expected:
            self.assertTrue((self.root / relative).is_file(), relative)
        self.assertTrue(self._receipt().is_file())
        self.assertFalse((self.root / ".any-harness" / "install.lock").exists())

        receipt = json.loads(self._receipt().read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], 1)
        self.assertEqual(receipt["name"], "demo")
        self.assertEqual(receipt["harness"], "opencode")
        self.assertEqual(set(receipt["files"]), expected)
        self.assertEqual(receipt["source"]["version"], "1.0.0")

    def test_idempotent_install_has_no_actions_or_content_changes(self):
        install(self.source, "opencode", self.root)
        before = self._snapshot()
        receipt_before = self._receipt().read_bytes()

        actions, _ = install(self.source, "opencode", self.root)

        self.assertEqual(actions, [])
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._receipt().read_bytes(), receipt_before)

    def test_update_replaces_changed_managed_file_and_refreshes_receipt(self):
        install(self.source, "opencode", self.root)
        skill = self.source / "skills" / "greet" / "SKILL.md"
        skill.write_text(
            "\n".join(
                [
                    "---",
                    "name: greet",
                    "description: A greeting skill",
                    "---",
                    "",
                    "Use the updated greeting workflow.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (self.source / "plugin.yaml").write_text(
            "\n".join(
                [
                    "schema: 1",
                    "name: demo",
                    "version: 1.1.0",
                    "description: A test plugin",
                    "author:",
                    "  name: Test Author",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        actions, _ = install(self.source, "opencode", self.root)

        self.assertEqual(actions, [("write", ".opencode/skills/greet/SKILL.md")])
        self.assertIn(
            b"updated greeting workflow",
            (self.root / ".opencode/skills/greet/SKILL.md").read_bytes(),
        )
        receipt = json.loads(self._receipt().read_text(encoding="utf-8"))
        self.assertEqual(receipt["source"]["version"], "1.1.0")

    def test_stale_missing_managed_file_is_recreated(self):
        install(self.source, "opencode", self.root)
        missing = self.root / ".opencode/agents/reviewer.md"
        missing.unlink()

        actions, _ = install(self.source, "opencode", self.root)

        self.assertEqual(actions, [("write", ".opencode/agents/reviewer.md")])
        self.assertTrue(missing.is_file())

    def test_removed_source_resource_is_removed_as_stale_owned_file(self):
        install(self.source, "opencode", self.root)
        (self.source / "skills" / "greet" / "references" / "guide.md").unlink()

        actions, _ = install(self.source, "opencode", self.root)

        self.assertEqual(
            actions, [("remove", ".opencode/skills/greet/references/guide.md")]
        )
        self.assertFalse(
            (self.root / ".opencode/skills/greet/references/guide.md").exists()
        )
        receipt = json.loads(self._receipt().read_text(encoding="utf-8"))
        self.assertNotIn(".opencode/skills/greet/references/guide.md", receipt["files"])

    def test_uninstall_removes_unchanged_owned_files_and_receipt(self):
        install(self.source, "opencode", self.root)
        owned = set(
            json.loads(self._receipt().read_text(encoding="utf-8"))["files"]
        )

        actions = apply(
            self.root,
            "demo",
            "opencode",
            {},
            {},
            uninstall=True,
        )

        self.assertEqual({relative for _, relative in actions}, owned)
        for relative in owned:
            self.assertFalse((self.root / relative).exists(), relative)
        self.assertFalse(self._receipt().exists())

    def test_unmanaged_collision_is_refused_without_partial_changes(self):
        collision = self.root / ".opencode/skills/greet/SKILL.md"
        collision.parent.mkdir(parents=True)
        collision.write_text("user-owned\n", encoding="utf-8")
        before = self._snapshot()

        with self.assertRaisesRegex(Error, "unmanaged file"):
            install(self.source, "opencode", self.root)

        self.assertEqual(self._snapshot(), before)
        self.assertFalse(self._receipt().exists())

    def test_user_modification_refuses_update_without_partial_changes(self):
        install(self.source, "opencode", self.root)
        changed_agent = self.source / "agents" / "reviewer.md"
        changed_agent.write_text(
            "\n".join(
                [
                    "---",
                    "name: reviewer",
                    "description: A review agent",
                    "---",
                    "",
                    "Review with the updated policy.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        installed_skill = self.root / ".opencode/skills/greet/SKILL.md"
        installed_skill.write_text("local customization\n", encoding="utf-8")
        before = self._snapshot()
        receipt_before = self._receipt().read_bytes()

        with self.assertRaisesRegex(Error, "Locally modified installed file"):
            install(self.source, "opencode", self.root)

        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._receipt().read_bytes(), receipt_before)
        self.assertEqual(installed_skill.read_text(encoding="utf-8"), "local customization\n")

    def test_user_modification_refuses_uninstall_without_partial_changes(self):
        install(self.source, "opencode", self.root)
        installed_skill = self.root / ".opencode/skills/greet/SKILL.md"
        installed_skill.write_text("local customization\n", encoding="utf-8")
        before = self._snapshot()
        receipt_before = self._receipt().read_bytes()

        with self.assertRaisesRegex(Error, "Locally modified installed file"):
            apply(
                self.root,
                "demo",
                "opencode",
                {},
                {},
                uninstall=True,
            )

        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._receipt().read_bytes(), receipt_before)

    def test_symlinked_install_parent_is_refused_without_external_write(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        opencode = self.root / ".opencode"
        opencode.parent.mkdir(parents=True)
        opencode.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(Error, "symlinked install path"):
            install(self.source, "opencode", self.root)

        self.assertEqual(self._snapshot(outside), {})
        self.assertFalse(self._receipt().exists())

    def test_symlinked_source_resource_is_refused(self):
        outside = self.workspace / "secret.md"
        outside.write_text("outside source\n", encoding="utf-8")
        link = self.source / "skills" / "greet" / "references" / "secret.md"
        link.symlink_to(outside)

        with self.assertRaisesRegex(Error, "Symlinks are not supported"):
            install(self.source, "opencode", self.root)

        self.assertFalse(self.root.exists())

    def test_competing_plugin_ownership_is_refused_even_for_same_path(self):
        first = self.source
        first_actions, _ = install(first, "opencode", self.root)
        self.assertTrue(first_actions)
        before = self._snapshot()
        receipt_before = self._receipt().read_bytes()

        second = self.workspace / "second"
        self.source = second
        # The native bytes are intentionally identical; ownership must still
        # prevent one plugin from claiming another plugin's paths.
        self._write_source(name="other", skill_body="Use the greeting workflow.")
        with self.assertRaisesRegex(Error, "Files already owned by opencode-demo"):
            install(second, "opencode", self.root)

        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._receipt().read_bytes(), receipt_before)
        self.assertEqual(
            len(list((self.root / ".any-harness/installed").glob("*.json"))), 1
        )

    @unittest.skipUnless(shutil.which("git"), "git is required for Git source tests")
    def test_source_folder_clones_pins_and_selects_subdir_without_network(self):
        repository = self.workspace / "repository"
        plugin = repository / "packages" / "plugin"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(
            "\n".join(
                [
                    "schema: 1",
                    "name: git-demo",
                    "version: 1.0.0",
                    "description: A Git test plugin",
                    "author:",
                    "  name: Git Test",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        skill = plugin / "skills" / "hello"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: hello\ndescription: Git skill\n---\n\nHello from Git.\n",
            encoding="utf-8",
        )

        def run_git(*args):
            return subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                text=True,
            )

        run_git("init", "--quiet", str(repository))
        run_git("-C", str(repository), "config", "user.email", "test@example.invalid")
        run_git("-C", str(repository), "config", "user.name", "Git Test")
        run_git("-C", str(repository), "add", ".")
        run_git("-C", str(repository), "commit", "--quiet", "-m", "initial")
        commit = run_git(
            "-C", str(repository), "rev-parse", "HEAD"
        ).stdout.strip()
        remote = "https://example.invalid/acme/git-demo.git"

        real_git = install_module.git

        def fake_git(*args, cwd=None):
            if args and args[0] == "clone":
                self.assertEqual(args[3], remote)
                return real_git(
                    "clone",
                    "--no-checkout",
                    "--",
                    str(repository),
                    args[4],
                    cwd=cwd,
                )
            return real_git(*args, cwd=cwd)

        with patch.object(install_module, "git", side_effect=fake_git):
            with source_folder(
                "git+" + remote,
                ref=commit,
                subdir="packages/plugin",
            ) as (folder, provenance):
                self.assertTrue((folder / "plugin.yaml").is_file())
                self.assertEqual(provenance["git"], remote)
                self.assertEqual(provenance["commit"], commit)
                self.assertEqual(provenance["subdir"], "packages/plugin")
                actions, _ = install(folder, "opencode", self.root, provenance=provenance)
                self.assertTrue(actions)

        self.assertFalse(folder.exists())
        self.assertTrue((self.root / ".opencode/skills/hello/SKILL.md").is_file())

    @unittest.skipUnless(shutil.which("git"), "git is required for Git source tests")
    def test_source_folder_selects_nondefault_branch_from_remote_tracking_ref(self):
        repository = self.workspace / "branch-repository"
        plugin = repository / "packages" / "plugin"
        skill = plugin / "skills" / "hello"
        skill.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text(
            "\n".join(
                [
                    "schema: 1",
                    "name: branch-demo",
                    "version: 1.0.0",
                    "description: A branch test plugin",
                    "author:",
                    "  name: Git Test",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        skill_file = skill / "SKILL.md"
        skill_file.write_text(
            "---\nname: hello\ndescription: Git skill\n---\n\nDefault branch.\n",
            encoding="utf-8",
        )

        def run_git(*args):
            return subprocess.run(
                ["git", *args],
                check=True,
                capture_output=True,
                text=True,
            )

        run_git("init", "--quiet", str(repository))
        run_git("-C", str(repository), "config", "user.email", "test@example.invalid")
        run_git("-C", str(repository), "config", "user.name", "Git Test")
        run_git("-C", str(repository), "add", ".")
        run_git("-C", str(repository), "commit", "--quiet", "-m", "default")
        default_branch = run_git(
            "-C", str(repository), "symbolic-ref", "--short", "HEAD"
        ).stdout.strip()
        run_git("-C", str(repository), "checkout", "--quiet", "-b", "feature")
        skill_file.write_text(
            "---\nname: hello\ndescription: Git skill\n---\n\nFeature branch.\n",
            encoding="utf-8",
        )
        run_git("-C", str(repository), "commit", "--quiet", "-am", "feature")
        feature_commit = run_git(
            "-C", str(repository), "rev-parse", "HEAD"
        ).stdout.strip()
        run_git("-C", str(repository), "checkout", "--quiet", default_branch)
        remote = "https://example.invalid/acme/branch-demo.git"

        real_git = install_module.git

        def fake_git(*args, cwd=None):
            if args and args[0] == "clone":
                self.assertEqual(args[3], remote)
                return real_git(
                    "clone",
                    "--no-checkout",
                    "--",
                    str(repository),
                    args[4],
                    cwd=cwd,
                )
            return real_git(*args, cwd=cwd)

        with patch.object(install_module, "git", side_effect=fake_git):
            with source_folder(
                "git+" + remote,
                ref="feature",
                subdir="packages/plugin",
            ) as (folder, provenance):
                self.assertEqual(provenance["commit"], feature_commit)
                self.assertIn(
                    b"Feature branch.",
                    (folder / "skills/hello/SKILL.md").read_bytes(),
                )

    def test_source_folder_local_subdir_accepts_plain_temporary_path(self):
        with tempfile.TemporaryDirectory(
            prefix="any-harness-local-source-", dir=str(self.workspace)
        ) as temporary:
            repository = Path(temporary)
            plugin = repository / "packages" / "plugin"
            plugin.mkdir(parents=True)
            (plugin / "plugin.yaml").write_text("source\n", encoding="utf-8")

            with source_folder(
                str(repository), subdir="packages/plugin"
            ) as (folder, provenance):
                self.assertEqual(folder, repository / "packages/plugin")
                self.assertEqual(provenance, {"path": str(folder)})


if __name__ == "__main__":
    unittest.main()
