from __future__ import annotations

import json
import os
from pathlib import Path

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from tradingcodex_service.application.runtime import tradingcodex_db_path
from tradingcodex_service.mcp_runtime import handle_mcp_batch


def workspace_root() -> Path:
    return Path(os.environ.get("TRADINGCODEX_WORKSPACE_ROOT", os.getcwd())).resolve()


def _origin_allowed(request: HttpRequest) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")


def _authenticated(request: HttpRequest) -> bool:
    api_key = os.environ.get("TRADINGCODEX_MCP_KEY")
    if not api_key:
        return request.META.get("REMOTE_ADDR", "") in {"127.0.0.1", "::1", ""}
    return request.headers.get("X-TradingCodex-MCP-Key") == api_key


@csrf_exempt
def mcp_endpoint(request: HttpRequest):
    if not _origin_allowed(request):
        return JsonResponse({"error": "origin not allowed"}, status=403)
    if not _authenticated(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    if request.method == "GET":
        return JsonResponse({
            "status": "ok",
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "protocolVersion": "2025-06-18",
            "methods": ["initialize", "tools/list", "tools/call", "resources/list", "prompts/list"],
            "db_path": str(tradingcodex_db_path()),
            "central_local_service": True,
        })
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        message = json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        return JsonResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}, status=400)
    response = handle_mcp_batch(workspace_root(), message)
    if response is None:
        return HttpResponse(status=202)
    if response == []:
        return HttpResponse(status=202)
    return JsonResponse(response, safe=not isinstance(response, list))
