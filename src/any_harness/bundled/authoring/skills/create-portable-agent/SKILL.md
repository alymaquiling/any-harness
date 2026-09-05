---
name: create-portable-agent
description: Add a focused portable agent to an any-harness source plugin with shared name and description frontmatter. Use when a task needs a specialized delegated role across Claude Code, Codex, OpenCode, and Cursor.
---

# Create a portable agent

Create the agent at `agents/<agent-name>.md`. The filename stem and frontmatter
`name` must match exactly. Keep shared frontmatter to `name` and `description`
so every adapter can render it:

```yaml
---
name: security-reviewer
description: Review security-sensitive changes for injection, authorization, and secret-exposure risks.
---

# Security reviewer

You are a focused security reviewer.

When invoked:

1. Identify security-sensitive code paths and trust boundaries.
2. Check input handling, authentication, authorization, and secret exposure.
3. Report concrete findings with severity and file references.
4. If no issue is found, state the checks performed and remaining limits.
```

The description is the routing signal for delegation. Give each agent one
responsibility and a concise prompt. Specify the expected report or handoff so
the parent agent can use the result without guessing.

## Add it with the CLI

From the canonical source plugin directory, create the scaffold with the
current command:

```sh
any-harness add agent security-reviewer --source PATH --description "Review security-sensitive changes for injection, authorization, and secret-exposure risks."
```

The command writes `agents/security-reviewer.md`. Replace its placeholder body,
then validate the source plugin:

```sh
any-harness validate PATH
```

Keep harness-specific fields out of the shared top-level frontmatter. For a
per-harness model or reasoning choice, use that agent's
`targets.<harness>.settings` block; the generator copies those native keys only
to the selected harness. Cursor uses bracket options in `model`, OpenCode uses
`provider/model` plus provider options, Codex uses
`model_reasoning_effort`, and Claude Code uses `model` and `effort`. Do not
invent a shared effort or thinking value. See `docs/model-settings.md` in the
any-harness checkout when available for current examples and the documented
skill/agent differences. Put larger
harness-only files under `native/<harness>/` when a settings block cannot
express the required behavior.

## Quality checks

Use a specific trigger in the description, avoid generic helper agents, and
keep the body focused on the role. Run
`any-harness build PATH --out OUTPUT` to inspect how the agent is represented in
each generated harness. If a target cannot represent agents natively, preserve
the source and report that limitation rather than silently pretending the agent
was installed.
