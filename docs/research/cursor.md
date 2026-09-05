# Cursor adapter research

Research date: 2026-09-05. Sources are Cursor's public documentation and the
official [`cursor/plugins`](https://github.com/cursor/plugins) repository. Cursor
is evolving the plugin format alongside the vendor-neutral Agent Plugins and
Agent Skills standards, so the generator should keep a small, explicit Cursor
adapter and validate output against the current schemas.

## Product model

Cursor recognizes two plugin formats:

| Format | Manifest | Components |
| --- | --- | --- |
| Agent Plugin (open standard) | `plugin.json` at the plugin root | Skills and MCP servers |
| Cursor Plugin | `.cursor-plugin/plugin.json` | Skills, MCP servers, rules, agents, commands, hooks, and variables |

The same repository may contain both generated outputs if the source project
needs a portable package and Cursor-only capabilities. A root Agent Plugin is
loaded by Cursor without changes when it conforms to the Agent Plugins
specification. A Cursor Plugin is the required output when the package includes
agents, rules, commands, hooks, or Cursor variables. See [Plugins reference](https://cursor.com/docs/reference/plugins#supported-plugin-formats)
and [Plugins](https://cursor.com/docs/plugins#the-agent-plugins-standard).

For a full Cursor Plugin, the normal tree is:

```text
my-plugin/
├── .cursor-plugin/
│   └── plugin.json
├── skills/
│   └── code-reviewer/
│       └── SKILL.md
├── agents/
│   └── security-reviewer.md
├── rules/
│   └── prefer-const.mdc
├── commands/
│   └── deploy-staging.md
├── hooks/
│   └── hooks.json
├── mcp.json
└── README.md
```

The minimal Agent Plugin tree is the same except that `plugin.json` is at the
root and only portable skills and MCP configuration are promised:

```text
my-agent-plugin/
├── plugin.json
├── skills/
│   └── code-reviewer/
│       └── SKILL.md
└── mcp.json
```

## Cursor Plugin manifest

The required field is `name`. Cursor documents lowercase kebab-case identifiers
(periods are also accepted by the official schema), beginning and ending with
an alphanumeric character. The official schema uses:

```text
^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$
```

The current official schema also accepts `displayName`, `description`,
`version`, `author` (`name` required, `email` optional), `publisher`,
`homepage`, `repository`, `license`, `logo`, `keywords`, `category`, `tags`,
`minClientVersions`, `commands`, `agents`, `skills`, `rules`, `hooks`,
`variables`, and `mcpServers`. Component path fields accept a string or an
array of strings. `hooks` accepts a path or an inline object. `mcpServers`
accepts a path, inline object, or an array of either. The official schema has
`additionalProperties: false`, so the adapter should reject unknown manifest
keys rather than silently emitting them.

The official schema is maintained at
[`schemas/plugin.schema.json`](https://raw.githubusercontent.com/cursor/plugins/main/schemas/plugin.schema.json).
Its `$id` is `https://cursor.com/schemas/cursor-plugin/plugin.json`.

A representative full manifest, based on Cursor's official `create-plugin`
plugin, is:

```json
{
  "name": "create-plugin",
  "displayName": "Create Plugin",
  "version": "1.0.0",
  "description": "Scaffold and validate new agent plugins.",
  "author": {
    "name": "Cursor",
    "email": "plugins@cursor.com"
  },
  "homepage": "https://github.com/cursor/plugins",
  "repository": "https://github.com/cursor/plugins",
  "license": "MIT",
  "logo": "assets/avatar.png",
  "keywords": ["create-plugin", "cursor-plugin", "marketplace"],
  "category": "developer-tools",
  "tags": ["authoring", "templates"],
  "skills": "./skills/",
  "rules": "./rules/",
  "agents": "./agents/"
}
```

Source: the official [`create-plugin/.cursor-plugin/plugin.json`](https://raw.githubusercontent.com/cursor/plugins/main/create-plugin/.cursor-plugin/plugin.json).
The explicit component paths are optional here because they match Cursor's
default folders; emitting them is useful when the generated tree intentionally
uses another layout.

Do not put secret values in a repository. Cursor's `variables` field is a
restricted JSON Schema describing values users provide through Plugins →
Configure. MCP configuration can refer to those values with `${VAR}`. Every
placeholder used by `mcp.json` should have a matching schema property. The
documented accepted schema keywords are limited (`type`, `title`,
`description`, `default`, `enum`, `const`, `properties`, `required`, `items`,
and common length/numeric constraints).

Example:

```json
// .cursor-plugin/plugin.json
{
  "name": "example-plugin",
  "variables": {
    "type": "object",
    "properties": {
      "API_TOKEN": {
        "type": "string",
        "title": "API token",
        "description": "Bearer token for the example HTTP MCP"
      }
    },
    "required": ["API_TOKEN"]
  }
}
```

```json
// mcp.json
{
  "mcpServers": {
    "example-api": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}"
      }
    }
  }
}
```

See [Variables and manifest fields](https://cursor.com/docs/reference/plugins#variables).

## Component discovery and paths

If a component path is omitted, Cursor scans these locations within the plugin:

| Component | Default path | Discovery |
| --- | --- | --- |
| Skills | `skills/` | Each subdirectory containing `SKILL.md` |
| Rules | `rules/` | `.md`, `.mdc`, or `.markdown` files |
| Agents | `agents/` | `.md`, `.mdc`, or `.markdown` files |
| Commands | `commands/` | `.md`, `.mdc`, `.markdown`, or `.txt` files |
| Hooks | `hooks/hooks.json` | Hook event names |
| MCP servers | `mcp.json` | Server entries |
| Root skill fallback | `SKILL.md` | One skill only when no `skills/` directory and no manifest `skills` field exist |

An explicit manifest field replaces discovery for that component; Cursor does
not additionally scan the default directory. Generated paths therefore need to
be consistent with the tree, and a generator should avoid emitting a path just
because a default folder happens to exist. All paths must be relative, valid,
and free of `..` traversal or absolute prefixes. See [component discovery](https://cursor.com/docs/reference/plugins#cursor-plugin-component-discovery)
and the [submission checklist](https://cursor.com/docs/reference/plugins#submission-checklist).

## Skills

Each skill is a directory containing `SKILL.md`. Cursor recursively walks skill
roots, so category folders are allowed and are organizational only:

```text
.cursor/skills/
├── shipping/
│   └── release-it/
│       └── SKILL.md
└── debugging/
    └── inspect-logs/
        └── SKILL.md
```

The skill identity is the directory containing `SKILL.md`, not its category
parent. Inside a plugin, use `skills/<skill-name>/SKILL.md`.

Cursor's required skill frontmatter is:

```yaml
---
name: api-designer
description: Design REST APIs and document their contracts. Use when creating or reviewing endpoints.
---
```

`name` must contain only lowercase letters, numbers, and hyphens and must match
the skill's parent folder. `description` tells Agent what the skill does and
when to use it. Cursor also documents these optional fields:

| Field | Cursor behavior |
| --- | --- |
| `paths` | One glob or a list of globs; the skill is surfaced only while working on matching files |
| `disable-model-invocation` | `true` makes it manual-only via `/skill-name` |
| `icon` | Custom Mode badge icon |
| `color` | Custom Mode badge color |
| `metadata` | Arbitrary key/value metadata |

`globs` remains accepted as a legacy fallback, but new Cursor skills should use
`paths`. For the portable source format, keep only `name`, `description`, and
the body unless another harness explicitly supports the optional keys.

Skills may contain `scripts/`, `references/`, and `assets/`. Reference these
files with paths relative to the skill root. Cursor loads supporting material on
demand, so keeping detailed material out of the main `SKILL.md` is useful.

Cursor discovers project and user skills from `.agents/skills/`,
`.cursor/skills/`, `~/.agents/skills/`, and `~/.cursor/skills/`; for
compatibility it also reads `.claude/skills/`, `.codex/skills/`, and their user
equivalents. Nested project skill directories are scoped automatically to files
under that directory. See [Agent Skills](https://cursor.com/docs/skills) and
[skill directories](https://cursor.com/docs/skills#skill-directories).

## Agents / subagents

Inside a Cursor Plugin, agents are markdown files in `agents/` with YAML
frontmatter. The minimal plugin form is:

```yaml
---
name: security-reviewer
description: Review security-sensitive changes for vulnerabilities and exposed secrets.
---

# Security reviewer

Review the changed code and report findings by severity.
```

Cursor's standalone project/user locations are `.cursor/agents/` and
`~/.cursor/agents/`. It also accepts `.claude/agents/` and `.codex/agents/` for
compatibility, with `.cursor/` taking precedence on name conflicts. There is no
documented `.agents/agents/` location.

The standalone subagent docs allow additional Cursor-specific fields:

```yaml
---
name: plugin-architect
description: Decide the right component mix, structure, and metadata for a new plugin.
model: inherit
readonly: true
---

Design a focused plugin and return a concrete implementation checklist.
```

`name` and `description` are the portable baseline. `model` defaults to
`inherit` and may name a specific model; `readonly: true` restricts writes; and
`is_background: true` runs without blocking the parent. These scheduling and
permission fields are Cursor-specific adapter fields. A configured model may be
overridden by plan or team restrictions, so it is a preference rather than a
portable guarantee.

The official Cursor `create-plugin` agent demonstrates `model: inherit` and
`readonly: true` in [`agents/plugin-architect.md`](https://raw.githubusercontent.com/cursor/plugins/main/create-plugin/agents/plugin-architect.md).
The full field table and delegation behavior are in [Subagents](https://cursor.com/docs/subagents#configuration-fields).

## Rules, commands, hooks, and MCP

These components are Cursor Plugin extensions and should be emitted only by the
Cursor-specific target when the canonical source requests them:

* Rules are `.mdc` files under `rules/` with `description`, `alwaysApply`, and
  optional `globs` frontmatter.
* Commands may be `.md`, `.mdc`, `.markdown`, or `.txt` under `commands/` and
  may use `name` and `description` frontmatter.
* Hooks live at `hooks/hooks.json`, with event names such as `sessionStart`,
  `preToolUse`, `subagentStart`, `afterFileEdit`, `beforeShellExecution`, and
  `workspaceOpen`.
* Both plugin formats use root `mcp.json`; Agent Plugins use the Agent Plugins
  MCP schema, while Cursor Plugins may infer transport from `command` or `url`.

See the [component reference](https://cursor.com/docs/reference/plugins#rules-format)
for exact examples. Do not map a Cursor rule to a skill automatically: their
triggering and persistence semantics differ.

## Multi-plugin Git repositories

A repository containing multiple Cursor Plugins has a root
`.cursor-plugin/marketplace.json`. Each entry names a plugin and points to its
directory; each plugin directory contains its own
`.cursor-plugin/plugin.json`.

```text
my-plugins/
├── .cursor-plugin/
│   └── marketplace.json
├── api-tools/
│   ├── .cursor-plugin/
│   │   └── plugin.json
│   └── skills/
│       └── api-review/SKILL.md
└── release-tools/
    ├── .cursor-plugin/
    │   └── plugin.json
    └── agents/
        └── release-reviewer.md
```

Documented marketplace shape:

```json
{
  "name": "my-marketplace",
  "owner": {
    "name": "Your Org",
    "email": "plugins@yourorg.com"
  },
  "metadata": {
    "description": "A collection of developer tool plugins"
  },
  "plugins": [
    {
      "name": "api-tools",
      "source": "api-tools",
      "description": "API review skills"
    },
    {
      "name": "release-tools",
      "source": "release-tools",
      "description": "Release review agents"
    }
  ]
}
```

For a source of `api-tools`, Cursor looks for
`api-tools/.cursor-plugin/plugin.json`, merges the marketplace entry with that
manifest, gives the per-plugin manifest precedence, and discovers components
inside `api-tools/`. The official reference documents `name`, `owner`, and
`plugins` as required and says the list may contain up to 500 plugins.

The current official repository also publishes a JSON Schema at
[`schemas/marketplace.schema.json`](https://raw.githubusercontent.com/cursor/plugins/main/schemas/marketplace.schema.json).
That schema currently requires only `name` and `plugins`, treats `owner` as
optional, and restricts each entry to `name`, `source`, `description`, and
`minClientVersions`; it describes `source` as a relative directory or remote
URL. This is a schema/docs drift worth handling explicitly: generate the
documented common shape (`owner`, `metadata`, `name`, `source`, `description`),
but validate against the pinned schema version used by the adapter and avoid
extra per-entry fields unless the target accepts them.

The official multi-plugin repository is a useful fixture: [`cursor/plugins`](https://github.com/cursor/plugins)
has a root marketplace and one manifest per plugin. Its marketplace file uses
source paths such as `third_party/github` for nested plugins.

## Git distribution and end-user installation

For a public plugin, Cursor's documented distribution path is:

1. Push the plugin repository to a public Git host.
2. Submit the repository at [`cursor.com/marketplace/publish`](https://cursor.com/marketplace/publish).
3. After review and listing, users open Customize, find the plugin, select
   Install, and choose project or user scope.

Cursor manually reviews Marketplace plugins and updates; the published plugin
must be open source. The submission checklist requires a valid manifest,
unique lowercase name, descriptions, valid component frontmatter, README,
relative paths, local testing, and matching variable declarations. See
[The marketplace and Installing plugins](https://cursor.com/docs/plugins#the-marketplace)
and [submission](https://cursor.com/docs/reference/plugins#submitting-a-plugin).

For a private or team repository, a Teams/Enterprise admin can open Dashboard
→ Plugins → Add Marketplace → Import from Repo, select the GitHub repository,
review its parsed plugins, and configure access. Auto Refresh requires the
Cursor GitHub App and re-indexes no more often than once every ten minutes;
manual Refresh is also available. Install behavior can be Default Off, Default
On, or Required. See [team marketplaces](https://cursor.com/docs/plugins#add-a-team-marketplace)
and [plugin installation modes](https://cursor.com/docs/plugins#plugin-installation-modes).

For local development, place the plugin at:

```text
~/.cursor/plugins/local/<plugin-name>/
```

Include either root `plugin.json` or `.cursor-plugin/plugin.json`, then reload
the window and inspect Customize. A symlink is supported for iteration:

```bash
ln -s /path/to/my-plugin ~/.cursor/plugins/local/my-plugin
```

Local imports can be disabled by Teams/Enterprise administrators. If a
marketplace plugin with the same name is installed, it takes precedence over
the local copy. See [Test plugins locally](https://cursor.com/docs/plugins#test-plugins-locally).

Cursor also exposes `/add-plugin` in the editor for direct plugin installation
(see the [official 2.5 changelog](https://cursor.com/changelog/2-5) and the
official [`create-plugin` README](https://github.com/cursor/plugins/blob/main/create-plugin/README.md)).
The public documentation does not specify a stable arbitrary-Git URL syntax or
its update guarantees, so generated user docs should make Marketplace/team
marketplace installation the primary path and label direct `/add-plugin` usage
as a Cursor-version-dependent convenience. For skills imported without a full
plugin, Cursor documents Customize → Rules → Add Rule → Remote Rule (Github),
which is a separate skills/rules flow; it should not be presented as full
plugin installation.

## Adapter requirements

The cross-harness generator should implement the following Cursor target rules:

1. Keep canonical component content portable. Generate skill frontmatter with
   required `name` and `description`; require `name` to equal its skill folder.
   Generate agent frontmatter with `name` and `description`; map `model`,
   `readonly`, and `is_background` only when the Cursor target requests them.
2. For the full Cursor target, emit one plugin directory with
   `.cursor-plugin/plugin.json`, `skills/`, `agents/`, and any requested Cursor
   component folders. For the portable Agent Plugin target, emit root
   `plugin.json` and only skills/MCP that the standard supports.
3. Use default folders whenever possible. If a custom path is needed, emit an
   explicit manifest path and ensure the target does not also expect the
   default folder to be scanned.
4. Validate `plugin.json` and marketplace JSON against the pinned official
   schemas. Reject unknown fields, absolute paths, `..` traversal, invalid
   names, invalid SemVer, broken component references, and undeclared `${VAR}`
   placeholders. Avoid putting `$schema` in a Cursor Plugin manifest unless
   the pinned schema explicitly allows it; the current official Cursor
   `plugin.json` schema has `additionalProperties: false`, and official Cursor
   plugin manifests omit `$schema`.
5. For a multi-plugin repository, generate a root
   `.cursor-plugin/marketplace.json`, one per-plugin manifest, unique plugin
   names, and relative `source` paths. Include README/install instructions in
   each plugin or at the repository root.
6. Generate installation documentation with three explicit paths: published
   Marketplace (recommended for end users), team Marketplace (private/team
   distribution), and `~/.cursor/plugins/local` (development/testing). Include
   the required reload step for local installs and the scope choice for
   Marketplace installs.
7. Treat Cursor-only features as capability annotations in the source model,
   so other adapters can omit them with a warning rather than silently
   producing a plugin that claims unsupported behavior.

## Official sources

* [Cursor Plugins](https://cursor.com/docs/plugins)
* [Cursor Plugins reference](https://cursor.com/docs/reference/plugins)
* [Cursor Agent Skills](https://cursor.com/docs/skills)
* [Cursor Subagents](https://cursor.com/docs/subagents)
* [Official Cursor plugin repository](https://github.com/cursor/plugins)
* [Cursor plugin schema](https://raw.githubusercontent.com/cursor/plugins/main/schemas/plugin.schema.json)
* [Cursor marketplace schema](https://raw.githubusercontent.com/cursor/plugins/main/schemas/marketplace.schema.json)
* [Official `create-plugin` manifest](https://raw.githubusercontent.com/cursor/plugins/main/create-plugin/.cursor-plugin/plugin.json)
* [Official `create-plugin` agent example](https://raw.githubusercontent.com/cursor/plugins/main/create-plugin/agents/plugin-architect.md)
* [Cursor 2.5 plugin changelog](https://cursor.com/changelog/2-5)
