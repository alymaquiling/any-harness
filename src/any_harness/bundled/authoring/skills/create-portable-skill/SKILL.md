---
name: create-portable-skill
description: Add a reusable portable skill to an any-harness source plugin using shared frontmatter and a clear workflow. Use when creating or refining a skill that should run across Claude Code, Codex, OpenCode, and Cursor.
---

# Create a portable skill

Create the skill inside the source plugin at
`skills/<skill-name>/SKILL.md`. The directory name and frontmatter `name` must
match exactly. Keep the frontmatter portable:

```yaml
---
name: review-api
description: Review API changes for contract, compatibility, and error-handling issues. Use when an API endpoint or schema changes.
---

# Review API

1. Inspect the changed routes, schemas, and callers.
2. Check request validation, response compatibility, and documented errors.
3. Report findings with file references, severity, and a concrete fix.
4. State what was checked and what remains uncertain.
```

The body must tell the agent when the skill applies, what context to inspect,
which steps to follow, and what result to return. Prefer relative references
such as `references/error-catalog.md` or `scripts/check-schema.py` when the
skill needs supporting material; keep those files under the same skill folder.

## Add it with the CLI

From the canonical source plugin directory, create the scaffold with the
current command:

```sh
any-harness add skill review-api --source PATH --description "Review API changes for contract and compatibility issues."
```

The command writes `skills/review-api/SKILL.md`. Replace its placeholder body,
then validate the complete source plugin:

```sh
any-harness validate PATH
```

Do not add Cursor-only fields such as `paths`, `icon`, or
`disable-model-invocation` to the shared frontmatter. Claude Code skills
support native `model` and `effort` settings, while Codex, OpenCode, and Cursor
skill frontmatter do not document model or reasoning overrides. If you need a
target-specific model or provider option, put the exact native key under that
skill's `targets.<harness>.settings` block only when the target supports it;
otherwise use an agent or a native file. Do not invent a shared effort or
thinking value. See `docs/model-settings.md` in the any-harness checkout when
available for current examples.

## Quality checks

Keep one clear responsibility per skill. Make the description describe both
the action and its trigger; avoid vague descriptions such as “helps with code”.
Use imperative steps, name the expected output, and explain any destructive or
irreversible action before it occurs. Run `any-harness build PATH --out OUTPUT`
when you need to inspect the generated skill in every harness.
