# Share skills across a team

Use a shared Git repository for reviewed skills and agents, then install a pinned revision into each working repository. Developers can choose Claude Code, Codex, Cursor, or OpenCode while maintaining the same source instructions.

This guide covers migrating existing copies, organizing workflows across repositories, connecting internal MCP servers, and releasing updates. The examples use development, documentation, and operations workflows, but the same approach applies to other teams.

Any Harness generates native skill and agent files, installs them, and tracks ownership for updates. It does not provide universal path routing, plugin dependency resolution, MCP configuration merging, or identical permission enforcement across clients. The optional team setup layer below handles the parts outside the CLI.

## 1. Choose where each kind of information belongs

Keep reusable workflows in the shared plugin repository. Keep repository-specific paths, commands, ownership, and component relationships beside the code they describe. Keep authoritative schemas and interfaces in their owning repositories, and retrieve live infrastructure information through the relevant MCP server.

For example, a shared change-review skill can explain how to assess compatibility. The consumer repository identifies its schema location and validation command. The skill reads those sources when needed, instead of carrying another copy of a schema that can become stale.

Assign reviewers for the shared content. A skill change that affects infrastructure operations should reach the same owners who review that operational workflow today.

## 2. Create a shared plugin repository

Start with one plugin if the team shares a single workflow. Split plugins when users need different subsets or ownership differs. Several plugins can live in one Git repository:

```text
team-workflows/
├── README.md
├── CODEOWNERS
├── plugins/
│   ├── team-core/
│   │   ├── plugin.yaml
│   │   └── skills/
│   │       └── team-standards/
│   │           ├── SKILL.md
│   │           └── references/mcp-contract.md
│   ├── team-development/
│   │   ├── plugin.yaml
│   │   └── skills/
│   │       └── team-change-component/
│   │           ├── SKILL.md
│   │           └── references/change-checklist.md
│   ├── team-documentation/
│   │   ├── plugin.yaml
│   │   └── skills/
│   │       └── team-update-guide/
│   │           └── SKILL.md
│   └── team-operations/
│       ├── plugin.yaml
│       └── skills/
│           └── team-review-infrastructure/
│               └── SKILL.md
└── setup/                         # Optional team-owned tooling
    ├── bootstrap.py
    ├── check.py
    └── templates/
        ├── claude-code/
        ├── codex/
        ├── cursor/
        └── opencode/
```

Only the plugin folders use the Any Harness source format. The `setup/` scripts and templates are suggested additions, not files or commands supplied by Any Harness.

Install the CLI as described in the [README](../README.md). From a new `team-workflows` repository, scaffold a plugin and skill:

```sh
any-harness init plugins/team-development --author "Your team"
any-harness add skill team-change-component \
  --source plugins/team-development \
  --description "Change a component and validate affected consumers"
```

Repeat for the other plugins you need. `init` creates a `getting-started` skill. Remove that starter or rename both its folder and frontmatter `name` before installing multiple plugins into one project. Direct installs share a native skill directory, so names must be unique across the plugins installed there. A plugin name does not automatically namespace its directly installed skill names.

Each plugin needs its own manifest:

```yaml
schema: 1
name: team-development
version: 0.1.0
description: Development workflows for maintained components
author:
  name: Your team
```

Keep shared dependencies explicit. If a development skill requires `team-standards`, document that requirement and install both plugins. Any Harness does not resolve or install plugin dependencies automatically.

## 3. Consolidate existing manual copies

Inventory the existing project, global, and marketplace installations before replacing them. Compare copies of the same skill and bring useful local changes into the shared source through review.

For each migrated skill:

1. Give it a stable name and a description that explains when to use it.
2. Keep common instructions in its Markdown body.
3. Put bundled references and scripts inside that skill's directory.
4. Move repository-specific paths and commands into repository context files.
5. Move client-specific settings into explicit `targets` blocks only when needed.
6. Validate the plugin with `any-harness validate plugins/team-development`.

All regular skill resources are copied, including executable scripts. Symlinks are rejected. The installer does not execute those scripts or install their dependencies, so document runtime requirements in the skill's `compatibility` field and setup instructions. Agent files are self-contained; put bundled references in skills rather than beside agent files.

Before the first managed installation, preserve useful edits and remove the exact legacy copies being replaced from their discovery locations. Any Harness refuses unmanaged collisions even when the file contents match. There is no automatic legacy import or adoption step.

See [source format](source-format.md) for supported fields and native overrides.

## 4. Describe repository context and component relationships

For repositories with unrelated projects or components spread across several paths, install at a common project root and provide an explicit component map. Do not assume a component occupies one directory.

One possible consumer layout is:

```text
working-repository/
├── .team-ai/
│   ├── pins.yaml
│   ├── instructions.md
│   └── components.yaml
├── applications/component-a/
├── applications/component-b/
└── packages/shared-client/
```

The `.team-ai/` directory is a team convention proposed by this guide. Any Harness does not parse these files. Skills read the instructions and component map; an optional team bootstrap script reads the pins.

For example, `components.yaml` could contain:

```yaml
components:
  component-a:
    paths:
      - applications/component-a/**
    shared_components:
      - shared-client
    documentation:
      repository: team-docs
      path: guides/component-a.md
    infrastructure:
      repository: team-platform
      path: modules/component-a

  component-b:
    paths:
      - applications/component-b/**
    shared_components:
      - shared-client

shared_components:
  shared-client:
    paths:
      - packages/shared-client/**
    consumers:
      - component-a
      - component-b
```

Use the map to identify ownership and related context. Consult the build system's dependency graph when determining affected consumers and tests; a hand-maintained map is not a complete dependency graph.

Resolve repository aliases such as `team-docs` through documented repository URLs, MCP resources, or an untracked local checkout map. Do not assume everyone has the same sibling directories or access to every repository. Record the source revision when compatibility depends on a particular schema or module version.

Documentation and infrastructure repositories can keep their own `.team-ai/instructions.md` with local validation commands and source locations. They only need a component map if their structure calls for one.

## 5. Write workflows and wire up their discovery

A shared skill should explain how to use repository context. For example, `skills/team-change-component/SKILL.md` could contain:

```markdown
---
name: team-change-component
description: >-
  Use when changing a maintained component or shared package described
  in the repository's .team-ai/components.yaml file.
---

Read .team-ai/instructions.md and .team-ai/components.yaml relative to
the working repository root. Identify affected components from the
request and changed paths. If required context is missing, report it.

Load the team-standards skill before choosing an implementation.
If it is unavailable, report the missing installation.

Read references/change-checklist.md relative to this skill directory.

Retrieve applicable framework guidance and internal standards through
the configured MCP servers. Record source identifiers and versions
where available.

Inspect the build system's dependency graph for shared-package changes.
Include affected consumers in the validation plan.

Read authoritative schemas and module interfaces before changing their
consumers. Use the repository mapping to locate external sources.

Run the repository's documented checks. Report changed behavior,
checks performed, and validation gaps.

If required standards or schemas cannot be retrieved, identify what is
missing and pause only the changes that depend on that information.
```

Create the referenced checklist and the `team-standards` skill before using this example. A reference in Markdown is not a dependency declaration or an automatic resource fetch.

Keep essential routing instructions in each client's supported repository instruction mechanism as well. A short entry point can say:

> For work on components listed in `.team-ai/components.yaml`, read `.team-ai/instructions.md` and use the corresponding team workflow. Include shared-package consumers when determining scope.

Manage these entry points as repository files or through your team setup layer. Any Harness does not generate a universal `AGENTS.md`, `CLAUDE.md`, or rules hierarchy. Native overrides can carry supported client-specific files, but their semantics remain client-specific.

Check discovery from the directories where developers actually start sessions. For example, Codex builds its initial instruction chain from the project root to the current working directory. Instructions buried under a component directory are not a substitute for routing at the root when a session starts there. See [Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md) and the [harness research notes](research/).

## 6. Configure internal MCP servers

Keep a shared contract for the information each workflow needs. The `team-standards` skill's `references/mcp-contract.md` can document:

- Logical server names and the information each server owns.
- Actual tool or resource names, required inputs, and authentication setup.
- How to select applicable framework, schema, and standards versions.
- How to handle unavailable sources or conflicting guidance.
- Which operations read information and which modify external systems.

Use consistent logical names where possible, but configure connections in each client's native format:

- Claude Code uses project-root `.mcp.json`. See [Claude Code MCP](https://code.claude.com/docs/en/mcp).
- Codex supports project `.codex/config.toml` in trusted projects. See [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).
- Cursor uses `.cursor/mcp.json`. See [Cursor MCP](https://cursor.com/docs/mcp).
- OpenCode's stable V1 configuration uses `mcp` entries in `opencode.json` or `opencode.jsonc`. See [OpenCode MCP](https://opencode.ai/docs/mcp-servers/).

Store connection templates without credentials. Use each client's supported authentication and environment-variable syntax; those syntaxes are not interchangeable. A team bootstrap script should preserve unrelated configuration and report conflicts instead of replacing an entire user-maintained config.

Any Harness does not translate or merge MCP configuration. Native direct-install files must remain inside the allowed harness directories, so arbitrary repository-root files such as Claude Code's `.mcp.json` and OpenCode's root `opencode.json` cannot be installed that way. Codex supports a native plugin MCP companion, but direct installation includes only the project payload, not plugin-only extras. See [native file support](source-format.md#resources-and-native-files).

Have the team setup check verify authentication and perform a harmless read against each required server in the chosen client. `any-harness doctor` checks Python and executable availability; it does not test MCP connectivity, authentication, or server capabilities.

## 7. Install pinned revisions in working repositories

Publish the reviewed source repository to your team's Git host. Git authentication must already work for private repositories. Direct installation fetches source and generates the chosen client files locally, so you do not need to commit four generated trees.

For a development repository, replace the URL, commit, and destination below, then preview both required plugins:

```sh
for plugin in team-core team-development; do
  any-harness install \
    git+ssh://git@github.com/YOUR-ORG/team-workflows.git \
    --ref '<APPROVED_COMMIT>' \
    --subdir "plugins/$plugin" \
    --harness cursor \
    --project /path/to/working-repository \
    --dry-run
done
```

After reviewing the preview, repeat without `--dry-run`. Choose `claude-code`, `codex`, or `opencode` for another client. Install `team-core` and `team-documentation` in a documentation repository, or `team-core` and `team-operations` in an operations repository.

Use a full commit hash for reproducibility. Tags and branches are resolved again at each installation. Pin the Any Harness tool revision in the team's setup process too, so adapter updates are reviewable alongside skill changes.

For project installs, the native skill locations and ownership receipts are:

```text
.claude/skills/<skill>/       # Claude Code
.agents/skills/<skill>/       # Codex
.cursor/skills/<skill>/       # Cursor
.opencode/skills/<skill>/     # OpenCode
.any-harness/installed/      # Managed installation receipts
```

Install only the clients a developer needs. Some clients also discover skills in other clients' directories, so installing every target in one checkout can produce duplicates. Avoid using both a marketplace install and direct install for the same plugin and client. Start a new client session after installation.

For this workflow, keep generated skills and receipts local, with narrowly scoped ignore entries for the installed content. Commit repository instructions, component maps, and the team's chosen pins. Do not ignore entire client directories if they contain other shared configuration, and preserve local receipts for managed updates and uninstall.

An optional bootstrap script can read the team's `pins.yaml`, install the selected plugins, configure native entry points and MCP connections, and run setup checks. This is a wrapper your team supplies; Any Harness has no built-in team lockfile or cross-repository synchronization command.

## 8. Validate the source and test behavior

From the shared plugin repository, build all plugins for all four targets:

```sh
any-harness build-all . --out dist/all
any-harness build-all . --out dist/all --check
```

For a source-only repository, a clean build validates and renders the collection. If you commit generated output, CI should run `--check` against that committed output before rebuilding it, so drift is detected rather than overwritten. Preserve the build ownership marker and use the same options. See [distribution checks](distribution.md#ci-and-release-checks).

Generation checks do not prove that a client invokes a skill or follows its instructions. Add fixtures and behavioral cases for the decisions that matter:

- A component change retrieves the applicable standards and chooses relevant checks.
- A shared-package change identifies affected consumers.
- An unrelated component avoids team-specific assumptions.
- A schema or module change checks compatibility with consumers.
- Missing MCP information is reported instead of invented.
- A read-only review leaves files and external systems unchanged.

Once a plugin has an eval suite, validate and preview it:

```sh
any-harness eval validate plugins/team-development
any-harness eval preview plugins/team-development --harness codex
```

Follow the [eval guide](evals.md) to run bounded cases in authenticated native clients. Test actual MCP connectivity separately; isolated project-native evals do not exercise marketplace installation or automatically reproduce internal MCP setup. Treat missing invocation evidence as inconclusive, not proof of success.

Record the client versions used for acceptance checks. This tool targets OpenCode's stable V1 format, and native verification has limits for every target. See [current validation evidence](validation.md) before deciding what a passing check establishes.

Keep mandatory schema validation, build checks, infrastructure policy, and access controls enforced through CI and the systems that perform the operations. Shared instructions help agents follow those requirements; they do not enforce them across clients.

## 9. Release updates through review

Use the same process for a new workflow and a correction to an existing one:

1. Edit the canonical skill source through a PR and request the relevant owners' review.
2. Run generation checks and the applicable behavioral cases.
3. Update plugin versions and publish the reviewed Git revision.
4. Update consuming repositories' team-owned pins through PRs.
5. Rerun setup, inspect the install preview, and start fresh client sessions.

Repeating `install` with a new `--ref` updates unchanged owned files and removes obsolete owned files. Local edits block updates and uninstall. Move useful changes back into the shared source through review before restoring the installed copy. Do not treat generated files as the place to maintain a team fix.

To roll back, repeat the same install command with the previous approved commit. The same ownership and local-edit checks apply. Reconcile removed plugins explicitly with `any-harness uninstall`; installing the remaining set does not remove a plugin omitted from your team's pins.

There is no background updater. A team bootstrap or CI check must detect outdated pins or installations if the team wants that behavior. Keep the approved revision visible so developers can tell which workflows they are using and how to update them.
