"""Discovery and deterministic builds for repositories containing many plugins."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile

from .build import json_bytes, put, render
from .model import Error, HARNESSES, load, name as validate_name


MARKER = ".any-harness-build-all.json"
_SINGLE_MARKER = ".any-harness-build.json"
_SKIPPED_DIRECTORIES = frozenset({"node_modules", "vendor", "build", "dist", "__pycache__"})
_MARKETPLACE_PATHS = {
    "claude-code": ".claude-plugin/marketplace.json",
    "codex": ".agents/plugins/marketplace.json",
    "cursor": ".cursor-plugin/marketplace.json",
}


def _inside(path, parent):
    """Return whether *path* is the same as or below *parent*."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _normal_path(value):
    """Canonicalize a path after callers have checked direct symlinks."""
    return Path(value).absolute().resolve(strict=False)


def _excluded_directory(path, root, output):
    if path != root and path.name.startswith("."):
        return True
    if path != root and path.name in _SKIPPED_DIRECTORIES:
        return True
    if output is not None and path != root and _inside(path, output):
        return True
    # A previous generated tree is not a repository of plugin sources. This
    # also keeps a custom output directory out of a later recursive scan.
    if path != root:
        for marker in (MARKER, _SINGLE_MARKER):
            candidate = path / marker
            if candidate.is_file() or candidate.is_symlink():
                return True
    return False


def discover(root, output=None):
    """Discover plugin roots in deterministic order without following links.

    The explicit root is considered even when its name starts with a dot or
    is one of the ordinary generated/vendor directory names. Once a
    ``plugin.yaml`` is found, that directory owns its complete subtree and
    discovery does not descend into it.
    """
    root_input = Path(root)
    if root_input.is_symlink():
        raise Error(f"Source root may not be a symlink: {root_input}")
    root = _normal_path(root_input)
    if not root.is_dir():
        raise Error(f"Source root must be a directory: {root}")

    output = _normal_path(output) if output is not None else None
    found = []

    def walk(current):
        if _excluded_directory(current, root, output):
            return
        manifest = current / "plugin.yaml"
        if manifest.is_symlink():
            raise Error(f"Plugin manifest may not be a symlink: {manifest}")
        if manifest.exists():
            if not manifest.is_file():
                raise Error(f"Plugin manifest must be a regular file: {manifest}")
            found.append(current)
            return
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            if child.is_symlink() or not child.is_dir():
                continue
            walk(child)

    walk(root)
    found.sort(key=lambda path: path.relative_to(root).as_posix())
    return found


def _default_marketplace_name(root):
    value = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")
    value = value[:64].rstrip("-")
    return value or "marketplace"


def _marketplace_name(root, value):
    selected = _default_marketplace_name(root) if value is None else value
    validate_name(selected, "marketplace name")
    return selected


def _selected_harnesses(harnesses):
    if harnesses is None:
        harnesses = HARNESSES
    try:
        selected = tuple(harnesses)
    except TypeError as exc:
        raise Error("harnesses must be an iterable of harness names") from exc
    if not selected:
        raise Error("At least one harness must be selected")
    if any(not isinstance(harness, str) for harness in selected):
        raise Error("Harnesses must be names")
    if len(set(selected)) != len(selected):
        raise Error("Harnesses may not be repeated")
    unknown = [harness for harness in selected if harness not in HARNESSES]
    if unknown:
        raise Error(f"Unknown harness: {unknown[0]}")
    return tuple(sorted(selected))


def _validate_output_location(output, plugins):
    original = Path(output)
    if original.is_symlink():
        raise Error("Build output may not be a symlink")
    if any(parent.is_symlink() for parent in original.parents):
        raise Error("Build output parents may not be symlinks; use a physical path")
    output = _normal_path(original)
    for plugin in plugins:
        source = _normal_path(plugin.root)
        if _inside(source, output):
            raise Error("Build output must be separate from plugin sources")
        if _inside(output, source):
            if output == source:
                raise Error("Build output must be separate from plugin sources")
            for component in ("skills", "agents", "native"):
                if _inside(output, source / component):
                    raise Error("Build output may not be inside source components")
    return output


def _json_value(data, where):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Error(f"{where}: generated JSON is invalid") from exc
    if not isinstance(value, dict):
        raise Error(f"{where}: generated JSON must be an object")
    return value


def _plugin_names(plugins):
    seen = {}
    for plugin in plugins:
        plugin_name = plugin.meta["name"]
        if plugin_name in seen:
            raise Error(
                f"Duplicate plugin name '{plugin_name}' in "
                f"{seen[plugin_name].root} and {plugin.root}"
            )
        seen[plugin_name] = plugin
    return sorted(seen)


def _marker_value(marketplace_name, plugin_names, harnesses):
    return {
        "schema": 1,
        "type": "multi-plugin",
        "name": marketplace_name,
        "plugins": list(sorted(plugin_names)),
        "harnesses": list(sorted(harnesses)),
    }


def _validate_marker(path, marketplace_name):
    if path.is_symlink() or not path.is_file():
        raise Error(f"Refusing to replace unowned output directory: {path.parent}")
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Error(f"Invalid build-all ownership marker: {path}") from exc
    if not isinstance(marker, dict) or set(marker) != {"schema", "type", "name", "plugins", "harnesses"}:
        raise Error(f"Invalid build-all ownership marker: {path}")
    if type(marker["schema"]) is not int or marker["schema"] != 1:
        raise Error(f"Invalid build-all ownership marker: {path}")
    if marker["type"] != "multi-plugin" or marker["name"] != marketplace_name:
        raise Error(f"Invalid build-all ownership marker or different marketplace name: {path}")
    try:
        validate_name(marker["name"], "build-all marketplace name")
    except (Error, TypeError):
        raise Error(f"Invalid build-all ownership marker: {path}")
    for field, allowed in (("plugins", None), ("harnesses", set(HARNESSES))):
        values = marker[field]
        if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
            raise Error(f"Invalid build-all ownership marker: {path}")
        if values != sorted(values) or len(set(values)) != len(values):
            raise Error(f"Invalid build-all ownership marker: {path}")
        if field == "plugins":
            try:
                for value in values:
                    validate_name(value, "build-all plugin name")
            except (Error, TypeError):
                raise Error(f"Invalid build-all ownership marker: {path}")
        elif not set(values) <= allowed:
            raise Error(f"Invalid build-all ownership marker: {path}")
    return marker


def _snapshot(root):
    """Read files and directory names, rejecting every link or special file."""
    if not root.exists():
        return {}, set()
    if root.is_symlink() or not root.is_dir():
        raise Error(f"Build output must be a regular directory: {root}")
    files = {}
    directories = set()
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise Error(f"Symlinks are not supported in build output: {item}")
        relative = item.relative_to(root).as_posix()
        if item.is_dir():
            directories.add(relative)
        elif item.is_file():
            mode = 0o755 if item.stat().st_mode & 0o111 else 0o644
            files[relative] = (item.read_bytes(), mode)
        else:
            raise Error(f"Not a regular file in build output: {item}")
    return files, directories


def _expected_directories(tree):
    return {
        parent.as_posix()
        for relative in tree
        for parent in Path(relative).parents
        if parent.as_posix() != "."
    }


def _validate_existing_output(output, marketplace_name):
    if output.is_symlink():
        raise Error("Build output may not be a symlink")
    if not output.exists():
        return False
    if not output.is_dir():
        raise Error(f"Build output must be a directory: {output}")
    _validate_marker(output / MARKER, marketplace_name)
    _snapshot(output)
    return True


def _project_destination(plugin_name, relative):
    if not relative.startswith("project/"):
        return relative
    project_relative = relative[len("project/"):]
    if not project_relative:
        raise Error("Generated project path may not be empty")
    return f"projects/{plugin_name}/{project_relative}"


def _rewrite_warning(warning, plugin_name):
    """Adapt generated warning paths after moving project output per plugin."""
    return warning.replace("project/", f"projects/{plugin_name}/")


def _plugin_readme(plugin, harness, marketplace_name, notes):
    name = plugin.meta["name"]
    description = plugin.meta["description"]
    if harness == "claude-code":
        instruction = (
            f"Add the generated `claude-code` folder with `/plugin marketplace add "
            f"<path-to-claude-code>`, then install `{name}@{marketplace_name}`."
        )
    elif harness == "codex":
        instruction = (
            f"Register the generated `codex` folder with `codex plugin marketplace add "
            f"<path-to-codex>`, then add `{name}@{marketplace_name}`."
        )
    elif harness == "cursor":
        instruction = (
            f"This plugin is listed in the generated Cursor marketplace `{marketplace_name}`. "
            "Publish or import the generated Cursor root using Cursor's documented marketplace flow."
        )
    else:
        instruction = (
            f"Copy `projects/{name}/.opencode/` into an OpenCode project. "
            "OpenCode has no marketplace package in this generator."
        )
    lines = [f"# {name}", "", description, "", f"# Install {name} for {harness}", "", instruction]
    lines.extend(["", *[f"- {note}" for note in notes]])
    lines.extend(["", "Restart or open a new harness session after installation.", ""])
    return "\n".join(lines).encode()


def _plugin_install(plugin_name, harness, marketplace_name, notes):
    if harness == "claude-code":
        instruction = (
            f"Add the generated `claude-code` folder with `/plugin marketplace add "
            f"<path-to-claude-code>`, then install `{plugin_name}@{marketplace_name}`."
        )
    elif harness == "codex":
        instruction = (
            f"Register the generated `codex` folder with `codex plugin marketplace add "
            f"<path-to-codex>`, then add `{plugin_name}@{marketplace_name}`."
        )
    elif harness == "cursor":
        instruction = (
            f"Use the generated Cursor marketplace `{marketplace_name}` and follow Cursor's "
            "documented submission or import flow."
        )
    else:
        instruction = (
            f"Copy `projects/{plugin_name}/.opencode/` into an OpenCode project. "
            "OpenCode discovers native project files directly; no marketplace is generated."
        )
    lines = [f"# Install {plugin_name} for {harness}", "", instruction]
    if notes:
        lines.extend(["", *[f"- {note}" for note in notes]])
    lines.extend(["", "Restart or open a new harness session after installation.", ""])
    return "\n".join(lines).encode()


def _combined_install(harness, marketplace_name, plugin_names, notes):
    lines = [
        f"# Install {marketplace_name}",
        "",
        f"This generated {harness} tree contains the plugins: {', '.join(plugin_names)}.",
        "",
    ]
    if harness == "claude-code":
        lines.extend([
            "Add this `claude-code` folder as a marketplace with `/plugin marketplace add <path-to-claude-code>`,",
            f"then install a plugin with `/plugin install PLUGIN@{marketplace_name}`.",
        ])
    elif harness == "codex":
        lines.extend([
            "Register this `codex` folder with `codex plugin marketplace add <path-to-codex>`,",
            f"then add a plugin with `codex plugin add PLUGIN@{marketplace_name}`.",
        ])
    elif harness == "cursor":
        lines.append(
            f"The generated Cursor marketplace is named `{marketplace_name}`. Follow Cursor's documented submission or import flow."
        )
    else:
        lines.extend([
            "OpenCode has no marketplace format in this generator.",
            "Copy the desired `projects/PLUGIN/.opencode/` tree into an OpenCode project.",
        ])
    if notes:
        lines.extend(["", "Compatibility notes:", *[f"- {note}" for note in notes]])
    lines.extend(["", "Restart or open a new harness session after installation.", ""])
    return "\n".join(lines).encode()


def _combined_compatibility(harness, marketplace_name, plugin_compatibility):
    plugins = []
    for name, compatibility in plugin_compatibility:
        plugins.append({
            "name": name,
            "skills": compatibility.get("skills", 0),
            "agents": compatibility.get("agents", 0),
            "notes": compatibility.get("notes", []),
        })
    return json_bytes({
        "schema": 1,
        "type": "multi-plugin",
        "name": marketplace_name,
        "harness": harness,
        "plugins": plugins,
    })


def _combined_marketplace(harness, marketplace_name, entries):
    entries = sorted(entries, key=lambda entry: entry["name"])
    if harness == "codex":
        display_name = marketplace_name.replace("-", " ").title()
        return {
            "name": marketplace_name,
            "interface": {"displayName": display_name},
            "plugins": entries,
        }
    return {
        "name": marketplace_name,
        "owner": {"name": marketplace_name},
        "plugins": entries,
    }


def _build_tree(plugins, harnesses, marketplace_name):
    tree = {}
    notes = []
    plugin_names = _plugin_names(plugins)
    for harness in harnesses:
        marketplace_path = _MARKETPLACE_PATHS.get(harness)
        entries = []
        compatibility = []
        harness_notes = []
        for plugin in sorted(plugins, key=lambda item: item.meta["name"]):
            plugin_name = plugin.meta["name"]
            rendered, warnings = render(plugin, harness)
            warnings = [_rewrite_warning(warning, plugin_name) for warning in warnings]
            notes.extend(f"{plugin_name} ({harness}): {warning}" for warning in warnings)
            harness_notes.extend(f"{plugin_name}: {warning}" for warning in warnings)
            if marketplace_path is not None:
                marketplace_value = _json_value(rendered[marketplace_path][0], marketplace_path)
                plugin_entries = marketplace_value.get("plugins")
                if not isinstance(plugin_entries, list) or len(plugin_entries) != 1:
                    raise Error(f"{marketplace_path}: expected one generated plugin entry")
                entries.extend(plugin_entries)
            compatibility_value = _json_value(rendered["compatibility.json"][0], "compatibility.json")
            if isinstance(compatibility_value.get("notes"), list):
                compatibility_value["notes"] = [
                    _rewrite_warning(note, plugin_name)
                    if isinstance(note, str) else note
                    for note in compatibility_value["notes"]
                ]
            compatibility_value["marketplace"] = marketplace_name
            compatibility.append((plugin_name, compatibility_value))

            for relative, (data, mode) in rendered.items():
                if relative == marketplace_path or relative in {"INSTALL.md", "compatibility.json"}:
                    continue
                destination = _project_destination(plugin_name, relative)
                if relative == f"plugins/{plugin_name}/README.md":
                    data = _plugin_readme(plugin, harness, marketplace_name, warnings)
                put(tree, f"{harness}/{destination}", data, mode)

            put(
                tree,
                f"{harness}/projects/{plugin_name}/INSTALL.md",
                _plugin_install(plugin_name, harness, marketplace_name, warnings),
            )
            put(
                tree,
                f"{harness}/projects/{plugin_name}/compatibility.json",
                json_bytes(compatibility_value),
            )

        if marketplace_path is not None:
            put(
                tree,
                f"{harness}/{marketplace_path}",
                json_bytes(_combined_marketplace(harness, marketplace_name, entries)),
            )
        put(
            tree,
            f"{harness}/INSTALL.md",
            _combined_install(harness, marketplace_name, plugin_names, harness_notes),
        )
        put(
            tree,
            f"{harness}/compatibility.json",
            _combined_compatibility(harness, marketplace_name, compatibility),
        )
    return tree, notes


def _write_tree(root, tree):
    for relative, (data, mode) in sorted(tree.items()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(mode)


def _replace_output(output, tree):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".any-harness-build-all-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        staging = temporary / "new"
        staging.mkdir()
        _write_tree(staging, tree)
        previous = temporary / "previous"
        had_output = output.exists()
        if had_output:
            output.rename(previous)
        try:
            staging.rename(output)
        except OSError:
            if had_output and previous.exists():
                previous.rename(output)
            raise


def build_all(root, output, harnesses=HARNESSES, check=False, marketplace_name=None) -> list[str]:
    """Build every plugin below *root* into one owned multi-plugin output.

    The return value contains compatibility notes emitted by the existing
    single-plugin renderer. ``check=True`` performs the same byte, executable
    mode, and directory-shape comparison without creating or changing files.
    """
    root_input = Path(root)
    if root_input.is_symlink():
        raise Error(f"Source root may not be a symlink: {root_input}")
    root = _normal_path(root_input)
    selected = _selected_harnesses(harnesses)
    selected_output = Path(output)
    if selected_output.is_symlink():
        raise Error("Build output may not be a symlink")
    if any(parent.is_symlink() for parent in selected_output.parents):
        raise Error("Build output parents may not be symlinks; use a physical path")
    output = _normal_path(selected_output)
    if output == root:
        raise Error("Build output must be separate from source")

    source_roots = discover(root, output)
    if not source_roots:
        raise Error(f"No plugin sources found below: {root}")
    plugins = [load(source) for source in source_roots]
    plugin_names = _plugin_names(plugins)
    output = _validate_output_location(output, plugins)
    selected_name = _marketplace_name(root, marketplace_name)
    tree, notes = _build_tree(plugins, selected, selected_name)
    marker = _marker_value(selected_name, plugin_names, selected)
    put(tree, MARKER, json_bytes(marker))

    if check:
        if output.exists():
            _validate_existing_output(output, selected_name)
        actual, actual_directories = _snapshot(output)
        if actual != tree or actual_directories != _expected_directories(tree):
            raise Error("Generated output is missing or stale; run build-all and commit the result")
        return notes

    _validate_existing_output(output, selected_name)
    _replace_output(output, tree)
    return notes
