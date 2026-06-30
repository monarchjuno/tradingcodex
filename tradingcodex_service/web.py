from __future__ import annotations

from dataclasses import replace
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from tradingcodex_cli.generator import bootstrap_workspace
from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    EXPECTED_SUBAGENTS,
    SKILL_SPECS,
    build_projection_state,
    create_or_update_optional_skill,
    delete_optional_skill,
    read_agent_additional_instructions,
    read_strategy_skill_records,
    set_optional_skill_status,
    write_agent_additional_instructions,
)
from tradingcodex_service.application.harness import (
    PROFILE_FIELD_KEYS,
    build_workflow_loop_preview,
    build_workflow_intake_summary,
    build_subagent_starter_prompt,
    get_harness_health,
    get_harness_topology,
    get_role_detail,
    investment_universe_label,
    list_policy_overview,
    list_recent_activity,
)
from tradingcodex_service.application.markdown_preview import (
    MarkdownPreview,
    read_markdown_preview,
    render_markdown_preview,
)
from tradingcodex_service.application.brokers import (
    connect_broker_connector,
    create_external_mcp_broker_connection,
    ensure_paper_broker_connection,
    list_broker_connections,
    list_reconciliation_runs,
    sync_broker_account,
)
from tradingcodex_service.application.decision_packages import get_decision_package, list_decision_packages
from tradingcodex_service.application.orders import create_order_ticket, run_order_checks
from tradingcodex_service.application.research import list_workspace_research_artifacts
from tradingcodex_service.application.portfolio import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_PORTFOLIO_ID,
    DEFAULT_STRATEGY_ID,
    default_paper_portfolio_state,
    list_positions,
)
from tradingcodex_service.application.runtime import (
    WORKSPACE_MANIFEST_REL,
    active_profile_for_workspace,
    ensure_runtime_database,
    persist_workspace_context_if_available,
    save_active_profile_for_workspace,
    workspace_context_payload,
)
from apps.mcp.services import (
    check_external_mcp_connection,
    discover_external_mcp_connection,
    evaluate_external_mcp_proxy_call,
    import_external_mcp_discovery,
    register_external_mcp_connection,
    review_external_mcp_tool,
)
from tradingcodex_service.application.common import local_or_staff_source


PRODUCT_NAV = [
    {"label": "Plan", "href": "/workflow/starter-prompt/", "key": "workflow"},
    {"label": "Decisions", "href": "/decisions/", "key": "decisions"},
    {"label": "Agents", "href": "/harness/agents/", "key": "agents"},
    {"label": "Strategies", "href": "/harness/strategies/", "key": "strategies"},
    {"label": "Brokers", "href": "/brokers/", "key": "brokers"},
    {"label": "Research", "href": "/research/", "key": "research"},
    {"label": "Data Sources", "href": "/integrations/mcp/", "key": "mcp-router"},
]
WORKSPACE_SESSION_KEY = "tradingcodex_selected_workspace_id"
WORKSPACE_NOTICE_SESSION_KEY = "tradingcodex_workspace_notice"
WORKSPACE_ERROR_SESSION_KEY = "tradingcodex_workspace_error"
SKILL_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "workspace_templates" / "modules" / "repo-skills" / "files" / ".agents" / "skills"
SUBAGENT_SKILL_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "workspace_templates" / "modules" / "repo-skills" / "files" / ".tradingcodex" / "subagents" / "skills"


def require_local_or_staff(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if local_or_staff_source(request):
            return view(request, *args, **kwargs)
        return HttpResponseForbidden("TradingCodex web is local or staff only.")

    return wrapped


def default_workspace_root() -> Path:
    return Path(os.environ.get("TRADINGCODEX_WORKSPACE_ROOT", os.getcwd())).expanduser().resolve()


def workspace_root(request: HttpRequest | None = None) -> Path:
    fallback = default_workspace_root()
    if request is None or not hasattr(request, "session"):
        return fallback

    query_workspace_id = str(request.GET.get("workspace") or "").strip()
    if query_workspace_id:
        selected = _workspace_option_by_id(query_workspace_id)
        if selected:
            request.session[WORKSPACE_SESSION_KEY] = selected["workspace_id"]
            request.session.modified = True
            return Path(selected["path"]).expanduser().resolve()
        request.session.pop(WORKSPACE_SESSION_KEY, None)
        request.session.modified = True
        return fallback

    session_workspace_id = request.session.get(WORKSPACE_SESSION_KEY)
    if isinstance(session_workspace_id, str) and session_workspace_id:
        selected = _workspace_option_by_id(session_workspace_id)
        if selected:
            return Path(selected["path"]).expanduser().resolve()
        request.session.pop(WORKSPACE_SESSION_KEY, None)
        request.session.modified = True

    return fallback


def base_context(request: HttpRequest, active: str) -> dict[str, Any]:
    root = workspace_root(request)
    context = workspace_context_payload(root)
    options = workspace_options(root)
    sidebar_options = _workspace_sidebar_options(options)
    return {
        "active": active,
        "nav_items": PRODUCT_NAV,
        "workspace_context": context,
        "workspace_options": options,
        "workspace_visible_options": sidebar_options["visible"],
        "workspace_hidden_options": sidebar_options["hidden"],
        "selected_workspace_id": context["workspace_id"],
        "workspace_notice": _pop_session_message(request, WORKSPACE_NOTICE_SESSION_KEY),
        "workspace_error": _pop_session_message(request, WORKSPACE_ERROR_SESSION_KEY),
    }


def _workspace_sidebar_options(options: list[dict[str, Any]], *, visible_limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    visible: list[dict[str, Any]] = []
    seen: set[str] = set()
    selected = next((option for option in options if option.get("selected")), None)
    if selected:
        visible.append(selected)
        seen.add(str(selected.get("workspace_id")))
    for option in options:
        workspace_id = str(option.get("workspace_id"))
        if workspace_id in seen:
            continue
        if len(visible) < visible_limit:
            visible.append(option)
            seen.add(workspace_id)
    hidden = [option for option in options if str(option.get("workspace_id")) not in seen]
    return {"visible": visible, "hidden": hidden}


@require_GET
@require_local_or_staff
def dashboard(request: HttpRequest) -> HttpResponse:
    return redirect("web-starter-prompt")


@require_GET
@require_local_or_staff
def harness(request: HttpRequest) -> HttpResponse:
    return redirect("web-agents")


@require_GET
@require_local_or_staff
def role_inspector(request: HttpRequest, role: str) -> HttpResponse:
    selected_role = get_role_detail(role, workspace_root(request))
    selected_role["latest_artifacts"] = _with_universe_labels(selected_role.get("latest_artifacts", []))
    return render(
        request,
        "web/fragments/role_inspector.html",
        {"selected_role": selected_role},
    )


@require_GET
@require_local_or_staff
def agents_index(request: HttpRequest) -> HttpResponse:
    return _render_agents(request)


@require_GET
@require_local_or_staff
def decisions(request: HttpRequest) -> HttpResponse:
    root = workspace_root(request)
    packages = list_decision_packages(root).get("packages", [])
    selected_id = str(request.GET.get("decision") or "").strip()
    try:
        selected_package = get_decision_package(root, selected_id) if selected_id else None
    except ValueError:
        selected_package = None
    preview = (
        render_markdown_preview(
            selected_package.get("markdown") or "",
            source_file=selected_package.get("path") or "",
            source_label="decision package",
            strip_frontmatter=False,
        )
        if selected_package
        else None
    )
    return render(
        request,
        "web/decisions.html",
        {
            **base_context(request, "decisions"),
            "packages": packages,
            "selected_package": selected_package,
            "decision_preview": preview,
        },
    )


@require_GET
@require_local_or_staff
def strategies_index(request: HttpRequest) -> HttpResponse:
    root = workspace_root(request)
    strategies = _strategy_web_records(root, read_strategy_skill_records(root))
    selected_name = str(request.GET.get("name") or "").strip()
    selected_strategy = next((strategy for strategy in strategies if strategy.get("name") == selected_name), None) if selected_name else None
    if selected_strategy:
        strategy_mode = "detail"
    else:
        strategy_mode = "list"
        selected_name = ""
    state = build_projection_state(root)
    preview = _skill_markdown_preview(root, state, selected_name) if selected_strategy else None
    return render(
        request,
        "web/strategies.html",
        {
            **base_context(request, "strategies"),
            "strategies": strategies,
            "strategy_mode": strategy_mode,
            "selected_strategy": selected_strategy,
            "selected_strategy_name": selected_name,
            "strategy_preview": preview,
        },
    )


@require_POST
@require_local_or_staff
def optional_skill_create(request: HttpRequest, role: str) -> HttpResponse:
    return _mutating_redirect(
        request,
        f"/harness/agents/?role={role}",
        lambda root: create_or_update_optional_skill(
            root,
            role,
            _post(request, "name"),
            description=_post(request, "description"),
            body=_post(request, "body"),
            status=_post(request, "status") or "draft",
            actor="web",
        ),
    )


@require_POST
@require_local_or_staff
def optional_skill_update(request: HttpRequest, role: str, name: str) -> HttpResponse:
    return _mutating_redirect(
        request,
        f"/harness/agents/?role={role}&skill={name}",
        lambda root: create_or_update_optional_skill(
            root,
            role,
            name,
            description=_post(request, "description"),
            body=_post(request, "body"),
            status=_post(request, "status") or "draft",
            actor="web",
        ),
    )


@require_POST
@require_local_or_staff
def optional_skill_activate(request: HttpRequest, role: str, name: str) -> HttpResponse:
    return _mutating_redirect(request, f"/harness/agents/?role={role}&skill={name}", lambda root: set_optional_skill_status(root, role, name, "active", actor="web"))


@require_POST
@require_local_or_staff
def optional_skill_archive(request: HttpRequest, role: str, name: str) -> HttpResponse:
    return _mutating_redirect(request, f"/harness/agents/?role={role}&skill={name}", lambda root: set_optional_skill_status(root, role, name, "archived", actor="web"))


@require_POST
@require_local_or_staff
def optional_skill_delete(request: HttpRequest, role: str, name: str) -> HttpResponse:
    return _mutating_redirect(request, f"/harness/agents/?role={role}", lambda root: delete_optional_skill(root, role, name, force=_post(request, "force") == "true", actor="web"))


@require_POST
@require_local_or_staff
def agent_instruction_update(request: HttpRequest, role: str) -> HttpResponse:
    return _mutating_redirect(
        request,
        f"/harness/agents/?role={role}",
        lambda root: write_agent_additional_instructions(root, role, _post_preserve_newlines(request, "body"), actor="web"),
    )


@require_POST
@require_local_or_staff
def workspace_open(request: HttpRequest) -> HttpResponse:
    next_url = _safe_next_url(str(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/research/"))
    raw_path = str(request.POST.get("workspace_path") or request.POST.get("path") or "").strip()
    if not raw_path:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = "Workspace path is required."
        request.session.modified = True
        return redirect(next_url)
    return _open_workspace_path(request, Path(raw_path).expanduser().resolve(), next_url)


@require_POST
@require_local_or_staff
def workspace_create(request: HttpRequest) -> HttpResponse:
    next_url = _safe_next_url(str(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/research/"))
    raw_path = str(request.POST.get("workspace_path") or request.POST.get("path") or "").strip()
    if not raw_path:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = "Workspace path is required."
        request.session.modified = True
        return redirect(next_url)
    return _create_workspace_path(request, Path(raw_path).expanduser().resolve(), next_url)


@require_POST
@require_local_or_staff
def workspace_browse(request: HttpRequest) -> HttpResponse:
    next_url = _safe_next_url(str(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/research/"))
    try:
        target = _choose_workspace_directory()
    except Exception as exc:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = f"Could not choose workspace folder: {exc}"
        request.session.modified = True
        return redirect(next_url)
    return _open_workspace_path(request, target, next_url)


@require_POST
@require_local_or_staff
def workspace_remove(request: HttpRequest, workspace_id: str) -> HttpResponse:
    next_url = _safe_next_url(str(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/research/"))
    try:
        ensure_runtime_database(None)
        from apps.harness.models import WorkspaceContext

        WorkspaceContext.objects.filter(workspace_id=workspace_id).delete()
        if request.session.get(WORKSPACE_SESSION_KEY) == workspace_id:
            request.session.pop(WORKSPACE_SESSION_KEY, None)
        request.session[WORKSPACE_NOTICE_SESSION_KEY] = "Workspace reference removed. Files were not touched."
        request.session.modified = True
    except Exception as exc:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = f"Could not remove workspace reference: {exc}"
        request.session.modified = True
    return redirect(_url_without_workspace(next_url))


def _open_workspace_path(request: HttpRequest, target: Path, next_url: str) -> HttpResponse:
    try:
        if not (target / WORKSPACE_MANIFEST_REL).exists():
            raise ValueError("Selected path is not a TradingCodex workspace. Create a workspace before opening it.")
        ensure_runtime_database(target)
        context = persist_workspace_context_if_available(target)
        request.session[WORKSPACE_SESSION_KEY] = context["workspace_id"]
        request.session[WORKSPACE_NOTICE_SESSION_KEY] = "Workspace opened."
        request.session.modified = True
        return redirect(_url_with_workspace(next_url, context["workspace_id"]))
    except Exception as exc:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = f"Could not open workspace: {exc}"
        request.session.modified = True
        return redirect(next_url)


def _create_workspace_path(request: HttpRequest, target: Path, next_url: str) -> HttpResponse:
    try:
        if (target / WORKSPACE_MANIFEST_REL).exists():
            return _open_workspace_path(request, target, next_url)
        bootstrap_workspace(target, force=False)
        ensure_runtime_database(target)
        context = persist_workspace_context_if_available(target)
        request.session[WORKSPACE_SESSION_KEY] = context["workspace_id"]
        request.session[WORKSPACE_NOTICE_SESSION_KEY] = "Workspace created and opened."
        request.session.modified = True
        return redirect(_url_with_workspace(next_url, context["workspace_id"]))
    except Exception as exc:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = f"Could not create workspace: {exc}"
        request.session.modified = True
        return redirect(next_url)


def _choose_workspace_directory() -> Path:
    if os.name != "posix":
        raise RuntimeError("native folder picker is only available on this local desktop platform")
    script = 'POSIX path of (choose folder with prompt "Open TradingCodex workspace")'
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "folder selection cancelled"
        raise RuntimeError(message)
    selected = result.stdout.strip()
    if not selected:
        raise RuntimeError("folder selection cancelled")
    return Path(selected).expanduser().resolve()


def _render_agents(request: HttpRequest, selected_role: str | None = None) -> HttpResponse:
    root = workspace_root(request)
    state = build_projection_state(root)
    role = selected_role or request.GET.get("role") or "head-manager"
    if role not in state["agents"]:
        role = "head-manager"
    active_panel = request.GET.get("panel") or "skills"
    if active_panel not in {"skills", "notes"}:
        active_panel = "skills"
    agent = state["agents"].get(role)
    if not agent:
        return HttpResponse("Unknown agent role.", status=404)
    include_internal_skills = request.GET.get("include_internal") == "true"
    required_skill_ids = list(agent.get("builtin_skills", []))
    if role == "head-manager" and not include_internal_skills:
        required_skill_ids = [
            skill_id
            for skill_id in required_skill_ids
            if state.get("skills", {}).get(skill_id, {}).get("user_visible")
        ]
    required_skills = [_skill_preview_item(root, state, skill_id, "required") for skill_id in required_skill_ids]
    optional_skills = [
        _skill_preview_item(root, state, str(record.get("name") or ""), "optional", record=record)
        for record in agent.get("optional_skills", [])
        if record.get("status") == "active"
    ]
    if role == "head-manager":
        optional_skills.extend(
            _skill_preview_item(root, state, skill_id, "strategy")
            for skill_id, skill in sorted(state.get("skills", {}).items())
            if skill.get("source") == "strategy" and skill.get("active")
        )
    skill_id = request.GET.get("skill") or (required_skills[0]["id"] if required_skills else optional_skills[0]["id"] if optional_skills else "")
    skill_preview = _skill_markdown_preview(root, state, skill_id) if skill_id else render_markdown_preview("_No skill selected._")
    workspace_id = request.GET.get("workspace") or ""

    def agents_href(*, next_role: str = role, panel: str = active_panel, skill: str = "") -> str:
        params = {"role": next_role, "panel": panel}
        if workspace_id:
            params["workspace"] = workspace_id
        if skill:
            params["skill"] = skill
        fragment = "notes-panel" if panel == "notes" else "skill-browser"
        return f"/harness/agents/?{urlencode(params)}#{fragment}"

    for item in [*required_skills, *optional_skills]:
        item["selected"] = item["id"] == skill_id
        item["web_href"] = agents_href(panel="skills", skill=item["id"])

    role_cards = []
    for card in [state["agents"]["head-manager"], *[state["agents"][agent_role] for agent_role in EXPECTED_SUBAGENTS]]:
        item = dict(card)
        item["web_href"] = agents_href(next_role=str(card.get("role") or "head-manager"), panel=active_panel)
        role_cards.append(item)
    context = {
        **base_context(request, "agents"),
        "state": state,
        "head_manager": state["agents"]["head-manager"],
        "agents": [state["agents"][agent_role] for agent_role in EXPECTED_SUBAGENTS],
        "role_cards": role_cards,
        "selected_agent": agent,
        "required_skills": required_skills,
        "optional_skills": optional_skills,
        "include_internal_skills": include_internal_skills,
        "selected_skill_id": skill_id,
        "skill_preview": skill_preview,
        "active_panel": active_panel,
        "skills_panel_href": agents_href(panel="skills", skill=skill_id),
        "notes_panel_href": agents_href(panel="notes"),
        "agent": agent,
        "additional_instructions": read_agent_additional_instructions(root, role),
    }
    return render(request, "web/agents.html", context)


@require_GET
@require_local_or_staff
def research(request: HttpRequest) -> HttpResponse:
    root = workspace_root(request)
    artifacts = _with_universe_labels(list_workspace_research_artifacts(root))
    selected_artifact_id = request.GET.get("artifact") or ""
    selected_artifact: dict[str, Any] | None = None
    artifact_preview: MarkdownPreview | None = None
    if selected_artifact_id:
        selected_artifact = next((artifact for artifact in artifacts if artifact.get("artifact_id") == selected_artifact_id or artifact.get("path") == selected_artifact_id), None)
        if selected_artifact:
            artifact_preview = read_markdown_preview(
                root / str(selected_artifact["path"]),
                source_file=str(selected_artifact["path"]),
                source_label="workspace research file",
            )
            artifact_preview = _with_preview_universe_label(artifact_preview)
        else:
            artifact_preview = render_markdown_preview("_Research file is unavailable._", source_label="workspace research file")
    loop_preview = build_workflow_loop_preview(
        root,
        str(request.GET.get("q") or ""),
        [str(selected_artifact["path"])] if selected_artifact else None,
    )
    context = {
        **base_context(request, "research"),
        "artifacts": artifacts,
        "selected_artifact": selected_artifact,
        "selected_artifact_id": selected_artifact_id,
        "artifact_preview": artifact_preview,
        "workflow_loop_preview": loop_preview,
        "research": research_overview(root),
    }
    return render(request, "web/research.html", context)


def _with_universe_labels(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labelled: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item["universe_label"] = investment_universe_label(item.get("universe"))
        labelled.append(item)
    return labelled


def _with_preview_universe_label(preview: MarkdownPreview) -> MarkdownPreview:
    items = []
    for item in preview.metadata_items:
        display_item = dict(item)
        if display_item.get("key") == "universe":
            display_item["value"] = investment_universe_label(display_item.get("value"))
        items.append(display_item)
    return replace(preview, metadata_items=items)


def _strategy_web_records(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    web_records: list[dict[str, Any]] = []
    for record in records:
        if record.get("name") == "strategy-creator":
            continue
        source_file = str(record.get("source_file") or "")
        preview = read_markdown_preview(root / source_file, source_file=source_file, source_label="strategy skill")
        fields = dict(record.get("frontmatter") or {})
        web_records.append(
            {
                **record,
                "heading": preview.heading or record.get("name"),
                "description": fields.get("description") or record.get("description") or "",
            }
        )
    return web_records


@require_GET
@require_local_or_staff
def portfolio(request: HttpRequest) -> HttpResponse:
    root = workspace_root(request)
    context = {**base_context(request, "portfolio"), "portfolio": portfolio_overview(root)}
    return render(request, "web/portfolio.html", context)


@require_POST
@require_local_or_staff
def portfolio_sync(request: HttpRequest) -> HttpResponse:
    return _service_redirect(request, "/portfolio/", lambda: sync_broker_account(workspace_root(request), {"broker_id": _post(request, "broker_id") or "paper-trading", "principal_id": "portfolio-manager"}))


@require_GET
@require_local_or_staff
def broker_center(request: HttpRequest) -> HttpResponse:
    context = {**base_context(request, "brokers"), **broker_center_overview(request)}
    return render(request, "web/brokers.html", context)


@require_POST
@require_local_or_staff
def broker_add_paper(request: HttpRequest) -> HttpResponse:
    def operation() -> Any:
        connection = ensure_paper_broker_connection(workspace_root(request), actor="web")
        if _post(request, "sync") == "true":
            sync_broker_account(workspace_root(request), {"broker_id": connection.broker_id, "principal_id": "portfolio-manager"})
        return connection

    return _service_redirect(request, "/brokers/", operation)


@require_POST
@require_local_or_staff
def broker_add_mcp(request: HttpRequest) -> HttpResponse:
    return _service_redirect(
        request,
        "/brokers/",
        lambda: create_external_mcp_broker_connection(
            workspace_root(request),
            broker_id=_post(request, "broker_id"),
            display_name=_post(request, "display_name") or _post(request, "broker_id"),
            router_name=_post(request, "router_name") or _post(request, "broker_id"),
            discovery_payload=_post(request, "discovery_payload"),
            credential_ref=_post(request, "credential_ref"),
            actor="web",
        ),
    )


@require_POST
@require_local_or_staff
def broker_connect(request: HttpRequest) -> HttpResponse:
    return _service_redirect(
        request,
        "/brokers/",
        lambda: connect_broker_connector(
            workspace_root(request),
            {
                "principal_id": "head-manager",
                "broker": _post(request, "broker") or _post(request, "broker_id"),
                "provider": _post(request, "provider"),
                "broker_id": _post(request, "broker_id") or _post(request, "broker"),
                "credential_ref": _post(request, "credential_ref"),
                "environment": _post(request, "environment"),
                "mode": _post(request, "mode") or "read-only",
            },
        ),
    )


@require_POST
@require_local_or_staff
def broker_sync(request: HttpRequest, broker_id: str) -> HttpResponse:
    return _service_redirect(request, f"/brokers/?broker={broker_id}", lambda: sync_broker_account(workspace_root(request), {"broker_id": broker_id, "principal_id": "portfolio-manager"}))


@require_GET
@require_local_or_staff
def orders(request: HttpRequest) -> HttpResponse:
    context = {**base_context(request, "orders"), **orders_overview(request)}
    return render(request, "web/orders.html", context)


@require_POST
@require_local_or_staff
def order_ticket_create(request: HttpRequest) -> HttpResponse:
    return _service_redirect(
        request,
        "/orders/",
        lambda: create_order_ticket(
            workspace_root(request),
            {
                "source": "web",
                "principal_id": "portfolio-manager",
                "ticket_id": _post(request, "ticket_id"),
                "natural_language": _post(request, "natural_language"),
                "symbol": _post(request, "symbol"),
                "side": _post(request, "side"),
                "quantity": _post(request, "quantity"),
                "limit_price": _post(request, "limit_price"),
                "currency": _post(request, "currency") or "KRW",
                "broker_id": _post(request, "broker_id") or "paper-trading",
                "time_in_force": _post(request, "time_in_force") or "day",
            },
        ),
    )


@require_POST
@require_local_or_staff
def order_ticket_checks(request: HttpRequest, ticket_id: str) -> HttpResponse:
    return _service_redirect(request, f"/orders/?ticket={ticket_id}", lambda: run_order_checks(workspace_root(request), {"ticket_id": ticket_id, "principal_id": "portfolio-manager"}))


@require_GET
@require_local_or_staff
def policy(request: HttpRequest) -> HttpResponse:
    context = {**base_context(request, "policy"), "policy": list_policy_overview(workspace_root(request))}
    return render(request, "web/policy.html", context)


@require_GET
@require_local_or_staff
def activity(request: HttpRequest) -> HttpResponse:
    context = {**base_context(request, "activity"), "activity": list_recent_activity(workspace_root(request), limit=50)}
    return render(request, "web/activity.html", context)


@require_GET
@require_local_or_staff
def mcp_router(request: HttpRequest) -> HttpResponse:
    context = {**base_context(request, "mcp-router"), **mcp_router_overview()}
    return render(request, "web/mcp_router.html", context)


@require_POST
@require_local_or_staff
def mcp_router_create(request: HttpRequest) -> HttpResponse:
    return _service_redirect(
        request,
        "/integrations/mcp/",
        lambda: register_external_mcp_connection(
            workspace_root(request),
            {
                "principal_id": "web",
                "name": _post(request, "name"),
                "label": _post(request, "label"),
                "transport": _post(request, "transport") or "stdio",
                "command": _post(request, "command"),
                "args": _post_preserve_newlines(request, "args"),
                "env": _post_preserve_newlines(request, "env"),
                "url": _post(request, "url"),
                "credential_ref": _post(request, "credential_ref"),
                "enabled": _post(request, "enabled") == "true",
            },
        ),
    )


@require_POST
@require_local_or_staff
def mcp_router_check(request: HttpRequest, router_id: int) -> HttpResponse:
    return _service_redirect(
        request,
        f"/integrations/mcp/#router-{router_id}",
        lambda: check_external_mcp_connection(
            workspace_root(request),
            {
                "principal_id": "web",
                "router_id": router_id,
                "timeout": _post(request, "timeout"),
            },
        ),
    )


@require_POST
@require_local_or_staff
def mcp_router_discover(request: HttpRequest, router_id: int) -> HttpResponse:
    return _service_redirect(
        request,
        f"/integrations/mcp/#router-{router_id}",
        lambda: discover_external_mcp_connection(
            workspace_root(request),
            {
                "principal_id": "web",
                "router_id": router_id,
                "timeout": _post(request, "timeout"),
            },
        ),
    )


@require_POST
@require_local_or_staff
def mcp_router_import(request: HttpRequest, router_id: int) -> HttpResponse:
    def operation() -> Any:
        from apps.mcp.models import McpRouter

        router = McpRouter.objects.get(pk=router_id)
        return import_external_mcp_discovery(router, _post(request, "discovery_payload"), actor="web")

    return _service_redirect(request, f"/integrations/mcp/#router-{router_id}", operation)


@require_POST
@require_local_or_staff
def mcp_external_tool_update(request: HttpRequest, tool_id: int) -> HttpResponse:
    return _service_redirect(
        request,
        f"/integrations/mcp/#tool-{tool_id}",
        lambda: review_external_mcp_tool(
            workspace_root(request),
            {
                "principal_id": "web",
                "tool_id": tool_id,
                "category": _post(request, "category"),
                "risk_level": _post(request, "risk_level"),
                "sensitivity": _post(request, "sensitivity"),
                "canonical_capability": _post(request, "canonical_capability"),
                "proxy_mode": _post(request, "proxy_mode"),
                "allowed_roles": _split_csv(_post(request, "allowed_roles")),
                "enabled": _post(request, "enabled") == "true",
                "review_status": "reviewed",
            },
        ),
    )


@require_POST
@require_local_or_staff
def mcp_external_tool_check(request: HttpRequest, tool_id: int) -> HttpResponse:
    def operation() -> Any:
        from apps.mcp.models import McpExternalTool

        tool = McpExternalTool.objects.get(pk=tool_id)
        raw_arguments = _post(request, "arguments")
        arguments = json.loads(raw_arguments) if raw_arguments else {}
        return evaluate_external_mcp_proxy_call(
            workspace_root(request),
            tool,
            principal_id=_post(request, "principal_id") or "head-manager",
            arguments=arguments if isinstance(arguments, dict) else {},
            actor="web",
        )

    return _service_redirect(request, f"/integrations/mcp/#tool-{tool_id}", operation)


@require_GET
@require_local_or_staff
def starter_prompt(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "")
    root = workspace_root(request)
    context = {
        **base_context(request, "workflow"),
        "query": query,
        "starter_prompt_examples": starter_prompt_examples(),
        "starter_prompt": build_subagent_starter_prompt(query, root) if query.strip() else "",
        "intake_summary": build_workflow_intake_summary(query, root),
        "workflow_loop_preview": build_workflow_loop_preview(root, query),
    }
    return render(request, "web/starter_prompt.html", context)


@require_GET
@require_local_or_staff
def starter_prompt_fragment(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "")
    root = workspace_root(request)
    return render(
        request,
        "web/fragments/starter_prompt.html",
        {
            "query": query,
            "starter_prompt": build_subagent_starter_prompt(query, root) if query.strip() else "",
            "intake_summary": build_workflow_intake_summary(query, root),
            "workflow_loop_preview": build_workflow_loop_preview(root, query),
        },
    )


def starter_prompt_examples() -> list[dict[str, str]]:
    return [
        {
            "label": "Idea research",
            "prompt": "TSLA feels interesting, no order",
        },
        {
            "label": "Rough hunch",
            "prompt": "AAPL seems cheap, but I am not sure why. No order.",
        },
        {
            "label": "Decision support",
            "prompt": "TSLA fair value and whether it fits my portfolio, no order",
        },
        {
            "label": "Portfolio risk",
            "prompt": "Rates and oil impact on my NVDA position, no order",
        },
        {
            "label": "Crypto trend",
            "prompt": "BTC trend review, no trading",
        },
    ]


@require_POST
@require_local_or_staff
def starter_profile_update(request: HttpRequest) -> HttpResponse:
    root = workspace_root(request)
    profile = active_profile_for_workspace(root)
    investor_profile = dict(profile.get("investor_profile") or {})
    allowed_keys = set(PROFILE_FIELD_KEYS.values())
    for key in allowed_keys:
        value = _post(request, key)
        if value:
            investor_profile[key] = value
    profile["investor_profile"] = investor_profile
    save_active_profile_for_workspace(root, profile)
    persist_workspace_context_if_available(root)
    query = _post(request, "q")
    return render(
        request,
        "web/fragments/starter_prompt.html",
        {
            "query": query,
            "starter_prompt": build_subagent_starter_prompt(query, root) if query.strip() else "",
            "intake_summary": build_workflow_intake_summary(query, root),
            "workflow_loop_preview": build_workflow_loop_preview(root, query),
            "profile_saved": True,
        },
    )


def _post(request: HttpRequest, name: str) -> str:
    return str(request.POST.get(name) or "").strip()


def _post_preserve_newlines(request: HttpRequest, name: str) -> str:
    return str(request.POST.get(name) or "")


def _mutating_redirect(request: HttpRequest, fallback_url: str, operation: Callable[[Path], Any]) -> HttpResponse:
    next_url = _safe_next_url(str(request.POST.get("next") or fallback_url))
    try:
        operation(workspace_root(request))
        request.session[WORKSPACE_NOTICE_SESSION_KEY] = "Workspace files updated."
    except Exception as exc:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = f"Could not update workspace files: {exc}"
    request.session.modified = True
    return redirect(next_url)


def _service_redirect(request: HttpRequest, fallback_url: str, operation: Callable[[], Any]) -> HttpResponse:
    next_url = _safe_next_url(str(request.POST.get("next") or fallback_url))
    try:
        operation()
        request.session[WORKSPACE_NOTICE_SESSION_KEY] = "MCP settings updated."
    except Exception as exc:
        request.session[WORKSPACE_ERROR_SESSION_KEY] = f"Could not update MCP settings: {exc}"
    request.session.modified = True
    return redirect(next_url)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def research_overview(root: Path) -> dict[str, Any]:
    artifacts = list_workspace_research_artifacts(root)[:5]
    universes = sorted({artifact.get("universe") for artifact in artifacts if artifact.get("universe")})
    return {"count": len(artifacts), "recent": artifacts, "universes": universes}


def _skill_preview_item(
    root: Path,
    state: dict[str, Any],
    skill_id: str,
    kind: str,
    *,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    skill = state.get("skills", {}).get(skill_id, {})
    spec = SKILL_SPECS.get(skill_id)
    source_file, source_label = _skill_source(root, skill_id, skill=skill, record=record)
    return {
        "id": skill_id,
        "label": str(skill.get("label") or (spec.label if spec else "") or _skill_heading(root, skill_id, skill=skill, record=record) or skill_id),
        "kind": kind,
        "status": str((record or {}).get("status") or skill.get("status") or "active"),
        "validation_status": str((record or {}).get("validation_status") or skill.get("validation_status") or "valid"),
        "risk_tags": list((record or {}).get("risk_tags") or skill.get("risk_tags") or []),
        "source_file": source_file,
        "source_label": source_label,
        "selected": False,
    }


def _skill_markdown_preview(root: Path, state: dict[str, Any], skill_id: str) -> MarkdownPreview:
    skill = state.get("skills", {}).get(skill_id, {})
    source_file, source_label = _skill_source(root, skill_id, skill=skill)
    path = _skill_markdown_path(root, skill_id, skill=skill)
    return read_markdown_preview(path, source_file=source_file, source_label=source_label)


def _skill_heading(
    root: Path,
    skill_id: str,
    *,
    skill: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
) -> str:
    path = _skill_markdown_path(root, skill_id, skill=skill, record=record)
    return read_markdown_preview(path).heading


def _skill_source(
    root: Path,
    skill_id: str,
    *,
    skill: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
) -> tuple[str, str]:
    path = _skill_markdown_path(root, skill_id, skill=skill, record=record)
    try:
        return path.relative_to(root).as_posix(), "workspace skill"
    except ValueError:
        try:
            return path.relative_to(Path(__file__).resolve().parents[1]).as_posix(), "repo template"
        except ValueError:
            return str(path), "markdown file"


def _skill_markdown_path(
    root: Path,
    skill_id: str,
    *,
    skill: dict[str, Any] | None = None,
    record: dict[str, Any] | None = None,
) -> Path:
    for raw in [record.get("source_file") if record else "", skill.get("source_file") if skill else ""]:
        if raw:
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else root / candidate
            if path.exists():
                return path
    workspace_path = root / ".agents" / "skills" / skill_id / "SKILL.md"
    if workspace_path.exists():
        return workspace_path
    spec = SKILL_SPECS.get(skill_id)
    if spec and spec.scope == "subagent_shared":
        return SUBAGENT_SKILL_TEMPLATE_ROOT / "shared" / skill_id / "SKILL.md"
    if spec and spec.scope == "subagent_role":
        role = spec.owner_roles[0] if spec.owner_roles else ""
        return SUBAGENT_SKILL_TEMPLATE_ROOT / role / skill_id / "SKILL.md"
    return SKILL_TEMPLATE_ROOT / skill_id / "SKILL.md"


def workspace_options(selected_root: Path) -> list[dict[str, Any]]:
    selected_context = workspace_context_payload(selected_root)
    options: list[dict[str, Any]] = []
    try:
        ensure_runtime_database(None)
        persist_workspace_context_if_available(selected_root)
        from apps.harness.models import WorkspaceContext

        for workspace in WorkspaceContext.objects.order_by("-last_seen_at", "project_name", "id")[:20]:
            options.append(_workspace_option_from_model(workspace))
    except Exception:
        options = []

    selected_option = _workspace_option_from_context(selected_context)
    if not any(option["workspace_id"] == selected_option["workspace_id"] for option in options):
        options.insert(0, selected_option)
    for option in options:
        option["selected"] = option["workspace_id"] == selected_context["workspace_id"]
    return options[:20]


def _workspace_option_by_id(workspace_id: str) -> dict[str, Any] | None:
    if not workspace_id:
        return None
    try:
        ensure_runtime_database(None)
        from apps.harness.models import WorkspaceContext

        workspace = WorkspaceContext.objects.filter(workspace_id=workspace_id).first()
        if not workspace:
            return None
        option = _workspace_option_from_model(workspace)
        if not Path(option["path"]).expanduser().exists():
            return None
        return option
    except Exception:
        return None


def _workspace_option_from_model(workspace: Any) -> dict[str, Any]:
    active_profile = workspace.active_profile if isinstance(workspace.active_profile, dict) else {}
    path = Path(workspace.path).expanduser()
    exists = path.exists()
    bootstrapped = (path / WORKSPACE_MANIFEST_REL).exists()
    return {
        "workspace_id": workspace.workspace_id,
        "project_name": workspace.project_name,
        "path": workspace.path,
        "git_branch": workspace.git_branch,
        "active_profile": active_profile,
        "active_profile_label": str(active_profile.get("label") or active_profile.get("profile_id") or "default-paper"),
        "last_seen_at": workspace.last_seen_at,
        "exists": exists,
        "bootstrapped": bootstrapped,
        "status_label": "Ready" if exists and bootstrapped else "Not attached" if exists else "Missing",
        "selected": False,
    }


def _workspace_option_from_context(context: dict[str, Any]) -> dict[str, Any]:
    active_profile = context.get("active_profile") if isinstance(context.get("active_profile"), dict) else {}
    path = Path(str(context["path"])).expanduser()
    exists = path.exists()
    bootstrapped = (path / WORKSPACE_MANIFEST_REL).exists()
    return {
        "workspace_id": context["workspace_id"],
        "project_name": context["project_name"],
        "path": context["path"],
        "git_branch": context.get("git_branch", ""),
        "active_profile": active_profile,
        "active_profile_label": str(active_profile.get("label") or active_profile.get("profile_id") or "default-paper"),
        "last_seen_at": None,
        "exists": exists,
        "bootstrapped": bootstrapped,
        "status_label": "Ready" if exists and bootstrapped else "Not attached" if exists else "Missing",
        "selected": False,
    }


def _pop_session_message(request: HttpRequest, key: str) -> str:
    if not hasattr(request, "session"):
        return ""
    value = request.session.pop(key, "")
    if value:
        request.session.modified = True
    return str(value)


def _safe_next_url(raw_url: str) -> str:
    if not raw_url.startswith("/") or raw_url.startswith("//"):
        return "/research/"
    return raw_url


def _url_with_workspace(raw_url: str, workspace_id: str) -> str:
    split = urlsplit(_safe_next_url(raw_url))
    query = [(key, value) for key, value in parse_qsl(split.query, keep_blank_values=True) if key != "workspace"]
    query.insert(0, ("workspace", workspace_id))
    return urlunsplit((split.scheme, split.netloc, split.path or "/research/", urlencode(query), split.fragment))


def _url_without_workspace(raw_url: str) -> str:
    split = urlsplit(_safe_next_url(raw_url))
    query = [(key, value) for key, value in parse_qsl(split.query, keep_blank_values=True) if key != "workspace"]
    return urlunsplit((split.scheme, split.netloc, split.path or "/research/", urlencode(query), split.fragment))


def portfolio_overview(root: Path | str | None = None) -> dict[str, Any]:
    try:
        state = list_positions(root or default_workspace_root())
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        return {
            "cash_krw": state.get("cash_krw", 0),
            "cash": state.get("cash", {"KRW": state.get("cash_krw", 0)}),
            "positions": sorted(
                [
                    {
                        "symbol": symbol,
                        "quantity": position.get("quantity", 0),
                        "average_price": position.get("average_price", 0),
                        "currency": position.get("currency", "KRW"),
                    }
                    for symbol, position in positions.items()
                ],
                key=lambda item: item["symbol"],
            ),
            "positions_count": len(positions),
            "updated_at": state.get("updated_at", ""),
            "portfolio_id": state.get("portfolio_id", DEFAULT_PORTFOLIO_ID),
            "account_id": state.get("account_id", DEFAULT_ACCOUNT_ID),
            "strategy_id": state.get("strategy_id", DEFAULT_STRATEGY_ID),
            "reconciliation": state.get("reconciliation") or {},
            "last_sync": state.get("last_sync") or {},
            "warnings": state.get("warnings") or [],
        }
    except Exception as exc:
        state = default_paper_portfolio_state(DEFAULT_PORTFOLIO_ID, DEFAULT_ACCOUNT_ID, DEFAULT_STRATEGY_ID)
        return {
            "cash_krw": state["cash_krw"],
            "positions": [],
            "positions_count": 0,
            "updated_at": state["updated_at"],
            "portfolio_id": DEFAULT_PORTFOLIO_ID,
            "account_id": DEFAULT_ACCOUNT_ID,
            "strategy_id": DEFAULT_STRATEGY_ID,
            "reconciliation": {},
            "last_sync": {},
            "warnings": [f"Portfolio state could not be loaded: {exc}"],
        }


def orders_overview(request: HttpRequest) -> dict[str, Any]:
    try:
        from apps.orders.models import OrderTicket

        tickets = OrderTicket.objects.select_related("broker_connection", "broker_account").prefetch_related("check_runs").order_by("-created_at", "-id")[:30]
        ticket_count = OrderTicket.objects.count()

        return {
            "order_tickets": tickets,
            "ticket_count": ticket_count,
            "ready_count": OrderTicket.objects.filter(current_state__in=["READY_FOR_APPROVAL", "APPROVED"]).count(),
            "review_count": OrderTicket.objects.filter(current_state="NEEDS_REVIEW").count(),
            "broker_options": list_broker_connections(workspace_root(request)).get("connections", []),
        }
    except Exception:
        return {
            "order_tickets": [],
            "ticket_count": 0,
            "ready_count": 0,
            "review_count": 0,
            "broker_options": [],
        }


def broker_center_overview(request: HttpRequest) -> dict[str, Any]:
    try:
        ensure_runtime_database(workspace_root(request))
        broker_state = list_broker_connections(workspace_root(request))
        reconciliation_state = list_reconciliation_runs(workspace_root(request), {"limit": 8})
        from apps.portfolio.models import BrokerSyncRun

        return {
            "brokers": broker_state["connections"],
            "broker_count": len(broker_state["connections"]),
            "recent_reconciliations": reconciliation_state["reconciliation_runs"],
            "recent_sync_runs": BrokerSyncRun.objects.select_related("broker_connection").order_by("-started_at", "-id")[:10],
        }
    except Exception:
        return {"brokers": [], "broker_count": 0, "recent_reconciliations": [], "recent_sync_runs": []}


_ACCESS_MODE_LABELS = {
    "blocked": "Blocked until reviewed",
    "read_only": "Read-only",
    "summary_only": "Summary only",
    "service_path": "Service path",
    "service_adapter": "Approved service path",
}
_SENSITIVITY_LABELS = {
    "canonical_state": "System state",
}
_REVIEW_STATUS_LABELS = {
    "review_required": "Review required",
}


def _access_mode_label(value: str | None) -> str:
    return _ACCESS_MODE_LABELS.get(str(value or ""), str(value or "Unknown").replace("_", " ").title())


def _choice_options(values: list[str], labels: dict[str, str] | None = None) -> list[dict[str, str]]:
    label_map = labels or {}
    return [{"value": value, "label": label_map.get(value, value.replace("_", " ").title())} for value in values]


def _choice_label(value: str | None, labels: dict[str, str] | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    return (labels or {}).get(text, text.replace("_", " ").title())


def _action_mapping_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not mapped"
    return text.replace(".", " ").replace("_", " ").title()


def mcp_router_overview() -> dict[str, Any]:
    try:
        ensure_runtime_database(None)
        from apps.mcp.models import McpExternalTool, McpExternalToolCall, McpRouter

        routers = list(McpRouter.objects.prefetch_related("external_tools").all())
        tools = list(McpExternalTool.objects.select_related("router").all())
        recent_calls = list(McpExternalToolCall.objects.select_related("external_tool")[:15])
        for tool in tools:
            tool.access_mode_label = _access_mode_label(tool.proxy_mode)
            tool.action_mapping_label = _action_mapping_label(tool.canonical_capability)
            tool.category_label = _choice_label(tool.category)
            tool.risk_label = _choice_label(tool.risk_level)
            tool.primitive_label = _choice_label(tool.primitive)
            tool.review_status_label = _choice_label(tool.review_status, _REVIEW_STATUS_LABELS)
        for call in recent_calls:
            call.access_mode_label = _access_mode_label(call.proxy_mode)
        return {
            "routers": routers,
            "external_tools": tools,
            "recent_external_calls": recent_calls,
            "router_count": len(routers),
            "external_tool_count": len(tools),
            "enabled_external_tool_count": sum(1 for tool in tools if tool.enabled),
            "review_required_count": sum(1 for tool in tools if tool.review_status != "reviewed" or tool.drift_detected),
            "category_options": _choice_options(["market_data", "account_read", "research_write", "portfolio_state", "policy_admin", "execution", "secret", "workflow_prompt", "unknown"]),
            "risk_options": _choice_options(["read", "write", "approval", "execution", "blocked", "unknown"]),
            "sensitivity_options": _choice_options(["public", "private", "research", "canonical_state", "secret", "unknown"], _SENSITIVITY_LABELS),
            "proxy_mode_options": [{"value": value, "label": _access_mode_label(value)} for value in _ACCESS_MODE_LABELS],
        }
    except Exception:
        return {
            "routers": [],
            "external_tools": [],
            "recent_external_calls": [],
            "router_count": 0,
            "external_tool_count": 0,
            "enabled_external_tool_count": 0,
            "review_required_count": 0,
            "category_options": [],
            "risk_options": [],
            "sensitivity_options": [],
            "proxy_mode_options": [],
        }
