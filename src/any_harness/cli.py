"""A small author/build/install CLI; no harness runtime is required to generate."""
import argparse
from importlib.resources import files as package_files
import json
import os
from pathlib import Path
import sys

import yaml

from . import __version__
from .build import build, generate, markdown
from . import eval as evaluation
from .install import apply, install, source_folder
from .model import Error, HARNESSES, name, string


def scaffold(destination, plugin_name, description="Reusable skills and agents", author="Plugin author"):
    destination = Path(destination)
    name(plugin_name, "plugin name")
    string(description, "description", 1024)
    string(author, "author name")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise Error(f"Scaffold destination must be empty: {destination}")
    if destination.is_symlink():
        raise Error("Scaffold destination may not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    meta = {"schema": 1, "name": plugin_name, "version": "0.1.0", "description": description, "author": {"name": author}}
    (destination / "plugin.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    add(destination, "skill", "getting-started", "Explain this plugin's workflows and when to use them.")
    (destination / "README.md").write_text(f"# {plugin_name}\n\n{description}\n\nEdit plugin.yaml, skills/*/SKILL.md and agents/*.md. Run `any-harness validate .` and `any-harness build . --out dist`. See each generated INSTALL.md for native distribution.\n", encoding="utf-8")
    (destination / ".gitignore").write_text("dist/\n.any-harness/\n", encoding="utf-8")


def add(source, kind, component_name, description):
    name(component_name, f"{kind} name")
    string(description, "description", 1024)
    source = Path(source)
    if not (source / "plugin.yaml").is_file():
        raise Error(f"No plugin.yaml in {source}; run init first")
    path = source / (f"skills/{component_name}/SKILL.md" if kind == "skill" else f"agents/{component_name}.md")
    if path.exists() or any(p.is_symlink() for p in [path, *path.parents]):
        raise Error(f"Refusing to overwrite existing or symlinked component: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "Describe the requested outcome, inspect the relevant project context, and return an actionable result. Replace this paragraph with the specific workflow before publishing.\n"
    path.write_bytes(markdown({"name": component_name, "description": description}, body))
    return path


def parser():
    result = argparse.ArgumentParser(prog="any-harness", description="Create skills and agents once; generate native files for four harnesses.")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create an authoring folder with a starter skill")
    init.add_argument("path")
    init.add_argument("--name", required=True)
    init.add_argument("--description", default="Reusable skills and agents")
    init.add_argument("--author", default="Plugin author")
    new = commands.add_parser("add", help="Scaffold a skill or agent")
    new.add_argument("kind", choices=("skill", "agent"))
    new.add_argument("name")
    new.add_argument("--source", default=".")
    new.add_argument("--description", required=True)
    validate = commands.add_parser("validate", help="Validate source and render all adapters without writing")
    validate.add_argument("source", nargs="?", default=".")
    eval_command = commands.add_parser("eval", help="Validate, preview, run, and report native harness evaluations")
    eval_commands = eval_command.add_subparsers(dest="eval_command", required=True)
    eval_validate = eval_commands.add_parser("validate", help="Validate an eval suite without starting a harness")
    eval_validate.add_argument("source", nargs="?", default=".")
    eval_preview = eval_commands.add_parser("preview", help="Show the native execution plan and capabilities")
    eval_preview.add_argument("source", nargs="?", default=".")
    _eval_selection_args(eval_preview)
    eval_preview.add_argument("--repeat", type=int, default=1)
    eval_preview.add_argument("--config", choices=("isolated", "inherit"), default="isolated")
    eval_run = eval_commands.add_parser("run", help="Run selected cases through native harness CLIs")
    eval_run.add_argument("source", nargs="?", default=".")
    eval_run.add_argument("--out", required=True, help="New or empty result directory")
    _eval_selection_args(eval_run)
    eval_run.add_argument("--repeat", type=int, default=1)
    eval_run.add_argument("--jobs", type=int, default=1)
    eval_run.add_argument("--timeout", type=float, default=300)
    eval_run.add_argument("--check-timeout", type=float, default=30)
    eval_run.add_argument("--config", choices=("isolated", "inherit"), default="isolated")
    eval_report = eval_commands.add_parser("report", help="Render a concise Markdown report from result JSON")
    eval_report.add_argument("result_json")
    eval_report.add_argument("--out", help="Markdown output path; defaults beside result JSON")
    create = commands.add_parser("build", help="Generate standalone native marketplace and project trees")
    create.add_argument("source", nargs="?", default=".")
    create.add_argument("--out", default="dist")
    create.add_argument("--harness", choices=HARNESSES, action="append", help="Repeat to select targets; defaults to all four")
    create.add_argument("--check", action="store_true", help="Fail if committed output is missing or stale")
    for verb in ("install", "uninstall", "authoring"):
        item = commands.add_parser(verb, help={"install": "Install or update native skills/agents from a folder or Git URL", "uninstall": "Remove unchanged files owned by an installed plugin", "authoring": "Install bundled plugin/skill/agent authoring skills"}[verb])
        if verb == "install":
            item.add_argument("source")
            item.add_argument("--ref", help="Git tag, branch, or commit (pin commits for reproducibility)")
            item.add_argument("--subdir", help="Plugin source directory within a repository")
        if verb == "uninstall":
            item.add_argument("name")
        item.add_argument("--harness", required=True, choices=HARNESSES)
        scope = item.add_mutually_exclusive_group(required=True)
        scope.add_argument("--project", metavar="DIR")
        scope.add_argument("--global", action="store_true", dest="global_scope")
        item.add_argument("--dry-run", action="store_true")
    return result


def _eval_selection_args(command):
    command.add_argument("--harness", choices=HARNESSES, action="append", dest="harnesses", help="Repeat to select targets")
    command.add_argument("--case", action="append", dest="case_ids", help="Repeat to select case IDs")
    command.add_argument("--variant", action="append", dest="variant_ids", help="Repeat to select variant IDs")


def _eval_json(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _run_eval_command(args):
    if args.eval_command == "validate":
        suite = evaluation.load_suite(args.source)
        value = {
            "schema": 1,
            "source": str(suite["source"]),
            "source_revision": evaluation._git_revision(suite["source"]),
            "source_hash": evaluation.hash_tree(suite["source"], exclude_dirs=("evals", ".git")),
            "suite_hash": evaluation.hash_tree(suite["suite_root"]),
            "cases": len(suite["cases"]),
            "variants": len(suite["variants"]),
            "harnesses": suite["harnesses"],
        }
        print(_eval_json(value), end="")
        return 0
    if args.eval_command == "preview":
        value = evaluation.preview_suite(args.source, harnesses=args.harnesses, case_ids=args.case_ids, variant_ids=args.variant_ids, repeat=args.repeat, config_mode=args.config)
        print(_eval_json(value), end="")
        return 0
    if args.eval_command == "run":
        result = evaluation.run_suite(
            args.source,
            args.out,
            harnesses=args.harnesses,
            case_ids=args.case_ids,
            variant_ids=args.variant_ids,
            repeat=args.repeat,
            jobs=args.jobs,
            timeout=args.timeout,
            check_timeout=args.check_timeout,
            config_mode=args.config,
        )
        print(_eval_json({"result": str(Path(args.out).absolute() / "result.json"), "report": str(Path(args.out).absolute() / "report.md"), "status": result.get("status"), "exit_code": evaluation.ci_exit_code(result)}), end="")
        return evaluation.ci_exit_code(result)
    if args.eval_command == "report":
        try:
            result, destination = evaluation.report_result(args.result_json, args.out)
        except evaluation.EvalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(_eval_json({"report": str(destination.absolute()), "status": result.get("status"), "exit_code": evaluation.ci_exit_code(result)}), end="")
        return evaluation.ci_exit_code(result)
    raise Error(f"Unknown eval command: {args.eval_command}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            scaffold(args.path, args.name, args.description, args.author)
            print(f"Created {Path(args.path).absolute()}")
        elif args.command == "add":
            print(f"Created {add(args.source, args.kind, args.name, args.description).absolute()}")
        elif args.command == "validate":
            plugin, tree, notes = generate(args.source)
            print(f"Valid: {plugin.meta['name']} ({len(plugin.skills)} skills, {len(plugin.agents)} agents; {len(tree)} generated files)")
            for note in notes:
                print(f"Note: {note}")
        elif args.command == "build":
            selected = tuple(dict.fromkeys(args.harness or HARNESSES))
            notes = build(args.source, args.out, selected, args.check)
            print(f"{'Verified' if args.check else 'Built'} {Path(args.out).absolute()}")
            for note in notes:
                print(f"Note: {note}")
        elif args.command == "eval":
            return _run_eval_command(args)
        else:
            global_opencode = args.global_scope and args.harness == "opencode"
            root = (Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "opencode") if global_opencode else (Path.home() if args.global_scope else Path(args.project))
            if args.command == "uninstall":
                actions = apply(root, args.name, args.harness, {}, {}, args.dry_run, uninstall=True)
                notes = []
            elif args.command == "authoring":
                source = Path(str(package_files("any_harness") / "bundled" / "authoring"))
                actions, notes = install(source, args.harness, root, args.dry_run, opencode_global=global_opencode)
            else:
                with source_folder(args.source, args.ref, args.subdir) as (source, provenance):
                    actions, notes = install(source, args.harness, root, args.dry_run, provenance, global_opencode)
            for action, relative in actions:
                print(f"{'Would ' if args.dry_run else ''}{action}: {root / relative}")
            print(f"{'Preview' if args.dry_run else 'Done'}: {len(actions)} file changes for {args.harness}.")
            for note in notes:
                print(f"Note: {note}")
            if not args.dry_run and args.command != "uninstall":
                print("Open a new harness session to load installed components.")
        return 0
    except (Error, OSError, ValueError, TypeError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
