# Model and reasoning settings

Put model and reasoning choices in the component's
`targets.<harness>.settings` block. The generator removes `targets` from the
generated file and copies each selected harness's settings into its native
agent or skill representation. Settings are passed through as native keys;
there is no shared `effort` or `thinking` value that the adapters translate
between vendors. Omit a setting to inherit the harness's normal default.

This example is an agent because all four harnesses document model selection
for agents:

```yaml
---
name: reviewer
description: Review changes for bugs and missing tests.
targets:
  claude-code:
    settings:
      model: sonnet
      effort: high
  codex:
    settings:
      model: gpt-5.6
      model_reasoning_effort: high
  opencode:
    settings:
      model: openai/gpt-5
      reasoningEffort: high
      textVerbosity: low
  cursor:
    settings:
      model: "claude-opus-5[effort=high]"
---

Review the requested changes and report concrete findings.
```

The values in the example are independent. Choose model IDs available in the
user's account and provider, and use the spelling and value set that the
target documents. A setting in one target block is not emitted into another
target's files.

Claude Code supports `model` on skills and subagents. It accepts aliases such
as `sonnet`, `opus`, and `haiku`, full model IDs, or `inherit`. Claude Code
also supports `effort` on skills and subagents; supported levels are
model-dependent and include `low`, `medium`, `high`, `xhigh`, and `max`.
Claude's effort scale is calibrated per model, so equal names do not promise
equal reasoning. Claude's extended-thinking behavior is session/model
configuration and inheritance; this source format does not invent a separate
portable `thinking` field. See the [Claude model configuration](https://code.claude.com/docs/en/model-config),
[skill frontmatter](https://code.claude.com/docs/en/slash-commands), and
[subagent](https://code.claude.com/docs/en/sub-agents) references.

Codex custom agent files are TOML under `.codex/agents/` and accept `model`
and `model_reasoning_effort`. The effort values depend on the selected model;
the current Codex documentation describes levels such as `low`, `medium`,
`high`, `xhigh`, `max`, and `ultra` where supported. Codex `SKILL.md` files
do not document a model or reasoning override, so keep those choices on an
agent or in Codex configuration. If a custom agent sets only `model`, Codex
preserves the effort already resolved from the spawn request, `[agents]`
defaults, or the parent; set both fields when the selected model needs a
different effort. A Codex plugin package contains skills; use the direct
installer when the source also contains custom agents. See
[Codex custom subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
and [Codex skills](https://learn.chatgpt.com/docs/build-skills).

OpenCode's generated stable V1 agent files use `model: provider/model`.
Provider options are native and pass through the agent configuration. The
official OpenCode example for OpenAI reasoning models uses
`reasoningEffort: high` and `textVerbosity: low`. Other keys are not
validated by this tool; OpenCode passes them to the provider, so use the
provider's documented shape. In particular, do not turn a provider's
`thinkingConfig` into a universal `thinking` or `effort` field. OpenCode V2
model configuration places provider-specific options such as `thinkingConfig`
under model/provider `settings` and supports model-specific variants; this
generator does not synthesize that V2 configuration. See [OpenCode agents](https://opencode.ai/docs/agents),
[OpenCode skills](https://opencode.ai/docs/skills), and
[OpenCode model configuration](https://opencode.ai/v2/docs/models).

Cursor documents model selection for subagents, not for skills. A Cursor
agent can use `model: inherit` or a specific model ID. Per-model parameters
are appended in brackets, for example
`claude-opus-5[effort=high,context=300k]`; available parameters depend on the
model. Cursor's skill frontmatter documents discovery and invocation fields,
not model or effort overrides. Use the bracket form on an agent model rather
than adding a standalone `effort` key. See [Cursor subagents](https://cursor.com/docs/subagents)
and [Cursor skills](https://cursor.com/docs/skills).

## Skills versus agents

The source schema lets both skills and agents carry target settings so a
target-specific native field can be preserved when that component supports
it. The documented model controls differ: Claude skills support `model` and
`effort`; Codex, OpenCode, and Cursor skill frontmatter does not document
those controls. For a portable skill, keep model selection outside the skill
unless the target's current documentation says otherwise. For provider-specific
reasoning or thinking configuration, put the exact native option in that
target's settings or add a native target file, then validate it with the
harness.

The CLI intentionally does not check model IDs or reasoning values against
vendor catalogs. This keeps the source format usable as models change, but a
successful `validate` means only that the source and generated syntax are
valid. Run the target's own load or validation command before release.
