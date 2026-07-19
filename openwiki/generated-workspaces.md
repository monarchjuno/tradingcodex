# Generated Workspaces Source Map

Canonical behavior: [Generated Workspaces](../docs/generated-workspaces.md),
[Knowledge Wikis](../docs/knowledge-wikis.md), and
[Installation](../installation.md).

## Primary Sources

| Concern | Source |
| --- | --- |
| Attach/update and module graph | `tradingcodex_cli/generator.py` |
| Runtime/home/service resolution | `tradingcodex_cli/runtime.py`, `startup_status.py`, `health.py` |
| Doctor | `tradingcodex_cli/commands/doctor.py` |
| Generated source files | `workspace_templates/modules/*/files/` |
| Module ownership | `workspace_templates/modules/*/module.json` |
| CLI launchers | `workspace_templates/modules/cli/files/` |
| Agent/skill projection | `tradingcodex_service/application/agents.py` |
| Workspace provenance | `tradingcodex_service/application/workspaces.py` and harness models |
| Local Wiki scaffold and active community projection | `tradingcodex_service/application/knowledge_wikis.py` |
| Wiki/Obsidian Git ignores | `tradingcodex_service/application/workspace_git.py` |

Generated files are projections, not hand-maintained runtime state. Edit their
template or projection owner and regenerate a disposable workspace.

## Identity Rules

- End-user attach uses the published package; source development uses
  `./install.sh --dev` from this checkout.
- Development and release workspaces keep separate HOME, DB, and service
  identities. Do not hard-code the release port in development diagnostics.
- `tcx update` preserves user-owned files and durable workspace research while
  refreshing owned generated paths.
- Attach creates missing `wikis/local` scaffold files outside the module lock;
  update preserves local pages, local index, Wiki sources, registry, packages,
  and projections while refreshing only `wikis/index.md`.
- A moved workspace needs an update so absolute project MCP and runtime bindings
  are regenerated.
- Attach/update never stage, commit, create a branch, push, publish, or run npm.
- Generated workspaces contain no Node runtime or frontend source.
- Credentials and raw secret values are never projected.

## Template Rules

- Keep reviewable prompts, skills, hooks, role profiles, policies, and launchers
  as ordinary files under `workspace_templates/modules/*/files`.
- Do not hide durable contract text in Python strings when a template file can
  own it.
- Keep one generated owner for each path and remove obsolete projections rather
  than layering compatibility indefinitely.
- Treat projection indexes as diagnostics, not authority. Do not add a new
  index when the native config or owning registry already answers the question.
- Project the direct fixed model fields already defined in
  [Roles, Skills, And Workflows](../docs/roles-skills-and-workflows.md). Do not
  project a model policy manifest or duplicate model availability checks in
  `doctor`.
- Keep hooks narrow: lifecycle proofs and TradingCodex-owned secret,
  service-state, broker, and order boundaries only. Native Codex owns ordinary
  shell, network, workdir, spawn, and model validation; `tcx-calc` owns its
  command and scratch boundary.
- Optional OpenBB is a direct non-required Codex MCP entry. Attach/update does
  not provision or start it and stores only configured environment-variable
  names.

## Development Bootstrap

```bash
./install.sh --dev /path/to/empty-workspace
./install.sh --dev --update /path/to/existing-dev-workspace
```

Then use the generated wrapper:

```bash
./tcx doctor
./tcx service status --json
./tcx service ensure
```

On Windows use `tcx.cmd`. Do not mix `--dev` and `--from` or convert a release
workspace in place.

## Edit Checklist

1. Locate the template/projection owner and all generated-path tests.
2. Preserve workspace identity, user-owned files, and HOME/DB isolation.
3. Regenerate a disposable development workspace.
4. Inspect the effective config, prompts, skills, hooks, and launchers touched.
5. Run `./tcx doctor` and the relevant native Codex smoke.

Do not validate generated behavior only by inspecting template source.
