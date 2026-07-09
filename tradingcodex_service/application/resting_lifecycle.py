from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingcodex_service.application.common import _parse_datetime
from tradingcodex_service.application.order_lineage import (
    BROKER_TERMINAL_STATUSES,
    _latest_status,
    _lineage_for_ticket,
    _lineage_index,
    _limit,
    _row_anomalies,
)
from tradingcodex_service.application.portfolio import portfolio_keys
from tradingcodex_service.application.runtime import ensure_runtime_database, workspace_context_payload


KST = ZoneInfo("Asia/Seoul")
REGULAR_SESSION_START = time(9, 0)
REGULAR_SESSION_END = time(15, 30)
NXT_SESSION_END = time(20, 0)
REGULAR_CHECK_INTERVAL = timedelta(minutes=5)
NXT_CHECK_INTERVAL = timedelta(minutes=10)
POST_SUBMIT_WINDOW = timedelta(minutes=5)
TTL_MISSED_CHECKPOINTS = 2
CHECKPOINT_RESULT_TAIL = 5

LIFECYCLE_STATES = (
    "approved",
    "submitted",
    "acked",
    "resting",
    "ttl_stale",
    "nxt_active",
    "terminal_blocked",
    "filled",
    "cancelled",
    "expired",
    "anomaly_unverified",
)
PANEL_TICKET_STATES = {
    "APPROVED",
    "RESERVED",
    "SUBMITTED",
    "ACKED",
    "PARTIALLY_FILLED",
    "NEEDS_REVIEW",
    "FILLED",
    "REJECTED",
    "CANCELED",
    "EXPIRED",
    "FAILED",
}
WORKING_TICKET_STATES = {"SUBMITTED", "ACKED", "PARTIALLY_FILLED"}
CANCELLED_BUCKET_STATES = {"CANCELED", "REJECTED", "FAILED"}
PANEL_HARD_ANOMALIES = {"id_mismatch", "missing_broker_order_id", "duplicate_replacement", "stale_original"}
STANDING_RULE_FLAGS = {"no_auto_reprice": True, "no_overnight_carry": True}
ALWAYS_BLOCKED_ACTIONS = ("auto_reprice", "overnight_carry")

PANEL_ACTION_POLICY = {
    "approved": {
        "next_action": "submit_approved_order",
        "next_role": "execution-operator",
        "next_reason": "approved ticket awaits manual submission",
        "blocked": ALWAYS_BLOCKED_ACTIONS,
    },
    "submitted": {
        "next_action": "refresh_broker_order_status",
        "next_role": "execution-operator",
        "next_reason": "await broker acknowledgement via an observed status refresh",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order",),
    },
    "acked": {
        "next_action": "refresh_broker_order_status",
        "next_role": "execution-operator",
        "next_reason": "confirm the resting order with an observed status refresh",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order",),
    },
    "resting": {
        "next_action": "refresh_broker_order_status",
        "next_role": "execution-operator",
        "next_reason": "continue the scheduled regular-session checkpoint refreshes",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order",),
    },
    "nxt_active": {
        "next_action": "refresh_broker_order_status",
        "next_role": "execution-operator",
        "next_reason": "continue the NXT cadence refreshes until 20:00 KST",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order",),
    },
    "ttl_stale": {
        "next_action": "refresh_broker_order_status",
        "next_role": "execution-operator",
        "next_reason": "checkpoint evidence is stale; refresh before relying on any status",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order", "terminal_inference"),
    },
    "terminal_blocked": {
        "next_action": "refresh_broker_order_status",
        "next_role": "execution-operator",
        "next_reason": "terminal evidence is missing; refresh before any expiry conclusion",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order", "expire_without_evidence", "terminal_inference"),
    },
    "anomaly_unverified": {
        "next_action": "validate_order_approval_crosswalk",
        "next_role": "risk-manager",
        "next_reason": "resolve the lineage anomaly before any further order action",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order", "cancel_approved_order", "terminal_inference"),
    },
    "filled": {
        "next_action": "",
        "next_role": "",
        "next_reason": "terminal state confirmed by broker evidence",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order", "cancel_approved_order"),
    },
    "cancelled": {
        "next_action": "",
        "next_role": "",
        "next_reason": "terminal state confirmed by recorded transition",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order", "cancel_approved_order"),
    },
    "expired": {
        "next_action": "",
        "next_role": "",
        "next_reason": "terminal state confirmed by recorded transition",
        "blocked": ALWAYS_BLOCKED_ACTIONS + ("submit_approved_order", "cancel_approved_order"),
    },
}


def get_resting_lifecycle_panel(workspace_root, args=None):
    args = args or {}
    root = Path(workspace_root)
    ensure_runtime_database(root)
    from apps.orders.models import ApprovalReceipt, OrderTicket

    as_of = _resolve_as_of(args)
    portfolio_id, account_id, strategy_id = portfolio_keys(args, root)
    queryset = (
        OrderTicket.objects.select_related("broker_connection", "broker_account")
        .prefetch_related("broker_orders", "fills", "events", "check_runs")
        .filter(portfolio_id=portfolio_id, account_id=account_id, strategy_id=strategy_id)
        .filter(current_state__in=sorted(PANEL_TICKET_STATES))
    )
    ticket_id = str(args.get("ticket_id") or args.get("order_ticket_id") or args.get("order_id") or "")
    broker_order_id = str(args.get("broker_order_id") or "")
    if ticket_id:
        queryset = queryset.filter(ticket_id=ticket_id)
    if broker_order_id:
        queryset = queryset.filter(broker_orders__broker_order_id=broker_order_id)

    tickets = list(queryset.distinct().order_by("-created_at", "-id")[: _limit(args)])
    approval_by_ticket = {}
    ticket_ids = {ticket.ticket_id for ticket in tickets}
    if ticket_ids:
        for approval in ApprovalReceipt.objects.filter(order_ticket_id__in=ticket_ids).order_by("-created_at", "-id"):
            approval_by_ticket.setdefault(approval.order_ticket_id, approval)
    lineage_index = _lineage_index(portfolio_id, account_id, strategy_id)

    rows = [_panel_row(root, ticket, approval_by_ticket.get(ticket.ticket_id), lineage_index, as_of) for ticket in tickets]
    state_filter = str(args.get("lifecycle_state") or "")
    if state_filter:
        rows = [row for row in rows if row["lifecycle_state"] == state_filter]

    state_counts = Counter(row["lifecycle_state"] for row in rows)
    blockers = sorted({blocker for row in rows for blocker in row["terminal_inference_blockers"]})
    attention_states = {"ttl_stale", "terminal_blocked", "anomaly_unverified"}
    as_of_kst = as_of.astimezone(KST)
    return {
        "status": "attention" if attention_states & set(state_counts) else "ok",
        "as_of": as_of.isoformat(),
        "as_of_kst": as_of_kst.isoformat(),
        "session_phase": _session_phase(as_of_kst),
        "portfolio_id": portfolio_id,
        "account_id": account_id,
        "strategy_id": strategy_id,
        "state_counts": dict(sorted(state_counts.items())),
        "evidence_gap_count": sum(len(row["evidence_gaps"]) for row in rows),
        "terminal_inference_allowed": not blockers,
        "terminal_inference_blockers": blockers,
        "rows": rows,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(root),
    }


def _resolve_as_of(args):
    raw = str(args.get("as_of") or "")
    if not raw:
        return datetime.now(timezone.utc)
    parsed = _parse_datetime(raw)
    if parsed is None:
        raise ValueError(f"as_of is not a valid ISO datetime: {raw}")
    return parsed


def _panel_row(root, ticket, approval, lineage_index, as_of):
    broker_orders = list(ticket.broker_orders.all())
    latest = _latest_status(ticket, broker_orders)
    lineage = _lineage_for_ticket(ticket, lineage_index)
    anomalies = _row_anomalies(approval, ticket, broker_orders, lineage)
    panel_anomalies = _panel_anomalies(ticket, broker_orders, latest)
    all_anomalies = sorted(set(anomalies) | set(panel_anomalies))
    timeline = _evidence_timeline(ticket, broker_orders, as_of)
    block_reasons = _refresh_block_reasons(root, ticket)
    anchor = _submission_anchor(ticket, broker_orders, timeline)
    as_of_kst = as_of.astimezone(KST)

    checkpoints, missed_streak = _checkpoint_report(anchor, as_of, timeline, block_reasons)
    gaps = _evidence_gaps(ticket, checkpoints, timeline, block_reasons, as_of)
    state, state_reasons = _derive_lifecycle_state(
        ticket, broker_orders, latest, all_anomalies, panel_anomalies, checkpoints, timeline, missed_streak, block_reasons, as_of_kst
    )
    policy = PANEL_ACTION_POLICY[state]

    last_evidence = timeline[-1]["ts"] if timeline else None
    blocking_gaps = sorted({gap["gap"] for gap in gaps if gap["gap"] == "no_terminal_evidence"})
    row_blockers = sorted(set(all_anomalies) | set(blocking_gaps))
    return {
        "ticket_id": ticket.ticket_id,
        "ticket_state": ticket.current_state,
        "lifecycle_state": state,
        "state_reasons": state_reasons,
        "owner": ticket.created_by,
        "approval": _approval_summary(approval),
        "symbol": ticket.symbol,
        "side": ticket.side,
        "quantity": float(ticket.quantity),
        "limit_price": float(ticket.limit_price or 0),
        "time_in_force": ticket.time_in_force,
        "currency": ticket.currency,
        "account_id": ticket.account_id,
        "broker_connection_id": ticket.broker_connection.broker_id if ticket.broker_connection else "",
        "broker_account_id": ticket.broker_account.broker_account_id if ticket.broker_account else "",
        "broker_order_ids": [order.broker_order_id for order in broker_orders if order.broker_order_id],
        "latest_status": latest,
        "replacement_lineage": lineage,
        "anomalies": all_anomalies,
        "submitted_at": anchor.isoformat() if anchor else "",
        "last_evidence_at": last_evidence.isoformat() if last_evidence else "",
        "evidence_age_seconds": int((as_of - last_evidence).total_seconds()) if last_evidence else None,
        "session": {
            "trading_day": checkpoints["trading_day"] if checkpoints else "",
            "phase": _session_phase(as_of_kst),
            "is_weekend": checkpoints["is_weekend"] if checkpoints else as_of_kst.weekday() >= 5,
        },
        "checkpoints": checkpoints["report"] if checkpoints else {},
        "evidence_gaps": gaps,
        "flags": dict(STANDING_RULE_FLAGS, day_order=str(ticket.time_in_force or "").lower() == "day"),
        "next_allowed_action": {
            "action": policy["next_action"],
            "role": policy["next_role"],
            "reason": policy["next_reason"],
        },
        "blocked_actions": sorted(policy["blocked"]),
        "terminal_inference_allowed": not row_blockers,
        "terminal_inference_blockers": row_blockers,
    }


def _approval_summary(approval):
    if approval is None:
        return None
    return {
        "receipt_id": approval.receipt_id,
        "approved_by": approval.approved_by,
        "created_at": approval.created_at.isoformat() if approval.created_at else "",
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else "",
        "valid_until": approval.valid_until.isoformat() if approval.valid_until else "",
    }


def _panel_anomalies(ticket, broker_orders, latest):
    anomalies = []
    broker_status = str(latest.get("broker_status") or "").lower()
    if broker_orders and broker_status in {"", "unknown"}:
        anomalies.append("unknown_broker_status")
    if ticket.current_state in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"} and broker_orders and broker_status not in BROKER_TERMINAL_STATUSES:
        anomalies.append("terminal_without_broker_evidence")
    return anomalies


def _refresh_block_reasons(root, ticket):
    reasons = []
    if ticket.broker_connection:
        from tradingcodex_service.application.brokers import broker_connection_provider_review_reasons

        reasons.extend(broker_connection_provider_review_reasons(ticket.broker_connection, root))
    from tradingcodex_service.application.orders import _approval_table_meta_from_check_runs

    meta = _approval_table_meta_from_check_runs(list(ticket.check_runs.all()))
    terminal_status = str(meta.get("terminal_refresh_status") or "ok")
    if terminal_status != "ok":
        reasons.append(f"approval table records terminal refresh status {terminal_status}")
    return reasons


def _evidence_timeline(ticket, broker_orders, as_of):
    entries = []
    for event in ticket.events.all():
        if event.event_type != "status_refreshed":
            continue
        if event.created_at and event.created_at <= as_of:
            payload = event.payload if isinstance(event.payload, dict) else {}
            entries.append({"ts": event.created_at, "source": "status_refreshed", "broker_status": str(payload.get("broker_status") or "").lower()})
    for order in broker_orders:
        if order.last_seen_at and order.last_seen_at <= as_of:
            entries.append({"ts": order.last_seen_at, "source": "broker_order_seen", "broker_status": str(order.broker_status or "").lower()})
        if order.submitted_at and order.submitted_at <= as_of:
            entries.append({"ts": order.submitted_at, "source": "broker_order_submitted", "broker_status": str(order.broker_status or "").lower()})
    return sorted(entries, key=lambda entry: entry["ts"])


def _submission_anchor(ticket, broker_orders, timeline):
    submitted = sorted(order.submitted_at for order in broker_orders if order.submitted_at)
    if submitted:
        return submitted[0]
    refreshed = [entry["ts"] for entry in timeline if entry["source"] == "status_refreshed"]
    if refreshed:
        return refreshed[0]
    return None


def _session_phase(moment_kst):
    if moment_kst.weekday() >= 5:
        return "closed"
    moment = moment_kst.time()
    if REGULAR_SESSION_START <= moment < REGULAR_SESSION_END:
        return "regular"
    if REGULAR_SESSION_END <= moment < NXT_SESSION_END:
        return "nxt"
    if moment >= NXT_SESSION_END:
        return "post_terminal"
    return "closed"


def _cadence_ticks(start, end, interval):
    ticks = []
    due = start + interval
    while due <= end:
        ticks.append(due)
        due += interval
    return ticks


def _planned_checkpoints(anchor, as_of):
    anchor_kst = anchor.astimezone(KST)
    trading_day = anchor_kst.date()
    is_weekend = anchor_kst.weekday() >= 5
    regular_start = datetime.combine(trading_day, REGULAR_SESSION_START, tzinfo=KST)
    regular_end = datetime.combine(trading_day, REGULAR_SESSION_END, tzinfo=KST)
    nxt_end = datetime.combine(trading_day, NXT_SESSION_END, tzinfo=KST)
    regular_window = (max(anchor_kst, regular_start), regular_end)
    nxt_window = (max(anchor_kst, regular_end), nxt_end)
    return {
        "trading_day": trading_day.isoformat(),
        "is_weekend": is_weekend,
        "post_submit_due": anchor_kst,
        "regular_window": regular_window,
        "regular_ticks": [] if is_weekend else _cadence_ticks(regular_window[0], regular_window[1], REGULAR_CHECK_INTERVAL),
        "nxt_window": nxt_window,
        "nxt_ticks": [] if is_weekend else _cadence_ticks(nxt_window[0], nxt_window[1], NXT_CHECK_INTERVAL),
        "terminal_due": max(nxt_end, anchor_kst),
    }


def _evidence_in_window(timeline, start, end=None):
    for entry in timeline:
        if entry["ts"] >= start and (end is None or entry["ts"] < end):
            return entry
    return None


def _cadence_summary(window, ticks, interval, timeline, as_of_kst, blocked):
    due_ticks = [tick for tick in ticks if tick <= as_of_kst]
    results = []
    for tick in due_ticks:
        evidence = _evidence_in_window(timeline, tick, tick + interval)
        if evidence is not None:
            results.append({"due_at": tick.isoformat(), "result": "observed", "evidence_at": evidence["ts"].astimezone(KST).isoformat()})
        elif blocked:
            results.append({"due_at": tick.isoformat(), "result": "blocked", "evidence_at": None})
        else:
            results.append({"due_at": tick.isoformat(), "result": "skipped", "evidence_at": None})
    missed_streak = 0
    for result in reversed(results):
        if result["result"] == "observed":
            break
        missed_streak += 1
    if results and missed_streak and missed_streak < TTL_MISSED_CHECKPOINTS and results[-1]["result"] == "skipped" and timeline:
        results[-1]["result"] = "stale"
    upcoming = [tick for tick in ticks if tick > as_of_kst]
    return {
        "window": [window[0].isoformat(), window[1].isoformat()],
        "interval_minutes": int(interval.total_seconds() // 60),
        "planned_count": len(ticks),
        "due_count": len(due_ticks),
        "observed_count": sum(1 for result in results if result["result"] == "observed"),
        "missed_count": sum(1 for result in results if result["result"] in {"skipped", "stale"}),
        "blocked_count": sum(1 for result in results if result["result"] == "blocked"),
        "next_due_at": upcoming[0].isoformat() if upcoming else None,
        "last_results": results[-CHECKPOINT_RESULT_TAIL:],
        "result": results[-1]["result"] if results else "pending",
    }, missed_streak


def _checkpoint_report(anchor, as_of, timeline, block_reasons):
    if anchor is None:
        return None, 0
    plan = _planned_checkpoints(anchor, as_of)
    as_of_kst = as_of.astimezone(KST)
    blocked = bool(block_reasons)

    post_submit_evidence = _evidence_in_window(
        [entry for entry in timeline if entry["source"] == "status_refreshed"],
        plan["post_submit_due"],
        plan["post_submit_due"] + POST_SUBMIT_WINDOW,
    )
    if post_submit_evidence is not None:
        post_submit_result = "observed"
    elif plan["post_submit_due"] + POST_SUBMIT_WINDOW > as_of_kst:
        post_submit_result = "pending"
    else:
        post_submit_result = "blocked" if blocked else "skipped"

    regular, regular_streak = _cadence_summary(plan["regular_window"], plan["regular_ticks"], REGULAR_CHECK_INTERVAL, timeline, as_of_kst, blocked)
    nxt, nxt_streak = _cadence_summary(plan["nxt_window"], plan["nxt_ticks"], NXT_CHECK_INTERVAL, timeline, as_of_kst, blocked)

    terminal_evidence = None
    if as_of_kst >= plan["terminal_due"]:
        terminal_evidence = _evidence_in_window(
            [entry for entry in timeline if entry["broker_status"] in BROKER_TERMINAL_STATUSES],
            plan["terminal_due"],
        )
        if terminal_evidence is not None:
            terminal_result = "observed"
        else:
            terminal_result = "blocked" if blocked else "skipped"
    else:
        terminal_result = "pending"

    # NXT cadence misses continue the regular streak when NXT has started.
    missed_streak = nxt_streak if nxt["due_count"] else regular_streak
    if nxt["due_count"] and nxt_streak == nxt["due_count"]:
        missed_streak = nxt_streak + regular_streak

    report = {
        "post_submit": {
            "due_at": plan["post_submit_due"].isoformat(),
            "result": post_submit_result,
            "evidence_at": post_submit_evidence["ts"].astimezone(KST).isoformat() if post_submit_evidence else None,
        },
        "regular_5min": regular,
        "nxt_10min": nxt,
        "terminal_refresh_post_2000": {
            "due_at": plan["terminal_due"].isoformat(),
            "result": terminal_result,
            "evidence_at": terminal_evidence["ts"].astimezone(KST).isoformat() if terminal_evidence else None,
        },
    }
    return {
        "trading_day": plan["trading_day"],
        "is_weekend": plan["is_weekend"],
        "terminal_due": plan["terminal_due"],
        "report": report,
    }, missed_streak


def _evidence_gaps(ticket, checkpoints, timeline, block_reasons, as_of):
    if checkpoints is None or ticket.current_state not in WORKING_TICKET_STATES:
        return _blocked_only_gaps(block_reasons) if ticket.current_state in WORKING_TICKET_STATES else []
    gaps = []
    report = checkpoints["report"]
    if checkpoints["is_weekend"]:
        gaps.append({"gap": "non_trading_day_submission", "detail": "submission anchored on a non-trading day; no cadence checkpoints planned"})
    if report["post_submit"]["result"] in {"skipped", "blocked"}:
        gaps.append({"gap": "no_post_submit_observation", "checkpoint": "post_submit"})
    for name in ("regular_5min", "nxt_10min"):
        if report[name]["missed_count"]:
            gaps.append(
                {
                    "gap": "missed_checkpoints",
                    "checkpoint": name,
                    "count": report[name]["missed_count"],
                    "window": report[name]["window"],
                }
            )
    if report["terminal_refresh_post_2000"]["result"] in {"skipped", "blocked"}:
        gaps.append(
            {
                "gap": "no_terminal_evidence",
                "checkpoint": "terminal_refresh_post_2000",
                "detail": "past the terminal-refresh deadline without an observed terminal broker status; expiry is not inferred from time",
            }
        )
    gaps.extend(_blocked_only_gaps(block_reasons))
    return gaps


def _blocked_only_gaps(block_reasons):
    if not block_reasons:
        return []
    return [
        {
            "gap": "refresh_blocked",
            "reasons": list(block_reasons),
            "detail": "historical blocked refresh attempts leave only audit events; missed ticks are attributed to blocked only while the block signal is currently evidenced",
        }
    ]


def _derive_lifecycle_state(ticket, broker_orders, latest, anomalies, panel_anomalies, checkpoints, timeline, missed_streak, block_reasons, as_of_kst):
    state = ticket.current_state
    reasons = []
    hard_set = PANEL_HARD_ANOMALIES & set(anomalies)
    if state in {"APPROVED", "RESERVED"}:
        # An approved-not-yet-submitted ticket legitimately has no broker order.
        hard_set.discard("missing_broker_order_id")
    hard = sorted(hard_set | set(panel_anomalies))
    if hard or state == "NEEDS_REVIEW":
        if state == "NEEDS_REVIEW":
            reasons.append("ticket state NEEDS_REVIEW requires manual verification")
        if hard:
            reasons.append(f"unverified anomalies: {', '.join(hard)}")
        return "anomaly_unverified", reasons
    if state == "FILLED":
        reasons.append("fill recorded with broker evidence")
        return "filled", reasons
    if state in CANCELLED_BUCKET_STATES:
        if state != "CANCELED":
            reasons.append(f"broker terminal state {state} mapped to cancelled bucket")
        else:
            reasons.append("cancellation recorded with broker evidence")
        return "cancelled", reasons
    if state == "EXPIRED":
        reasons.append("expiry recorded via observed broker status or approval expiry transition")
        return "expired", reasons
    if state in {"APPROVED", "RESERVED"}:
        reasons.append("approved and not yet submitted; checkpoint schedule anchors on submission")
        return "approved", reasons

    # Working states: SUBMITTED / ACKED / PARTIALLY_FILLED.
    if state == "PARTIALLY_FILLED":
        reasons.append("partial fills recorded; remainder still resting")
    terminal_report = checkpoints["report"]["terminal_refresh_post_2000"] if checkpoints else None
    if terminal_report and terminal_report["result"] in {"skipped", "blocked"}:
        reasons.append("past 20:00 KST terminal-refresh deadline without observed terminal evidence; expiry is not inferred from time")
        if block_reasons:
            reasons.extend(block_reasons)
        return "terminal_blocked", reasons
    if any("terminal refresh status" in reason for reason in block_reasons):
        reasons.extend(block_reasons)
        return "terminal_blocked", reasons
    if missed_streak >= TTL_MISSED_CHECKPOINTS:
        reasons.append(f"{missed_streak} consecutive due checkpoints without an observed refresh")
        return "ttl_stale", reasons
    if state == "SUBMITTED":
        reasons.append("submitted without acknowledgement evidence yet")
        return "submitted", reasons
    has_observed_refresh = any(entry["source"] == "status_refreshed" for entry in timeline)
    if not has_observed_refresh:
        reasons.append("acknowledged but no observed status refresh yet; resting requires refresh evidence")
        return "acked", reasons
    phase = _session_phase(as_of_kst)
    if phase == "nxt":
        reasons.append("working order with fresh refresh evidence during the NXT window")
        return "nxt_active", reasons
    if phase == "regular":
        reasons.append("working order with fresh refresh evidence during the regular session")
    else:
        reasons.append("working order with refresh evidence outside session windows; no cadence checkpoint currently due")
    return "resting", reasons
