"""Install native files with ownership receipts and conservative updates."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .build import json_bytes, render
from .model import Error, HARNESSES, load, name


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    path = Path(relative)
    if not isinstance(relative, str) or path.is_absolute() or ".." in path.parts or not path.parts or "\\" in relative:
        raise Error(f"Unsafe install path: {relative}")
    destination = root / path
    for parent in [root, *root.parents, destination, *destination.parents]:
        if parent.is_symlink():
            raise Error(f"Refusing symlinked install path: {parent}")
    return destination


def read_receipt(path):
    if not path.exists():
        return {"schema": 1, "files": {}}
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt["schema"] != 1 or not isinstance(receipt["files"], dict):
            raise ValueError("unsupported receipt schema")
        for path, entry in receipt["files"].items():
            if not isinstance(path, str) or not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
                raise ValueError("invalid receipt entry")
        return receipt
    except (ValueError, KeyError, TypeError) as exc:
        raise Error(f"Invalid installation receipt: {path}: {exc}") from exc


def matches(path, entry):
    return path.is_file() and digest(path.read_bytes()) == entry["sha256"] and (0o755 if path.stat().st_mode & 0o111 else 0o644) == entry.get("mode", 0o644)


def replace(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".any-harness-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lock(root, dry_run):
    if dry_run:
        yield
        return
    path = safe_path(root, ".any-harness/install.lock")
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Error(f"Installation is locked: {path}. If no installer is running, remove this stale lock.") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()
        if not parent_existed:
            try:
                path.parent.rmdir()
            except OSError:
                pass  # A successful install created the receipts directory.


def apply(root, plugin_name, harness, desired, provenance, dry_run=False, uninstall=False):
    root = Path(root).absolute()
    if root.is_symlink():
        raise Error(f"Refusing symlinked install root: {root}")
    root = root.resolve()  # Accept OS aliases such as macOS /var and /tmp.
    name(plugin_name, "plugin name")
    if harness not in HARNESSES:
        raise Error(f"Unknown harness: {harness}")
    receipt_path = safe_path(root, f".any-harness/installed/{harness}-{plugin_name}.json")
    with lock(root, dry_run):
        prior = read_receipt(receipt_path)
        owned = prior["files"]
        if uninstall and not receipt_path.exists():
            raise Error(f"{plugin_name} is not installed for {harness} in {root}")
        # Refuse another package's files even if the bytes happen to match.
        for other in receipt_path.parent.glob("*.json"):
            safe_path(root, other.relative_to(root).as_posix())
            if other != receipt_path:
                overlap = set(read_receipt(other)["files"]) & set(desired)
                if overlap:
                    raise Error(f"Files already owned by {other.stem}: {', '.join(sorted(overlap))}")
        actions = []
        for relative in sorted(set(owned) | set(desired)):
            destination = safe_path(root, relative)
            if destination.exists():
                if relative not in owned:
                    raise Error(f"Refusing to overwrite unmanaged file: {destination}")
                if not matches(destination, owned[relative]):
                    raise Error(f"Locally modified installed file: {destination}. Move it aside or restore it before updating/uninstalling.")
            if relative not in desired:
                if destination.exists():
                    actions.append(("remove", relative))
            elif not destination.exists() or not matches(destination, {"sha256": digest(desired[relative][0]), "mode": desired[relative][1]}):
                actions.append(("write", relative))
        if dry_run:
            return actions
        receipt = {"schema": 1, "name": plugin_name, "harness": harness, "source": provenance, "files": {p: {"sha256": digest(data), "mode": mode} for p, (data, mode) in sorted(desired.items())}}
        # Roll back completed writes/removals if a filesystem operation fails.
        backup = {}
        try:
            for action, relative in actions:
                destination = safe_path(root, relative)
                backup[destination] = (destination.read_bytes(), destination.stat().st_mode & 0o777) if destination.exists() else None
                if action == "remove":
                    destination.unlink()
                else:
                    replace(destination, *desired[relative])
            if uninstall:
                receipt_path.unlink()
            else:
                replace(receipt_path, json_bytes(receipt))
        except OSError:
            for destination, old in reversed(list(backup.items())):
                if old is None:
                    destination.unlink(missing_ok=True)
                else:
                    replace(destination, *old)
            raise
        return actions


def native_files(plugin, harness):
    rendered, notes = render(plugin, harness)
    if (plugin.root / "native" / harness / "plugin").exists():
        notes.append("Direct installation uses project/ only. Native plugin-only extras require the native marketplace package or corresponding native project files.")
    return {p[len("project/"):]: value for p, value in rendered.items() if p.startswith("project/")}, notes


def install(source, harness, root, dry_run=False, provenance=None, opencode_global=False):
    plugin = load(source)
    desired, notes = native_files(plugin, harness)
    if opencode_global:
        if any(not p.startswith(".opencode/") for p in desired):
            raise Error("Global OpenCode native project files must stay under .opencode/")
        desired = {p[len(".opencode/"):]: v for p, v in desired.items()}
    provenance = dict(provenance or {"path": str(Path(source).absolute())})
    provenance["version"] = plugin.meta["version"]
    return apply(root, plugin.meta["name"], harness, desired, provenance, dry_run), notes


def git(*args, cwd=None):
    try:
        result = subprocess.run(["git", "-c", f"core.hooksPath={os.devnull}", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=120)
        return result.stdout.strip()
    except FileNotFoundError as exc:
        raise Error("Git must be installed to install from a repository URL") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Avoid echoing credential-bearing remote URLs from git stderr.
        raise Error("Git operation failed. Check the repository URL, ref, network, and Git authentication.") from exc


@contextmanager
def source_folder(source, ref=None, subdir=None):
    if source.startswith("git+"):
        url = source[4:]
        if not (url.startswith("https://") or url.startswith("ssh://") or url.startswith("git@")):
            raise Error("Git sources must use git+https://, git+ssh:// or git+git@")
        if ref is not None and (not ref or ref.startswith("-")):
            raise Error("Invalid Git ref")
        with tempfile.TemporaryDirectory(prefix="any-harness-source-") as temp:
            checkout = Path(temp).resolve() / "repo"
            git("clone", "--no-checkout", "--", url, str(checkout))
            try:
                commit = git("rev-parse", "--verify", "--end-of-options", f"{ref or 'HEAD'}^{{commit}}", cwd=checkout)
            except Error:
                if ref is None:
                    raise
                # A nondefault branch in a normal clone is a remote-tracking
                # ref, not a local branch. Tags and commit hashes resolve above.
                commit = git("rev-parse", "--verify", "--end-of-options", f"refs/remotes/origin/{ref}^{{commit}}", cwd=checkout)
            git("checkout", "--detach", commit, cwd=checkout)
            folder = checkout if subdir in (None, ".") else safe_path(checkout, subdir)
            yield folder, {"git": url, "commit": commit, "subdir": subdir or "."}
    else:
        if ref is not None:
            raise Error("--ref is only valid for git+ sources")
        root = Path(source).absolute()
        if root.is_symlink():
            raise Error(f"Refusing symlinked source root: {root}")
        root = root.resolve()
        folder = root if subdir in (None, ".") else safe_path(root, subdir)
        yield folder, {"path": str(folder)}
