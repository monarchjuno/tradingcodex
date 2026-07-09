from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tradingcodex_service.application.artifact_quality import evaluate_artifact_quality
from tradingcodex_service.application.common import _status_class, _unique, append_jsonl, now_iso, read_json, sanitize_id, stable_hash, write_json
from tradingcodex_service.application.agents import (
    AGENT_SPECS,
    EXPECTED_SKILLS,
    EXPECTED_SUBAGENTS,
    ROLE_DISPLAY_GROUPS,
    ROLE_FORBIDDEN_ACTIONS,
    ROLE_HANDOFF_CONTRACTS,
    ROLE_PURPOSES,
    ROLE_PERMISSION_PROFILES,
    ROLE_SKILL_MAP,
    USER_VISIBLE_SKILLS,
    read_strategy_skill_records,
)
from tradingcodex_service.application.components import count_harness_component_tags, list_harness_components
from tradingcodex_service.application.policy import EXPLICIT_DENY_ACTIONS
from tradingcodex_service.application.research import list_workspace_research_artifacts
from tradingcodex_service.application.runtime import active_profile_for_workspace, ensure_runtime_database, tradingcodex_db_path, workspace_context_payload
from tradingcodex_service.mcp_runtime import static_mcp_tools as _static_mcp_tools
from tradingcodex_service.application.workflow_routing import (
    ARTIFACT_EVALUATION_STATES,
    BLOCKED_ACTION_COPY,
    DEFAULT_LOOP_POLICY,
    EDGE_GROUP_CONTRACTS,
    FIXED_INVESTMENT_ROLES,
    HANDOFF_STATES,
    IDEA_TRANSLATION_COPY,
    INVESTMENT_UNIVERSE_LABELS,
    JUDGMENT_CONTROL_COPY,
    JUDGMENT_REVIEW_ROLE,
    LANE_LOOP_POLICY_OVERRIDES,
    LOOP_CONTROL_COPY,
    LOOP_RUNS_DIR,
    LOOP_RUN_STATE_FILENAME,
    LOOP_STATE_PATH,
    LOOP_VERIFICATION_CONTROL,
    METHOD_LENS_COPY,
    NEXT_ALLOWED_ACTION_COPY,
    PLANNER_ACTIONS,
    PROFILE_COMPACT_KEYS,
    PROFILE_FIELD_KEYS,
    PROFILE_QUESTION_COPY,
    PROFILE_REQUIRED_LANES,
    RESEARCH_AND_DECISION_ROLES,
    RESEARCH_STAGE_ROLES,
    ROLE_SELECTION_COPY,
    SUITABILITY_PROFILE_FIELDS,
    TERMINAL_WORKFLOW_ACTIONS,
    WORKFLOW_LANE_COPY,
    NormalizedInvestmentIntent,
    base_research_team,
    build_artifact_supervisor_loop_contract,
    build_delta_follow_up_brief,
    build_loop_exit_criteria,
    build_loop_policy,
    classify_investment_universe,
    classify_starter_request,
    explicit_public_equity_research_team,
    is_connector_build_request,
    is_connector_operations_only_request,
    is_investment_workflow_request,
    is_secret_only_request,
    is_secret_warning_request,
    negates_scope,
    normalize_investment_intent,
    plan_follow_up_request,
    workflow_plan_has_role,
    strip_guardrail_verification_phrases,
    strip_negated_action_phrases,
    strip_skill_invocation_tokens,
)
from tradingcodex_service.application.workflow_diagnostics import diagnose_workflow_loop_state



IMPROVE_LEDGER_PATH = Path(".tradingcodex/mainagent/improve.jsonl")
IMPROVE_INDEX_PATH = Path(".tradingcodex/mainagent/improve-index.json")
IMPROVE_INDEX_VERSION = 1
IMPROVE_INDEX_RECENT_LIMIT = 200


def workflow_loop_state_relpath(workflow_run_id: str) -> str:
    return f"{LOOP_RUNS_DIR}/{sanitize_id(workflow_run_id)}/{LOOP_RUN_STATE_FILENAME}"


def workflow_loop_state_path(workspace_root: Path | str, workflow_run_id: str) -> Path:
    return Path(workspace_root) / workflow_loop_state_relpath(workflow_run_id)


def read_workflow_loop_state(workspace_root: Path | str | None = None, workflow_run_id: str = "") -> dict[str, Any]:
    root = Path(workspace_root or ".")
    if workflow_run_id:
        state = read_json(workflow_loop_state_path(root, workflow_run_id), {})
        return diagnose_workflow_loop_state(state) if isinstance(state, dict) and state else {}
    latest = read_json(root / LOOP_STATE_PATH, {})
    if isinstance(latest, dict):
        state_path = str(latest.get("state_path") or "")
        if state_path and state_path != LOOP_STATE_PATH:
            state = read_json(root / state_path, latest)
            return diagnose_workflow_loop_state(state) if isinstance(state, dict) and state else diagnose_workflow_loop_state(latest)
        return diagnose_workflow_loop_state(latest) if latest else latest
    state = latest
    return state if isinstance(state, dict) else {}


def compact_workflow_loop_summary(state: dict[str, Any]) -> dict[str, Any]:
    workflow_run_id = str(state.get("workflow_run_id") or "")
    pending = state.get("pending_tasks") if isinstance(state.get("pending_tasks"), list) else []
    completed = state.get("completed_artifacts") if isinstance(state.get("completed_artifacts"), list) else []
    decisions = state.get("loop_decisions") if isinstance(state.get("loop_decisions"), list) else []
    return {
        "workflow_run_id": workflow_run_id,
        "lane": state.get("lane", ""),
        "state_path": workflow_loop_state_relpath(workflow_run_id) if workflow_run_id else LOOP_STATE_PATH,
        "iteration": state.get("iteration", 0),
        "selected_team": state.get("selected_team", []) if isinstance(state.get("selected_team"), list) else [],
        "allowed_followup_team": state.get("allowed_followup_team", []) if isinstance(state.get("allowed_followup_team"), list) else [],
        "escalation_team": state.get("escalation_team", []) if isinstance(state.get("escalation_team"), list) else [],
        "pending_tasks": pending,
        "completed_artifacts": completed[-12:],
        "loop_decisions": decisions[-12:],
        "escalation_proposals": state.get("escalation_proposals", []) if isinstance(state.get("escalation_proposals"), list) else [],
        "improvements": (state.get("improvements", []) if isinstance(state.get("improvements"), list) else [])[-12:],
        "blocked_actions": state.get("blocked_actions", []) if isinstance(state.get("blocked_actions"), list) else [],
        "terminal_action": state.get("terminal_action", ""),
        "stop_reason": state.get("stop_reason", ""),
        "diagnostics": state.get("diagnostics", {}),
        "state_mode": state.get("state_mode", "inspectable_assisted_loop"),
        "auto_spawn": False,
        "recursive_hook_dispatch": False,
        "updated_at": state.get("updated_at", ""),
    }


def write_workflow_loop_state(workspace_root: Path | str, state: dict[str, Any], *, update_latest: bool = True) -> str:
    root = Path(workspace_root)
    workflow_run_id = str(state.get("workflow_run_id") or "")
    relpath = workflow_loop_state_relpath(workflow_run_id) if workflow_run_id else LOOP_STATE_PATH
    state["state_path"] = relpath
    write_json(root / relpath, state)
    if update_latest:
        write_json(root / LOOP_STATE_PATH, compact_workflow_loop_summary(state))
    return relpath


def build_workflow_loop_preview(
    workspace_root: Path | str | None = None,
    request: str = "",
    artifact_paths: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root or ".")
    state = read_workflow_loop_state(root)
    artifacts = artifact_paths or []
    if artifacts:
        return evaluate_artifact_supervisor_loop(root, request, artifacts, record=False)
    return {
        "workflow_run_id": state.get("workflow_run_id", ""),
        "workflow_lane": state.get("lane", ""),
        "state_exists": bool(state),
        "state_path": state.get("state_path") or LOOP_STATE_PATH,
        "latest_state_path": LOOP_STATE_PATH,
        "pending_tasks": state.get("pending_tasks", []) if isinstance(state.get("pending_tasks"), list) else [],
        "completed_artifacts": state.get("completed_artifacts", []) if isinstance(state.get("completed_artifacts"), list) else [],
        "loop_decisions": state.get("loop_decisions", []) if isinstance(state.get("loop_decisions"), list) else [],
        "escalation_proposals": state.get("escalation_proposals", []) if isinstance(state.get("escalation_proposals"), list) else [],
        "improvements": state.get("improvements", []) if isinstance(state.get("improvements"), list) else [],
        "blocked_actions": state.get("blocked_actions", []) if isinstance(state.get("blocked_actions"), list) else [],
        "terminal_action": state.get("terminal_action", ""),
        "stop_reason": state.get("stop_reason", ""),
        "diagnostics": state.get("diagnostics", {}),
        "auto_spawn": False,
        "recursive_hook_dispatch": False,
    }


def list_workflow_improvements(workspace_root: Path | str | None = None, *, limit: int = 50) -> dict[str, Any]:
    root = Path(workspace_root or ".")
    index, index_status = _read_or_rebuild_improvement_index(root)
    records = index.get("recent", []) if isinstance(index.get("recent"), list) else []
    capped_limit = max(1, min(int(limit), IMPROVE_INDEX_RECENT_LIMIT))
    return {
        "status": "ok",
        "improvement_count": int(index.get("improvement_count") or 0),
        "improvements": records[-capped_limit:],
        "ledger_path": IMPROVE_LEDGER_PATH.as_posix(),
        "index_path": IMPROVE_INDEX_PATH.as_posix(),
        "index_status": index_status,
        "summary": {
            "by_type": index.get("by_type", {}),
            "by_role": index.get("by_role", {}),
            "by_materiality": index.get("by_materiality", {}),
            "by_source_type": index.get("by_source_type", {}),
            "recent_summaries": (index.get("recent_summaries", []) if isinstance(index.get("recent_summaries"), list) else [])[-min(capped_limit, 50):],
        },
        "investment_judgment_only": True,
        "authority_boundary": "no_policy_skill_or_execution_change",
    }


def evaluate_artifact_supervisor_loop(
    workspace_root: Path | str | None,
    request: str,
    artifact_paths: list[str],
    *,
    record: bool = False,
) -> dict[str, Any]:
    root = Path(workspace_root or ".")
    state = read_workflow_loop_state(root)
    plan = _loop_plan_for_runtime(request, state)
    policy = plan.get("loopPolicy") or build_loop_policy(str(plan.get("lane") or "research_only"))
    workflow_run_id = str(state.get("workflow_run_id") or f"workflow-preview-{stable_hash({'request': request, 'artifacts': artifact_paths})[:12]}")
    pending_tasks: list[dict[str, Any]] = []
    escalation_proposals: list[dict[str, Any]] = []
    loop_decisions: list[dict[str, Any]] = []
    artifact_evaluations: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    blocked_actions = list(state.get("blocked_actions") or plan.get("blockedActions") or [])
    task_budget = int(policy.get("max_loop_subagent_tasks") or 0)
    existing_loop_tasks = [
        task
        for task in state.get("pending_tasks", [])
        if isinstance(task, dict) and task.get("task_type") in {"artifact_follow_up", "same_role_revision"}
    ]
    remaining_budget = max(0, task_budget - len(existing_loop_tasks))
    followups_this_iteration = int(policy.get("max_followups_per_iteration") or 0)

    for artifact_path in artifact_paths:
        quality = evaluate_artifact_quality(root, artifact_path, strict=True)
        frontmatter = quality.get("frontmatter") or {}
        role = str(frontmatter.get("role") or "unknown")
        handoff_state = str(frontmatter.get("handoff_state") or "waiting")
        evaluation_action = _artifact_evaluation_action(quality, handoff_state)
        artifact_record = {
            "artifact_path": quality.get("path") or artifact_path,
            "role": role,
            "handoff_state": handoff_state,
            "artifact_evaluation": evaluation_action,
            "quality_status": quality.get("status"),
            "context_summary": frontmatter.get("context_summary", ""),
            "follow_up_request_count": len(frontmatter.get("follow_up_requests") or []),
            "improvement_count": len(frontmatter.get("improvements") or []),
            "warnings": quality.get("warnings", [])[:5],
        }
        artifact_evaluations.append(artifact_record)
        improvements.extend(_artifact_improvements(workflow_run_id, artifact_record, frontmatter))

        if evaluation_action == "revise_artifact":
            pending_tasks.append(_same_role_revision_task(workflow_run_id, plan, artifact_record, quality))
            loop_decisions.append(_loop_decision("revise_same_role", f"{role} artifact needs repair before downstream use", artifact_record))
            continue
        if evaluation_action == "block_artifact":
            blocked_actions = _unique([*blocked_actions, *[str(item) for item in frontmatter.get("blocked_actions") or []]])
            loop_decisions.append(_loop_decision("blocked", f"{role} artifact is blocked", artifact_record))
            continue
        if evaluation_action == "wait_for_artifact":
            loop_decisions.append(_loop_decision("waiting", f"{role} artifact is waiting or unavailable", artifact_record))
            continue

        for index, follow_up in enumerate(frontmatter.get("follow_up_requests") or [], start=1):
            if not isinstance(follow_up, dict):
                continue
            request_with_source = {
                **follow_up,
                "source_artifact_path": follow_up.get("source_artifact_path") or artifact_record["artifact_path"],
                "source_artifact_id": follow_up.get("source_artifact_id") or frontmatter.get("artifact_id"),
            }
            decision = plan_follow_up_request(plan, request_with_source)
            planner_action = decision["planner_decision"]
            decision_record = {
                **decision,
                "source_artifact_path": request_with_source.get("source_artifact_path"),
                "requested_by_role": request_with_source.get("requested_by_role") or role,
                "materiality": request_with_source.get("materiality"),
            }
            if planner_action in {"follow_up_existing_team", "challenge_conflict"} and len(pending_tasks) < followups_this_iteration and remaining_budget > 0:
                pending_tasks.append(_follow_up_task(workflow_run_id, plan, request_with_source, decision, index))
                remaining_budget -= 1
                loop_decisions.append(_loop_decision(planner_action, decision["policy_reason"], decision_record))
            elif planner_action == "lane_escalation_proposal":
                escalation_proposals.append(decision_record)
                loop_decisions.append(_loop_decision(planner_action, decision["policy_reason"], decision_record))
            elif planner_action in {"follow_up_existing_team", "challenge_conflict"}:
                wait_record = {**decision_record, "budget_exhausted": True}
                loop_decisions.append(_loop_decision("waiting", "loop task budget exhausted before follow-up could be queued", wait_record))
            else:
                loop_decisions.append(_loop_decision(planner_action, decision["policy_reason"], decision_record))

    improvements.extend(_loop_feedback_improvements(workflow_run_id, loop_decisions))
    terminal_action = _loop_terminal_action(pending_tasks, escalation_proposals, loop_decisions, artifact_evaluations)
    result = {
        "workflow_run_id": workflow_run_id,
        "workflow_lane": plan.get("lane"),
        "state_path": workflow_loop_state_relpath(workflow_run_id),
        "latest_state_path": LOOP_STATE_PATH,
        "loop_policy": policy,
        "selected_team": plan.get("selectedTeam") or plan.get("subagents") or [],
        "allowed_followup_team": plan.get("allowedFollowupTeam") or [],
        "escalation_team": plan.get("escalationTeam") or [],
        "artifact_evaluations": artifact_evaluations,
        "pending_tasks": pending_tasks,
        "loop_decisions": loop_decisions,
        "escalation_proposals": escalation_proposals,
        "improvements": _unique_improvements(improvements),
        "blocked_actions": _unique([str(item) for item in blocked_actions]),
        "terminal_action": terminal_action,
        "stop_reason": _loop_stop_reason(terminal_action),
        "auto_spawn": False,
        "recursive_hook_dispatch": False,
    }
    if record:
        _record_workflow_loop_result(root, state, result)
    return result


def _loop_plan_for_runtime(request: str, state: dict[str, Any]) -> dict[str, Any]:
    if request.strip():
        return classify_starter_request(request)
    lane = str(state.get("lane") or "research_only")
    selected = [str(role) for role in state.get("selected_team") or []]
    plan: dict[str, Any] = {
        "universe": "public_equity",
        "lane": lane,
        "subagents": selected,
        "blockedActions": [str(item) for item in state.get("blocked_actions") or []],
        "selectedTeam": selected,
        "allowedFollowupTeam": [str(role) for role in state.get("allowed_followup_team") or selected],
        "escalationTeam": [str(role) for role in state.get("escalation_team") or []],
        "loopPolicy": state.get("loop_policy") or build_loop_policy(lane),
        "loopStatePath": LOOP_STATE_PATH,
        "exitCriteria": build_loop_exit_criteria(lane, selected),
        "terminalWorkflowActions": list(TERMINAL_WORKFLOW_ACTIONS),
        "artifactHandoffStates": list(HANDOFF_STATES),
        "plannerActions": list(PLANNER_ACTIONS),
    }
    return plan


def _artifact_evaluation_action(quality: dict[str, Any], handoff_state: str) -> str:
    if not quality.get("exists") or handoff_state == "waiting":
        return "wait_for_artifact"
    if handoff_state == "blocked":
        return "block_artifact"
    if handoff_state == "revise" or quality.get("status") != "pass":
        return "revise_artifact"
    return "accept_artifact"


def _same_role_revision_task(workflow_run_id: str, plan: dict[str, Any], artifact: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    role = str(artifact.get("role") or "unknown")
    task_key = stable_hash({"run": workflow_run_id, "role": role, "artifact": artifact.get("artifact_path"), "action": "revise_same_role"})[:12]
    return {
        "task_id": f"{workflow_run_id}:{role}:revise:{task_key}",
        "role": role,
        "task_type": "same_role_revision",
        "status": "pending",
        "planner_action": "revise_same_role",
        "source_artifact_path": artifact.get("artifact_path"),
        "delta_brief": "\n".join([
            "Planner action: revise_same_role",
            f"Workflow lane: {plan.get('lane')}",
            f"Source artifact: {artifact.get('artifact_path')}",
            f"Context summary: {artifact.get('context_summary') or ''}",
            "Repair artifact quality or handoff gaps before downstream use.",
            "Warnings: " + "; ".join(str(item) for item in quality.get("warnings", [])[:4]),
        ]),
    }


def _follow_up_task(
    workflow_run_id: str,
    plan: dict[str, Any],
    follow_up_request: dict[str, Any],
    decision: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    role = str(follow_up_request.get("suggested_role") or "unknown")
    task_key = stable_hash({"run": workflow_run_id, "role": role, "follow_up": follow_up_request, "index": index})[:12]
    return {
        "task_id": f"{workflow_run_id}:{role}:followup:{task_key}",
        "role": role,
        "task_type": "artifact_follow_up",
        "status": "pending",
        "planner_action": decision.get("planner_decision") or "follow_up_existing_team",
        "source_artifact_path": follow_up_request.get("source_artifact_path"),
        "trigger": follow_up_request.get("trigger"),
        "materiality": follow_up_request.get("materiality"),
        "policy_within_current_lane": decision.get("policy_within_current_lane"),
        "policy_requires_user_consent": decision.get("policy_requires_user_consent"),
        "delta_brief": decision.get("delta_brief"),
    }


def _artifact_improvements(workflow_run_id: str, artifact: dict[str, Any], frontmatter: dict[str, Any]) -> list[dict[str, Any]]:
    improvements: list[dict[str, Any]] = []
    for improvement in frontmatter.get("improvements") or []:
        if not isinstance(improvement, dict):
            continue
        payload = {
            "workflow_run_id": workflow_run_id,
            "source_type": "research_artifact",
            "source_path": artifact.get("artifact_path"),
            "source_role": artifact.get("role"),
            "improvement_type": str(improvement.get("improvement_type") or "decision_readiness"),
            "improvement": str(improvement.get("improvement") or ""),
            "reason": str(improvement.get("reason") or ""),
            "materiality": str(improvement.get("materiality") or "medium"),
            "suggested_role": str(improvement.get("suggested_role") or ""),
            "applies_to": improvement.get("applies_to") if isinstance(improvement.get("applies_to"), list) else [],
            "evidence_refs": improvement.get("evidence_refs") if isinstance(improvement.get("evidence_refs"), list) else [],
            "blocked_actions": improvement.get("blocked_actions") if isinstance(improvement.get("blocked_actions"), list) else [],
        }
        if not payload["improvement"] or not payload["reason"]:
            continue
        improvements.append(_improvement(payload))
    return improvements


def _loop_feedback_improvements(workflow_run_id: str, decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    improvements: list[dict[str, Any]] = []
    for decision in decisions:
        action = str(decision.get("planner_action") or "")
        detail = decision.get("detail") if isinstance(decision.get("detail"), dict) else {}
        payload: dict[str, Any] | None = None
        if detail.get("budget_exhausted"):
            payload = {
                "workflow_run_id": workflow_run_id,
                "source_type": "artifact_supervisor_loop",
                "improvement_type": "decision_readiness",
                "improvement": "Do not synthesize until the unresolved investment question is named and either answered, blocked, or marked waiting.",
                "reason": str(decision.get("reason") or "Loop task budget was exhausted."),
                "materiality": "medium",
            }
        elif action == "lane_escalation_proposal":
            payload = {
                "workflow_run_id": workflow_run_id,
                "source_type": "artifact_supervisor_loop",
                "improvement_type": "portfolio_context_gap",
                "improvement": "Treat requests for portfolio or risk context as a separate investment judgment gap rather than silently widening the current lane.",
                "reason": str(decision.get("reason") or "Useful next work was outside the allowed follow-up team."),
                "materiality": "medium",
            }
        elif action == "blocked":
            payload = {
                "workflow_run_id": workflow_run_id,
                "source_type": "artifact_supervisor_loop",
                "improvement_type": "evidence_gap",
                "improvement": "Carry blocked evidence or judgment gaps into the next investment review instead of treating them as resolved.",
                "reason": str(decision.get("reason") or "The loop reached a blocked state."),
                "materiality": "medium",
            }
        if payload:
            improvements.append(_improvement(payload))
    return improvements


def _improvement(payload: dict[str, Any]) -> dict[str, Any]:
    base = {
        "workflow_run_id": payload.get("workflow_run_id", ""),
        "source_type": payload.get("source_type", ""),
        "source_path": payload.get("source_path", ""),
        "source_role": payload.get("source_role", ""),
        "improvement_type": payload.get("improvement_type", "decision_readiness"),
        "improvement": payload.get("improvement", ""),
        "reason": payload.get("reason", ""),
        "materiality": payload.get("materiality", "medium"),
        "suggested_role": payload.get("suggested_role", ""),
        "applies_to": payload.get("applies_to", []),
        "evidence_refs": payload.get("evidence_refs", []),
        "blocked_actions": payload.get("blocked_actions", []),
    }
    return {
        "improvement_id": "improve-" + stable_hash(base)[:16],
        "status": "captured",
        "review_state": "needs_investment_review",
        "reuse_state": "available_for_future_judgment",
        "authority_boundary": "no_policy_skill_or_execution_change",
        "created_at": now_iso(),
        **base,
    }


def _unique_improvements(improvements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for improvement in improvements:
        key = str(improvement.get("improvement_id") or stable_hash(improvement))
        if key in seen:
            continue
        seen.add(key)
        unique.append(improvement)
    return unique


def _loop_decision(planner_action: str, reason: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": now_iso(),
        "planner_action": planner_action,
        "reason": reason,
        "detail": detail,
    }


def _loop_terminal_action(
    pending_tasks: list[dict[str, Any]],
    escalation_proposals: list[dict[str, Any]],
    loop_decisions: list[dict[str, Any]],
    artifact_evaluations: list[dict[str, Any]],
) -> str:
    actions = {str(item.get("planner_action") or "") for item in loop_decisions}
    if "blocked" in actions:
        return "blocked"
    if escalation_proposals:
        return "lane_escalation_proposal"
    if pending_tasks or "waiting" in actions or any(item.get("artifact_evaluation") == "wait_for_artifact" for item in artifact_evaluations):
        return "waiting"
    return "synthesize" if artifact_evaluations else "waiting"


def _loop_stop_reason(terminal_action: str) -> str:
    if terminal_action == "synthesize":
        return "accepted_artifacts_ready_for_synthesis"
    if terminal_action == "lane_escalation_proposal":
        return "user_visible_lane_escalation_required"
    if terminal_action == "blocked":
        return "workflow_blocked_by_artifact_or_policy"
    return "waiting_for_artifact_or_delta_followup"


def _record_workflow_loop_result(root: Path, state: dict[str, Any], result: dict[str, Any]) -> None:
    existing = read_workflow_loop_state(root, str(result["workflow_run_id"]))
    merged = dict(existing or state or {})
    merged.update({
        "workflow_run_id": result["workflow_run_id"],
        "lane": result["workflow_lane"],
        "loop_policy": result["loop_policy"],
        "selected_team": result["selected_team"],
        "allowed_followup_team": result["allowed_followup_team"],
        "escalation_team": result["escalation_team"],
        "blocked_actions": result["blocked_actions"],
        "terminal_action": result["terminal_action"],
        "stop_reason": result["stop_reason"],
        "updated_at": now_iso(),
    })
    merged["pending_tasks"] = [
        *(merged.get("pending_tasks", []) if isinstance(merged.get("pending_tasks"), list) else []),
        *result["pending_tasks"],
    ]
    merged["loop_decisions"] = [
        *(merged.get("loop_decisions", []) if isinstance(merged.get("loop_decisions"), list) else []),
        *result["loop_decisions"],
    ][-40:]
    merged["escalation_proposals"] = [
        *(merged.get("escalation_proposals", []) if isinstance(merged.get("escalation_proposals"), list) else []),
        *result["escalation_proposals"],
    ][-20:]
    merged["improvements"] = _unique_improvements([
        *(merged.get("improvements", []) if isinstance(merged.get("improvements"), list) else []),
        *result["improvements"],
    ])[-40:]
    merged["completed_artifacts"] = [
        *(merged.get("completed_artifacts", []) if isinstance(merged.get("completed_artifacts"), list) else []),
        *[
            {
                "role": item.get("role"),
                "artifact_path": item.get("artifact_path"),
                "handoff_state": item.get("handoff_state"),
                "artifact_evaluation": item.get("artifact_evaluation"),
                "completed_at": now_iso(),
            }
            for item in result["artifact_evaluations"]
        ],
    ][-40:]
    merged["iteration"] = int(merged.get("iteration") or 0) + 1
    merged["state_mode"] = "inspectable_assisted_loop"
    merged["auto_spawn"] = False
    merged["recursive_hook_dispatch"] = False
    write_workflow_loop_state(root, merged)
    _record_improvements(root, result["improvements"])
    append_jsonl(root / "trading" / "audit" / "workflow-loop-events.jsonl", {"ts": now_iso(), "event": "planner-preview-recorded", **result})


def _record_improvements(root: Path, improvements: list[dict[str, Any]]) -> None:
    if not improvements:
        return
    path = root / IMPROVE_LEDGER_PATH
    index, _status = _read_or_rebuild_improvement_index(root)
    existing_ids = {str(item) for item in index.get("ids", []) if item}
    recorded: list[dict[str, Any]] = []
    for improvement in improvements:
        improvement_id = str(improvement.get("improvement_id") or "")
        if not improvement_id or improvement_id in existing_ids:
            continue
        append_jsonl(path, improvement)
        existing_ids.add(improvement_id)
        recorded.append(improvement)
    if recorded:
        _write_improvement_index(root, _append_improvement_index(index, recorded, path))


def _read_or_rebuild_improvement_index(root: Path) -> tuple[dict[str, Any], str]:
    ledger = root / IMPROVE_LEDGER_PATH
    index = read_json(root / IMPROVE_INDEX_PATH, {})
    if _improvement_index_current(index, ledger):
        return index, "current"
    if ledger.exists():
        return _rebuild_improvement_index(root), "rebuilt"
    return _empty_improvement_index(ledger), "empty"


def _improvement_index_current(index: Any, ledger: Path) -> bool:
    if not isinstance(index, dict):
        return False
    if int(index.get("schema_version") or 0) != IMPROVE_INDEX_VERSION:
        return False
    marker = _improvement_ledger_marker(ledger)
    return (
        int(index.get("ledger_size") or 0) == marker["ledger_size"]
        and int(index.get("ledger_mtime_ns") or 0) == marker["ledger_mtime_ns"]
    )


def _rebuild_improvement_index(root: Path) -> dict[str, Any]:
    ledger = root / IMPROVE_LEDGER_PATH
    records: list[dict[str, Any]] = []
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict) and record.get("improvement_id"):
                records.append(record)
    index = _append_improvement_index(_empty_improvement_index(ledger), _unique_improvements(records), ledger)
    _write_improvement_index(root, index)
    return index


def _empty_improvement_index(ledger: Path) -> dict[str, Any]:
    marker = _improvement_ledger_marker(ledger)
    return {
        "schema_version": IMPROVE_INDEX_VERSION,
        "updated_at": now_iso(),
        "ledger_path": IMPROVE_LEDGER_PATH.as_posix(),
        "index_path": IMPROVE_INDEX_PATH.as_posix(),
        "ledger_size": marker["ledger_size"],
        "ledger_mtime_ns": marker["ledger_mtime_ns"],
        "improvement_count": 0,
        "ids": [],
        "recent": [],
        "recent_summaries": [],
        "by_type": {},
        "by_role": {},
        "by_materiality": {},
        "by_source_type": {},
        "by_status": {},
        "by_reuse_state": {},
        "authority_boundary": "no_policy_skill_or_execution_change",
        "investment_judgment_only": True,
    }


def _append_improvement_index(index: dict[str, Any], improvements: list[dict[str, Any]], ledger: Path) -> dict[str, Any]:
    merged = dict(index)
    ids = [str(item) for item in merged.get("ids", []) if item]
    existing = set(ids)
    recent = list(merged.get("recent", []) if isinstance(merged.get("recent"), list) else [])
    summaries = list(merged.get("recent_summaries", []) if isinstance(merged.get("recent_summaries"), list) else [])
    for improvement in improvements:
        improvement_id = str(improvement.get("improvement_id") or "")
        if not improvement_id or improvement_id in existing:
            continue
        existing.add(improvement_id)
        ids.append(improvement_id)
        recent.append(improvement)
        summaries.append(_improvement_summary(improvement))
        _increment_improvement_bucket(merged, "by_type", improvement.get("improvement_type") or "decision_readiness")
        _increment_improvement_bucket(merged, "by_role", improvement.get("suggested_role") or improvement.get("source_role") or "unspecified")
        _increment_improvement_bucket(merged, "by_materiality", improvement.get("materiality") or "medium")
        _increment_improvement_bucket(merged, "by_source_type", improvement.get("source_type") or "unknown")
        _increment_improvement_bucket(merged, "by_status", improvement.get("status") or "captured")
        _increment_improvement_bucket(merged, "by_reuse_state", improvement.get("reuse_state") or "available_for_future_judgment")
    marker = _improvement_ledger_marker(ledger)
    merged.update({
        "schema_version": IMPROVE_INDEX_VERSION,
        "updated_at": now_iso(),
        "ledger_path": IMPROVE_LEDGER_PATH.as_posix(),
        "index_path": IMPROVE_INDEX_PATH.as_posix(),
        "ledger_size": marker["ledger_size"],
        "ledger_mtime_ns": marker["ledger_mtime_ns"],
        "improvement_count": len(ids),
        "ids": ids,
        "recent": recent[-IMPROVE_INDEX_RECENT_LIMIT:],
        "recent_summaries": summaries[-IMPROVE_INDEX_RECENT_LIMIT:],
        "authority_boundary": "no_policy_skill_or_execution_change",
        "investment_judgment_only": True,
    })
    return merged


def _improvement_summary(improvement: dict[str, Any]) -> dict[str, Any]:
    return {
        "improvement_id": improvement.get("improvement_id", ""),
        "created_at": improvement.get("created_at", ""),
        "improvement_type": improvement.get("improvement_type", "decision_readiness"),
        "materiality": improvement.get("materiality", "medium"),
        "suggested_role": improvement.get("suggested_role") or improvement.get("source_role") or "",
        "source_path": improvement.get("source_path", ""),
        "review_state": improvement.get("review_state", ""),
        "reuse_state": improvement.get("reuse_state", ""),
        "improvement": improvement.get("improvement", ""),
    }


def _increment_improvement_bucket(index: dict[str, Any], bucket: str, raw: Any) -> None:
    value = str(raw or "unknown")
    current = index.get(bucket)
    counts = current if isinstance(current, dict) else {}
    counts[value] = int(counts.get(value) or 0) + 1
    index[bucket] = counts


def _improvement_ledger_marker(path: Path) -> dict[str, int]:
    try:
        stat = path.stat()
    except OSError:
        return {"ledger_size": 0, "ledger_mtime_ns": 0}
    return {"ledger_size": stat.st_size, "ledger_mtime_ns": stat.st_mtime_ns}


def _write_improvement_index(root: Path, index: dict[str, Any]) -> None:
    write_json(root / IMPROVE_INDEX_PATH, index)




def build_subagent_starter_prompt(request: str, workspace_root: Path | str | None = None) -> str:
    plan = classify_starter_request(request)
    lane = str(plan["lane"])
    flags = plan.get("routingFlags", {})
    artifact_language = infer_research_artifact_language(request)
    profile_status = investor_profile_status(plan, workspace_root)
    profile_inputs = profile_status["missing_fields"]
    known_profile = profile_status["known_fields"]
    stage_order = " -> ".join(stage["label"] for stage in build_workflow_stages(plan))
    spawn_line = ", ".join(plan["subagents"]) if plan["subagents"] else "none"
    has_judgment_review = JUDGMENT_REVIEW_ROLE in plan.get("subagents", [])
    has_valuation = workflow_plan_has_role(plan, "valuation-analyst")
    if has_judgment_review:
        review_instruction = "Independent judgment review: before synthesis or downstream portfolio/risk/order gates, dispatch `judgment-reviewer` to challenge accepted artifacts for strongest support, strongest contrary evidence, stale or weak source posture, overconfidence risk, update triggers, and invalidation conditions."
        gate_instruction = "Judgment gate: do not move accepted artifacts downstream until `judgment-reviewer` returns accepted, revise, blocked, or waiting with contrary evidence and source-trust posture named."
    elif lane == "order_ticket_approval_execution_gate":
        review_instruction = "Approved action gate: do not dispatch `judgment-reviewer` for execution-only checks; verify ticket, approval, policy, duplicate-request, connection, and audit evidence through service gates."
        gate_instruction = "Service gate: do not reopen investment judgment in this lane; stop if ticket, approval, policy, duplicate-request, connection, or audit evidence is missing."
    else:
        review_instruction = "Evidence check: no independent judgment reviewer is needed for this narrow producer-only research lane; verify source freshness, missing evidence, and blocked actions before synthesis."
        gate_instruction = "Evidence gate: do not widen this lane into thesis, valuation, portfolio, risk, order, approval, or execution without a new validated plan."
    if has_valuation:
        scenario_instruction = "Scenario discipline: when thesis or valuation is in scope, include scenario cases, contrary_evidence, source_trust_notes, update_triggers, invalidation_conditions, and unresolved conflicts or mark the artifact not-decision-ready."
    elif lane == "order_ticket_approval_execution_gate":
        scenario_instruction = "Approved action discipline: verify the existing ticket, approval, policy, duplicate-request, connection, and audit evidence; do not add research, valuation, forecast, portfolio, or scenario fields."
    elif lane == "order_ticket_draft_gate" and workflow_plan_has_role(plan, "risk-manager"):
        scenario_instruction = "Draft-order discipline: verify accepted evidence, portfolio fit, canonical order fields, policy readiness, blocked approval/execution status, contrary evidence, and source trust; do not add valuation, forecast, approval, or execution fields unless requested."
    elif lane == "order_ticket_draft_gate":
        scenario_instruction = "Draft-order discipline: verify accepted evidence, portfolio fit, canonical order fields, policy readiness, blocked risk-review status, blocked approval/execution status, contrary evidence, and source trust; do not perform risk review unless requested."
    elif lane == "portfolio_risk_review":
        scenario_instruction = "Portfolio/risk discipline: verify holdings, exposure, profile gaps, constraints, downside risks, contrary evidence, source trust, update triggers, and invalidation conditions without turning the review into an order lane."
    elif lane == "thesis_review" or flags.get("decision_quality_required"):
        scenario_instruction = "Scenario discipline: for non-valuation thesis review, use qualitative scenarios, contrary_evidence, source_trust_notes, update_triggers, invalidation_conditions, and unresolved conflicts or mark the artifact not-decision-ready."
    else:
        scenario_instruction = "Evidence discipline: keep this narrow research artifact source-aware; record missing evidence and blocked actions without adding scenario, valuation, forecast, portfolio, or action fields unless requested."
    scope_instruction = (
        "For `research_only`, do not add valuation, portfolio, risk, approval, or execution roles."
        if lane == "research_only"
        else f"Stay inside the recorded `{lane}` lane; do not add roles, actions, or artifacts outside the validated plan."
    )
    method_guardrails = "separate facts, inferences, and assumptions; do not add portfolio fit or action advice."
    if lane in {"thesis_review_then_portfolio_risk_review", "portfolio_risk_review", "order_ticket_draft_gate"}:
        method_guardrails = "separate facts, inferences, and assumptions; check portfolio fit before action advice."
    elif lane == "thesis_review":
        method_guardrails = "separate facts, inferences, and assumptions; do not infer portfolio fit or action advice."
    elif lane == "order_ticket_approval_execution_gate":
        method_guardrails = "verify approved artifacts and service gates; do not reopen research judgment."
    if lane == "order_ticket_approval_execution_gate":
        artifact_memory_instruction = "Artifact memory: write approved-action artifacts in the requested artifact language with context_summary, reader_summary, next_action, ticket, approval, policy, duplicate-request, connection, and audit references, plus blocked or rejected reasons."
        quality_instruction = "Approved Action Spine: preserve constraints and negations; require ticket_id, approval_receipt_id, policy_state, duplicate_request_status, connection_status, audit_reference, handoff_state, next_recipient, and blocked_actions in role artifacts."
        handoff_instruction = "Require each role handoff to include artifact path, reader summary, next action, handoff state, ticket/approval/policy/audit references, readiness or rejection reason, next eligible recipient, and blocked actions."
        reader_mode_instruction = "Reader mode: keep final chat brief: approved-action status, artifact path when one exists, blockers or rejection reason, and next allowed action. Do not paste full status evidence into chat unless the user explicitly asks."
        synthesis_instruction = "Wait for all recorded-plan stages, then return a brief approved-action status with artifact paths, handoff states, service-gate evidence summary, rejected or blocked reasons, and next allowed action."
    elif lane == "research_only" and not flags.get("decision_quality_required"):
        artifact_memory_instruction = "Artifact memory: write artifacts in the requested artifact language with context_summary, reader_summary, next_action, source snapshots, missing-evidence notes, and improvements for future judgment."
        quality_instruction = "Evidence Quality Floor: preserve constraints and negations; require source_as_of, confidence, missing_evidence, source_snapshot_ids, next_recipient, and blocked_actions in role artifacts."
        handoff_instruction = "Require each role handoff to include artifact path, reader summary, next action, handoff state, source/as-of posture, confidence, missing evidence, next eligible recipient, and blocked actions."
        reader_mode_instruction = "Reader mode: keep final chat brief: synthesis status, saved report path, 1-3 key takeaways, and next allowed action. Do not paste the full synthesis into chat unless the user explicitly asks."
        synthesis_instruction = "Wait for all recorded-plan stages, then create a Markdown synthesis report through create_research_artifact at `trading/reports/head-manager/synthesis-<workflow_run_id>.md` with artifact_id `synthesis-<workflow_run_id>`, artifact_type `synthesis_report`, role and created_by `head-manager`, required research frontmatter, and sections for direct answer, accepted artifact inputs, synthesis, disagreements/conflicts, source/as-of posture, missing evidence, caveats, and next allowed action. Reply briefly with the report path, 1-3 takeaways, and next allowed action."
    else:
        artifact_memory_instruction = "Artifact memory: write artifacts in the requested artifact language with context_summary, reader_summary, next_action, source snapshots, missing-evidence notes, and improvements for future judgment."
        quality_instruction = "Decision Quality Spine: preserve constraints and negations; require evidence_grade, source_freshness, source_quality, source_trust_notes, conflict_status, decision_readiness, confidence, missing_evidence, next_recipient, and blocked_actions in role artifacts."
        handoff_instruction = "Require each role handoff to include artifact path, reader summary, next action, handoff state, source/as-of posture, confidence, missing evidence, readiness/support gaps, next eligible recipient, and blocked actions."
        reader_mode_instruction = "Reader mode: keep final chat brief: synthesis status, saved report path, 1-3 key takeaways, and next allowed action. Do not paste the full synthesis into chat unless the user explicitly asks."
        synthesis_instruction = "Wait for all recorded-plan stages, then create a Markdown synthesis report through create_research_artifact at `trading/reports/head-manager/synthesis-<workflow_run_id>.md` with artifact_id `synthesis-<workflow_run_id>`, artifact_type `synthesis_report`, role and created_by `head-manager`, required research frontmatter, and sections for direct answer, accepted artifact inputs, synthesis, disagreements/conflicts, source/as-of posture, missing evidence, caveats, and next allowed action. Reply briefly with the report path, 1-3 takeaways, and next allowed action."
    if not plan["subagents"]:
        ops = no_subagent_lane_copy(plan)
        return "\n".join([
            ops["workflow_intro"],
            "No fixed-role subagent dispatch is required for this lane.",
            f'Original user request (verbatim): "{request}"',
            f"Workflow lane: {plan['lane']}",
            f"Operational universe: {investment_universe_label(plan['universe'])}",
            f"Workflow stage order: {stage_order}",
            ops["skill_instruction"],
            ops["secret_instruction"],
            ops["broker_instruction"],
            ops["artifact_instruction"],
            ops["output_instruction"],
            "Method lenses for this lane: " + format_method_lenses(plan),
            "Iteration controls for this lane: " + format_loop_controls(plan),
            "Judgment controls for this lane: " + format_judgment_controls(plan),
            f"Blocked actions: {', '.join(plan['blockedActions'])}",
        ])
    lines = [
        "Use this workspace's fixed-role subagent workflow through $tcx-workflow.",
        "Draft, validate, and record a staged workflow plan before dispatch.",
        f'Original user request (verbatim): "{request}"',
        f"Artifact language: {artifact_language}",
        f"Investment universe: {investment_universe_label(plan['universe'])}",
        f"Workflow lane: {plan['lane']}",
        f"Workflow stage order: {stage_order}",
        f"Deterministic preview roles likely needed: {spawn_line}",
        "This preview is not the final workflow contract; validate and record a staged workflow plan before spawning roles.",
        scope_instruction,
        "When calling `spawn_agent` for a recorded fixed role stage, use `agent_type` and a compact `message`; do not set `fork_context` to true.",
        "Use each role's exact `.codex/agents/*.toml` name as the runtime label.",
        "Preserve the original user request and explicit constraints in every subagent brief.",
        "Context budget: use artifact paths, context_summary, source/as-of metadata, and short deltas; do not paste full prior artifacts, source dumps, or unrelated chat history.",
        reader_mode_instruction,
        artifact_memory_instruction,
        quality_instruction,
        review_instruction,
        scenario_instruction,
        "Iteration controls: stay within the selected lane; verify handoff quality after each artifact; lane controls: " + format_loop_controls(plan),
        "Artifact Supervisor Loop: evaluate artifacts first; accepted is a handoff state, not terminal action.",
        "Loop roles: follow-up=" + (", ".join(plan.get("allowedFollowupTeam") or []) or "none") + "; escalation-only roles stay proposal-only in loop state.",
        "Follow-ups: subagents may propose `follow_up_requests`; recompute lane/consent before recording deltas.",
        "Loop state: record the validated plan first, then keep compact tasks, decisions, escalations, blocks, and stop reason under `.tradingcodex/mainagent/workflows/<workflow_run_id>/`; no recursive dispatch.",
        "Judgment controls: fixed rules and selected strategy context are read-only; do not change strategy, policy, role authority, approval, execution, or MCP gates; lane controls: " + format_judgment_controls_compact(plan),
        gate_instruction,
        "Method lenses for this lane: " + format_method_lenses(plan) + "; guardrails: " + method_guardrails,
        "Strategy baseline: " + build_strategy_baseline(workspace_root)["prompt_summary"],
        "Do not let head-manager perform substantive investment analysis before subagent outputs exist.",
        handoff_instruction,
        "Use handoff states: accepted, revise, blocked, waiting.",
        "Do not let downstream roles redo missing upstream work; request revision from the owning role or stop with waiting/blocked status.",
        synthesis_instruction,
        f"Blocked actions before artifacts: {', '.join(plan['blockedActions'])}",
    ]
    if flags.get("forecast_contract_required"):
        lines.insert(
            -1,
            "Forecast contract: include forecast_required, forecast_allowed, forecast_block_reason when blocked, forecast_target, forecast_horizon, probability or probability_range, base_rate, evidence_ids, contrary_evidence, source_trust_notes, resolution_source, review_date, update_triggers, and invalidation_conditions.",
        )
    if flags.get("forecast_negated"):
        lines.insert(
            -1,
            "Forecast negated: scenarios and qualitative update triggers are allowed, but do not create probability fields or forecast ledger records.",
        )
    if flags.get("anti_overfit_required"):
        lines.insert(
            -1,
            "Anti-overfit required: check look-ahead leakage, survivorship bias, data snooping, out-of-sample coverage, costs, liquidity, capacity, regime sensitivity, and implementation friction.",
        )
    if flags.get("deep_thesis_default"):
        lines.insert(
            -1,
            (
                "Deep thesis default: broad public-equity review uses fundamental, technical, news, and valuation artifacts unless explicit constraints removed one of them."
                if has_valuation
                else "Deep thesis default: broad public-equity review uses the selected research artifacts; valuation is omitted because the request excluded it."
            ),
        )
    if known_profile:
        known = "; ".join(f"{item['field']}: {item['answer']}" for item in known_profile)
        lines.insert(
            -1,
            "Known investor profile context from the active profile: " + known + ".",
        )
    if profile_inputs:
        question_items = build_profile_questions(plan, workspace_root)
        question_examples = "; ".join(item["question"] for item in question_items[:3])
        lines.insert(
            -1,
            "Investor profile gaps to request before recommendation, sizing, approval, or execution: "
            + ", ".join(profile_inputs)
            + ".",
        )
        lines.insert(
            -1,
            "Investor profile questions to ask if unanswered include: "
            + question_examples
            + ". Use the listed missing fields for any remaining profile questions.",
        )
    return "\n".join(lines)


def build_compact_dispatch_context(request: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    plan = classify_starter_request(request)
    profile_status = investor_profile_status(plan, workspace_root)
    has_subagents = bool(plan["subagents"])
    allowed_followup_team = plan.get("allowedFollowupTeam") or []
    escalation_team = plan.get("escalationTeam") or []
    allowed_same_as_selected = allowed_followup_team == plan["subagents"]
    loop_contract = {
        "state_path": plan.get("loopStatePath", LOOP_STATE_PATH),
        "allowed_ref": "selected_team" if allowed_same_as_selected else "allowed_followup_team",
        "escalation_count": len(escalation_team),
        "max_iterations": (plan.get("loopPolicy") or {}).get("max_iterations"),
    }
    if not allowed_same_as_selected:
        loop_contract["allowed_followup_team"] = allowed_followup_team
    context = {
        "context_mode": "compact_workflow_intake",
        "deterministic_preview": True,
        "requires_workflow_planning": has_subagents or plan["lane"] in {"connector_build", "head_manager_connector_operations"},
        "workflow_lane": plan["lane"],
        "required_subagents": plan["subagents"],
        "loop_contract": loop_contract,
        "routing_status": {
            "lane": plan["lane"],
            "selected_team": plan["subagents"],
            "blocked_actions": plan["blockedActions"],
        },
        "research_artifact_language": compact_artifact_language(request),
        "profile_missing": [
            PROFILE_COMPACT_KEYS.get(PROFILE_FIELD_KEYS.get(field, field), field)
            for field in profile_status["missing_fields"]
        ],
        "selected_team_binding": False,
        "workflow_intake_path": ".tradingcodex/mainagent/latest-workflow-intake.json",
        "dispatch_rules": (
            [
                "draft_validate_record_staged_plan_before_dispatch",
                "hook_hints_are_not_final_workflow_decisions",
                "waiting_if_exact_role_routing_unavailable",
                "no_downstream_repair_of_missing_upstream_work",
            ]
            if has_subagents
            else [
                "handle_in_head_manager_lane",
                "do_not_dispatch_fixed_role_subagents",
                "do_not_create_blocked_artifacts",
            ]
        ),
    }
    for flag in (
        "decision_quality_required",
        "forecast_contract_required",
        "profile_gate_required",
        "anti_overfit_required",
        "deep_thesis_default",
    ):
        if plan.get("routingFlags", {}).get(flag):
            context[flag] = True
    if profile_status["known_fields"]:
        context["profile_known"] = [PROFILE_COMPACT_KEYS.get(item["key"], item["key"]) for item in profile_status["known_fields"]]
    return context


def build_workflow_intake_summary(request: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    if not request.strip():
        return {}
    plan = classify_starter_request(request)
    lane_copy = workflow_lane_copy(plan)
    profile_status = investor_profile_status(plan, workspace_root)
    return {
        "label": lane_copy["label"],
        "summary": lane_copy["summary"],
        "primary_question": lane_copy["primary_question"],
        "idea_translation": build_idea_translation(plan),
        "investment_universe": plan["universe"],
        "investment_universe_label": investment_universe_label(plan["universe"]),
        "workflow_lane": plan["lane"],
        "routing_flags": plan.get("routingFlags", {}),
        "selected_team": plan.get("selectedTeam", plan.get("subagents", [])),
        "allowed_followup_team": plan.get("allowedFollowupTeam", []),
        "escalation_team": plan.get("escalationTeam", []),
        "loop_policy": plan.get("loopPolicy", {}),
        "workflow_loop_state_path": plan.get("loopStatePath", LOOP_STATE_PATH),
        "workflow_loop_run_state_pattern": plan.get("loopRunStatePattern", f"{LOOP_RUNS_DIR}/<workflow_run_id>/{LOOP_RUN_STATE_FILENAME}"),
        "terminal_workflow_actions": plan.get("terminalWorkflowActions", list(TERMINAL_WORKFLOW_ACTIONS)),
        "artifact_handoff_states": plan.get("artifactHandoffStates", list(HANDOFF_STATES)),
        "planner_actions": plan.get("plannerActions", list(PLANNER_ACTIONS)),
        "exit_criteria": plan.get("exitCriteria", []),
        "subagents": build_selected_role_details(plan),
        "workflow_stages": build_workflow_stages(plan),
        "method_lenses": build_method_lenses(plan),
        "loop_controls": build_loop_controls(plan),
        "judgment_controls": build_judgment_controls(plan),
        "review_highlights": build_review_highlights(plan, profile_status),
        "strategy_baseline": build_strategy_baseline(workspace_root),
        "next_allowed_actions": build_next_allowed_actions(plan, profile_status),
        "blocked_actions": plan["blockedActions"],
        "blocked_action_details": build_blocked_action_details(plan["blockedActions"]),
        "investor_profile_inputs": profile_status["missing_fields"],
        "questions_to_answer": build_profile_questions(plan, workspace_root),
        "investor_profile": profile_status,
        "artifact_language": infer_research_artifact_language(request),
        "plain_language_output": True,
    }


def compact_loop_policy(plan: dict[str, Any]) -> dict[str, Any]:
    policy = plan.get("loopPolicy") or build_loop_policy(str(plan.get("lane") or "research_only"))
    return {
        "max_iterations": policy.get("max_iterations"),
        "max_followups_per_iteration": policy.get("max_followups_per_iteration"),
        "max_same_role_revisions": policy.get("max_same_role_revisions"),
        "max_total_subagent_tasks": policy.get("max_total_subagent_tasks"),
        "max_loop_subagent_tasks": policy.get("max_loop_subagent_tasks"),
    }


def compact_artifact_language(request: str) -> str:
    language = infer_research_artifact_language(request)
    if language.startswith("same language"):
        return "same_as_request"
    return language


def investment_universe_label(universe: str | None) -> str:
    value = str(universe or "").strip()
    return INVESTMENT_UNIVERSE_LABELS.get(value, value.replace("_", " ").title() if value else "Unknown")


def no_subagent_lane_copy(plan: dict[str, Any]) -> dict[str, str]:
    lane = str(plan.get("lane") or "")
    if lane == "head_manager_strategy_authoring":
        return {
            "workflow_intro": "Use this workspace's head-manager strategy workflow.",
            "skill_instruction": "Use `$strategy-creator` and the `tcx strategies` path for validated strategy creation, update, inspection, or activation.",
            "secret_instruction": "Do not read, print, store, or transform raw secrets.",
            "broker_instruction": "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code.",
            "artifact_instruction": "Do not create ticker research, order tickets, approvals, or execution artifacts unless the user later asks for a separate workflow lane.",
            "output_instruction": "Use plain-English strategy status first, then put validation details behind concise evidence labels.",
        }
    if lane == "connector_build":
        return {
            "workflow_intro": "Use this workspace's TradingCodex build workflow.",
            "skill_instruction": "Use `$tcx-build` plus `tcx connectors connect` first, with providers|scaffold|register|validate as advanced fallback for provider implementation work.",
            "secret_instruction": "Do not read, print, store, or transform raw secrets; create only credential_ref schemas.",
            "broker_instruction": "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code outside TradingCodex service validation paths.",
            "artifact_instruction": "Create provider/connector scaffold files, docs, and tests only; do not create order tickets, approvals, execution artifacts, or live submit enablement.",
            "output_instruction": "Report build gate, scaffold files, validation status, and restart/doctor steps; stop before live execution.",
        }
    return {
        "workflow_intro": "Use this workspace's head-manager operational workflow.",
        "skill_instruction": "Use `$tcx-server` for connector setup, profile inspection, health checks, and translation preview.",
        "secret_instruction": "Do not read, print, store, or transform raw secrets.",
        "broker_instruction": "Do not call broker APIs directly from shell, hooks, skills, or ad hoc code.",
        "artifact_instruction": "Do not create order tickets, approvals, or execution artifacts unless the user later asks for them explicitly.",
        "output_instruction": "Use plain-English status output first, then put technical connector details behind concise evidence labels.",
    }


def required_profile_inputs(plan: dict[str, Any]) -> list[str]:
    return list(SUITABILITY_PROFILE_FIELDS) if plan.get("lane") in PROFILE_REQUIRED_LANES else []


def investor_profile_status(plan: dict[str, Any], workspace_root: Path | str | None = None) -> dict[str, Any]:
    required = required_profile_inputs(plan)
    profile = active_profile_for_workspace(workspace_root) if workspace_root is not None else {}
    investor_profile = profile.get("investor_profile") if isinstance(profile.get("investor_profile"), dict) else {}
    known_fields = []
    missing_fields = []
    for field in required:
        key = PROFILE_FIELD_KEYS[field]
        answer = investor_profile.get(key) or investor_profile.get(field)
        if answer not in (None, ""):
            known_fields.append({"field": field, "key": key, "answer": str(answer)})
        else:
            missing_fields.append(field)
    completion = 1.0 if not required else round((len(required) - len(missing_fields)) / len(required), 2)
    return {
        "profile_id": str(profile.get("profile_id") or ""),
        "required_fields": required,
        "known_fields": known_fields,
        "missing_fields": missing_fields,
        "completion": completion,
    }


def build_selected_role_details(plan: dict[str, Any]) -> list[dict[str, str]]:
    details = []
    for role in plan.get("subagents") or []:
        label = AGENT_SPECS[role].label if role in AGENT_SPECS else role
        details.append({
            "role": role,
            "label": label,
            "why_selected": ROLE_SELECTION_COPY.get(role, "Handles the role-specific workflow step selected for this lane."),
        })
    return details


def build_method_lenses(plan: dict[str, Any]) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    lenses = [dict(item) for item in METHOD_LENS_COPY.get(lane, METHOD_LENS_COPY["research_only"])]
    if lane == "thesis_review" and not workflow_plan_has_role(plan, "valuation-analyst") and lenses:
        lenses[0] = {
            **lenses[0],
            "detail": "Use thesis, catalyst, technical, and news artifacts as qualitative scenarios with assumptions and uncertainty.",
            "reference": "Scenario analysis practice",
        }
    return lenses


def build_loop_controls(plan: dict[str, Any]) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    controls = [dict(item) for item in LOOP_CONTROL_COPY.get(lane, LOOP_CONTROL_COPY["research_only"])]
    if lane == "thesis_review" and not workflow_plan_has_role(plan, "valuation-analyst") and controls:
        controls[0] = {
            **controls[0],
            "detail": "Iterate across evidence, thesis assumptions, and source freshness until the thesis is accepted, revised, blocked, or waiting.",
        }
    if lane == "order_ticket_draft_gate" and not workflow_plan_has_role(plan, "risk-manager") and controls:
        controls[0] = {
            **controls[0],
            "detail": "Iterate on missing canonical order fields, instrument support, portfolio fit, policy readiness, and blocked risk-review status until a draft is ready or blocked.",
        }
    verification = dict(LOOP_VERIFICATION_CONTROL)
    if lane == "order_ticket_approval_execution_gate":
        verification["detail"] = "After each pass, verify ticket, approval receipt, policy allow state, duplicate-request status, connection posture, audit evidence, and blocked actions; stop with revise, blocked, or waiting instead of widening the lane."
    elif lane in {"connector_build", "head_manager_connector_operations"}:
        verification["detail"] = "After each pass, verify service status, connector metadata, secret-free posture, blocked actions, and doctor or validation output; stop with revise, blocked, or waiting instead of widening the lane."
    elif lane == "head_manager_strategy_authoring":
        verification["detail"] = "After each pass, verify required strategy sections, validation errors, user approval status, blocked actions, and absence of policy, broker, approval, or execution authority; stop with revise, blocked, or waiting instead of widening the lane."
    return [*controls, verification]


def build_judgment_controls(plan: dict[str, Any]) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    controls = [dict(item) for item in JUDGMENT_CONTROL_COPY.get(lane, JUDGMENT_CONTROL_COPY["default"])]
    if workflow_plan_has_role(plan, JUDGMENT_REVIEW_ROLE):
        return controls
    for item in controls:
        if item.get("label") == "Independent judgment review":
            item["label"] = "Evidence check"
            item["detail"] = "Before synthesis, check selected role artifacts for source freshness, missing evidence, and blocked actions without adding a separate challenge role."
    return controls


def build_review_highlights(plan: dict[str, Any], profile_status: dict[str, Any] | None = None) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    review = next(
        (item for item in build_judgment_controls(plan) if item.get("label") not in {"Fixed rule baseline", "Fixed service baseline"}),
        {},
    )
    review_label = {
        "Independent judgment review": "Independent review",
        "Service gate verification": "Service gate",
    }.get(review.get("label"), review.get("label") or "Review")
    highlights = [
        {
            "label": review_label,
            "detail": review.get("detail")
            or "Before synthesis, name the strongest reason the idea may be wrong.",
        }
    ]
    if lane in PROFILE_REQUIRED_LANES:
        status = profile_status or investor_profile_status(plan)
        missing = list(status.get("missing_fields") or [])
        if missing:
            profile_gap_prefix = "Recommendation and sizing stay weak until these are answered: "
            if lane == "order_ticket_approval_execution_gate":
                profile_gap_prefix = "Approved-action readiness is incomplete until these are confirmed: "
            elif lane == "order_ticket_draft_gate":
                profile_gap_prefix = "Draft-order readiness is incomplete until these are answered: "
            elif lane == "portfolio_risk_review":
                profile_gap_prefix = "Portfolio and risk review stay weak until these are answered: "
            highlights.append({
                "label": "Profile gap",
                "detail": profile_gap_prefix + ", ".join(missing) + ".",
            })
        else:
            highlights.append({
                "label": "Profile fit",
                "detail": "Saved objective, horizon, risk, liquidity, holdings, and constraints must stay visible in the decision.",
            })
    stop_detail = "A useful answer can end at decision support; order, approval, and execution remain separate gates."
    if lane == "order_ticket_approval_execution_gate":
        stop_detail = "Submission stays blocked unless ticket, approval, policy, duplicate-request, connection, and audit checks pass."
    elif lane == "order_ticket_draft_gate":
        stop_detail = "Stop after draft-order readiness; approval and execution remain separate gates."
    elif lane == "portfolio_risk_review":
        stop_detail = "A useful answer can end at portfolio and risk review; order, approval, and execution remain separate gates."
    elif lane in {"connector_build", "head_manager_connector_operations"}:
        stop_detail = "Stop before secrets, order tickets, approvals, execution, or raw broker API calls."
    elif lane == "head_manager_strategy_authoring":
        stop_detail = "Stop after strategy drafting or validation; do not turn it into ticker analysis, recommendation, order, approval, or execution."
    highlights.append({
        "label": "Stop before action",
        "detail": stop_detail,
    })
    return highlights


def build_strategy_baseline(workspace_root: Path | str | None = None) -> dict[str, Any]:
    if workspace_root is None:
        return {
            "mode": "not_inspected",
            "active_strategies": [],
            "summary": "Strategy library not inspected in this preview; use explicit user constraints and fixed TradingCodex rules as the baseline.",
            "prompt_summary": "Strategy library not inspected; use explicit user constraints and fixed TradingCodex rules.",
        }
    try:
        records = read_strategy_skill_records(workspace_root, active_only=True)
    except Exception:
        records = []
    strategies = [
        {
            "name": str(record.get("name") or ""),
            "heading": str(record.get("heading") or record.get("name") or ""),
            "status": str(record.get("status") or ""),
        }
        for record in records
        if record.get("name")
    ]
    if strategies:
        names = ", ".join(item["name"] for item in strategies)
        return {
            "mode": "active_user_strategy",
            "active_strategies": strategies,
            "summary": "Active user-approved strategy skills available: "
            + names
            + ". Select at most one relevant strategy and treat it as fixed context, not authority to approve or execute.",
            "prompt_summary": "Active user-approved strategy skills available: "
            + names
            + ". Select at most one relevant strategy as fixed context only.",
        }
    return {
        "mode": "no_saved_strategy",
        "active_strategies": [],
        "summary": "No active user-approved strategy is saved for this workspace; use explicit user constraints and fixed TradingCodex rules as temporary workflow context, not a persistent strategy.",
        "prompt_summary": "No active user-approved strategy; treat request preferences as temporary context, not a persistent strategy.",
    }


def format_method_lenses(plan: dict[str, Any]) -> str:
    return "; ".join(
        f"{item['label']} ({item['reference']})"
        for item in build_method_lenses(plan)
    )


def format_loop_controls(plan: dict[str, Any]) -> str:
    return "; ".join(
        f"{item['label']}: {item['detail']}"
        for item in build_loop_controls(plan)
    )


def format_judgment_controls(plan: dict[str, Any]) -> str:
    return "; ".join(
        f"{item['label']}: {item['detail']}"
        for item in build_judgment_controls(plan)
    )


def format_judgment_controls_compact(plan: dict[str, Any]) -> str:
    return "; ".join(item["label"] for item in build_judgment_controls(plan))


def build_next_allowed_actions(plan: dict[str, Any], profile_status: dict[str, Any] | None = None) -> list[dict[str, str]]:
    lane = str(plan.get("lane") or "research_only")
    actions = [dict(item) for item in NEXT_ALLOWED_ACTION_COPY.get(lane, NEXT_ALLOWED_ACTION_COPY["research_only"])]
    if lane == "thesis_review":
        terms = _joined_context_terms(_thesis_context_terms(plan))
        actions[0] = {
            "label": "Dispatch thesis roles",
            "detail": (
                f"Collect selected {terms} context only for the requested thesis scope."
                if workflow_plan_has_role(plan, "valuation-analyst")
                else f"Collect selected {terms} context only for the requested non-valuation thesis scope."
            ),
        }
    if lane == "order_ticket_draft_gate":
        prerequisites = ["order scope", "instrument support", "canonical order fields"]
        if workflow_plan_has_role(plan, "portfolio-manager"):
            prerequisites.insert(0, "portfolio fit")
        if workflow_plan_has_role(plan, "risk-manager"):
            prerequisites.insert(1 if "portfolio fit" in prerequisites else 0, "risk review")
        actions[0] = {
            "label": "Complete draft prerequisites",
            "detail": "Confirm " + ", ".join(prerequisites[:-1]) + ", and " + prerequisites[-1] + ".",
        }
    if lane in PROFILE_REQUIRED_LANES and actions and actions[0].get("label") == "Answer missing profile questions":
        status = profile_status or investor_profile_status(plan)
        missing = list(status.get("missing_fields") or [])
        known = list(status.get("known_fields") or [])
        required = list(status.get("required_fields") or [])
        if required and not missing:
            actions[0] = {
                "label": "Use saved profile context",
                "detail": "Objective, horizon, loss capacity, liquidity, holdings, and constraints are present; keep them visible while dispatching roles.",
            }
        elif known:
            actions[0] = {
                "label": "Answer remaining profile questions",
                "detail": "Still missing: " + ", ".join(missing) + ".",
            }
    return actions


def build_idea_translation(plan: dict[str, Any]) -> dict[str, str]:
    lane = str(plan.get("lane") or "research_only")
    lane_copy = workflow_lane_copy(plan)
    copy = dict(IDEA_TRANSLATION_COPY.get(lane, IDEA_TRANSLATION_COPY["research_only"]))
    if lane == "thesis_review":
        terms = _joined_context_terms(_thesis_context_terms(plan))
        if workflow_plan_has_role(plan, "valuation-analyst"):
            copy["working_hypothesis"] = f"Treat the idea as a thesis check: review selected {terms} context without moving into portfolio action."
        else:
            copy["working_hypothesis"] = f"Treat the idea as a thesis check: review selected {terms} context without valuation or portfolio action."
            copy["safety_boundary"] = "Valuation, order, approval, and execution paths remain blocked unless the user later asks for a broader lane."
    return {
        "label": "Idea translated",
        "plain_english": f"{lane_copy['label']}: {lane_copy['primary_question']}",
        "working_hypothesis": copy["working_hypothesis"],
        "safety_boundary": copy["safety_boundary"],
    }


def workflow_lane_copy(plan: dict[str, Any]) -> dict[str, str]:
    lane = str(plan.get("lane") or "research_only")
    copy = dict(WORKFLOW_LANE_COPY.get(lane, WORKFLOW_LANE_COPY["research_only"]))
    if lane == "thesis_review":
        terms = _joined_context_terms(_thesis_context_terms(plan))
        if terms:
            copy["summary"] = (
                f"Review {terms} context without moving into portfolio action."
                if workflow_plan_has_role(plan, "valuation-analyst")
                else f"Review {terms} context without valuation or portfolio action."
            )
        if not workflow_plan_has_role(plan, "valuation-analyst"):
            copy["primary_question"] = "Does the thesis have enough current non-valuation evidence to support a research view?"
    return copy


def _thesis_context_terms(plan: dict[str, Any]) -> list[str]:
    role_terms = [
        ("fundamental-analyst", "business"),
        ("technical-analyst", "technical"),
        ("news-analyst", "news"),
        ("macro-analyst", "macro"),
        ("instrument-analyst", "instrument"),
        ("valuation-analyst", "valuation"),
    ]
    return [term for role, term in role_terms if workflow_plan_has_role(plan, role)]


def _joined_context_terms(terms: list[str]) -> str:
    if len(terms) <= 1:
        return terms[0] if terms else "selected evidence"
    if len(terms) == 2:
        return " and ".join(terms)
    return ", ".join(terms[:-1]) + ", and " + terms[-1]


def build_profile_questions(plan: dict[str, Any], workspace_root: Path | str | None = None) -> list[dict[str, str]]:
    questions = []
    for field in investor_profile_status(plan, workspace_root)["missing_fields"]:
        copy = PROFILE_QUESTION_COPY[field]
        questions.append({
            "category": "investor_profile",
            "field": field,
            "key": PROFILE_FIELD_KEYS[field],
            "question": copy["question"],
            "why_required": copy["why_required"],
        })
    return questions


def build_blocked_action_details(actions: list[str]) -> list[dict[str, str]]:
    details = []
    for action in actions:
        copy = BLOCKED_ACTION_COPY.get(action, {})
        details.append({
            "action": action,
            "label": copy.get("label") or action.replace("_", " ").title(),
            "reason": copy.get("reason") or "This action waits for the required TradingCodex workflow gate.",
        })
    return details


def build_workflow_stages(plan: dict[str, Any]) -> list[dict[str, Any]]:
    lane = str(plan.get("lane") or "")
    subagents = list(plan.get("subagents") or [])
    role_labels = {role: AGENT_SPECS[role].label for role in subagents if role in AGENT_SPECS}
    stages: list[dict[str, Any]] = [
        {
            "key": "intake",
            "label": "Intake",
            "owner": "head-manager",
            "summary": "Classify the request, preserve user constraints, and keep blocked actions visible.",
            "exit_criteria": ["workflow lane selected", "required roles identified", "blocked actions recorded"],
            "roles": ["head-manager"],
        }
    ]
    if lane == "head_manager_connector_operations":
        stages.append({
            "key": "connector_setup",
            "label": "Connector setup",
            "owner": "head-manager",
            "summary": "Inspect connector metadata and health without reading secrets or creating trade artifacts.",
            "exit_criteria": ["connector metadata reviewed", "secret-free status reported", "no order artifacts created"],
            "roles": ["head-manager"],
        })
        stages.append(_synthesis_stage())
        return stages
    if lane == "connector_build":
        stages.append({
            "key": "build_gate",
            "label": "Build gate",
            "owner": "head-manager",
            "summary": "Verify Codex full access and explicit TradingCodex build mode before modifying connector or harness files.",
            "exit_criteria": ["full access detected", "TradingCodex build mode active", "live submission remains service-gated"],
            "roles": ["head-manager"],
        })
        stages.append({
            "key": "connector_scaffold",
            "label": "Connector scaffold",
            "owner": "head-manager",
            "summary": "Scaffold or register a broker/API provider using credential_ref, read scopes, signed health, and service-gated validation.",
            "exit_criteria": ["connector files generated", "credential_ref schema present", "provider status checked", "live submit not performed"],
            "roles": ["head-manager"],
        })
        stages.append(_synthesis_stage())
        return stages
    if lane == "head_manager_strategy_authoring":
        stages.append({
            "key": "strategy_authoring",
            "label": "Strategy authoring",
            "owner": "head-manager",
            "summary": "Draft or update a fixed strategy guide through strategy-creator validation.",
            "exit_criteria": ["required strategy sections present", "risk controls stated", "no action authority added"],
            "roles": ["head-manager"],
        })
        stages.append(_challenge_review_stage())
        stages.append(_synthesis_stage())
        return stages

    research_roles = [role for role in subagents if role in RESEARCH_STAGE_ROLES]
    if research_roles:
        stages.append({
            "key": "evidence",
            "label": "Evidence",
            "owner": "research roles",
            "summary": "Collect source-aware role artifacts before any downstream judgment.",
            "exit_criteria": ["artifact paths written", "source/as-of posture recorded", "missing evidence named"],
            "roles": [{"role": role, "label": role_labels.get(role, role)} for role in research_roles],
        })
    if "valuation-analyst" in subagents:
        stages.append({
            "key": "valuation",
            "label": "Valuation",
            "owner": "valuation-analyst",
            "summary": "Translate accepted evidence into scenarios, assumptions, valuation range, and uncertainty.",
            "exit_criteria": ["assumptions stated", "scenario range produced", "confidence and sensitivity recorded"],
            "roles": [{"role": "valuation-analyst", "label": role_labels.get("valuation-analyst", "valuation-analyst")}],
        })
    has_judgment_review = JUDGMENT_REVIEW_ROLE in subagents
    judgment_after_research = has_judgment_review and bool(research_roles or "valuation-analyst" in subagents)
    if judgment_after_research:
        stages.append(_judgment_review_stage(role_labels))
    if lane == "order_ticket_draft_gate":
        stages.append({
            "key": "order_ticket_draft",
            "label": "Order draft",
            "owner": "portfolio-manager",
            "summary": "Prepare a structured order-ticket candidate while approval and execution stay blocked.",
            "exit_criteria": ["canonical order fields complete", "policy checks ready", "approval remains blocked"],
            "roles": [{"role": "portfolio-manager", "label": role_labels.get("portfolio-manager", "portfolio-manager")}],
        })
    elif "portfolio-manager" in subagents:
        stages.append({
            "key": "portfolio_fit",
            "label": "Portfolio fit",
            "owner": "portfolio-manager",
            "summary": "Check exposure, concentration, liquidity, opportunity cost, and investor-profile gaps.",
            "exit_criteria": ["portfolio impact stated", "profile gaps named", "sizing support or blockage recorded"],
            "roles": [{"role": "portfolio-manager", "label": role_labels.get("portfolio-manager", "portfolio-manager")}],
        })
    if "risk-manager" in subagents:
        stages.append({
            "key": "risk_review",
            "label": "Risk review",
            "owner": "risk-manager",
            "summary": "Review policy, restricted-list, downside, approval readiness, and blocked actions.",
            "exit_criteria": ["policy decision recorded", "downside risks stated", "approval readiness or blocked reason returned"],
            "roles": [{"role": "risk-manager", "label": role_labels.get("risk-manager", "risk-manager")}],
        })
    if has_judgment_review and not judgment_after_research:
        stages.append(_judgment_review_stage(role_labels))
    if "execution-operator" in subagents:
        stages.append({
            "key": "execution_boundary",
            "label": "Approved action path",
            "owner": "execution-operator",
            "summary": "Submit only if ticket, approval receipt, policy, duplicate-request, connection, and audit checks pass.",
            "exit_criteria": ["approved ticket matched", "duplicate-request status checked", "connection and audit result recorded"],
            "roles": [{"role": "execution-operator", "label": role_labels.get("execution-operator", "execution-operator")}],
        })
    stages.append(_synthesis_stage())
    return stages


def _judgment_review_stage(role_labels: dict[str, str]) -> dict[str, Any]:
    return {
        "key": "judgment_review",
        "label": "Judgment review",
        "owner": JUDGMENT_REVIEW_ROLE,
        "summary": "Independently test accepted upstream artifacts against contrary evidence, source trust, stale or missing data, overconfidence, update triggers, invalidation conditions, and downstream readiness.",
        "exit_criteria": ["judgment review artifact written", "source trust and contrary evidence named", "accepted, revise, blocked, or waiting outcome returned", "downstream blocked when support is weak"],
        "roles": [{"role": JUDGMENT_REVIEW_ROLE, "label": role_labels.get(JUDGMENT_REVIEW_ROLE, "Judgment Reviewer")}],
    }


def _challenge_review_stage(has_risk_role: bool = False) -> dict[str, Any]:
    roles: list[Any] = ["head-manager"]
    if has_risk_role:
        roles.append({"role": "risk-manager", "label": "Risk Manager"})
    return {
        "key": "challenge_review",
        "label": "Challenge review",
        "owner": "head-manager" if not has_risk_role else "head-manager with risk-manager artifact",
        "summary": "Test accepted artifacts against contrary evidence, stale or weak source posture, alternative scenarios, rule or strategy conflicts, and blocked actions before synthesis.",
        "exit_criteria": ["counterarguments named", "source trust named", "rule and strategy conflicts checked", "revise, blocked, or waiting used when support is weak"],
        "roles": roles,
    }


def _synthesis_stage() -> dict[str, Any]:
    return {
        "key": "synthesis",
        "label": "Synthesis",
        "owner": "head-manager",
        "summary": "Save the accepted-artifact synthesis as a head-manager Markdown report, then return a brief chat summary with the report path and next allowed action.",
        "exit_criteria": ["accepted artifacts cited", "uncertainties preserved", "next allowed action stated"],
        "roles": ["head-manager"],
    }


def infer_research_artifact_language(request: str) -> str:
    return "same language as the original user request unless explicitly overridden"




ROLE_NODE_POSITIONS: dict[str, tuple[int, int]] = {
    "head-manager": (50, 10),
    "fundamental-analyst": (12, 29),
    "technical-analyst": (31, 29),
    "news-analyst": (50, 29),
    "macro-analyst": (69, 29),
    "instrument-analyst": (88, 29),
    "valuation-analyst": (31, 53),
    "judgment-reviewer": (50, 53),
    "portfolio-manager": (50, 66),
    "risk-manager": (69, 78),
    "execution-operator": (88, 91),
}


TOPOLOGY_EDGES: tuple[dict[str, str], ...] = (
    {"source": "head-manager", "target": "fundamental-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "technical-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "news-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "macro-analyst", "group": "dispatch"},
    {"source": "head-manager", "target": "instrument-analyst", "group": "dispatch"},
    {"source": "fundamental-analyst", "target": "valuation-analyst", "group": "research-handoff"},
    {"source": "technical-analyst", "target": "valuation-analyst", "group": "research-handoff"},
    {"source": "news-analyst", "target": "valuation-analyst", "group": "research-handoff"},
    {"source": "fundamental-analyst", "target": "judgment-reviewer", "group": "judgment-review-gate"},
    {"source": "technical-analyst", "target": "judgment-reviewer", "group": "judgment-review-gate"},
    {"source": "news-analyst", "target": "judgment-reviewer", "group": "judgment-review-gate"},
    {"source": "macro-analyst", "target": "judgment-reviewer", "group": "judgment-review-gate"},
    {"source": "instrument-analyst", "target": "judgment-reviewer", "group": "judgment-review-gate"},
    {"source": "valuation-analyst", "target": "judgment-reviewer", "group": "judgment-review-gate"},
    {"source": "judgment-reviewer", "target": "portfolio-manager", "group": "portfolio-risk-gate"},
    {"source": "portfolio-manager", "target": "risk-manager", "group": "approval-gate"},
    {"source": "risk-manager", "target": "execution-operator", "group": "execution-gate"},
)


EDGE_GROUP_LABELS: dict[str, str] = {
    "dispatch": "Dispatch",
    "research-handoff": "Research handoff",
    "judgment-review-gate": "Judgment review gate",
    "portfolio-risk-gate": "Portfolio/risk gate",
    "approval-gate": "Approval gate",
    "execution-gate": "Approved action gate",
}


def get_harness_topology(workspace_root: Path | str | None = None) -> dict[str, Any]:
    tools = _static_mcp_tools()
    nodes = []
    for role, skills in ROLE_SKILL_MAP.items():
        x, y = ROLE_NODE_POSITIONS[role]
        spec = AGENT_SPECS[role]
        allowed_tools = _allowed_tools_for_role(role, tools)
        nodes.append({
            "role": role,
            "label": spec.label,
            "group": ROLE_DISPLAY_GROUPS.get(role, spec.group),
            "purpose": ROLE_PURPOSES.get(role, ""),
            "skills_count": len(skills),
            "tools_count": len(allowed_tools),
            "x": x,
            "y": y,
        })
    edges = []
    for edge in TOPOLOGY_EDGES:
        source_x, source_y = ROLE_NODE_POSITIONS[edge["source"]]
        target_x, target_y = ROLE_NODE_POSITIONS[edge["target"]]
        mid_y = round((source_y + target_y) / 2, 2)
        edges.append({
            **edge,
            "label": EDGE_GROUP_LABELS[edge["group"]],
            "contract": EDGE_GROUP_CONTRACTS[edge["group"]],
            "source_x": source_x,
            "source_y": source_y,
            "target_x": target_x,
            "target_y": target_y,
            "mid_y": mid_y,
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "edge_groups": [{"key": key, "label": label, "contract": EDGE_GROUP_CONTRACTS[key]} for key, label in EDGE_GROUP_LABELS.items()],
        "handoff_states": list(HANDOFF_STATES),
        "systems": get_harness_systems(),
        "components": list_harness_components(),
        "component_tag_counts": count_harness_component_tags(),
        "layers": [
            {"label": "Coordinator", "y": 10},
            {"label": "Research roles", "y": 29},
            {"label": "Valuation", "y": 53},
            {"label": "Judgment review", "y": 53},
            {"label": "Portfolio fit", "y": 66},
            {"label": "Risk approval", "y": 78},
            {"label": "Approved submission", "y": 91},
        ],
        "boundary": {
            "label": "Approved action boundary",
            "summary": "Execution-sensitive actions must prove the requester, permission, policy fit, exact approval, duplicate-request status, connection, and audit trail.",
            "x": 78,
            "y1": 72,
            "y2": 96,
        },
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_harness_systems() -> list[dict[str, Any]]:
    return [
        {
            "key": "guardrails",
            "label": "Guardrails",
            "summary": "Reduce, isolate, and block risky behavior before executable action.",
            "items": [
                {"label": "Guidance", "summary": "Prompts, skills, hooks, and checklists shape agent behavior."},
                {"label": "Enforcement", "summary": "Policy, schemas, approvals, allowlists, duplicate-request checks, and connection gates block unsafe final paths."},
                {"label": "Information barriers", "summary": "Role-local context, file walls, secret walls, and tool boundaries limit knowledge flow."},
            ],
        },
        {
            "key": "improvement",
            "label": "Improvement",
            "summary": "Raise workflow quality and feed improve records back into the harness.",
            "items": [
                {"label": "Workflow quality", "summary": "Workflow maps, no-overlap handoffs, role briefs, quality gates, and readiness labels."},
                {"label": "Research memory", "summary": "Workspace markdown artifacts, versions, source snapshots, and freshness warnings."},
                {"label": "Skill evolution", "summary": "File proposal, validation, projection, and manifest state."},
                {"label": "Postmortems", "summary": "Rejected orders, thesis changes, and process failures become concrete improvements."},
                {"label": "Validation feedback", "summary": "Recurring issues become tests, smoke checks, and routing scenarios."},
            ],
        },
    ]


def get_role_detail(role: str, workspace_root: Path | str | None = None) -> dict[str, Any]:
    if role not in ROLE_SKILL_MAP:
        role = "head-manager"
    tools = _static_mcp_tools()
    spec = AGENT_SPECS[role]
    return {
        "role": role,
        "label": spec.label,
        "group": ROLE_DISPLAY_GROUPS.get(role, spec.group),
        "purpose": ROLE_PURPOSES.get(role, ""),
        "skills": ROLE_SKILL_MAP[role],
        "handoff_contract": ROLE_HANDOFF_CONTRACTS.get(role, {}),
        "handoff_states": list(HANDOFF_STATES),
        "allowed_tools": _allowed_tools_for_role(role, tools),
        "forbidden_actions": list(ROLE_FORBIDDEN_ACTIONS.get(role, ())),
        "latest_artifacts": _latest_role_artifacts(role, workspace_root),
        "latest_activity": _latest_role_activity(role, workspace_root),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def get_harness_health(workspace_root: Path | str | None = None) -> dict[str, Any]:
    try:
        ensure_runtime_database(workspace_root)
    except Exception:
        pass

    from tradingcodex_service.mcp_runtime import static_mcp_tools

    tools = static_mcp_tools()
    counts = {
        "roster": len(EXPECTED_SUBAGENTS),
        "roles_total": len(ROLE_SKILL_MAP),
        "skills": len(EXPECTED_SKILLS),
        "components": len(list_harness_components()),
        "mcp_tools": len(tools),
        "mcp_execution_tools": sum(1 for tool in tools if tool.get("annotations", {}).get("risk_level") == "execution"),
        "policy_blocks": _model_count("apps.policy.models", "PolicyDecision", decision="deny"),
        "restricted_symbols": _model_count("apps.policy.models", "RestrictedSymbol", active=True),
        "workspace_contexts": _model_count("apps.harness.models", "WorkspaceContext"),
        "research_artifacts": _workspace_research_count(workspace_root),
        "order_tickets": _model_count("apps.orders.models", "OrderTicket"),
        "approval_receipts": _model_count("apps.orders.models", "ApprovalReceipt"),
        "execution_results": _model_count("apps.orders.models", "ExecutionResult"),
        "mcp_calls": _model_count("apps.mcp.models", "McpToolCall"),
    }
    checks = [
        {"label": "Fixed subagent roster", "value": f"{counts['roster']} of {len(EXPECTED_SUBAGENTS)}", "status": "good"},
        {"label": "Repo skills installed", "value": str(counts["skills"]), "status": "good"},
        {"label": "Handoff contract", "value": "/".join(HANDOFF_STATES), "status": "good"},
        {"label": "Harness components", "value": str(counts["components"]), "status": "good"},
        {"label": "Available actions", "value": str(counts["mcp_tools"]), "status": "good"},
        {"label": "Execution tools", "value": str(counts["mcp_execution_tools"]), "status": "warn"},
        {"label": "Policy blocks", "value": str(counts["policy_blocks"]), "status": "neutral"},
        {"label": "Workspace contexts", "value": str(counts["workspace_contexts"]), "status": "neutral"},
    ]
    return {
        "counts": counts,
        "checks": checks,
        "systems": get_harness_systems(),
        "components": list_harness_components(),
        "component_tag_counts": count_harness_component_tags(),
        "db_path": str(tradingcodex_db_path()),
        "central_local_service": True,
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def list_recent_activity(workspace_root: Path | str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    try:
        ensure_runtime_database(workspace_root)
    except Exception:
        pass
    context = workspace_context_payload(workspace_root)
    items: list[dict[str, Any]] = []
    try:
        from apps.mcp.models import McpToolCall

        for call in _filter_workspace_queryset(McpToolCall.objects, context).order_by("-created_at", "-id")[:limit]:
            items.append({
                "kind": "MCP",
                "title": call.tool_name,
                "subtitle": call.principal_id,
                "status": call.status,
                "status_class": _status_class(call.status),
                "created_at": call.created_at,
            })
    except Exception:
        pass
    try:
        from apps.audit.models import AuditEvent

        for event in _filter_workspace_queryset(AuditEvent.objects, context).order_by("-created_at", "-id")[:limit]:
            items.append({
                "kind": "Audit",
                "title": event.action,
                "subtitle": event.actor_principal,
                "status": event.decision,
                "status_class": _status_class(event.decision),
                "created_at": event.created_at,
            })
    except Exception:
        pass
    try:
        from apps.workflows.models import WorkflowRun

        for run in _filter_workspace_queryset(WorkflowRun.objects, context).order_by("-created_at", "-id")[:limit]:
            items.append({
                "kind": "Workflow",
                "title": run.lane,
                "subtitle": run.universe,
                "status": run.status,
                "status_class": _status_class(run.status),
                "created_at": run.created_at,
            })
    except Exception:
        pass
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items[:limit]


def list_policy_overview(workspace_root: Path | str | None = None) -> dict[str, Any]:
    try:
        ensure_runtime_database(workspace_root)
    except Exception:
        pass
    restricted_symbols: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    principals: list[dict[str, Any]] = []
    try:
        from apps.policy.models import PolicyDecision, Principal, RestrictedSymbol

        restricted_symbols = [
            {"symbol": item.symbol, "reason": item.reason, "active": item.active, "status_class": "bad" if item.active else "neutral"}
            for item in RestrictedSymbol.objects.order_by("symbol")[:50]
        ]
        decisions = [
            {
                "principal_id": item.principal_id,
                "action": item.action,
                "resource": item.resource,
                "decision": item.decision,
                "reasons": item.reasons,
                "created_at": item.created_at,
                "status_class": _status_class(item.decision),
            }
            for item in PolicyDecision.objects.order_by("-created_at", "-id")[:20]
        ]
        principals = [
            {"principal_id": item.principal_id, "role": item.role, "active": item.active}
            for item in Principal.objects.order_by("role", "principal_id")[:50]
        ]
    except Exception:
        pass
    return {
        "restricted_symbols": restricted_symbols,
        "recent_decisions": decisions,
        "principals": principals,
        "explicit_denies": sorted(EXPLICIT_DENY_ACTIONS),
        "db_canonical": True,
        "workspace_context": workspace_context_payload(workspace_root),
    }


def _allowed_tools_for_role(role: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = []
    for tool in tools:
        annotations = tool.get("annotations") or {}
        risk_level = str(annotations.get("risk_level", "read") or "read")
        if role in annotations.get("allowed_roles", []):
            allowed.append({
                "name": tool["name"],
                "category": annotations.get("category", ""),
                "risk_level": risk_level,
                "risk_label": risk_level.replace("_", " ").title(),
                "requires_approval": bool(annotations.get("requires_approval")),
                "status_class": _status_class(risk_level),
            })
    return allowed


def _latest_role_artifacts(role: str, workspace_root: Path | str | None) -> list[dict[str, Any]]:
    try:
        role_alias = role.replace("-analyst", "").replace("-manager", "").replace("-operator", "")
        artifacts = [
            artifact
            for artifact in list_workspace_research_artifacts(Path(workspace_root or Path.cwd()))
            if artifact.get("created_by") == role or artifact.get("role") in {role, role_alias}
        ]
        return [
            {
                "artifact_id": artifact["artifact_id"],
                "title": artifact["title"],
                "artifact_type": artifact["artifact_type"],
                "universe": artifact["universe"],
                "readiness_label": artifact.get("readiness_label") or "unlabeled",
                "updated_at": artifact["updated_at"],
            }
            for artifact in artifacts[:5]
        ]
    except Exception:
        return []


def _workspace_research_count(workspace_root: Path | str | None) -> int:
    try:
        return len(list_workspace_research_artifacts(Path(workspace_root or Path.cwd())))
    except Exception:
        return 0


def _latest_role_activity(role: str, workspace_root: Path | str | None = None) -> list[dict[str, Any]]:
    try:
        from apps.mcp.models import McpToolCall

        context = workspace_context_payload(workspace_root)
        return [
            {
                "title": call.tool_name,
                "status": call.status,
                "status_class": _status_class(call.status),
                "created_at": call.created_at,
            }
            for call in _filter_workspace_queryset(McpToolCall.objects.filter(principal_id=role), context).order_by("-created_at", "-id")[:5]
        ]
    except Exception:
        return []


def _filter_workspace_queryset(queryset: Any, context: dict[str, Any]) -> Any:
    workspace_id = str(context.get("workspace_id") or "")
    if workspace_id:
        return queryset.filter(workspace_context__workspace_id=workspace_id)
    return queryset.none()


def _model_count(module_name: str, class_name: str, **filters: Any) -> int:
    try:
        module = __import__(module_name, fromlist=[class_name])
        model = getattr(module, class_name)
        queryset = model.objects.filter(**filters) if filters else model.objects
        return int(queryset.count())
    except Exception:
        return 0
