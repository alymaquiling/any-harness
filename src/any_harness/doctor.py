"""Read-only prerequisite checks for any-harness and native harness CLIs."""

import shutil
import sys


SCHEMA = 1
HARNESSES = ("claude-code", "codex", "opencode", "cursor")
HARNESS_COMMANDS = {
    "claude-code": ("claude",),
    "codex": ("codex",),
    "opencode": ("opencode",),
    "cursor": ("cursor-agent", "agent"),
}
MIN_PYTHON = (3, 9)


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def _python_check():
    version = sys.version_info
    try:
        major = int(version[0])
        minor = int(version[1])
        patch = int(version[2])
    except (IndexError, TypeError, ValueError):
        major, minor, patch = 0, 0, 0

    version_text = "%d.%d.%d" % (major, minor, patch)
    if (major, minor) < MIN_PYTHON:
        return _check(
            "python",
            "missing",
            "Python %s is unsupported; Python 3.9 or newer is required." % version_text,
        )
    return _check(
        "python",
        "ok",
        "Python %s is supported; Python 3.9 or newer is required." % version_text,
    )


def _optional_tool_check(name, executable, missing_detail):
    path = shutil.which(executable)
    if path:
        return _check(
            name,
            "ok",
            "Found %s at %s; authentication and version compatibility were not checked."
            % (executable, path),
        )
    return _check(name, "optional", missing_detail)


def _uv_check():
    detail = (
        "uv was not found on PATH. uv is optional; install it to simplify environment "
        "and command management."
    )
    if sys.platform == "darwin":
        detail += " On macOS, install it with: brew install uv."
    return _optional_tool_check("uv", "uv", detail)


def _git_check():
    return _optional_tool_check(
        "git",
        "git",
        "Git was not found on PATH. Git is optional for generation and evaluation; "
        "it is needed for Git URL installs and useful revision provenance. "
        "Authentication and version compatibility were not checked.",
    )


def _harness_check(harness, required):
    candidates = HARNESS_COMMANDS[harness]
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return _check(
                harness,
                "ok",
                "Found %s at %s; authentication and version compatibility were not checked."
                % (candidate, path),
            )

    command_text = " or ".join("'%s'" % candidate for candidate in candidates)
    if required:
        detail = (
            "Required executable %s was not found on PATH because %s was selected. "
            "Authentication and version compatibility were not checked."
            % (command_text, harness)
        )
        return _check(harness, "missing", detail)

    return _check(
        harness,
        "optional",
        "Executable %s was not found on PATH. This check is optional because %s "
        "was not selected. Authentication and version compatibility were not checked."
        % (command_text, harness),
    )


def _invalid_selection_detail(value):
    if isinstance(value, str):
        rendered = repr(value)
    else:
        rendered = "a value of type %s" % type(value).__name__
    return (
        "Invalid harness selection: %s. Supported harnesses: %s."
        % (rendered, ", ".join(HARNESSES))
    )


def _normalize_harnesses(harnesses):
    if harnesses is None:
        return [], None
    if isinstance(harnesses, str):
        values = [harnesses]
    elif isinstance(harnesses, (bytes, bytearray, dict)):
        return [], (
            "Invalid harness selection: expected harness names as text, not %s. "
            "Supported harnesses: %s."
            % (type(harnesses).__name__, ", ".join(HARNESSES))
        )
    else:
        try:
            values = list(harnesses)
        except Exception:
            return [], (
                "Invalid harness selection: harnesses must be None, a harness name, "
                "or an iterable of harness names. Supported harnesses: %s."
                % ", ".join(HARNESSES)
            )

    for value in values:
        if not isinstance(value, str) or value not in HARNESSES:
            return [], _invalid_selection_detail(value)

    # Repeated selections are one requirement. Keeping the first occurrence
    # also avoids making the report depend on a caller's iterable type.
    return list(dict.fromkeys(values)), None


def run_doctor(harnesses=None):
    """Return read-only prerequisite diagnostics and a process exit code.

    ``harnesses`` may be a harness name or an iterable of names. Selected
    harnesses are required; unselected harnesses, Git, and uv are optional.
    The function only inspects the Python runtime and PATH. It never starts a
    subprocess, contacts a network, or invokes a native harness.
    """
    selected, invalid_detail = _normalize_harnesses(harnesses)
    required = set(selected)
    checks = []
    checks.extend((_python_check(), _git_check(), _uv_check()))
    checks.extend(_harness_check(harness, harness in required) for harness in HARNESSES)
    if invalid_detail:
        checks.append(_check("harnesses", "missing", invalid_detail))

    report = {"schema": SCHEMA, "checks": checks}
    return report, int(any(check["status"] == "missing" for check in checks))
