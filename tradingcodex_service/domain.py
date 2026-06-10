from __future__ import annotations

from pathlib import Path
from typing import Any

from tradingcodex_service.version import TRADINGCODEX_VERSION

from tradingcodex_service.application.audit import *
from tradingcodex_service.application.common import (
    _number,
    _parse_datetime,
    _resolve_path,
    _safe_read,
    _status_class,
    _unique,
    _validate_positive,
    append_jsonl,
    now_iso,
    read_json,
    sanitize_id,
    stable_hash,
    write_json,
)
from tradingcodex_service.application.harness import *
from tradingcodex_service.application.orders import *
from tradingcodex_service.application.policy import *
from tradingcodex_service.application.portfolio import *
from tradingcodex_service.application.research import *
from tradingcodex_service.application.runtime import *


def call_tool(workspace_root: Path | str, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    from tradingcodex_service.mcp_runtime import call_mcp_tool

    return call_mcp_tool(workspace_root, name, args)


from tradingcodex_service.mcp_runtime import static_mcp_tools as _static_mcp_tools

MCP_TOOLS = _static_mcp_tools()


def mcp_handle_rpc(workspace_root: Path | str, message: dict[str, Any]) -> dict[str, Any] | None:
    from tradingcodex_service.mcp_runtime import handle_mcp_rpc

    return handle_mcp_rpc(workspace_root, message)
