from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.http import require_GET

from tradingcodex_service.application.harness import (
    build_subagent_starter_prompt,
    get_harness_health,
    get_harness_topology,
    get_role_detail,
    list_policy_overview,
    list_recent_activity,
)
from tradingcodex_service.application.portfolio import (
    DEFAULT_ACCOUNT_ID,
    DEFAULT_PORTFOLIO_ID,
    DEFAULT_STRATEGY_ID,
    default_paper_portfolio_state,
)
from tradingcodex_service.application.research import (
    list_research_artifacts,
)
from tradingcodex_service.application.runtime import (
    tradingcodex_db_path,
    workspace_context_payload,
)


PRODUCT_NAV = [
    {"label": "Dashboard", "href": "/", "key": "dashboard"},
    {"label": "Harness", "href": "/harness/", "key": "harness"},
    {"label": "Research", "href": "/research/", "key": "research"},
    {"label": "Portfolio", "href": "/portfolio/", "key": "portfolio"},
    {"label": "Orders", "href": "/orders/", "key": "orders"},
    {"label": "Policy", "href": "/policy/", "key": "policy"},
    {"label": "Activity", "href": "/activity/", "key": "activity"},
]


def require_local_or_staff(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        remote_addr = request.META.get("REMOTE_ADDR", "")
        if remote_addr in {"127.0.0.1", "::1", ""}:
            return view(request, *args, **kwargs)
        if getattr(request, "user", None) and request.user.is_staff:
            return view(request, *args, **kwargs)
        return HttpResponseForbidden("TradingCodex web is local or staff only.")

    return wrapped


def workspace_root() -> Path:
    return Path(os.environ.get("TRADINGCODEX_WORKSPACE_ROOT", os.getcwd())).expanduser().resolve()


def base_context(active: str) -> dict[str, Any]:
    root = workspace_root()
    return {
        "active": active,
        "nav_items": PRODUCT_NAV,
        "db_path": str(tradingcodex_db_path()),
        "workspace_context": workspace_context_payload(root),
    }


@require_GET
@require_local_or_staff
def dashboard(request: HttpRequest) -> HttpResponse:
    root = workspace_root()
    topology = get_harness_topology(root)
    context = {
        **base_context("dashboard"),
        "topology": topology,
        "selected_role": get_role_detail("head-manager", root),
        "health": get_harness_health(root),
        "activity": list_recent_activity(root, limit=7),
        "policy": list_policy_overview(root),
        "portfolio": portfolio_overview(),
        "research": research_overview(root),
    }
    return render(request, "web/dashboard.html", context)


@require_GET
@require_local_or_staff
def harness(request: HttpRequest) -> HttpResponse:
    root = workspace_root()
    selected = request.GET.get("role") or "head-manager"
    context = {
        **base_context("harness"),
        "topology": get_harness_topology(root),
        "selected_role": get_role_detail(selected, root),
        "health": get_harness_health(root),
        "activity": list_recent_activity(root, limit=8),
    }
    return render(request, "web/harness.html", context)


@require_GET
@require_local_or_staff
def role_inspector(request: HttpRequest, role: str) -> HttpResponse:
    return render(
        request,
        "web/fragments/role_inspector.html",
        {"selected_role": get_role_detail(role, workspace_root())},
    )


@require_GET
@require_local_or_staff
def research(request: HttpRequest) -> HttpResponse:
    root = workspace_root()
    result = list_research_artifacts(root, {"include_markdown": False, "limit": 100})
    context = {
        **base_context("research"),
        "artifacts": result.get("artifacts", []),
        "research": research_overview(root),
    }
    return render(request, "web/research.html", context)


@require_GET
@require_local_or_staff
def portfolio(request: HttpRequest) -> HttpResponse:
    context = {**base_context("portfolio"), "portfolio": portfolio_overview()}
    return render(request, "web/portfolio.html", context)


@require_GET
@require_local_or_staff
def orders(request: HttpRequest) -> HttpResponse:
    context = {**base_context("orders"), **orders_overview()}
    return render(request, "web/orders.html", context)


@require_GET
@require_local_or_staff
def policy(request: HttpRequest) -> HttpResponse:
    context = {**base_context("policy"), "policy": list_policy_overview(workspace_root())}
    return render(request, "web/policy.html", context)


@require_GET
@require_local_or_staff
def activity(request: HttpRequest) -> HttpResponse:
    context = {**base_context("activity"), "activity": list_recent_activity(workspace_root(), limit=50)}
    return render(request, "web/activity.html", context)


@require_GET
@require_local_or_staff
def starter_prompt(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "")
    context = {
        **base_context("workflow"),
        "query": query,
        "starter_prompt": build_subagent_starter_prompt(query) if query.strip() else "",
    }
    return render(request, "web/starter_prompt.html", context)


@require_GET
@require_local_or_staff
def starter_prompt_fragment(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "")
    return render(
        request,
        "web/fragments/starter_prompt.html",
        {"query": query, "starter_prompt": build_subagent_starter_prompt(query) if query.strip() else ""},
    )


def research_overview(root: Path) -> dict[str, Any]:
    result = list_research_artifacts(root, {"include_markdown": False, "limit": 5})
    artifacts = result.get("artifacts", [])
    universes = sorted({artifact.get("universe") for artifact in artifacts if artifact.get("universe")})
    return {"count": len(artifacts), "recent": artifacts, "universes": universes}


def portfolio_overview() -> dict[str, Any]:
    try:
        from apps.portfolio.models import PortfolioSnapshot

        latest = PortfolioSnapshot.objects.order_by("-created_at", "-id").first()
        if latest and isinstance(latest.payload, dict):
            state = dict(latest.payload)
            state.setdefault("updated_at", latest.created_at.isoformat())
        else:
            state = default_paper_portfolio_state(DEFAULT_PORTFOLIO_ID, DEFAULT_ACCOUNT_ID, DEFAULT_STRATEGY_ID)
        positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
        return {
            "cash_krw": state.get("cash_krw", 0),
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
        }
    except Exception:
        state = default_paper_portfolio_state(DEFAULT_PORTFOLIO_ID, DEFAULT_ACCOUNT_ID, DEFAULT_STRATEGY_ID)
        return {
            "cash_krw": state["cash_krw"],
            "positions": [],
            "positions_count": 0,
            "updated_at": state["updated_at"],
            "portfolio_id": DEFAULT_PORTFOLIO_ID,
            "account_id": DEFAULT_ACCOUNT_ID,
            "strategy_id": DEFAULT_STRATEGY_ID,
        }


def orders_overview() -> dict[str, Any]:
    try:
        from apps.orders.models import ApprovalReceipt, ExecutionResult, OrderIntent

        return {
            "order_intents": OrderIntent.objects.order_by("-created_at", "-id")[:30],
            "approval_receipts": ApprovalReceipt.objects.order_by("-created_at", "-id")[:30],
            "execution_results": ExecutionResult.objects.order_by("-created_at", "-id")[:30],
            "order_count": OrderIntent.objects.count(),
            "approval_count": ApprovalReceipt.objects.count(),
            "execution_count": ExecutionResult.objects.count(),
        }
    except Exception:
        return {
            "order_intents": [],
            "approval_receipts": [],
            "execution_results": [],
            "order_count": 0,
            "approval_count": 0,
            "execution_count": 0,
        }
