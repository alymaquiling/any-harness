"""Pure adapters and deterministic, owned build output."""
import json
from pathlib import Path
import tempfile

import tomli_w
import yaml

from .model import Error, HARNESSES, files, load


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def markdown(meta, body):
    return ("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body.rstrip() + "\n").encode()


def put(tree, path, data, mode=0o644):
    path = str(path).replace("\\", "/")
    if path in tree or any(
        path.startswith(existing + "/") or existing.startswith(path + "/")
        for existing in tree
    ):
        raise Error(f"Generated file collision: {path}")
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise Error(f"Unsafe generated path: {path}")
    tree[path] = (data, mode)


def copy(tree, source, destination, skip=()):
    for file in files(source):
        relative = file.relative_to(source).as_posix()
        if relative not in skip:
            put(tree, f"{destination}/{relative}", file.read_bytes(), 0o755 if file.stat().st_mode & 0o111 else 0o644)


def render(plugin, harness):
    """Return relative path -> (bytes, mode), plus material compatibility notes."""
    if harness not in HARNESSES:
        raise Error(f"Unknown harness: {harness}")
    tree, notes = {}, []
    meta = {k: v for k, v in plugin.meta.items() if k != "schema"}
    plugin_name = meta["name"]
    package = f"plugins/{plugin_name}"
    project = {"claude-code": ".claude", "codex": ".agents", "opencode": ".opencode", "cursor": ".cursor"}[harness]
    active_skills = 0
    active_agents = 0
    for skill in plugin.skills:
        options = skill.options(harness)
        if options.get("enabled", True) is False:
            continue
        active_skills += 1
        header = {k: v for k, v in skill.meta.items() if k != "targets"}
        header.update(options.get("settings", {}))
        body = options.get("body", skill.body)
        destinations = [f"{package}/skills/{skill.name}", f"project/{project}/skills/{skill.name}"]
        for destination in destinations:
            put(tree, f"{destination}/SKILL.md", markdown(header, body))
            copy(tree, skill.path.parent, destination, skip=("SKILL.md",))
            if "openai" in options:
                put(tree, f"{destination}/agents/openai.yaml", yaml.safe_dump(options["openai"], sort_keys=False).encode())
    for agent in plugin.agents:
        options = agent.options(harness)
        if options.get("enabled", True) is False:
            continue
        active_agents += 1
        settings = options.get("settings", {})
        project_header = {"name": agent.name, "description": agent.meta["description"]}
        project_header.update(settings)
        package_header = dict(project_header)
        if harness == "claude-code":
            unsupported = sorted(set(settings) & {"hooks", "mcpServers", "permissionMode"})
            if unsupported:
                for key in unsupported:
                    package_header.pop(key, None)
                notes.append(
                    f"Claude Code plugin agent '{agent.name}' omits unsupported fields: "
                    f"{', '.join(unsupported)}. The standalone project agent at "
                    f"project/.claude/agents/{agent.name}.md retains them; use any-harness "
                    "install for direct installation."
                )
        body = options.get("body", agent.body)
        if harness == "codex":
            project_header["developer_instructions"] = body.rstrip() + "\n"
            put(tree, f"project/.codex/agents/{agent.name}.toml", tomli_w.dumps(project_header).encode())
        else:
            if harness == "opencode":
                project_header.pop("name")
                project_header.setdefault("mode", "subagent")
                package_header = project_header
            project_content = markdown(project_header, body)
            put(tree, f"project/{project}/agents/{agent.name}.md", project_content)
            if harness != "opencode":
                put(tree, f"{package}/agents/{agent.name}.md", markdown(package_header, body))
    if harness == "codex" and active_agents:
        notes.append("Codex agents are native .codex/agents/*.toml files in project/. Plugin-only installation does not install them; use any-harness install for agents.")
    if harness == "opencode":
        # OpenCode's 'plugins' are executable JS/TS extensions, not agent bundles.
        tree = {k: v for k, v in tree.items() if not k.startswith("plugins/")}
        notes.append("OpenCode uses native skills and agent directories. No JavaScript runtime plugin is generated.")
    else:
        marker = {"claude-code": ".claude-plugin", "codex": ".codex-plugin", "cursor": ".cursor-plugin"}[harness]
        if harness == "codex":
            if active_skills:
                meta["skills"] = "./skills/"
            companion_root = plugin.root / "native" / "codex" / "plugin"
            for filename, field in ((".mcp.json", "mcpServers"), (".app.json", "apps")):
                if (companion_root / filename).is_file():
                    meta[field] = f"./{filename}"
                    notes.append(f"Codex plugin manifest wires {field} to native/codex/plugin/{filename}.")
            meta["interface"] = {
                "displayName": plugin_name.replace("-", " ").title(),
                "shortDescription": meta["description"][:120],
                "longDescription": meta["description"],
                "developerName": meta["author"]["name"],
                "category": "Productivity",
                "capabilities": [],
                "defaultPrompt": [f"Help me use {plugin_name}."],
            }
            marketplace = {"name": plugin_name, "interface": {"displayName": meta["interface"]["displayName"]}, "plugins": [{"name": plugin_name, "source": {"source": "local", "path": f"./{package}"}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}
            marketplace_path = ".agents/plugins/marketplace.json"
        else:
            if harness == "cursor":
                meta["author"] = {k: v for k, v in meta["author"].items() if k in {"name", "email"}}
                if "url" in plugin.meta["author"]:
                    notes.append("Cursor author.url omitted because its native author schema only accepts name and email.")
            marketplace = {"name": plugin_name, "owner": meta["author"], "plugins": [{"name": plugin_name, "source": f"./{package}", "description": meta["description"], "version": meta["version"]}]}
            if harness == "cursor":
                marketplace["plugins"][0].pop("version")
            marketplace_path = f"{marker}/marketplace.json"
        put(tree, f"{package}/{marker}/plugin.json", json_bytes(meta))
        put(tree, marketplace_path, json_bytes(marketplace))
        put(tree, f"{package}/README.md", (f"# {plugin_name}\n\n{meta['description']}\n\n" + install_notes(plugin_name, harness, notes)).encode())
    for area in ("plugin", "project"):
        source = plugin.root / "native" / harness / area
        if harness == "opencode" and area == "plugin" and files(source):
            raise Error("OpenCode native extensions belong in native/opencode/project/.opencode/plugins/")
        if source.exists():
            if area == "project":
                allowed = {"codex": {".agents", ".codex"}, "claude-code": {".claude"}, "cursor": {".cursor"}, "opencode": {".opencode"}}[harness]
                for file in files(source):
                    if file.relative_to(source).parts[0] not in allowed:
                        raise Error(f"Native project files must stay in {sorted(allowed)}: {file}")
            copy(tree, source, package if area == "plugin" else "project")
            notes.append(f"Native {harness}/{area} files copied verbatim; their API semantics must be validated with the harness.")
    put(tree, "INSTALL.md", install_notes(plugin_name, harness, notes).encode())
    put(tree, "compatibility.json", json_bytes({"harness": harness, "skills": active_skills, "agents": active_agents, "notes": notes}))
    return tree, notes


def install_notes(name, harness, notes):
    instructions = {
        "claude-code": f"From Claude Code, add this generated folder with `/plugin marketplace add /absolute/path/to/claude-code`, then `/plugin install {name}@{name}`. A Git repository whose root contains this generated tree can be used instead of a local path. For local testing: `claude --plugin-dir /absolute/path/to/claude-code/plugins/{name}`.",
        "codex": f"Register this generated marketplace root with `codex plugin marketplace add /absolute/path/to/codex`, then `codex plugin add {name}@{name}`. A Git repository with this generated root is also accepted as the marketplace source. This requires a Codex version supporting the plugin CLI. Agent files in project/.codex/agents require the direct installer or a separate copy; plugin installation covers skills only.",
        "cursor": "Use the direct installer for project-local skills and agents. To distribute through Cursor's marketplace, publish this generated tree at a Git repository root and follow Cursor's plugin submission process. An arbitrary Git URL is not promised to be a public marketplace install.",
        "opencode": "Use the direct installer or copy the contents of project/ into your project. OpenCode discovers .opencode/skills and .opencode/agents directly. No npm plugin or daemon is needed.",
    }
    return f"# Install {name} for {harness}\n\n{instructions[harness]}\n\n" + "\n".join(f"- {note}" for note in notes) + "\n\nRestart or open a new harness session after installation. Do not also install the same components with a second method.\n"


def generate(source, harnesses=HARNESSES):
    plugin = load(source)
    tree, notes = {}, []
    for harness in harnesses:
        rendered, warnings = render(plugin, harness)
        for path, (data, mode) in rendered.items():
            put(tree, f"{harness}/{path}", data, mode)
        notes.extend(warnings)
    return plugin, tree, notes


def write_tree(root, tree):
    for relative, (data, mode) in sorted(tree.items()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(mode)


def build(source, output, harnesses=HARNESSES, check=False):
    plugin, tree, notes = generate(source, harnesses)
    output = Path(output).absolute()
    # Never replace the source, its ancestors, or a symlinked destination.
    if output.is_symlink() or output.resolve() == plugin.root.resolve() or output.resolve() in plugin.root.resolve().parents:
        raise Error("Build output must be separate from source and its ancestors")
    if any(parent.is_symlink() for parent in output.parents):
        raise Error("Build output parents may not be symlinks; use a physical path")
    for protected in (plugin.root / "skills", plugin.root / "agents", plugin.root / "native"):
        if output.resolve() == protected.resolve() or protected.resolve() in output.resolve().parents:
            raise Error("Build output may not be inside source components")
    put(tree, ".any-harness-build.json", json_bytes({"schema": 1, "name": plugin.meta["name"], "harnesses": list(harnesses)}))
    if check:
        actual = {p.relative_to(output).as_posix(): (p.read_bytes(), 0o755 if p.stat().st_mode & 0o111 else 0o644) for p in files(output)}
        expected_dirs = {str(parent) for path in tree for parent in Path(path).parents if str(parent) != "."}
        actual_dirs = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_dir()}
        if actual != tree or actual_dirs != expected_dirs:
            raise Error("Generated output is missing or stale; run build and commit the result")
        return notes
    if output.exists():
        marker = output / ".any-harness-build.json"
        if not marker.is_file() or marker.is_symlink():
            raise Error(f"Refusing to replace unowned output directory: {output}")
        try:
            ownership = json.loads(marker.read_text(encoding="utf-8"))
            previous_harnesses = ownership["harnesses"]
            valid = (type(ownership["schema"]) is int and ownership["schema"] == 1
                     and ownership["name"] == plugin.meta["name"]
                     and isinstance(previous_harnesses, list) and bool(previous_harnesses)
                     and all(h in HARNESSES for h in previous_harnesses)
                     and len(set(previous_harnesses)) == len(previous_harnesses))
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise Error(f"Invalid build ownership marker or different plugin name: {marker}")
        # Inspect all children before any replacement, including hidden symlinks.
        files(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".any-harness-build-", dir=output.parent) as temp:
        staging = Path(temp) / "new"
        staging.mkdir()
        write_tree(staging, tree)
        previous = Path(temp) / "previous"
        if output.exists():
            output.rename(previous)
        try:
            staging.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise
    return notes
