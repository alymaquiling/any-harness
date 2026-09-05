# Multi-plugin repository example

Keep related plugins in one repository when they share maintainers or a
release workflow but serve different purposes. This fixture contains
`release-notes` and `repository-review`, each with its own manifest and
instructions:

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

Both use a skill named `getting-started`; their distinct plugin names keep
the generated packages and native project trees isolated. The review plugin
is named `repository-review` so it also stays distinct from the separate
[`examples/review-kit`](../review-kit) example when discovering this whole
repository.

From the any-harness checkout, build and then check the combined output for
every supported harness:

```sh
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi
uv run any-harness build-all examples/multi-plugin-repo --out dist/multi --check
```

`build-all [ROOT]` defaults to the current directory and `--out dist/all`.
Repeat `--harness` to select targets; `--name NAME` overrides the marketplace
name derived from the source root. The commands above use the default name
`multi-plugin-repo`.

Discovery recursively finds `plugin.yaml`, including at the requested root,
stops at each plugin root, and orders plugins by manifest name. It skips hidden
directories, `node_modules`, `vendor`, `build`, `dist`, `__pycache__`, the selected
output subtree, and directories carrying `.any-harness-build.json` or
`.any-harness-build-all.json`. It never follows symlinks. Duplicate manifest
names fail before output is written.

The generated Claude Code, Codex, and Cursor roots each contain one
marketplace with packages at `plugins/release-notes` and
`plugins/repository-review`. Their native project trees are separate at
`projects/release-notes/` and `projects/repository-review/`. OpenCode gets
only `projects/release-notes/.opencode/` and
`projects/repository-review/.opencode/`; this tool generates no OpenCode
marketplace. Combined metadata and READMEs use `multi-plugin-repo`, also the
marketplace `owner.name`.

Build-all output uses `.any-harness-build-all.json`, distinct from the
single-plugin `.any-harness-build.json`, so a single-plugin build cannot
overwrite the collection. The marker contains `schema: 1`, `type: multi-plugin`,
the marketplace `name`, sorted `plugins`, and sorted `harnesses`. It owns one
output root containing the harness subtrees. Rebuilding removes stale generated files; existing
unowned output or output owned by a different build kind or marketplace name
is rejected.
`--check` compares file paths, bytes, executable modes, and directories
without writing. Keep custom files outside the owned output tree.

You can still build or install either plugin on its own, for example with
`uv run any-harness build examples/multi-plugin-repo/plugins/repository-review`.
See [distribution and installation](../../docs/distribution.md#multi-plugin-repositories)
for output layouts, release use, and unchanged native harness limitations.
