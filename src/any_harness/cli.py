"""A small author/build/install CLI; no harness runtime is required to generate."""

from importlib.resources import files as package_files
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import List, Optional, Sequence, Tuple

import click
import typer
from typer.main import get_command
import yaml

from . import __version__
from .build import build, generate, markdown
from .config import Config, find_config, save_config, validate_config
from . import eval as evaluation
from .doctor import run_doctor
from .install import apply, install, source_folder
from .model import Error, HARNESSES, name, string
from .multi import build_all as build_all_plugins


app = typer.Typer(
    name="any-harness",
    help="Create skills and agents once; generate native files for four harnesses.",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode=None,
)
eval_app = typer.Typer(
    help="Validate, preview, run, and report native harness evaluations.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
app.add_typer(eval_app, name="eval")


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


def _version_callback(value: bool):
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root_callback(
    ctx: click.Context,
    no_config: bool = typer.Option(False, "--no-config", help="Ignore local .any-harness/config.yaml defaults."),
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show the installed version and exit."),
):
    ctx.ensure_object(dict)
    ctx.obj["no_config"] = no_config


def _config(ctx: click.Context) -> Optional[Config]:
    root = ctx.find_root()
    state = root.ensure_object(dict)
    if state.get("no_config"):
        return None
    return find_config()


def _usage(message: str):
    raise click.UsageError(message)


def _selected_harnesses(explicit: Optional[Sequence[str]], config: Optional[Config] = None) -> Tuple[str, ...]:
    values = config.harnesses if not explicit and config is not None else (explicit or HARNESSES)
    values = tuple(values)
    if not values:
        raise Error("At least one harness must be selected")
    result = []
    for harness in values:
        if harness not in HARNESSES:
            raise Error(f"Unknown harness: {harness}")
        if harness not in result:
            result.append(harness)
    return tuple(result)


def _single_harness(explicit: Optional[str], config: Optional[Config]) -> str:
    if explicit is not None:
        return _selected_harnesses((explicit,))[0]
    if config is None:
        _usage("Missing option '--harness'.")
    if len(config.harnesses) != 1:
        _usage("Config has multiple harnesses; pass '--harness' for a single-harness command.")
    return config.harnesses[0]


def _configured_path(config: Optional[Config], value: Optional[str], field: str, default: str) -> str:
    if value is not None:
        return value
    if config is None:
        return default
    return str(config.resolve(getattr(config, field)))


def _source_value(config: Optional[Config], value: Optional[str], required: bool = False) -> str:
    if value is not None:
        return value
    if config is not None:
        return str(config.resolve(config.source))
    if required:
        _usage("Missing source; pass SOURCE or configure it with 'any-harness setup'.")
    return "."


def _project_root(config: Optional[Config], project: Optional[str], global_scope: bool, harness: str):
    if global_scope and project is not None:
        _usage("Options '--project' and '--global' are mutually exclusive.")
    if global_scope:
        if harness == "opencode":
            xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
            return Path(xdg) / "opencode", True
        return Path.home(), False
    if project is not None:
        return Path(project), False
    if config is not None:
        return config.resolve(config.project), False
    _usage("Missing one of '--project' or '--global'.")


def _prompt_harnesses() -> Tuple[str, ...]:
    value = typer.prompt("Harnesses (comma-separated)", default=", ".join(HARNESSES))
    return _selected_harnesses(tuple(item.strip() for item in value.split(",") if item.strip()))


def _eval_harnesses(explicit: Optional[Sequence[str]], config: Optional[Config]):
    """Keep eval's suite-defined default when no CLI/config selection exists."""
    if explicit:
        return list(_selected_harnesses(explicit))
    if config is not None:
        return list(config.harnesses)
    return None


@app.command("init", help="Create an authoring folder with a starter skill.")
def init_command(
    path: str = typer.Argument(..., help="Destination directory."),
    plugin_name: Optional[str] = typer.Option(None, "--name", help="Plugin name; defaults to the destination folder name."),
    description: str = typer.Option("Reusable skills and agents", "--description", help="Plugin description."),
    author: str = typer.Option("Plugin author", "--author", help="Plugin author name."),
):
    destination = Path(path)
    inferred = plugin_name if plugin_name is not None else destination.resolve().name
    scaffold(destination, inferred, description, author)
    typer.echo(f"Created {destination.absolute()}")


@app.command("add", help="Scaffold a skill or agent.")
def add_command(
    ctx: click.Context,
    kind: str = typer.Argument(..., click_type=click.Choice(("skill", "agent")), help="Component kind."),
    component_name: str = typer.Argument(..., metavar="NAME", help="Component name."),
    source: Optional[str] = typer.Option(None, "--source", help="Plugin source directory."),
    description: str = typer.Option(..., "--description", help="Component description."),
):
    config = _config(ctx)
    source_value = _source_value(config, source)
    typer.echo(f"Created {add(source_value, kind, component_name, description).absolute()}")


@app.command("validate", help="Validate source and render all adapters without writing.")
def validate_command(
    ctx: click.Context,
    source: Optional[str] = typer.Argument(None, help="Plugin source directory."),
):
    source_value = _source_value(_config(ctx), source)
    plugin, tree, notes = generate(source_value)
    typer.echo(f"Valid: {plugin.meta['name']} ({len(plugin.skills)} skills, {len(plugin.agents)} agents; {len(tree)} generated files)")
    for note in notes:
        typer.echo(f"Note: {note}")


@app.command("setup", help="Scaffold or validate a plugin and save local CLI defaults.")
def setup_command(
    ctx: click.Context,
    path: Optional[str] = typer.Argument(None, help="Plugin source directory; defaults to the current directory."),
    harnesses: Optional[List[str]] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Repeat to select configured build/install harnesses."),
    project: Optional[str] = typer.Option(None, "--project", help="Default project directory for installs."),
    author: Optional[str] = typer.Option(None, "--author", help="Author name when scaffolding a new source."),
    plugin_name: Optional[str] = typer.Option(None, "--name", help="Plugin name when scaffolding a new source."),
    out: Optional[str] = typer.Option(None, "--out", help="Default build output directory."),
    no_input: bool = typer.Option(False, "--no-input", help="Use defaults without prompting."),
):
    del ctx  # Setup writes the config selected by this invocation, rather than reading old defaults.
    config_root = Path.cwd().absolute().resolve()
    source_arg = path if path is not None else "."
    source_path = Path(source_arg).expanduser()
    if not source_path.is_absolute():
        source_path = config_root / source_path
    source_path = source_path.absolute()

    new_source = not source_path.exists() or (source_path.is_dir() and not any(source_path.iterdir()))
    # Typer may represent an omitted repeated option as []; treat it as absent
    # while gathering setup values, so every prompt and validation completes
    # before scaffolding can write anything.
    harnesses = tuple(harnesses or ())
    if harnesses:
        selected_harnesses = _selected_harnesses(harnesses)
    elif no_input:
        selected_harnesses = HARNESSES
    else:
        selected_harnesses = _prompt_harnesses()

    project_value = project
    if project_value is None:
        project_value = "." if no_input else typer.prompt("Project directory", default=".")
    out_value = out
    if out_value is None:
        out_value = "dist" if no_input else typer.prompt("Build output directory", default="dist")
    if new_source:
        inferred_name = plugin_name if plugin_name is not None else source_path.resolve().name
        if not no_input and plugin_name is None:
            inferred_name = typer.prompt("Plugin name", default=inferred_name)
        selected_author = author if author is not None else "Plugin author"
        if not no_input and author is None:
            selected_author = typer.prompt("Author", default=selected_author)
        name(inferred_name, "plugin name")
        string(selected_author, "author name")
        validate_config(config_root, source_path, selected_harnesses, project_value, out_value)
        scaffold(source_path, inferred_name, author=selected_author)
        message = f"Created {source_path}"
    else:
        validate_config(config_root, source_path, selected_harnesses, project_value, out_value)
        if not (source_path / "plugin.yaml").is_file():
            raise Error(f"Existing setup path is not a plugin source: {source_path}")
        generate(source_path)
        message = f"Validated {source_path}"

    config_path = save_config(config_root, source_path, selected_harnesses, project_value, out_value)
    typer.echo(message)
    typer.echo(f"Saved defaults to {config_path}")


@app.command("build", help="Generate standalone native marketplace and project trees.")
def build_command(
    ctx: click.Context,
    source: Optional[str] = typer.Argument(None, help="Plugin source directory."),
    out: Optional[str] = typer.Option(None, "--out", help="Generated output directory."),
    harnesses: Optional[List[str]] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Repeat to select targets; defaults to all four."),
    check: bool = typer.Option(False, "--check", help="Fail if committed output is missing or stale."),
):
    config = _config(ctx)
    selected = _selected_harnesses(harnesses, config)
    source_value = _source_value(config, source)
    out_value = _configured_path(config, out, "out", "dist")
    notes = build(source_value, out_value, selected, check)
    typer.echo(f"{'Verified' if check else 'Built'} {Path(out_value).absolute()}")
    for note in notes:
        typer.echo(f"Note: {note}")


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
        code = evaluation.ci_exit_code(result)
        print(_eval_json({"result": str(Path(args.out).absolute() / "result.json"), "report": str(Path(args.out).absolute() / "report.md"), "status": result.get("status"), "exit_code": code}), end="")
        return code
    if args.eval_command == "report":
        try:
            result, destination = evaluation.report_result(args.result_json, args.out)
        except evaluation.EvalError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        code = evaluation.ci_exit_code(result)
        print(_eval_json({"report": str(destination.absolute()), "status": result.get("status"), "exit_code": code}), end="")
        return code
    raise Error(f"Unknown eval command: {args.eval_command}")


def _eval_source(ctx: click.Context, source: Optional[str]) -> str:
    return _source_value(_config(ctx), source)


@eval_app.command("validate", help="Validate an eval suite without starting a harness.")
def eval_validate_command(
    ctx: click.Context,
    source: Optional[str] = typer.Argument(None, help="Plugin source directory."),
):
    return _run_eval_command(SimpleNamespace(eval_command="validate", source=_eval_source(ctx, source)))


@eval_app.command("preview", help="Show the native execution plan and capabilities.")
def eval_preview_command(
    ctx: click.Context,
    source: Optional[str] = typer.Argument(None, help="Plugin source directory."),
    harnesses: Optional[List[str]] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Repeat to select targets."),
    case_ids: Optional[List[str]] = typer.Option(None, "--case", help="Repeat to select case IDs."),
    variant_ids: Optional[List[str]] = typer.Option(None, "--variant", help="Repeat to select variant IDs."),
    repeat: int = typer.Option(1, "--repeat", min=1),
    config: str = typer.Option("isolated", "--config", click_type=click.Choice(("isolated", "inherit")), help="Harness configuration scope."),
):
    configured = _config(ctx)
    selected = _eval_harnesses(harnesses, configured)
    return _run_eval_command(SimpleNamespace(
        eval_command="preview",
        source=_eval_source(ctx, source),
        harnesses=selected,
        case_ids=case_ids,
        variant_ids=variant_ids,
        repeat=repeat,
        config=config,
    ))


@eval_app.command("run", help="Run selected cases through native harness CLIs.")
def eval_run_command(
    ctx: click.Context,
    source: Optional[str] = typer.Argument(None, help="Plugin source directory."),
    out: Optional[str] = typer.Option(None, "--out", help="New or empty result directory; required."),
    harnesses: Optional[List[str]] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Repeat to select targets."),
    case_ids: Optional[List[str]] = typer.Option(None, "--case", help="Repeat to select case IDs."),
    variant_ids: Optional[List[str]] = typer.Option(None, "--variant", help="Repeat to select variant IDs."),
    repeat: int = typer.Option(1, "--repeat", min=1),
    jobs: int = typer.Option(1, "--jobs", min=1),
    timeout: float = typer.Option(300, "--timeout", min=0),
    check_timeout: float = typer.Option(30, "--check-timeout", min=0),
    config: str = typer.Option("isolated", "--config", click_type=click.Choice(("isolated", "inherit")), help="Harness configuration scope."),
):
    if out is None:
        _usage("Missing option '--out'.")
    configured = _config(ctx)
    selected = _eval_harnesses(harnesses, configured)
    return _run_eval_command(SimpleNamespace(
        eval_command="run",
        source=_eval_source(ctx, source),
        out=out,
        harnesses=selected,
        case_ids=case_ids,
        variant_ids=variant_ids,
        repeat=repeat,
        jobs=jobs,
        timeout=timeout,
        check_timeout=check_timeout,
        config=config,
    ))


@eval_app.command("report", help="Render a concise Markdown report from result JSON.")
def eval_report_command(
    result_json: str = typer.Argument(..., help="Result JSON path."),
    out: Optional[str] = typer.Option(None, "--out", help="Markdown output path; defaults beside result JSON."),
):
    return _run_eval_command(SimpleNamespace(eval_command="report", result_json=result_json, out=out))


@app.command("build-all", help="Build one combined marketplace from every plugin source in a repository.")
def build_all_command(
    root: str = typer.Argument(".", help="Repository root containing plugin sources."),
    out: str = typer.Option("dist/all", "--out", help="Generated combined output directory."),
    harnesses: Optional[List[str]] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Repeat to select targets; defaults to all four."),
    name_value: Optional[str] = typer.Option(None, "--name", help="Stable combined marketplace name."),
    check: bool = typer.Option(False, "--check", help="Fail if committed output is missing or stale."),
):
    selected = _selected_harnesses(harnesses)
    notes = build_all_plugins(root, out, selected, check, name_value)
    typer.echo(f"{'Verified' if check else 'Built'} {Path(out).absolute()}")
    for note in notes:
        typer.echo(f"Note: {note}")


@app.command("doctor", help="Check local prerequisites without starting a harness.")
def doctor_command(
    harnesses: Optional[List[str]] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Repeat to require a native harness CLI."),
    as_json: bool = typer.Option(False, "--json", help="Print the machine-readable report."),
):
    report, code = run_doctor(harnesses or None)
    if as_json:
        print(_eval_json(report), end="")
    else:
        for check in report.get("checks", []):
            typer.echo(f"{check['status']}: {check['name']} - {check['detail']}")
    return code


def _install_command(
    source: Optional[str],
    ref: Optional[str],
    subdir: Optional[str],
    harness: Optional[str],
    project: Optional[str],
    global_scope: bool,
    dry_run: bool,
    ctx: click.Context,
):
    config = _config(ctx)
    selected_harness = _single_harness(harness, config)
    source_value = _source_value(config, source, required=True)
    root, global_opencode = _project_root(config, project, global_scope, selected_harness)
    with source_folder(source_value, ref, subdir) as (source_path, provenance):
        actions, notes = install(source_path, selected_harness, root, dry_run, provenance, global_opencode)
    for action, relative in actions:
        typer.echo(f"{'Would ' if dry_run else ''}{action}: {root / relative}")
    typer.echo(f"{'Preview' if dry_run else 'Done'}: {len(actions)} file changes for {selected_harness}.")
    for note in notes:
        typer.echo(f"Note: {note}")
    if not dry_run:
        typer.echo("Open a new harness session to load installed components.")


@app.command("install", help="Install or update native skills/agents from a folder or Git URL.")
def install_command(
    ctx: click.Context,
    source: Optional[str] = typer.Argument(None, help="Local source or git+ URL; defaults to configured source."),
    ref: Optional[str] = typer.Option(None, "--ref", help="Git tag, branch, or commit (pin commits for reproducibility)."),
    subdir: Optional[str] = typer.Option(None, "--subdir", help="Plugin source directory within a repository."),
    harness: Optional[str] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Target harness; may come from local config."),
    project: Optional[str] = typer.Option(None, "--project", metavar="DIR", help="Project installation root."),
    global_scope: bool = typer.Option(False, "--global", help="Install into the harness's global scope."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing."),
):
    return _install_command(source, ref, subdir, harness, project, global_scope, dry_run, ctx)


@app.command("uninstall", help="Remove unchanged files owned by an installed plugin.")
def uninstall_command(
    ctx: click.Context,
    plugin_name: str = typer.Argument(..., metavar="NAME", help="Installed plugin name."),
    harness: Optional[str] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Target harness; may come from local config."),
    project: Optional[str] = typer.Option(None, "--project", metavar="DIR", help="Project installation root."),
    global_scope: bool = typer.Option(False, "--global", help="Uninstall from the harness's global scope."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing."),
):
    config = _config(ctx)
    selected_harness = _single_harness(harness, config)
    root, _global_opencode = _project_root(config, project, global_scope, selected_harness)
    actions = apply(root, plugin_name, selected_harness, {}, {}, dry_run, uninstall=True)
    for action, relative in actions:
        typer.echo(f"{'Would ' if dry_run else ''}{action}: {root / relative}")
    typer.echo(f"{'Preview' if dry_run else 'Done'}: {len(actions)} file changes for {selected_harness}.")


@app.command("authoring", help="Install bundled plugin/skill/agent authoring skills.")
def authoring_command(
    ctx: click.Context,
    harness: Optional[str] = typer.Option(None, "--harness", click_type=click.Choice(HARNESSES), help="Target harness; may come from local config."),
    project: Optional[str] = typer.Option(None, "--project", metavar="DIR", help="Project installation root."),
    global_scope: bool = typer.Option(False, "--global", help="Install into the harness's global scope."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing."),
):
    config = _config(ctx)
    selected_harness = _single_harness(harness, config)
    root, global_opencode = _project_root(config, project, global_scope, selected_harness)
    source = Path(str(package_files("any_harness") / "bundled" / "authoring"))
    actions, notes = install(source, selected_harness, root, dry_run, opencode_global=global_opencode)
    for action, relative in actions:
        typer.echo(f"{'Would ' if dry_run else ''}{action}: {root / relative}")
    typer.echo(f"{'Preview' if dry_run else 'Done'}: {len(actions)} file changes for {selected_harness}.")
    for note in notes:
        typer.echo(f"Note: {note}")


def parser():
    """Return the Typer-generated Click command for embedding and inspection."""
    return get_command(app)


def main(argv=None):
    """Run the CLI and always return an integer process exit code."""
    args = None if argv is None else [str(value) for value in argv]
    try:
        result = parser().main(args=args, prog_name="any-harness", standalone_mode=False)
    except click.Abort:
        print("Aborted.", file=sys.stderr)
        return 1
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.ClickException as exc:
        exc.show()
        return exc.exit_code
    except (Error, OSError, ValueError, TypeError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if result is None else int(result)


if __name__ == "__main__":
    sys.exit(main())
