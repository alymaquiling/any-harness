# Source format, schema 1

The source is a plugin directory with one `plugin.yaml`, skills in `skills/<name>/SKILL.md`, and agents in `agents/<name>.md`. IDs are 1–64 lowercase alphanumeric characters separated by single hyphens. Descriptions are nonempty strings up to 1,024 characters. Names must match their directories or filenames. YAML duplicate keys and unknown shared fields fail validation.

## Manifest

```yaml
schema: 1
name: review-kit
version: 1.0.0
description: Review changes and explain concrete findings
author:
  name: Your team
license: MIT
repository: https://github.com/your-team/review-kit
keywords: [review, development]
```

Required fields are `schema`, `name`, `version`, `description`, and `author.name`. Optional fields are `license`, `repository`, `homepage`, `keywords`, `author.email`, and `author.url`. When present, `repository` and `homepage` must be absolute `http://` or `https://` URLs with a host. Supply your own accurate author/license information. Cursor's stricter author schema cannot represent `author.url`; the adapter omits it with a note.

## Skills and agents

```markdown
---
name: explain-changes
description: Explain a code change and its consequences for reviewers
---

Inspect the requested diff. Explain the behavior before and after the change.
Read references/checklist.md when assessing test coverage.
```

Skills additionally allow `license`, `compatibility`, and `metadata` (string-to-string). Agent shared fields are only `name`, `description`, and `targets`. Keep tool names, model IDs, sandbox options, and execution flags inside a target block. A model omitted from the source inherits the harness default; there is no guessed mapping between vendors.

The Markdown body stays unchanged unless that target supplies `body`. This replaces the body, so prefer shared instructions whenever practical. Harness-specific tool calls, environment variables, and absolute paths embedded in prose are not rewritten. Agent Markdown becomes Codex's `developer_instructions` TOML string and remains Markdown in the other adapters. OpenCode gets `mode: subagent` unless explicitly overridden.

### Target blocks

```yaml
targets:
  cursor:
    settings:
      paths: ["src/**"]
  claude-code:
    settings:
      disable-model-invocation: true
  codex:
    openai:
      policy:
        allow_implicit_invocation: false
  opencode:
    enabled: false
```

This is a **skill** example. `targets.<harness>` supports `enabled` (boolean), `settings` (native mapping), `body` (replacement instructions), and `openai` (Codex skills only). `openai` becomes `agents/openai.yaml` inside that skill; other harnesses never receive it. The source format deliberately does not have a universal manual-only or read-only switch: support and enforcement differ. Use explicit overrides, and disable a target if your workflow needs behavior it cannot provide.

`settings` values are passed through to native skill/agent frontmatter or Codex agent TOML. They cannot override generated `name`, `description`, or `developer_instructions`. This is an escape hatch for vendor features, **not a guarantee that arbitrary keys are accepted or enforced**. Model and reasoning controls are target-specific; see [model and reasoning settings](model-settings.md) for current examples and the documented differences between skills and agents. Validate these settings in the actual harness. The research notes list current fields.

Codex plugin companions have an explicit source location. A regular `native/codex/plugin/.mcp.json` is copied to the package root and referenced as `mcpServers: "./.mcp.json"`; a regular `native/codex/plugin/.app.json` is copied and referenced as `apps: "./.app.json"`. Each manifest field is emitted only when its companion exists, so generated manifests do not contain dangling paths. The companion contents remain native Codex configuration and are not translated.

## Resources and native files

Keep a skill's resources inside that skill directory. All regular files are copied, including binary assets and executable scripts. Executable permissions are preserved. Symlinks are rejected. The installer copies scripts but does not execute plugin scripts or install their dependencies. State runtime requirements in the skill's `compatibility` field and document setup.

Agent files are self-contained. They cannot bundle sibling resources; put reusable references in a skill and invoke that skill explicitly if appropriate. Avoid hardcoded plugin-root environment variables in shared instructions.

For capabilities that really require native files, use:

```text
native/
  cursor/
    plugin/
      rules/project-style.mdc
    project/
      .cursor/rules/project-style.mdc
  opencode/
    project/
      .opencode/plugins/example.ts
```

`plugin/` is copied into the native plugin package; `project/` is copied into the direct-install payload. Files must stay under that harness's project directories (`.claude`, `.cursor`, `.opencode`, or `.agents`/`.codex`). OpenCode accepts only `project/`, since executable runtime plugins have a different package API. Native files cannot replace generated files. They are intentional native implementations: the tool does not translate their semantics, merge existing user configs, synthesize dependencies, or publish runtime packages. Avoid a native config file that your users already maintain; a collision fails rather than overwriting it.

## Build behavior

`validate` parses the complete source and renders every adapter in memory. `build` emits deterministic files and adds a build ownership marker. Later builds replace that entire owned directory, removing stale output. A non-owned existing output directory, source directory, source ancestor, or component subtree is refused. Do not hand-edit generated output or add valuable files inside it.

`build --harness cursor --harness codex` restricts targets. `build --check` compares bytes, paths, and executable modes without writing; it also detects extra output files. Select the same targets when checking as when building. Pin the CLI version or commit in CI so future adapter updates are reviewable.

Schema 1 models one plugin per source folder. For a repository with several plugins, run the CLI for each source and use `--subdir` at install time. Marketplace aggregation, automatic legacy-plugin imports, and translation of arbitrary hooks/MCP/tool permissions are outside the current scope; the explicit Codex companion wiring described above is the supported exception.
