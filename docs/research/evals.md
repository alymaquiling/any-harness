# Eval runner research

Research checked 2026-09-05 against the official harness documentation. This
record separates documented native interfaces from observations on this host.

## Claude Code

Claude Code documents print mode (`claude -p`) for non-interactive runs. The
native structured output choices are `text`, `json`, and newline-delimited
`stream-json`; `--verbose --include-partial-messages` adds streaming events.
`--no-session-persistence` prevents print-mode session persistence. The CLI
also accepts a native `--model` alias or full model ID, `--effort`,
`--permission-mode`, `--max-turns`, and `--max-budget-usd`. The JSON result can
include usage and `total_cost_usd`; the runner records those values only when
the installed CLI emits them. Stream events can expose tool or hook activity,
but prose claiming that a skill or agent ran is not activation evidence.

The runner stages the generated project files and does not run `--plugin-dir`
or install into a user's plugin cache. Isolated runs use a temporary
`CLAUDE_CONFIG_DIR` and fresh `--no-session-persistence` sessions; this avoids
copying credentials but does not remove managed policy or host network access.
Model effort is model-dependent: the same level name does not imply the same
token budget across models, and the runner reports requested and observed
values independently.

- [Claude Code CLI usage](https://code.claude.com/docs/en/cli-usage)
- [Run Claude Code programmatically](https://code.claude.com/docs/en/headless)
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config)
- [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes)
- [Claude Code plugins](https://code.claude.com/docs/en/plugins)

## Codex CLI

The Codex CLI documents `codex exec PROMPT` as its non-interactive native
entry point. `--json` emits JSONL lifecycle/item events, `--ephemeral` avoids
persisting the rollout, `--cd` selects the workspace, `--model` selects a
native model, and `--sandbox` preserves the native read-only or workspace-write
policy. The CLI's native configuration override can carry the documented
reasoning-effort setting; the runner passes it as a Codex config override
instead of translating it to another harness's effort scale. `CODEX_HOME` is
the configuration root used for isolated runs, but an isolated root has no
copied login credentials and therefore may be skipped when authentication is
required.

Codex JSONL can include a thread ID, item events, a final `turn.completed`
event, and usage. Provider model identity or cost is not guaranteed in every
release, so missing fields stay `null`; the requested model is never reported
as observed merely because it was passed on the command line. Project skills
are discovered from `.agents/skills`, and custom agents use standalone
`.codex/agents/*.toml` files. A skill can be requested through the native
`$skill-name` prompt syntax. The current headless CLI has no documented
dedicated custom-agent selector, so automatic or prompt-requested agent use is
`unknown` unless structured events identify it. The app-server JSON-RPC
`skills/list` and `plugin/read` calls are useful read-only discovery checks;
they do not establish behavioral activation or outcome quality.

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Build skills for Codex](https://learn.chatgpt.com/docs/build-skills)
- [Configure Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Build Codex plugins](https://learn.chatgpt.com/docs/build-plugins)
- [Codex plugin availability](https://learn.chatgpt.com/docs/plugins)

## OpenCode stable V1

The official CLI documents `opencode run [message..]` for non-interactive
automation. Its run flags include `--model provider/model`, `--agent`,
`--format default|json`, `--variant`, `--thinking`, `--auto`, `--dir`,
`--session`/`--continue`, `--file`, and `--attach`:

- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [OpenCode agents](https://opencode.ai/docs/agents)
- [OpenCode Agent Skills](https://opencode.ai/docs/skills)
- [OpenCode permissions](https://opencode.ai/docs/permissions)
- [OpenCode config and environment variables](https://opencode.ai/docs/config/)

`--format json` is documented as raw JSON events. `opencode export` emits
session JSON, `opencode session list --format json` lists sessions, and
`opencode stats` reports token/cost statistics by time, tool, model, or project.
The docs do not promise that cost is available as a per-run field, so the
runner records it only when it can associate a native value with that run.

Isolation can use `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`,
`OPENCODE_CONFIG_CONTENT`, and `OPENCODE_PERMISSION`. `OPENCODE_CONFIG_DIR`
also controls discovery of agents, commands, modes, and plugins. The runner
does not set `--auto` implicitly: native permission prompts and denials must
remain observable. OpenCode also documents `--pure`, which disables external
plugins; the runner does not add that flag because it would change the
component under test.

OpenCode 2 is a separate beta executable (`opencode2`) and has a changed
plugin API. Its docs describe `opencode2 run` and `--standalone`, but do not
provide the same stable V1 flag contract. The runner reports V2 as unsupported
unless a future adapter declares it explicitly:

- [OpenCode 2 introduction](https://opencode.ai/v2/docs)
- [OpenCode 2 permissions](https://opencode.ai/v2/docs/permissions)
- [OpenCode 2 models](https://opencode.ai/v2/docs/models)

## Cursor CLI

Cursor's official CLI reference documents `cursor-agent -p/--print` for
non-interactive runs. `--output-format` accepts `text`, `json`, or
`stream-json` in print mode; the default is `stream-json`. `-m/--model` sets
the selected model, `-f/--force` allows commands unless explicitly denied,
and `--resume` continues a session:

- [Cursor CLI parameters](https://docs.cursor.com/en/cli/reference/parameters)
- [Cursor CLI output format](https://docs.cursor.com/en/cli/reference/output-format)
- [Cursor CLI headless mode](https://docs.cursor.com/en/cli/headless)
- [Cursor CLI permissions](https://prod.cursor.com/docs/cli/reference/permissions)

The documented JSON format is one final aggregate result on success; tool and
delta events are omitted. A failure has a non-zero exit status and writes an
error to stderr without a valid JSON result. `stream-json` is therefore the
native evidence channel for tool activity. Cursor does not document a direct
CLI `--skill` flag. A skill's selection or explicit `/skill-name` invocation
is unknown unless the output stream or a native protocol event proves it.

Cursor also documents ACP (`agent acp`) as a stdio JSON-RPC interface with
`initialize`, `authenticate`, `session/new`, `session/prompt`, streaming
`session/update`, permission requests, and cancellation:

- [Cursor ACP](https://prod.cursor.com/docs/cli/acp)

The runner uses print mode for the smallest native adapter and records ACP as
an available future path. ACP permission decisions must be handled by the
client; automatically answering them would bypass the harness's native
permission behavior.

Cursor project CLI configuration is `<project>/.cursor/cli.json` and controls
permissions. Other CLI settings are global in `~/.cursor/cli-config.json`.
`CURSOR_CONFIG_DIR` and Linux/BSD `XDG_CONFIG_HOME` can point at an isolated
configuration root:

- [Cursor CLI configuration](https://prod.cursor.com/docs/cli/reference/configuration)

Cursor discovers skills from `.agents/skills`, `.cursor/skills`, and the
compatibility `.claude/skills`/`.codex/skills` locations, with user-level
counterparts. Custom subagents are Markdown files under `.cursor/agents` (plus
compatibility directories) and can be named in a prompt with `/name`:

- [Cursor Agent Skills](https://prod.cursor.com/docs/skills)
- [Cursor subagents](https://prod.cursor.com/docs/subagents)

Cursor plugins have two documented forms: root `plugin.json` for the
Agent Plugins standard (skills and MCP), or `.cursor-plugin/plugin.json` for
Cursor-specific agents, skills, commands, rules, hooks, variables, and MCP.
Local plugin testing is documented under `~/.cursor/plugins/local` followed by
a reload. The docs do not define a project-local plugin package loading mode
for the CLI, so a project eval uses native project skill/agent discovery and
reports plugin-package behavior as unsupported or unknown rather than
claiming equivalence:

- [Cursor plugins](https://prod.cursor.com/docs/plugins)
- [Cursor plugin reference](https://prod.cursor.com/docs/reference/plugins)

## Local observations

This checkout has Codex CLI 0.153.4 installed and authenticated. Claude Code,
OpenCode, and Cursor binaries are absent from `PATH`, so no live behavioral
evaluation was run for them. One bounded Codex behavioral smoke used inherited
configuration, a read-only sandbox, model `gpt-5.4-mini`, and low effort: the
native process exited 0, two deterministic assertions passed, and the overall
case remained inconclusive because skill-selection evidence was unavailable.
The observed model/effort and cost were null; usage was recorded when emitted.
The source revision and complete inherited configuration fingerprint were
unavailable, so this smoke is evidence of one native behavior only. Local
tests and mocked subprocess tests prove runner orchestration, schema
validation, isolation, timeout handling, and reporting. The bounded Codex
discovery script is a separate native check; it reads generated
skills/plugin metadata without starting a model turn.
