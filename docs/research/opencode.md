# OpenCode adapter research

Research checked 2026-09-05 against OpenCode's official documentation. OpenCode currently has two documented tracks: OpenCode 1 (opencode) and OpenCode 2 (opencode2). The V2 installation is explicitly beta, can be installed beside V1, and changes the plugin API, server/client contracts, and terminal client configuration. The official migration guide says that V1 plugins do not work in V2. A generator therefore needs an explicit OpenCode target (v1 or v2) for executable plugins; skills and most file based definitions are intended to remain compatible. See [V2 introduction](https://opencode.ai/v2/docs) and [V1-to-V2 migration](https://opencode.ai/v2/docs/migrate-v1).

## What OpenCode calls a plugin

OpenCode's “plugin” is executable JavaScript or TypeScript that exports one or more plugin functions. A function receives a context and returns hooks; it can add hooks, custom tools, integrations, commands, agents, and other behavior. This is separate from a skill (Markdown instructions) or an agent (a configured assistant profile). The [V1 plugin guide](https://opencode.ai/docs/plugins) shows a local plugin such as:

~~~js
// .opencode/plugins/example.js
export const MyPlugin = async ({ project, client, $, directory, worktree }) => {
  return {
    // Hook implementations go here
  }
}
~~~

The V1 context includes project, client, Bun's $ shell API, directory, and worktree. TypeScript plugins can import Plugin from @opencode-ai/plugin. The V1 guide also documents events such as session.idle, tool.execute.before, tool.execute.after, and shell.env, plus custom tools. Treat these as OpenCode-specific implementation code rather than portable source content.

### V1 plugin loading and dependencies

Project JavaScript/TypeScript plugins go in .opencode/plugins/; global plugins go in ~/.config/opencode/plugins/. Those files are loaded at startup. A project plugins/ directory beside a root opencode.json is not automatically discovered; put it under .opencode/ or configure its path. The V1 [plugin guide](https://opencode.ai/docs/plugins) documents npm packages through the singular plugin config key:

~~~json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-helicone-session", "@my-org/custom-plugin"]
}
~~~

OpenCode installs configured npm plugins with Bun at startup and caches them under ~/.cache/opencode/node_modules/. A local plugin that imports external packages needs a .opencode/package.json; OpenCode runs bun install at startup. The generator should copy that package manifest with a generated local plugin, or generate a published package entry instead of leaving an unresolved import.

The documented V1 load order is global config, project config, global plugin directory, then project plugin directory. Hooks from all loaded plugins run in sequence, and duplicate npm packages with the same name and version are loaded once. See [plugin loading and dependencies](https://opencode.ai/docs/plugins#use-a-plugin).

### V2 plugin loading and Git installation

V2 uses the plural plugins key and a new plugin API. It discovers direct .ts/.js files and immediate package directories under .opencode/plugins/ and the global OpenCode config directory. A package may also be configured explicitly:

~~~jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": [
    "opencode-acme-plugin",
    "opencode-acme-plugin@1.2.0",
    "@acme/opencode-plugin",
    "./plugins/local",
    "../shared/plugin.ts",
    "/absolute/path/plugin.ts",
    "file:///home/me/plugins/local",
    {
      "package": "@acme/opencode-plugin",
      "options": { "agent": "reviewer", "strict": true }
    }
  ]
}
~~~

Relative plugin paths resolve from the config file containing the entry. The V2 [plugin guide](https://opencode.ai/v2/docs/plugins) says that opencode2 plugin add accepts npm-compatible Git package specifications, including hosted shortcuts, HTTPS/SSH URLs, branches, tags, complete commit hashes, private repositories available through existing Git credentials, and npm's ::path: subdirectory selector:

~~~sh
opencode2 plugin add github:acme/opencode-plugin
opencode2 plugin add git+ssh://git@github.com/acme/opencode-plugin.git#main
opencode2 plugin add 'github:acme/plugins#main::path:packages/opencode-plugin'
~~~

opencode2 plugin list, check, update, and remove manage global package plugins. Exact npm versions and full Git commit hashes remain pinned; unpinned npm/Git plugins can be checked for updates. Local paths, tarballs, and npm aliases are not accepted by plugin add. This gives an OpenCode-specific, one-command Git install for V2. V1's official plugin guide documents local files and npm packages, but does not document a corresponding plugin add Git workflow; for V1, a checked-in .opencode/plugins/ tree or an npm package is the portable path.

V2 also has a separate CLI-only plugin list in global cli.json. A plugin that needs both the OpenCode server and terminal client should keep its server and TUI entrypoints together in the package and document which list it belongs in. See [V2 CLI plugins](https://opencode.ai/v2/docs/cli/plugins).

## Skills

Skills are Markdown instructions loaded on demand through OpenCode's native skill tool. The directory form is important when a skill has scripts, references, or other assets because paths are relative to the directory containing SKILL.md.

~~~text
.opencode/skills/git-release/
├── SKILL.md
├── scripts/changelog.ts
└── references/release-policy.md
~~~

The [V1 Agent Skills guide](https://opencode.ai/docs/skills) requires SKILL.md YAML frontmatter with name and description; it optionally recognizes license, compatibility, and a string-to-string metadata map. Unknown fields are ignored. V1's documented name constraints are 1–64 characters, lowercase alphanumeric words separated by single hyphens, no leading/trailing hyphen or consecutive --, and the name must match the containing directory. Description must be 1–1024 characters. A portable source should follow these constraints even though V2 currently relaxes enforcement.

Portable V1-style skill example:

~~~md
---
name: git-release
description: Create consistent releases and changelogs
license: MIT
compatibility: opencode
metadata:
  audience: maintainers
  workflow: github
---

## Workflow

1. Read references/release-policy.md.
2. Summarize merged changes since the previous tag.
3. Propose the version bump before changing files.
~~~

OpenCode searches project and global .opencode/skills, and also the compatibility locations .claude/skills and .agents/skills. Project discovery walks upward toward the Git worktree. This overlap means a single generated skill can often be emitted once into .agents/skills/<id>/SKILL.md and discovered by OpenCode as well as other Agent Skills-compatible harnesses; use .opencode/skills when targeting OpenCode-native precedence or when avoiding compatibility-directory collisions. See [skill discovery](https://opencode.ai/docs/skills#understand-discovery).

### V2 skill differences

V2 keeps the same recommended directory layout but expands discovery. It accepts root-level Markdown files in a source and SKILL.md at any depth; the directory form remains preferred because it provides a private base directory for support files. V2's skills config array can add local directories or HTTP catalogs:

~~~jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "skills": [
    "./team-skills",
    "~/shared/opencode-skills",
    "/opt/company-skills",
    "https://example.com/opencode/skills/"
  ]
}
~~~

An HTTP catalog has an index.json with a version and file list, for example:

~~~json
{
  "skills": [
    {
      "name": "git-release",
      "version": "3",
      "files": ["git-release.md", "references/release-policy.md"]
    }
  ]
}
~~~

Files are fetched from <base-url>/<name>/<file>, must be safe same-origin relative paths, and the version must change when files change. A named git-release.md gives the ID git-release; a root-level SKILL.md in the downloaded directory currently gives the literal ID SKILL. There is no documented Git URL form in the V2 skills array; use a local path, an HTTP catalog, or place the generated skill tree in the user's checked-out project.

V2 reads name, description, slash, metadata.opencode/slash, and metadata.opencode/autoinvoke. Name and description are optional at runtime, but a description is recommended because only described skills are advertised to the model. License, compatibility, and other metadata are retained for portability but are not interpreted by V2. V2 IDs come from paths, are exact and case-sensitive, and the frontmatter name is only a display label; it is not the ID. For cross-harness output, keep the path ID and frontmatter name aligned with the V1 regex. See [V2 skills](https://opencode.ai/v2/docs/skills).

V2 skill precedence is built-ins, compatibility .claude/skills, compatibility .agents/skills, global .opencode skills, project .opencode skills, then explicit skills config entries. Later definitions replace earlier definitions for a duplicate ID. A generator should detect duplicate IDs within its output and warn when writing into a project that already contains the same IDs.

## Agents

An OpenCode agent combines a system prompt, model preference, tool permissions, and display metadata. In V1, agents can be configured in JSON or Markdown. The most portable generated form is a Markdown file under .opencode/agents/<name>.md (or ~/.config/opencode/agents/<name>.md for global installation). The filename becomes the agent name, and the Markdown body is the system prompt:

~~~md
---
description: Reviews code for quality and best practices
mode: subagent
model: anthropic/claude-sonnet-4-20250514
temperature: 0.1
permission:
  edit: deny
  bash: deny
---

You are in code review mode. Focus on correctness, security, performance, and maintainability.
~~~

The [V1 agents guide](https://opencode.ai/docs/agents) documents description, mode (primary, subagent, or all; default all), model (provider/model-id), temperature, steps (maximum agentic iterations), prompt (for JSON configuration), permission, and the legacy tools map. New V1 output should use permission; the docs mark tools as deprecated. V1 permission values are allow, ask, and deny; permission.task controls subagent invocation and permission.skill controls skill loading. A Markdown body is preferable to a JSON prompt: "{file:...}" reference because it keeps the agent self-contained when copied from Git.

V1 JSON configuration uses the singular agent map:

~~~json
{
  "$schema": "https://opencode.ai/config.json",
  "agent": {
    "code-reviewer": {
      "description": "Reviews code for best practices and potential issues",
      "mode": "subagent",
      "model": "anthropic/claude-sonnet-4-20250514",
      "permission": { "edit": "deny" },
      "prompt": "Review the current changes for security, performance, and maintainability."
    }
  }
}
~~~

### V2 agent differences

V2's preferred Markdown location remains .opencode/agents/<name>.md, and the body remains the system prompt. V2 native frontmatter mirrors its JSON agent fields:

~~~md
---
description: Reviews changes without modifying files
mode: subagent
model: anthropic/claude-sonnet-4-5#high
color: "#ff6b6b"
steps: 8
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
---

Review for correctness, security, regressions, and missing tests.
~~~

V2 JSON configuration uses the plural agents map and system instead of prompt:

~~~jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "default_agent": "reviewer",
  "agents": {
    "reviewer": {
      "description": "Reviews changes for correctness, security, and missing tests",
      "mode": "all",
      "model": "anthropic/claude-sonnet-4-5#high",
      "system": "Review the current changes. Report findings before any summary.",
      "color": "#ff6b6b",
      "steps": 8,
      "permissions": [
        { "action": "edit", "resource": "*", "effect": "deny" },
        { "action": "shell", "resource": "*", "effect": "deny" }
      ]
    }
  }
}
~~~

V2 native changes relevant to an adapter are:

| V1 | V2 | Adapter implication |
| --- | --- | --- |
| agent | agents | Select the config key by OpenCode target. |
| prompt | system | Markdown bodies remain the system prompt; only JSON needs the rename. |
| permission | permissions | Convert grouped tool maps to ordered {action, resource, effect} rules. |
| bash permission action | shell | Map shell command permissions explicitly. |
| task permission action | subagent | Map child-agent permissions explicitly. |
| write/patch permission action | edit | Map all edit/write/patch tools to V2 edit. |
| disable | disabled | Rename when emitting V2 JSON/frontmatter. |
| maxSteps | steps | Use positive steps; maxSteps is legacy. |
| separate variant | model: provider/model#variant | Join the variant to the model reference. |
| temperature, top_p, provider options | request.body (but see caveat) | Do not rely on agent request overlays until the V2 runner applies them; prefer provider/model variant settings. |

The [V2 agents guide](https://opencode.ai/v2/docs/agents) says custom agent mode defaults to all, descriptions are recommended for model-facing subagent catalogs, and permissions are ordered with the last matching rule winning. It also warns that the current V2 runner preserves request.headers/request.body overlays but does not yet apply them to model requests. It specifically advises against new V2 use of legacy fields such as temperature, top_p, prompt, permission, tools, disable, and maxSteps.

The V2 migration guide says V1 agent frontmatter is translated automatically, so a single V1-compatible Markdown agent can be a useful fallback when the generator does not need V2-only features. That fallback loses or cannot express V2-only fields such as ordered permissions, color, or request overlays. Emit separate V1/V2 files when those semantics matter. See [V2 agent migration](https://opencode.ai/v2/docs/migrate-v1#agent-files).

## Git distribution and end-user installation

There are three practical distribution paths:

1. **Project checkout (skills and agents).** Commit .opencode/skills/**, .opencode/agents/**, and any supporting files to the plugin repository. A user clones the repository as their project or copies/merges its .opencode directory into an existing project; OpenCode discovers project definitions while walking toward the Git worktree. A root opencode.json(c) is also explicitly safe to commit. This is the lowest-friction path for file-based content and needs no package manager.
2. **V1 executable plugin.** Publish an npm package and tell users to add it under plugin in opencode.json, or commit local JS/TS under .opencode/plugins/ with .opencode/package.json for dependencies. The stable docs do not specify a Git URL installer for V1.
3. **V2 executable plugin.** Publish to npm or tell users to run opencode2 plugin add github:org/repo, a git+ssh URL, or another npm-compatible Git spec. Use #tag or a full commit hash for reproducibility; use ::path: for a monorepo subdirectory. Private repositories work through the user's existing Git credentials. V2 package installation is global through the CLI, while project-local files are loaded from the checked-out .opencode/plugins tree.

For a repository containing all harness outputs, make the OpenCode install command target the generated OpenCode package subdirectory (for example, github:org/plugin-suite#v1::path:dist/opencode-v1 or ...#v2::path:dist/opencode-v2) rather than asking users to manually copy files. Keep a clearly documented fallback for file-based skills/agents because plugin add is for package plugins, not a general Git skill installer.

## Adapter requirements for the generator

The canonical source model should contain at least a stable kebab-case ID, display name, description, Markdown body, optional assets/scripts/references, and optional agent/plugin capability metadata. Preserve each item directory as a unit so relative paths continue to resolve after generation.

For OpenCode output:

- Copy skills to .opencode/skills/<id>/SKILL.md and copy all sibling assets. Keep name and the path ID aligned with the strict V1 regex so the same output remains portable to .claude/skills/ and .agents/skills consumers. Add description even though V2 runtime permits it to be omitted.
- Copy agents to .opencode/agents/<id>.md. Put the system instructions in the Markdown body, not in a frontmatter system field. Render V1 frontmatter (permission, optional separate variant) or V2 frontmatter (permissions, joined model#variant) according to the selected target.
- Generate opencode.json only when JSON configuration is needed. Use agent/plugin/permission for V1 and agents/plugins/permissions for V2; do not merge V1 and V2 nested agent entries in one generated object.
- Treat executable plugins as target-specific source files. V1 and V2 plugin APIs are not interchangeable. Emit .opencode/plugins/ plus .opencode/package.json for local V1/V2 code, or emit a package directory with the appropriate entrypoint and package metadata for V2 Git/npm installation.
- Record the OpenCode target and plugin API generation in the generated manifest/README. A Git repository alone does not tell a user whether to run opencode or opencode2, nor whether to use plugin or plugins.
- Validate duplicate skill IDs and agent filenames before generation. For HTTP skill catalogs, generate the V2 index.json, require a version field, and ensure every listed file is same-origin and includes the named Markdown entrypoint.
- Generate an install snippet for each path: project checkout instructions for skills/agents, V1 config/npm instructions for stable plugins, and opencode2 plugin add ... with a pinned tag or commit for V2 package plugins.

## Sources

- [OpenCode V1 intro](https://opencode.ai/docs)
- [V1 plugins](https://opencode.ai/docs/plugins)
- [V1 Agent Skills](https://opencode.ai/docs/skills)
- [V1 agents](https://opencode.ai/docs/agents)
- [V2 intro](https://opencode.ai/v2/docs)
- [V2 plugins and Git package installation](https://opencode.ai/v2/docs/plugins)
- [V2 CLI plugins](https://opencode.ai/v2/docs/cli/plugins)
- [V2 skills and HTTP catalogs](https://opencode.ai/v2/docs/skills)
- [V2 agents](https://opencode.ai/v2/docs/agents)
- [V1-to-V2 migration](https://opencode.ai/v2/docs/migrate-v1)
- [V2 configuration](https://opencode.ai/v2/docs/config)
