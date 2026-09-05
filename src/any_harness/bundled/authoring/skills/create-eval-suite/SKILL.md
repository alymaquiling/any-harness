---
name: create-eval-suite
description: Create and improve native cross-harness evaluation cases for an any-harness plugin, including isolated fixtures, activation checks, outcome assertions, and prohibited-change constraints.
---

# Create an eval suite

Use this skill when a plugin needs repeatable checks for skills, agents, or a
plugin-level workflow. Keep the source plugin and its eval suite together:

```text
plugin/
├── plugin.yaml
├── skills/ and agents/
└── evals/
    ├── cases.yaml
    └── fixtures/<case-id>/...
```

Read [the bundled schema notes](references/eval-schema.md) for the case shape
and harness capability notes. The runner treats `cases.yaml` as grader data;
expected answers and checks must stay there, outside the prompt sent to an
evaluated harness. The repository also keeps the longer guide in
`docs/evals.md`.

## Author a case

Start with a small user request that could be answered by a real user. Give it
only the fixture and context it needs. Identify the component under test:

```yaml
- id: reviewer-agent
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
      - type: output_contains
        value: missing test
    constraints:
      - type: unchanged
        path: .
```

Use `mode: automatic` when the request should cause the harness to choose a
relevant skill or agent from its native discovery catalog. Use `mode: explicit`
when the prompt or native command names the component. Use `invocation:
required` when activation is part of the case; if the harness cannot expose
selection telemetry the runner reports `inconclusive` with unknown evidence.
Use `optional` only when the case evaluates the outcome independently of
component selection, and `forbidden` for negative activation cases. The runner
never treats a good answer as proof that a component ran.

Cover four independent concerns when they matter:

1. activation: the component should or should not be selected;
2. explicit invocation: a named component should perform its workflow;
3. outcome: output text, expected files, and executable checks;
4. constraints: files, text, commands, or edits that must not occur.

Use the smallest deterministic assertions first (`output_contains`,
`output_not_contains`, `file_exists`, `file_contains`, `file_not_exists`,
`unchanged`, and executable `check`). `action_forbidden` and `tool_not_used`
fail when a prohibited call is observed; if the call is absent but the native
trace is incomplete, they remain inconclusive. Keep
model-based or human review optional and separate from deterministic
assertions. A model grader should consume `result.json` and write its own
versioned review artifact; it must not rewrite deterministic results. Never
encode the expected finding in the prompt just to make an assertion pass.

## Compare safely

Add named `variants` for controlled comparisons. Keep the fixture, prompt,
harness, timeout, and permissions constant. Use `exclude: ["agent:name"]` or
`exclude: ["skill:name"]` for with/without comparisons, and use `source:` for a
checked-in plugin revision or sibling source directory. Put native model and
effort/variant settings under the target harness only when that harness
documents them; do not translate a value from one provider into another.

Run one fresh fixture per repeat and cap concurrency:

```sh
any-harness eval validate .
any-harness eval preview . --harness codex
any-harness eval run . --harness codex --case reviewer-agent --repeat 2 --jobs 1 --timeout 120 --out ../eval-results
any-harness eval report ../eval-results/result.json
```

Preview before a live run. Check the reported harness version, model, effort,
configuration scope, and native command. If a harness is not installed or
authenticated, leave the case skipped or inconclusive; do not replace it with
a direct model API call. Isolated mode uses temporary workspaces and config
roots, but is not an OS sandbox and does not remove host policy or managed
settings. Opt into inherited user configuration only when the run explicitly
records that choice. Review artifacts and clean up only the exact temporary
output directory outside the source tree.
