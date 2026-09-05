#!/usr/bin/env python3
"""Smoke-test the installed wheel without importing the source checkout.

The CI job installs the wheel into a temporary virtual environment and runs
this file with ``python -I``.  Keeping the check in the standard library makes
it useful in a minimal environment while the package itself still receives
its declared runtime dependencies from pip.
"""

from importlib import metadata
from pathlib import Path
import importlib
import os
import subprocess
import sys
import tempfile


HARNESS_PROJECT_ROOTS = {
    "claude-code": ".claude",
    "codex": ".agents",
    "opencode": ".opencode",
    "cursor": ".cursor",
}
AUTHORING_SKILLS = (
    "add-client-harness",
    "create-eval-suite",
    "create-portable-agent",
    "create-portable-plugin",
    "create-portable-skill",
)


def fail(message):
    raise RuntimeError(message)


def check_installed_origin():
    """Verify that the import is provided by the installed distribution."""
    package = importlib.import_module("any_harness")
    origin = Path(package.__file__).resolve()
    checkout = Path(__file__).resolve().parents[1]
    source_package = checkout / "src" / "any_harness"
    try:
        origin.relative_to(source_package)
    except ValueError:
        pass
    else:
        fail("any_harness was imported from the editable source checkout: %s" % origin)

    distribution = metadata.distribution("any-harness")
    recorded = Path(distribution.locate_file("any_harness/__init__.py")).resolve()
    if recorded != origin:
        fail("import origin does not match the installed distribution metadata: %s != %s" % (origin, recorded))

    print("Imported any_harness from installed distribution: %s" % origin)


def run_cli(*arguments, cwd):
    environment = os.environ.copy()
    # A caller's PYTHONPATH must not make the smoke test accidentally use a
    # checkout or another editable installation.
    environment.pop("PYTHONPATH", None)
    command = [sys.executable, "-I", "-m", "any_harness", *arguments]
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail("CLI timed out after 30 seconds: %s" % " ".join(command))
    if result.returncode:
        fail(
            "CLI failed (%s): %s\nstdout:\n%s\nstderr:\n%s"
            % (" ".join(command), result.returncode, result.stdout, result.stderr)
        )
    return result.stdout


def check_authoring_installations():
    """Install the bundled authoring plugin for every native project root."""
    with tempfile.TemporaryDirectory(prefix="any-harness-distribution-smoke-") as temporary:
        root = Path(temporary).resolve()
        expected = set(AUTHORING_SKILLS)
        for harness, native_root in HARNESS_PROJECT_ROOTS.items():
            project = root / harness
            run_cli("authoring", "--harness", harness, "--project", str(project), cwd=root)
            skills_root = project / native_root / "skills"
            found = {
                path.parent.name
                for path in skills_root.glob("*/SKILL.md")
                if path.is_file()
            }
            if found != expected:
                fail(
                    "%s authoring install produced %s; expected %s"
                    % (harness, sorted(found), sorted(expected))
                )
            for skill in AUTHORING_SKILLS:
                skill_file = skills_root / skill / "SKILL.md"
                if not skill_file.read_text(encoding="utf-8").startswith("---\n"):
                    fail("%s has invalid generated frontmatter: %s" % (harness, skill_file))
            print("Installed all five bundled authoring skills for %s" % harness)


def check_eval_cli():
    """Smoke-test packaged eval help and schema validation with a tiny suite."""
    with tempfile.TemporaryDirectory(prefix="any-harness-eval-cli-smoke-") as temporary:
        root = Path(temporary).resolve()
        source = root / "plugin"
        fixture = source / "evals" / "fixtures" / "basic"
        fixture.mkdir(parents=True)
        (source / "plugin.yaml").write_text(
            "schema: 1\n"
            "name: smoke-plugin\n"
            "version: 1.0.0\n"
            "description: Packaged eval CLI smoke suite\n"
            "author:\n"
            "  name: Smoke\n",
            encoding="utf-8",
        )
        skill = source / "skills" / "smoke"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\n"
            "name: smoke\n"
            "description: Report the fixture contents\n"
            "---\n\n"
            "Report the fixture contents without editing files.\n",
            encoding="utf-8",
        )
        (source / "evals" / "cases.yaml").write_text(
            "schema: 1\n"
            "cases:\n"
            "  - id: packaged-validation\n"
            "    fixture: fixtures/basic\n"
            "    prompt: List the fixture files.\n"
            "    component:\n"
            "      kind: plugin\n"
            "      name: smoke-plugin\n"
            "    mode: automatic\n"
            "    invocation: optional\n"
            "    assertions: []\n",
            encoding="utf-8",
        )
        (fixture / "README.txt").write_text("packaged eval fixture\n", encoding="utf-8")
        help_text = run_cli("eval", "--help", cwd=root)
        if "validate" not in help_text or "preview" not in help_text:
            fail("packaged eval help omitted validate or preview")
        validation = run_cli("eval", "validate", str(source), cwd=root)
        if '"cases": 1' not in validation:
            fail("packaged eval validate did not report one case: %s" % validation)
        preview = run_cli("eval", "preview", str(source), "--harness", "codex", cwd=root)
        if '"runs"' not in preview or '"case": "packaged-validation"' not in preview:
            fail("packaged eval preview did not plan the smoke case: %s" % preview)
        print("Packaged eval CLI help, validate, and preview passed")


def main():
    check_installed_origin()
    check_authoring_installations()
    check_eval_cli()
    print("Packaged distribution smoke check passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, KeyError, ImportError) as error:
        print("distribution smoke check failed: %s" % error, file=sys.stderr)
        sys.exit(1)
