from __future__ import annotations

import os
from typing import Any, Callable

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from tradingcodex_service.application.common import local_or_staff_source
from tradingcodex_service.application.viewer import (
    get_artifact_detail,
    get_skill_detail,
    viewer_snapshot,
)
from tradingcodex_service.application.workspaces import WorkspaceSelectionError, bind_request_workspace
from tradingcodex_service.runtime_profile import LOCAL_PROFILE


def _read_allowed(view: Callable[..., JsonResponse]) -> Callable[..., JsonResponse]:
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        if not local_or_staff_source(
            request,
            api_key=os.environ.get("TRADINGCODEX_API_KEY"),
            api_key_principal=os.environ.get("TRADINGCODEX_API_PRINCIPAL"),
            allow_local_readonly=settings.SERVICE_PROFILE == LOCAL_PROFILE,
        ):
            return _error("forbidden", "TradingCodex viewer is local or staff only.", 403)
        try:
            root = bind_request_workspace(request)
        except WorkspaceSelectionError as exc:
            return _error("invalid_workspace", str(exc), 404)
        request.tradingcodex_workspace_root = root
        return view(request, *args, **kwargs)

    return wrapped


@require_GET
@_read_allowed
def snapshot(request: HttpRequest) -> JsonResponse:
    return JsonResponse(viewer_snapshot(request.tradingcodex_workspace_root))


@require_GET
@_read_allowed
def skill_detail(request: HttpRequest, skill_id: str) -> JsonResponse:
    return _read_response(lambda: get_skill_detail(request.tradingcodex_workspace_root, skill_id))


@require_GET
@_read_allowed
def artifact_detail(request: HttpRequest, artifact_id: str) -> JsonResponse:
    return _read_response(lambda: get_artifact_detail(request.tradingcodex_workspace_root, artifact_id))


def _read_response(operation: Callable[[], dict[str, Any]]) -> JsonResponse:
    try:
        return JsonResponse(operation())
    except ValueError as exc:
        status = 404 if "not found" in str(exc) or "unknown" in str(exc) else 400
        return _error("not_found" if status == 404 else "invalid_request", str(exc), status)
    except Exception:
        return _error("unavailable", "Viewer data is temporarily unavailable.", 503)


def _error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)
