from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

import yaml
from packaging.version import Version

from tradingcodex_service.application.common import (
    exclusive_file_lock,
    file_hash,
    now_iso,
    read_json,
    write_json,
)
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter
from tradingcodex_service.application.git_subprocess import (
    isolated_git_command,
    isolated_git_environment,
)


BRAIN_ID_PREFIX = "investment-brain-"
BRAIN_ID_PATTERN = re.compile(r"^investment-brain-[a-z0-9]+(?:-[a-z0-9]+)*$")
BRAIN_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
BRAIN_PLUGIN_FORMAT = "tradingcodex.investment-brain"
BRAIN_PLUGIN_TYPE = "investment-brain"
BRAIN_PLUGIN_SCHEMA_VERSION = 1
BRAIN_REGISTRY_FORMAT = "tradingcodex.investment-brain-registry"
BRAIN_REGISTRY_SCHEMA_VERSION = 1
BRAIN_REGISTRY_DIR = Path(".tradingcodex/investment-brains")
BRAIN_REGISTRY_PATH = BRAIN_REGISTRY_DIR / "registry.json"
BRAIN_PACKAGE_DIR = BRAIN_REGISTRY_DIR / "packages"
BRAIN_PROJECTION_DIR = Path(".agents/skills")
BRAIN_MANIFEST_PATH = Path(".tradingcodex/plugin.json")
BRAIN_SKILL_PATH = Path("skill")
BRAIN_STATUSES = {"active", "inactive", "removed"}
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SKILL_BYTES = 256 * 1024
MAX_METADATA_BYTES = 64 * 1024
MAX_REFERENCE_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024
MAX_REFERENCE_FILES = 64
MAX_REFERENCE_DIRECTORIES = 32
MAX_REFERENCE_ENTRIES = 96
MAX_REFERENCE_DEPTH = 4
MAX_GIT_TREE_OUTPUT_BYTES = 256 * 1024

_PLUGIN_FIELDS = {"format", "schema_version", "type", "id", "version", "skill", "source"}
_DECLARED_SOURCE_FIELDS = {"publisher", "repository", "license"}
_SKILL_TOP_LEVEL_ENTRIES = {"SKILL.md", "agents", "references"}
_RESERVED_RUNTIME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "TradingCodex runtime surface",
        re.compile(
            r"\bTradingCodex\b|\.tradingcodex(?:/|\\)|\.codex(?:/|\\)|\.agents(?:/|\\)|"
            r"(?<![\w.-])(?:\./)?tcx(?:\.cmd)?(?:\s|$)",
            re.I,
        ),
    ),
    (
        "fixed role identifier",
        re.compile(
            r"(?<![A-Za-z0-9_-])(?:head-manager|"
            r"fundamental-analyst|technical-analyst|news-analyst|macro-analyst|"
            r"instrument-analyst|valuation-analyst|portfolio-manager|risk-manager|"
            r"judgment-reviewer|execution-operator)(?![A-Za-z0-9_-])",
            re.I,
        ),
    ),
    (
        "orchestration or tool identifier",
        re.compile(
            r"(?<![A-Za-z0-9_])(?:agent_type|fork_turns|spawn_agent|followup_task|"
            r"workflow_run_id|begin_analysis_run|create_research_artifact|"
            r"model_reasoning_effort|sandbox_mode|approval_policy)(?![A-Za-z0-9_])|"
            r"(?<![A-Za-z0-9_])mcp__[A-Za-z0-9_-]+",
            re.I,
        ),
    ),
)


@dataclass(frozen=True)
class ValidatedBrainBundle:
    root: Path
    manifest: dict[str, Any]
    content_digest: str
    skill_digest: str
    description: str
    display_name: str
    files: tuple[tuple[str, bytes], ...]


def validate_investment_brain_source(
    workspace_root: Path | str,
    *,
    local_source: Path | str | None = None,
    git_source: str | None = None,
    ref: str = "",
) -> dict[str, Any]:
    """Validate one explicit source without changing registry or projection state."""

    root = Path(workspace_root).resolve()
    with _materialized_source(
        local_source=local_source,
        git_source=git_source,
        ref=ref,
    ) as (source_root, source):
        _reject_managed_source(root, source_root)
        bundle = validate_investment_brain_bundle(source_root)
        brain_id = str(bundle.manifest["id"])
        _assert_no_host_global_brain_collision(root, brain_id)
        return {
            "status": "valid",
            "brain_id": brain_id,
            "version": str(bundle.manifest["version"]),
            "content_digest": bundle.content_digest,
            "skill_digest": bundle.skill_digest,
            "display_name": bundle.display_name,
            "description": bundle.description,
            "source": _tracked_source_metadata(
                root,
                source_root,
                source,
                dict(bundle.manifest["source"]),
            ),
            "file_count": len(bundle.files),
            "total_bytes": sum(len(content) for _, content in bundle.files),
            "registry_mutated": False,
            "projection_mutated": False,
        }


def install_investment_brain(
    workspace_root: Path | str,
    *,
    local_source: Path | str | None = None,
    git_source: str | None = None,
    ref: str = "",
    active: bool = True,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    with _materialized_source(local_source=local_source, git_source=git_source, ref=ref) as (source_root, source):
        _reject_managed_source(root, source_root)
        bundle = validate_investment_brain_bundle(source_root)
        _assert_no_host_global_brain_collision(root, str(bundle.manifest["id"]))
        source = _tracked_source_metadata(
            root,
            source_root,
            source,
            dict(bundle.manifest["source"]),
        )
        return _install_validated_bundle(root, bundle, source, active=active, actor=actor, update=False)


def update_investment_brain(
    workspace_root: Path | str,
    brain_id: str,
    *,
    local_source: Path | str | None = None,
    git_source: str | None = None,
    ref: str | None = None,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    brain_id = normalize_investment_brain_id(brain_id)
    current = get_investment_brain_record(root, brain_id)
    selected = next(
        item for item in current["versions"] if item["content_digest"] == current["content_digest"]
    )
    selected_source = dict(selected["source"])
    if local_source is None and git_source is None:
        kind = selected_source.get("kind")
        location = str(selected_source.get("location") or "")
        if kind == "local":
            if not location:
                raise ValueError(
                    "investment brain update requires an explicit --local source because the "
                    "installed local source is outside this workspace"
                )
            local_source = _resolve_workspace_source_locator(root, location)
        elif kind == "git":
            if not location:
                raise ValueError(
                    "investment brain update requires an explicit --git source because the "
                    "installed Git source is a local path outside this workspace"
                )
            git_source = (
                location
                if _is_remote_git_location(location)
                else str(_resolve_workspace_source_locator(root, location))
            )
            if ref is None:
                ref = str(selected_source.get("ref") or "")
        else:
            raise ValueError(f"investment brain update source is unavailable: {brain_id}")
    with _materialized_source(
        local_source=local_source,
        git_source=git_source,
        ref=str(ref or ""),
    ) as (source_root, source):
        _reject_managed_source(root, source_root)
        bundle = validate_investment_brain_bundle(source_root)
        if bundle.manifest["id"] != brain_id:
            raise ValueError("investment brain update id does not match the installed plugin")
        _assert_no_host_global_brain_collision(root, brain_id)
        source = _tracked_source_metadata(
            root,
            source_root,
            source,
            dict(bundle.manifest["source"]),
        )
        return _install_validated_bundle(
            root,
            bundle,
            source,
            active=current["status"] == "active",
            actor=actor,
            update=True,
        )


def rollback_investment_brain(
    workspace_root: Path | str,
    brain_id: str,
    *,
    version: str = "",
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    brain_id = normalize_investment_brain_id(brain_id)

    def mutate(registry: dict[str, Any]) -> None:
        plugin = _registry_plugin(registry, brain_id)
        versions = list(plugin["versions"])
        current = _selected_version(plugin)
        current_version = Version(str(current["version"]))
        if version:
            target = next((item for item in versions if item["version"] == version), None)
            if target is None:
                raise ValueError(f"investment brain version is not installed: {brain_id}@{version}")
        else:
            lower_versions = [
                item
                for item in versions
                if Version(str(item["version"])) < current_version
            ]
            if not lower_versions:
                raise ValueError(f"investment brain has no earlier installed version: {brain_id}")
            target = max(lower_versions, key=lambda item: Version(str(item["version"])))
        plugin["selected_digest"] = target["content_digest"]
        plugin["updated_at"] = now_iso()
        plugin["updated_by"] = actor

    _mutate_registry_and_project(root, mutate)
    return get_investment_brain_record(root, brain_id)


def set_investment_brain_status(
    workspace_root: Path | str,
    brain_id: str,
    status: str,
    *,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    brain_id = normalize_investment_brain_id(brain_id)
    if status not in {"active", "inactive"}:
        raise ValueError(f"unsupported investment brain status: {status}")

    def mutate(registry: dict[str, Any]) -> None:
        plugin = _registry_plugin(registry, brain_id)
        if status == "active":
            _assert_no_host_global_brain_collision(root, brain_id)
            selected = _selected_version(plugin)
            _validate_installed_version(root, plugin, selected)
        plugin["status"] = status
        plugin["updated_at"] = now_iso()
        plugin["updated_by"] = actor

    _mutate_registry_and_project(root, mutate)
    return get_investment_brain_record(root, brain_id)


def remove_investment_brain(
    workspace_root: Path | str,
    brain_id: str,
    *,
    actor: str = "local",
) -> dict[str, Any]:
    """Remove the active projection while retaining immutable versions for provenance."""

    root = Path(workspace_root).resolve()
    brain_id = normalize_investment_brain_id(brain_id)

    def mutate(registry: dict[str, Any]) -> None:
        plugin = _registry_plugin(registry, brain_id)
        plugin["status"] = "removed"
        plugin["updated_at"] = now_iso()
        plugin["updated_by"] = actor

    _mutate_registry_and_project(root, mutate)
    return get_investment_brain_record(root, brain_id)


def read_investment_brain_records(
    workspace_root: Path | str,
    *,
    include_removed: bool = True,
) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve()
    registry = _read_registry(root)
    records: list[dict[str, Any]] = []
    for brain_id in sorted(registry["plugins"]):
        plugin = dict(registry["plugins"][brain_id])
        if not include_removed and plugin["status"] == "removed":
            continue
        records.append(_brain_record(root, plugin))
    return records


def get_investment_brain_record(workspace_root: Path | str, brain_id: str) -> dict[str, Any]:
    brain_id = normalize_investment_brain_id(brain_id)
    for record in read_investment_brain_records(workspace_root, include_removed=True):
        if record["brain_id"] == brain_id:
            return record
    raise ValueError(f"unknown investment brain: {brain_id}")


def resolve_active_investment_brain(workspace_root: Path | str, brain_id: str) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    _assert_no_host_global_brain_collision(root, normalize_investment_brain_id(brain_id))
    record = get_investment_brain_record(root, brain_id)
    if record["status"] != "active":
        raise ValueError(f"investment brain is not active: {record['brain_id']}")
    if record["validation_status"] != "valid":
        raise ValueError(
            f"investment brain is invalid: {record['brain_id']}: "
            + "; ".join(record["validation_errors"])
        )
    projection = root / record["projected_skill_path"]
    _assert_managed_workspace_path(root, projection)
    if not projection.is_dir() or _skill_tree_digest(projection) != record["skill_digest"]:
        raise ValueError(f"investment brain projected digest mismatch: {record['brain_id']}")
    return {
        "brain_id": record["brain_id"],
        "version": record["version"],
        "content_digest": record["content_digest"],
        "skill_digest": record["skill_digest"],
        "source": dict(record["source"]),
        "manifest_path": record["manifest_path"],
        "source_file": record["source_file"],
        "projected_skill_path": record["projected_skill_path"],
        "validation_status": record["validation_status"],
        "status": record["status"],
    }


def resolve_sealed_investment_brain_reference(
    workspace_root: Path | str,
    binding: dict[str, Any],
    reference_path: str,
) -> Path:
    """Resolve one Markdown reference from the exact Brain projection sealed to a run."""

    root = Path(workspace_root).resolve()
    if not isinstance(binding, dict):
        raise ValueError("analysis run Investment Brain binding is unavailable")
    brain_id = str(binding.get("brain_id") or "")
    if not BRAIN_ID_PATTERN.fullmatch(brain_id):
        raise ValueError("analysis run does not seal an Investment Brain")
    if not str(binding.get("version") or ""):
        raise ValueError("analysis run Investment Brain version is unavailable")
    if not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("content_digest") or "")):
        raise ValueError("analysis run Investment Brain content digest is invalid")
    skill_digest = str(binding.get("skill_digest") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", skill_digest):
        raise ValueError("analysis run Investment Brain skill digest is invalid")

    projected_relative = BRAIN_PROJECTION_DIR / brain_id
    if str(binding.get("projected_skill_path") or "") != projected_relative.as_posix():
        raise ValueError("analysis run Investment Brain projection path is invalid")
    projection = root / projected_relative
    _assert_managed_workspace_path(root, projection)
    if not projection.is_dir() or _skill_tree_digest(projection) != skill_digest:
        raise ValueError("analysis run Investment Brain projection no longer matches its sealed digest")

    reference_path = str(reference_path or "").strip()
    _validate_snapshot_path(reference_path)
    relative = Path(reference_path)
    references_root = projected_relative / "references"
    try:
        nested_reference = relative.relative_to(references_root)
    except ValueError as exc:
        raise ValueError("Investment Brain runtime reads are limited to its references directory") from exc
    if nested_reference == Path() or relative.suffix.lower() != ".md":
        raise ValueError("Investment Brain runtime references must be Markdown files")

    reference = root / relative
    _assert_managed_workspace_path(root, reference)
    _read_bounded_regular_bytes(
        reference,
        MAX_REFERENCE_BYTES,
        f"sealed reference {nested_reference.as_posix()}",
    )
    return reference


def investment_brain_skill_index_records(workspace_root: Path | str) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve()
    records: list[dict[str, Any]] = []
    for record in read_investment_brain_records(root, include_removed=True):
        active = record["status"] == "active" and record["validation_status"] == "valid"
        projected_dir = root / record["projected_skill_path"]
        package_source = root / record["source_file"]
        source_file = projected_dir / "SKILL.md" if active else package_source
        metadata_file = source_file.parent / "agents" / "openai.yaml"
        if record["validation_status"] != "valid":
            source_file = None
            metadata_file = None
        records.append(
            {
                "id": record["brain_id"],
                "name": record["brain_id"],
                "label": record["display_name"],
                "description": record["description"],
                "owner_roles": ["head-manager"],
                "risk_tags": ["investment-brain"],
                "user_visible": active,
                "source": "investment-brain",
                "layer": "workspace_investment_brain",
                "trust_scope": "explicit_user_install",
                "scope": "head-manager",
                "core": False,
                "implicit_invocation": False,
                "status": record["status"],
                "active": active,
                "installed": True,
                "source_file": _relative(root, source_file) if source_file else "",
                "resolved_source_file": _relative(root, source_file) if source_file else "",
                "source_file_hash": file_hash(source_file),
                "metadata_file": _relative(root, metadata_file) if metadata_file else "",
                "metadata_file_hash": file_hash(metadata_file),
                "package_source_file": record["source_file"],
                "manifest_path": record["manifest_path"],
                "version": record["version"],
                "content_digest": record["content_digest"],
                "validation_status": record["validation_status"],
                "validation_errors": list(record["validation_errors"]),
            }
        )
    return records


def project_investment_brain_skills(workspace_root: Path | str) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve()
    registry = _read_registry(root)
    selected: list[tuple[str, str, tuple[tuple[str, bytes], ...]]] = []
    for brain_id in sorted(registry["plugins"]):
        plugin = registry["plugins"][brain_id]
        if plugin["status"] != "active":
            continue
        _assert_no_host_global_brain_collision(root, brain_id)
        version = _selected_version(plugin)
        bundle = _validate_installed_version(root, plugin, version)
        skill_files = tuple(
            (path.removeprefix("skill/"), content)
            for path, content in bundle.files
            if path.startswith("skill/")
        )
        selected.append((brain_id, str(version["skill_digest"]), skill_files))

    active_ids = {brain_id for brain_id, _, _ in selected}
    registered_ids = set(registry["plugins"])
    projection_root = root / BRAIN_PROJECTION_DIR
    _assert_managed_workspace_path(root, projection_root)
    if projection_root.exists():
        for path in sorted(projection_root.glob(f"{BRAIN_ID_PREFIX}*")):
            if path.name not in registered_ids:
                raise ValueError(f"unregistered skill uses the reserved investment-brain- namespace: {path.name}")
            if path.name not in active_ids:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)

    for brain_id, skill_digest, skill_files in selected:
        target = root / BRAIN_PROJECTION_DIR / brain_id
        _assert_managed_workspace_path(root, target)
        if target.is_dir() and not target.is_symlink() and _skill_tree_digest(target) == skill_digest:
            continue
        _replace_skill_snapshot(skill_files, target)
    return read_investment_brain_records(root, include_removed=True)


def validate_investment_brain_bundle(bundle_root: Path | str) -> ValidatedBrainBundle:
    unresolved_root = Path(bundle_root).expanduser()
    if unresolved_root.is_symlink():
        raise ValueError("investment brain bundle root cannot be a symlink")
    root = unresolved_root.resolve()
    if not root.is_dir():
        raise ValueError(f"investment brain source is not a directory: {root}")
    files = _capture_bundle_files(root)
    manifest_path = BRAIN_MANIFEST_PATH.as_posix()
    try:
        manifest = json.loads(_decode_utf8(files[manifest_path], "manifest"))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"investment brain manifest is invalid: {root / BRAIN_MANIFEST_PATH}") from exc
    _validate_manifest(manifest)

    text = _decode_utf8(files["skill/SKILL.md"], "SKILL.md")
    _reject_yaml_graph_features(_frontmatter_source(text), "SKILL.md frontmatter")
    try:
        document = split_markdown_frontmatter(text)
    except RecursionError as exc:
        raise ValueError("investment brain SKILL.md frontmatter is too deeply nested") from exc
    if set(document.frontmatter) != {"name", "description"}:
        raise ValueError("investment brain SKILL.md frontmatter must contain only name and description")
    if document.frontmatter["name"] != manifest["id"]:
        raise ValueError("investment brain SKILL.md name must match the manifest id")
    description = document.frontmatter["description"]
    if not isinstance(description, str) or not description.strip() or description != description.strip():
        raise ValueError("investment brain SKILL.md description must be a non-empty string")
    if not document.body.strip():
        raise ValueError("investment brain SKILL.md body must not be empty")
    if len(text.splitlines()) > 500:
        raise ValueError("investment brain SKILL.md must not exceed 500 lines")

    metadata_text = _decode_utf8(files["skill/agents/openai.yaml"], "openai.yaml")
    display_name = _validate_openai_metadata(metadata_text, str(manifest["id"]))
    _validate_platform_neutral_text(files, str(manifest["id"]))
    frozen_files = tuple(sorted(files.items()))
    return ValidatedBrainBundle(
        root=root,
        manifest=manifest,
        content_digest=_snapshot_digest(frozen_files),
        skill_digest=_snapshot_digest(
            tuple(
                (path.removeprefix("skill/"), content)
                for path, content in frozen_files
                if path.startswith("skill/")
            )
        ),
        description=description,
        display_name=display_name,
        files=frozen_files,
    )


def normalize_investment_brain_id(value: str) -> str:
    brain_id = str(value or "").strip()
    if not BRAIN_ID_PATTERN.fullmatch(brain_id) or len(brain_id) > 64:
        raise ValueError("investment brain id must use investment-brain-* lowercase hyphen-case")
    return brain_id


def _install_validated_bundle(
    root: Path,
    bundle: ValidatedBrainBundle,
    source: dict[str, Any],
    *,
    active: bool,
    actor: str,
    update: bool,
) -> dict[str, Any]:
    brain_id = str(bundle.manifest["id"])
    package_rel = BRAIN_PACKAGE_DIR / brain_id / f"{bundle.manifest['version']}-{bundle.content_digest[:12]}"
    package_path = root / package_rel
    _assert_managed_workspace_path(root, package_path)
    created_package = False
    with exclusive_file_lock(root / BRAIN_REGISTRY_PATH):
        registry = _read_registry(root)
        previous_registry = json.loads(json.dumps(registry))
        existing = registry["plugins"].get(brain_id)
        projection_target = root / BRAIN_PROJECTION_DIR / brain_id
        if existing is None and (projection_target.exists() or projection_target.is_symlink()):
            raise ValueError(f"reserved investment brain projection path already exists: {projection_target}")
        if existing and not update:
            selected = _selected_version(existing)
            if selected["content_digest"] != bundle.content_digest:
                raise ValueError(f"investment brain is already installed; use update: {brain_id}")
        if update and not existing:
            raise ValueError(f"unknown investment brain: {brain_id}")
        if existing:
            same_version = next(
                (item for item in existing["versions"] if item["version"] == bundle.manifest["version"]),
                None,
            )
            if same_version and same_version["content_digest"] != bundle.content_digest:
                raise ValueError("investment brain version content is immutable; publish a new version")
            selected = _selected_version(existing)
            if update and bundle.content_digest != selected["content_digest"]:
                highest_installed = max(Version(str(item["version"])) for item in existing["versions"])
                if Version(str(bundle.manifest["version"])) <= highest_installed:
                    raise ValueError(
                        "investment brain update must publish a version higher than every installed version; "
                        "use rollback for older versions"
                    )

        if not package_path.exists():
            _copy_validated_bundle(bundle, package_path)
            created_package = True
        installed_bundle = validate_investment_brain_bundle(package_path)
        if installed_bundle.content_digest != bundle.content_digest:
            raise ValueError("installed investment brain content digest mismatch")
        timestamp = now_iso()
        version_record = {
            "version": bundle.manifest["version"],
            "content_digest": bundle.content_digest,
            "skill_digest": bundle.skill_digest,
            "source": source,
            "package_path": package_rel.as_posix(),
            "manifest_path": (package_rel / BRAIN_MANIFEST_PATH).as_posix(),
            "source_file": (package_rel / BRAIN_SKILL_PATH / "SKILL.md").as_posix(),
            "metadata_file": (package_rel / BRAIN_SKILL_PATH / "agents" / "openai.yaml").as_posix(),
            "installed_at": timestamp,
            "installed_by": actor,
        }
        if existing:
            versions = list(existing["versions"])
            if not any(item["content_digest"] == bundle.content_digest for item in versions):
                versions.append(version_record)
            plugin = {
                **existing,
                "status": ("active" if active else "inactive") if not update else existing["status"],
                "selected_digest": bundle.content_digest,
                "versions": versions,
                "updated_at": timestamp,
                "updated_by": actor,
            }
        else:
            plugin = {
                "id": brain_id,
                "status": "active" if active else "inactive",
                "selected_digest": bundle.content_digest,
                "versions": [version_record],
                "created_at": timestamp,
                "created_by": actor,
                "updated_at": timestamp,
                "updated_by": actor,
            }
        registry["plugins"][brain_id] = plugin
        _write_registry(root, registry)
        try:
            _project_all(root, actor)
        except Exception:
            _write_registry(root, previous_registry)
            if created_package and not _package_is_referenced(previous_registry, package_rel.as_posix()):
                shutil.rmtree(package_path, ignore_errors=True)
            try:
                _project_all(root, "investment-brain-rollback")
            except Exception:
                pass
            raise
    return get_investment_brain_record(root, brain_id)


def _mutate_registry_and_project(root: Path, mutate: Any) -> None:
    _assert_managed_workspace_path(root, root / BRAIN_REGISTRY_PATH)
    with exclusive_file_lock(root / BRAIN_REGISTRY_PATH):
        registry = _read_registry(root)
        previous = json.loads(json.dumps(registry))
        mutate(registry)
        _write_registry(root, registry)
        try:
            _project_all(root, "investment-brain-registry")
        except Exception:
            _write_registry(root, previous)
            try:
                _project_all(root, "investment-brain-rollback")
            except Exception:
                pass
            raise


def _project_all(root: Path, actor: str) -> None:
    from tradingcodex_service.application.agents import project_agent_configuration

    project_agent_configuration(root, applied_by=actor)


def _brain_record(root: Path, plugin: dict[str, Any]) -> dict[str, Any]:
    selected = _selected_version(plugin)
    errors: list[str] = []
    try:
        bundle = _validate_installed_version(root, plugin, selected)
    except ValueError as exc:
        errors.append(str(exc))
        bundle = None
    collisions = _host_global_brain_collisions(root, str(plugin["id"]))
    if collisions:
        errors.append(_host_global_collision_message(str(plugin["id"]), collisions))
    projected_path = BRAIN_PROJECTION_DIR / plugin["id"]
    if plugin["status"] == "active" and bundle is not None:
        projection = root / projected_path
        try:
            _assert_managed_workspace_path(root, projection)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if not projection.is_dir() or projection.is_symlink():
                errors.append("active investment brain projection is missing")
            elif _skill_tree_digest(projection) != selected["skill_digest"]:
                errors.append("active investment brain projected digest mismatch")
    return {
        "brain_id": plugin["id"],
        "version": selected["version"],
        "content_digest": selected["content_digest"],
        "skill_digest": selected["skill_digest"],
        "source": dict(selected["source"]),
        "manifest_path": selected["manifest_path"],
        "source_file": selected["source_file"],
        "metadata_file": selected["metadata_file"],
        "projected_skill_path": projected_path.as_posix(),
        "validation_status": "blocked" if errors else "valid",
        "validation_errors": errors,
        "status": plugin["status"],
        "active": plugin["status"] == "active" and not errors,
        "description": bundle.description if bundle else "",
        "display_name": bundle.display_name if bundle else plugin["id"],
        "created_at": plugin["created_at"],
        "created_by": plugin["created_by"],
        "updated_at": plugin["updated_at"],
        "updated_by": plugin["updated_by"],
        "versions": list(plugin["versions"]),
    }


def _validate_installed_version(
    root: Path,
    plugin: dict[str, Any],
    selected: dict[str, Any],
) -> ValidatedBrainBundle:
    package_path = root / str(selected["package_path"])
    _assert_managed_workspace_path(root, package_path)
    bundle = validate_investment_brain_bundle(package_path)
    if bundle.manifest["id"] != plugin["id"] or bundle.manifest["version"] != selected["version"]:
        raise ValueError("installed investment brain manifest does not match its registry record")
    if bundle.content_digest != selected["content_digest"] or bundle.skill_digest != selected["skill_digest"]:
        raise ValueError("installed investment brain content digest mismatch")
    if dict(bundle.manifest["source"]) != dict(selected["source"].get("declared") or {}):
        raise ValueError("installed investment brain declared source metadata mismatch")
    return bundle


def _selected_version(plugin: dict[str, Any]) -> dict[str, Any]:
    selected = next(
        (item for item in plugin.get("versions", []) if item.get("content_digest") == plugin.get("selected_digest")),
        None,
    )
    if selected is None:
        raise ValueError(f"investment brain selected version is unavailable: {plugin.get('id', '')}")
    return selected


def _registry_plugin(registry: dict[str, Any], brain_id: str) -> dict[str, Any]:
    plugin = registry["plugins"].get(brain_id)
    if plugin is None:
        raise ValueError(f"unknown investment brain: {brain_id}")
    return plugin


def _read_registry(root: Path) -> dict[str, Any]:
    path = root / BRAIN_REGISTRY_PATH
    _assert_managed_workspace_path(root, path)
    value = read_json(path, None)
    if value is None:
        return {
            "format": BRAIN_REGISTRY_FORMAT,
            "schema_version": BRAIN_REGISTRY_SCHEMA_VERSION,
            "plugins": {},
        }
    if not isinstance(value, dict) or set(value) != {"format", "schema_version", "plugins"}:
        raise ValueError(f"investment brain registry schema is invalid: {path}")
    if value["format"] != BRAIN_REGISTRY_FORMAT or value["schema_version"] != BRAIN_REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported investment brain registry: {path}")
    if not isinstance(value["plugins"], dict):
        raise ValueError(f"investment brain registry plugins must be an object: {path}")
    for brain_id, plugin in value["plugins"].items():
        normalize_investment_brain_id(brain_id)
        _validate_registry_plugin(brain_id, plugin)
    return value


def _validate_registry_plugin(brain_id: str, plugin: Any) -> None:
    required = {
        "id",
        "status",
        "selected_digest",
        "versions",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    }
    if not isinstance(plugin, dict) or set(plugin) != required:
        raise ValueError(f"investment brain registry entry is invalid: {brain_id}")
    if plugin["id"] != brain_id or plugin["status"] not in BRAIN_STATUSES:
        raise ValueError(f"investment brain registry identity or status is invalid: {brain_id}")
    if not isinstance(plugin["versions"], list) or not plugin["versions"]:
        raise ValueError(f"investment brain registry versions are invalid: {brain_id}")
    versions_seen: dict[str, str] = {}
    digests: set[str] = set()
    version_fields = {
        "version",
        "content_digest",
        "skill_digest",
        "source",
        "package_path",
        "manifest_path",
        "source_file",
        "metadata_file",
        "installed_at",
        "installed_by",
    }
    for item in plugin["versions"]:
        if not isinstance(item, dict) or set(item) != version_fields:
            raise ValueError(f"investment brain version record is invalid: {brain_id}")
        if not BRAIN_VERSION_PATTERN.fullmatch(str(item["version"])):
            raise ValueError(f"investment brain version is invalid: {brain_id}")
        for digest_field in ("content_digest", "skill_digest"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(item[digest_field])):
                raise ValueError(f"investment brain digest is invalid: {brain_id}")
        previous = versions_seen.setdefault(item["version"], item["content_digest"])
        if previous != item["content_digest"]:
            raise ValueError(f"investment brain version is not immutable: {brain_id}@{item['version']}")
        digests.add(item["content_digest"])
        if not isinstance(item["source"], dict):
            raise ValueError(f"investment brain source metadata is invalid: {brain_id}")
        expected_package = (
            BRAIN_PACKAGE_DIR
            / brain_id
            / f"{item['version']}-{item['content_digest'][:12]}"
        )
        expected_paths = {
            "package_path": expected_package.as_posix(),
            "manifest_path": (expected_package / BRAIN_MANIFEST_PATH).as_posix(),
            "source_file": (expected_package / BRAIN_SKILL_PATH / "SKILL.md").as_posix(),
            "metadata_file": (expected_package / BRAIN_SKILL_PATH / "agents" / "openai.yaml").as_posix(),
        }
        if any(item[field] != expected for field, expected in expected_paths.items()):
            raise ValueError(f"investment brain package paths are invalid: {brain_id}@{item['version']}")
        _validate_recorded_source(item["source"], brain_id)
    if plugin["selected_digest"] not in digests:
        raise ValueError(f"investment brain selected digest is invalid: {brain_id}")


def _write_registry(root: Path, registry: dict[str, Any]) -> None:
    path = root / BRAIN_REGISTRY_PATH
    _assert_managed_workspace_path(root, path)
    write_json(path, registry)


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict) or set(manifest) != _PLUGIN_FIELDS:
        raise ValueError("investment brain manifest fields do not match the v1 schema")
    if manifest["format"] != BRAIN_PLUGIN_FORMAT:
        raise ValueError("investment brain manifest format is invalid")
    if manifest["schema_version"] != BRAIN_PLUGIN_SCHEMA_VERSION or type(manifest["schema_version"]) is not int:
        raise ValueError("investment brain manifest schema_version is invalid")
    if manifest["type"] != BRAIN_PLUGIN_TYPE:
        raise ValueError("investment brain manifest type is invalid")
    normalize_investment_brain_id(str(manifest["id"]))
    version = str(manifest["version"])
    if not BRAIN_VERSION_PATTERN.fullmatch(version):
        raise ValueError("investment brain version must use stable major.minor.patch syntax")
    if manifest["skill"] != BRAIN_SKILL_PATH.as_posix():
        raise ValueError("investment brain manifest skill must be the fixed skill directory")
    source = manifest["source"]
    if not isinstance(source, dict) or not {"publisher", "license"}.issubset(source):
        raise ValueError("investment brain manifest source requires publisher and license")
    if not set(source).issubset(_DECLARED_SOURCE_FIELDS):
        raise ValueError("investment brain manifest source fields do not match the v1 schema")
    for field, value in source.items():
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"investment brain manifest source {field} must be a non-empty string")
    if source.get("repository"):
        _validate_public_repository(str(source["repository"]))


def _capture_bundle_files(root: Path) -> dict[str, bytes]:
    root_entries = _bounded_directory_entries(root, maximum=4, label="bundle root")
    allowed_root_entries = {BRAIN_MANIFEST_PATH.parts[0], BRAIN_SKILL_PATH.name, ".git"}
    if not {BRAIN_MANIFEST_PATH.parts[0], BRAIN_SKILL_PATH.name}.issubset(root_entries):
        raise ValueError("investment brain bundle root must contain .tradingcodex and skill")
    unexpected = set(root_entries) - allowed_root_entries
    if unexpected:
        raise ValueError("investment brain bundle root contains unsupported entries")
    for required in (BRAIN_MANIFEST_PATH.parts[0], BRAIN_SKILL_PATH.name):
        if not stat.S_ISDIR(root_entries[required]):
            raise ValueError(f"investment brain {required} entry must be a directory")

    manifest_dir = root / BRAIN_MANIFEST_PATH.parent
    manifest_entries = _bounded_directory_entries(
        manifest_dir,
        maximum=2,
        label=".tradingcodex directory",
    )
    if set(manifest_entries) != {BRAIN_MANIFEST_PATH.name}:
        raise ValueError("investment brain .tradingcodex directory must contain only a regular plugin.json")
    manifest = _read_bounded_regular_bytes(
        root / BRAIN_MANIFEST_PATH,
        MAX_MANIFEST_BYTES,
        "manifest",
    )
    files = {BRAIN_MANIFEST_PATH.as_posix(): manifest}
    files.update(
        {
            f"{BRAIN_SKILL_PATH.as_posix()}/{relative}": content
            for relative, content in _capture_skill_files(
                root / BRAIN_SKILL_PATH,
                byte_budget=MAX_BUNDLE_BYTES - len(manifest),
            ).items()
        }
    )
    if sum(len(content) for content in files.values()) > MAX_BUNDLE_BYTES:
        raise ValueError("investment brain bundle exceeds the total size limit")
    return files


def _capture_skill_files(
    skill_root: Path,
    *,
    byte_budget: int = MAX_BUNDLE_BYTES,
) -> dict[str, bytes]:
    entries = _bounded_directory_entries(skill_root, maximum=4, label="skill directory")
    if not {"SKILL.md", "agents"}.issubset(entries) or not set(entries).issubset(
        _SKILL_TOP_LEVEL_ENTRIES
    ):
        raise ValueError(
            "investment brain skill may contain only SKILL.md, agents/openai.yaml, and references/*.md"
        )
    if not stat.S_ISREG(entries["SKILL.md"]) or not stat.S_ISDIR(entries["agents"]):
        raise ValueError("investment brain SKILL.md and agents entries have invalid types")

    agents = skill_root / "agents"
    agent_entries = _bounded_directory_entries(agents, maximum=2, label="agents directory")
    if set(agent_entries) != {"openai.yaml"} or not stat.S_ISREG(agent_entries["openai.yaml"]):
        raise ValueError("investment brain agents directory must contain only regular openai.yaml")

    files = {
        "SKILL.md": _read_bounded_regular_bytes(
            skill_root / "SKILL.md",
            MAX_SKILL_BYTES,
            "SKILL.md",
        ),
        "agents/openai.yaml": _read_bounded_regular_bytes(
            agents / "openai.yaml",
            MAX_METADATA_BYTES,
            "openai.yaml",
        ),
    }
    used_bytes = sum(len(content) for content in files.values())
    if used_bytes > byte_budget:
        raise ValueError("investment brain bundle exceeds the total size limit")
    references = skill_root / "references"
    if "references" in entries:
        if not stat.S_ISDIR(entries["references"]):
            raise ValueError("investment brain references must be a directory")
        files.update(_capture_reference_files(references, byte_budget=byte_budget - used_bytes))
    return files


def _capture_reference_files(references: Path, *, byte_budget: int) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    entry_count = 0
    directory_count = 0
    total_bytes = 0

    def walk(directory: Path, relative: Path, depth: int) -> None:
        nonlocal directory_count, entry_count, total_bytes
        remaining = MAX_REFERENCE_ENTRIES - entry_count
        entries = _bounded_directory_entries(
            directory,
            maximum=max(1, remaining + 1),
            label="references directory",
        )
        for name, mode in sorted(entries.items()):
            entry_count += 1
            if entry_count > MAX_REFERENCE_ENTRIES:
                raise ValueError("investment brain references contain too many entries")
            child_depth = depth + 1
            if child_depth > MAX_REFERENCE_DEPTH:
                raise ValueError("investment brain references exceed the maximum directory depth")
            child = directory / name
            child_relative = relative / name
            if stat.S_ISDIR(mode):
                directory_count += 1
                if directory_count > MAX_REFERENCE_DIRECTORIES:
                    raise ValueError("investment brain references contain too many directories")
                walk(child, child_relative, child_depth)
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("investment brain references must contain regular files only")
            if child.suffix.lower() != ".md":
                raise ValueError("investment brain references may contain Markdown files only")
            if len(files) >= MAX_REFERENCE_FILES:
                raise ValueError("investment brain references contain too many files")
            relative_name = child_relative.as_posix()
            _validate_snapshot_path(relative_name)
            content = _read_bounded_regular_bytes(
                child,
                MAX_REFERENCE_BYTES,
                f"reference {relative_name}",
            )
            total_bytes += len(content)
            if total_bytes > byte_budget:
                raise ValueError("investment brain bundle exceeds the total size limit")
            files[f"references/{relative_name}"] = content

    walk(references, Path(), 0)
    return files


def _bounded_directory_entries(path: Path, *, maximum: int, label: str) -> dict[str, int]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"investment brain {label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(initial.st_mode):
        raise ValueError(f"investment brain {label} must be a regular directory")
    entries: dict[str, int] = {}
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(entries) >= maximum:
                    raise ValueError(f"investment brain {label} contains too many entries")
                entry_mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(entry_mode):
                    raise ValueError("investment brain bundles cannot contain symlinks")
                entries[entry.name] = entry_mode
        final = path.lstat()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"investment brain {label} cannot be enumerated safely") from exc
    if not stat.S_ISDIR(final.st_mode) or _stat_identity(initial) != _stat_identity(final):
        raise ValueError(f"investment brain {label} changed during validation")
    return entries


def _validate_openai_metadata(text: str, brain_id: str) -> str:
    try:
        _reject_yaml_graph_features(text, "openai.yaml")
        metadata = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("investment brain openai.yaml is invalid") from exc
    if not isinstance(metadata, dict) or set(metadata) != {"interface", "policy"}:
        raise ValueError("investment brain openai.yaml fields do not match the v1 schema")
    interface = metadata["interface"]
    if not isinstance(interface, dict) or set(interface) != {
        "display_name",
        "short_description",
        "default_prompt",
    }:
        raise ValueError("investment brain openai.yaml interface does not match the v1 schema")
    for field, value in interface.items():
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"investment brain openai.yaml {field} must be a non-empty string")
    policy = metadata["policy"]
    if not isinstance(policy, dict) or set(policy) != {"allow_implicit_invocation"}:
        raise ValueError("investment brain openai.yaml policy does not match the v1 schema")
    if policy["allow_implicit_invocation"] is not False:
        raise ValueError("investment brain skills must disable implicit invocation")
    if f"${brain_id}" not in interface["default_prompt"]:
        raise ValueError("investment brain default_prompt must use the exact explicit skill id")
    return interface["display_name"]


def _validate_platform_neutral_text(files: dict[str, bytes], brain_id: str) -> None:
    documents = {
        path: content
        for path, content in files.items()
        if path == "skill/SKILL.md"
        or path == "skill/agents/openai.yaml"
        or path.startswith("skill/references/")
    }
    for path, content in sorted(documents.items()):
        relative = path.removeprefix("skill/")
        text = _decode_utf8(content, relative)
        skill_tokens = re.findall(r"\$(?:investment-brain|strategy|tcx)[a-z0-9-]*", text)
        allowed_tokens = {f"${brain_id}"} if path == "skill/agents/openai.yaml" else set()
        if set(skill_tokens) - allowed_tokens:
            raise ValueError(
                "investment brain must remain platform-neutral; prohibited TradingCodex skill invocation in "
                f"{relative}"
            )
        for label, pattern in _RESERVED_RUNTIME_PATTERNS:
            if pattern.search(text):
                raise ValueError(
                    f"investment brain must remain platform-neutral; prohibited {label} in "
                    f"{relative}"
                )


def _skill_tree_digest(skill_root: Path) -> str:
    if not skill_root.is_dir() or skill_root.is_symlink():
        return ""
    try:
        files = _capture_skill_files(skill_root)
    except ValueError:
        return ""
    return _snapshot_digest(tuple(sorted(files.items())))


def _snapshot_digest(files: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256()
    for path, data in sorted(files):
        _validate_snapshot_path(path)
        relative = path.encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _read_bounded_regular_bytes(path: Path, max_bytes: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"investment brain {label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"investment brain {label} must be a regular file")
    if os.name != "nt" and stat.S_IMODE(initial.st_mode) & 0o111:
        raise ValueError(f"investment brain {label} must not be executable")
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"investment brain {label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"investment brain {label} must be a regular file")
        if (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"investment brain {label} changed before it could be read")
        if opened.st_size > max_bytes:
            raise ValueError(f"investment brain {label} exceeds the size limit")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            content = handle.read(max_bytes + 1)
            opened_after = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > max_bytes:
        raise ValueError(f"investment brain {label} exceeds the size limit")
    try:
        final = path.lstat()
    except OSError as exc:
        raise ValueError(f"investment brain {label} changed during validation") from exc
    if _stat_identity(opened) != _stat_identity(opened_after) or (
        final.st_dev,
        final.st_ino,
    ) != (opened.st_dev, opened.st_ino):
        raise ValueError(f"investment brain {label} changed during validation")
    return content


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _decode_utf8(content: bytes, label: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"investment brain {label} must be readable UTF-8") from exc


def _validate_snapshot_path(path: str) -> None:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("investment brain bundle contains an invalid relative path")
    try:
        path.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("investment brain bundle path must be UTF-8 compatible") from exc


def _frontmatter_source(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    return ""


def _reject_yaml_graph_features(text: str, label: str) -> None:
    try:
        tokens = yaml.scan(text or "")
        for token in tokens:
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)):
                raise ValueError(f"investment brain {label} cannot use YAML aliases, anchors, or tags")
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError(f"investment brain {label} is invalid YAML") from exc


def _copy_validated_bundle(bundle: ValidatedBrainBundle, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for relative, content in bundle.files:
            _validate_snapshot_path(relative)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if target.exists() or target.is_symlink():
            raise ValueError(f"investment brain immutable package path already exists: {target}")
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _replace_skill_snapshot(
    files: tuple[tuple[str, bytes], ...],
    target: Path,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.new-", dir=target.parent))
    backup = target.with_name(f".{target.name}.old")
    for relative, content in files:
        _validate_snapshot_path(relative)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    if backup.exists():
        shutil.rmtree(backup) if backup.is_dir() else backup.unlink()
    try:
        if target.exists() or target.is_symlink():
            target.replace(backup)
        staging.replace(target)
        if backup.exists():
            shutil.rmtree(backup) if backup.is_dir() else backup.unlink()
    except Exception:
        if not target.exists() and backup.exists():
            backup.replace(target)
        shutil.rmtree(staging, ignore_errors=True)
        raise


@contextmanager
def _materialized_source(
    *,
    local_source: Path | str | None,
    git_source: str | None,
    ref: str,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    if bool(local_source) == bool(git_source):
        raise ValueError("select exactly one explicit investment brain source: local or git")
    if local_source:
        if ref:
            raise ValueError("investment brain --ref is valid only for an explicit Git source")
        path = Path(local_source).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"investment brain local source is not a directory: {path}")
        yield path, {
            "kind": "local",
            "location": str(path),
            "ref": "",
            "resolved_revision": _git_revision(path),
        }
        return

    location = str(git_source or "").strip()
    if (
        not location
        or location.startswith("-")
        or "\x00" in location
        or "\n" in location
        or "\r" in location
        or len(location) > 4096
    ):
        raise ValueError("investment brain Git source is invalid")
    _validate_git_location(location)
    if ref and (
        ref.startswith("-")
        or "\x00" in ref
        or "\n" in ref
        or "\r" in ref
        or len(ref) > 256
    ):
        raise ValueError("investment brain Git ref is invalid")
    with tempfile.TemporaryDirectory(prefix="tradingcodex-brain-git-") as temporary:
        checkout = Path(temporary) / "checkout"
        _run_git(["init", "--quiet", str(checkout)])
        _run_git(["-C", str(checkout), "remote", "add", "origin", location])
        _run_git(
            [
                "-C",
                str(checkout),
                "fetch",
                "--quiet",
                "--depth",
                "1",
                "--no-tags",
                "--filter=blob:none",
                "origin",
                ref or "HEAD",
            ]
        )
        revision = _git_revision(checkout, "FETCH_HEAD^{commit}")
        if not revision:
            raise ValueError("investment brain Git source did not resolve to a commit")
        _preflight_git_bundle(checkout)
        _run_git(
            [
                "-C",
                str(checkout),
                "checkout",
                "--quiet",
                "FETCH_HEAD",
                "--",
                BRAIN_MANIFEST_PATH.parts[0],
                BRAIN_SKILL_PATH.as_posix(),
            ]
        )
        yield checkout, {
            "kind": "git",
            "location": location,
            "ref": ref or "HEAD",
            "resolved_revision": revision,
        }


def _preflight_git_bundle(checkout: Path) -> None:
    root_records = _git_tree_records(checkout, recursive=False)
    for _, _, _, path in root_records:
        _validate_snapshot_path(path)
    root = {path: (mode, object_type) for mode, object_type, _, path in root_records}
    if len(root) != len(root_records):
        raise ValueError("investment brain Git bundle root contains duplicate entries")
    expected_root = {BRAIN_MANIFEST_PATH.parts[0], BRAIN_SKILL_PATH.as_posix()}
    if set(root) != expected_root:
        raise ValueError("investment brain Git bundle root contains unsupported entries")
    if any(root[path] != ("040000", "tree") for path in expected_root):
        raise ValueError("investment brain Git bundle root entries must be directories")

    records = _git_tree_records(checkout, recursive=True)
    required_limits = {
        BRAIN_MANIFEST_PATH.as_posix(): MAX_MANIFEST_BYTES,
        "skill/SKILL.md": MAX_SKILL_BYTES,
        "skill/agents/openai.yaml": MAX_METADATA_BYTES,
    }
    seen_required: set[str] = set()
    reference_files = 0
    reference_directories: set[str] = set()
    seen_paths: set[str] = set()
    total_bytes = 0
    for mode, object_type, size, path in records:
        _validate_snapshot_path(path)
        if path in seen_paths:
            raise ValueError("investment brain Git bundle contains duplicate paths")
        seen_paths.add(path)
        if mode != "100644" or object_type != "blob" or size is None:
            raise ValueError("investment brain Git bundle must contain non-executable regular files only")
        if path in required_limits:
            if size > required_limits[path]:
                raise ValueError(f"investment brain Git bundle file exceeds its size limit: {path}")
            seen_required.add(path)
        elif path.startswith("skill/references/"):
            relative = path.removeprefix("skill/references/")
            _validate_snapshot_path(relative)
            parts = relative.split("/")
            if len(parts) > MAX_REFERENCE_DEPTH:
                raise ValueError("investment brain Git references exceed the maximum directory depth")
            if not relative.lower().endswith(".md"):
                raise ValueError("investment brain Git references may contain Markdown files only")
            reference_files += 1
            if reference_files > MAX_REFERENCE_FILES:
                raise ValueError("investment brain Git references contain too many files")
            if size > MAX_REFERENCE_BYTES:
                raise ValueError("investment brain Git reference exceeds the size limit")
            reference_directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
        else:
            raise ValueError(f"investment brain Git bundle contains an unsupported file: {path}")
        total_bytes += size
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("investment brain Git bundle exceeds the total size limit")

    if seen_required != set(required_limits):
        raise ValueError("investment brain Git bundle is missing required files")
    if len(reference_directories) > MAX_REFERENCE_DIRECTORIES:
        raise ValueError("investment brain Git references contain too many directories")
    if reference_files + len(reference_directories) > MAX_REFERENCE_ENTRIES:
        raise ValueError("investment brain Git references contain too many entries")


def _git_tree_records(
    checkout: Path,
    *,
    recursive: bool,
) -> list[tuple[str, str, int | None, str]]:
    arguments = ["-C", str(checkout), "ls-tree"]
    if recursive:
        arguments.append("-r")
    arguments.extend(("-z", "-l", "FETCH_HEAD"))
    if recursive:
        arguments.extend(("--", BRAIN_MANIFEST_PATH.parts[0], BRAIN_SKILL_PATH.as_posix()))
    output = _run_git_bounded_output(
        arguments,
        max_bytes=MAX_GIT_TREE_OUTPUT_BYTES,
        read_only=True,
        timeout=15,
    )
    records: list[tuple[str, str, int | None, str]] = []
    for raw_record in output.split(b"\0"):
        if not raw_record:
            continue
        try:
            header, raw_path = raw_record.split(b"\t", 1)
            mode, object_type, _object_id, raw_size = header.split(b" ", 3)
            path = raw_path.decode("utf-8")
            raw_size = raw_size.strip()
            size = None if raw_size == b"-" else int(raw_size)
            records.append((mode.decode("ascii"), object_type.decode("ascii"), size, path))
        except (UnicodeError, ValueError) as exc:
            raise ValueError("investment brain Git tree metadata is invalid") from exc
    return records


def _run_git_bounded_output(
    args: list[str],
    *,
    max_bytes: int,
    read_only: bool,
    timeout: int,
) -> bytes:
    try:
        process = subprocess.Popen(
            isolated_git_command(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=isolated_git_environment(read_only=read_only),
        )
    except OSError as exc:
        raise ValueError("investment brain Git tree could not be inspected") from exc
    expired = threading.Event()

    def terminate_expired() -> None:
        if process.poll() is None:
            expired.set()
            try:
                process.kill()
            except OSError:
                pass

    timer = threading.Timer(timeout, terminate_expired)
    timer.daemon = True
    timer.start()
    try:
        if process.stdout is None:  # pragma: no cover - guaranteed by PIPE
            raise ValueError("investment brain Git tree output is unavailable")
        output = process.stdout.read(max_bytes + 1)
        overflow = len(output) > max_bytes
        if overflow:
            process.kill()
        return_code = process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        process.kill()
        process.wait()
        raise ValueError("investment brain Git tree could not be inspected") from exc
    finally:
        timer.cancel()
        if process.stdout is not None:
            process.stdout.close()
    if expired.is_set():
        raise ValueError("investment brain Git tree inspection timed out")
    if overflow:
        raise ValueError("investment brain Git tree exceeds the inspection output limit")
    if return_code != 0:
        detail = output.decode("utf-8", errors="replace").strip().splitlines()
        raise ValueError(
            "investment brain Git tree could not be inspected"
            + (f": {detail[-1]}" if detail else "")
        )
    return output


def _run_git(args: list[str]) -> None:
    try:
        _run_git_bounded_output(
            args,
            max_bytes=64 * 1024,
            read_only=False,
            timeout=120,
        )
    except ValueError as exc:
        raise ValueError("investment brain Git source could not be materialized") from exc


def _git_revision(path: Path, revision_name: str = "HEAD^{commit}") -> str:
    try:
        output = _run_git_bounded_output(
            ["-C", str(path), "rev-parse", "--verify", revision_name],
            max_bytes=4096,
            read_only=True,
            timeout=10,
        )
    except ValueError:
        return ""
    revision = output.decode("ascii", errors="ignore").strip()
    return revision if re.fullmatch(r"[0-9a-fA-F]{40,64}", revision) else ""


def _validate_git_location(location: str) -> None:
    if "::" in location:
        raise ValueError("investment brain Git source uses a prohibited remote helper")
    if "://" in location:
        parsed = urlsplit(location)
        if parsed.scheme not in {"https", "ssh", "file"}:
            raise ValueError("investment brain Git source scheme is not allowed")
        if parsed.query or parsed.fragment or parsed.password:
            raise ValueError("investment brain Git source must not contain credentials, query, or fragment")
        if parsed.scheme == "https" and parsed.username:
            raise ValueError("investment brain HTTPS source must not contain user information")
        return
    expanded = Path(location).expanduser()
    if expanded.exists():
        return
    if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+", location):
        raise ValueError("investment brain Git source must be an allowed URL, local path, or SSH location")


def _validate_public_repository(repository: str) -> None:
    if (
        not repository
        or len(repository) > 4096
        or repository != repository.strip()
        or "\\" in repository
        or any(ord(char) < 32 or ord(char) == 127 for char in repository)
    ):
        raise ValueError("investment brain declared repository is invalid")
    parsed = urlsplit(repository)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(
            "investment brain declared repository must be a public HTTPS URL"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "investment brain declared repository must not contain credentials, query, or fragment"
        )
    try:
        hostname = parsed.hostname or ""
        port = parsed.port
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise ValueError("investment brain declared repository host is invalid") from exc
    if port not in {None, 443}:
        raise ValueError(
            "investment brain declared repository must use the standard HTTPS port"
        )
    reserved_host_suffixes = (
        ".localhost",
        ".local",
        ".localdomain",
        ".internal",
        ".lan",
        ".home",
        ".home.arpa",
        ".invalid",
        ".test",
        ".example",
        ".onion",
        ".alt",
    )
    if (
        not ascii_hostname
        or ascii_hostname.endswith(".")
        or ascii_hostname == "localhost"
        or ascii_hostname in {suffix.removeprefix(".") for suffix in reserved_host_suffixes}
        or ascii_hostname.endswith(reserved_host_suffixes)
    ):
        raise ValueError(
            "investment brain declared repository must use a public host"
        )
    try:
        address = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        labels = ascii_hostname.split(".")
        if len(labels) < 2 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError(
                "investment brain declared repository must use a public host"
            )
        numeric_label = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.IGNORECASE)
        if all(numeric_label.fullmatch(label) for label in labels) or labels[-1].isdigit():
            raise ValueError(
                "investment brain declared repository must use a public host"
            )
    else:
        if not address.is_global:
            raise ValueError(
                "investment brain declared repository must use a public host"
            )
    if not parsed.path or parsed.path == "/":
        raise ValueError(
            "investment brain declared repository must identify a repository path"
        )


def _tracked_source_metadata(
    workspace_root: Path,
    source_root: Path,
    source: dict[str, Any],
    declared: dict[str, Any],
) -> dict[str, Any]:
    tracked = {**source, "declared": declared}
    if tracked["kind"] == "local":
        tracked["location"] = _workspace_source_locator(workspace_root, source_root)
        return tracked

    location = str(tracked["location"])
    if not _is_remote_git_location(location):
        local_path = _local_git_source_path(location)
        tracked["location"] = (
            _workspace_source_locator(workspace_root, local_path)
            if local_path is not None
            else ""
        )
    return tracked


def validate_public_investment_brain_repository_url(repository: str) -> str:
    """Validate an agent-supplied public HTTPS repository without materializing it."""

    normalized = str(repository or "")
    _validate_public_repository(normalized)
    return normalized


def _workspace_source_locator(workspace_root: Path, source_root: Path) -> str:
    try:
        relative = source_root.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return ""
    locator = relative.as_posix()
    if locator == ".":
        return ""
    _validate_workspace_source_locator(locator)
    return locator


def _resolve_workspace_source_locator(workspace_root: Path, locator: str) -> Path:
    _validate_workspace_source_locator(locator)
    return workspace_root.joinpath(*PurePosixPath(locator).parts)


def _validate_workspace_source_locator(locator: str) -> None:
    path = PurePosixPath(locator)
    if (
        not locator
        or path.is_absolute()
        or PureWindowsPath(locator).is_absolute()
        or locator != path.as_posix()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in locator)
        or "\\" in locator
        or re.match(r"^[A-Za-z]:", locator)
    ):
        raise ValueError("investment brain tracked source must be a canonical workspace-relative POSIX path")


def _is_remote_git_location(location: str) -> bool:
    if "://" in location:
        return urlsplit(location).scheme != "file"
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+", location))


def _local_git_source_path(location: str) -> Path | None:
    if location.startswith("file://"):
        parsed = urlsplit(location)
        if parsed.netloc not in {"", "localhost"}:
            return None
        return Path(unquote(parsed.path)).expanduser().resolve()
    return Path(location).expanduser().resolve()


def _validate_recorded_source(source: dict[str, Any], brain_id: str) -> None:
    fields = {"kind", "location", "ref", "resolved_revision", "declared"}
    if set(source) != fields or source["kind"] not in {"local", "git"}:
        raise ValueError(f"investment brain source metadata is invalid: {brain_id}")
    for field in ("location", "ref", "resolved_revision"):
        if not isinstance(source[field], str):
            raise ValueError(f"investment brain source metadata is invalid: {brain_id}")
    location = source["location"]
    if source["kind"] == "local":
        if location:
            try:
                _validate_workspace_source_locator(location)
            except ValueError as exc:
                raise ValueError(f"investment brain source metadata is invalid: {brain_id}") from exc
        if source["ref"]:
            raise ValueError(f"investment brain source metadata is invalid: {brain_id}")
        revision = source["resolved_revision"]
        if revision and not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
            raise ValueError(f"investment brain resolved Git revision is invalid: {brain_id}")
    else:
        if location:
            try:
                if _is_remote_git_location(location):
                    _validate_git_location(location)
                else:
                    _validate_workspace_source_locator(location)
            except ValueError as exc:
                raise ValueError(f"investment brain source metadata is invalid: {brain_id}") from exc
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", source["resolved_revision"]):
            raise ValueError(f"investment brain resolved Git revision is invalid: {brain_id}")
    declared = source["declared"]
    if not isinstance(declared, dict) or not {"publisher", "license"}.issubset(declared):
        raise ValueError(f"investment brain declared source metadata is invalid: {brain_id}")


def _reject_managed_source(workspace_root: Path, source_root: Path) -> None:
    managed_roots = (
        (workspace_root / BRAIN_REGISTRY_DIR).resolve(),
        (workspace_root / BRAIN_PROJECTION_DIR).resolve(),
    )
    if any(source_root == managed or source_root.is_relative_to(managed) for managed in managed_roots):
        raise ValueError("investment brain source cannot be a TradingCodex-managed package or projection")


def _host_global_brain_collisions(root: Path, brain_id: str) -> list[dict[str, Any]]:
    from tradingcodex_service.application.agents import detect_host_global_skill_collisions

    projected = root / BRAIN_PROJECTION_DIR / brain_id / "SKILL.md"
    return detect_host_global_skill_collisions(
        root,
        {
            brain_id: {
                "layer": "workspace_investment_brain",
                "resolved_source_file": _relative(root, projected),
            }
        },
    )


def _host_global_collision_message(brain_id: str, collisions: list[dict[str, Any]]) -> str:
    locations = sorted(
        str(collision.get("resolved_source_file") or "host-global skill")
        for collision in collisions
    )
    return f"host-global same-id skill collision blocks investment brain {brain_id}: {', '.join(locations)}"


def _assert_no_host_global_brain_collision(root: Path, brain_id: str) -> None:
    collisions = _host_global_brain_collisions(root, brain_id)
    if collisions:
        raise ValueError(_host_global_collision_message(brain_id, collisions))


def _package_is_referenced(registry: dict[str, Any], package_path: str) -> bool:
    return any(
        item.get("package_path") == package_path
        for plugin in registry.get("plugins", {}).values()
        for item in plugin.get("versions", [])
    )


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _assert_managed_workspace_path(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    try:
        path.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"investment brain managed path escapes the workspace: {path}") from exc
    current = path
    while current != resolved_root:
        if current.is_symlink():
            raise ValueError(f"investment brain managed path cannot traverse a symlink: {current}")
        if current.parent == current:
            break
        current = current.parent
