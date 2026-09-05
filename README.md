# Any Harness

Write skills and agents once. Generate native files for **Claude Code, Codex, OpenCode, and Cursor**, or install them directly from a Git repository.

A small Python CLI with two runtime dependencies: PyYAML and tomli-w. No Node, background service, LLM API key, or harness SDK. The generator runs without any harness installed.

## Start here

Requires Python 3.9+ (3.11+ recommended). In a checkout of this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/any-harness init my-plugin --name my-plugin --author "Your name"
.venv/bin/any-harness add agent reviewer --source my-plugin --description "Review changes for bugs"
.venv/bin/any-harness validate my-plugin
.venv/bin/any-harness build my-plugin --out dist
```

On Windows use `.venv\Scripts\python.exe` and `.venv\Scripts\any-harness.exe`. Subsequent examples assume `any-harness` is on PATH, either by activating the environment or using `uv tool install .`. `uv` can manage Python for you; it is optional. This project has not been published to PyPI.

Edit the generated starter instructions before sharing your plugin. See [the working example](examples/review-kit) and [source format](docs/source-format.md).

## One source folder

```text
my-plugin/
├── plugin.yaml
├── skills/
│   └── explain-changes/
│       ├── SKILL.md
│       └── references/checklist.md
└── agents/
    └── reviewer.md
```

Both skills and agents use YAML frontmatter with `name` and `description`, followed by their Markdown instructions. A skill name matches its folder; an agent name matches its filename. Keep the common instructions in one place, and add native settings only where needed:

```yaml
---
name: reviewer
description: Review changes for bugs and missing tests
targets:
  claude-code:
    settings:
      tools: Read, Grep, Glob
  codex:
    settings:
      sandbox_mode: read-only
  cursor:
    settings:
      readonly: true
  opencode:
    settings:
      permission:
        edit: deny
        bash: deny
---

Review the changes without editing files. Explain each bug's trigger and
consequence, and cite file paths and lines.
```

The generator removes `targets` and emits native frontmatter or TOML. It does **not** equate those permission settings: each retains its harness's meaning. With no overrides, model and tool access inherit the harness defaults.

Set per-harness model and reasoning options in the same `targets.<harness>.settings` blocks. The [model settings reference](docs/model-settings.md) lists the current native fields and explains which controls apply to skills or agents.

## Install from Git

After installing this CLI, end users run one command:

```sh
any-harness install git+https://github.com/OWNER/PLUGIN.git --ref v1.0.0 --harness cursor --project .
```

Replace `OWNER/PLUGIN` with the plugin's repository. Choose `claude-code`, `codex`, `opencode`, or `cursor`; choose `--project DIR` or `--global`. Use `--subdir plugins/my-plugin` for a plugin inside a larger repository. A local path also works:

```sh
any-harness install ./my-plugin --harness codex --project ../my-app --dry-run
any-harness install ./my-plugin --harness codex --project ../my-app
any-harness uninstall my-plugin --harness codex --project ../my-app
```

Repeat the install command with a new `--ref` to update. The receipt records the resolved commit. Installs refuse unmanaged files, another plugin's files, and local edits to previously installed files. Updates remove obsolete owned files. Uninstall removes only unchanged owned files. Open a new harness session afterward.

For a one-off CLI launch from this repository on GitHub:

```sh
  uvx --from git+https://github.com/alymaquiling/any-harness.git any-harness install git+https://github.com/OWNER/PLUGIN.git --harness claude-code --global
```

The plugin URL above is a placeholder for the plugin you publish. This tool's
repository is `https://github.com/alymaquiling/any-harness.git`. See
[distribution and updates](docs/distribution.md) for native marketplace
installation, release workflows, and runtime limitations.

## Let your agent author compatible content

The CLI includes an authoring plugin with five skills:

```sh
any-harness authoring --harness codex --project .
# Or use claude-code, cursor, or opencode; --global is also available.
```

Ask your harness to use **create-portable-plugin**, **create-portable-skill**, **create-portable-agent**, **create-eval-suite**, or **add-client-harness**. Codex exposes `$create-portable-skill`; Claude Code and Cursor expose `/create-portable-skill` for direct installs. In OpenCode, ask it to load the named skill through its native skill tool. Plugin marketplace installs may add a plugin namespace to the skill name.

For example: “Use create-portable-agent to add a reviewer that checks database migrations to this plugin.” To add repeatable native checks, ask for “Use create-eval-suite to cover the reviewer’s automatic activation, explicit invocation, findings, and prohibited edits.” The bundled skills teach the actual source format, scaffold commands, native settings, and validation. Their [source folder](src/any_harness/bundled/authoring) is also a real input to this generator.

In a checkout of this tool, ask “Use add-client-harness to support NEW-HARNESS.” That skill dispatches independent subagents to research official formats and installation, then guides implementation, compatibility tests, and documentation for a native adapter.

## Generated output

Each `dist/<harness>/` is independent:

- **Claude Code:** `.claude-plugin/marketplace.json`, a native plugin under `plugins/<name>`, and `.claude` project files under `project/`.
- **Codex:** `.agents/plugins/marketplace.json`, a `.codex-plugin` skills package, and `.agents/skills` plus native `.codex/agents/*.toml` under `project/`. The documented plugin package does not install custom agents; use the direct installer for both.
- **Cursor:** `.cursor-plugin/marketplace.json`, a native plugin containing skills and agents, and `.cursor` project files.
- **OpenCode:** `.opencode/skills` and `.opencode/agents` under `project/`. Targets the stable V1 file format. No executable JavaScript plugin is synthesized; V2 beta's changed plugin API is outside this tool's scope.

Every output includes `INSTALL.md` and `compatibility.json`. Choose one installation method per plugin/harness; compatibility discovery across harness directories can otherwise produce duplicates. Explicit native overrides and support files stay inside the source folder. Hooks, MCP servers, model aliases, tool permissions, and executable plugin APIs are **not automatically translated**.

## Documentation and checks

- [Validation and current evidence](docs/validation.md)
- [Source format and native escape hatches](docs/source-format.md)
- [Model and reasoning settings](docs/model-settings.md)
- [Native harness evaluations](docs/evals.md)
- [Git distribution, installation, and maintenance](docs/distribution.md)
- Research: [Claude Code](docs/research/claude-code.md), [Codex](docs/research/codex.md), [OpenCode](docs/research/opencode.md), [Cursor](docs/research/cursor.md)

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
any-harness build examples/review-kit --out dist/example
any-harness build examples/review-kit --out dist/example --check
any-harness eval validate examples/review-kit
any-harness eval preview examples/review-kit --harness codex
```

Tests exercise generation, YAML validation, preservation of resources, installation ownership, update/uninstall, and failure paths. See [validation and current evidence](docs/validation.md) for the packaged-wheel check, the HTTPS Git test, the local Codex manifest/discovery checks, and the native host behavior that remains unverified. Official-source research was checked September 5, 2026; native escape hatches are passed through, not validated against every vendor's evolving configuration schema.

Inspired by the single-source approach in [wshobson/agents](https://github.com/wshobson/agents). This implementation focuses on a reusable generator and installer rather than a large content catalog.
