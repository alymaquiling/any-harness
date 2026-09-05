"""Native command line adapters used by the evaluation runner.

The adapters deliberately do very little orchestration.  They turn a case's
native settings into a command line and turn the harness's machine readable
events back into a small, common record.  A runner owns subprocess creation,
timeouts, fixture staging, and exit status; keeping those concerns here would
make it too easy to accidentally bypass a harness's normal permissions or
discovery rules.

The functions in this module do not invoke a model and do not install a
plugin.  In particular, a ``plugin`` component means "run the project-local
native files staged by the runner".  Marketplace installation and other
global plugin operations are intentionally reported as unsupported.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HARNESSES = ("claude-code", "codex", "opencode", "cursor")


# These descriptions are data rather than executable policy.  They are exposed
# to the report layer so that a suite can show why a comparison is unavailable
# on one target.  The URLs are the primary references used when this adapter
# was written and are kept here because capability information is useful even
# when the documentation is not available on the machine running the suite.
_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "claude-code": {
        "cli": "claude",
        "config_env": "CLAUDE_CONFIG_DIR",
        "config_isolation": "supported",
        "output_formats": ["stream-json", "json"],
        "skills": {"automatic": True, "explicit": True, "evidence": "Skill tool event"},
        "agents": {"automatic": True, "explicit": True, "evidence": "Agent tool event or --agent"},
        "plugins": {
            "project_files": True,
            "package_loading": True,
            "package_loading_in_runner": False,
            "note": "The runner stages project files; --plugin-dir/package installation is not performed.",
        },
        "model": True,
        "effort": True,
        "permissions": ["default", "acceptEdits", "plan", "auto", "dontAsk", "manual"],
        "references": [
            "https://code.claude.com/docs/en/cli-reference",
            "https://code.claude.com/docs/en/headless",
            "https://code.claude.com/docs/en/claude-directory",
        ],
    },
    "codex": {
        "cli": "codex",
        "config_env": "CODEX_HOME",
        "config_isolation": "supported; authentication is not copied",
        "output_formats": ["jsonl"],
        "skills": {"automatic": True, "explicit": True, "evidence": "structured skill/tool item when emitted"},
        "agents": {"automatic": True, "explicit": False, "evidence": "no documented exec agent selector"},
        "plugins": {
            "project_files": True,
            "package_loading": True,
            "package_loading_in_runner": False,
            "note": "Plugin marketplace installation changes user/project configuration and is not performed.",
        },
        "model": True,
        "effort": True,
        "permissions": ["sandbox:read-only", "sandbox:workspace-write"],
        "references": [
            "https://learn.chatgpt.com/docs/non-interactive-mode",
            "https://learn.chatgpt.com/docs/build-skills",
            "https://learn.chatgpt.com/docs/agent-configuration/subagents",
        ],
    },
    "opencode": {
        "cli": "opencode",
        "supported_major": 1,
        "config_env": "OPENCODE_CONFIG_DIR",
        "config_isolation": "supported; managed/global configuration may still apply",
        "output_formats": ["json"],
        "skills": {"automatic": True, "explicit": True, "evidence": "structured skill tool event when emitted"},
        "agents": {"automatic": True, "explicit": True, "evidence": "--agent and structured events"},
        "plugins": {
            "project_files": True,
            "package_loading": False,
            "package_loading_in_runner": False,
            "note": "OpenCode JavaScript/TypeScript extensions are distinct from this project's portable plugin.",
        },
        "model": True,
        "effort": "variant/thinking only",
        "permissions": False,
        "references": [
            "https://opencode.ai/docs/cli/",
            "https://opencode.ai/docs/config/",
            "https://opencode.ai/docs/skills",
        ],
    },
    "cursor": {
        "cli": "cursor-agent",
        "config_env": None,
        "config_isolation": "supported with CURSOR_CONFIG_DIR",
        "output_formats": ["stream-json", "json", "text"],
        "skills": {"automatic": True, "explicit": True, "evidence": "structured tool event when emitted"},
        "agents": {"automatic": True, "explicit": False, "evidence": "no documented headless agent selector"},
        "plugins": {
            "project_files": True,
            "package_loading": False,
            "package_loading_in_runner": False,
            "note": "No documented project-local plugin package loader is used by the runner.",
        },
        "model": True,
        "effort": False,
        "permissions": [],
        "references": [
            "https://docs.cursor.com/en/cli/reference/output-format",
            "https://docs.cursor.com/en/cli/reference/parameters",
            "https://docs.cursor.com/en/cli/headless",
        ],
    },
}


_MISSING = object()


def capabilities(harness: Optional[str] = None) -> Dict[str, Any]:
    """Return documented native capabilities.

    With no argument a deep copy for every known harness is returned.  A copy
    is intentional: report code may add observations without changing the
    adapter's static contract for later cases.
    """

    if harness is None:
        return copy.deepcopy(_CAPABILITIES)
    _check_harness(harness)
    return copy.deepcopy(_CAPABILITIES[harness])


def prepare(
    harness: str,
    workspace: Path,
    config_dir: Optional[Path],
    settings: Mapping[str, Any],
    config_mode: str,
    component: Mapping[str, Any],
    mode: str,
    prompt: str,
) -> Dict[str, Any]:
    """Build a native invocation without executing it.

    ``unsupported`` is a string instead of an exception so the runner can
    record a useful ``skipped`` result and retain the requested case in JSON.
    The command is still returned, which makes ``preview`` useful for cases
    that will eventually be run on a different harness.
    """

    _check_harness(harness)
    workspace = Path(workspace)
    config_dir = Path(config_dir) if config_dir is not None else None
    settings = dict(settings or {})
    component = dict(component or {})
    kind = str(component.get("kind", "plugin"))
    name = component.get("name")
    if kind not in {"skill", "agent", "plugin"}:
        return _result([], {}, [], "unsupported component kind: %s" % kind)
    if name is not None and not isinstance(name, str):
        return _result([], {}, [], "component name must be a string")
    if mode not in {"automatic", "explicit"}:
        return _result([], {}, [], "unsupported evaluation mode: %s" % mode)

    config_mode = str(config_mode or "isolated")
    if config_mode not in {"isolated", "inherit"}:
        return _result([], {}, [], "unsupported config mode: %s" % config_mode)

    unsupported: List[str] = []
    notes: List[str] = []
    env: Dict[str, str] = {}
    argv: List[str]

    if config_mode == "isolated" and config_dir is None:
        unsupported.append("isolated config mode requires config_dir")

    if harness == "claude-code":
        argv, env, accepted, notes = _prepare_claude(workspace, config_dir, settings, config_mode)
    elif harness == "codex":
        argv, env, accepted, notes = _prepare_codex(workspace, config_dir, settings, config_mode)
    elif harness == "opencode":
        argv, env, accepted, notes = _prepare_opencode(workspace, config_dir, settings, config_mode)
    else:
        argv, env, accepted, notes = _prepare_cursor(workspace, config_dir, settings, config_mode)

    unsupported.extend(_unknown_settings(settings, accepted))
    unsupported.extend(_setting_conflicts(harness, settings))

    # These flags exist in some native CLIs, but using them would weaken the
    # safety boundary of an evaluation.  A case must opt into ordinary safe
    # workspace permissions (for example Codex ``workspace-write``) and keep
    # bypasses out of the suite's portable settings contract.
    permission = settings.get("permission_mode", settings.get("permissionMode"))
    if harness == "claude-code" and permission == "bypassPermissions":
        unsupported.append("Claude bypassPermissions is not enabled by the eval adapter")
    sandbox = settings.get("sandbox", settings.get("sandbox_mode"))
    if harness == "codex" and sandbox == "danger-full-access":
        unsupported.append("Codex danger-full-access is not enabled by the eval adapter")
    if harness == "codex" and sandbox is not None and sandbox not in {"read-only", "workspace-write"}:
        unsupported.append("Codex sandbox must be read-only or workspace-write")
    if harness == "codex":
        codex_effort = settings.get(
            "model_reasoning_effort",
            settings.get("reasoning_effort", settings.get("effort")),
        )
        if codex_effort is not None and codex_effort not in {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            unsupported.append("Codex reasoning effort is unsupported: %s" % codex_effort)
        if settings.get("ignore_user_config") not in (None, True, False):
            unsupported.append("Codex ignore_user_config must be boolean")
    if harness == "claude-code":
        effort = settings.get("effort")
        if effort is not None and effort not in {"low", "medium", "high", "xhigh", "max", "ultracode"}:
            unsupported.append("Claude effort must be low, medium, high, xhigh, max, or ultracode")
        if settings.get("no_session_persistence") not in (None, True, False):
            unsupported.append("Claude no_session_persistence must be boolean")
        if permission is not None and permission not in {"default", "acceptEdits", "plan", "auto", "dontAsk", "manual"}:
            unsupported.append("Claude permission_mode is unsupported or unsafe: %s" % permission)
    if harness == "opencode" and settings.get("thinking") not in (None, True, False):
        unsupported.append("OpenCode thinking must be boolean")

    # A native direct agent selector is valuable evidence when available.  A
    # prompt that merely says "use agent X" cannot establish activation and is
    # therefore not a substitute on targets without a selector.
    if mode == "explicit" and kind == "agent":
        if harness == "claude-code" and name:
            argv.extend(["--agent", name])
        elif harness == "opencode" and name:
            argv.extend(["--agent", name])
        elif harness in {"codex", "cursor"} and name:
            # These CLIs do not expose a dedicated headless selector.  Keep an
            # explicit request runnable so native automatic delegation can be
            # observed, while reporting invocation as unknown if no structured
            # agent event is emitted.
            notes.append(
                "%s has no documented headless agent selector; the prompt requests native delegation and evidence may remain unknown."
                % harness
            )

    invocation_prompt = str(prompt or "")
    if mode == "explicit" and name and kind == "agent" and harness in {"codex", "cursor"}:
        if harness == "codex":
            invocation_prompt = ("Use agent '%s' for this request. %s" % (name, invocation_prompt)).strip()
        else:
            invocation_prompt = ("/%s %s" % (name.lstrip("/"), invocation_prompt)).strip()
    if mode == "explicit" and name and kind == "skill":
        # The slash/$ prefixes are native prompt syntax.  They are sent to the
        # harness as user input; no hidden API or direct model call is used.
        if harness == "codex":
            prefix = name if name.startswith("$") else "$" + name
        elif harness == "opencode":
            # OpenCode's slash commands are not the native skill tool.  Ask
            # the model to use the discovered skill through that tool and let
            # structured tool events establish whether it actually happened.
            prefix = "Use the native skill tool to invoke '%s' for this request." % name.lstrip("/")
        else:
            prefix = name if name.startswith("/") else "/" + name
        invocation_prompt = (prefix + " " + invocation_prompt).strip()

    if kind == "plugin":
        notes.append(
            "Plugin evaluation uses staged project-native files. Native marketplace/cache installation, "
            "MCP/app setup, and other package side effects are not performed by the runner."
        )

    env_remove: List[str] = []
    marker = env.pop("_ANY_HARNESS_ENV_REMOVE", None)
    if marker:
        env_remove.extend(str(marker).split(","))
    if unsupported:
        unsupported_text = "; ".join(dict.fromkeys(unsupported))
    else:
        unsupported_text = None
    argv.append(invocation_prompt)
    result = _result(argv, env, notes, unsupported_text)
    if env_remove:
        result["env_remove"] = env_remove
    return result


def _prepare_claude(
    workspace: Path,
    config_dir: Optional[Path],
    settings: Mapping[str, Any],
    config_mode: str,
) -> Tuple[List[str], Dict[str, str], Sequence[str], List[str]]:
    argv = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-hook-events"]
    accepted = {
        "model",
        "effort",
        "permission_mode",
        "permissionMode",
        "max_turns",
        "maxTurns",
        "max_budget_usd",
        "maxBudgetUsd",
        "no_session_persistence",
    }
    notes: List[str] = []
    env: Dict[str, str] = {}
    if config_mode == "isolated":
        if config_dir is not None:
            env.update(_isolated_env(config_dir, "CLAUDE_CONFIG_DIR"))
        notes.append("Claude config isolated with CLAUDE_CONFIG_DIR; credentials are not copied.")
    else:
        notes.append("Claude uses the user's normal configuration and installed plugins (inherit requested).")

    model = _value(settings, "model")
    if model is not _MISSING and model is not None:
        argv.extend(["--model", str(model)])
    effort = _value(settings, "effort")
    if effort is not _MISSING and effort is not None:
        if str(effort) not in {"low", "medium", "high", "xhigh", "max", "ultracode"}:
            notes.append("Claude effort value is passed to the native CLI for validation: %s" % effort)
        argv.extend(["--effort", str(effort)])
    permission = _value(settings, "permission_mode", "permissionMode")
    if permission is not _MISSING and permission is not None:
        argv.extend(["--permission-mode", str(permission)])
    max_turns = _value(settings, "max_turns", "maxTurns")
    if max_turns is not _MISSING and max_turns is not None:
        argv.extend(["--max-turns", str(max_turns)])
    max_budget = _value(settings, "max_budget_usd", "maxBudgetUsd")
    if max_budget is not _MISSING and max_budget is not None:
        argv.extend(["--max-budget-usd", str(max_budget)])
    no_persist = _value(settings, "no_session_persistence")
    if no_persist is _MISSING or no_persist is True:
        argv.append("--no-session-persistence")
    elif no_persist not in (False, None):
        notes.append("no_session_persistence should be boolean; native CLI will reject its value")
    return argv, env, accepted, notes


def _prepare_codex(
    workspace: Path,
    config_dir: Optional[Path],
    settings: Mapping[str, Any],
    config_mode: str,
) -> Tuple[List[str], Dict[str, str], Sequence[str], List[str]]:
    argv = ["codex", "exec", "--json", "--ephemeral", "--cd", str(workspace)]
    accepted = {
        "model",
        "model_reasoning_effort",
        "reasoning_effort",
        "effort",
        "sandbox",
        "sandbox_mode",
        "output_schema",
        "ignore_user_config",
    }
    notes: List[str] = []
    env: Dict[str, str] = {}
    if config_mode == "isolated":
        if config_dir is not None:
            env.update(_isolated_env(config_dir, "CODEX_HOME"))
        notes.append("Codex config isolated with CODEX_HOME; authentication is not copied.")
    else:
        notes.append("Codex uses the user's normal CODEX_HOME/configuration (inherit requested).")

    model = _value(settings, "model")
    if model is not _MISSING and model is not None:
        argv.extend(["--model", str(model)])
    effort = _value(settings, "model_reasoning_effort", "reasoning_effort", "effort")
    if effort is not _MISSING and effort is not None:
        # The installed CLI accepts this as a native config override.  Quoting
        # protects values such as "high" from being mistaken for shell input
        # if the argv is later rendered for a preview.
        argv.extend(["-c", 'model_reasoning_effort="%s"' % str(effort).replace('"', '\\"')])
    sandbox = _value(settings, "sandbox", "sandbox_mode")
    if sandbox is not _MISSING and sandbox is not None:
        value = str(sandbox)
        if value not in {"read-only", "workspace-write"}:
            notes.append("Codex sandbox value requests an unsafe or unknown permission mode: %s" % value)
        else:
            argv.extend(["--sandbox", value])
    schema = _value(settings, "output_schema")
    if schema is not _MISSING and schema is not None:
        argv.extend(["--output-schema", str(schema)])
    ignore_user_config = _value(settings, "ignore_user_config")
    if ignore_user_config is True:
        argv.append("--ignore-user-config")
        notes.append("Codex user configuration was explicitly ignored by the case.")
    elif ignore_user_config not in (_MISSING, False, None):
        notes.append("ignore_user_config should be boolean; native CLI will reject its value")
    if not (workspace / ".git").exists():
        argv.append("--skip-git-repo-check")
    return argv, env, accepted, notes


def _prepare_opencode(
    workspace: Path,
    config_dir: Optional[Path],
    settings: Mapping[str, Any],
    config_mode: str,
) -> Tuple[List[str], Dict[str, str], Sequence[str], List[str]]:
    argv = ["opencode", "run", "--format", "json", "--dir", str(workspace)]
    accepted = {"model", "variant", "thinking"}
    notes: List[str] = []
    env: Dict[str, str] = {}
    if config_mode == "isolated":
        if config_dir is not None:
            env.update(_isolated_env(config_dir, "OPENCODE_CONFIG_DIR"))
            env["_ANY_HARNESS_ENV_REMOVE"] = "OPENCODE_CONFIG_CONTENT,OPENCODE_CONFIG,OPENCODE_PERMISSION"
        notes.append("OpenCode config directory isolated with OPENCODE_CONFIG_DIR; managed config may still apply.")
    else:
        notes.append("OpenCode uses its normal global/project configuration (inherit requested).")
    model = _value(settings, "model")
    if model is not _MISSING and model is not None:
        argv.extend(["--model", str(model)])
    variant = _value(settings, "variant")
    if variant is not _MISSING and variant is not None:
        argv.extend(["--variant", str(variant)])
    thinking = _value(settings, "thinking")
    if thinking is not _MISSING and thinking is not None:
        if thinking is True:
            argv.append("--thinking")
        elif thinking is not False:
            notes.append("OpenCode thinking should be boolean; native CLI will reject its value")
    return argv, env, accepted, notes


def _prepare_cursor(
    workspace: Path,
    config_dir: Optional[Path],
    settings: Mapping[str, Any],
    config_mode: str,
) -> Tuple[List[str], Dict[str, str], Sequence[str], List[str]]:
    # Cursor documents CURSOR_CONFIG_DIR for the CLI's user state.  It is kept
    # separate from the common XDG roots below so an isolated run can still
    # use the native directory contract.
    argv = ["cursor-agent", "-p", "--output-format", "stream-json"]
    accepted = {"model"}
    notes: List[str] = []
    env: Dict[str, str] = {}
    if config_mode == "isolated":
        if config_dir is not None:
            env.update(_isolated_env(config_dir, "CURSOR_CONFIG_DIR"))
        notes.append("Cursor config is isolated with CURSOR_CONFIG_DIR; credentials are not copied.")
    else:
        notes.append("Cursor uses its normal user/global configuration (inherit requested).")
    model = _value(settings, "model")
    if model is not _MISSING and model is not None:
        argv.extend(["--model", str(model)])
    return argv, env, accepted, notes


def _isolated_env(config_dir: Path, native_key: str) -> Dict[str, str]:
    """Set native and common XDG roots for a fresh, non-inherited session."""

    root = Path(config_dir)
    return {
        native_key: str(root),
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
    }


def _result(argv: List[str], env: Dict[str, str], notes: List[str], unsupported: Optional[str]) -> Dict[str, Any]:
    return {"argv": argv, "env": env, "notes": notes, "unsupported": unsupported}


def _check_harness(harness: str) -> None:
    if harness not in HARNESSES:
        raise ValueError("Unknown harness: %s" % harness)


def _value(settings: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in settings:
            return settings[key]
    return _MISSING


def _unknown_settings(settings: Mapping[str, Any], accepted: Sequence[str]) -> List[str]:
    accepted_set = set(accepted)
    return ["unsupported native setting(s): %s" % ", ".join(sorted(set(settings) - accepted_set))] if set(settings) - accepted_set else []


def _setting_conflicts(harness: str, settings: Mapping[str, Any]) -> List[str]:
    aliases = {
        "claude-code": (("permission_mode", "permissionMode"),),
        "codex": (
            ("model_reasoning_effort", "reasoning_effort", "effort"),
            ("sandbox", "sandbox_mode"),
        ),
        "opencode": (),
        "cursor": (),
    }.get(harness, ())
    conflicts = []
    for group in aliases:
        present = [key for key in group if key in settings and settings[key] is not None]
        if len(present) > 1:
            conflicts.append("conflicting native setting aliases: %s" % ", ".join(present))
    return conflicts


def inspect_output(
    harness: str,
    stdout: str,
    stderr: str,
    component: Mapping[str, Any],
) -> Dict[str, Any]:
    """Extract output and native evidence from structured harness events.

    The parser never treats an assistant's prose as proof that a skill or
    agent ran.  ``invocation`` remains ``unknown`` unless a structured native
    tool/agent/skill event identifies the requested component.  Extra keys are
    intentionally included for the runner's report layer: ``tool_calls``,
    ``tool_trace_complete``, ``completed``, ``error``, and ``status``.
    """

    _check_harness(harness)
    component = dict(component or {})
    kind = str(component.get("kind", "plugin"))
    name = component.get("name")
    events, plain = _parse_events(stdout)
    tool_calls = _extract_tool_calls(harness, events)
    tool_calls = _dedupe_tool_calls(tool_calls)
    # Any non-JSON line means that the structured stream is incomplete.  A
    # valid-looking nested object inside prose/tool arguments must not be
    # promoted to invocation evidence.
    invocation = "observed" if not plain and _component_observed(tool_calls, kind, name) else "unknown"

    if harness == "claude-code":
        data = _inspect_claude(events, plain, stderr)
    elif harness == "codex":
        data = _inspect_codex(events, plain, stderr)
    elif harness == "opencode":
        data = _inspect_opencode(events, plain, stderr)
    else:
        data = _inspect_cursor(events, plain, stderr)

    data.update(
        {
            "invocation": invocation,
            "tool_calls": tool_calls,
            # The runner uses the same structured native calls for
            # action_forbidden checks.  No assistant prose is converted into
            # an action here.
            "actions": tool_calls,
            # A terminal success plus an entirely structured stream is the
            # only point at which we can say that the native trace was
            # complete.  Otherwise the absence of an event is unknown.
            "tool_trace_complete": bool(
                data.get("completed") and data.get("error") is None and events and not plain
            ),
        }
    )
    return data


def _parse_events(stdout: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    events: List[Dict[str, Any]] = []
    plain: List[str] = []
    for line in str(stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            plain.append(line)
            continue
        if isinstance(value, dict):
            events.append(value)
        elif isinstance(value, list):
            events.extend(item for item in value if isinstance(item, dict))
        else:
            plain.append(str(value))
    return events, plain


def _inspect_claude(events: Sequence[Mapping[str, Any]], plain: Sequence[str], stderr: str) -> Dict[str, Any]:
    assistant_output: List[str] = []
    final_result: Optional[str] = None
    model = None
    effort = None
    usage = None
    cost = None
    completed = False
    native_error = None
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type == "system" and event.get("subtype") == "init":
            model = model or _string_field(event, "model")
            effort = effort or _string_field(event, "effort", "effortLevel")
        if event_type == "assistant":
            assistant_output.extend(_content_text(event.get("message", {}).get("content") if isinstance(event.get("message"), dict) else event.get("content")))
        if event_type == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                assistant_output.append(delta["text"])
        if event_type == "result":
            result = event.get("result")
            if isinstance(result, str):
                # Claude's result event is the authoritative final response;
                # assistant deltas are intermediate and often repeat it.
                final_result = result
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else usage
            cost = _number(event.get("total_cost_usd"), default=cost)
            subtype = str(event.get("subtype", ""))
            if subtype == "success":
                completed = True
            elif subtype.startswith("error") or subtype in {"failure", "failed"}:
                native_error = result if isinstance(result, str) else subtype
        if event_type == "error":
            native_error = _event_error(event) or native_error
    if not events and plain:
        assistant_output.extend(plain)
    output = [final_result] if final_result is not None else assistant_output
    if native_error is None and not completed and stderr and not events:
        native_error = stderr.strip() or None
    return _record(output, model, effort, usage, cost, completed, native_error)


def _inspect_codex(events: Sequence[Mapping[str, Any]], plain: Sequence[str], stderr: str) -> Dict[str, Any]:
    output: List[str] = []
    model = None
    effort = None
    usage = None
    cost = None
    completed = False
    native_error = None
    for event in events:
        model = model or _string_field(event, "model")
        effort = effort or _string_field(event, "effort", "reasoning_effort", "model_reasoning_effort")
        event_type = str(event.get("type", ""))
        item = event.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type", ""))
            # Only completed agent messages are final output.  A started item
            # can contain a partial or speculative text fragment.
            if event_type in {"item.completed", "item_completed"} and item_type in {"agent_message", "assistant_message", "message"}:
                output.extend(_content_text(item.get("text", item.get("content", item.get("message")))))
            model = model or _string_field(item, "model")
            effort = effort or _string_field(item, "effort", "reasoning_effort")
        if event_type == "turn.completed":
            completed = True
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else usage
        elif event_type in {"turn.failed", "error"}:
            native_error = _event_error(event) or native_error
    if not events and plain:
        output.extend(plain)
    if native_error is None and not completed and stderr and not events:
        native_error = stderr.strip() or None
    return _record(output, model, effort, usage, cost, completed, native_error)


def _inspect_opencode(events: Sequence[Mapping[str, Any]], plain: Sequence[str], stderr: str) -> Dict[str, Any]:
    output: List[str] = []
    model = None
    effort = None
    usage = None
    cost = None
    completed = False
    native_error = None
    for event in events:
        model = model or _string_field(event, "model")
        effort = effort or _string_field(event, "effort", "reasoning_effort", "reasoningEffort", "variant")
        event_type = str(event.get("type", ""))
        if event_type in {"text", "assistant", "message", "response"}:
            output.extend(_content_text(event.get("text", event.get("content", event.get("message")))))
        if isinstance(event.get("part"), dict):
            output.extend(_content_text(event["part"].get("text")))
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        if isinstance(event.get("tokens"), dict):
            usage = event["tokens"]
        part = event.get("part")
        if event_type == "step_finish" and isinstance(part, Mapping):
            if isinstance(part.get("tokens"), Mapping):
                usage = part["tokens"]
            cost = _number(part.get("cost"), default=cost)
        cost = _number(event.get("cost"), default=cost)
        if event_type in {"session.completed", "run.completed", "done", "step_finish"}:
            completed = True
        if event_type in {"error", "session.error", "run.error"}:
            native_error = _event_error(event) or native_error
    if not events and plain:
        output.extend(plain)
    if native_error is None and not completed and stderr and not events:
        native_error = stderr.strip() or None
    return _record(output, model, effort, usage, cost, completed, native_error)


def _inspect_cursor(events: Sequence[Mapping[str, Any]], plain: Sequence[str], stderr: str) -> Dict[str, Any]:
    output: List[str] = []
    model = None
    effort = None
    usage = None
    cost = None
    completed = False
    native_error = None
    for event in events:
        model = model or _string_field(event, "model")
        effort = effort or _string_field(event, "effort", "reasoning_effort", "reasoningEffort")
        event_type = str(event.get("type", ""))
        if event_type in {"assistant", "message"}:
            output.extend(_content_text(event.get("message", event.get("content", event.get("text")))))
        if event_type == "result":
            output.extend(_content_text(event.get("result")))
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else usage
            cost = _number(event.get("cost"), default=cost)
            subtype = str(event.get("subtype", ""))
            if subtype in {"success", "completed"}:
                completed = True
            elif subtype:
                native_error = _event_error(event) or subtype
        if event_type == "error":
            native_error = _event_error(event) or native_error
    if not events and plain:
        output.extend(plain)
    if native_error is None and not completed and stderr and not events:
        native_error = stderr.strip() or None
    return _record(output, model, effort, usage, cost, completed, native_error)


def _record(
    output: Sequence[str],
    model: Optional[str],
    effort: Optional[str],
    usage: Optional[Mapping[str, Any]],
    cost: Optional[float],
    completed: bool,
    native_error: Optional[str],
) -> Dict[str, Any]:
    text = "\n".join(str(item) for item in output if str(item).strip()).strip()
    error = native_error.strip() if isinstance(native_error, str) and native_error.strip() else None
    status = "failure" if error else ("success" if completed else "inconclusive")
    return {
        "output": text,
        "observed_model": model,
        "observed_effort": effort,
        "tokens": dict(usage) if isinstance(usage, Mapping) else None,
        "cost": cost,
        "completed": completed,
        "error": error,
        "status": status,
    }


def _string_field(value: Any, *keys: str) -> Optional[str]:
    if isinstance(value, Mapping):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item:
                return item
    return None


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)):
        return default
    return float(value)


def _event_error(event: Mapping[str, Any]) -> Optional[str]:
    for key in ("error", "message", "reason", "result"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, Mapping):
            text = _string_field(value, "message", "error", "reason")
            if text:
                return text
    return None


def _content_text(value: Any) -> List[str]:
    """Extract textual blocks from a known assistant/message field only."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        output: List[str] = []
        for item in value:
            if isinstance(item, str):
                output.append(item)
            elif isinstance(item, Mapping):
                item_type = str(item.get("type", ""))
                if item_type in {"text", "output_text", "text_delta"}:
                    text = item.get("text", item.get("content"))
                    if isinstance(text, str):
                        output.append(text)
                elif item_type in {"message", "content"}:
                    output.extend(_content_text(item.get("content", item.get("text"))))
        return output
    if isinstance(value, Mapping):
        if isinstance(value.get("text"), str):
            return [value["text"]]
        return _content_text(value.get("content"))
    return []


def _extract_tool_calls(harness: str, events: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Extract calls from documented event envelopes only.

    A recursive walk is tempting, but it can mistake an object in a tool
    argument or assistant output for a real invocation.  Each harness has a
    small set of event envelopes, so keep the evidence boundary explicit.
    """

    calls: List[Dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("type", "")).lower().replace("-", "_")
        if harness == "claude-code":
            if event_type == "system" and event.get("subtype") == "init":
                # Claude's init event can identify a selected --agent.  Keep
                # this to direct fields only; nested prompt data is not proof.
                for key in ("agent", "skill"):
                    if isinstance(event.get(key), str):
                        calls.append({"name": event[key], "input": {key: event[key]}, "type": key})
            elif event_type in {"assistant", "message"}:
                message = event.get("message")
                content = message.get("content") if isinstance(message, Mapping) else event.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, Mapping) and str(block.get("type", "")).lower() in {"tool_use", "tool_call"}:
                            call = _tool_call(block, str(block.get("type", "tool_use")).lower())
                            if call:
                                calls.append(call)
            elif event_type in {"tool_use", "tool_call"}:
                call = _tool_call(event, event_type)
                if call:
                    calls.append(call)
        elif harness == "codex":
            if event_type in {"item.completed", "item_completed"}:
                item = event.get("item")
                if isinstance(item, Mapping):
                    item_type = str(item.get("type", "")).lower().replace("-", "_")
                    if item_type in {
                        "skill",
                        "agent",
                        "agent_spawn",
                        "tool_use",
                        "tool_call",
                        "mcp_tool_call",
                        "collab_tool_call",
                        "command_execution",
                    }:
                        call = _tool_call(item, item_type)
                        if call:
                            calls.append(call)
        elif harness == "opencode":
            if event_type in {"tool_use", "tool_call"}:
                part = event.get("part")
                if isinstance(part, Mapping):
                    calls.append(_opencode_part_call(part, event_type))
                else:
                    call = _tool_call(event, event_type)
                    if call:
                        calls.append(call)
            else:
                part = event.get("part")
                if isinstance(part, Mapping) and str(part.get("type", "")).lower() in {"tool", "tool_use", "tool_call"}:
                    calls.append(_opencode_part_call(part, "tool_use"))
                elif event_type in {"session", "step_start", "run_started"} and isinstance(event.get("agent"), str):
                    calls.append({"name": event["agent"], "input": {"agent": event["agent"]}, "type": "agent"})
        else:  # Cursor Agent
            if event_type == "tool_call":
                envelope = event.get("tool_call") if isinstance(event.get("tool_call"), Mapping) else event
                call = _tool_call(envelope, event_type)
                if call:
                    calls.append(call)
            elif event_type in {"assistant", "message"}:
                content = event.get("content")
                message = event.get("message")
                if isinstance(message, Mapping):
                    content = message.get("content", content)
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, Mapping) and str(block.get("type", "")).lower() in {"tool_use", "tool_call"}:
                            call = _tool_call(block, str(block.get("type", "tool_use")).lower())
                            if call:
                                calls.append(call)
    return [call for call in calls if call]


def _opencode_part_call(part: Mapping[str, Any], event_type: str) -> Dict[str, Any]:
    state = part.get("state") if isinstance(part.get("state"), Mapping) else {}
    source = {
        "name": part.get("tool") or part.get("name"),
        "input": state.get("input") if isinstance(state, Mapping) else None,
    }
    if source["input"] is None:
        source["input"] = part.get("input")
    return _tool_call(source, event_type) or {"name": event_type, "input": None, "type": event_type}


def _tool_call(value: Any, event_type: str) -> Optional[Dict[str, Any]]:
    if isinstance(value, str):
        return {"name": value, "input": None, "type": event_type}
    if not isinstance(value, Mapping):
        return None
    source = value
    name = source.get("name") or source.get("tool_name") or source.get("tool") or source.get("skill") or source.get("agent")
    if not isinstance(name, str):
        name = event_type
    args = source.get("input", source.get("arguments", source.get("args")))
    if args is None:
        # Skill/agent items often put the target in a top-level field.  Keep a
        # small, structured input record so the report can show evidence.
        args = {key: source[key] for key in ("skill", "agent", "subagent_type", "name", "command") if key in source}
        if not args:
            args = None
    return {"name": str(name), "input": args, "type": event_type}


def _dedupe_tool_calls(calls: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for call in calls:
        key = json.dumps(dict(call), sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(dict(call))
    return result


def _component_observed(calls: Sequence[Mapping[str, Any]], kind: str, name: Any) -> bool:
    if not isinstance(name, str) or not name:
        return False
    target = name.lstrip("/$").lower()
    for call in calls:
        call_name = str(call.get("name", "")).lstrip("/$").lower()
        call_type = str(call.get("type", "")).lower()
        input_value = call.get("input")
        input_targets = set()
        if isinstance(input_value, Mapping):
            for key in ("skill", "agent", "subagent_type", "name"):
                value = input_value.get(key)
                if isinstance(value, str):
                    input_targets.add(value.lstrip("/$").lower())
        skill_call = kind == "skill" and (
            call_name in {"skill", "load_skill"} or call_type == "skill" or call_name == "tool"
        )
        agent_call = kind == "agent" and (
            call_name in {"agent", "task", "delegate", "spawn_agent", "agent_spawn"}
            or call_type in {"agent", "agent_spawn", "collab_tool_call"}
        )
        plugin_call = kind == "plugin" and call_name == "plugin"
        if (skill_call or agent_call or plugin_call) and (
            target == call_name or target in input_targets
        ):
            return True
    return False


__all__ = ["HARNESSES", "capabilities", "prepare", "inspect_output"]
