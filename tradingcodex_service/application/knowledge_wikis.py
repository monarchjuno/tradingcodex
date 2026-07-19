from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from packaging.version import Version

from tradingcodex_service.application.common import (
    atomic_write_text,
    exclusive_file_lock,
    now_iso,
    read_json,
    write_json,
)
from tradingcodex_service.application.managed_package_sources import (
    git_tree_records,
    is_remote_git_location,
    materialized_package_source,
    resolve_workspace_source_locator,
    tracked_source_metadata,
    validate_git_location,
    validate_public_https_url,
    validate_workspace_source_locator,
)
from tradingcodex_service.application.markdown_preview import split_markdown_frontmatter


WIKI_ID_PREFIX = "knowledge-wiki-"
WIKI_ID_PATTERN = re.compile(r"^knowledge-wiki-[a-z0-9]+(?:-[a-z0-9]+)*$")
WIKI_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
WIKI_PLUGIN_FORMAT = "tradingcodex.knowledge-wiki"
WIKI_PLUGIN_TYPE = "knowledge-wiki"
WIKI_PLUGIN_SCHEMA_VERSION = 1
WIKI_REGISTRY_FORMAT = "tradingcodex.knowledge-wiki-registry"
WIKI_REGISTRY_SCHEMA_VERSION = 1
WIKI_REGISTRY_DIR = Path(".tradingcodex/knowledge-wikis")
WIKI_REGISTRY_PATH = WIKI_REGISTRY_DIR / "registry.json"
WIKI_PACKAGE_DIR = WIKI_REGISTRY_DIR / "packages"
WIKI_SOURCE_DIR = Path("wiki-packages")
WIKI_VAULT_DIR = Path("wikis")
LOCAL_WIKI_DIR = WIKI_VAULT_DIR / "local"
WIKI_MANIFEST_PATH = Path(".tradingcodex/plugin.json")
WIKI_STATUSES = {"active", "inactive", "removed"}
PAGE_STATUSES = {"draft", "current", "contested", "superseded"}
RECOMMENDED_PAGE_TYPES = {
    "company", "product", "technology", "material", "process", "concept",
    "value-chain", "comparison", "synthesis",
}

MAX_MANIFEST_BYTES = 64 * 1024
MAX_MARKDOWN_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_MARKDOWN_FILES = 1024
MAX_DIRECTORY_DEPTH = 8
MAX_GIT_TREE_OUTPUT_BYTES = 2 * 1024 * 1024

_PLUGIN_FIELDS = {"format", "schema_version", "type", "id", "version", "wiki", "source"}
_SOURCE_FIELDS = {"publisher", "repository", "license"}
_PAGE_FIELDS = {"title", "type", "summary", "aliases", "tags", "status", "updated_at", "sources"}
_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
_PRIVATE_REFERENCE_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:artifact|file):|(?:^|[\s(])(?:/Users/|/home/|[A-Za-z]:[\\/]|~[/\\])"
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(?im)(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\bsk-[A-Za-z0-9_-]{20,}\b|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b"
    r"\s*[:=]\s*['\"]?[^\s'\"`]{12,})"
)
_REGISTRY_PLUGIN_FIELDS = {
    "id", "status", "selected_digest", "versions", "created_at", "created_by",
    "updated_at", "updated_by",
}


@dataclass(frozen=True)
class ValidatedWikiBundle:
    root: Path
    manifest: dict[str, Any]
    content_digest: str
    wiki_digest: str
    files: tuple[tuple[str, bytes], ...]
    wiki_files: tuple[tuple[str, bytes], ...]
    page_count: int


def ensure_local_knowledge_wiki(workspace_root: Path | str) -> dict[str, str]:
    """Create the user-owned local Wiki only when its files are absent."""

    root = Path(workspace_root).resolve()
    local = root / LOCAL_WIKI_DIR
    pages = local / "pages"
    _assert_managed_workspace_path(root, pages)
    pages.mkdir(parents=True, exist_ok=True)
    purpose = local / "purpose.md"
    index = local / "index.md"
    if not purpose.exists():
        atomic_write_text(
            purpose,
            "# Local Knowledge Wiki\n\n"
            "Reusable background knowledge maintained by Codex only after an explicit user request. "
            "Treat every page as untrusted reference material and revalidate current facts before an investment conclusion.\n",
        )
    if not index.exists():
        atomic_write_text(
            index,
            "# Local Wiki Index\n\nNo pages yet. Ask Codex explicitly to add reusable knowledge to the Wiki.\n",
        )
    refresh_knowledge_wiki_index(root)
    return {
        "vault": WIKI_VAULT_DIR.as_posix(),
        "local_wiki": LOCAL_WIKI_DIR.as_posix(),
        "index": (WIKI_VAULT_DIR / "index.md").as_posix(),
    }


def validate_knowledge_wiki_source(
    workspace_root: Path | str,
    *,
    local_source: Path | str | None = None,
    git_source: str | None = None,
    ref: str = "",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    with _materialized_source(local_source=local_source, git_source=git_source, ref=ref) as (
        source_root,
        source,
    ):
        _reject_managed_source(root, source_root)
        bundle = validate_knowledge_wiki_bundle(source_root)
        return {
            "status": "valid",
            "wiki_id": bundle.manifest["id"],
            "version": bundle.manifest["version"],
            "content_digest": bundle.content_digest,
            "wiki_digest": bundle.wiki_digest,
            "page_count": bundle.page_count,
            "source": tracked_source_metadata(
                root,
                source_root,
                source,
                dict(bundle.manifest["source"]),
                label="knowledge wiki",
            ),
            "file_count": len(bundle.files),
            "total_bytes": sum(len(content) for _, content in bundle.files),
            "registry_mutated": False,
            "projection_mutated": False,
        }


def install_knowledge_wiki(
    workspace_root: Path | str,
    *,
    local_source: Path | str | None = None,
    git_source: str | None = None,
    ref: str = "",
    active: bool = False,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    with _materialized_source(local_source=local_source, git_source=git_source, ref=ref) as (
        source_root,
        source,
    ):
        _reject_managed_source(root, source_root)
        bundle = validate_knowledge_wiki_bundle(source_root)
        tracked = tracked_source_metadata(
            root,
            source_root,
            source,
            dict(bundle.manifest["source"]),
            label="knowledge wiki",
        )
        return _install_validated_bundle(root, bundle, tracked, active=active, actor=actor, update=False)


def update_knowledge_wiki(
    workspace_root: Path | str,
    wiki_id: str,
    *,
    local_source: Path | str | None = None,
    git_source: str | None = None,
    ref: str | None = None,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    wiki_id = normalize_knowledge_wiki_id(wiki_id)
    current = get_knowledge_wiki_record(root, wiki_id)
    selected = next(
        item for item in current["versions"] if item["content_digest"] == current["content_digest"]
    )
    recorded = dict(selected["source"])
    if local_source is None and git_source is None:
        location = str(recorded.get("location") or "")
        if recorded.get("kind") == "local":
            if not location:
                raise ValueError("knowledge wiki update requires an explicit --local source")
            local_source = resolve_workspace_source_locator(root, location, label="knowledge wiki")
        elif recorded.get("kind") == "git":
            if not location:
                raise ValueError("knowledge wiki update requires an explicit --git source")
            git_source = (
                location
                if is_remote_git_location(location)
                else str(resolve_workspace_source_locator(root, location, label="knowledge wiki"))
            )
            if ref is None:
                ref = str(recorded.get("ref") or "")
        else:
            raise ValueError(f"knowledge wiki update source is unavailable: {wiki_id}")
    with _materialized_source(
        local_source=local_source,
        git_source=git_source,
        ref=str(ref or ""),
    ) as (source_root, source):
        _reject_managed_source(root, source_root)
        bundle = validate_knowledge_wiki_bundle(source_root)
        if bundle.manifest["id"] != wiki_id:
            raise ValueError("knowledge wiki update id does not match the installed package")
        tracked = tracked_source_metadata(
            root,
            source_root,
            source,
            dict(bundle.manifest["source"]),
            label="knowledge wiki",
        )
        return _install_validated_bundle(
            root,
            bundle,
            tracked,
            active=current["status"] == "active",
            actor=actor,
            update=True,
        )


def rollback_knowledge_wiki(
    workspace_root: Path | str,
    wiki_id: str,
    *,
    version: str = "",
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    wiki_id = normalize_knowledge_wiki_id(wiki_id)

    def mutate(registry: dict[str, Any]) -> None:
        plugin = _registry_plugin(registry, wiki_id)
        current = _selected_version(plugin)
        if version:
            target = next((item for item in plugin["versions"] if item["version"] == version), None)
            if target is None:
                raise ValueError(f"knowledge wiki version is not installed: {wiki_id}@{version}")
        else:
            lower = [
                item for item in plugin["versions"]
                if Version(str(item["version"])) < Version(str(current["version"]))
            ]
            if not lower:
                raise ValueError(f"knowledge wiki has no earlier installed version: {wiki_id}")
            target = max(lower, key=lambda item: Version(str(item["version"])))
        plugin["selected_digest"] = target["content_digest"]
        plugin["updated_at"] = now_iso()
        plugin["updated_by"] = actor

    _mutate_registry_and_project(root, mutate)
    return get_knowledge_wiki_record(root, wiki_id)


def set_knowledge_wiki_status(
    workspace_root: Path | str,
    wiki_id: str,
    status: str,
    *,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    wiki_id = normalize_knowledge_wiki_id(wiki_id)
    if status not in {"active", "inactive"}:
        raise ValueError("knowledge wiki status must be active or inactive")

    def mutate(registry: dict[str, Any]) -> None:
        plugin = _registry_plugin(registry, wiki_id)
        plugin["status"] = status
        plugin["updated_at"] = now_iso()
        plugin["updated_by"] = actor

    _mutate_registry_and_project(root, mutate)
    return get_knowledge_wiki_record(root, wiki_id)


def remove_knowledge_wiki(
    workspace_root: Path | str,
    wiki_id: str,
    *,
    actor: str = "local",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    wiki_id = normalize_knowledge_wiki_id(wiki_id)

    def mutate(registry: dict[str, Any]) -> None:
        plugin = _registry_plugin(registry, wiki_id)
        plugin["status"] = "removed"
        plugin["updated_at"] = now_iso()
        plugin["updated_by"] = actor

    _mutate_registry_and_project(root, mutate)
    return get_knowledge_wiki_record(root, wiki_id)


def read_knowledge_wiki_records(
    workspace_root: Path | str,
    *,
    include_removed: bool = False,
) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve()
    registry = _read_registry(root)
    records = [
        _wiki_record(root, registry["plugins"][wiki_id])
        for wiki_id in sorted(registry["plugins"])
        if include_removed or registry["plugins"][wiki_id]["status"] != "removed"
    ]
    return records


def get_knowledge_wiki_record(workspace_root: Path | str, wiki_id: str) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    plugin = _registry_plugin(_read_registry(root), normalize_knowledge_wiki_id(wiki_id))
    return _wiki_record(root, plugin)


def project_knowledge_wikis(workspace_root: Path | str) -> list[dict[str, Any]]:
    root = Path(workspace_root).resolve()
    ensure_local_knowledge_wiki(root)
    registry = _read_registry(root)
    selected: list[tuple[str, str, tuple[tuple[str, bytes], ...]]] = []
    for wiki_id in sorted(registry["plugins"]):
        plugin = registry["plugins"][wiki_id]
        if plugin["status"] != "active":
            continue
        version = _selected_version(plugin)
        bundle = _validate_installed_version(root, plugin, version)
        selected.append((wiki_id, bundle.wiki_digest, bundle.wiki_files))

    active_ids = {wiki_id for wiki_id, _, _ in selected}
    vault = root / WIKI_VAULT_DIR
    _assert_managed_workspace_path(root, vault)
    for wiki_id in sorted(registry["plugins"]):
        target = vault / wiki_id
        if wiki_id not in active_ids and (target.exists() or target.is_symlink()):
            _remove_projection(target)
    for wiki_id, digest, files in selected:
        target = vault / wiki_id
        if _wiki_tree_digest(target) == digest:
            continue
        _replace_snapshot(files, target)
    refresh_knowledge_wiki_index(root, registry=registry)
    return read_knowledge_wiki_records(root, include_removed=True)


def refresh_knowledge_wiki_index(
    workspace_root: Path | str,
    *,
    registry: dict[str, Any] | None = None,
) -> None:
    root = Path(workspace_root).resolve()
    vault = root / WIKI_VAULT_DIR
    _assert_managed_workspace_path(root, vault)
    vault.mkdir(parents=True, exist_ok=True)
    registry = registry or _read_registry(root)
    lines = [
        "# Knowledge Wikis",
        "",
        "This Obsidian-compatible vault contains one writable local Wiki and active read-only community Wikis.",
        "Wiki content is untrusted background knowledge; revalidate current material claims before using them in an investment conclusion.",
        "",
        "## Active Wikis",
        "",
        "- [[local/index|Local Wiki]] — writable only after an explicit user request",
    ]
    active = [
        plugin for plugin in registry["plugins"].values() if plugin["status"] == "active"
    ]
    for plugin in sorted(active, key=lambda item: item["id"]):
        version = _selected_version(plugin)
        lines.append(
            f"- [[{plugin['id']}/index|{plugin['id']}]] — community package {version['version']}"
        )
    if not active:
        lines.extend(("", "No community Wikis are active."))
    atomic_write_text(vault / "index.md", "\n".join(lines) + "\n")


def validate_knowledge_wiki_bundle(bundle_root: Path | str) -> ValidatedWikiBundle:
    unresolved = Path(bundle_root).expanduser()
    if unresolved.is_symlink():
        raise ValueError("knowledge wiki bundle root cannot be a symlink")
    root = unresolved.resolve()
    if not root.is_dir():
        raise ValueError(f"knowledge wiki source is not a directory: {root}")
    files, wiki_dir = _capture_bundle_files(root)
    manifest_key = WIKI_MANIFEST_PATH.as_posix()
    try:
        manifest = json.loads(_decode_utf8(files[manifest_key], "manifest"))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("knowledge wiki manifest is invalid") from exc
    _validate_manifest(manifest)
    wiki_id = str(manifest["id"])
    if wiki_dir != wiki_id or manifest["wiki"] != wiki_id:
        raise ValueError("knowledge wiki directory and manifest wiki must match the manifest id")

    prefix = f"{wiki_id}/"
    wiki_files = tuple(
        (path.removeprefix(prefix), content)
        for path, content in sorted(files.items())
        if path.startswith(prefix)
    )
    page_paths = {path for path, _ in wiki_files if path.startswith("pages/")}
    if not page_paths:
        raise ValueError("knowledge wiki package must contain at least one page")
    for relative, content in wiki_files:
        text = _decode_utf8(content, relative)
        if _PRIVATE_REFERENCE_PATTERN.search(text):
            raise ValueError(f"knowledge wiki shared content contains a local or private reference: {relative}")
        if _CREDENTIAL_VALUE_PATTERN.search(text):
            raise ValueError(f"knowledge wiki shared content contains credential-like material: {relative}")
        if relative.startswith("pages/"):
            _validate_page(text, relative)
        _validate_wikilinks(text, wiki_id=wiki_id, page_paths=page_paths, label=relative)

    frozen = tuple(sorted(files.items()))
    return ValidatedWikiBundle(
        root=root,
        manifest=manifest,
        content_digest=_snapshot_digest(frozen),
        wiki_digest=_snapshot_digest(wiki_files),
        files=frozen,
        wiki_files=wiki_files,
        page_count=len(page_paths),
    )


def normalize_knowledge_wiki_id(value: str) -> str:
    wiki_id = str(value or "").strip()
    if not WIKI_ID_PATTERN.fullmatch(wiki_id) or len(wiki_id) > 80:
        raise ValueError("knowledge wiki id must use knowledge-wiki-* lowercase hyphen-case")
    return wiki_id


def _install_validated_bundle(
    root: Path,
    bundle: ValidatedWikiBundle,
    source: dict[str, Any],
    *,
    active: bool,
    actor: str,
    update: bool,
) -> dict[str, Any]:
    wiki_id = str(bundle.manifest["id"])
    package_rel = WIKI_PACKAGE_DIR / wiki_id / f"{bundle.manifest['version']}-{bundle.content_digest[:12]}"
    package_path = root / package_rel
    _assert_managed_workspace_path(root, package_path)
    created = False
    with exclusive_file_lock(root / WIKI_REGISTRY_PATH):
        registry = _read_registry(root)
        previous = json.loads(json.dumps(registry))
        existing = registry["plugins"].get(wiki_id)
        projection = root / WIKI_VAULT_DIR / wiki_id
        if existing is None and (projection.exists() or projection.is_symlink()):
            raise ValueError(f"reserved knowledge wiki projection path already exists: {projection}")
        if existing and not update:
            if _selected_version(existing)["content_digest"] != bundle.content_digest:
                raise ValueError(f"knowledge wiki is already installed; use update: {wiki_id}")
        if update and not existing:
            raise ValueError(f"unknown knowledge wiki: {wiki_id}")
        if existing:
            same_version = next(
                (item for item in existing["versions"] if item["version"] == bundle.manifest["version"]),
                None,
            )
            if same_version and same_version["content_digest"] != bundle.content_digest:
                raise ValueError("knowledge wiki version content is immutable; publish a new version")
            if update and bundle.content_digest != _selected_version(existing)["content_digest"]:
                highest = max(Version(str(item["version"])) for item in existing["versions"])
                if Version(str(bundle.manifest["version"])) <= highest:
                    raise ValueError(
                        "knowledge wiki update must publish a version higher than every installed version; "
                        "use rollback for older versions"
                    )
        if not package_path.exists():
            _copy_bundle(bundle, package_path)
            created = True
        installed = validate_knowledge_wiki_bundle(package_path)
        if installed.content_digest != bundle.content_digest:
            raise ValueError("installed knowledge wiki content digest mismatch")
        timestamp = now_iso()
        record = {
            "version": bundle.manifest["version"],
            "content_digest": bundle.content_digest,
            "wiki_digest": bundle.wiki_digest,
            "page_count": bundle.page_count,
            "source": source,
            "package_path": package_rel.as_posix(),
            "manifest_path": (package_rel / WIKI_MANIFEST_PATH).as_posix(),
            "installed_at": timestamp,
            "installed_by": actor,
        }
        if existing:
            versions = list(existing["versions"])
            if not any(item["content_digest"] == bundle.content_digest for item in versions):
                versions.append(record)
            plugin = {
                **existing,
                "status": existing["status"] if update else ("active" if active else "inactive"),
                "selected_digest": bundle.content_digest,
                "versions": versions,
                "updated_at": timestamp,
                "updated_by": actor,
            }
        else:
            plugin = {
                "id": wiki_id,
                "status": "active" if active else "inactive",
                "selected_digest": bundle.content_digest,
                "versions": [record],
                "created_at": timestamp,
                "created_by": actor,
                "updated_at": timestamp,
                "updated_by": actor,
            }
        registry["plugins"][wiki_id] = plugin
        _write_registry(root, registry)
        try:
            project_knowledge_wikis(root)
        except Exception:
            _write_registry(root, previous)
            if created and not _package_is_referenced(previous, package_rel.as_posix()):
                shutil.rmtree(package_path, ignore_errors=True)
            try:
                project_knowledge_wikis(root)
            except Exception:
                pass
            raise
    return get_knowledge_wiki_record(root, wiki_id)


def _mutate_registry_and_project(root: Path, mutate: Any) -> None:
    with exclusive_file_lock(root / WIKI_REGISTRY_PATH):
        registry = _read_registry(root)
        previous = json.loads(json.dumps(registry))
        mutate(registry)
        _write_registry(root, registry)
        try:
            project_knowledge_wikis(root)
        except Exception:
            _write_registry(root, previous)
            try:
                project_knowledge_wikis(root)
            except Exception:
                pass
            raise


def _wiki_record(root: Path, plugin: dict[str, Any]) -> dict[str, Any]:
    selected = _selected_version(plugin)
    errors: list[str] = []
    try:
        bundle = _validate_installed_version(root, plugin, selected)
    except ValueError as exc:
        errors.append(str(exc))
        bundle = None
    projected = WIKI_VAULT_DIR / plugin["id"]
    if plugin["status"] == "active" and bundle is not None:
        if _wiki_tree_digest(root / projected) != selected["wiki_digest"]:
            errors.append("active knowledge wiki projected digest mismatch")
    return {
        "wiki_id": plugin["id"],
        "version": selected["version"],
        "content_digest": selected["content_digest"],
        "wiki_digest": selected["wiki_digest"],
        "page_count": selected["page_count"],
        "source": dict(selected["source"]),
        "manifest_path": selected["manifest_path"],
        "projected_wiki_path": projected.as_posix(),
        "validation_status": "blocked" if errors else "valid",
        "validation_errors": errors,
        "status": plugin["status"],
        "active": plugin["status"] == "active" and not errors,
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
) -> ValidatedWikiBundle:
    path = root / selected["package_path"]
    _assert_managed_workspace_path(root, path)
    bundle = validate_knowledge_wiki_bundle(path)
    if bundle.manifest["id"] != plugin["id"] or bundle.manifest["version"] != selected["version"]:
        raise ValueError("installed knowledge wiki manifest does not match its registry record")
    if bundle.content_digest != selected["content_digest"] or bundle.wiki_digest != selected["wiki_digest"]:
        raise ValueError("installed knowledge wiki digest does not match its registry record")
    return bundle


def _selected_version(plugin: dict[str, Any]) -> dict[str, Any]:
    selected = next(
        (item for item in plugin["versions"] if item["content_digest"] == plugin["selected_digest"]),
        None,
    )
    if selected is None:
        raise ValueError(f"knowledge wiki selected version is invalid: {plugin.get('id', '')}")
    return selected


def _registry_plugin(registry: dict[str, Any], wiki_id: str) -> dict[str, Any]:
    plugin = registry["plugins"].get(wiki_id)
    if plugin is None:
        raise ValueError(f"unknown knowledge wiki: {wiki_id}")
    return plugin


def _read_registry(root: Path) -> dict[str, Any]:
    path = root / WIKI_REGISTRY_PATH
    _assert_managed_workspace_path(root, path)
    if not path.exists():
        return {
            "format": WIKI_REGISTRY_FORMAT,
            "schema_version": WIKI_REGISTRY_SCHEMA_VERSION,
            "plugins": {},
        }
    registry = read_json(path)
    if not isinstance(registry, dict) or set(registry) != {"format", "schema_version", "plugins"}:
        raise ValueError("knowledge wiki registry fields do not match the v1 schema")
    if registry["format"] != WIKI_REGISTRY_FORMAT or registry["schema_version"] != 1:
        raise ValueError("knowledge wiki registry format is invalid")
    if not isinstance(registry["plugins"], dict):
        raise ValueError("knowledge wiki registry plugins must be an object")
    for wiki_id, plugin in registry["plugins"].items():
        normalize_knowledge_wiki_id(wiki_id)
        if not isinstance(plugin, dict) or set(plugin) != _REGISTRY_PLUGIN_FIELDS:
            raise ValueError(f"knowledge wiki registry record is invalid: {wiki_id}")
        if plugin["id"] != wiki_id or plugin["status"] not in WIKI_STATUSES:
            raise ValueError(f"knowledge wiki registry status or versions are invalid: {wiki_id}")
        if not isinstance(plugin["versions"], list) or not plugin["versions"]:
            raise ValueError(f"knowledge wiki registry has no versions: {wiki_id}")
        for field in ("selected_digest", "created_at", "created_by", "updated_at", "updated_by"):
            if not isinstance(plugin[field], str) or not plugin[field]:
                raise ValueError(f"knowledge wiki registry metadata is invalid: {wiki_id}")
        versions_seen: dict[str, str] = {}
        digests: set[str] = set()
        for item in plugin["versions"]:
            _validate_version_record(wiki_id, item)
            previous = versions_seen.setdefault(item["version"], item["content_digest"])
            if previous != item["content_digest"]:
                raise ValueError(
                    f"knowledge wiki version is not immutable: {wiki_id}@{item['version']}"
                )
            digests.add(item["content_digest"])
        if plugin["selected_digest"] not in digests:
            raise ValueError(f"knowledge wiki selected digest is invalid: {wiki_id}")
    return registry


def _validate_version_record(wiki_id: str, item: Any) -> None:
    fields = {
        "version", "content_digest", "wiki_digest", "page_count", "source", "package_path",
        "manifest_path", "installed_at", "installed_by",
    }
    if not isinstance(item, dict) or set(item) != fields:
        raise ValueError(f"knowledge wiki version record is invalid: {wiki_id}")
    if not WIKI_VERSION_PATTERN.fullmatch(str(item["version"])):
        raise ValueError(f"knowledge wiki version is invalid: {wiki_id}")
    if not all(re.fullmatch(r"[0-9a-f]{64}", str(item[field])) for field in ("content_digest", "wiki_digest")):
        raise ValueError(f"knowledge wiki digest is invalid: {wiki_id}")
    if type(item["page_count"]) is not int or item["page_count"] < 1:
        raise ValueError(f"knowledge wiki page count is invalid: {wiki_id}")
    if not isinstance(item["installed_at"], str) or not item["installed_at"]:
        raise ValueError(f"knowledge wiki installed timestamp is invalid: {wiki_id}")
    if not isinstance(item["installed_by"], str) or not item["installed_by"]:
        raise ValueError(f"knowledge wiki installer is invalid: {wiki_id}")
    expected = WIKI_PACKAGE_DIR / wiki_id / f"{item['version']}-{item['content_digest'][:12]}"
    if item["package_path"] != expected.as_posix() or item["manifest_path"] != (expected / WIKI_MANIFEST_PATH).as_posix():
        raise ValueError(f"knowledge wiki package paths are invalid: {wiki_id}")
    _validate_recorded_source(item["source"], wiki_id)


def _write_registry(root: Path, registry: dict[str, Any]) -> None:
    path = root / WIKI_REGISTRY_PATH
    _assert_managed_workspace_path(root, path)
    write_json(path, registry)


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict) or set(manifest) != _PLUGIN_FIELDS:
        raise ValueError("knowledge wiki manifest fields do not match the v1 schema")
    if manifest["format"] != WIKI_PLUGIN_FORMAT or manifest["type"] != WIKI_PLUGIN_TYPE:
        raise ValueError("knowledge wiki manifest format or type is invalid")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("knowledge wiki manifest schema_version is invalid")
    wiki_id = normalize_knowledge_wiki_id(str(manifest["id"]))
    if manifest["wiki"] != wiki_id:
        raise ValueError("knowledge wiki manifest wiki must match id")
    if not WIKI_VERSION_PATTERN.fullmatch(str(manifest["version"])):
        raise ValueError("knowledge wiki version must use stable major.minor.patch syntax")
    source = manifest["source"]
    if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
        raise ValueError("knowledge wiki manifest source requires publisher, repository, and license")
    for field, value in source.items():
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"knowledge wiki manifest source {field} must be a non-empty string")
    validate_public_https_url(source["repository"], label="knowledge wiki declared repository")


def _capture_bundle_files(root: Path) -> tuple[dict[str, bytes], str]:
    entries = _directory_entries(root, maximum=4, label="bundle root")
    if WIKI_MANIFEST_PATH.parts[0] not in entries:
        raise ValueError("knowledge wiki bundle root must contain .tradingcodex")
    wiki_dirs = [name for name in entries if name.startswith(WIKI_ID_PREFIX)]
    unexpected = set(entries) - {WIKI_MANIFEST_PATH.parts[0], ".git", *wiki_dirs}
    if len(wiki_dirs) != 1 or unexpected:
        raise ValueError("knowledge wiki bundle root must contain one knowledge-wiki-* directory")
    wiki_dir = wiki_dirs[0]
    if not stat.S_ISDIR(entries[WIKI_MANIFEST_PATH.parts[0]]) or not stat.S_ISDIR(entries[wiki_dir]):
        raise ValueError("knowledge wiki bundle entries must be directories")
    manifest_entries = _directory_entries(root / WIKI_MANIFEST_PATH.parent, maximum=2, label="manifest directory")
    if set(manifest_entries) != {WIKI_MANIFEST_PATH.name}:
        raise ValueError("knowledge wiki .tradingcodex directory must contain only plugin.json")
    files = {
        WIKI_MANIFEST_PATH.as_posix(): _read_regular_bytes(
            root / WIKI_MANIFEST_PATH, MAX_MANIFEST_BYTES, "manifest"
        )
    }
    wiki_root = root / wiki_dir
    top = _directory_entries(wiki_root, maximum=4, label="wiki directory")
    if set(top) != {"purpose.md", "index.md", "pages"}:
        raise ValueError("knowledge wiki directory must contain only purpose.md, index.md, and pages")
    if not stat.S_ISDIR(top["pages"]):
        raise ValueError("knowledge wiki pages must be a directory")
    for name in ("purpose.md", "index.md"):
        files[f"{wiki_dir}/{name}"] = _read_regular_bytes(
            wiki_root / name, MAX_MARKDOWN_BYTES, name
        )
    _walk_markdown_pages(wiki_root / "pages", Path(), files, wiki_dir)
    if sum(len(value) for value in files.values()) > MAX_BUNDLE_BYTES:
        raise ValueError("knowledge wiki bundle exceeds the total size limit")
    return files, wiki_dir


def _walk_markdown_pages(
    directory: Path,
    relative: Path,
    files: dict[str, bytes],
    wiki_dir: str,
) -> None:
    entries = _directory_entries(directory, maximum=MAX_MARKDOWN_FILES + 1, label="pages directory")
    for name, mode in sorted(entries.items()):
        child = directory / name
        child_relative = relative / name
        if len(child_relative.parts) > MAX_DIRECTORY_DEPTH:
            raise ValueError("knowledge wiki pages exceed the maximum directory depth")
        if stat.S_ISDIR(mode):
            _walk_markdown_pages(child, child_relative, files, wiki_dir)
            continue
        if not stat.S_ISREG(mode) or child.suffix.lower() != ".md":
            raise ValueError("knowledge wiki pages may contain regular Markdown files only")
        if sum(1 for key in files if f"{wiki_dir}/pages/" in key) >= MAX_MARKDOWN_FILES:
            raise ValueError("knowledge wiki contains too many pages")
        relative_name = child_relative.as_posix()
        _validate_relative_path(relative_name)
        files[f"{wiki_dir}/pages/{relative_name}"] = _read_regular_bytes(
            child, MAX_MARKDOWN_BYTES, f"page {relative_name}"
        )


def _validate_page(text: str, path: str) -> None:
    try:
        document = split_markdown_frontmatter(text)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"knowledge wiki page frontmatter is invalid: {path}") from exc
    if set(document.frontmatter) != _PAGE_FIELDS:
        raise ValueError(f"knowledge wiki page frontmatter fields do not match the v1 schema: {path}")
    metadata = document.frontmatter
    for field in ("title", "summary", "type", "status"):
        value = metadata[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"knowledge wiki page {field} must be a non-empty string: {path}")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["type"]):
        raise ValueError(f"knowledge wiki page type must be kebab-case: {path}")
    if metadata["status"] not in PAGE_STATUSES:
        raise ValueError(f"knowledge wiki page status is invalid: {path}")
    updated = metadata["updated_at"]
    if isinstance(updated, date):
        updated = updated.isoformat()
    if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated):
        raise ValueError(f"knowledge wiki page updated_at must be YYYY-MM-DD: {path}")
    try:
        datetime.strptime(updated, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"knowledge wiki page updated_at must be a real date: {path}") from exc
    for field in ("aliases", "tags", "sources"):
        values = metadata[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in values
        ):
            raise ValueError(f"knowledge wiki page {field} must be a string list: {path}")
    if not metadata["sources"]:
        raise ValueError(f"knowledge wiki shared page requires at least one portable source: {path}")
    for source in metadata["sources"]:
        _validate_portable_source(source, path)
    if not document.body.strip():
        raise ValueError(f"knowledge wiki page body must not be empty: {path}")


def _validate_portable_source(source: str, path: str) -> None:
    if source.startswith("https://"):
        validate_public_https_url(
            source,
            label=f"knowledge wiki page source in {path}",
            require_path=False,
        )
        return
    if re.fullmatch(r"doi:10\.\d{4,9}/\S+", source, re.I):
        return
    if re.fullmatch(r"(?:standard|patent):[A-Za-z0-9][A-Za-z0-9._:/ -]{1,200}", source, re.I):
        return
    raise ValueError(f"knowledge wiki shared page source is not portable: {path}")


def _validate_wikilinks(text: str, *, wiki_id: str, page_paths: set[str], label: str) -> None:
    for match in _WIKILINK_PATTERN.finditer(text):
        target = match.group(1).strip()
        prefix = f"{wiki_id}/pages/"
        if not target.startswith(prefix):
            raise ValueError(f"knowledge wiki shared wikilink must stay inside {wiki_id}: {label}")
        relative = target.removeprefix(prefix)
        if not relative.endswith(".md"):
            relative += ".md"
        _validate_relative_path(relative)
        if f"pages/{relative}" not in page_paths:
            raise ValueError(f"knowledge wiki wikilink target does not exist: {target}")


def _materialized_source(*, local_source: Path | str | None, git_source: str | None, ref: str):
    if git_source:
        validate_public_https_url(git_source, label="knowledge wiki Git source")
    return materialized_package_source(
        local_source=local_source,
        git_source=git_source,
        ref=ref,
        label="knowledge wiki",
        temporary_prefix="tradingcodex-wiki-git-",
        checkout_paths=(".",),
        preflight=_preflight_git_bundle,
    )


def _preflight_git_bundle(checkout: Path) -> None:
    root_records = git_tree_records(
        checkout,
        recursive=False,
        max_bytes=MAX_GIT_TREE_OUTPUT_BYTES,
        label="knowledge wiki",
    )
    root = {path: (mode, object_type) for mode, object_type, _, path in root_records}
    wiki_dirs = [path for path in root if path.startswith(WIKI_ID_PREFIX)]
    if set(root) != {WIKI_MANIFEST_PATH.parts[0], *wiki_dirs} or len(wiki_dirs) != 1:
        raise ValueError("knowledge wiki Git bundle root contains unsupported entries")
    if any(root[path] != ("040000", "tree") for path in root):
        raise ValueError("knowledge wiki Git bundle root entries must be directories")
    wiki_id = wiki_dirs[0]
    records = git_tree_records(
        checkout,
        recursive=True,
        paths=(WIKI_MANIFEST_PATH.parts[0], wiki_id),
        max_bytes=MAX_GIT_TREE_OUTPUT_BYTES,
        label="knowledge wiki",
    )
    required = {WIKI_MANIFEST_PATH.as_posix(), f"{wiki_id}/purpose.md", f"{wiki_id}/index.md"}
    seen: set[str] = set()
    page_count = 0
    total = 0
    for mode, object_type, size, path in records:
        _validate_relative_path(path)
        if mode != "100644" or object_type != "blob" or size is None:
            raise ValueError("knowledge wiki Git bundle must contain non-executable regular files only")
        allowed = path in required or path.startswith(f"{wiki_id}/pages/")
        if not allowed or (path.startswith(f"{wiki_id}/pages/") and not path.lower().endswith(".md")):
            raise ValueError(f"knowledge wiki Git bundle contains an unsupported file: {path}")
        if path.startswith(f"{wiki_id}/pages/"):
            relative = path.removeprefix(f"{wiki_id}/pages/")
            if len(PurePosixPath(relative).parts) > MAX_DIRECTORY_DEPTH:
                raise ValueError("knowledge wiki Git pages exceed the maximum directory depth")
            page_count += 1
            if page_count > MAX_MARKDOWN_FILES:
                raise ValueError("knowledge wiki Git bundle contains too many pages")
        limit = MAX_MANIFEST_BYTES if path == WIKI_MANIFEST_PATH.as_posix() else MAX_MARKDOWN_BYTES
        if size > limit:
            raise ValueError(f"knowledge wiki Git bundle file exceeds its size limit: {path}")
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("knowledge wiki Git bundle exceeds the total size limit")
        seen.add(path)
    if not required.issubset(seen) or page_count < 1:
        raise ValueError("knowledge wiki Git bundle is missing required files")


def _directory_entries(path: Path, *, maximum: int, label: str) -> dict[str, int]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"knowledge wiki {label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(initial.st_mode):
        raise ValueError(f"knowledge wiki {label} must be a regular directory")
    entries: dict[str, int] = {}
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(entries) >= maximum:
                    raise ValueError(f"knowledge wiki {label} contains too many entries")
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError("knowledge wiki bundles cannot contain symlinks")
                entries[entry.name] = mode
        final = path.lstat()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"knowledge wiki {label} cannot be enumerated safely") from exc
    if not stat.S_ISDIR(final.st_mode) or _stat_identity(initial) != _stat_identity(final):
        raise ValueError(f"knowledge wiki {label} changed during validation")
    return entries


def _read_regular_bytes(path: Path, limit: int, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError(f"knowledge wiki {label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"knowledge wiki {label} must be a regular file")
    if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o111:
        raise ValueError(f"knowledge wiki {label} must not be executable")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"knowledge wiki {label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"knowledge wiki {label} changed before it could be read")
        if opened.st_size > limit:
            raise ValueError(f"knowledge wiki {label} exceeds the size limit")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            content = handle.read(limit + 1)
            opened_after = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > limit:
        raise ValueError(f"knowledge wiki {label} exceeds the size limit")
    try:
        final = path.lstat()
    except OSError as exc:
        raise ValueError(f"knowledge wiki {label} changed during validation") from exc
    if _stat_identity(opened) != _stat_identity(opened_after) or (
        final.st_dev,
        final.st_ino,
    ) != (opened.st_dev, opened.st_ino):
        raise ValueError(f"knowledge wiki {label} changed during validation")
    return content


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _decode_utf8(content: bytes, label: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"knowledge wiki {label} must be readable UTF-8") from exc


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("knowledge wiki bundle contains an invalid relative path")


def _snapshot_digest(files: tuple[tuple[str, bytes], ...]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files):
        _validate_relative_path(path)
        encoded = path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _copy_bundle(bundle: ValidatedWikiBundle, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for relative, content in bundle.files:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if target.exists() or target.is_symlink():
            raise ValueError(f"knowledge wiki immutable package path already exists: {target}")
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _replace_snapshot(files: tuple[tuple[str, bytes], ...], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.new-", dir=target.parent))
    backup = target.with_name(f".{target.name}.old")
    try:
        for relative, content in files:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if backup.exists() or backup.is_symlink():
            _remove_projection(backup)
        if target.exists() or target.is_symlink():
            target.replace(backup)
        staging.replace(target)
        if backup.exists() or backup.is_symlink():
            _remove_projection(backup)
    except Exception:
        if not target.exists() and backup.exists():
            backup.replace(target)
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _remove_projection(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _wiki_tree_digest(path: Path) -> str:
    if not path.is_dir() or path.is_symlink():
        return ""
    files: list[tuple[str, bytes]] = []
    try:
        for child in sorted(path.rglob("*")):
            if child.is_symlink() or (child.is_file() and child.suffix.lower() != ".md"):
                return ""
            if child.is_file():
                files.append((child.relative_to(path).as_posix(), _read_regular_bytes(child, MAX_MARKDOWN_BYTES, "projection")))
    except (OSError, ValueError):
        return ""
    return _snapshot_digest(tuple(files))


def _validate_recorded_source(source: Any, wiki_id: str) -> None:
    fields = {"kind", "location", "ref", "resolved_revision", "declared"}
    if not isinstance(source, dict) or set(source) != fields or source["kind"] not in {"local", "git"}:
        raise ValueError(f"knowledge wiki source metadata is invalid: {wiki_id}")
    if not all(isinstance(source[field], str) for field in ("location", "ref", "resolved_revision")):
        raise ValueError(f"knowledge wiki source metadata is invalid: {wiki_id}")
    location = source["location"]
    if source["kind"] == "local":
        if location:
            validate_workspace_source_locator(location, label="knowledge wiki")
        if source["ref"]:
            raise ValueError(f"knowledge wiki source metadata is invalid: {wiki_id}")
        if source["resolved_revision"] and not re.fullmatch(
            r"[0-9a-fA-F]{40,64}", source["resolved_revision"]
        ):
            raise ValueError(f"knowledge wiki source revision is invalid: {wiki_id}")
    else:
        if not location:
            raise ValueError(f"knowledge wiki Git source location is invalid: {wiki_id}")
        validate_git_location(location, label="knowledge wiki")
        validate_public_https_url(location, label="knowledge wiki Git source")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", source["resolved_revision"]):
            raise ValueError(f"knowledge wiki source revision is invalid: {wiki_id}")
    if not isinstance(source["declared"], dict) or set(source["declared"]) != _SOURCE_FIELDS:
        raise ValueError(f"knowledge wiki declared source metadata is invalid: {wiki_id}")
    for field, value in source["declared"].items():
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"knowledge wiki declared source metadata is invalid: {wiki_id}")
    validate_public_https_url(
        source["declared"]["repository"],
        label="knowledge wiki declared repository",
    )


def _reject_managed_source(workspace_root: Path, source_root: Path) -> None:
    managed = (
        (workspace_root / WIKI_REGISTRY_DIR).resolve(),
        (workspace_root / WIKI_VAULT_DIR).resolve(),
    )
    if any(source_root == path or source_root.is_relative_to(path) for path in managed):
        raise ValueError("knowledge wiki source cannot be a managed package or projection")


def _package_is_referenced(registry: dict[str, Any], package_path: str) -> bool:
    return any(
        item.get("package_path") == package_path
        for plugin in registry.get("plugins", {}).values()
        for item in plugin.get("versions", [])
    )


def _assert_managed_workspace_path(root: Path, path: Path) -> None:
    unresolved_root = Path(root).absolute()
    unresolved_path = Path(path).absolute()
    try:
        relative = unresolved_path.relative_to(unresolved_root)
    except ValueError as exc:
        raise ValueError("knowledge wiki managed path escaped the workspace") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("knowledge wiki managed path is invalid")
    current = unresolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("knowledge wiki managed paths cannot traverse symlinks")
