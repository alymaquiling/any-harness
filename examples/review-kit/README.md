# review-kit

Review changes and explain concrete findings

Edit plugin.yaml, skills/*/SKILL.md and agents/*.md. Run `any-harness validate .` and `any-harness build . --out dist`. See each generated INSTALL.md for native distribution.

The `evals/` directory contains a small cross-harness suite: automatic and
explicit skill cases, an explicit reviewer-agent case, a plugin workflow, and
a negative activation case. Validate and preview it before a bounded native
run:

```sh
any-harness eval validate .
any-harness eval preview . --harness codex
any-harness eval run . --harness codex --case agent-finds-regression \
  --repeat 2 --jobs 1 --timeout 120 --out ../eval-results/review-kit
any-harness eval report ../eval-results/review-kit/result.json
```

The fixture guard at `evals/checks/review_guard.sh` is a suite grader and is
kept outside the staged fixture. `tools/grade-result.py` creates a separate
pending review artifact from `result.json`; its rubric is not sent to a
harness.
