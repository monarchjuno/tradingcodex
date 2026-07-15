from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from tradingcodex_cli.__main__ import dispatch_workspace_command
from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    build_projection_state,
    inspect_skill_projection,
    project_agent_configuration,
)
from tradingcodex_service.application.investment_brains import (
    BRAIN_REGISTRY_PATH,
    get_investment_brain_record,
    install_investment_brain,
    read_investment_brain_records,
    remove_investment_brain,
    resolve_active_investment_brain,
    rollback_investment_brain,
    set_investment_brain_status,
    update_investment_brain,
)
from tradingcodex_service.application import investment_brains as brain_service


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    bootstrap_workspace(root)
    return root


def _bundle(
    tmp_path: Path,
    *,
    version: str = "1.0.0",
    body: str = "# Quality Growth\n\nPrioritize durable reinvestment economics, expectations, contrary evidence, and falsifiers.",
    implicit: bool = False,
    default_prompt: str = "Use $investment-brain-quality-growth for this investment analysis.",
) -> Path:
    root = tmp_path / f"brain-{version}-{len(list(tmp_path.glob('brain-*')))}"
    (root / ".tradingcodex").mkdir(parents=True)
    (root / "skill" / "agents").mkdir(parents=True)
    (root / "skill" / "references").mkdir(parents=True)
    (root / ".tradingcodex" / "plugin.json").write_text(
        json.dumps(
            {
                "format": "tradingcodex.investment-brain",
                "schema_version": 1,
                "type": "investment-brain",
                "id": "investment-brain-quality-growth",
                "version": version,
                "skill": "skill",
                "source": {
                    "publisher": "Example Research Collective",
                    "repository": "https://example.com/quality-growth",
                    "license": "MIT",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "skill" / "SKILL.md").write_text(
        "---\n"
        "name: investment-brain-quality-growth\n"
        "description: Frame investment questions through durable quality and expectations.\n"
        "---\n\n"
        f"{body.strip()}\n",
        encoding="utf-8",
    )
    (root / "skill" / "agents" / "openai.yaml").write_text(
        "interface:\n"
        '  display_name: "Quality Growth Brain"\n'
        '  short_description: "Frame quality-growth investment judgments"\n'
        f'  default_prompt: "{default_prompt}"\n'
        "policy:\n"
        f"  allow_implicit_invocation: {'true' if implicit else 'false'}\n",
        encoding="utf-8",
    )
    (root / "skill" / "references" / "falsifiers.md").write_text(
        "# Falsifiers\n\nTreat deteriorating unit economics and unsupported expectations as contrary evidence.\n",
        encoding="utf-8",
    )
    return root


def test_local_install_projects_explicit_head_manager_skill_only(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)

    record = install_investment_brain(workspace, local_source=source, actor="test")

    assert record["brain_id"] == "investment-brain-quality-growth"
    assert record["status"] == "active"
    assert record["validation_status"] == "valid"
    assert record["source"]["kind"] == "local"
    assert record["source"]["declared"]["publisher"] == "Example Research Collective"
    assert (workspace / record["manifest_path"]).is_file()
    assert (workspace / record["source_file"]).is_file()
    projected = workspace / record["projected_skill_path"]
    assert projected.is_dir()
    assert "allow_implicit_invocation: false" in (projected / "agents" / "openai.yaml").read_text(encoding="utf-8")

    config = (workspace / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "../.agents/skills/investment-brain-quality-growth/SKILL.md" in config
    fixed_role = (workspace / ".codex" / "agents" / "fundamental-analyst.toml").read_text(encoding="utf-8")
    assert "investment-brain-quality-growth" not in fixed_role

    binding = resolve_active_investment_brain(workspace, record["brain_id"])
    assert set(binding) == {
        "brain_id",
            "version",
            "content_digest",
            "skill_digest",
            "source",
        "manifest_path",
        "source_file",
        "projected_skill_path",
        "validation_status",
        "status",
    }
    state = build_projection_state(workspace)
    assert state["skills"][record["brain_id"]]["layer"] == "workspace_investment_brain"
    assert record["brain_id"] in state["agents"]["head-manager"]["effective_skills"]
    assert all(
        record["brain_id"] not in state["agents"][role]["effective_skills"]
        for role in state["agents"]
        if role != "head-manager"
    )
    assert inspect_skill_projection(workspace, "head-manager")["ok"] is True


@pytest.mark.parametrize(
    "repository",
    [
        "file:///Users/alice/Private/StealthFundResearch",
        "/Users/alice/Private/StealthFundResearch",
        "../private/brain",
        "git@example.com:private/brain.git",
        "http://example.com/public/brain",
        "https://user@example.com/public/brain",
        "https://example.com/public/brain?token=secret",
        "https://example.com/public/brain#signed-secret",
        "https://localhost/public/brain",
        "https://127.0.0.1/public/brain",
        "https://127.1/public/brain",
        "https://0177.0.0.1/public/brain",
        "https://0x7f.0.0.1/public/brain",
        "https://2130706433/public/brain",
        "https://10.0.0.5/public/brain",
        "https://git.internal/private/brain",
        "https://host.localdomain/private/brain",
        "https://host.home.arpa/private/brain",
        "https://example.invalid/private/brain",
        "https://example.test/private/brain",
        "https://example.123/private/brain",
        "https://example.com:8443/public/brain",
    ],
)
def test_declared_repository_rejects_local_private_and_credential_bearing_locators(
    tmp_path: Path,
    repository: str,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    manifest_path = source / ".tradingcodex" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["repository"] = repository
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="declared repository"):
        install_investment_brain(workspace, local_source=source, actor="test")

    registry_path = workspace / BRAIN_REGISTRY_PATH
    assert not registry_path.exists() or repository not in registry_path.read_text(
        encoding="utf-8"
    )


def test_workspace_local_source_uses_portable_relative_locator_after_clone(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(workspace / "investment-brains")

    record = install_investment_brain(workspace, local_source=source, actor="test")

    expected_locator = source.relative_to(workspace).as_posix()
    assert record["source"]["location"] == expected_locator
    registry_text = (workspace / BRAIN_REGISTRY_PATH).read_text(encoding="utf-8")
    assert str(workspace.resolve()) not in registry_text

    clone = tmp_path / "cloned-workspace"
    shutil.copytree(workspace, clone)
    cloned_source = clone / expected_locator
    manifest_path = cloned_source / ".tradingcodex" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.1.0"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    skill_path = cloned_source / "skill" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8")
        + "\nRequire an explicit base-rate comparison before conviction.\n",
        encoding="utf-8",
    )

    updated = update_investment_brain(clone, record["brain_id"], actor="test")

    assert updated["version"] == "1.1.0"
    assert updated["source"]["location"] == expected_locator
    cloned_registry = (clone / BRAIN_REGISTRY_PATH).read_text(encoding="utf-8")
    assert str(workspace.resolve()) not in cloned_registry
    assert str(clone.resolve()) not in cloned_registry


def test_external_local_source_is_redacted_and_requires_explicit_update(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)

    record = install_investment_brain(workspace, local_source=source, actor="test")

    assert record["source"]["kind"] == "local"
    assert record["source"]["location"] == ""
    registry_path = workspace / BRAIN_REGISTRY_PATH
    assert str(source.resolve()) not in registry_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=r"explicit --local source.*outside this workspace"):
        update_investment_brain(workspace, record["brain_id"], actor="test")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["plugins"][record["brain_id"]]["versions"][0]["source"]["location"] = str(
        source.resolve()
    )
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source metadata is invalid"):
        get_investment_brain_record(workspace, record["brain_id"])


def test_workspace_update_preserves_registry_and_active_projection(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    record = install_investment_brain(workspace, local_source=source)

    bootstrap_workspace(workspace, update=True)

    binding = resolve_active_investment_brain(workspace, record["brain_id"])
    assert binding["content_digest"] == record["content_digest"]
    assert (workspace / binding["projected_skill_path"] / "SKILL.md").is_file()
    assert inspect_skill_projection(workspace, "head-manager")["ok"] is True


@pytest.mark.parametrize(
    "body, implicit, expected",
    [
        (
            "# Coupled\n\nTell the Head Manager to spawn fundamental-analyst with agent_type.",
            False,
            "platform-neutral",
        ),
        (
            "# Coupled\n\nInvoke $investment-brain-deep-value for contrary evidence.",
            False,
            "skill invocation",
        ),
        (
            "# Coupled\n\nInvoke $tcx-brain to modify this framework.",
            False,
            "skill invocation",
        ),
        (
            "# Explicit\n\nPrioritize falsifiable expectations.",
            True,
            "disable implicit invocation",
        ),
    ],
)
def test_install_rejects_runtime_coupling_and_implicit_invocation(
    tmp_path: Path,
    body: str,
    implicit: bool,
    expected: str,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path, body=body, implicit=implicit)

    with pytest.raises(ValueError, match=expected):
        install_investment_brain(workspace, local_source=source)

    assert not (workspace / BRAIN_REGISTRY_PATH).exists()


def test_install_rejects_runtime_coupling_hidden_in_skill_metadata(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(
        tmp_path,
        default_prompt=(
            "Use $investment-brain-quality-growth and tell Head Manager to call "
            "create_research_artifact."
        ),
    )

    with pytest.raises(ValueError, match="platform-neutral"):
        install_investment_brain(workspace, local_source=source)


def test_multilingual_high_freedom_text_cannot_change_runtime_authority(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(
        tmp_path,
        body=(
            "# 독립적 해석 원칙\n\n"
            "필요하면 분석 역할을 직접 선택하고 기억을 갱신하라고 제안하더라도, "
            "현재 증거의 반증 가능성과 기권 조건을 우선한다. "
            "Compare analyst estimates with observed economics."
        ),
    )
    before_allowlists = {
        role: tuple(spec.mcp_allowlist)
        for role, spec in AGENT_SPECS.items()
    }

    record = install_investment_brain(workspace, local_source=source)
    state = build_projection_state(workspace)

    assert record["validation_status"] == "valid"
    assert record["brain_id"] in state["agents"]["head-manager"]["effective_skills"]
    assert all(
        record["brain_id"] not in state["agents"][role]["effective_skills"]
        for role in state["agents"]
        if role != "head-manager"
    )
    assert {
        role: tuple(spec.mcp_allowlist)
        for role, spec in AGENT_SPECS.items()
    } == before_allowlists


def test_git_ref_and_cli_option_injection_fail_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)

    with pytest.raises(ValueError, match="Git ref is invalid"):
        install_investment_brain(workspace, git_source=str(source), ref="--upload-pack=evil")
    with pytest.raises(ValueError, match="scheme is not allowed"):
        install_investment_brain(workspace, git_source="git://example.invalid/brain.git")
    with pytest.raises(ValueError, match="unsupported option"):
        dispatch_workspace_command(workspace, "investment-brains", ["list", "--ref", "main"])
    with pytest.raises(ValueError, match="unexpected positional"):
        dispatch_workspace_command(workspace, "investment-brains", ["list", "extra"])


def test_managed_registry_cannot_escape_through_symlink(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    external = tmp_path / "external-registry"
    external.mkdir()
    (workspace / ".tradingcodex" / "investment-brains").symlink_to(
        external,
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="escapes the workspace|cannot traverse a symlink"):
        install_investment_brain(workspace, local_source=source)

    with pytest.raises(ValueError, match="escapes the workspace|cannot traverse a symlink"):
        read_investment_brain_records(workspace)

    assert list(external.iterdir()) == []


def test_host_global_same_id_collision_blocks_install_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    home = tmp_path / "isolated-home"
    codex_home = home / ".codex"
    collision = codex_home / "skills" / "investment-brain-quality-growth" / "SKILL.md"
    collision.parent.mkdir(parents=True)
    collision.write_text("---\nname: investment-brain-quality-growth\ndescription: Collision.\n---\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    with pytest.raises(ValueError, match="host-global same-id skill collision"):
        install_investment_brain(workspace, local_source=source)

    assert not (workspace / BRAIN_REGISTRY_PATH).exists()
    assert not (workspace / ".agents" / "skills" / "investment-brain-quality-growth").exists()


def test_late_host_global_collision_blocks_resolution_and_projection_but_allows_deactivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    home = tmp_path / "isolated-home"
    codex_home = home / ".codex"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    record = install_investment_brain(workspace, local_source=source)
    collision = home / ".agents" / "skills" / record["brain_id"] / "SKILL.md"
    collision.parent.mkdir(parents=True)
    collision.write_text("---\nname: collision\ndescription: Collision.\n---\n", encoding="utf-8")

    inspected = get_investment_brain_record(workspace, record["brain_id"])
    assert inspected["validation_status"] == "blocked"
    assert any("host-global same-id skill collision" in error for error in inspected["validation_errors"])
    with pytest.raises(ValueError, match="host-global same-id skill collision"):
        resolve_active_investment_brain(workspace, record["brain_id"])
    with pytest.raises(ValueError, match="host-global same-id skill collision"):
        project_agent_configuration(workspace, applied_by="collision-test")

    deactivated = set_investment_brain_status(workspace, record["brain_id"], "inactive")
    assert deactivated["status"] == "inactive"
    assert not (workspace / deactivated["projected_skill_path"]).exists()


@pytest.mark.parametrize("managed_relative", [Path(".codex"), Path(".tradingcodex/generated")])
def test_generic_projection_symlink_escape_fails_without_external_writes(
    tmp_path: Path,
    managed_relative: Path,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    managed = workspace / managed_relative
    external = tmp_path / ("external-" + "-".join(managed_relative.parts))
    managed.rename(external)
    managed.symlink_to(external, target_is_directory=True)
    before = {
        path.relative_to(external).as_posix(): path.read_bytes()
        for path in external.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="projection target .*workspace|projection target .*symlink"):
        install_investment_brain(workspace, local_source=source)

    after = {
        path.relative_to(external).as_posix(): path.read_bytes()
        for path in external.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert read_investment_brain_records(workspace) == []


def test_read_only_resolution_rejects_projection_symlink_and_registry_path_escape(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    record = install_investment_brain(workspace, local_source=source)
    projection = workspace / record["projected_skill_path"]
    external_projection = tmp_path / "external-projection"
    projection.rename(external_projection)
    projection.symlink_to(external_projection, target_is_directory=True)

    with pytest.raises(ValueError, match="invalid|symlink|escapes"):
        resolve_active_investment_brain(workspace, record["brain_id"])

    projection.unlink()
    external_projection.rename(projection)
    registry_path = workspace / BRAIN_REGISTRY_PATH
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["plugins"][record["brain_id"]]["versions"][0]["package_path"] = "../../outside"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="package paths are invalid"):
        read_investment_brain_records(workspace)


def test_bundle_manifest_directory_symlink_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    manifest_dir = source / ".tradingcodex"
    external = tmp_path / "external-manifest"
    manifest_dir.rename(external)
    manifest_dir.symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="regular plugin.json|symlink"):
        install_investment_brain(workspace, local_source=source)


def test_bundle_rejects_oversized_manifest(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    (source / ".tradingcodex" / "plugin.json").write_bytes(
        b"{" + b" " * brain_service.MAX_MANIFEST_BYTES
    )

    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="manifest exceeds the size limit"):
        install_investment_brain(workspace, local_source=source)
    assert not (workspace / BRAIN_REGISTRY_PATH).exists()


def test_bundle_rejects_oversized_skill(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    (source / "skill" / "SKILL.md").write_bytes(b"x" * (brain_service.MAX_SKILL_BYTES + 1))

    with pytest.raises(ValueError, match="SKILL.md exceeds the size limit"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_bundle_rejects_oversized_reference(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    (source / "skill" / "references" / "falsifiers.md").write_bytes(
        b"x" * (brain_service.MAX_REFERENCE_BYTES + 1)
    )

    with pytest.raises(ValueError, match="reference falsifiers.md exceeds the size limit"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_bundle_rejects_oversized_aggregate(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    references = source / "skill" / "references"
    for index in range(9):
        (references / f"large-{index}.md").write_bytes(b"x" * brain_service.MAX_REFERENCE_BYTES)

    with pytest.raises(ValueError, match="bundle exceeds the total size limit"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_bundle_rejects_too_many_reference_files_directories_and_depth(tmp_path: Path) -> None:
    file_source = _bundle(tmp_path)
    file_references = file_source / "skill" / "references"
    for index in range(brain_service.MAX_REFERENCE_FILES):
        (file_references / f"extra-{index}.md").write_text("bounded\n", encoding="utf-8")
    with pytest.raises(ValueError, match="too many files"):
        install_investment_brain(_workspace(tmp_path / "file-case"), local_source=file_source)

    directory_source = _bundle(tmp_path)
    directory_references = directory_source / "skill" / "references"
    for index in range(brain_service.MAX_REFERENCE_DIRECTORIES + 1):
        (directory_references / f"directory-{index}").mkdir()
    with pytest.raises(ValueError, match="too many directories"):
        install_investment_brain(_workspace(tmp_path / "directory-case"), local_source=directory_source)

    depth_source = _bundle(tmp_path)
    deep = depth_source / "skill" / "references"
    for index in range(brain_service.MAX_REFERENCE_DEPTH):
        deep = deep / f"level-{index}"
        deep.mkdir()
    (deep / "too-deep.md").write_text("bounded\n", encoding="utf-8")
    with pytest.raises(ValueError, match="maximum directory depth"):
        install_investment_brain(_workspace(tmp_path / "depth-case"), local_source=depth_source)


def test_bundle_rejects_non_regular_reference_and_invalid_utf8(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable on this platform")
    fifo_source = _bundle(tmp_path)
    fifo = fifo_source / "skill" / "references" / "falsifiers.md"
    fifo.unlink()
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="regular files only"):
        install_investment_brain(_workspace(tmp_path / "fifo-case"), local_source=fifo_source)

    invalid_source = _bundle(tmp_path)
    (invalid_source / "skill" / "references" / "falsifiers.md").write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="readable UTF-8"):
        install_investment_brain(_workspace(tmp_path / "utf8-case"), local_source=invalid_source)


@pytest.mark.parametrize("yaml_graph", ["anchor", "alias", "tag"])
def test_bundle_rejects_yaml_graph_features(tmp_path: Path, yaml_graph: str) -> None:
    source = _bundle(tmp_path)
    metadata = source / "skill" / "agents" / "openai.yaml"
    if yaml_graph == "anchor":
        text = metadata.read_text(encoding="utf-8").replace(
            'display_name: "Quality Growth Brain"',
            'display_name: &label "Quality Growth Brain"',
        )
    elif yaml_graph == "alias":
        text = metadata.read_text(encoding="utf-8").replace(
            'display_name: "Quality Growth Brain"',
            'display_name: &label "Quality Growth Brain"',
        ).replace(
            'short_description: "Frame quality-growth investment judgments"',
            "short_description: *label",
        )
    else:
        text = metadata.read_text(encoding="utf-8").replace(
            'display_name: "Quality Growth Brain"',
            'display_name: !!str "Quality Growth Brain"',
        )
    metadata.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="aliases, anchors, or tags"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_bundle_rejects_yaml_graph_features_in_skill_frontmatter(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    skill = source / "skill" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8").replace(
            "name: investment-brain-quality-growth",
            "name: &identity investment-brain-quality-growth",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="aliases, anchors, or tags"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_bundle_rejects_unsupported_root_entry(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    (source / "README.md").write_text("Not part of the strict bundle.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="root contains unsupported entries"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_bundle_root_enumeration_is_bounded(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    for index in range(5):
        (source / f"extra-{index}.md").write_text("unsupported\n", encoding="utf-8")

    with pytest.raises(ValueError, match="bundle root contains too many entries"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_install_copies_the_validated_snapshot_when_source_changes_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    source_skill = source / "skill" / "SKILL.md"
    validated_content = source_skill.read_bytes()
    original_copy = brain_service._copy_validated_bundle

    def mutate_then_copy(bundle: brain_service.ValidatedBrainBundle, target: Path) -> None:
        source_skill.write_bytes(b"x" * (brain_service.MAX_SKILL_BYTES + 1))
        original_copy(bundle, target)

    monkeypatch.setattr(brain_service, "_copy_validated_bundle", mutate_then_copy)

    record = install_investment_brain(workspace, local_source=source)

    assert source_skill.stat().st_size > brain_service.MAX_SKILL_BYTES
    assert (workspace / record["source_file"]).read_bytes() == validated_content
    assert resolve_active_investment_brain(workspace, record["brain_id"])["content_digest"] == record[
        "content_digest"
    ]


def test_bundle_rejects_executable_local_payload(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX executable mode is unavailable on native Windows")
    source = _bundle(tmp_path)
    skill = source / "skill" / "SKILL.md"
    skill.chmod(0o755)

    with pytest.raises(ValueError, match="must not be executable"):
        install_investment_brain(_workspace(tmp_path), local_source=source)


def test_projection_uses_validated_snapshot_when_package_changes_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    record = install_investment_brain(workspace, local_source=source)
    projection = workspace / record["projected_skill_path"]
    validated_skill = (projection / "SKILL.md").read_bytes()
    shutil.rmtree(projection)
    package_skill = workspace / record["source_file"]
    original_validate = brain_service._validate_installed_version
    mutated = False

    def validate_then_mutate(
        root: Path,
        plugin: dict[str, object],
        selected: dict[str, object],
    ) -> brain_service.ValidatedBrainBundle:
        nonlocal mutated
        bundle = original_validate(root, plugin, selected)
        if not mutated:
            package_skill.write_text("mutated after validation\n", encoding="utf-8")
            mutated = True
        return bundle

    monkeypatch.setattr(brain_service, "_validate_installed_version", validate_then_mutate)

    records = brain_service.project_investment_brain_skills(workspace)

    assert mutated is True
    assert (projection / "SKILL.md").read_bytes() == validated_skill
    assert records[0]["validation_status"] == "blocked"


def test_failed_projection_rollback_cannot_clobber_concurrent_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    v1 = _bundle(tmp_path, version="1.0.0")
    v2 = _bundle(
        tmp_path,
        version="1.1.0",
        body="# Quality Growth\n\nPrioritize expectations, reinvestment, and explicit abstention.",
    )
    brain_id = install_investment_brain(workspace, local_source=v1)["brain_id"]
    projection_entered = threading.Event()
    release_projection = threading.Event()
    original_project = brain_service._project_all
    errors: list[Exception] = []

    def controlled_project(root: Path, actor: str) -> None:
        if actor == "thread-a":
            projection_entered.set()
            assert release_projection.wait(timeout=5)
            raise RuntimeError("injected projection failure")
        original_project(root, actor)

    monkeypatch.setattr(brain_service, "_project_all", controlled_project)

    def update_worker() -> None:
        try:
            update_investment_brain(workspace, brain_id, local_source=v2, actor="thread-a")
        except Exception as exc:  # pragma: no branch - asserted below
            errors.append(exc)

    result: list[dict[str, object]] = []

    def deactivate_worker() -> None:
        result.append(set_investment_brain_status(workspace, brain_id, "inactive", actor="thread-b"))

    update_thread = threading.Thread(target=update_worker)
    update_thread.start()
    assert projection_entered.wait(timeout=5)
    deactivate_thread = threading.Thread(target=deactivate_worker)
    deactivate_thread.start()
    assert deactivate_thread.is_alive()
    release_projection.set()
    update_thread.join(timeout=5)
    deactivate_thread.join(timeout=5)

    assert errors and str(errors[0]) == "injected projection failure"
    assert result[0]["status"] == "inactive"
    final = get_investment_brain_record(workspace, brain_id)
    assert final["version"] == "1.0.0"
    assert final["status"] == "inactive"
    assert len(final["versions"]) == 1
    packages = workspace / ".tradingcodex" / "investment-brains" / "packages" / brain_id
    assert len(list(packages.iterdir())) == 1


def test_update_rollback_remove_retains_immutable_versions(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    v1 = _bundle(tmp_path, version="1.0.0")
    v2 = _bundle(
        tmp_path,
        version="1.1.0",
        body="# Quality Growth\n\nPrioritize reinvestment, expectations, base rates, contrary evidence, and abstention.",
    )
    install_investment_brain(workspace, local_source=v1, actor="test")

    updated = update_investment_brain(
        workspace,
        "investment-brain-quality-growth",
        local_source=v2,
        actor="test",
    )
    assert updated["version"] == "1.1.0"
    assert len(updated["versions"]) == 2
    assert resolve_active_investment_brain(workspace, updated["brain_id"])["version"] == "1.1.0"

    rolled_back = rollback_investment_brain(workspace, updated["brain_id"], actor="test")
    assert rolled_back["version"] == "1.0.0"
    assert len(rolled_back["versions"]) == 2
    assert (workspace / rolled_back["source_file"]).is_file()

    removed = remove_investment_brain(workspace, updated["brain_id"], actor="test")
    assert removed["status"] == "removed"
    assert len(removed["versions"]) == 2
    assert not (workspace / removed["projected_skill_path"]).exists()
    with pytest.raises(ValueError, match="not active"):
        resolve_active_investment_brain(workspace, updated["brain_id"])
    assert "investment-brain-quality-growth/SKILL.md" not in (
        workspace / ".codex" / "config.toml"
    ).read_text(encoding="utf-8")


def test_update_version_must_exceed_all_installed_versions_after_rollback(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    v1 = _bundle(tmp_path, version="1.0.0")
    v2 = _bundle(
        tmp_path,
        version="2.0.0",
        body="# Quality Growth\n\nVersion two adds explicit regime and base-rate comparison.",
    )
    v15 = _bundle(
        tmp_path,
        version="1.5.0",
        body="# Quality Growth\n\nThis intermediate release must not supersede an installed version two.",
    )
    v3 = _bundle(
        tmp_path,
        version="3.0.0",
        body="# Quality Growth\n\nVersion three adds stronger falsification and abstention guidance.",
    )
    brain_id = install_investment_brain(workspace, local_source=v1)["brain_id"]
    update_investment_brain(workspace, brain_id, local_source=v2)
    rolled_back = rollback_investment_brain(workspace, brain_id)
    assert rolled_back["version"] == "1.0.0"

    with pytest.raises(ValueError, match="higher than every installed version"):
        update_investment_brain(workspace, brain_id, local_source=v15)

    idempotent = update_investment_brain(workspace, brain_id, local_source=v1)
    assert idempotent["version"] == "1.0.0"
    assert len(idempotent["versions"]) == 2

    reselected = rollback_investment_brain(workspace, brain_id, version="2.0.0")
    assert reselected["version"] == "2.0.0"
    assert rollback_investment_brain(workspace, brain_id, version="2.0.0")["version"] == "2.0.0"
    assert rollback_investment_brain(workspace, brain_id, version="1.0.0")["version"] == "1.0.0"

    updated = update_investment_brain(workspace, brain_id, local_source=v3)
    assert updated["version"] == "3.0.0"
    assert len(updated["versions"]) == 3
    assert rollback_investment_brain(workspace, brain_id)["version"] == "2.0.0"


def test_update_rejects_republished_version_and_projection_tampering(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path, version="1.0.0")
    record = install_investment_brain(workspace, local_source=source)
    republished = _bundle(
        tmp_path,
        version="1.0.0",
        body="# Changed\n\nThis silently changes an already published version.",
    )

    with pytest.raises(ValueError, match="version content is immutable"):
        update_investment_brain(workspace, record["brain_id"], local_source=republished)

    projected = workspace / record["projected_skill_path"] / "SKILL.md"
    projected.write_text(projected.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid|digest mismatch"):
        resolve_active_investment_brain(workspace, record["brain_id"])


def test_activation_revalidates_immutable_package(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    record = install_investment_brain(workspace, local_source=source, active=False)
    package_skill = workspace / record["source_file"]
    package_skill.write_text(package_skill.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="content digest mismatch"):
        set_investment_brain_status(workspace, record["brain_id"], "active")


def test_compromised_active_brain_can_always_be_deactivated(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    record = install_investment_brain(workspace, local_source=source)
    package_skill = workspace / record["source_file"]
    package_skill.write_text(package_skill.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")

    deactivated = set_investment_brain_status(workspace, record["brain_id"], "inactive")

    assert deactivated["status"] == "inactive"
    assert not (workspace / deactivated["projected_skill_path"]).exists()


def test_explicit_git_install_records_resolved_commit_without_mutating_source(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "brain@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Brain Publisher"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "--quiet", "-m", "Publish brain"], check=True)
    before = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    record = install_investment_brain(workspace, git_source=str(source), ref="HEAD", actor="test")

    assert record["source"]["kind"] == "git"
    assert record["source"]["location"] == ""
    assert record["source"]["ref"] == "HEAD"
    assert len(record["source"]["resolved_revision"]) == 40
    assert str(source.resolve()) not in (workspace / BRAIN_REGISTRY_PATH).read_text(encoding="utf-8")
    after = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert before == after == ""


def test_git_install_rejects_executable_bundle_file(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX executable mode is unavailable on native Windows")
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    (source / "skill" / "SKILL.md").chmod(0o755)
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "brain@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Brain Publisher"],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "--quiet", "-m", "Executable payload"],
        check=True,
    )

    with pytest.raises(ValueError, match="non-executable regular files"):
        install_investment_brain(workspace, git_source=str(source), ref="HEAD")


def test_git_install_ignores_inherited_repository_and_config_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "brain@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Brain Publisher"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "--quiet", "-m", "Publish brain"], check=True)
    source_revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    poison = tmp_path / "poison"
    poison.mkdir()
    subprocess.run(["git", "init", "--quiet", str(poison)], check=True)
    poison_config = (poison / ".git" / "config").read_bytes()

    monkeypatch.setenv("GIT_DIR", str(poison / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(poison))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "poison-index"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "url.file:///definitely-not-the-source.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(source))
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ext")
    monkeypatch.setenv("GIT_SSH_COMMAND", "unsafe-ssh-helper")

    record = install_investment_brain(workspace, git_source=str(source), ref="HEAD", actor="test")

    assert record["source"]["resolved_revision"] == source_revision
    assert record["validation_status"] == "valid"
    assert (poison / ".git" / "config").read_bytes() == poison_config


def test_git_materialization_never_executes_ext_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = tmp_path / "ext-helper-executed"
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ext")

    with pytest.raises(ValueError, match="could not be materialized"):
        brain_service._run_git(["ls-remote", f"ext::touch {sentinel}"])

    assert not sentinel.exists()


def test_git_bundle_is_preflighted_before_selected_paths_are_checked_out(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    (source / "README.md").write_text("outside the strict bundle\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "brain@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Brain Publisher"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "--quiet", "-m", "Publish brain"], check=True)

    with pytest.raises(ValueError, match="Git bundle root contains unsupported entries"):
        install_investment_brain(workspace, git_source=str(source), ref="HEAD")

    assert not (workspace / BRAIN_REGISTRY_PATH).exists()


def test_cli_install_list_inspect_and_remove(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)

    dispatch_workspace_command(workspace, "investment-brains", ["install", "--local", str(source)])
    install_payload = json.loads(capsys.readouterr().out)
    assert install_payload["brain_id"] == "investment-brain-quality-growth"

    dispatch_workspace_command(workspace, "investment-brains", ["list"])
    assert "investment-brain-quality-growth\t1.0.0\tactive" in capsys.readouterr().out
    dispatch_workspace_command(
        workspace,
        "investment-brains",
        ["inspect", "investment-brain-quality-growth"],
    )
    assert json.loads(capsys.readouterr().out)["validation_status"] == "valid"
    dispatch_workspace_command(
        workspace,
        "investment-brains",
        ["remove", "investment-brain-quality-growth"],
    )
    assert json.loads(capsys.readouterr().out)["status"] == "removed"
    assert get_investment_brain_record(workspace, "investment-brain-quality-growth")["status"] == "removed"


def test_cli_validate_is_non_mutating(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _workspace(tmp_path)
    source = _bundle(tmp_path)
    before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }

    dispatch_workspace_command(
        workspace,
        "investment-brains",
        ["validate", "--local", str(source)],
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "valid"
    assert payload["brain_id"] == "investment-brain-quality-growth"
    assert payload["version"] == "1.0.0"
    assert payload["registry_mutated"] is False
    assert payload["projection_mutated"] is False
    assert payload["file_count"] == 4
    assert payload["total_bytes"] > 0
    assert not (workspace / BRAIN_REGISTRY_PATH).exists()
    assert not (workspace / ".agents/skills/investment-brain-quality-growth").exists()
    assert {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    } == before
