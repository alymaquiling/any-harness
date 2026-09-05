# Codex adapter research

Checked September 5, 2026 against official OpenAI documentation and the installed plugin-creator manifest validator.

Skills use `SKILL.md` with required `name` and `description`; optional support files remain within each skill. Project discovery uses `.agents/skills`, while `agents/openai.yaml` can supply UI/dependency/invocation metadata. [Build skills](https://learn.chatgpt.com/docs/build-skills)

Current custom agents are standalone `.codex/agents/*.toml` files (or `~/.codex/agents` globally), requiring `name`, `description`, and `developer_instructions`. Session configuration keys such as `model`, `model_reasoning_effort`, and `sandbox_mode` may be set explicitly. Omitted model configuration inherits runtime defaults. Do not emit historical agent registration tables when targeting this current standalone-file format. [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)

Plugins package skills and connectors using `.codex-plugin/plugin.json`. The generator follows the installed plugin-creator contract, including author/interface metadata and a repository marketplace at `.agents/plugins/marketplace.json`. Entries use a local source object, installation/authentication policy, and category. Public packaging and submission are separate from local development. [Build plugins](https://learn.chatgpt.com/docs/build-plugins)

Codex companion configuration has a concrete package contract. If the source contains regular files at `native/codex/plugin/.mcp.json` or `native/codex/plugin/.app.json`, the renderer copies each file to the generated plugin root and adds the matching `mcpServers: "./.mcp.json"` or `apps: "./.app.json"` field to `.codex-plugin/plugin.json`. Missing companions leave their fields out, avoiding dangling manifest paths. The files remain native Codex JSON and are validated by the Codex plugin contract; the adapter does not translate their contents. [Plugin manifest specification](https://learn.chatgpt.com/docs/build-plugins)

The documented plugin manifest does not establish discovery of bundled custom agent TOML files. Accordingly this adapter generates those separately in `project/.codex/agents`, with an explicit compatibility note. Direct installation copies them to native discovery paths; plugin-only installation does not claim to activate them. IDE clients support standalone skills but currently do not support plugins. [Plugins availability](https://learn.chatgpt.com/docs/plugins)

The manifest validator bundled with the development environment is stricter than the minimal public example: it requires author/interface fields and rejects some optional fields shown by other examples. The adapter uses that stricter shape. It is a tested local contract, not a claim that every Codex release has identical ingestion behavior.
