"""Native-harness evaluation runner.

The evaluator intentionally has no model or vendor SDK dependency.  It stages
the same native project files that :mod:`any_harness.install` would install and
then delegates the actual session invocation to a small adapter module.  The
adapter is responsible for preserving each harness's native CLI, permissions,
model selection, and reasoning controls.

Evaluation inputs live under ``<source>/evals``.  Assertions are read by the
runner only; they are never included in the prompt sent to a harness.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .install import native_files
from .model import Error, HARNESSES, Plugin, load, read_yaml


SCHEMA = 1
STATUSES = ("pass", "failure", "runner_error", "timeout", "skipped", "inconclusive")
INVOCATIONS = ("required", "optional", "forbidden")
MODES = ("automatic", "explicit")
ASSERTION_TYPES = {
    "output_contains",
    "output_not_contains",
    "file_exists",
    "file_contains",
    "file_not_exists",
    "unchanged",
    "check",
    # Structured evidence supplied by adapters.  Unknown evidence is never
    # treated as success.
    "action_forbidden",
    "tool_not_used",
}
HARNESS_COMMANDS = {
    "claude-code": ("claude",),
    "codex": ("codex",),
    "opencode": ("opencode",),
    # Cursor has used both names in documented releases; adapters can provide
    # an exact executable through capabilities().
    "cursor": ("cursor-agent", "agent"),
}
CONFIG_ENV_NAMES = {
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "OPENCODE_CONFIG_DIR",
    "CURSOR_CONFIG_DIR",
    "CI",
    "NO_COLOR",
    "PATH",
}
SECRET_ENV_RE = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|KEY|AUTH|COOKIE|CREDENTIAL)", re.I)
FORBIDDEN_FIXTURE_NAMES = {
    ".git",
    ".any-harness",
    ".claude",
    ".cursor",
    ".opencode",
    ".agents",
    ".codex",
    "cases.yaml",
    "graders",
    "checks",
}
ADAPTER_MODULES = (".eval_adapters",)


class EvalError(Error):
    """Invalid suite or evaluation configuration."""


def _mapping(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise EvalError(f"{where}: expected a mapping")
    return value


def _list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise EvalError(f"{where}: expected an array")
    return value


def _string(value: Any, where: str, *, empty: bool = False, limit: int = 4096) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise EvalError(f"{where}: expected a nonempty string")
    if len(value) > limit:
        raise EvalError(f"{where}: exceeds {limit} characters")
    return value


def _eval_id(value: Any, where: str) -> str:
    value = _string(value, where, limit=128)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise EvalError(f"{where}: use lowercase letters, digits and single hyphens")
    return value


def _bool(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise EvalError(f"{where}: expected a boolean")
    return value


def _int(value: Any, where: str, *, minimum: int = 0, maximum: int = 10000) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise EvalError(f"{where}: expected an integer from {minimum} to {maximum}")
    return value


def _keys(value: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise EvalError(f"{where}: unknown fields {', '.join(sorted(map(str, unknown)))}")


def _relative(value: Any, where: str, *, allow_file: bool = True) -> str:
    value = _string(value, where, limit=1024)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise EvalError(f"{where}: path must be relative and stay inside the suite")
    if not allow_file and len(path.parts) == 0:
        raise EvalError(f"{where}: expected a directory path")
    return path.as_posix()


def _relative_source(value: Any, where: str) -> str:
    """Validate a variant source while permitting an explicit sibling path."""
    value = _string(value, where, limit=2048)
    path = Path(value)
    if path.is_absolute() or "" in path.parts:
        raise EvalError(f"{where}: path must be relative")
    return path.as_posix()


def _safe_resolve(root: Path, relative: str, where: str, *, must_exist: bool = True) -> Path:
    """Resolve a suite-relative path without following links outside root."""
    root = root.absolute()
    path = root / relative
    if path.is_symlink():
        raise EvalError(f"{where}: symlinks are not supported: {relative}")
    if must_exist and not path.exists():
        raise EvalError(f"{where}: path does not exist: {relative}")
    if path.exists() and path.resolve() != root.resolve() and root.resolve() not in path.resolve().parents:
        raise EvalError(f"{where}: path escapes the suite: {relative}")
    # Inspect every component, including a dangling link.
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise EvalError(f"{where}: symlinks are not supported: {relative}")
    return path


def _validate_file_relative(value: Any, where: str) -> str:
    return _relative(value, where)


def _validate_component(value: Any, where: str) -> Dict[str, Any]:
    component = _mapping(value, where)
    _keys(component, {"kind", "name"}, where)
    kind = _string(component.get("kind"), f"{where}.kind")
    if kind not in {"skill", "agent", "plugin"}:
        raise EvalError(f"{where}.kind: expected skill, agent, or plugin")
    if kind == "plugin":
        if "name" in component:
            _string(component["name"], f"{where}.name")
    elif "name" not in component:
        raise EvalError(f"{where}.name: required for a skill or agent")
    else:
        _string(component["name"], f"{where}.name")
    return {"kind": kind, **({"name": component["name"]} if "name" in component else {})}


def _validate_assertion(value: Any, where: str, *, constraint: bool = False) -> Dict[str, Any]:
    assertion = _mapping(value, where)
    kind = _string(assertion.get("type"), f"{where}.type")
    if kind not in ASSERTION_TYPES:
        raise EvalError(f"{where}.type: unsupported assertion {kind!r}")
    fields = {
        "output_contains": {"type", "text", "value"},
        "output_not_contains": {"type", "text", "value"},
        "file_exists": {"type", "path"},
        "file_not_exists": {"type", "path"},
        "file_contains": {"type", "path", "text", "value"},
        "unchanged": {"type", "path"},
        "action_forbidden": {"type", "action", "value"},
        "tool_not_used": {"type", "name", "value"},
        "check": {"type", "script", "path", "args", "timeout"},
    }[kind]
    _keys(assertion, fields, where)
    if "text" in assertion and "value" in assertion:
        raise EvalError(f"{where}: use either text or value, not both")
    if kind in {"output_contains", "output_not_contains", "file_contains"} and not ({"text", "value"} & set(assertion)):
        raise EvalError(f"{where}: text (or value) is required")
    if kind in {"file_exists", "file_not_exists", "file_contains", "unchanged"} and "path" not in assertion:
        raise EvalError(f"{where}.path: required")
    if kind == "action_forbidden" and not ({"action", "value"} & set(assertion)):
        raise EvalError(f"{where}: action (or value) is required")
    if kind == "tool_not_used" and not ({"name", "value"} & set(assertion)):
        raise EvalError(f"{where}: name (or value) is required")
    result: Dict[str, Any] = {"type": kind}
    if kind in {"file_exists", "file_not_exists", "file_contains", "unchanged"}:
        result["path"] = _validate_file_relative(assertion.get("path"), f"{where}.path")
    elif "path" in assertion:
        result["path"] = _validate_file_relative(assertion["path"], f"{where}.path")
    if kind in {"output_contains", "output_not_contains", "file_contains"}:
        result["text"] = _string(assertion.get("text", assertion.get("value")), f"{where}.text", empty=True)
    if kind == "action_forbidden":
        result["action"] = _string(assertion.get("action", assertion.get("value")), f"{where}.action")
    if kind == "tool_not_used":
        result["name"] = _string(assertion.get("name", assertion.get("value")), f"{where}.name")
    if kind == "check":
        script = assertion.get("script", assertion.get("path"))
        result["script"] = _relative(script, f"{where}.script")
        args = assertion.get("args", [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise EvalError(f"{where}.args: expected an array of strings")
        result["args"] = list(args)
        if "timeout" in assertion:
            result["timeout"] = _int(assertion["timeout"], f"{where}.timeout", minimum=1, maximum=3600)
    # Keep constraint marker internal and out of user-facing case schema.
    if constraint:
        result["constraint"] = True
    return result


def _validate_settings(value: Any, where: str) -> Dict[str, Any]:
    settings = _mapping(value, where)
    result: Dict[str, Any] = {}
    allowed = {
        "model", "effort", "variant", "thinking", "reasoning_effort", "model_reasoning_effort",
        "sandbox", "sandbox_mode", "permission_mode", "permissionMode", "force",
        "max_turns", "maxTurns", "max_budget_usd", "maxBudgetUsd", "no_session_persistence",
        "debug_file", "output_schema", "ignore_user_config", "ignore_rules",
    }
    for harness, values in settings.items():
        if harness not in HARNESSES:
            raise EvalError(f"{where}: unknown harness {harness!r}")
        values = _mapping(values, f"{where}.{harness}")
        _keys(values, allowed, f"{where}.{harness}")
        clean = {}
        for key, val in values.items():
            if isinstance(val, (str, int, float, bool)) and not (isinstance(val, float) and not math.isfinite(val)):
                clean[key] = val
            else:
                raise EvalError(f"{where}.{harness}.{key}: expected a scalar native setting")
        result[harness] = clean
    return result


def _validate_variant(value: Any, where: str, suite_root: Path) -> Dict[str, Any]:
    variant = _mapping(value, where)
    _keys(variant, {"id", "source", "exclude", "settings"}, where)
    result = {"id": _eval_id(variant.get("id"), f"{where}.id"), "exclude": [], "settings": {}}
    if "source" in variant:
        result["source"] = _relative_source(variant["source"], f"{where}.source")
        source_path_lexical = suite_root / result["source"]
        source_path = source_path_lexical.resolve()
        if source_path_lexical.is_symlink() or not source_path.is_dir():
            raise EvalError(f"{where}.source: expected a directory")
        current = suite_root
        for part in Path(result["source"]).parts:
            current = current / part
            if current.is_symlink():
                raise EvalError(f"{where}.source: symlinks are not supported")
    else:
        result["source"] = None
    excludes = _list(variant.get("exclude", []), f"{where}.exclude")
    for index, item in enumerate(excludes):
        text = _string(item, f"{where}.exclude[{index}]", limit=256)
        match = re.fullmatch(r"(skill|agent):([a-z0-9]+(?:-[a-z0-9]+)*)", text)
        if not match:
            raise EvalError(f"{where}.exclude[{index}]: expected skill:<name> or agent:<name>")
        result["exclude"].append(text)
    result["settings"] = _validate_settings(variant.get("settings", {}), f"{where}.settings")
    return result


def _validate_case(value: Any, where: str, suite_root: Path, global_harnesses: Sequence[str]) -> Dict[str, Any]:
    case = _mapping(value, where)
    allowed = {"id", "fixture", "prompt", "component", "mode", "invocation", "assertions", "constraints", "human_review", "harnesses"}
    _keys(case, allowed, where)
    result: Dict[str, Any] = {
        "id": _eval_id(case.get("id"), f"{where}.id"),
        "fixture": _relative(case.get("fixture"), f"{where}.fixture"),
        "prompt": _string(case.get("prompt"), f"{where}.prompt", limit=100000),
        "component": _validate_component(case.get("component"), f"{where}.component"),
        "mode": _string(case.get("mode"), f"{where}.mode"),
        "invocation": case.get("invocation", "optional"),
        "assertions": [],
        "human_review": case.get("human_review", False),
    }
    result["mode"] = _string(result["mode"], f"{where}.mode")
    if result["mode"] not in MODES:
        raise EvalError(f"{where}.mode: expected automatic or explicit")
    result["invocation"] = _string(result["invocation"], f"{where}.invocation")
    if result["invocation"] not in INVOCATIONS:
        raise EvalError(f"{where}.invocation: expected required, optional, or forbidden")
    _bool(result["human_review"], f"{where}.human_review")
    fixture = _safe_resolve(suite_root, result["fixture"], f"{where}.fixture")
    if fixture.resolve() == suite_root.resolve():
        raise EvalError(f"{where}.fixture: fixture must be a child workspace, not the eval suite root")
    if Path(result["fixture"]).parts and Path(result["fixture"]).parts[0] in FORBIDDEN_FIXTURE_NAMES:
        raise EvalError(f"{where}.fixture: reserved suite/config path")
    if not fixture.is_dir():
        raise EvalError(f"{where}.fixture: expected a directory")
    _validate_fixture_tree(fixture, where)
    assertions = _list(case.get("assertions", []), f"{where}.assertions")
    for index, assertion in enumerate(assertions):
        result["assertions"].append(_validate_assertion(assertion, f"{where}.assertions[{index}]"))
    constraints = _list(case.get("constraints", []), f"{where}.constraints")
    for index, assertion in enumerate(constraints):
        result["assertions"].append(_validate_assertion(assertion, f"{where}.constraints[{index}]", constraint=True))
    selected = case.get("harnesses", list(global_harnesses))
    selected = _list(selected, f"{where}.harnesses")
    if not selected:
        raise EvalError(f"{where}.harnesses: expected at least one harness")
    for index, harness in enumerate(selected):
        _string(harness, f"{where}.harnesses[{index}]")
        if harness not in HARNESSES:
            raise EvalError(f"{where}.harnesses[{index}]: unknown harness {harness!r}")
    result["harnesses"] = list(dict.fromkeys(selected))
    return result


def _validate_fixture_tree(fixture: Path, where: str) -> None:
    """Reject fixture links and config roots which could contaminate a run."""
    for item in sorted(fixture.rglob("*")):
        relative = item.relative_to(fixture)
        if item.is_symlink():
            raise EvalError(f"{where}.fixture: symlinks are not supported: {relative}")
        if relative.parts and relative.parts[0] in FORBIDDEN_FIXTURE_NAMES:
            raise EvalError(f"{where}.fixture: native config root is reserved: {relative.parts[0]}")
        if not item.is_file() and not item.is_dir():
            raise EvalError(f"{where}.fixture: not a regular file or directory: {relative}")


def _load_suite(source: Path) -> Dict[str, Any]:
    lexical_source = Path(source).absolute()
    if lexical_source.is_symlink():
        raise EvalError(f"Source may not be a symlink: {lexical_source}")
    source = lexical_source.resolve()
    plugin = load(source)
    suite_root = source / "evals"
    if suite_root.is_symlink():
        raise EvalError(f"Evaluation suite may not be a symlink: {suite_root}")
    cases_path = suite_root / "cases.yaml"
    if not cases_path.is_file() or cases_path.is_symlink():
        raise EvalError(f"Missing regular evaluation suite: {cases_path}")
    document = read_yaml(cases_path.read_text(encoding="utf-8"), str(cases_path))
    _keys(document, {"schema", "harnesses", "cases", "variants"}, str(cases_path))
    if type(document.get("schema")) is not int or document["schema"] != SCHEMA:
        raise EvalError(f"{cases_path}: schema must be {SCHEMA}")
    harnesses = document.get("harnesses", list(HARNESSES))
    harnesses = _list(harnesses, f"{cases_path}.harnesses")
    if not harnesses:
        raise EvalError(f"{cases_path}.harnesses: expected at least one harness")
    for index, harness in enumerate(harnesses):
        _string(harness, f"{cases_path}.harnesses[{index}]")
        if harness not in HARNESSES:
            raise EvalError(f"{cases_path}.harnesses[{index}]: unknown harness {harness!r}")
    harnesses = list(dict.fromkeys(harnesses))
    cases = _list(document.get("cases"), f"{cases_path}.cases")
    if not cases:
        raise EvalError(f"{cases_path}.cases: expected at least one case")
    clean_cases = [_validate_case(value, f"{cases_path}.cases[{index}]", suite_root, harnesses) for index, value in enumerate(cases)]
    ids = [case["id"] for case in clean_cases]
    if len(set(ids)) != len(ids):
        raise EvalError(f"{cases_path}.cases: ids must be unique")
    variants = document.get("variants", [])
    variants = _list(variants, f"{cases_path}.variants")
    clean_variants = [_validate_variant(value, f"{cases_path}.variants[{index}]", suite_root) for index, value in enumerate(variants)]
    variant_ids = [variant["id"] for variant in clean_variants]
    if len(set(variant_ids)) != len(variant_ids):
        raise EvalError(f"{cases_path}.variants: ids must be unique")
    if not clean_variants:
        clean_variants = [{"id": "base", "source": None, "exclude": [], "settings": {}}]
    # Validate every version before any native process starts.  Exclusions may
    # intentionally remove a component for a negative activation case, but the
    # component must exist in the source being compared.
    for index, variant in enumerate(clean_variants):
        variant_source = source if not variant.get("source") else (suite_root / variant["source"]).resolve()
        if variant_source != source.resolve() and source.parent.resolve() not in variant_source.parents:
            raise EvalError(f"{cases_path}.variants[{index}].source: sibling source must stay beside the plugin source")
        variant_plugin = load(variant_source)
        available = {f"skill:{item.name}" for item in variant_plugin.skills} | {f"agent:{item.name}" for item in variant_plugin.agents}
        missing = sorted(set(variant.get("exclude", ())) - available)
        if missing:
            raise EvalError(f"{cases_path}.variants[{index}].exclude: component not found: {', '.join(missing)}")
    # Validate trusted executable checks now so a malformed grader cannot be
    # discovered after a native model session has already run.
    for case in clean_cases:
        for assertion in case["assertions"]:
            if assertion["type"] == "check":
                script = _safe_resolve(suite_root, assertion["script"], f"{cases_path}: check.script")
                if not script.is_file() or script.is_symlink():
                    raise EvalError(f"check.script must be a regular file: {assertion['script']}")
                if not (script.stat().st_mode & stat.S_IXUSR):
                    raise EvalError(f"check.script must be executable: {assertion['script']}")
                script_path = script.resolve()
                fixture_path = (suite_root / case["fixture"]).resolve()
                if fixture_path == script_path or fixture_path in script_path.parents:
                    raise EvalError(f"check.script must be outside the fixture workspace: {assertion['script']}")
    return {"source": source, "plugin": plugin, "suite_root": suite_root, "cases": clean_cases, "harnesses": harnesses, "variants": clean_variants, "cases_path": cases_path}


def load_suite(source: Any) -> Dict[str, Any]:
    """Load and strictly validate an evaluation suite.

    The returned mapping contains the already parsed plugin and normalized
    cases.  It is intentionally a plain mapping so callers can serialize a
    preview without depending on dataclass internals.
    """

    return _load_suite(Path(source))


def _iter_files(root: Path, *, exclude_dirs: Sequence[str] = ()) -> Iterable[Path]:
    if root.is_symlink():
        raise EvalError(f"Symlinks are not supported: {root}")
    if not root.exists():
        return
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root)
        if any(part in exclude_dirs for part in relative.parts):
            continue
        if item.is_symlink():
            raise EvalError(f"Symlinks are not supported: {item}")
        if item.is_file():
            yield item
        elif not item.is_dir():
            raise EvalError(f"Not a regular file: {item}")


def hash_tree(root: Path, *, exclude_dirs: Sequence[str] = ()) -> str:
    digest = hashlib.sha256()
    root = Path(root)
    for item in _iter_files(root, exclude_dirs=exclude_dirs):
        relative = item.relative_to(root).as_posix().encode()
        mode = stat.S_IMODE(item.stat().st_mode)
        data = item.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(mode.to_bytes(4, "big"))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _git_revision(source: Path) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value if re.fullmatch(r"[0-9a-fA-F]{7,64}", value) else None


def _adapter_module() -> Optional[Any]:
    for name in ADAPTER_MODULES:
        try:
            module = importlib.import_module(name, __package__)
        except (ImportError, ModuleNotFoundError):
            continue
        if all(hasattr(module, attr) for attr in ("prepare", "inspect_output", "capabilities")):
            return module
    return None


def _capabilities(adapter: Any, harness: str) -> Dict[str, Any]:
    try:
        value = adapter.capabilities()
    except TypeError:
        value = adapter.capabilities(harness)
    except Exception as exc:  # adapter errors are surfaced in preview, not hidden
        return {"error": f"capabilities failed: {type(exc).__name__}: {exc}"}
    if isinstance(value, Mapping) and harness in value and isinstance(value[harness], Mapping):
        value = value[harness]
    if not isinstance(value, Mapping):
        return {"value": value}
    # Make a JSON-safe copy while avoiding accidental secret/config values.
    return _jsonable(value)


def _executable(capabilities: Mapping[str, Any], harness: str) -> Optional[str]:
    configured = capabilities.get("executable", capabilities.get("command"))
    candidates: List[str] = []
    if isinstance(configured, str) and configured.strip():
        candidates.append(configured)
    elif isinstance(configured, (list, tuple)) and configured and isinstance(configured[0], str):
        candidates.append(configured[0])
    candidates.extend(HARNESS_COMMANDS.get(harness, ()))
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _version(executable: Optional[str], capabilities: Mapping[str, Any]) -> Optional[str]:
    if not executable:
        return None
    argv = capabilities.get("version_argv")
    if isinstance(argv, (list, tuple)) and argv and all(isinstance(v, str) for v in argv):
        command = list(argv)
    else:
        command = [executable, "--version"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or completed.stderr).strip()
    if not text:
        return None
    return text.splitlines()[0][:512]


def _version_support_reason(version: Optional[str], capabilities: Mapping[str, Any]) -> Optional[str]:
    supported = capabilities.get("supported_major")
    if supported is None:
        return None
    if version is None:
        return "installed harness version is unavailable; supported-major validation is inconclusive"
    match = re.search(r"(?<!\d)(\d+)(?:\.\d+)?", version)
    if not match:
        return "installed harness version could not be parsed for supported-major validation"
    try:
        major = int(match.group(1))
    except ValueError:
        return "installed harness version could not be parsed for supported-major validation"
    allowed = {supported} if isinstance(supported, int) else set(supported) if isinstance(supported, (list, tuple, set)) else set()
    if allowed and major not in allowed:
        return f"harness major version {major} is unsupported; adapter supports {sorted(allowed)}"
    return None


def _safe_copy_tree(source: Path, destination: Path) -> None:
    source = Path(source)
    if source.is_symlink():
        raise EvalError(f"Symlinks are not supported: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if item.is_symlink():
            raise EvalError(f"Symlinks are not supported: {item}")
        target = destination / relative
        if item.is_dir():
            target.mkdir()
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        else:
            raise EvalError(f"Not a regular file or directory: {item}")


def _safe_path(root: Path, relative: str, where: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise EvalError(f"{where}: workspace root is not a regular directory")
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise EvalError(f"{where}: path escapes workspace")
    resolved_root = root.resolve()
    if path.exists() and path.resolve() != resolved_root and resolved_root not in path.resolve().parents:
        raise EvalError(f"{where}: path escapes workspace")
    return path


def _stage_fixture(fixture: Path, workspace: Path) -> None:
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _safe_copy_tree(fixture, workspace)


def _snapshot_workspace(root: Path) -> Dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise EvalError(f"Workspace root is not a regular directory: {root}")
    snapshot = {}
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            snapshot[relative] = "<symlink>"
        elif item.is_file():
            digest = hashlib.sha256(item.read_bytes()).hexdigest()
            snapshot[relative] = f"{stat.S_IMODE(item.stat().st_mode):04o}:{digest}"
        elif item.is_dir():
            snapshot[relative] = f"dir:{stat.S_IMODE(item.stat().st_mode):04o}"
        elif not item.is_dir():
            snapshot[relative] = "<special>"
    return snapshot


def _capture_workspace(root: Path, destination: Path) -> Optional[str]:
    if root.is_symlink() or not root.is_dir():
        return "workspace root is not a regular directory"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        for item in sorted(root.rglob("*")):
            relative = item.relative_to(root)
            target = destination / relative
            if item.is_symlink():
                return f"artifact contains symlink: {relative}"
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            else:
                return f"artifact contains special file: {relative}"
    except OSError as exc:
        return f"artifact capture failed: {exc}"
    return None


def _safe_result_path(out: Path, source: Path, suite_root: Path) -> Path:
    lexical = Path(out).absolute()
    if lexical.is_symlink():
        raise EvalError(f"Result output may not be a symlink: {lexical}")
    # Resolve ordinary OS aliases such as macOS /var -> /private/var.  A
    # symlink in the final output itself is rejected above; parent aliases are
    # canonicalized so a safe temporary path is not rejected merely for using
    # the platform's conventional spelling.
    out = lexical.resolve()
    source_resolved = source.resolve()
    suite_resolved = suite_root.resolve()
    out_resolved = out.resolve() if out.exists() else out
    if out_resolved == source_resolved or source_resolved in out_resolved.parents or out_resolved == suite_resolved or suite_resolved in out_resolved.parents:
        raise EvalError("Result output must be outside source and its eval suite")
    return out


def _config_summary(env: Mapping[str, str], config_mode: str, config_dir: Optional[Path]) -> Dict[str, Any]:
    prefixes = ("CODEX_", "CLAUDE_", "ANTHROPIC_", "OPENAI_", "OPENCODE_", "CURSOR_")
    names = sorted(key for key in env if key in CONFIG_ENV_NAMES or key.startswith("ANY_HARNESS_EVAL_") or key.startswith(prefixes))
    values = {}
    for key in names:
        value = env.get(key, "")
        if SECRET_ENV_RE.search(key):
            values[key] = "<redacted>"
        else:
            values[key] = hashlib.sha256(value.encode()).hexdigest()
    return {
        "mode": config_mode,
        "config_dir": str(config_dir) if config_dir else None,
        "environment_keys": names,
        "environment_value_hashes": values,
        "inherited_environment": config_mode == "inherit",
        "environment_policy": "inherited user environment with isolated config roots/removals" if config_mode == "isolated" else "inherited user environment and native configuration",
    }


def _config_fingerprint(env: Mapping[str, str], config_mode: str, harness: str) -> Dict[str, Any]:
    """Hash only documented, non-secret configuration inputs.

    Runtime caches, databases, logs, and auth/session material are deliberately
    excluded.  A missing or unknown config root is represented explicitly so a
    report never implies full configuration isolation from a partial hash.
    """
    roots = []
    key = {"claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "opencode": "OPENCODE_CONFIG_DIR", "cursor": "CURSOR_CONFIG_DIR"}.get(harness)
    if key and env.get(key):
        roots.append(Path(env[key]))
    elif harness == "claude-code":
        roots.append(Path(env.get("HOME", str(Path.home()))) / ".claude")
    elif harness == "codex":
        roots.append(Path(env.get("HOME", str(Path.home()))) / ".codex")
    elif harness == "opencode":
        roots.append(Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "opencode")
    elif harness == "cursor":
        roots.append(Path(env.get("HOME", str(Path.home()))) / ".cursor")
    allowed_names = {"config.toml", "settings.json", "settings.local.json", "opencode.json", "opencode.jsonc", "AGENTS.md", "cli-config.json"}
    excluded_words = ("auth", "token", "secret", "credential", "session", "sqlite", "database", "db", "log")
    digest = hashlib.sha256()
    files_seen = []
    for root in roots:
        if not root.exists() or root.is_symlink():
            continue
        if not root.is_dir():
            continue
        # Read only documented root-level files.  Walking an inherited Codex
        # home would otherwise traverse caches, databases, and transcripts.
        for filename in sorted(allowed_names):
            item = root / filename
            if item.is_symlink() or not item.is_file() or item.name not in allowed_names:
                continue
            if any(word in item.name.lower() for word in excluded_words):
                continue
            try:
                data = item.read_bytes()
            except OSError:
                continue
            relative = str(item.relative_to(root)).replace("\\", "/")
            digest.update(relative.encode())
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
            files_seen.append(relative)
    return {
        "scope": "documented non-secret config files",
        "roots": [str(root) for root in roots],
        "files": files_seen,
        "hash": digest.hexdigest() if files_seen else None,
        # Native clients may also read managed settings, installed component
        # indexes, and provider-specific files that are intentionally outside
        # this conservative allow-list.  Mark the fingerprint partial even
        # when a known root exists.
        "complete": False,
    }


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, Mapping):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_limited(path: Path, limit: int = 2 * 1024 * 1024) -> Tuple[str, bool]:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


def _invoke(adapter: Any, harness: str, workspace: Path, config_dir: Path, settings: Mapping[str, Any], component: Mapping[str, Any], mode: str, prompt: str, config_mode: str, stdout_path: Path, stderr_path: Path, timeout: float, executable: Optional[str] = None) -> Dict[str, Any]:
    started = time.monotonic()
    prepared = adapter.prepare(harness, workspace, config_dir, dict(settings), config_mode, dict(component), mode, prompt)
    if not isinstance(prepared, Mapping):
        raise EvalError("adapter.prepare must return a mapping")
    argv = prepared.get("argv")
    unsupported = prepared.get("unsupported")
    if unsupported:
        argv = list(argv) if isinstance(argv, (list, tuple)) else []
    elif not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(item, str) or not item for item in argv):
        raise EvalError("adapter.prepare returned an invalid argv")
    env_overrides = prepared.get("env", {})
    if not isinstance(env_overrides, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env_overrides.items()):
        raise EvalError("adapter.prepare returned invalid environment overrides")
    env_remove = prepared.get("env_remove", [])
    if not isinstance(env_remove, (list, tuple)) or any(not isinstance(key, str) for key in env_remove):
        raise EvalError("adapter.prepare returned invalid environment removals")
    if executable and argv:
        argv = list(argv)
        argv[0] = executable
    env = os.environ.copy()
    if config_mode == "isolated":
        # Keep authentication/provider variables available, but remove native
        # config/cache selectors that could silently pull in user state.
        for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "OPENCODE_CONFIG_DIR", "CURSOR_CONFIG_DIR"):
            env.pop(key, None)
        env["HOME"] = str(config_dir / "home")
        env["XDG_CONFIG_HOME"] = str(config_dir / "xdg-config")
        env["XDG_DATA_HOME"] = str(config_dir / "xdg-data")
        env["XDG_CACHE_HOME"] = str(config_dir / "xdg-cache")
    env.update(env_overrides)
    for key in env_remove:
        env.pop(key, None)
    env["ANY_HARNESS_EVAL"] = "1"
    notes = list(prepared.get("notes", [])) if isinstance(prepared.get("notes", []), (list, tuple)) else []
    base = {
        "argv": list(argv),
        "requested_settings": dict(settings),
        "adapter_notes": [str(note) for note in notes],
        "unsupported": str(unsupported) if unsupported else None,
        "config": _config_summary(env, config_mode, config_dir if config_mode == "isolated" else None),
        "config_fingerprint": _config_fingerprint(env, config_mode, harness),
        "duration_seconds": None,
        "exit_status": None,
        "timed_out": False,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    if unsupported:
        stdout_path.touch()
        stderr_path.touch()
        base["status"] = "skipped"
        base["reason"] = str(unsupported)
        return base
    stdout_handle = stdout_path.open("wb")
    stderr_handle = stderr_path.open("wb")
    process = None
    process_group = None
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=str(workspace),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )
        if hasattr(os, "getpgid"):
            try:
                process_group = os.getpgid(process.pid)
            except OSError:
                process_group = None
        try:
            exit_status = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            base["timed_out"] = True
            try:
                if process_group and hasattr(os, "killpg"):
                    os.killpg(process_group, signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                pass
            process.wait(timeout=5)
            exit_status = process.returncode
        base["exit_status"] = exit_status
    except OSError as exc:
        base["runner_error"] = f"failed to start harness: {exc}"
    finally:
        # A harness can leave a helper/server child behind after its CLI exits.
        # Each invocation owns a fresh process group, so clean that group before
        # the temporary workspace is removed.  This is best-effort because a
        # native harness may have already reaped its children.
        if process_group and hasattr(os, "killpg"):
            try:
                os.killpg(process_group, signal.SIGTERM)
                time.sleep(0.05)
                os.killpg(process_group, signal.SIGKILL)
            except OSError:
                pass
        stdout_handle.close()
        stderr_handle.close()
    base["duration_seconds"] = round(time.monotonic() - started, 6)
    try:
        base["stdout"], stdout_truncated = _read_limited(stdout_path)
        base["stderr"], stderr_truncated = _read_limited(stderr_path)
        base["output_truncated"] = stdout_truncated or stderr_truncated
    except OSError as exc:
        base["runner_error"] = f"failed to read harness output: {exc}"
        base["stdout"] = ""
        base["stderr"] = ""
    if base.get("runner_error"):
        return base
    try:
        inspection = adapter.inspect_output(harness, base["stdout"], base["stderr"], dict(component))
    except Exception as exc:
        base["runner_error"] = f"adapter.inspect_output failed: {type(exc).__name__}: {exc}"
        return base
    if not isinstance(inspection, Mapping):
        base["runner_error"] = "adapter.inspect_output must return a mapping"
        return base
    if inspection.get("error"):
        base["inspection_error"] = str(inspection.get("error"))
    base.update({
        "normalized_output": str(inspection.get("output", base["stdout"])),
        "invocation": inspection.get("invocation", "unknown") if inspection.get("invocation", "unknown") in {"observed", "unknown"} else "unknown",
        "activation": (inspection.get("activation") if inspection.get("activation") in {"observed", "unknown"} else (inspection.get("invocation") if mode == "automatic" and inspection.get("invocation") in {"observed", "unknown"} else "unknown")),
        "completed": inspection.get("completed"),
        "observed_model": inspection.get("observed_model"),
        "observed_effort": inspection.get("observed_effort"),
        "tokens": inspection.get("tokens"),
        "cost": inspection.get("cost"),
        "actions": inspection.get("actions", None),
        "tool_calls": inspection.get("tool_calls", None),
        "tool_trace_complete": inspection.get("tool_trace_complete", False),
    })
    if base.get("output_truncated"):
        base["tool_trace_complete"] = False
    return base


def _assertions(case: Mapping[str, Any], run: Mapping[str, Any], workspace: Path, before: Mapping[str, str], suite_root: Path, *, check_timeout: float) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    details: List[Dict[str, Any]] = []
    output = str(run.get("normalized_output", ""))
    after = _snapshot_workspace(workspace)
    for assertion in case["assertions"]:
        kind = assertion["type"]
        detail: Dict[str, Any] = {"type": kind}
        passed: Optional[bool]
        reason = None
        if kind == "output_contains":
            passed = assertion["text"] in output
        elif kind == "output_not_contains":
            passed = assertion["text"] not in output
        elif kind == "file_exists":
            path = _safe_path(workspace, assertion["path"], "assertion.path")
            passed = path.is_file() and not path.is_symlink()
        elif kind == "file_not_exists":
            path = _safe_path(workspace, assertion["path"], "assertion.path")
            passed = not path.exists() and not path.is_symlink()
        elif kind == "file_contains":
            path = _safe_path(workspace, assertion["path"], "assertion.path")
            if path.is_symlink() or not path.is_file():
                passed = False
            else:
                passed = assertion["text"] in path.read_text(encoding="utf-8", errors="replace")
        elif kind == "unchanged":
            path = _safe_path(workspace, assertion["path"], "assertion.path")
            relative = assertion["path"]
            if path.is_dir() or relative in {".", ""}:
                prefix = "" if relative in {".", ""} else relative.rstrip("/") + "/"
                before_subset = {key: value for key, value in before.items() if key == relative or key.startswith(prefix)}
                after_subset = {key: value for key, value in after.items() if key == relative or key.startswith(prefix)}
                passed = before_subset == after_subset
            else:
                passed = before.get(relative) == after.get(relative)
        elif kind == "action_forbidden":
            actions = run.get("actions")
            if not isinstance(actions, (list, tuple)):
                passed = None
                reason = "adapter did not expose action evidence"
            else:
                names = [_evidence_name(item) for item in actions]
                if assertion["action"] in names:
                    passed = False
                elif run.get("tool_trace_complete") is True:
                    passed = True
                else:
                    passed = None
                    reason = "adapter did not expose a complete action trace"
        elif kind == "tool_not_used":
            calls = run.get("tool_calls")
            if not isinstance(calls, (list, tuple)):
                passed = None
                reason = "adapter did not expose tool evidence"
            else:
                names = [_evidence_name(item) for item in calls]
                if assertion["name"] in names:
                    passed = False
                elif run.get("tool_trace_complete") is True:
                    passed = True
                else:
                    passed = None
                    reason = "adapter did not expose a complete tool trace"
        elif kind == "check":
            script = _safe_resolve(suite_root, assertion["script"], "check.script")
            timeout = min(check_timeout, float(assertion.get("timeout", check_timeout)))
            started = time.monotonic()
            check_process = None
            try:
                check_process = subprocess.Popen(
                    [str(script), *assertion.get("args", [])],
                    cwd=str(workspace),
                    env={**os.environ, "ANY_HARNESS_EVAL_WORKSPACE": str(workspace)},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                check_stdout, check_stderr = check_process.communicate(timeout=timeout)
                passed = check_process.returncode == 0
                detail["exit_status"] = check_process.returncode
                detail["stdout"] = (check_stdout or "")[:8192]
                detail["stderr"] = (check_stderr or "")[:8192]
            except subprocess.TimeoutExpired:
                passed = None
                reason = "check timed out"
                if check_process is not None:
                    try:
                        if hasattr(os, "killpg"):
                            os.killpg(os.getpgid(check_process.pid), signal.SIGKILL)
                        else:
                            check_process.kill()
                    except OSError:
                        pass
                    check_process.communicate()
            except OSError as exc:
                passed = None
                reason = f"check runner error: {exc}"
            detail["duration_seconds"] = round(time.monotonic() - started, 6)
        else:
            passed = None
            reason = "unsupported assertion"
        detail["passed"] = passed
        if reason:
            detail["reason"] = reason
        if "path" in assertion:
            detail["path"] = assertion["path"]
        if "text" in assertion:
            detail["text"] = assertion["text"]
        details.append(detail)
    return details, None


def _evidence_name(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("name", "tool", "action", "skill", "agent"):
            if isinstance(value.get(key), str):
                return value[key]
    return str(value)


def _status(case: Mapping[str, Any], run: Mapping[str, Any], assertion_details: Sequence[Mapping[str, Any]]) -> Tuple[str, Optional[str]]:
    if run.get("timed_out"):
        return "timeout", "harness timed out"
    if run.get("runner_error"):
        return "runner_error", str(run["runner_error"])
    if run.get("unsupported"):
        return "skipped", str(run["unsupported"])
    if run.get("inspection_error"):
        return "runner_error", str(run["inspection_error"])
    if run.get("exit_status") not in (0, None) and run.get("completed") is not True:
        return "runner_error", f"native harness exited with status {run.get('exit_status')}"
    if run.get("output_truncated"):
        return "inconclusive", "captured output was truncated before grading"
    if run.get("completed") is not True:
        return "inconclusive", "native completion evidence is unknown"
    values = [item.get("passed") for item in assertion_details]
    if any(value is False for value in values):
        return "failure", "one or more assertions failed"
    if run.get("exit_status") not in (0, None):
        return "failure", f"harness exited with status {run['exit_status']}"
    invocation = run.get("invocation", "unknown")
    activation = run.get("activation", "unknown")
    requirement = case.get("invocation", "optional")
    evidence = invocation
    if case.get("mode") == "automatic":
        evidence = activation if activation != "unknown" else invocation
    if requirement == "required" and evidence != "observed":
        return "inconclusive", "required invocation/activation evidence is unknown"
    if requirement == "forbidden":
        if evidence == "observed":
            return "failure", "forbidden component invocation was observed"
        if evidence == "unknown":
            return "inconclusive", "forbidden invocation evidence is unknown"
    if any(value is None for value in values):
        return "inconclusive", "one or more assertions have unavailable evidence"
    if case.get("human_review"):
        return "inconclusive", "human review pending"
    if not assertion_details and evidence == "unknown":
        return "inconclusive", "native invocation evidence is unknown"
    return "pass", None


def _plan(suite: Mapping[str, Any], harnesses: Optional[Sequence[str]] = None, case_ids: Optional[Sequence[str]] = None, variant_ids: Optional[Sequence[str]] = None, repeat: int = 1) -> List[Dict[str, Any]]:
    selected_harnesses = list(harnesses or suite["harnesses"])
    for harness in selected_harnesses:
        if harness not in HARNESSES:
            raise EvalError(f"Unknown harness: {harness}")
        if harness not in suite["harnesses"]:
            raise EvalError(f"Harness {harness} is not enabled by this suite")
    selected_cases = [case for case in suite["cases"] if not case_ids or case["id"] in set(case_ids)]
    if case_ids and len(selected_cases) != len(set(case_ids)):
        missing = sorted(set(case_ids) - {case["id"] for case in selected_cases})
        raise EvalError(f"Unknown case(s): {', '.join(missing)}")
    selected_variants = [variant for variant in suite["variants"] if not variant_ids or variant["id"] in set(variant_ids)]
    if variant_ids and len(selected_variants) != len(set(variant_ids)):
        missing = sorted(set(variant_ids) - {variant["id"] for variant in selected_variants})
        raise EvalError(f"Unknown variant(s): {', '.join(missing)}")
    repeat = _int(repeat, "repeat", minimum=1, maximum=100)
    result = []
    number = 0
    for case in selected_cases:
        for harness in selected_harnesses:
            if harness not in case["harnesses"]:
                continue
            for variant in selected_variants:
                for iteration in range(1, repeat + 1):
                    number += 1
                    result.append({"sequence": number, "case": case, "harness": harness, "variant": variant, "repeat": iteration})
    return result


def _preview_payload(suite: Mapping[str, Any], plan: Sequence[Mapping[str, Any]], config_mode: str = "isolated") -> Dict[str, Any]:
    adapter = _adapter_module()
    capabilities = {harness: _capabilities(adapter, harness) if adapter else {"available": False, "reason": "adapter unavailable"} for harness in suite["harnesses"]}
    executables = {harness: _executable(capabilities[harness], harness) for harness in suite["harnesses"]}
    versions = {harness: _version(executables[harness], capabilities[harness]) for harness in suite["harnesses"]}
    version_reasons = {harness: _version_support_reason(versions[harness], capabilities[harness]) if executables[harness] else None for harness in suite["harnesses"]}
    prepared_by_sequence: Dict[int, Dict[str, Any]] = {}
    if adapter:
        with tempfile.TemporaryDirectory(prefix="any-harness-eval-preview-") as preview_root:
            preview_root = Path(preview_root)
            for item in plan:
                workspace = preview_root / f"workspace-{item['sequence']}"
                config_dir = preview_root / f"config-{item['sequence']}"
                workspace.mkdir()
                config_dir.mkdir()
                settings = item["variant"]["settings"].get(item["harness"], {})
                try:
                    variant_plugin, _variant_source = _variant_plugin(suite, item["variant"])
                    prepared = adapter.prepare(item["harness"], workspace, config_dir, settings, config_mode, item["case"]["component"], item["case"]["mode"], item["case"]["prompt"])
                    if not isinstance(prepared, Mapping):
                        raise EvalError("adapter.prepare must return a mapping")
                    argv = list(prepared.get("argv", [])) if isinstance(prepared.get("argv", []), (list, tuple)) else []
                    if argv and executables.get(item["harness"]):
                        argv[0] = executables[item["harness"]]
                    prepared_by_sequence[item["sequence"]] = {
                        "argv": argv,
                        "environment_keys": sorted(str(key) for key in (prepared.get("env", {}) or {})),
                        "environment_removed": sorted(str(key) for key in (prepared.get("env_remove", []) or [])),
                        "notes": [str(note) for note in prepared.get("notes", [])] if isinstance(prepared.get("notes", []), (list, tuple)) else [],
                        "unsupported": str(prepared.get("unsupported")) if prepared.get("unsupported") else None,
                    }
                    if _native_plugin_extra(variant_plugin, item["harness"]):
                        prepared_by_sequence[item["sequence"]]["unsupported"] = "native plugin package extras are unsupported by the project-file runner"
                except Exception as exc:
                    prepared_by_sequence[item["sequence"]] = {"error": f"prepare failed: {type(exc).__name__}: {exc}"}
    return {
        "schema": SCHEMA,
        "source": str(suite["source"]),
        "source_revision": _git_revision(suite["source"]),
        "source_hash": hash_tree(suite["source"], exclude_dirs=("evals", ".git")),
        "suite_hash": hash_tree(suite["suite_root"]),
        "cases_path": str(suite["cases_path"]),
        "harnesses": list(suite["harnesses"]),
        "capabilities": capabilities,
        "executables": executables,
        "versions": versions,
        "version_support": version_reasons,
        "config_mode": config_mode,
        "runs": [
            {
                "sequence": item["sequence"],
                "case": item["case"]["id"],
                "harness": item["harness"],
                "variant": item["variant"]["id"],
                "repeat": item["repeat"],
                "mode": item["case"]["mode"],
                "component": item["case"]["component"],
                "requested_settings": item["variant"]["settings"].get(item["harness"], {}),
                "executable_available": executables.get(item["harness"]) is not None and version_reasons.get(item["harness"]) is None,
                "version_support": version_reasons.get(item["harness"]),
                "prepared": prepared_by_sequence.get(item["sequence"], {"error": "native adapter unavailable"}),
            }
            for item in plan
        ],
    }


def preview_suite(source: Any, *, harnesses: Optional[Sequence[str]] = None, case_ids: Optional[Sequence[str]] = None, variant_ids: Optional[Sequence[str]] = None, repeat: int = 1, config_mode: str = "isolated") -> Dict[str, Any]:
    suite = _load_suite(Path(source))
    plan = _plan(suite, harnesses, case_ids, variant_ids, repeat)
    if config_mode not in {"isolated", "inherit"}:
        raise EvalError("config mode must be isolated or inherit")
    return _preview_payload(suite, plan, config_mode)


def _variant_source_path(suite: Mapping[str, Any], variant: Mapping[str, Any]) -> Path:
    return suite["source"] if not variant.get("source") else (suite["suite_root"] / variant["source"]).resolve()


def _variant_plugin(suite: Mapping[str, Any], variant: Mapping[str, Any]) -> Tuple[Plugin, Path]:
    source = _variant_source_path(suite, variant)
    plugin = load(source)
    excluded_skills = {item.split(":", 1)[1] for item in variant.get("exclude", ()) if item.startswith("skill:")}
    excluded_agents = {item.split(":", 1)[1] for item in variant.get("exclude", ()) if item.startswith("agent:")}
    # Plugin is a small dataclass; only its component lists are changed.  The
    # original source remains read-only, and no evals/graders are copied into
    # the harness workspace.
    filtered = Plugin(
        root=plugin.root,
        meta=plugin.meta,
        skills=[skill for skill in plugin.skills if skill.name not in excluded_skills],
        agents=[agent for agent in plugin.agents if agent.name not in excluded_agents],
    )
    return filtered, source


def _native_plugin_extra(plugin: Plugin, harness: str) -> bool:
    root = plugin.root / "native" / harness / "plugin"
    if not root.exists():
        return False
    return any(item.is_file() and not item.is_symlink() for item in root.rglob("*"))


def _artifact_id(sequence: int, case_id: str, harness: str, variant_id: str, repeat: int) -> str:
    raw = f"{sequence:04d}-{case_id}-{harness}-{variant_id}-r{repeat}"
    if len(raw) <= 180:
        return raw
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{sequence:04d}-{case_id[:48]}-{harness}-{variant_id[:48]}-{digest}-r{repeat}"


def _run_one(item: Mapping[str, Any], suite: Mapping[str, Any], out: Path, config_mode: str, timeout: float, check_timeout: float, adapter: Any, executable: Optional[str], availability_reason: Optional[str] = None) -> Dict[str, Any]:
    case = item["case"]
    harness = item["harness"]
    variant = item["variant"]
    run_id = _artifact_id(item["sequence"], case["id"], harness, variant["id"], item["repeat"])
    run_artifacts = out / "artifacts" / run_id
    run_artifacts.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    base = {
        "id": run_id,
        "sequence": item["sequence"],
        "case": case["id"],
        "harness": harness,
        "variant": variant["id"],
        "repeat": item["repeat"],
        "mode": case["mode"],
        "component": case["component"],
        "prompt_hash": hashlib.sha256(case["prompt"].encode()).hexdigest(),
        "case_hash": _hash_json(case),
        "requested_settings": variant["settings"].get(harness, {}),
        "fixture_hash": hash_tree(suite["suite_root"] / case["fixture"]),
        "source_revision": None,
        "source_hash": None,
        "variant_source": None,
        "variant_source_hash": None,
        "suite_hash": hash_tree(suite["suite_root"]),
        "harness_version": _version(executable, _capabilities(adapter, harness)) if adapter and executable else None,
        "argv": None,
        "config": _config_summary(os.environ, config_mode, None),
        "config_fingerprint": _config_fingerprint(
            {key: value for key, value in os.environ.items() if not (config_mode == "isolated" and key in {"CLAUDE_CONFIG_DIR", "CODEX_HOME", "OPENCODE_CONFIG_DIR", "CURSOR_CONFIG_DIR"})},
            config_mode,
            harness,
        ),
        "artifact_path": str(run_artifacts / "workspace"),
        "stdout_path": str(run_artifacts / "stdout.txt"),
        "stderr_path": str(run_artifacts / "stderr.txt"),
        "duration_seconds": None,
        "tokens": None,
        "cost": None,
        "observed": {"invocation": "unknown", "activation": "unknown", "model": None, "effort": None, "tokens": None, "cost": None},
        "assertions": [],
        "notes": [],
    }
    fixture = suite["suite_root"] / case["fixture"]
    temp_parent = Path(tempfile.mkdtemp(prefix="any-harness-eval-"))
    workspace = temp_parent / "workspace"
    config_dir = temp_parent / "config"
    try:
        _validate_fixture_tree(fixture, f"case {case['id']}.fixture")
        _stage_fixture(fixture, workspace)
        plugin, variant_source = _variant_plugin(suite, variant)
        base["variant_source"] = str(variant_source)
        base["variant_source_hash"] = hash_tree(variant_source, exclude_dirs=("evals", ".git"))
        base["source_hash"] = base["variant_source_hash"]
        base["source_revision"] = _git_revision(variant_source)
        desired, notes = native_files(plugin, harness)
        base["notes"].extend(str(note) for note in notes)
        base["unsupported_features"] = [str(note) for note in notes if "unsupported" in str(note).lower() or "plugin-only" in str(note).lower() or "not generated" in str(note).lower()]
        if _native_plugin_extra(plugin, harness):
            base["unsupported_features"].append("native plugin package extras are outside this project-file evaluation")
        base["generated_hash"] = hashlib.sha256(b"".join(relative.encode() + data for relative, (data, _mode) in sorted(desired.items()))).hexdigest()
        for relative, (data, mode) in desired.items():
            destination = _safe_path(workspace, relative, "generated file")
            if destination.exists() or destination.is_symlink():
                raise EvalError(f"fixture collides with generated native file: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(mode)
        # Generated files are part of the baseline.  ``unchanged`` therefore
        # catches edits to the fixture while allowing the runner to stage the
        # native component itself.
        before = _snapshot_workspace(workspace)
        config_dir.mkdir(parents=True, exist_ok=False)
        isolated_env = dict(os.environ)
        if config_mode == "isolated":
            isolated_env["HOME"] = str(config_dir / "home")
            for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "OPENCODE_CONFIG_DIR", "CURSOR_CONFIG_DIR"):
                isolated_env.pop(key, None)
        base["config"] = _config_summary(isolated_env, config_mode, config_dir if config_mode == "isolated" else None)
        base["config_fingerprint"] = _config_fingerprint(isolated_env, config_mode, harness)
        if _native_plugin_extra(plugin, harness):
            _capture_workspace(workspace, run_artifacts / "workspace")
            base["stdout_path"] = None
            base["stderr_path"] = None
            base["status"] = "skipped"
            base["reason"] = "native plugin package extras are unsupported by the project-file runner"
            return base
        if adapter is None:
            _capture_workspace(workspace, run_artifacts / "workspace")
            base["stdout_path"] = None
            base["stderr_path"] = None
            base["status"] = "skipped"
            base["reason"] = "native adapter unavailable"
            return base
        if executable is None or availability_reason:
            _capture_workspace(workspace, run_artifacts / "workspace")
            base["stdout_path"] = None
            base["stderr_path"] = None
            base["status"] = "skipped"
            base["reason"] = availability_reason or f"{harness} executable unavailable"
            return base
        stdout_temp = temp_parent / "stdout.txt"
        stderr_temp = temp_parent / "stderr.txt"
        run = _invoke(adapter, harness, workspace, config_dir, base["requested_settings"], case["component"], case["mode"], case["prompt"], config_mode, stdout_temp, stderr_temp, timeout, executable)
        base.update({key: value for key, value in run.items() if key not in {"stdout", "stderr", "stdout_path", "stderr_path"}})
        # The temporary transcript paths are implementation details; results
        # must point to the durable copies under the result directory.
        base["stdout_path"] = str(run_artifacts / "stdout.txt")
        base["stderr_path"] = str(run_artifacts / "stderr.txt")
        # Copy transcripts before the temporary workspace/config is removed.
        shutil.copy2(stdout_temp, run_artifacts / "stdout.txt")
        shutil.copy2(stderr_temp, run_artifacts / "stderr.txt")
        capture_error = _capture_workspace(workspace, run_artifacts / "workspace")
        if capture_error:
            base["artifact_error"] = capture_error
        assertion_details, _ = _assertions(case, run, workspace, before, suite["suite_root"], check_timeout=check_timeout)
        base["assertions"] = assertion_details
        base["tokens"] = run.get("tokens")
        base["cost"] = run.get("cost")
        status, reason = _status(case, run, assertion_details)
        if capture_error:
            status, reason = "runner_error", capture_error
        base["status"] = status
        if reason:
            base["reason"] = reason
        base["observed"] = {
            "invocation": run.get("invocation", "unknown"),
            "activation": run.get("activation", "unknown"),
            "model": run.get("observed_model"),
            "effort": run.get("observed_effort"),
            "tokens": run.get("tokens"),
            "cost": run.get("cost"),
        }
        return base
    except (EvalError, Error, OSError, ValueError, TypeError) as exc:
        base["status"] = "runner_error"
        base["reason"] = str(exc)
        if base.get("stdout_path") and not Path(base["stdout_path"]).exists():
            base["stdout_path"] = None
        if base.get("stderr_path") and not Path(base["stderr_path"]).exists():
            base["stderr_path"] = None
        if base.get("artifact_path") and not Path(base["artifact_path"]).exists():
            base["artifact_path"] = None
        return base
    finally:
        base["duration_seconds"] = round(time.monotonic() - started, 6)
        shutil.rmtree(temp_parent, ignore_errors=True)


def ci_exit_code(result: Mapping[str, Any]) -> int:
    statuses = [run.get("status") for run in result.get("runs", [])]
    if not statuses or any(status not in STATUSES for status in statuses):
        return 2
    if any(status in {"runner_error", "timeout"} for status in statuses):
        return 2
    if any(status == "failure" for status in statuses):
        return 1
    if any(status in {"skipped", "inconclusive"} for status in statuses):
        return 3
    return 0


def run_suite(source: Any, out: Any, *, harnesses: Optional[Sequence[str]] = None, case_ids: Optional[Sequence[str]] = None, variant_ids: Optional[Sequence[str]] = None, repeat: int = 1, jobs: int = 1, timeout: float = 300, check_timeout: float = 30, config_mode: str = "isolated") -> Dict[str, Any]:
    if config_mode not in {"isolated", "inherit"}:
        raise EvalError("config mode must be isolated or inherit")
    jobs = _int(jobs, "jobs", minimum=1, maximum=32)
    if not math.isfinite(float(timeout)) or timeout <= 0 or timeout > 86400:
        raise EvalError("timeout must be greater than 0 and at most 86400 seconds")
    if not math.isfinite(float(check_timeout)) or check_timeout <= 0 or check_timeout > 3600:
        raise EvalError("check timeout must be greater than 0 and at most 3600 seconds")
    suite = _load_suite(Path(source))
    out = _safe_result_path(Path(out), suite["source"], suite["suite_root"])
    out_resolved = out.resolve()
    for variant in suite["variants"]:
        variant_source = _variant_source_path(suite, variant).resolve()
        if out_resolved == variant_source or variant_source in out_resolved.parents:
            raise EvalError("Result output must be outside every variant source")
    if out.exists():
        if not out.is_dir():
            raise EvalError(f"Result output must be a directory: {out}")
        if any(out.iterdir()):
            raise EvalError(f"Result output must be new or empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    plan = _plan(suite, harnesses, case_ids, variant_ids, repeat)
    adapter = _adapter_module()
    capabilities = {harness: _capabilities(adapter, harness) if adapter else {"available": False, "reason": "adapter unavailable"} for harness in suite["harnesses"]}
    executables = {harness: _executable(capabilities[harness], harness) for harness in suite["harnesses"]}
    versions = {harness: _version(executables[harness], capabilities[harness]) for harness in suite["harnesses"]}
    version_reasons = {harness: _version_support_reason(versions[harness], capabilities[harness]) if executables[harness] else None for harness in suite["harnesses"]}
    started = time.monotonic()
    runs: List[Optional[Dict[str, Any]]] = [None] * len(plan)
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="any-harness-eval") as executor:
        futures = {
            executor.submit(_run_one, item, suite, out, config_mode, float(timeout), float(check_timeout), adapter, executables.get(item["harness"]), version_reasons.get(item["harness"])): index
            for index, item in enumerate(plan)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                runs[index] = future.result()
            except Exception as exc:  # no worker exception may erase other run records
                item = plan[index]
                runs[index] = {
                    "id": _artifact_id(item["sequence"], item["case"]["id"], item["harness"], item["variant"]["id"], item["repeat"]),
                    "sequence": item["sequence"],
                    "case": item["case"]["id"],
                    "harness": item["harness"],
                    "variant": item["variant"]["id"],
                    "repeat": item["repeat"],
                    "status": "runner_error",
                    "reason": f"worker failed: {type(exc).__name__}: {exc}",
                }
    result = {
        "schema": SCHEMA,
        "tool": "any-harness",
        "source": str(suite["source"]),
        "source_revision": _git_revision(suite["source"]),
        "source_hash": hash_tree(suite["source"], exclude_dirs=("evals", ".git")),
        "suite_hash": hash_tree(suite["suite_root"]),
        "cases_path": str(suite["cases_path"]),
        "config_mode": config_mode,
        "jobs": jobs,
        "timeout_seconds": timeout,
        "check_timeout_seconds": check_timeout,
        "capabilities": capabilities,
        "executables": executables,
        "versions": versions,
        "version_support": version_reasons,
        "started_at": time.time() - (time.monotonic() - started),
        "duration_seconds": round(time.monotonic() - started, 6),
        "runs": [run for run in runs if run is not None],
    }
    groups: Dict[Tuple[str, str, str], List[Mapping[str, Any]]] = {}
    for run in result["runs"]:
        groups.setdefault((run.get("case", ""), run.get("harness", ""), run.get("variant", "")), []).append(run)
    variability = []
    for key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        signatures = [
            {
                "status": run.get("status"),
                "invocation": (run.get("observed") or {}).get("invocation"),
                "activation": (run.get("observed") or {}).get("activation"),
                "model": (run.get("observed") or {}).get("model"),
                "effort": (run.get("observed") or {}).get("effort"),
                "assertions": [item.get("passed") for item in run.get("assertions", [])],
            }
            for run in group
        ]
        variability.append({"case": key[0], "harness": key[1], "variant": key[2], "repeats": len(group), "stable": all(signature == signatures[0] for signature in signatures[1:]), "observations": signatures})
    result["variability"] = variability
    result["status"] = "pass" if ci_exit_code(result) == 0 else ("failure" if ci_exit_code(result) == 1 else "inconclusive" if ci_exit_code(result) == 3 else "runner_error")
    write_result(result, out)
    return result


def write_result(result: Mapping[str, Any], out: Any) -> Tuple[Path, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / "result.json"
    report_path = out / "report.md"
    result_path.write_text(json.dumps(_jsonable(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(render_report(result), encoding="utf-8")
    return result_path, report_path


def read_result(path: Any) -> Dict[str, Any]:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalError(f"Invalid result JSON: {path}: {exc}") from exc
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value.get("schema") != SCHEMA or not isinstance(value.get("runs"), list):
        raise EvalError(f"Invalid result schema: {path}")
    for index, run in enumerate(value["runs"]):
        if not isinstance(run, dict) or run.get("status") not in STATUSES:
            raise EvalError(f"Invalid result run status at index {index}: {path}")
    return value


def render_report(result: Mapping[str, Any]) -> str:
    runs = list(result.get("runs", []))
    counts = {status: sum(1 for run in runs if run.get("status") == status) for status in STATUSES}
    lines = [
        "# any-harness evaluation report",
        "",
        f"Status: **{result.get('status', 'unknown')}**",
        f"Runs: {len(runs)} | " + ", ".join(f"{status}={counts[status]}" for status in STATUSES if counts[status]),
        f"Source revision: `{result.get('source_revision') or 'unavailable'}`",
        f"Source hash: `{result.get('source_hash', 'unavailable')}`",
        f"Suite hash: `{result.get('suite_hash', 'unavailable')}`",
        f"Config mode: `{result.get('config_mode', 'unknown')}`",
        "",
        "| Case | Harness | Variant | Repeat | Status | Assertions | Evidence | Artifact | Reason |",
        "| --- | --- | --- | ---: | --- | ---: | --- | --- | --- |",
    ]
    for run in runs:
        reason = str(run.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        assertions = run.get("assertions") or []
        passed = sum(1 for item in assertions if item.get("passed") is True)
        evidence = run.get("observed") or {}
        evidence_text = f"invoke={evidence.get('invocation', 'unknown')}; model={evidence.get('model') or 'unknown'}"
        artifact = run.get("artifact_path") or "unavailable"
        lines.append(f"| {run.get('case', '')} | {run.get('harness', '')} | {run.get('variant', '')} | {run.get('repeat', '')} | {run.get('status', '')} | {passed}/{len(assertions)} | {evidence_text} | `{artifact}` | {reason} |")
    lines.extend(["", "## Capability and evidence notes", ""])
    for harness, capabilities in sorted((result.get("capabilities") or {}).items()):
        lines.append(f"- **{harness}:** {json.dumps(capabilities, ensure_ascii=False, sort_keys=True)}")
    for run in runs:
        notes = run.get("notes") or []
        if notes:
            lines.append(f"- `{run.get('id')}`: " + "; ".join(str(note) for note in notes))
    variability = result.get("variability") or []
    if variability:
        lines.extend(["", "## Repeat variability", ""])
        for item in variability:
            lines.append(f"- `{item.get('case')}` / `{item.get('harness')}` / `{item.get('variant')}`: {item.get('repeats')} repeats; stable={item.get('stable')}")
    lines.append("")
    return "\n".join(lines)


def report_result(path: Any, out: Optional[Any] = None) -> Tuple[Dict[str, Any], Path]:
    result = read_result(path)
    input_path = Path(path).absolute()
    destination = Path(out) if out else input_path.with_name("report.md")
    if destination.is_symlink():
        raise EvalError("Report output may not be a symlink")
    if destination.resolve() == input_path.resolve():
        raise EvalError("Report output must not overwrite result JSON")
    destination.write_text(render_report(result), encoding="utf-8")
    return result, destination
