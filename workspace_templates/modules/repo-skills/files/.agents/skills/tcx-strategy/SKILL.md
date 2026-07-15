---
name: tcx-strategy
description: "Author and manage standalone Codex strategy skills named `strategy-*` when the user wants an agent-readable investment strategy, library entry, entry or exit criteria, sizing rule, evidence standard, or decision-ready procedure. Start tool-using management as an exact first-line `$tcx-strategy` root turn; do not wrap it in `$tcx-build`."
---

# Strategy Creator

Use this skill to author and manage strategy skills. A strategy skill is a standalone Codex-compatible skill whose `name` starts with `strategy-`, stored as a normal project skill under `.agents/skills/strategy-*`. It captures a user-approved investment strategy as an agent-readable operating procedure and should remain usable without importing TradingCodex service code.

Strategy skills guide judgment only. They never approve orders, execute orders, override policy, change MCP allowlists, bypass role boundaries, read secrets, or grant broker authority.

Each workflow must bind the selected strategy name and content hash before
analysis. Later edits do not rewrite the strategy snapshot used by an earlier
decision, replay, forecast, or postmortem. Historical and forward evidence may
justify a draft change proposal, but it never silently changes or activates the
strategy.

In native Codex, selection requires one exact explicit `$strategy-*` invocation;
an unprefixed name or natural-language similarity never selects a strategy. The
hook accepts only an active validated workspace strategy and seals its content
under the run before planning. The read-only viewer never selects a strategy.

The generated strategy body must be standalone. Do not mention platform names, platform role identifiers, subagent mechanics, MCP, approval gates, execution gates, or handoff mechanics inside the strategy skill. If a section has no user-provided rule, write `not specified` without adding a delegation sentence.

## Managed Turn Admission

Creating, updating, activating, archiving, or deleting a strategy is a durable
managed operation. Proceed only when the current root prompt has the exact
physical first line `$tcx-strategy`, followed by a non-empty concrete request.
Do not combine it with `$tcx-build`, `$tcx-brain`, or an order marker. Use the
normal `trading-research` profile; the marker admits only allowlisted Strategy
lifecycle calls through the proof-protected `manage_strategy` MCP tool for this
root turn. It does not grant generic Build, Brain,
order, credential, publication, or global-config authority. Plan mode and
subagents cannot issue or use the grant.

If the current turn lacks that admission, do not call shell or file-edit tools.
Clarify or draft the proposed strategy in the conversation, then return a new
root-turn prompt in this exact shape:

```text
$tcx-strategy
<Create, update, activate, archive, or delete> <strategy-name> with <reviewed request>.
```

Managed admission remains bounded by the active Codex sandbox, the deterministic
hook, workspace-only staging files, and the shared strategy service.

## Workflow

1. Verify managed admission before any tool use. Identify whether the explicit
   request creates, updates, activates, archives, or deletes a `strategy-*`
   skill.
2. Choose a short lowercase hyphen-case `name` with the required `strategy-` prefix.
3. Draft a concise body directly from the required sections and strategy
   boundaries below. The pristine TradingCodex path must not depend on a
   host-global or plugin authoring skill. If the user explicitly selected an
   available external authoring skill, it may help draft the body within the
   same boundaries, but the shared strategy service remains the exclusive
   writer of the strategy bundle and projection.
4. For create or update, prepare a workspace-local body file outside protected
   TradingCodex paths, such as `<strategy-name>.draft.md` at workspace root, using
   native `apply_patch`. Put only the strategy body in that file; do not add
   frontmatter or `agents/openai.yaml`.
5. For each durable operation, invoke only its corresponding
   `manage_strategy` MCP action:

   ```text
   action=create|update name=<strategy-name> description=<description> body_path=<root-level-workspace-body-file> language=<language> status=<draft|active|archived>
   action=inspect name=<strategy-name>
   action=list [active_only]
   action=activate|archive name=<strategy-name>
   action=delete name=<strategy-name> [force]
   ```

   For create or update, pass the reviewed body as `body_path`, plus an
   accurate description and language. Always pass `--status`; use `active` only
   when the current root request explicitly approves that exact reviewed
   content. Otherwise use `draft`, and never update an existing active strategy
   merely to stage an unapproved proposal.
6. Never manually edit `.agents/skills`, `.codex/config.toml`, generated
   indexes, or fixed subagent TOML. Never repair around a service validation
   error; report the error and revise only the staged body when appropriate.
7. Inspect the service result and, within the admitted Strategy turn, validate
   with `manage_strategy` actions `inspect` and `list`. Treat service-created
   bundle and projection metadata as the source of truth.
8. For an evidence-driven change, show the old and proposed rules, reason,
   affected scope, supporting and contrary cases, and validation status. Keep
   the current active version unchanged until approval. If an active strategy
   needs changed content, inspect and propose in one turn, then require a new
   exact `$tcx-strategy` root turn that explicitly approves the reviewed update;
   never carry the prior turn's managed grant across the reply.
9. Delete only when the root request explicitly names deletion. Do not add
   `--force` unless the user explicitly approved permanent removal of the
   active strategy after reviewing the consequence; otherwise let the service
   archive an active strategy.
10. Remove the staged body with native `apply_patch` after a successful service
    write unless the user explicitly asked to retain the draft file.

An exact create request with sufficient rules may create a `draft` in one turn;
do not add a redundant confirmation. Activation, deletion, and replacement of
active content require the original request to name that exact effect.

## Service-Generated Skill Shape

The shared service must generate this folder shape:

```text
.agents/skills/strategy-<name>/
  SKILL.md
  agents/openai.yaml
```

`SKILL.md` frontmatter must include these scalar fields, and `name` must match
the strategy directory name. Do not write this frontmatter directly:

```yaml
---
name: strategy-<name>
description: "<what this strategy does and when to use it>"
type: strategy
status: draft
language: <BCP-47 language tag or unknown>
owner: user
last_reviewed: unknown
---
```

Set `status: active` only after the user approves the strategy. Use `draft`, `active`, or `archived`.

## Required Body Sections

Keep the body concise and include these headings:

- `# <Strategy Name>`
- `## Thesis`
- `## Eligible Universe`
- `## Preferred Setups`
- `## Entry Criteria`
- `## Exit Criteria`
- `## Evidence Requirements`
- `## Decision-Ready Standard`
- `## Sizing Guidance`
- `## Risk Controls`
- `## Block Conditions`
- `## Change Log`

Use `unknown` or `not specified` for missing user input. Do not invent strategy rules.
For `## Sizing Guidance`, include only strategy-level sizing rules supplied by the user, such as max position size, leverage limits, loss limits, scaling rules, or cash/reserve constraints. If the user did not specify them, write exactly `not specified`.

## Service-Generated Metadata

The service must generate `agents/openai.yaml` with:

```yaml
interface:
  display_name: "<human strategy name>"
  short_description: "<25-64 character UI description>"
  default_prompt: "Use $strategy-<name> to apply this user-approved strategy."
policy:
  allow_implicit_invocation: false
```

The default prompt must mention the exact `$strategy-<name>` name.

## Root Config Projection

The shared service projects active strategy skills into the strategy marker
block in `.codex/config.toml`. Never edit the block directly. Its resulting
shape is:

```toml
# BEGIN TradingCodex strategy skills
[[skills.config]]
path = "/absolute/path/to/.agents/skills/strategy-<name>/SKILL.md"
enabled = true
# END TradingCodex strategy skills
```

Do not add strategy skills to fixed subagent TOML files. The coordinator may read the selected strategy and pass compact context in assignment briefs, but that orchestration detail must not appear inside the strategy body.

## Validation

Before finishing, confirm:

- The name starts with `strategy-`.
- The frontmatter `name` matches the `.agents/skills/<name>/` directory.
- Required frontmatter is present.
- Required body sections are present.
- `agents/openai.yaml` has a valid default prompt with the exact strategy name.
- The shared service projected active strategies into the root strategy marker
  block.
- Fixed subagent TOML files do not reference the strategy.
- The strategy body has no platform coupling terms such as TradingCodex, role names, MCP, approval gates, execution gates, or handoff instructions.
- `$tcx-build` was not used as a wrapper, and no Brain or generic Build action
  was attempted with the Strategy grant.
