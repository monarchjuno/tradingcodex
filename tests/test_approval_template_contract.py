from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_template(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_risk_approve_order_skill_names_stale_table_fields() -> None:
    body = read_template("workspace_templates/modules/repo-skills/files/.tradingcodex/subagents/skills/risk-manager/approve-order/SKILL.md")
    required = [
        "valid_until",
        "invalidates_on",
        "quote_as_of",
        "cash_as_of",
        "order_status_as_of",
        "quote drift",
        "cash delta",
        "order-status delta",
        "replacement-ticket creation",
        "terminal-refresh failure",
        "age threshold",
        "request_order_approval",
        "recheck",
    ]
    missing = [item for item in required if item not in body]
    assert not missing


def test_order_ticket_skill_names_cash_reserve_stress_line() -> None:
    body = read_template("workspace_templates/modules/repo-skills/files/.tradingcodex/subagents/skills/portfolio-manager/create-order-ticket/SKILL.md")
    required = [
        "cash-reserve stress",
        "pre-batch orderable",
        "total notional",
        "fee/tax reserve",
        "post-batch residual",
        "residual warning threshold",
    ]
    missing = [item for item in required if item not in body]
    assert not missing
