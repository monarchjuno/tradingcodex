from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingcodex_service.version import TRADINGCODEX_VERSION
from tradingcodex_service.application.agents import project_agent_configuration
from tradingcodex_service.application.components import list_harness_components
from tradingcodex_service.application.runtime import ensure_workspace_manifest, read_workspace_manifest, tradingcodex_home
from tradingcodex_cli.startup_status import write_server_status_snapshot

DEFAULT_MODULE_IDS = [
    "codex-base",
    "fixed-subagents",
    "repo-skills",
    "guidance-guardrails",
    "enforcement-guardrails",
    "information-barriers",
    "audit",
    "tradingcodex-mcp",
    "stub-execution",
    "paper-trading",
    "postmortem",
]


@dataclass(frozen=True)
class Module:
    id: str
    description: str
    dir: Path
    manifest: dict[str, Any]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return repo_root() / "workspace_templates"


def bootstrap_workspace(project_dir: Path | str, force: bool = False, dry_run: bool = False, module_ids: list[str] | None = None) -> dict[str, Any]:
    target = Path(project_dir).resolve()
    registry = load_module_registry(templates_dir())
    modules = resolve_module_graph(registry, module_ids or DEFAULT_MODULE_IDS)
    subagents = collect_template_subagent_names(modules)
    existing_manifest = read_workspace_manifest(target)
    workspace_id = str(existing_manifest.get("workspace_id") or f"tcxw_{uuid.uuid4().hex}")
    context = {
        "PROJECT_NAME": sanitize_project_name(target.name or "tradingcodex-workspace"),
        "PROJECT_DIR": str(target),
        "WORKSPACE_ID": workspace_id,
        "SOURCE_ROOT": str(repo_root()),
        "PYTHON_EXECUTABLE": sys.executable,
        "GENERATED_AT": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "TRADINGCODEX_VERSION": TRADINGCODEX_VERSION,
        "TRADINGCODEX_MCP_PACKAGE_SPEC": os.environ.get("TRADINGCODEX_MCP_PACKAGE_SPEC", "tradingcodex"),
        "TRADINGCODEX_HOME": str(tradingcodex_home()),
        "SUBAGENT_COUNT": str(len(subagents)),
    }
    result = {
        "targetDir": str(target),
        "workspaceId": workspace_id,
        "modules": [module.id for module in modules],
        "capabilities": collect_capabilities(modules),
    }
    if dry_run:
        return result
    ensure_target_dir(target, force)
    for module in modules:
        files_dir = module.dir / "files"
        if files_dir.exists():
            copy_template_tree(files_dir, target, context)
    ensure_workspace_manifest(target, project_name=context["PROJECT_NAME"], generated_at=context["GENERATED_AT"])
    write_generated_indexes(target, modules, context)
    project_agent_configuration(target, applied_by="bootstrap", generated_at=context["GENERATED_AT"])
    write_server_status_snapshot(target)
    return result


def load_module_registry(base_templates_dir: Path) -> dict[str, Module]:
    modules_dir = base_templates_dir / "modules"
    registry: dict[str, Module] = {}
    for module_dir in sorted(path for path in modules_dir.iterdir() if path.is_dir()):
        manifest_path = module_dir / "module.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        module_id = manifest["id"]
        if module_id != module_dir.name:
            raise ValueError(f'Module id "{module_id}" does not match directory "{module_dir.name}"')
        registry[module_id] = Module(module_id, manifest.get("description", ""), module_dir, manifest)
    return registry


def resolve_module_graph(registry: dict[str, Module], requested_ids: list[str]) -> list[Module]:
    resolved: list[Module] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(module_id: str, parent_id: str | None = None) -> None:
        if module_id in seen:
            return
        if module_id in visiting:
            raise ValueError(f'Circular module dependency detected at "{module_id}"')
        if module_id not in registry:
            suffix = f' required by "{parent_id}"' if parent_id else ""
            raise ValueError(f'Unknown module "{module_id}"{suffix}')
        visiting.add(module_id)
        module = registry[module_id]
        for dependency in module.manifest.get("requires", {}).get("modules", []):
            visit(dependency, module_id)
        visiting.remove(module_id)
        seen.add(module_id)
        resolved.append(module)

    for module_id in requested_ids:
        visit(module_id)
    assert_no_conflicts(resolved)
    return resolved


def collect_capabilities(modules: list[Module]) -> list[str]:
    capabilities: set[str] = set()
    for module in modules:
        capabilities.update(module.manifest.get("provides", {}).get("capabilities", []))
    return sorted(capabilities)


def ensure_target_dir(target: Path, force: bool) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if not force and not target_has_only_bootstrap_files(target):
        raise ValueError(f"Target directory already has files: {target}. Use an empty directory, a git-initialized empty directory, or pass --overwrite to update matching generated workspace paths.")


def target_has_only_bootstrap_files(target: Path) -> bool:
    allowed_names = {".git", ".gitignore", ".gitattributes"}
    return all(child.name in allowed_names for child in target.iterdir())


def copy_template_tree(source: Path, target: Path, context: dict[str, str]) -> None:
    for item in source.iterdir():
        if item.name in {"__pycache__", ".DS_Store"} or item.suffix in {".pyc", ".pyo"}:
            continue
        destination = target / item.name
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            copy_template_tree(item, destination, context)
            continue
        if not item.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = item.read_text(encoding="utf-8")
        rendered = render_template(text, context)
        destination.write_text(rendered, encoding="utf-8")
        if rendered.startswith("#!"):
            destination.chmod(0o755)


def write_generated_indexes(target: Path, modules: list[Module], context: dict[str, str]) -> None:
    generated_dir = target / ".tradingcodex" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    lock = {
        "generated_at": context["GENERATED_AT"],
        "tradingcodex_version": context["TRADINGCODEX_VERSION"],
        "tradingcodex_package_spec": context["TRADINGCODEX_MCP_PACKAGE_SPEC"],
        "tradingcodex_home": context["TRADINGCODEX_HOME"],
        "modules": [
            {
                "id": module.id,
                "description": module.description,
                "capabilities": module.manifest.get("provides", {}).get("capabilities", []),
            }
            for module in modules
        ],
    }
    capability_index = {
        "generated_at": context["GENERATED_AT"],
        "capabilities": collect_capabilities(modules),
    }
    component_index = {
        "generated_at": context["GENERATED_AT"],
        "source": "tradingcodex_service.application.components",
        "components": list_harness_components(),
    }
    (generated_dir / "module-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    (generated_dir / "capability-index.json").write_text(json.dumps(capability_index, indent=2) + "\n", encoding="utf-8")
    (generated_dir / "component-index.json").write_text(json.dumps(component_index, indent=2) + "\n", encoding="utf-8")


def collect_template_subagent_names(modules: list[Module]) -> list[str]:
    names: list[str] = []
    for module in modules:
        agents_dir = module.dir / "files" / ".codex" / "agents"
        if not agents_dir.exists():
            continue
        for file_path in sorted(agents_dir.glob("*.toml")):
            text = file_path.read_text(encoding="utf-8")
            name = file_path.stem
            for line in text.splitlines():
                if line.startswith("name = "):
                    name = line.split('"')[1]
                    break
            names.append(name)
    return sorted(names)


def assert_no_conflicts(modules: list[Module]) -> None:
    ids = {module.id for module in modules}
    for module in modules:
        for conflict in module.manifest.get("conflicts", []):
            if conflict in ids:
                raise ValueError(f'Module "{module.id}" conflicts with "{conflict}"')


def render_template(source: str, context: dict[str, str]) -> str:
    rendered = source
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def sanitize_project_name(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch in "._-" else "-" for ch in name)
    return cleaned.strip("-") or "tradingcodex-workspace"


def reset_tmp_generated_workspaces(repo: Path | None = None) -> None:
    tmp = (repo or repo_root()) / "tmp"
    for child in ["smoke", "dry-run-smoke", "non-empty-smoke", "scenario-quality", "external-data", "quality-scenarios-20"]:
        shutil.rmtree(tmp / child, ignore_errors=True)
