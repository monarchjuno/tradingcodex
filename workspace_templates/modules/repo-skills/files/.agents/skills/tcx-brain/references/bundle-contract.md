# User-Owned Local Investment Brain Bundle Contract

Create only this v1 shape under `investment-brains/<investment-brain-id>` by
default:

```text
investment-brains/investment-brain-example/
├── .tradingcodex/
│   └── plugin.json
└── skill/
    ├── SKILL.md
    ├── agents/
    │   └── openai.yaml
    └── references/        # optional Markdown only
```

Do not add scripts, assets, symlinks, generated caches, curation notes, private
case material, or extra root entries. A local `.git` directory is tolerated by
installation validation, but this skill must not create one.

## Manifest

Use exactly these fields. `repository` is optional; omit it for a private local
source without a user-selected repository. Ask the user for publisher and
license rather than inventing legal ownership terms.
When the user supplies `repository`, require a credential-free public HTTPS
repository URL with a repository path. Do not place local/file paths, SSH
locators, private hosts, numeric address aliases, special-use DNS names, custom
ports, signed URLs, queries, or fragments in declared metadata.

```json
{
  "format": "tradingcodex.investment-brain",
  "schema_version": 1,
  "type": "investment-brain",
  "id": "investment-brain-example",
  "version": "1.0.0",
  "skill": "skill",
  "source": {
    "publisher": "User-selected publisher",
    "license": "User-selected license"
  }
}
```

The id must be lowercase hyphen-case, start with `investment-brain-`, and match
the bundle skill name. The version uses stable `major.minor.patch` syntax.
Changed content that has already been installed requires a version higher than
every installed version.

## Brain Skill

`skill/SKILL.md` frontmatter contains only:

```yaml
---
name: investment-brain-example
description: "What this inquiry and interpretation framework does and when it applies."
---
```

Keep the body concise and use these sections when useful:

- `# <Brain Name>`
- `## Philosophy`
- `## Inquiry Priorities`
- `## Interpretation Principles`
- `## Scenarios And Falsifiers`
- `## Applicability And Abstention`

The body and optional Markdown references must be original, platform-neutral
abstractions. Do not include private case details, Decision Memory references,
platform or skill names, role ids, tool or runtime identifiers, workflow or
dispatch instructions, model/sandbox settings, artifact paths, secrets, or any
policy, approval, broker, order, or execution authority.

## Codex Metadata

`skill/agents/openai.yaml` contains exactly:

```yaml
interface:
  display_name: "Example Brain"
  short_description: "Prioritize durable causal evidence"
  default_prompt: "Use $investment-brain-example for this analysis."
policy:
  allow_implicit_invocation: false
```

The default prompt must name the exact Brain id. Both the projected Brain and
the `tcx-brain` management entrypoint remain explicit-only.

## Review Checklist

- The user selected every source case and at least one material counterexample.
- Privacy review excluded private facts and verbatim case material.
- The directory, manifest id, skill name, metadata prompt, and version agree.
- Publisher and license came from the user.
- Only the allowed regular files exist; references are Markdown.
- The Brain is inquiry and interpretation only, with applicability and
  abstention limits.
- No managed package, registry, projection, Decision Memory, or third-party
  source was edited.
- No install, activation, Git, or publication action occurred during source
  authoring.

Run the non-mutating authoring check before handoff:

```text
manage_investment_brain action=validate local_source=investment-brains/<investment-brain-id>
```

The hook injects the required Brain-turn proof. The equivalent `./tcx
investment-brains validate --local ...` (`tcx.cmd` on native Windows) is only a
user-terminal fallback, not a model-shell path. Validation must not install,
activate, project, or mutate the registry. Do not use installation as an
authoring-time validation shortcut.
