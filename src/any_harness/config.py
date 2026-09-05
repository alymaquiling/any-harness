"""Local CLI defaults stored in ``.any-harness/config.yaml``."""

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Iterable, Optional, Tuple

import yaml

from .model import Error, HARNESSES, keys, read_yaml, string


CONFIG_DIRECTORY = ".any-harness"
CONFIG_FILENAME = "config.yaml"
CONFIG_FIELDS = {"schema", "source", "harnesses", "project", "out"}


@dataclass(frozen=True)
class Config:
    """Validated defaults and the project root they are relative to."""

    path: Path
    root: Path
    source: str
    harnesses: Tuple[str, ...]
    project: str
    out: str

    def resolve(self, value: str) -> Path:
        """Resolve a configured path relative to this config's project root."""
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.root / path
        return path.absolute()


def _config_path(root: Path) -> Path:
    return root / CONFIG_DIRECTORY / CONFIG_FILENAME


def _project_root(value) -> Path:
    try:
        raw_root = Path(value).expanduser().absolute()
    except (TypeError, ValueError, RuntimeError) as exc:
        raise Error("Config project root must be a valid path") from exc
    if raw_root.is_symlink() or any(parent.is_symlink() for parent in raw_root.parents):
        raise Error(f"Config project root may not be a symlink: {raw_root}")
    return raw_root.resolve()


def _path_text(value, where):
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    string(value, where, 4096)
    if "\x00" in value:
        raise Error(f"{where}: path contains a NUL byte")
    return value


def _harnesses(value, where="config.yaml: harnesses") -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise Error(f"{where}: expected a nonempty list")
    result = []
    for item in value:
        if not isinstance(item, str) or item not in HARNESSES:
            raise Error(f"{where}: unknown harness: {item}")
        if item in result:
            raise Error(f"{where}: duplicate harness: {item}")
        result.append(item)
    return tuple(result)


def load_config(path) -> Config:
    """Read and validate one local config file with duplicate-key checks."""
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise Error(f"Config file may not be a symlink: {path}")
    if not path.is_file():
        raise Error(f"Missing config file: {path}")
    if path.name != CONFIG_FILENAME or path.parent.name != CONFIG_DIRECTORY:
        raise Error(f"Config file must be {CONFIG_DIRECTORY}/{CONFIG_FILENAME}: {path}")
    if path.parent.is_symlink():
        raise Error(f"Config directory may not be a symlink: {path.parent}")
    data = read_yaml(path.read_text(encoding="utf-8"), str(path))
    keys(data, CONFIG_FIELDS, str(path))
    if type(data.get("schema")) is not int or data["schema"] != 1:
        raise Error(f"{path}: schema must be 1")
    source = _path_text(data.get("source", "."), f"{path}: source")
    harnesses = _harnesses(data.get("harnesses", list(HARNESSES)), f"{path}: harnesses")
    project = _path_text(data.get("project", "."), f"{path}: project")
    out = _path_text(data.get("out", "dist"), f"{path}: out")
    return Config(path=path, root=path.parent.parent.absolute(), source=source, harnesses=harnesses, project=project, out=out)


def find_config(start=None) -> Optional[Config]:
    """Find the nearest config, stopping at Git repositories and plugin roots."""
    current = Path.cwd() if start is None else Path(start).expanduser()
    if current.exists() and current.is_file():
        current = current.parent
    try:
        current = current.absolute().resolve()
    except RuntimeError as exc:
        raise Error(f"Config search path is invalid: {current}") from exc

    while True:
        directory = current / CONFIG_DIRECTORY
        if directory.is_symlink():
            raise Error(f"Config directory may not be a symlink: {directory}")
        candidate = directory / CONFIG_FILENAME
        if candidate.exists() or candidate.is_symlink():
            return load_config(candidate)
        if (current / ".git").exists() or (current / "plugin.yaml").is_file():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _stored_path(value, root, where):
    value = _path_text(value, where)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.as_posix()
    return relative.as_posix() or "."


def _prepare_config(root, source, harnesses: Iterable[str], project=".", out="dist"):
    root = _project_root(root)
    harnesses = _harnesses(list(harnesses), "config.yaml: harnesses")
    path = _config_path(root)
    directory = path.parent
    payload = {
        "schema": 1,
        "source": _stored_path(source, root, "config.yaml: source"),
        "harnesses": list(harnesses),
        "project": _stored_path(project, root, "config.yaml: project"),
        "out": _stored_path(out, root, "config.yaml: out"),
    }
    if directory.is_symlink():
        raise Error(f"Config directory may not be a symlink: {directory}")
    if path.is_symlink():
        raise Error(f"Config file may not be a symlink: {path}")
    return root, path, directory, payload


def validate_config(root, source, harnesses: Iterable[str], project=".", out="dist") -> None:
    """Validate setup values before a source or config file is written."""
    _prepare_config(root, source, harnesses, project, out)


def save_config(root, source, harnesses: Iterable[str], project=".", out="dist") -> Path:
    """Write generated setup defaults below ``root`` and return the file path."""
    _root, path, directory, payload = _prepare_config(root, source, harnesses, project, out)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(directory), prefix=".config-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path
