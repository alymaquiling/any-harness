# Claude Code adapter reference

Checked 2026-09-05 against the current official Claude Code documentation. This note records the format a generator should emit for Claude Code plugins, skills, subagents, and a Git-backed marketplace. The primary references are [Create plugins](https://code.claude.com/docs/en/plugins), [Plugins reference](https://code.claude.com/docs/en/plugins-reference), [Extend Claude with skills](https://code.claude.com/docs/en/slash-commands), [Create custom subagents](https://code.claude.com/docs/en/sub-agents), and [Create and distribute a plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

## Native package shape

Claude Code treats a plugin as a self-contained directory. The manifest, when present, is `.claude-plugin/plugin.json`; component directories are siblings of `.claude-plugin` at the plugin root. Do not put `skills/`, `agents/`, `commands/`, or `hooks/` inside `.claude-plugin`. The manifest is optional when only default component locations are used, but a distributable generated plugin should always include it for stable identity and metadata.

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── code-review/
│       ├── SKILL.md
│       ├── reference.md       # optional supporting material
│       └── scripts/            # optional executable helpers
├── agents/
│   └── security-reviewer.md
├── hooks/
│   └── hooks.json              # optional
├── .mcp.json                   # optional
├── .lsp.json                   # optional
├── settings.json               # optional defaults
└── bin/                        # optional executables
```

`commands/` is still accepted as a directory of flat Markdown commands, but new work should use `skills/`. A plugin containing one root-level `SKILL.md` is also accepted as a single-skill plugin; use `skills/<skill-name>/SKILL.md` for generated packages that may contain more than one skill. A root `CLAUDE.md` is not loaded as project context for a plugin, so package instructions belong in a skill or agent body. ([Plugins](https://code.claude.com/docs/en/plugins), [Plugins reference](https://code.claude.com/docs/en/plugins-reference))

## `plugin.json`

If included, `name` is the only required field. It must be a kebab-case identifier without spaces, control characters, or bidirectional-formatting characters; it supplies the plugin namespace. The following is a conservative manifest for generated output:

```json
{
  "$schema": "https://json.schemastore.org/claude-code-plugin-manifest.json",
  "name": "example-tools",
  "displayName": "Example Tools",
  "version": "1.0.0",
  "description": "Reusable review and release workflows",
  "author": {
    "name": "Example Team",
    "email": "dev@example.com",
    "url": "https://github.com/example"
  },
  "homepage": "https://example.com/example-tools",
  "repository": "https://github.com/example/example-tools",
  "license": "MIT",
  "keywords": ["review", "release"],
  "skills": "./skills/",
  "agents": "./agents/"
}
```

Default locations are auto-discovered. The `skills` path adds to the default `skills/` scan in ordinary plugin installs, while a declared `agents` path replaces the default `agents/` scan. If custom component paths are emitted, all paths must be relative to the plugin root and start with `./` (the `skills` field also accepts `.`). A path entry should never escape the plugin root.

The full current manifest accepts metadata such as `displayName`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `metadata`, and `defaultEnabled`; component fields including `skills`, `commands`, `agents`, `workflows`, `hooks`, `mcpServers`, `outputStyles`, `lspServers`, `userConfig`, `channels`, and `dependencies`; and experimental `themes` and `monitors` under `experimental`. Claude Code ignores unrecognized top-level fields at runtime, but `claude plugin validate --strict` turns those warnings into CI failures. This makes it possible to retain other ecosystem metadata in one JSON file, but the generator should avoid relying on that tolerance for required Claude behavior. ([Plugins reference: manifest schema](https://code.claude.com/docs/en/plugins-reference))

`version` is optional. If it is set in `plugin.json`, Claude Code updates an installed plugin when that version changes; if it is omitted, version resolution falls back to marketplace/source information. The generator should emit a semantic version and bump it for releases unless a project explicitly chooses commit-based versioning.

## Skills

A plugin skill is a directory under `skills/` with a required `SKILL.md`. Supporting files can live beside it and should be linked from the main file. Claude Code loads the description into its skill listing and loads the body when the skill is invoked. Plugin skill names are namespaced by the plugin name, for example `skills/review/SKILL.md` in `example-tools` is `/example-tools:review`.

The frontmatter must begin on the first line, be delimited by `---`, and use YAML. All fields are optional, but `description` is recommended. A minimal portable skill looks like this:

```markdown
---
name: code-review
description: Review code for correctness, security, and maintainability.
license: MIT
compatibility: Requires a Git repository and a POSIX shell.
metadata:
  source: example-tools
allowed-tools: Read Grep Glob
---

Review the selected code or current changes. Report concrete findings with file and
line references, explain the risk, and suggest a fix.
```

Claude Code also supports these extensions for local/plugin skills:

| Field | Use |
| --- | --- |
| `when_to_use` | Additional trigger guidance appended to `description`. |
| `argument-hint` | Autocomplete hint such as `[issue-number]`. |
| `arguments` | Named positional arguments for `$name` substitutions. |
| `disable-model-invocation` | `true` makes the skill user-invocable only, useful for side-effecting workflows. |
| `user-invocable` | `false` hides the skill from the `/` menu and leaves invocation to Claude. |
| `allowed-tools` / `disallowed-tools` | Permission grants or tool restrictions while the skill is active. |
| `model` / `effort` | Temporary model or effort override; with `context: fork`, `model` applies to the fork. |
| `context` | `fork` runs the skill in a forked subagent context. |
| `agent` / `background` | Agent type and foreground/background behavior for a forked skill. |
| `hooks` | Hooks registered when the skill is invoked. |
| `paths` | File globs that limit automatic activation. |
| `shell` | `bash` (default) or `powershell` for `!` command injection. |
| `metadata` | Free-form YAML map for tooling; Claude Code does not interpret its contents. |

For cross-harness source authoring, the safest Claude-compatible portable subset is `name`, `description`, `license`, `compatibility`, `metadata`, and `allowed-tools`. Claude-only controls should be represented in the source model and emitted only in Claude output (or explicitly marked as lossy on targets that do not support them). Claude Code’s documentation identifies the six-field subset as the one accepted by claude.ai uploads, the Skills API, and `package_skill.py`; those paths reject extra Claude Code-only keys instead of ignoring them. ([Skills frontmatter reference](https://code.claude.com/docs/en/slash-commands))

Skill names have a subtle distinction: in personal/project skills the directory name controls the command, while in plugin skills the frontmatter `name` controls the final namespaced segment and falls back to the directory name. To keep generated commands stable across cache directories and releases, always write an explicit kebab-case `name` in each generated plugin `SKILL.md`.

Claude Code supports `$ARGUMENTS`, indexed `$0`/`$1` (or `$ARGUMENTS[0]`), named `$name`, and plugin path variables such as `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_SKILL_DIR}`. A generator should preserve these only in Claude-targeted bodies or provide target-specific substitutions; other harnesses may treat them as literal text.

## Subagents

Plugin subagents are Markdown files under `agents/`; the body is the agent’s system prompt and the YAML frontmatter configures it. Plugin `agents/` directories are scanned recursively. A nested file such as `agents/review/security.md` is addressed as `plugin-name:review:security` in Claude’s UI and `@`-mention typeahead.

```markdown
---
name: security-reviewer
description: Find security vulnerabilities in changed code and explain practical fixes.
model: sonnet
effort: medium
maxTurns: 20
tools: Read, Grep, Glob
disallowedTools: Write, Edit
skills:
  - code-review
isolation: worktree
---

You are a security reviewer. Inspect the requested files and changes, identify
credible vulnerabilities, and return prioritized, actionable findings with exact
file and line references. Do not modify files.
```

The supported plugin-agent fields are `name`, `description`, `model`, `effort`, `maxTurns`, `tools`, `disallowedTools`, `skills`, `memory`, `background`, and `isolation` (whose only valid value is `worktree`). `hooks`, `mcpServers`, and `permissionMode` are intentionally unsupported for plugin-shipped agents and are ignored. If the source model uses those fields, the adapter should warn and either strip them or require a project/user agent output instead.

`name` and `description` are required for normal project/user/managed subagent files. Claude Code is more permissive for plugin agents: a missing `name` falls back to the filename, and unparsable frontmatter still registers the agent using the filename and a generic description. Do not depend on that fallback in generated output; always emit valid frontmatter and explicit `name`/`description`. Agent names cannot contain `:` because the colon is reserved for plugin-scoped identifiers.

The `skills` field preloads the full content of the named skills into the subagent’s initial context. It is distinct from listing `Skill` in `tools`. A subagent normally starts in the parent working directory; `isolation: worktree` requests an isolated Git worktree. The body receives the agent system prompt plus environment details and the delegation task, not the full parent conversation. ([Plugins reference: agents](https://code.claude.com/docs/en/plugins-reference), [Subagents](https://code.claude.com/docs/en/sub-agents))

## Git marketplace and end-user installation

A marketplace is a separate catalog directory/repository with `.claude-plugin/marketplace.json` at its root. The catalog requires `name`, `owner.name`, and a `plugins` array. Each entry requires a public-facing `name` and `source`; a same-repository plugin should use a path beginning with `./`, resolved relative to the marketplace root (the directory containing `.claude-plugin/`), not relative to `.claude-plugin/` itself.

```text
my-marketplace/
├── .claude-plugin/
│   ├── marketplace.json
│   └── (no plugin components here)
└── plugins/
    └── example-tools/
        ├── .claude-plugin/plugin.json
        ├── skills/
        └── agents/
```

```json
{
  "$schema": "https://json.schemastore.org/claude-code-marketplace.json",
  "name": "example-marketplace",
  "owner": {
    "name": "Example Team",
    "url": "https://github.com/example"
  },
  "plugins": [
    {
      "name": "example-tools",
      "source": "./plugins/example-tools",
      "description": "Reusable review and release workflows",
      "version": "1.0.0"
    }
  ]
}
```

GitHub is the recommended host. The shortest user flow is:

```text
/plugin marketplace add example/example-marketplace
/plugin install example-tools@example-marketplace
```

For a non-GitHub host, users provide a full Git URL (for example, `https://gitlab.com/example/plugins.git`). A GitHub shorthand can be pinned with `@ref`; a Git URL can be pinned with `#ref`. Plugin source entries can use relative paths, GitHub repositories, Git repositories, Git subdirectories, npm packages, archives, or command sources. For reproducible releases, a Git plugin source can carry both `ref` and a full 40-character `sha`; the SHA is the effective pin.

The non-interactive equivalent is useful for project bootstrap scripts:

```sh
claude plugin marketplace add example/example-marketplace --scope project
claude plugin install example-tools@example-marketplace --scope project
```

Project scope writes `enabledPlugins` into the repository’s `.claude/settings.json`, so collaborators receive the declaration when they clone the project. User scope (the default) writes to `~/.claude/settings.json`; local scope writes to `.claude/settings.local.json` and is intended for one user in one project. A project marketplace and plugin declaration is the most seamless Git-based installation path for a team: users clone the repo, accept workspace trust, and start Claude Code. They still need Claude Code installed and may need to run `/reload-plugins` when an install summary requests it.

Claude Code copies marketplace plugins into `~/.claude/plugins/cache` by version. Therefore each installed plugin must be self-contained: paths such as `../shared-utils` are rejected and files outside the plugin are not copied. For same-marketplace sharing, use a symlink only when the target is within the marketplace; links outside the marketplace are skipped. A plugin’s own relative paths should use `${CLAUDE_PLUGIN_ROOT}` where a script or MCP/LSP configuration needs the installed plugin directory.

Private Git repositories use the user’s existing Git credential helpers for manual marketplace add/install/update operations. SSH requires the host in `known_hosts` and a loaded key; GitHub shorthand sources use SSH by default unless `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` is set. A generated README should document the repository’s visibility and credential expectation rather than trying to embed tokens.

Validate generated output before publishing:

```sh
claude plugin validate ./plugins/example-tools --strict
claude plugin validate . --strict
```

The first checks a plugin manifest and its default skills, agents, commands, and hooks; the second checks the marketplace and local plugin entries. In CI, run the validator against every generated Claude plugin and the marketplace root. ([Plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces), [Discover and install plugins](https://code.claude.com/docs/en/discover-plugins), [Plugins reference: validation and caching](https://code.claude.com/docs/en/plugins-reference))

## Generator requirements

The adapter should treat Claude output as a target renderer over a harness-neutral source model:

1. Emit one plugin directory per Claude package, with `.claude-plugin/plugin.json` and root-level `skills/` and `agents/`.
2. Generate an explicit kebab-case plugin `name`, stable skill `name`, and valid agent `name`/`description`; do not rely on filename or install-directory fallbacks.
3. Keep portable skill frontmatter to the six fields accepted by the Agent Skills distribution paths. Store Claude-only invocation, fork, path, shell, and permission controls in target-specific metadata and emit them only when requested.
4. For agents, map only the plugin-supported fields. Warn on or drop `hooks`, `mcpServers`, and `permissionMode` rather than producing a file that appears configured but silently loses behavior.
5. Keep every referenced file under the plugin root, use `${CLAUDE_PLUGIN_ROOT}` for installed paths, and copy or explicitly package shared resources. Avoid cross-plugin `../` references.
6. Generate `.claude-plugin/marketplace.json` at the Git repository root with `./plugins/<plugin>` sources, a stable marketplace name, owner metadata, and synchronized versions.
7. Generate an end-user README with the two `/plugin` commands plus non-interactive `claude plugin ... --scope project` commands, and include `/reload-plugins` when the install summary asks for it.
8. Run `claude plugin validate --strict` as a release/CI check. Pin Git sources to a release tag and, when reproducibility matters, a commit SHA.

The main compatibility boundary is that Claude Code plugins are a package and distribution mechanism, while `SKILL.md` is the cross-tool part. A single source can share skill bodies and portable frontmatter across harnesses, but Claude’s plugin manifest, namespacing, agent fields, install cache, and marketplace catalog need a dedicated renderer.
