# Any Harness

Write skills and agents once. Generate native files for **Claude Code, Codex, OpenCode, and Cursor**, or install them directly from a Git repository.

A small Python CLI. `uv run` installs the project's Python dependencies; they do not need global installation. No Node, background service, LLM API key, or harness SDK. The generator runs without any harness installed.

## Start here

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once, then run this from a checkout to build the working example:

```sh
uv run poe example
```

`uv run` creates the environment, installs the project and development tools, and runs the command. No separate virtualenv creation, activation, or pip install is needed. Python 3.9+ is supported (3.11+ recommended); uv can download Python if needed. The committed `uv.lock` pins dependencies; use `uv run --locked ...` to require that lockfile to be up to date.

To create and configure your own plugin:

```sh
uv run any-harness setup my-plugin --author "Your name"
uv run any-harness add agent reviewer --source my-plugin --description "Review changes for bugs"
uv run any-harness build
```

`setup` guides source creation and saves defaults in `.any-harness/config.yaml` under the current directory. It validates an existing plugin without changing its manifest. Setup does not build; the following `build` uses the saved source, harnesses, and output. Add `--no-input` to setup to use defaults without prompts. See [CLI workflows](docs/cli-workflows.md) for saved paths, explicit overrides, and config lookup.

The explicit source workflow remains available with `uv run any-harness init my-plugin --author "Your name"` followed by `uv run any-harness build my-plugin`. `init` infers the name from the folder; use `--name` to override it. `build` validates the source before generating output, so a separate `validate` command is optional. Without saved defaults or flags, it generates all four harness outputs in `dist/`.

To make the actual `any-harness` command available anywhere, run `uv tool install .` once. Subsequent examples assume this installation; when working in the checkout you can instead prefix commands with `uv run`.

Check prerequisites without starting a vendor CLI:

```sh
uv run any-harness doctor
uv run any-harness doctor --harness codex --json
```

Doctor requires Python 3.9 or newer and only inspects Python and `PATH`; it does not check vendor authentication or version compatibility. Git, uv, and unselected native CLIs are optional. An explicitly selected harness is required, so a missing selected CLI makes doctor exit 1. Doctor ignores saved config.

Typer shell completion uses no-argument flags that auto-detect the current shell:

```sh
any-harness --show-completion
any-harness --install-completion
```

`--show-completion` only prints the script; `--install-completion` edits the shell configuration. See [CLI workflows](docs/cli-workflows.md) for config lookup, explicit overrides, doctor details, and completion.

<details>
<summary>Alternative: use Python and pip without uv</summary>

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/any-harness init my-plugin
.venv/bin/any-harness build my-plugin
```

On Windows use `.venv\Scripts\python.exe` and `.venv\Scripts\any-harness.exe`. Activate the environment to use `any-harness` directly. This project has not been published to PyPI.

</details>

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

## Several plugins in one repository

Keep related plugins together when they share maintainers or a release workflow. Each plugin retains its own manifest, instructions, and native settings:

```text
multi-plugin-repo/
└── plugins/
    ├── release-notes/
    │   ├── plugin.yaml                 # name: release-notes
    │   └── skills/getting-started/SKILL.md
    └── repository-review/
        ├── plugin.yaml                 # name: repository-review
        └── skills/getting-started/SKILL.md
```

Build and check the [multi-plugin example](examples/multi-plugin-repo):

```sh
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi --check
```

`build-all [ROOT]` defaults to the current directory and `--out dist/all`. Repeat `--harness` to select targets; use `--name NAME` to override the marketplace name derived from the root folder. It ignores saved setup/config defaults.

Discovery recursively finds `plugin.yaml`, including at `ROOT` itself, and stops descending at each plugin root. It skips hidden directories, `node_modules`, `vendor`, `build`, `dist`, `__pycache__`, the selected output subtree, and directories carrying `.any-harness-build.json` or `.any-harness-build-all.json`. It never follows symlinks. Plugins are ordered by manifest name; duplicate names fail before writes. Both example plugins can use `getting-started` because their packages and project trees are isolated by plugin name.

Claude Code, Codex, and Cursor each receive one combined marketplace with packages at `plugins/<name>` and native project trees at `projects/<name>/...`. OpenCode receives only isolated native project trees. Build-all records ownership in `.any-harness-build-all.json`, distinct from the single-plugin `.any-harness-build.json`, so `build SOURCE` cannot overwrite a collection. It rejects unowned or mismatched output, removes stale generated files on rebuild, and compares paths, bytes, executable modes, and directories with `--check` without writing. See [multi-plugin distribution](docs/distribution.md#multi-plugin-repositories) for the output layout and release workflow.

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

For a single-plugin `build SOURCE`, each `<out>/<harness>/` is independent (`--out` defaults to `dist` without saved config):

- **Claude Code:** `.claude-plugin/marketplace.json`, a native plugin under `plugins/<name>`, and `.claude` project files under `project/`.
- **Codex:** `.agents/plugins/marketplace.json`, a `.codex-plugin` skills package, and `.agents/skills` plus native `.codex/agents/*.toml` under `project/`. The documented plugin package does not install custom agents; use the direct installer for both.
- **Cursor:** `.cursor-plugin/marketplace.json`, a native plugin containing skills and agents, and `.cursor` project files.
- **OpenCode:** `.opencode/skills` and `.opencode/agents` under `project/`. Targets the stable V1 file format. No executable JavaScript plugin is synthesized; V2 beta's changed plugin API is outside this tool's scope.

Every output includes `INSTALL.md` and `compatibility.json`. Choose one installation method per plugin/harness; compatibility discovery across harness directories can otherwise produce duplicates. Explicit native overrides and support files stay inside the source folder. Hooks, MCP servers, model aliases, tool permissions, and executable plugin APIs are **not automatically translated**.

## Documentation and checks

- [CLI workflows and saved defaults](docs/cli-workflows.md)
- [Validation and current evidence](docs/validation.md)
- [Source format and native escape hatches](docs/source-format.md)
- [Model and reasoning settings](docs/model-settings.md)
- [Native harness evaluations](docs/evals.md)
- [Git distribution, installation, and maintenance](docs/distribution.md)
- Research: [Claude Code](docs/research/claude-code.md), [Codex](docs/research/codex.md), [OpenCode](docs/research/opencode.md), [Cursor](docs/research/cursor.md)

```sh
uv run poe check          # Tests, both examples, and bundled plugin checks
uv run poe test           # Only unit and integration tests
uv run poe example        # Only single-plugin example generation/eval preview
uv run poe multi-example  # Build and check the combined multi-plugin example
uv run poe                # List available tasks
```

[Poe the Poet](https://poethepoet.natn.io/) supplies cross-platform task shortcuts as a development dependency. End-user installs use the project's runtime dependencies; Poe is needed only for checkout tasks. CI runs the same `poe check` task and separately checks the packaged wheel.

Tests exercise generation, YAML validation, preservation of resources, installation ownership, update/uninstall, and failure paths. See [validation and current evidence](docs/validation.md) for the packaged-wheel check, the HTTPS Git test, the local Codex manifest/discovery checks, and the native host behavior that remains unverified. Official-source research was checked September 5, 2026; native escape hatches are passed through, not validated against every vendor's evolving configuration schema.

Inspired by the single-source approach in [wshobson/agents](https://github.com/wshobson/agents). This implementation focuses on a reusable generator and installer rather than a large content catalog.
