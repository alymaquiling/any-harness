# CLI workflows

## Run from a checkout with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then use it to run the CLI and the project's Poe tasks:

```sh
uv run poe example
uv run poe multi-example
uv run any-harness --help
uv run poe
```

`uv run` creates the project environment and installs the project dependencies and development tools. These dependencies do not need global installation or a separate environment activation step. The committed `uv.lock` pins dependencies; `uv run --locked ...` requires it to be up to date. Python 3.9+ is supported, with 3.11+ recommended. `uv tool install .` is available when you want to run `any-harness` outside this checkout.

## Check prerequisites

Use `doctor` for a read-only prerequisite check:

```sh
uv run any-harness doctor
uv run any-harness doctor --harness codex --json
```

Python 3.9 or newer is required. Doctor only inspects the Python runtime and executable names on `PATH`; it never launches a vendor CLI or makes a network call, so vendor authentication and version compatibility are not checked. Git, uv, and native CLIs for unselected harnesses are optional. Pass `--harness` to require a native CLI for a selected harness; if that CLI is missing, doctor exits with status 1. Doctor ignores saved `.any-harness/config.yaml` values.

## Enable shell completion

Install the tool globally when you want the actual `any-harness` executable available to shell completion:

```sh
uv tool install .
any-harness --show-completion
any-harness --install-completion
```

Typer's `--show-completion` and `--install-completion` are no-argument flags that auto-detect the current shell. `--show-completion` only prints the completion script. `--install-completion` edits the shell configuration to install it. Pass no shell argument to either flag.

Poe supplies shortcuts for repository maintenance. Use `uv run poe test` for the test suite, `uv run poe multi-example` to build and check the combined plugin fixture, and `uv run poe check` for the repository's combined checks. See [validation](validation.md) for what those commands are intended to check and the limits of native-host evidence.

## Set up a plugin and save defaults

Run setup from the directory where you want the config to live:

```sh
uv run any-harness setup my-plugin --author "Your name"
uv run any-harness build
```

`any-harness setup [PATH]` uses the invocation's current working directory as the config root. It creates a new plugin source or validates an existing one, then writes `<cwd>/.any-harness/config.yaml`. `--name` and `--author` affect only a newly scaffolded source. Setup validates an existing plugin before writing config and leaves that plugin's manifest unchanged. Setup does not automatically build; run `build` afterward to use the saved defaults.

For setup without prompts:

```sh
uv run any-harness setup my-plugin --no-input
uv run any-harness build
```

With `--no-input` and no overrides, setup uses the source folder name, the default author, all four harnesses, project `.`, and output `dist`. `--project` and `--out` set different saved defaults, and repeated `--harness` options set the saved harness list:

```sh
uv run any-harness setup my-plugin --no-input --harness codex --harness cursor --project ../my-app --out generated
uv run any-harness build
```

The config uses schema 1 and these exact fields. For `setup my-plugin --no-input` run from the repository root, its values are:

```yaml
schema: 1
source: my-plugin
harnesses:
  - claude-code
  - codex
  - opencode
  - cursor
project: .
out: dist
```

`source` identifies one plugin source. `harnesses` is a nonempty list drawn from `claude-code`, `codex`, `opencode`, and `cursor`. `project` is the native installation destination; `out` is the generated build destination. Paths are saved relative to the config root when possible and resolve from that root when the config is read.

## Lookup and explicit options

Config lookup applies to `add`, `validate`, `build`, `install`, `uninstall`, `authoring`, and the applicable `eval` source and harness options. Each command reads only the fields it supports: source for `add`, `validate`, and eval source; source, harness, and output for `build`; source, harness, and project for `install`; harness and project for `uninstall` and `authoring`; and source or harness for the applicable eval subcommands. Lookup starts at the invocation directory and walks upward. It stops at a `.git` directory or a `plugin.yaml`, so a nested plugin does not inherit a parent repository's config.

For example, running `setup my-plugin` from a repository root saves config at the repository root. Run the following `build` there to use those defaults. A later command invoked inside `my-plugin` reaches its own `plugin.yaml` boundary and does not inherit that parent config.

Explicit CLI arguments and flags win over saved values. An explicit `--global` selects the harness's native global destination. `--no-config` bypasses config lookup entirely. Put this root option before the subcommand when you want to use only explicit values:

```sh
any-harness --no-config build SOURCE --harness codex --out dist/codex
```

Saved `project` and `out` paths retain their meaning when you invoke a command from a different directory within the same config lookup scope.

The existing explicit source commands remain available:

```sh
uv run any-harness init another-plugin --author "Your name"
uv run any-harness add agent reviewer --source another-plugin --description "Review changes for bugs"
uv run any-harness build another-plugin --out dist/another-plugin
uv run any-harness install another-plugin --harness codex --project ../my-app
uv run any-harness uninstall another-plugin --harness codex --project ../my-app
```

`init` scaffolds source without saving setup defaults. Install and uninstall retain their ownership, collision, and local-edit protections; see [distribution and installation](distribution.md).

## Build a repository of plugins

`build-all` discovers a collection of plugin sources and ignores saved config. Its command is `any-harness build-all [ROOT]`, with `ROOT` defaulting to the current directory and `--out` defaulting to `dist/all`. Repeat `--harness` to choose targets and use `--name NAME` to override the derived marketplace name.

```sh
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi --check
```

The second command compares the generated output without writing. See [multi-plugin repositories](distribution.md#multi-plugin-repositories) for discovery exclusions, duplicate names, isolated native project trees, and build ownership.

## Keep eval configuration separate

Eval retains its existing `--config isolated|inherit` option for native harness configuration. That option is separate from `.any-harness/config.yaml`. When neither an explicit `--harness` nor saved config supplies harnesses, `eval preview` and `eval run` retain the suite's harness selection. `eval run --out DIR` remains required and is never filled from saved setup defaults. See [native harness evaluations](evals.md) for the eval command workflow.
