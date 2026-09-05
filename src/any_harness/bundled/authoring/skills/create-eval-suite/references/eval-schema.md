# Eval suite reference

Keep `evals/cases.yaml` beside the plugin source. It starts with `schema: 1`
and a `cases` list. A case has `id`, `fixture` (relative to the suite),
`prompt`, `component.kind` (`skill`, `agent`, or `plugin`), optional
`component.name`, `mode` (`automatic` or `explicit`), `invocation` (`required`,
`optional`, or `forbidden`), and `assertions`.

The first deterministic assertion types are:

- `output_contains` and `output_not_contains` with `value`;
- `file_exists`, `file_contains`, and `file_not_exists` with `path`;
- `unchanged` with a fixture-relative `path`; and
- `check` for an executable suite-local grader script under `evals/`. The
  runner invokes it with the staged workspace as its current directory and
  sets `ANY_HARNESS_EVAL_WORKSPACE`; optional `args` and a per-check timeout
  may be supplied. It is grader data and is never sent to the harness.

`human_review: true` requests a separate human decision. A future model
grader must remain separate from the evaluated prompt and deterministic
assertions. Variants can select a sibling or revision with `source`, omit
components with `exclude: ["skill:name", "agent:name"]`, and pass native
`settings.<harness>` such as that harness's model, effort, or variant. Keep
all other conditions fixed during comparisons.

Use the native automation interface for each harness. Stable OpenCode V1 uses
`opencode run --format json`; Cursor uses
`cursor-agent --print --output-format stream-json`; Codex uses `codex exec`
JSON events; Claude Code uses its documented print mode. Do not replace a
missing native harness with a direct model API call. If the harness does not
expose component-selection telemetry, invocation is `unknown` even when the
answer is good. A native discovery/list response proves loading only. A
prohibited action/tool assertion fails when the trace observes that call; an
incomplete trace with no observed call is inconclusive.

Isolate configuration and fixture workspaces by default. Record the selected
configuration scope, executable version, requested and observed model/effort,
permissions, native command, event/transcript paths, duration, exit status,
and any token/cost values that the harness actually reports. Preserve
`skipped`, `inconclusive`, `timeout`, and runner-error states distinctly.
