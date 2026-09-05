---
name: create-portable-plugin
description: Create or maintain an any-harness source plugin that generates native skills and agents for Claude Code, Codex, OpenCode, and Cursor. Use when scaffolding a cross-harness plugin or deciding its component layout.
---

# Create a portable plugin

Use one source directory as the contract for every harness. Keep shared behavior
under `skills/` and `agents/`; use `native/<harness>/plugin/` or
`native/<harness>/project/` only for deliberate harness-specific files.

## Workflow

For a new plugin, scaffold an empty destination with the current CLI command:

```sh
any-harness init PATH --name NAME
```

`PATH` is the canonical source directory. Edit the generated `plugin.yaml`,
then add components with the component-specific skills or these exact commands:

```sh
any-harness add skill NAME --source PATH --description TEXT
any-harness add agent NAME --source PATH --description TEXT
```

Replace each scaffold body with the real workflow. Validate before generating
native output, then build into a separate directory:

```sh
any-harness validate SOURCE
any-harness build SOURCE --out OUTPUT
```

Keep the source directory free of generated output. Use project-scoped
installation when a user asks to install the result; do not turn authoring into
a global installation.

## Source layout

```text
plugin-source/
├── plugin.yaml
├── skills/
│   └── review-api/
│       └── SKILL.md
├── agents/
│   └── security-reviewer.md
└── native/
    └── cursor/
        ├── plugin/
        └── project/
```

`native/` is optional. Do not copy a harness-only manifest or agent format
into shared `skills/` or `agents/`; put it under the matching native directory
so other adapters can omit it with a clear compatibility note.

## `plugin.yaml`

Use the source format below. `schema`, `name`, `version`, `description`, and an
author name are required by the loader. Names use lowercase letters, digits,
and single hyphens; versions use semantic versioning.

```yaml
schema: 1
name: api-toolkit
version: 1.0.0
description: Review API changes and produce implementation guidance.
author:
  name: Example Team
  email: team@example.com
license: MIT
repository: https://github.com/example/api-toolkit
keywords:
  - api
  - review
```

Shared component files use only the portable `name` and `description`
frontmatter fields. Keep descriptions specific enough for automatic discovery;
put the actual workflow, inputs, and output contract in the markdown body.

When an agent needs a target-specific model or reasoning setting, use its
`targets.<harness>.settings` block and keep the native key in the matching
target. Do not treat `effort`, `reasoningEffort`, `model_reasoning_effort`, or
Cursor's bracketed model options as interchangeable. See
`docs/model-settings.md` in the any-harness checkout when available before
choosing a value.

## Before handoff

Run `any-harness validate SOURCE` and fix every error. Then run
`any-harness build SOURCE --out OUTPUT` and inspect each generated harness
directory. Check that skill folder names, agent filenames, and frontmatter names
match exactly, and that target-specific files were intentionally placed under
`native/`.
