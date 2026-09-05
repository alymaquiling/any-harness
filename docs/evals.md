# Native evaluations

An eval suite lives beside the source plugin. The suite is grader data, so the
evaluated prompt never contains the expected answer or the checks that judge
it:

```text
examples/review-kit/
├── plugin.yaml
├── skills/ and agents/
└── evals/
    ├── cases.yaml
    └── fixtures/<case-id>/...
```

Validate a suite before running it:

```sh
any-harness eval validate examples/review-kit
any-harness eval preview examples/review-kit --harness codex
```

`preview` resolves cases, variants, fixture hashes, native commands, and
configuration scope without starting a model turn. It is safe to use in CI.
Run a bounded native evaluation only after reviewing the preview:

```sh
any-harness eval run examples/review-kit \
  --harness codex \
  --case agent-finds-regression \
  --variant with-reviewer \
  --repeat 2 \
  --jobs 1 \
  --timeout 120 \
  --config isolated \
  --out ../eval-results/review-kit
any-harness eval report ../eval-results/review-kit/result.json
```

Use `--harness` and `--case` more than once to select a subset. `--repeat`
creates a fresh fixture for every repetition, `--jobs` caps concurrency, and
`--timeout` bounds each native process. The default isolated configuration
uses temporary harness configuration roots to reduce accidental use of user
skills, agents, plugins, and permissions. It is workspace and configuration
isolation, not an operating-system sandbox: host policies, managed settings,
network access, and inherited credentials can still apply. `--config inherit`
is an explicit exception for a local authenticated smoke test; the result
records that it inherited user configuration and credentials. The runner
never installs into a user's global plugin directory as part of an eval.

Isolated plugin cases evaluate the project-native files staged into the fresh
workspace. They do not install a marketplace package, populate a global plugin
cache, or exercise a package's MCP, hook, command, or JavaScript extension
installation path. Use the generator's native-output and manifest checks for
those package artifacts, and label a project-native plugin workflow separately
from package installation behavior.

The runner does not copy a user's login into an isolated configuration root.
Use a harness-supported credential environment or an explicit `--config
inherit` smoke run when authentication is required; never add credentials to a
fixture or silently change global configuration. On this host, Claude Code,
OpenCode, and Cursor were unavailable. Install and authenticate them using
their official [Claude Code quickstart](https://code.claude.com/docs/en/quickstart),
[OpenCode installation guide](https://opencode.ai/docs), or [Cursor CLI
installation guide](https://docs.cursor.com/en/cli/installation) before a live
run.

## Case shape

`cases.yaml` has `schema: 1`, a `cases` list, and an optional `variants` list.
Each case has an ID, a fixture path relative to the suite directory, a prompt,
a component, a mode, an invocation policy, and deterministic assertions:

```yaml
schema: 1
cases:
  - id: agent-finds-regression
    fixture: fixtures/review-bug
    prompt: Review the change for correctness and missing tests. Do not edit files.
    component:
      kind: agent
      name: change-reviewer
    mode: explicit
    invocation: required
    assertions:
      - type: output_contains
        value: zero
    constraints:
      - type: unchanged
        path: src/parse.py
      - type: file_not_exists
        path: REVIEW.md
variants:
  - id: without-reviewer
    exclude: [agent:change-reviewer]
```

`component.kind` is `skill`, `agent`, or `plugin`. `mode: automatic` asks the
harness to select a discovered component from the user request; `mode:
explicit` names the component in the prompt or the harness-native invocation
channel. `invocation` can be `required`, `optional`, or `forbidden`. A required
invocation becomes `inconclusive` when native evidence is unavailable and
fails when evidence proves it was not used. Missing evidence is reported as
`unknown` where the harness does not expose selection telemetry. A good final
answer is not proof that a skill or agent ran.

The initial assertion types are `output_contains`, `output_not_contains`,
`file_exists`, `file_contains`, `file_not_exists`, `unchanged`, `check`,
`action_forbidden`, and `tool_not_used`. Text assertions use `text` (the
runner also accepts `value` as a convenience); file assertions use `path`.
An executable `check` uses `script`, optional `args`, and an optional timeout.
The script is grader data under `evals/`, is validated before any native run,
must have its executable bit set, and runs with the staged workspace as its
current directory plus `ANY_HARNESS_EVAL_WORKSPACE`. `action_forbidden` and
`tool_not_used` fail when the prohibited action or tool is observed. If it is
absent but the adapter cannot expose a complete native trace, the result is
inconclusive rather than a pass.
Keep expected findings and prohibited paths in assertions or fixtures, never in
the evaluated agent's input. Human review can be requested with
`human_review: true`; the runner records a pending review request and never
fabricates a decision. A model grader is an extension point: it should read
`result.json` and write a separate, versioned review artifact (for example,
`review/model-grader-v1.json`) without mutating deterministic assertions.

Variants hold conditions constant while changing one factor. Use `source` for
a checked-in plugin revision or sibling source folder and `exclude` for
with/without component comparisons (`skill:<name>` or `agent:<name>`). Native
model and effort settings live under `settings.<harness>` and are passed to the
harness in its own syntax. Omit them to inherit the harness default; never
pretend that a Codex effort, OpenCode variant, Cursor parameter, or Claude
effort has the same meaning on another provider.

For example, this variant asks Codex for a specific native model and reasoning
setting while leaving the fixture, prompt, permissions, and timeout unchanged:

```yaml
variants:
  - id: codex-high-review
    settings:
      codex:
        model: gpt-5.5
        effort: high
```

Use a model ID and effort value supported by the installed Codex release; the
example is a native setting shape, not a cross-harness model mapping. A simple
external grader can consume the deterministic result without entering the
evaluated prompt:

```sh
examples/review-kit/tools/grade-result.py \
  ../eval-results/review-kit/result.json \
  --output ../eval-results/review-kit/review/model-grader-v1.json
```

Keep that grader outside the fixture and record its version and output as a
separate review artifact. It must not edit `result.json` or turn unavailable
native evidence into a pass.

## Result evidence

Each run writes machine-readable JSON (`result.json`) and a concise Markdown report with
content hashes for the source, suite, cases, and fixture; harness version;
requested and observed model/effort; relevant configuration scope; native
command; exit status; assertion outcomes; transcript and artifact paths;
duration; and token/cost fields when the harness reports them (otherwise they
remain `null`). Results distinguish `pass`, `failure`, `runner_error`,
`timeout`, `skipped`, and `inconclusive`; unsupported capabilities are visible
as skipped or inconclusive instead of passing silently.

The CLI uses stable CI exit codes: `0` means every selected run passed, `1`
means a behavioral assertion or observed prohibited action failed, `2` means a
runner error, timeout, or invalid suite/result, and `3` means at least one run
was skipped or inconclusive. A nonzero code must be reported alongside the
per-run status rather than collapsed into a generic failure.

When `--repeat N` is greater than one, `result.json` groups repetitions by
case, harness, and variant under `variability`. Each group records whether
status, invocation/activation evidence, observed model/effort, and assertion
outcomes were stable; it does not invent a mean quality or cost score.

The artifact directory contains the native stdout/stderr or event stream,
the isolated workspace snapshot, and any files produced by the evaluated
harness. Inspect it before deleting the exact output directory. It is safe to
rerun the same case because each attempt receives a new temporary workspace
and a fresh native session. Timeout cleanup uses process groups on POSIX hosts;
the Windows fallback can terminate only the immediate native process, so
descendant cleanup and POSIX executable grader scripts require separate
Windows validation.

## Harness differences

The runner invokes each installed harness through its native automation path:

Native settings remain harness-specific. Claude accepts `model`, `effort`, and
`permission_mode`; Codex accepts `model`, `effort` (as its native
`model_reasoning_effort` config override), and `sandbox` values such as
`read-only` or `workspace-write`; OpenCode accepts `model`, `variant`, and a
boolean `thinking`; Cursor currently accepts `model` in the eval schema. The
runner does not turn Cursor's `--force` into a permission setting, and it does
not map a reasoning value from one provider to another. Unsupported settings
remain visible in preview/results.

- Claude Code uses its documented non-interactive print mode and preserves
  native permission/model flags. Component-selection evidence depends on the
  output and hooks the installed CLI exposes.
- Codex uses `codex exec` with its native JSON event stream and sandbox/model/
  reasoning controls. Codex's app-server can perform read-only discovery
  checks, but those checks are separate from behavioral evals.
- OpenCode targets stable V1 `opencode run`. The documented flags include
  `--model`, `--agent`, `--format json`, `--variant`, `--thinking`, and
  `--dir`; `--format json` is a raw event stream. OpenCode 2 is a separate
  beta binary and plugin API, so it is not treated as equivalent.
- Cursor uses `cursor-agent --print --output-format stream-json` for native
  headless runs. `--model` remains a native selection control. Cursor's
  documented `--force` changes command approval behavior and is never added by
  the runner implicitly; use explicit permission settings when an eval needs
  writes. Cursor's stream events can provide tool evidence, while its CLI docs do not promise a
  direct `--skill` selector; skill or agent invocation is unknown unless the
  stream exposes it. Cursor project CLI configuration controls permissions,
  while other CLI settings are global; isolated `CURSOR_CONFIG_DIR` runs may
  not have the user's login credentials.

See [the harness research record](research/evals.md) for official sources,
verified local runtime availability, and limitations. A native discovery check
proves loading or listing only. It does not prove that the component changed
the model's behavior, obeyed constraints, or produced a correct result.
