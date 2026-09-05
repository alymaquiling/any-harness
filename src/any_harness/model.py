"""Strict shared authoring format. Native settings are explicit escape hatches."""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

HARNESSES = ("claude-code", "codex", "opencode", "cursor")


class Error(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise Error("YAML mapping keys must be strings")
        if key in result:
            raise Error(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def mapping(value, where):
    if not isinstance(value, dict):
        raise Error(f"{where}: expected a mapping")
    return value


def keys(value, allowed, where):
    mapping(value, where)
    unknown = set(value) - set(allowed)
    if unknown:
        raise Error(f"{where}: unknown fields {', '.join(sorted(unknown))}")


def string(value, where, limit=None):
    if not isinstance(value, str) or not value.strip():
        raise Error(f"{where}: expected a nonempty string")
    if limit and len(value) > limit:
        raise Error(f"{where}: exceeds {limit} characters")
    return value


def name(value, where):
    string(value, where, 64)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise Error(f"{where}: use lowercase letters, digits and single hyphens")
    return value


def read_yaml(text, where):
    try:
        return mapping(yaml.load(text, Loader=UniqueLoader), where)
    except yaml.YAMLError as exc:
        raise Error(f"{where}: invalid YAML: {exc}") from exc


def files(root):
    """Sorted regular files only; never follow links out of an author directory."""
    if root.is_symlink():
        raise Error(f"Symlinks are not supported: {root}")
    if not root.exists():
        return []
    result = []
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise Error(f"Symlinks are not supported: {item}")
        if item.is_file():
            result.append(item)
        elif not item.is_dir():
            raise Error(f"Not a regular file: {item}")
    return result


def targets(value, where, kind):
    keys(value, HARNESSES, where)
    for harness, options in value.items():
        keys(options, {"enabled", "settings", "body", "openai"}, f"{where}.{harness}")
        if "enabled" in options and type(options["enabled"]) is not bool:
            raise Error(f"{where}.{harness}.enabled must be a boolean")
        settings = mapping(options.get("settings", {}), f"{where}.{harness}.settings")
        if set(settings) & {"name", "description", "developer_instructions", "targets"}:
            raise Error(f"{where}: name, description, instructions and targets are managed by the generator")
        if "body" in options:
            string(options["body"], f"{where}.{harness}.body")
        if "openai" in options:
            if harness != "codex" or kind != "skill":
                raise Error(f"{where}: openai is only supported for Codex skills")
            mapping(options["openai"], f"{where}.{harness}.openai")


@dataclass
class Component:
    path: Path
    kind: str
    meta: dict
    body: str

    @property
    def name(self):
        return self.meta["name"]

    def options(self, harness):
        return self.meta.get("targets", {}).get(harness, {})


@dataclass
class Plugin:
    root: Path
    meta: dict
    skills: list
    agents: list


def component(path, kind):
    text = path.read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)\Z", text, re.S)
    if not match:
        raise Error(f"{path}: expected YAML frontmatter between --- delimiters")
    meta = read_yaml(match[1], str(path))
    common = {"name", "description", "targets"}
    if kind == "skill":
        common |= {"license", "compatibility", "metadata"}
    keys(meta, common, str(path))
    name(meta.get("name"), f"{path}: name")
    string(meta.get("description"), f"{path}: description", 1024)
    expected = path.parent.name if kind == "skill" else path.stem
    if meta["name"] != expected:
        raise Error(f"{path}: name must match {expected}")
    for key in ("license", "compatibility"):
        if key in meta:
            string(meta[key], f"{path}: {key}")
    if "metadata" in meta:
        mapping(meta["metadata"], f"{path}: metadata")
        for key, value in meta["metadata"].items():
            string(value, f"{path}: metadata.{key}")
    targets(meta.get("targets", {}), str(path), kind)
    string(match[2], f"{path}: body")
    return Component(path, kind, meta, match[2].lstrip("\n"))


def load(root):
    root = Path(root).absolute()
    if root.is_symlink():
        raise Error(f"Source may not be a symlink: {root}")
    manifest = root / "plugin.yaml"
    if not manifest.is_file() or manifest.is_symlink():
        raise Error(f"Missing regular file: {manifest}")
    meta = read_yaml(manifest.read_text(encoding="utf-8"), str(manifest))
    keys(meta, {"schema", "name", "version", "description", "author", "license", "repository", "homepage", "keywords"}, str(manifest))
    if type(meta.get("schema")) is not int or meta["schema"] != 1:
        raise Error("plugin.yaml: schema must be 1")
    name(meta.get("name"), "plugin.yaml: name")
    string(meta.get("version"), "plugin.yaml: version")
    numeric = r"(?:0|[1-9]\d*)"
    prerelease = r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    if not re.fullmatch(rf"{numeric}\.{numeric}\.{numeric}(?:-{prerelease}(?:\.{prerelease})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", meta["version"]):
        raise Error("plugin.yaml: version must be semantic versioning, e.g. 1.0.0")
    string(meta.get("description"), "plugin.yaml: description", 1024)
    keys(meta.get("author"), {"name", "email", "url"}, "plugin.yaml: author")
    string(meta["author"].get("name"), "plugin.yaml: author.name")
    for key in ("license", "repository", "homepage"):
        if key in meta:
            string(meta[key], f"plugin.yaml: {key}")
            if key in ("repository", "homepage"):
                parsed = urlsplit(meta[key])
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise Error(f"plugin.yaml: {key} must be an absolute HTTP(S) URL")
    for key, value in meta["author"].items():
        string(value, f"plugin.yaml: author.{key}")
    if "url" in meta["author"]:
        parsed = urlsplit(meta["author"]["url"])
        if parsed.scheme != "https" or not parsed.netloc:
            raise Error("plugin.yaml: author.url must be an absolute HTTPS URL")
    if "keywords" in meta:
        if not isinstance(meta["keywords"], list):
            raise Error("plugin.yaml: keywords must be an array of strings")
        for value in meta["keywords"]:
            string(value, "plugin.yaml: keyword")
    for folder in ("skills", "agents", "native"):
        files(root / folder)
    skills = [component(p, "skill") for p in sorted((root / "skills").glob("*/SKILL.md"))]
    agents = [component(p, "agent") for p in sorted((root / "agents").glob("*.md"))]
    if not skills and not agents:
        raise Error("Source must contain at least one skill or agent")
    if (root / "skills").exists():
        for directory in (root / "skills").iterdir():
            if not directory.is_dir() or not (directory / "SKILL.md").is_file():
                raise Error(f"Expected skills/<name>/SKILL.md, found {directory}")
    for path in files(root / "agents"):
        if path not in [a.path for a in agents]:
            raise Error(f"Agent resources belong inside skills; unexpected {path}")
    native = root / "native"
    if native.exists():
        for target in native.iterdir():
            if target.name not in HARNESSES or not target.is_dir():
                raise Error(f"Unknown native harness directory: {target}")
            for area in target.iterdir():
                if area.name not in {"plugin", "project"} or not area.is_dir():
                    raise Error(f"Native files must be in native/<harness>/plugin or project: {area}")
    return Plugin(root, meta, skills, agents)
