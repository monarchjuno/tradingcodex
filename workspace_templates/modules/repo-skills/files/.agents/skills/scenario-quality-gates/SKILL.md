---
name: scenario-quality-gates
description: "Select the right workflow scenario, role team, artifact path plan, and quality gates for varied user requests before subagent dispatch or final synthesis."
---

# Scenario Quality Gates

Use this skill before assigning subagents and again before final synthesis.

Boundary:

- This skill owns scenario classification, minimum useful role team, artifact expectations, blocked actions, and artifact/synthesis quality gates.
- It does not own fixed-role spawn mechanics, runtime state reuse, or message templates; use `manage-subagents` for those.
- It does not own universe support mapping or source/as-of posture; use `investment-workflow-map` first when the request is an investment workflow.
- It does not authorize order drafting, approval, or execution. Readiness labels are quality states, not permissions.

Purpose:

- Match the user request to a scenario archetype.
- Pick the minimum useful role team without over-prescribing each role's methods.
- Set artifact expectations and quality gates so the team produces useful, comparable outputs.
- Prevent shallow summaries, false certainty, missing handoffs, and unsafe action escalation.

Universal quality floor:

- Preserve the original user request and explicit constraints.
- State the scenario archetype and workflow lane.
- Name the selected subagents and why each role is needed.
- For investment workflows, set subagent dispatch as required before any substantive head-manager answer. Also record whether the user's prompt explicitly requested subagents or parallel/delegated agent work.
- Use canonical artifact paths.
- Separate facts, assumptions, inferences, and missing evidence.
- Tag material narrative claims as `[factual]`, `[inference]`, or `[assumption]`.
- Include source dates or data timestamps when sources are used.
- State confidence and what would change the conclusion.
- Preserve material disagreements between subagents.
- End with one next allowed action and blocked actions.
- Do not turn unrequested metrics or frameworks into mandatory checks.
- For investment workflows, use `investment-workflow-map` to set the universe, workflow type, source/as-of posture, hero artifact versus support files, support gaps, and conservative readiness label.
- Keep market-sensitive data tied to freeze time, source date, or retrieved-at time when it affects the decision.

Risk, uncertainty, and anti-hallucination floor:

- Use `[factual]` only for verified data, cited source content, artifact content, or directly observed command/test output.
- Use `[inference]` for reasoned conclusions drawn from evidence.
- Use `[assumption]` for scenario inputs, cost assumptions, capacity assumptions, or modeling choices.
- Do not fabricate performance metrics, factor loadings, transaction costs, validation results, source dates, market prices, filings, or artifact contents.
- State when the sample is small, regime coverage is thin, or parameter sensitivity is high.
- State when evidence is suggestive rather than conclusive.
- Separate empirical stability from economic plausibility.
- Explicitly note when live implementation friction may erase paper alpha.
- If data quality, source coverage, sample size, regime coverage, or validation setup is weak, lower confidence rather than overstating the result.

Scenario playbook:

| Scenario | Typical user request | Lane | Minimum team | Quality gates |
| --- | --- | --- | --- | --- |
| Broad company analysis, no trade | "Analyze Samsung." | `research_only` | fundamental, news, technical | business drivers, recent events, technical context if useful, risks, missing evidence, no order |
| Decision support, no order | "Should I buy?" | `thesis_review` then `portfolio_risk_review` | fundamental, news, technical, valuation, portfolio, risk | bull/base/bear, valuation range, portfolio fit, downside, no draft unless explicit |
| Earnings or event update | "Earnings came out; what changed?" | `thesis_review` | fundamental, news, valuation if valuation changed, risk if downside changed | what changed vs prior thesis, magnitude, source dates, forward indicators, open questions |
| Public issuer baseline or tearsheet | "Build a source-backed issuer profile." | `research_only` | fundamental, news; add valuation if valuation context is requested | factual baseline, source inventory, stale/missing fields, no recommendation |
| Idea screen or watchlist triage | "Screen public-equity ideas in this theme." | `research_only` or `thesis_review` | fundamental, news, valuation, portfolio if fit matters | research-priority buckets, first rejection, next workflow, no final recommendation |
| Pre-earnings preview | "Preview this quarter before earnings." | `thesis_review` | fundamental, news, valuation | expectation bar, freeze time, guide/consensus/source posture, call questions, missing evidence |
| Post-earnings deep dive | "Analyze the print and transcript." | `thesis_review` | fundamental, news, valuation; add portfolio/risk if action or thesis break is requested | thesis change, clean vs headline result, source tags, catalysts, model/valuation impact |
| Catalyst calendar or thesis tracker | "Build a 90-day catalyst calendar" | `thesis_review` | news, fundamental, portfolio if position context exists | confirmed dates vs inferred windows, decision pressure, prep actions, append-only updates |
| Public-equity valuation/model/scenario work | "Build comps or scenario sensitivity." | `thesis_review` | valuation; add fundamental for driver evidence | current-price implication, source-backed assumptions, model status, not-decision-ready if anchors are missing |
| Position sizing or hedge plan | "How should I size or hedge this listed-equity thesis?" | `portfolio_risk_review` | portfolio, risk; add news/valuation if thesis inputs are stale | intended alpha, unwanted risk, retained exposure, binding constraint, implementation-readiness gaps |
| Model audit, financial normalization, or report QC | "Audit this public-equity model/report." | `harness_administration` or underlying investment lane | valuation, risk, or owning role depending on artifact | support findings affect readiness but do not create an investment conclusion by themselves |
| Macro or cross-asset read-through | "How do rates or oil affect this position?" | `research_only` or `thesis_review` | macro, news; add valuation/portfolio/risk if decision impact is requested | transmission channel, source/as-of posture, regime uncertainty, no instrument execution support by implication |
| Instrument mechanics or support review | "Can this ETF/options/crypto instrument fit the workflow?" | `research_only` or `portfolio_risk_review` | instrument, technical; add portfolio/risk if actionability is requested | contract/methodology/liquidity/support gaps, no unsupported execution path |
| Watchlist comparison | "Compare A vs B." | `thesis_review` | fundamental, news, valuation, portfolio | same criteria for both assets, tradeoffs, key uncertainty, no false precision |
| Existing position review | "I own this and it is down." | `portfolio_risk_review` | portfolio, risk, technical, news; add fundamental if thesis changed | exposure, drawdown, thesis break, hold/add/trim/exit conditions if requested, no unrequested order |
| Concentration or correlation review | "Will adding this over-concentrate me?" | `portfolio_risk_review` | portfolio, risk | exposure math, correlation logic, drawdown impact, sizing limits |
| Draft paper order intent | "Draft a paper order if analysis passes." | `order_intent_draft` | analysis roles as needed, portfolio, risk | prerequisites exist, draft fields valid, stop before approval |
| Approval and execution | "Submit approved paper order." | `approval_execution` | execution; risk if approval validity is uncertain | approved order, approval receipt, policy allow, MCP-only execution |
| Restricted, secret, direct broker, or unsupported live request | "Place live order directly." | `blocked_request` | none, or risk for policy memo if useful | refuse unsafe part, no secret access, no draft/approval/execution, safe alternative |
| Postmortem or rejected order | "Explain what failed." | `postmortem` | risk; execution if execution/audit details matter | timeline, intended vs actual, guardrail fired, root cause, process improvement |
| Harness administration | "Change a skill or subagent config." | `harness_administration` | none, or affected role for review | proposal artifact, validation plan, no policy change and execution in same workflow |

Edge-case quality rules:

- Direct company/security analysis prompt: requests such as "삼성전자 종목 분석해줘", "Analyze Samsung", or "005930 분석" are investment workflows. If the prompt does not explicitly ask for subagents, the head-manager may state the lane and selected team, then must ask for subagent workflow confirmation or provide a starter prompt. It must not provide business, valuation, technical, news, or recommendation content before fixed role subagent outputs exist.
- Broad analysis without a decision request: keep the lane `research_only` and do not add `valuation-analyst` unless the user asks for valuation, fair value, buy/sell decision support, thesis review, or an order path. Asking "how the workflow proceeds" does not upgrade the lane.
- Buy/sell decision support without an order: include `portfolio-manager` and `risk-manager` with the research/valuation team. The portfolio role checks fit, sizing context, concentration, and suitability for the user's portfolio even when no order draft is allowed.
- Restricted-symbol order requests: if the user says the symbol is restricted, blocked, or on a restricted list, classify as `blocked_request`. Do not create or list a draft order intent artifact; only an optional policy/risk memo is allowed.
- Method-constrained analysis: preserve the user's named method as binding, and do not add excluded methods. Example: if the user says DCF only, peer comps and EV/EBITDA stay blocked unless the user later allows them.
- External data setup or use: classify configuration or tool-surface changes as `harness_administration`; classify requests to use an approved read-only data source for evidence as the underlying research/decision lane. Always apply `external-data-source-gate`.
- Investment workflow selection: for issuer tearsheets, idea triage, earnings previews or deep dives, catalyst calendars, thesis trackers, long/short pitches, valuation/model work, scenario sensitivity, technical/market-structure reviews, macro or crypto research, model audits, financial normalization, sizing, hedge design, or report QC, use `investment-workflow-map` first, then select the final lane and role team here.
- Hero versus support artifacts: choose the reader-facing report/workbook/tracker first; CSV, JSON, source indexes, run logs, manifests, and normalized tables are support artifacts unless the user explicitly asks for them.
- Readiness labels: use `factual-baseline`, `screen-grade`, `not-decision-ready`, `ready-for-portfolio-risk`, `ready-for-draft`, or `blocked` conservatively; missing source/as-of posture lowers readiness.
- Regulator or filing-led research: classify as `research_only` unless the user asks for a decision; use filing/regulator sources as evidence inputs and record dates. Treat "focused on" or "중심으로" as primary-source preference, not exclusivity, unless the user says "only."
- Public crypto market review: classify as research or thesis review only; public market data is allowed evidence, but account access and trading remain blocked.
- Policy change plus execution in the same workflow: classify as `blocked_request`; never loosen policy and execute in one flow.
- Panic execution requests such as "sell everything now": route to `portfolio_risk_review` when a safer review path is possible, but block natural-language execution, broker calls, approval, and order submission until structured artifacts exist.
- Method autonomy requests: when the user says not to force metrics, assign outcomes and artifact paths, then let subagents choose methods from their role skills.

Dispatch gate:

```text
Scenario: <archetype>
Lane: <workflow_lane>
Original user request: "<verbatim>"
Explicit user constraints: <constraints>
Subagent dispatch required before substantive answer: true|false
Explicit subagent request present: true|false
Selected team: <role -> why needed>
Artifacts expected: <paths>
Quality gates: <checks from scenario playbook>
Blocked actions: <unsafe or out-of-scope actions>
If explicit subagent request missing: ask for confirmation/starter prompt; no direct analysis
If dispatch unavailable: waiting_for_subagent_dispatch; no direct analysis
```

Artifact review gate:

- The artifact answers the assigned role question.
- The artifact does not ignore user constraints.
- Evidence and assumptions are not mixed.
- Material narrative claims are tagged as `[factual]`, `[inference]`, or `[assumption]`.
- Metrics, costs, factor loadings, validation results, source dates, prices, filings, and artifact contents are not fabricated.
- Confidence and missing evidence are explicit.
- Weak data quality, thin samples, narrow regime coverage, high sensitivity, or weak validation setup lowers confidence.
- The artifact can be used by the next role without hidden context.

Synthesis gate:

- Do not synthesize until required artifacts exist or you explicitly say the workflow is waiting.
- Summarize by role, then reconcile conflicts.
- Preserve claim tags or restate material claims with `[factual]`, `[inference]`, or `[assumption]` when the distinction affects the conclusion.
- Do not turn suggestive evidence into a conclusive recommendation.
- Separate empirical stability from economic plausibility when discussing strategy, valuation, technical setup, or risk.
- Say whether the output is research-only, ready for risk review, ready for draft, blocked, approved, executed, or revise.
- If the user asked for action, state the exact remaining gate before action.
- If the user did not ask for action, stop before order intent.
