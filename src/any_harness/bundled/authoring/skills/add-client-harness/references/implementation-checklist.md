# New harness implementation checklist

Use this reference after the two independent official research passes. Replace
the placeholders with facts from the target harness and link every non-obvious
choice to its primary source.

## Research record

```text
Harness identifier: <lowercase-id>
Research date: <YYYY-MM-DD>
Format/schema sources: <official URLs>
Installation sources: <official URLs>
Observed or inferred behavior: <clearly labeled notes>
Subagent status: <two returned research results, or fallback with no delegation>
```

Record at least:

* plugin or package manifest filename and required fields;
* skill/agent filenames, directories, frontmatter, discovery, and precedence;
* project and user/global installation directories;
* plugin/marketplace or Git installation and update behavior;
* whether custom subagents exist and what fallback is supported;
* reload, authentication, platform, and version constraints.

## Code touchpoints

| Area | Required check |
| --- | --- |
| `model.py` | Add the identifier to `HARNESSES`; ensure `targets` validation and source loading remain strict. |
| `build.py` | Map project root; render native skill/agent files; emit the researched plugin/manifest or a project-only note; copy only allowed native paths. |
| `install.py` | Map project and global paths; preserve receipts, locks, dry-run, symlink, ownership, and unmanaged-file protections. |
| `cli.py` | Expose the identifier in choices/help; keep existing command forms and errors unchanged; add only researched flags. |
| Docs/research | Add exact official examples, citations, install steps, scopes, limitations, and update caveats. |
| Tests | Assert source validation, generated paths/frontmatter, install scopes, CLI behavior, and old-adapter compatibility. |

If the harness does not have a plugin concept, do not invent a manifest or
marketplace. Keep generated package output empty or absent as appropriate,
place native files in the researched project directory, and make the
compatibility note explicit.

## Output checks

For a valid source plugin, check all of the following:

```text
load(source) succeeds
generate(source, harnesses=(new_id,)) succeeds
generated paths match the official project/global layout
shared name/description frontmatter is preserved
native settings are present only where documented
manifest/schema validation passes when a manifest exists
compatibility.json and INSTALL.md explain limitations
existing harness renders are unchanged
```

For installation, first run a dry run against a temporary project directory,
then an actual project-scoped install if the operation is safe. Test global
installation only when the researched path and user request authorize it; never
write to the real home directory merely to prove a path calculation.

## Forward-test report

Use a temporary directory and a realistic agent request. A concise report looks
like this:

```text
Forward test: PASS/FAIL
Source: <temporary path>
Request: audit an OAuth callback change for auth and secret-handling issues
Commands: init, add agent, validate, build
Generated agent: <relative path(s)>
New-harness smoke test: PASS or unavailable (<reason>)
```
