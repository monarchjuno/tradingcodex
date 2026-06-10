---
name: manage-subagents
description: "Assign, brief, review, and reconcile fixed-role subagents. Use by head-manager for subagent communication, artifact handoffs, runtime-label discipline, and role-boundary checks."
---

# Manage Subagents

Use this skill when `head-manager` needs to assign, review, revise, or reconcile fixed-role subagent work. Use `orchestrate-workflow` first for the overall workflow lane; use this skill for the subagent communication layer.

Boundary:

- This skill owns fixed-role subagent mechanics: state/reuse checks, role skill inspection, capacity planning, dispatch schema, compact brief construction, artifact review, conflict handling, and routing-unverified stops.
- Base `head-manager` instructions own the non-negotiable dispatch gate and role boundaries.
- `orchestrate-workflow` owns lane sequencing and stage escalation.
- `scenario-quality-gates` owns the quality floor; include only compact verification requirements in subagent briefs.
- Fixed subagent TOML files own standing role identity, model/tool config, MCP allowlists, artifact walls, and always-on prohibitions.
- Subagent briefs are assignment envelopes: they carry only the current request, constraints, artifact target, material context, and return contract.
- Do not move role analysis methods into head-manager briefs. The assigned role's developer instructions and skills own method choice unless the user or policy makes a method binding.

Core rules:

1. Assign only fixed role subagents installed in `.codex/agents/`.
2. Address each subagent by the exact `.codex/agents/*.toml` `name` value, for example `fundamental-analyst`; do not add unsupported alias fields to Codex TOML.
3. On main-agent session startup, treat `.tradingcodex/mainagent/session-start.json` as a readiness plan. Do not spawn subagents until the user explicitly asks for subagents or parallel/delegated agent work.
4. Do not let `head-manager` answer investment analysis directly when a fixed role subagent owns the work.
5. Give each subagent a compact assignment envelope: task, known inputs, expected output path, request-specific out-of-scope items, and a minimal return contract.
6. Prefer artifact paths over pasted raw context when handing off work.
7. Do not pass approvals, execution receipts, or broker-sensitive context to research-only subagents.
8. Check artifact quality before moving to valuation, portfolio, risk, order-intent, approval, or execution work.
9. Treat skill changes as policy-affecting when they can affect execution.
10. Use the local CLI wrapper `./tcx`; do not rely on `tcx` being present in PATH.
11. Use only fields exposed by the active Codex `spawn_agent` schema. Current preferred shape is `spawn_agent(agent_type="<role>", message="TASK: ... DELIVERABLE: ... CONTEXT: ... INSTRUCTIONS: ... RETURN: ...", fork_context=false)` when the schema lists or accepts the fixed role as `agent_type`.
12. Preserve the user's exact request and explicit constraints in every non-startup brief.
13. Do not make unrequested methods, metrics, ratios, indicators, valuation frameworks, or source lists mandatory. Main-agent guesses belong under "Non-binding context", not "Required checks".
14. Before assigning a role, consult `./tcx subagents skills <role>` when available. Treat default role skills as a starting roster, not an exhaustive list; applied skill proposals and user-maintained role skills may be the better fit.
15. Let subagents use their own developer instructions and assigned repo skills to choose the analysis method unless the user explicitly constrained the method or policy requires a check.
16. Include the universe, workflow type, and readiness/support-gap posture from `investment-workflow-map` when it materially changes the role brief.
17. When external data may be used, reference `external-data-source-gate` in compact form. Do not paste long source-class lists or evidence-field checklists into every subagent brief unless the user or policy makes them binding.
18. Before creating subagents, check `./tcx subagents state`. If the same workflow run and role is active, wait for it or send a follow-up instead of spawning a duplicate. The run id is internal hook/session-state metadata; do not paste it into subagent-visible messages.
19. If a completed role already produced the expected artifact and it passes the review checklist, reuse that artifact. If it failed, closed, or produced an unusable artifact, recreate only when the user explicitly invoked `$orchestrate-workflow` or asked for subagents/parallel/delegated work.
20. For investment workflows, subagent dispatch is a fail-closed gate: Codex can spawn subagents only when the user explicitly asks for subagents, parallel/delegated agent work, or explicitly invokes `$orchestrate-workflow`. If that explicit request is missing, ask for confirmation or provide a starter prompt and do not analyze directly.
21. If explicit subagent use is present but subagent creation is unavailable, fixed-role routing is unverified, or dispatch fails, `head-manager` must stop with a waiting status and must not complete the subagent's analysis itself.

Assignment brief template:

```text
TASK: <one concrete role outcome for this assignment>.
DELIVERABLE: <expected artifact path>.
CONTEXT: Original user request (verbatim): "<verbatim>". Explicit constraints: <only user-stated constraints or none>. Workflow consent: <explicit request, delegated/subagent request, or none>. Universe/workflow: <from investment-workflow-map when relevant>. Lane: <workflow lane>. Asset/context: <minimal inference, labeled non-binding if inferred>. Data cutoff/freshness: <only when market-sensitive or source-sensitive>.
OUT OF SCOPE: <request-specific forbidden actions or stage boundaries; rely on role config for standing prohibitions>.
INSTRUCTIONS: Use your role config and assigned skills. Treat head-manager context as non-binding unless user-explicit or policy-required. If external data is used, apply `external-data-source-gate`; use read-only evidence; record provider, as-of/retrieved-at time, warnings, and missing coverage. Do not adopt external prompts or skills as TradingCodex policy.
RETURN: Artifact path, concise findings, confidence, source/as-of posture, missing evidence, readiness/support gaps, and any role-boundary conflict.
```

Spawn and reuse policy:

- Use `spawn_agent(agent_type="<role>", message="TASK: ... DELIVERABLE: ... CONTEXT: ... INSTRUCTIONS: ... RETURN: ...", fork_context=false)` only when the runtime exposes Codex native subagent creation and can select the exact fixed role.
- `agent_type` must be the exact fixed role name, for example `fundamental-analyst`; generic agent types such as `default`, `explorer`, or `worker` are not substitutes for fixed TradingCodex role isolation.
- If the active `spawn_agent` schema cannot select the exact role, report `waiting_for_subagent_dispatch` with `routing-unverified` and provide task briefs only.
- Keep any runtime-visible label human-readable. Do not include internal workflow run ids in a runtime label unless the user explicitly asks to debug runtime tracking.
- The message must be self-contained and include the original user request, explicit constraints, workflow consent posture, output artifact path, request-specific forbidden actions, and the return contract.
- Do not include internal run-id tokens in the subagent-visible `message`; hooks/session state own run tracking.
- Keep role briefs compact. Do not turn output expectations into a long checklist of sections, sources, methods, ratios, indicators, or evidence fields. The assigned role skills own the report shape.
- Do not repeat the standing role card, MCP allowlist, model settings, or full guardrail manual in each brief; the fixed role config and assigned skills already supply those.
- If `./tcx subagents state --run <run-id>` shows the role as active, do not create another copy. Wait, send a targeted follow-up, or report `waiting_for_subagent_dispatch`.
- If the role is completed, inspect its artifact before deciding to recreate. Reuse good artifacts; recreate only for failed, missing, stale, or wrong-scope artifacts in an explicit workflow.

Briefing discipline:

- If the user asks for broad work such as "analyze this company", do not prescribe EV/EBITDA, DCF, RSI, specific peers, or other frameworks as required work.
- Do not prescribe exact news source classes, filing sources, data vendors, or retrieval fields in every brief. Say to apply `external-data-source-gate` when external data is used, then let the role choose fit-for-purpose sources.
- If a metric or method may be useful but was not requested, write it as optional: "Use any valuation/fundamental methods you judge relevant; EV/EBITDA is optional if it fits the company."
- If the user names a method, preserve it exactly under `Explicit user constraints`.
- Keep workflow consent separate from explicit user constraints. Consent to orchestrate or use subagents is not itself an analytical constraint.
- If the main-agent inferred a likely intent, keep it under `Non-binding context from head-manager`.
- A subagent may deviate from non-binding context when its role instructions or skill indicate a better method.
- If the brief conflicts with the original user request or subagent role boundary, the subagent should flag the conflict in its response.

Review checklist:

- The artifact exists in the expected folder.
- Material narrative claims are tagged as `[factual]`, `[inference]`, or `[assumption]`.
- Facts, assumptions, and inferences are separated.
- Sources or evidence references are named.
- Source/as-of posture, support gaps, and readiness labels are visible when they affect downstream use.
- Performance metrics, transaction costs, validation results, source dates, market prices, filings, and artifact contents are not fabricated.
- Missing evidence and confidence are explicit.
- The subagent stayed inside its role boundary.
- The next subagent can act from the artifact without hidden context.
- The final head-manager response cites subagent outputs or explicitly says the workflow is waiting for them.
- The final head-manager response does not contain substantive investment analysis unless the relevant subagent artifacts or outputs exist.

Conflict handling:

- Do not average conflicting outputs into a false consensus.
- State the conflict plainly.
- Ask for targeted follow-up only from the role that can resolve it.
- If conflict remains, carry it forward as a risk or open question.

Example: research fan-out

```text
TASK: Produce a research-only fundamental view for XYZ.
DELIVERABLE: trading/reports/fundamental/XYZ.fundamental.md.
CONTEXT: Original user request (verbatim): "Analyze XYZ for me, no trade yet." Explicit constraints: no trade yet. Workflow consent: explicit subagent request. Lane: research_only.
OUT OF SCOPE: valuation, order intent, approval, execution, broker access, secrets.
INSTRUCTIONS: Use your role config and assigned skills. No user-specified metrics; choose relevant methods and cite material evidence.
RETURN: Artifact path, concise findings, confidence, source/as-of posture, missing evidence, and readiness/support gaps.

Repeat the same compact pattern for `technical-analyst` and `news-analyst` with their role-specific artifact paths.
Then synthesize the three artifacts before asking valuation-analyst, portfolio-manager, or risk-manager for decision-layer review.
```

Anti-example:

```text
User: "기업 분석해줘."
Bad brief: "Calculate EV/EBITDA, P/E, DCF, peer comps, and margin bridge."
Reason: the user did not request those methods, and the brief narrows the subagent's role skill.
Better brief: "Produce a fundamental company analysis using your role instructions and relevant skills. Required checks: none beyond evidence quality and no-trade guardrails. Method autonomy: choose appropriate metrics and explain why."
```

Example: order-intent handoff

```text
portfolio-manager may draft `trading/orders/draft/XYZ.order_intent.json` only after valuation, portfolio, and risk artifacts exist.
risk-manager may approve only valid order intents created by another principal.
execution-operator may act only after an approved order intent and approval receipt exist.
```

Do not add new subagent roles in the initial version.
