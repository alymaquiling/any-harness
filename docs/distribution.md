# Distribution and installation

## Lowest-maintenance Git workflow

Keep `plugin.yaml`, `skills/`, `agents/`, and optional `native/` in Git. The direct installer fetches that source and generates files locally; you do not need to commit four generated trees. It clones into a temporary directory, resolves a ref to a commit, reads the selected source, generates only the requested harness, installs native files, and removes the temporary checkout. It does not initialize submodules or run plugin build scripts. Git and its configured authentication must be available for remote sources.

Install this tool once from its eventual repository (replace `OWNER`):

```sh
uv tool install git+https://github.com/OWNER/any-harness.git
```

Or use an isolated Python environment and `python -m pip install git+https://github.com/OWNER/any-harness.git`. Only the tool installation executes Python packaging; a plugin source needs no Python package or install hooks.

Then:

```sh
any-harness install git+https://github.com/TEAM/PLUGIN.git --ref v1.0.0 --harness opencode --global
any-harness install git+ssh://git@github.com/TEAM/PRIVATE.git --subdir plugins/review-kit --ref v1.0.0 --harness codex --project .
```

These are templates, not published package addresses. A full commit hash is the strongest pin. Branches and tags are resolved at each invocation; repeating an unpinned command gets the current remote default branch. Repeating `install` updates existing owned files and removes stale ones. There is no background updater.

## Scope and receipts

Project installs place files under `.claude`, `.cursor`, `.opencode`, or `.agents` and `.codex` inside `--project DIR`. Global installs use your home directory for Claude Code, Cursor, and Codex. OpenCode uses `$XDG_CONFIG_HOME/opencode` or `~/.config/opencode`. Custom harness environment overrides such as a nonstandard Codex home are not inferred; use a project directory containing the desired native layout or copy generated files yourself.

The installer records relative file paths, hashes, executable modes, and Git commit/source provenance under `.any-harness/installed/` within its destination root. Keep these receipts for managed updates and uninstall. They do not include file contents. It uses a lock to prevent overlapping managed installations, preflights all conflicts before writing, and rolls back completed file operations on ordinary filesystem errors. A process/power crash is not a fully transactional filesystem guarantee; restore from Git/backup if needed. Empty directories may remain after uninstall.

`--dry-run` previews file changes without writing to the destination (remote sources still need a temporary clone). A modified installed file blocks update/uninstall: move the local edit aside or restore it first. Unmanaged collisions are never adopted even when their bytes match. There is no broad `--force` flag. Source/destination roots are normalized to physical paths to support OS aliases such as macOS `/var`; a root that is itself a symlink, or symlinks inside the installation tree, are refused. Build output additionally rejects symlinked parents. Native hooks copied into discovery folders may run when you subsequently launch the harness, so review plugin sources you install as you would other developer tools.

If you use multiple harnesses in one project, their compatibility discovery may find skills in another harness's directory. Install only the targets you need, and watch for duplicate skill names in the harness UI. We use native directories so explicit per-harness settings stay intact.

## Native marketplace releases

`any-harness build plugin-source --out dist` produces an independent root for each selected harness, defaulting to all four without saved config or `--harness` options. `dist/claude-code`, `dist/codex`, and `dist/cursor` can each be the root of a release repository or dedicated release branch. They contain their own native marketplace metadata pointing at `plugins/<name>`. **Do not point a marketplace at the multi-harness `dist/` parent**: that parent has no root marketplace manifest. A local checkout can register the specific generated subdirectory where the harness supports local sources.

For Claude Code, add the release repository with `/plugin marketplace add TEAM/RELEASE-REPO`, then `/plugin install NAME@NAME`. For local testing, run `claude --plugin-dir /absolute/path/to/dist/claude-code/plugins/NAME`. The plugin manifest and marketplace use the plugin name as their identifier. See [Claude's marketplace docs](https://code.claude.com/docs/en/plugin-marketplaces).

For Codex, register the specific local marketplace root with `codex plugin marketplace add /absolute/path/to/dist/codex`, then run `codex plugin add NAME@NAME`. A generated root published as a Git repository can be registered with `codex plugin marketplace add TEAM/RELEASE-REPO --ref v1.0.0`. These command forms were checked against the locally available Codex CLI 0.153.4 help. Workspace GitHub plugin import is a separate administrative flow. The custom TOML agents in `project/.codex/agents` are outside the documented plugin package: use the direct installer when you need both skills and agents. See [Build plugins](https://learn.chatgpt.com/docs/build-plugins) and [custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents).

For Cursor, publish a generated Cursor root and follow the public submission or team GitHub marketplace import workflow. For local plugin testing, copy `dist/cursor/plugins/NAME` to `~/.cursor/plugins/local/NAME` and reload. An arbitrary Git repository is not automatically a reviewed public marketplace plugin. See [Cursor plugins](https://cursor.com/docs/plugins).

OpenCode's native executable plugin system is JavaScript/TypeScript; a skills/agents folder is not an npm runtime plugin. The direct installer uses the stable V1 native file format. OpenCode V2 beta has a distinct runtime plugin API; this tool does not generate that API. See [OpenCode skills](https://opencode.ai/docs/skills) and [agents](https://opencode.ai/docs/agents).

Native marketplace users do not need this CLI. Publishers need to generate release roots and, for public marketplaces, satisfy each vendor's submission requirements. Each release includes `INSTALL.md` describing the actual supported path.

## Multi-plugin repositories

A repository can hold several plugins that share maintainers, documentation, or a release schedule. Keep a separate `plugin.yaml` and component folders for each plugin so users can install only the plugin they need. The [multi-plugin fixture](../examples/multi-plugin-repo) uses this layout:

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

From the any-harness checkout:

```sh
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi --check
```

The repository shortcut `uv run poe multi-example` runs these two commands in sequence, and `uv run poe check` includes that task.

The command is `any-harness build-all [ROOT]`. `ROOT` defaults to the invocation directory, `--out` defaults to `dist/all`, and all four harnesses are selected unless `--harness` is supplied. Repeat `--harness` for multiple targets. `--name NAME` overrides the marketplace name derived from the root folder; use the same name, output, and target selection for subsequent builds and checks. Build-all ignores `.any-harness/config.yaml`.

### Discovery and ordering

Discovery recursively searches for `plugin.yaml` in deterministic directory order. Finding a manifest makes that directory a plugin root and ends recursion beneath it, so a plugin's support files do not become additional repository plugins. A source root containing `plugin.yaml` therefore selects that plugin alone.

Discovery skips hidden directories, `node_modules`, `vendor`, `build`, `dist`, `__pycache__`, the selected output subtree, and directories carrying `.any-harness-build.json` or `.any-harness-build-all.json`. It never follows symlinks. The `plugins/` layout is a convention, not a required depth: other nested source folders are discovered too. The discovered plugins are rendered in manifest-name order. Manifest `name` values must be unique across the selected repository; duplicates fail before output is written, even if their folders differ.

### Combined output

Claude Code, Codex, and Cursor each get one marketplace that lists every discovered plugin, plus one native package and one isolated native project tree per plugin. OpenCode gets the isolated native project trees only. With `<name>` standing for each plugin's manifest name, the layout is:

```text
dist/multi/
├── .any-harness-build-all.json
├── claude-code/
│   ├── .claude-plugin/marketplace.json
│   ├── plugins/<name>/...
│   └── projects/<name>/.claude/...
├── codex/
│   ├── .agents/plugins/marketplace.json
│   ├── plugins/<name>/...
│   └── projects/<name>/
│       ├── .agents/skills/...
│       └── .codex/agents/...
├── cursor/
│   ├── .cursor-plugin/marketplace.json
│   ├── plugins/<name>/...
│   └── projects/<name>/.cursor/...
└── opencode/
    └── projects/<name>/.opencode/...
```

Component folders appear when the plugin supplies the corresponding content; this fixture supplies skills. Each harness root also contains `INSTALL.md` and `compatibility.json`. For example, the two Cursor skills live under `projects/release-notes/.cursor/skills/getting-started/` and `projects/repository-review/.cursor/skills/getting-started/`. Their matching skill basenames do not collide during generation. Installing both plugins directly into the same project's native skill directory still follows the installer's existing collision rules.

Publish or register the specific `dist/multi/claude-code`, `dist/multi/codex`, or `dist/multi/cursor` root using the native release workflows above. The `dist/multi` parent has no marketplace manifest. Combined metadata and generated READMEs use the combined marketplace name, and marketplace `owner.name` is that name. For Claude Code and Codex, the plugin selector is `PLUGIN@MARKETPLACE`, where `MARKETPLACE` is the derived name or `--name` value; the example uses `repository-review@multi-plugin-repo`. Follow the generated `INSTALL.md` for each harness.

Native limitations are unchanged: the Codex marketplace package distributes skills, while custom TOML agents are in `projects/<name>/.codex/agents` and require project-file installation or the direct installer. Cursor's submission and marketplace-import requirements still apply. OpenCode output uses stable V1 skills/agents files and provides neither a marketplace nor a generated executable JavaScript/TypeScript plugin. Native settings retain each harness's meaning; combining plugins does not translate hooks, MCP servers, permissions, or executable plugin APIs.

### Build ownership and updates

Build-all owns one output root containing the `<harness>/` subtrees. Its `.any-harness-build-all.json` marker contains `schema: 1`, `type: multi-plugin`, the marketplace `name`, sorted `plugins`, and sorted `harnesses`. Single-plugin `build SOURCE` uses `.any-harness-build.json`; the distinct markers prevent a single-plugin build from overwriting a multi-plugin collection. An existing output directory must have a valid matching build-all marker: unowned directories, single-plugin output, malformed markers, and output for a different marketplace name are rejected. Choose a fresh output path when switching build types or marketplace names.

Rebuilding an owned output replaces the generated tree and removes stale files and directories, including output for removed plugins or deselected harnesses. Keep hand-maintained content outside this tree. `--check` renders the expected result and compares file paths, bytes, executable modes, and directories without writing; missing or extra content and directory drift cause failure. Use the same command arguments as the build when checking committed output.

Existing single-plugin workflows remain available. Build one source with `any-harness build examples/multi-plugin-repo/plugins/repository-review --out dist/repository-review`, install a local source folder, or select it from Git using `--subdir plugins/repository-review`. `build-all` generates release output; installation continues to select one source plugin at a time.

## CI and release checks

For a single-plugin source-only repository, run `any-harness validate .` in CI. If you commit generated output, run `any-harness build . --out generated --check`; remove `generated/` from `.gitignore` and commit the first build. Pin the tool ref/version and Python dependencies for reproducibility. See [CLI workflows](cli-workflows.md) for saved defaults and explicit overrides.

For a multi-plugin repository, use `any-harness build-all . --out generated` for the first build and `any-harness build-all . --out generated --check` in CI. Preserve the ownership marker with the generated output and repeat any `--harness` or `--name` options used to generate it.

This tool's own CI workflow is configured to run tests on Python 3.9, 3.11 and 3.13, validate both example and authoring source, build the authoring package, and build/install a wheel into a temporary environment before running the packaged smoke check. Its Git end-to-end test uses a temporary loopback HTTPS server; CI does not contact a public Git service or invoke a vendor host. See [validation and current evidence](validation.md) for the exact commands, the local Codex checks, and the limits of the evidence. Publishing to Git, package registries, or vendor marketplaces remains a publisher action. No release credentials or remote repositories are configured here.
