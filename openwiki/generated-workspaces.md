# Generated Workspaces

Use this page before changing `tcx attach`, `tcx init`, `tcx update`, template modules, generated files, hooks, project MCP config, projection indexes, or the generated `./tcx` wrapper. Human-facing rules live in [docs/generated-workspaces.md](../docs/generated-workspaces.md).

## Attach Model

TradingCodex is installed globally or invoked through `uvx`, then attached to the workspace where Codex should work:

```bash
uvx --refresh --from tradingcodex tcx attach . && ./tcx doctor
```

Valid targets are empty directories or git-initialized directories containing only `.git` plus optional git metadata files. A source checkout of this repository is not a generated workspace.

## Generator Flow

`tradingcodex_cli/generator.py`:

1. loads `workspace_templates/modules/*/module.json`
2. resolves module dependencies and conflicts
3. renders `files/` template trees into the target workspace
4. ensures `.tradingcodex/workspace.json`
5. writes generated indexes under `.tradingcodex/generated/`
6. calls `project_agent_configuration()`
7. writes startup status snapshot

Default modules include `codex-base`, `fixed-subagents`, `repo-skills`, guardrails, information barriers, audit, MCP, stub/paper execution, and postmortem.

## Generated Contract

Generated workspaces should contain:

- `AGENTS.md`
- `.codex/config.toml`
- `.codex/prompts/base_instructions/head-manager.md`
- `.codex/agents/*.toml`
- `.codex/hooks/tradingcodex_hook.py`
- `.agents/skills/*`
- `.tradingcodex/*`
- `trading/*`
- `./tcx`

Clean generated workspaces must not contain `package.json`, Node MCP runtime files, workspace-local canonical investment DBs, broker credentials, raw secrets, legacy `.tradingcodex/mainagent/*.yaml` role registries, or policy-local `role_owned_skills` roster copies. Role skill sources are projected from `tradingcodex_service/application/agents.py` into `.codex/agents/*.toml`.

## Projection Outputs

Generated indexes under `.tradingcodex/generated/` include module, capability, component, agent, skill, and projection metadata. Component data comes from `tradingcodex_service/application/components.py`. Agent and skill projection comes from `tradingcodex_service/application/agents.py`.

When generated agent behavior changes, inspect generated output, not just template source.

## Update Rules

`tcx update .` refreshes generated paths for an existing workspace while preserving immutable `workspace_id` and active profile. Inside a generated workspace, `head-manager` should not rewrite protected harness surfaces itself; it should direct the user to the appropriate terminal update command.

## Edit Checklist

When changing this area:

- update `docs/generated-workspaces.md` for durable contract changes
- keep human-readable generated content under `workspace_templates/modules/*/files`
- update generator tests for module graph, rendered paths, and generated indexes
- run a clean generated workspace smoke
- inspect `.tradingcodex/generated/*.json`
