#!/usr/bin/env python3
"""Read-only Codex native discovery smoke test.

This optional check uses Codex's app-server JSON-lines protocol to confirm
skill discovery and plugin metadata reading.  It never starts a model turn or
installs a Codex plugin. Generated files are placed in a temporary directory;
Codex itself inherits the user's normal runtime environment.
"""

import argparse
import importlib
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time


AUTHORING_SKILLS = {
    "add-client-harness",
    "create-eval-suite",
    "create-portable-agent",
    "create-portable-plugin",
    "create-portable-skill",
}


class CheckError(RuntimeError):
    pass


def cli(*arguments, cwd):
    environment = os.environ.copy()
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
        raise CheckError("CLI timed out after 30 seconds: %s" % " ".join(command))
    if result.returncode:
        raise CheckError(
            "CLI failed (%s):\nstdout:\n%s\nstderr:\n%s"
            % (" ".join(command), result.stdout, result.stderr)
        )
    return result.stdout


class Rpc:
    """Small JSON-lines client that waits for the response with a matching ID."""

    def __init__(self, process, timeout):
        self.process = process
        self.timeout = timeout
        self.buffer = bytearray()

    def _send(self, payload):
        if self.process.stdin is None:
            raise CheckError("Codex app-server stdin is unavailable")
        self.process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def notify(self, method, params=None):
        payload = {"method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def request(self, identifier, method, params):
        self._send({"id": identifier, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            while b"\n" in self.buffer:
                line, _, remaining = self.buffer.partition(b"\n")
                self.buffer = bytearray(remaining)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise CheckError("Codex app-server returned invalid JSON: %s" % error)
                # Notifications and responses to earlier requests can be
                # interleaved. Only the requested response is authoritative.
                if message.get("id") != identifier:
                    continue
                if "error" in message:
                    raise CheckError("Codex app-server error for %s: %s" % (method, message["error"]))
                if "result" not in message:
                    raise CheckError("Codex app-server response for %s has no result" % method)
                return message["result"]

            if self.process.poll() is not None:
                raise CheckError("Codex app-server exited before responding to %s" % method)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CheckError("Timed out waiting for Codex app-server response to %s" % method)
            if self.process.stdout is None:
                raise CheckError("Codex app-server stdout is unavailable")
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                raise CheckError("Timed out waiting for Codex app-server response to %s" % method)
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise CheckError("Codex app-server closed stdout while handling %s" % method)
            self.buffer.extend(chunk)


def stop(process):
    """Terminate the app-server and kill it if it ignores the bounded wait."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def is_under(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def discovered_skill_names(result, project):
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise CheckError("Codex skills/list response has no data array")
    found = set()
    for group in result["data"]:
        if not isinstance(group, dict) or not isinstance(group.get("skills"), list):
            continue
        for skill in group["skills"]:
            if not isinstance(skill, dict) or not isinstance(skill.get("name"), str):
                continue
            path = skill.get("path")
            if isinstance(path, str) and is_under(Path(path).resolve(), project):
                found.add(skill["name"])
    return found


def response_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from response_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from response_strings(item)
    elif isinstance(value, str):
        yield value


def check(codex_command, timeout):
    if os.name == "nt":
        raise CheckError("this read-only check uses Unix pipe polling; run it on macOS or Linux")
    package = importlib.import_module("any_harness")
    source = Path(package.__file__).resolve().parent / "bundled" / "authoring"
    if not (source / "plugin.yaml").is_file():
        raise CheckError("Installed any_harness package has no bundled authoring source: %s" % source)

    if os.path.sep in codex_command:
        resolved_codex = str(Path(codex_command).expanduser().resolve())
    else:
        resolved_codex = shutil.which(codex_command)
    if not resolved_codex:
        raise CheckError("Codex CLI not found; pass --codex /path/to/codex or install Codex first")

    with tempfile.TemporaryDirectory(prefix="any-harness-codex-native-") as temporary:
        root = Path(temporary).resolve()
        project = root / "project"
        generated = root / "generated"
        cli("authoring", "--harness", "codex", "--project", str(project), cwd=root)
        cli("build", str(source), "--harness", "codex", "--out", str(generated), cwd=root)

        # The two read-only RPCs below do not require a model or authentication.
        # Inherit the normal Codex environment so this check does not guess at
        # user-specific runtime configuration or redirect its cache.
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        stderr_path = root / "codex-app-server.stderr"
        stderr_file = stderr_path.open("wb")
        try:
            process = subprocess.Popen(
                [resolved_codex, "app-server", "--stdio"],
                cwd=str(project),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
            )
        except OSError:
            stderr_file.close()
            raise
        rpc = Rpc(process, timeout)
        try:
            rpc.request(
                1,
                "initialize",
                {
                    "clientInfo": {"name": "any-harness-validation", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            rpc.notify("initialized")
            skills = rpc.request(2, "skills/list", {"cwds": [str(project)], "forceReload": True})
            found = discovered_skill_names(skills, project)
            if found != AUTHORING_SKILLS:
                raise CheckError(
                    "Codex skills/list discovered %s under %s; expected %s"
                    % (sorted(found), project, sorted(AUTHORING_SKILLS))
                )
            print("Codex skills/list discovered all five installed authoring skills")

            plugin = rpc.request(
                3,
                "plugin/read",
                {
                    "marketplacePath": str(generated / "codex" / ".agents" / "plugins" / "marketplace.json"),
                    "pluginName": "authoring",
                },
            )
            names = set()
            for value in response_strings(plugin):
                if value in AUTHORING_SKILLS:
                    names.add(value)
                elif value.startswith("authoring:") and ":" in value:
                    names.add(value.split(":", 1)[1])
            if names != AUTHORING_SKILLS:
                raise CheckError(
                    "Codex plugin/read returned %s; expected all five authoring skill names"
                    % sorted(names)
                )
            print("Codex plugin/read returned all five generated plugin skills")
        finally:
            stop(process)
            stderr_file.close()
        if process.returncode not in (0, -15):
            details = stderr_path.read_text(encoding="utf-8", errors="replace")
            raise CheckError("Codex app-server exited with %s:\n%s" % (process.returncode, details[-4000:]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default=os.environ.get("CODEX_BIN", "codex"), help="Codex executable (default: codex or CODEX_BIN)")
    parser.add_argument("--timeout", type=float, default=20.0, help="Seconds to wait for each RPC response")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    check(args.codex, args.timeout)
    print("Codex native read-only smoke check passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CheckError, OSError, ValueError) as error:
        print("Codex native smoke check failed: %s" % error, file=sys.stderr)
        sys.exit(1)
