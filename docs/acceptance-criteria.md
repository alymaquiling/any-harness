# Acceptance criteria

Consolidated from the user's requests in this session.

1. Maintain plugins, skills, agents, and their supporting files in one source folder.
2. Generate compatible native output for Claude Code, Codex, OpenCode, and Cursor.
3. Dispatch subagents to research official frontmatter, structures, capabilities, and installation flows for each harness.
4. Automate format differences so authors do not maintain four independent copies; support explicit native settings where needed.
5. Provide an easy tool for creating plugins and their skills/agents, validating the source, and generating output.
6. Make end-user installation of Git-hosted plugins as seamless as practical; document installation and maintenance clearly.
7. Bundle skills invocable in all four harnesses that help authors create compatible plugins, skills, and agents.
8. Bundle an add-client-harness skill that dispatches web-research subagents and guides implementation of a new native adapter, tests, and documentation.
9. Provide clear setup, authoring, generation, distribution, compatibility, and extension documentation.
10. Prefer the simplest solution that meets these requirements; use wshobson/agents only as optional inspiration.
11. Keep dependencies minimal, account for an unfinished local runtime setup, and communicate what testing/validation requires.
12. Validate the working tool and report which native harness checks were performed and which remain unverified.

## Suggested goal

Build and validate a minimal-dependency, documented tool that generates and installs native plugins, skills, and agents for Claude Code, Codex, OpenCode, and Cursor from one source folder, supports straightforward Git distribution, and bundles cross-harness authoring skills plus a research-driven skill for adding future harnesses.

Native feature gaps must be explicit; compatibility does not mean silently simulating unsupported capabilities. Publishing the tool or a plugin to a remote repository, registry, or vendor marketplace has not been requested as part of this session.
