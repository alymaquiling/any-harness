---
name: add-client-harness
description: Add a new agent client harness to any-harness after independent official research, preserving existing adapters and verifying generated native output. Use when extending HARNESSES or supporting a new harness.
---

# Add a client harness

Extend the any-harness tool so one portable source plugin can target a new
agent client. Treat the harness's published format and installation behavior as
the contract; do not infer them from a similar client. Keep the existing
adapters working and report any capability the new client cannot represent.

## Inspect before changing anything

Confirm that the requested work is in the any-harness checkout before editing:

```sh
pwd
find .. -name AGENTS.md -print
rg --files src/any_harness docs pyproject.toml
git status --short
```

Read applicable `AGENTS.md` files and the current `model.py`, `build.py`,
`install.py`, `cli.py`, README, research notes, and tests. If this is not the
any-harness repository or the requested checkout/path is unclear, ask for the
exact path before making changes.

## Research in two independent passes

Before implementation, delegate two separate research tasks when the current
agent environment provides subagents:

1. **Format/schema researcher:** use the harness's official documentation,
   schema repositories, and maintained examples to record manifest names,
   frontmatter fields, required directories, discovery rules, and unsupported
   features. Capture exact examples and URLs.
2. **Installation researcher:** independently check the official CLI/editor
   installation flow, project and user/global directories, marketplace or Git
   distribution, update behavior, scopes, and any authentication or reload
   steps. Capture exact commands and URLs.

Give each researcher the harness name and repository context, but do not give
one researcher's conclusions to the other. Wait for both results before
choosing the adapter shape. Reconcile disagreements against primary sources and
save the result in `docs/research/<harness>.md`, clearly separating documented
facts, observed behavior, and inference.

If delegation is unavailable, perform two separate official-source research
passes yourself and say so in the final report. Never claim that subagents were
spawned unless delegation returned their results. If the target harness itself
does not support custom subagents, document that limitation and use its closest
native equivalent, such as a skill, command, or static prompt; do not claim
that generated files provide subagent behavior.

## Implement the smallest native adapter

Use the findings and read [the implementation checklist](references/implementation-checklist.md)
while changing the tool.

1. Add the stable lowercase harness identifier to `HARNESSES` in
   `src/any_harness/model.py`. Update every validation set and dispatch map that
   uses the tuple. Keep the portable source schema unchanged unless the new
   harness requires a capability that cannot be expressed by existing native
   escape hatches.
2. Extend `build.render` with the researched project directory, plugin/marker
   layout, manifest shape, component discovery rules, and compatibility notes.
   If the client has no plugin package, generate only the native project tree
   and explain that fact in `INSTALL.md` and `compatibility.json`.
3. Extend `install.native_files` and the global/project path handling using the
   documented locations. Preserve receipts, symlink checks, collision checks,
   dry-run behavior, and refusal to overwrite unmanaged files. Do not create a
   global install path from guesswork or from the current machine's preference.
4. Update CLI choices, help text, and any source/repository options needed by
   the researched installation flow. Keep the existing command contract and
   error behavior for Claude Code, Codex, OpenCode, and Cursor.
5. Update README and research/install documentation with exact current
   commands, path examples, scopes, version caveats, and a clear unavailable
   feature note. Do not document flags that are not implemented or verified.
6. Add meaningful tests for model validation, rendered paths and frontmatter,
   project/global installation decisions, and the new CLI route. Preserve
   existing adapter tests and compare their expected output when practical.
7. Add an eval adapter for the harness when it supports a native CLI or
   automation protocol. Record the exact command, structured output or
   protocol events, model/effort flags, permission behavior, configuration
   isolation, timeout handling, and what invocation evidence is observable.
   If the harness has no supported native path, mark behavioral evals
   unsupported; do not substitute a direct model API call.

Do not copy an entire vendor manual into the source tree. Keep shared
`name`/`description` frontmatter portable and put target-specific files under
`native/<harness>/plugin/` or `native/<harness>/project/` only when the
researched behavior truly needs them.

## Verify the result

Run the repository's normal test and validation commands. Exercise
`load(...)`/`generate(...)` for the new harness and inspect the complete output
tree, including manifest, skill, agent, project, plugin, and install files. Run
the CLI against a temporary source plugin as well as the existing fixtures.

If the native client is installed on the host, run a harmless version/help or
load smoke test and inspect the generated files through that client. If it is
not installed, check for its executable without installing anything and report
native runtime verification as unavailable. Never imply that a file-level test
proves a harness loaded the output.

## Add evaluation support

Read the bundled `create-eval-suite` skill's schema reference and the official
research record before writing cases. Add at least one local fixture and one
case for each supported component type, then cover automatic selection,
explicit invocation, deterministic outcome checks, and a prohibited-change
constraint. Keep expected findings in `evals/cases.yaml`, outside the prompt.

Use a fresh fixture per repeat and bounded jobs in local tests. Add a preview
check to CI; run live behavior only when the harness is installed and
authenticated. Report skipped, timeout, runner error, and inconclusive states
distinctly. Update the harness capability notes and the packaged authoring
smoke check when the bundled skill set grows.

After implementation, run one bounded independent forward test in a temporary
directory. Use the `create-portable-agent` skill for a realistic request (for
example, an agent that audits an OAuth callback change), create it with the
current CLI, replace the placeholder body with the focused workflow, then run
`validate` and `build` for the resulting source plugin. Keep the temporary
source and output outside the repository; report the commands and results and
remove only the exact temporary artifacts if cleanup is safe.

The final report must state: which two research passes ran and their sources,
files changed, compatibility limitations, tests and native smoke checks that
passed, and checks that were unavailable.
