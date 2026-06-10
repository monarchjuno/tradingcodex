---
name: investment-workflow-map
description: "Map investment requests across asset universes into TradingCodex workflow lanes, role teams, source posture, hero artifacts, support files, and conservative readiness labels before dispatch or synthesis."
---

# Investment Workflow Map

Use this skill by `head-manager` before `scenario-quality-gates` or inside `orchestrate-workflow` when the request is an investment workflow. This skill improves routing and artifact quality; it does not replace fixed role subagents or let `head-manager` perform analyst work directly.

Boundary:

- This skill owns universe classification, workflow-type mapping, source/as-of posture, support gaps, hero/support artifact choice, and conservative readiness labels.
- `scenario-quality-gates` owns final scenario selection, role team, quality gates, and blocked actions.
- `manage-subagents` owns fixed-role dispatch details and role briefs.
- `synthesize-decision` owns final user-facing decision states.
- Readiness labels from this map are not approvals, permissions, or execution authorization.

Reference basis: this map incorporates institutional public-equity workflow patterns such as issuer tearsheets, idea triage, pre-earnings previews, post-earnings deep dives, catalyst calendars, thesis trackers, long/short pitches, valuation/model work, model audit, financial normalization, position sizing, hedge design, and report QC. Treat public equity as the first fully specified sleeve, not the only TradingCodex investment universe.

## Universe Boundary

TradingCodex can support multiple investment universes through the same safety model:

- Public equities, ADRs, ETFs, indices, and listed options.
- Public crypto market research and paper/stub workflows when no account access or trading API is exposed.
- Macro, rates, FX, commodities, and cross-asset overlays as research, thesis, or portfolio-risk inputs.
- Credit, convertibles, preferreds, and capital-structure signals as supported workflows when a role skill, external read-only source, and policy boundary are explicitly installed; until then, use them only as inputs to the allowed workflow and label handoff gaps.
- Private markets, funds, tax, legal, and regulated advice are out of scope unless the user installs explicit skills and policies for those domains.

Live execution remains outside the default product regardless of universe. Any executable action must still pass structured artifacts, policy, approval, TradingCodex MCP, and audit.

## Invocation Boundary

Apply this map when the user asks for investment work on a listed issuer, ticker, public security, ETF/index constituent, crypto asset, macro/commodity/rate/FX exposure, portfolio position, hedge, catalyst, thesis, model, or risk decision.

Do not use this map for generic background research, private-company diligence, banking deliverables, legal/tax advice, raw broker access, direct live execution, or unsupported instruments. When the universe is not fully supported by the installed role skills, classify the work as research-only, screen-grade, or blocked rather than pretending the harness can underwrite it.

## Source Posture

Every investment workflow needs an explicit source posture before synthesis:

- Source categories: issuer/company disclosures, regulator/exchange records, transcripts/presentations, macro or instrument data, internal research or user notes, portfolio/models/trackers, market data/estimates, current news, and instrument-specific sources such as borrow, options, liquidity, credit spread, or chain data when relevant.
- Freeze time: market-sensitive inputs such as price, consensus, estimates, ownership, short interest, borrow, options, index weights, funding rates, yields, spreads, FX, commodity prices, catalyst dates, and portfolio exposures need an as-of or retrieved-at timestamp.
- Evidence labels: separate reported facts, issuer claims, consensus/provider data, user-provided inputs, derived calculations, analyst assumptions, and PM judgment.
- Connector honesty: never imply live Bloomberg, FactSet, OpenBB, official regulator/exchange disclosure feeds, Binance account, broker, email, drive, or internal-system access unless that source is actually callable in the current runtime.
- Missing or stale load-bearing evidence must lower readiness, confidence, sizing/action language, or circulation status.

## Workflow Taxonomy

| Investment request | TradingCodex lane | Minimum useful team | Hero artifact | Readiness rule |
| --- | --- | --- | --- | --- |
| Issuer baseline, company tearsheet, public profile | `research_only` | fundamental, news; add valuation when valuation context is requested | issuer baseline or evidence-backed research note | factual baseline only; no recommendation |
| Idea screen, market map, watchlist triage | `research_only` or `thesis_review` | fundamental, news, valuation, portfolio when fit matters | idea triage report or watchlist board | research-priority status, not final recommendation |
| Pre-earnings preview | `thesis_review` | fundamental, news, valuation; add portfolio/risk if position action is requested | earnings preview report | freeze expectation bar and label missing consensus/guide/price data |
| Post-earnings deep dive | `thesis_review` | fundamental, news, valuation; add portfolio/risk if thesis break or sizing is requested | earnings deep-dive report | state what changed versus prior thesis and which sources are still missing |
| Catalyst calendar or monitoring queue | `thesis_review` | news, fundamental, portfolio when position context exists | catalyst calendar or tracker | confirmed dates, inferred windows, and unscheduled monitor items stay distinct |
| Thesis tracker update | `thesis_review` or `portfolio_risk_review` | fundamental, news, valuation, portfolio, risk when action changes | append-only thesis tracker or update memo | separate thesis status, security/instrument readiness, and action threshold origin |
| Long/short pitch, pair idea, trade expression | `thesis_review` then `portfolio_risk_review` | fundamental, news, valuation, portfolio, risk | trade pitch or memo | expression, catalyst, risk/reward, disconfirmers, and implementation gates must be visible |
| DCF, comps, model, scenario sensitivity | `thesis_review` | valuation; add fundamental for driver evidence and portfolio/risk for action use | valuation report or workbook | current-price implication, model status, and source-backed assumptions must be explicit |
| Model update, model audit, financial normalization | `thesis_review` support or `harness_administration` if only process/tooling | valuation plus support/QC; add fundamental for source facts | workbook, audit report, or normalization pack | support files do not create an investment conclusion by themselves |
| Event-driven, macro/economic impact, policy shock | `thesis_review` or `portfolio_risk_review` | news, fundamental or valuation as relevant, risk; add portfolio when position impact matters | event report or impact map | probabilities, timing, and payoffs are assumptions unless source-backed |
| Technical, trend, liquidity, volatility, or market-structure review | `research_only` or `portfolio_risk_review` | technical, news; add portfolio/risk when action or sizing is requested | technical/setup report | observations are not trade instructions; stale price data lowers readiness |
| Position sizing, hedge design, integrated risk plan | `portfolio_risk_review` | portfolio, risk; add valuation/news/technical when thesis or market inputs are stale | risk decision report | missing price/liquidity/borrow/options/funding inputs block implementation-ready language |
| Report/deck QC or circulation review | `harness_administration` or underlying lane | risk or owning role plus QC support | QC issue log or circulation memo | QC never certifies facts it cannot tie out |
| Unsupported universe or instrument | `blocked_request` or `research_only` | none, or research roles only | limitation memo | no order path; ask for installed workflow/policy or user-provided evidence |

## Hero And Support Artifacts

Choose a user-facing hero artifact first, then keep deterministic support files behind it:

- Research-heavy work: report, dashboard-style memo, thesis update, catalyst calendar, or evidence pack.
- Model-heavy work: workbook with a first visible cover or decision dashboard.
- Support work: source indexes, normalized CSVs, run logs, manifests, issue logs, and raw JSON are audit/support files unless the user explicitly asks for them.

If the user explicitly asks for a quick chat answer or no-file output, stay concise but keep source posture, missing evidence, and readiness visible.

## PM Judgment Questions

Use these questions to sharpen the dispatch brief and synthesis without forcing unrequested methods:

- What changed, and does it change the thesis, estimates, valuation support, sizing, hedge, or next catalyst?
- What is priced in, what is ignored, and where is the variant view?
- Why now: catalyst path, evidence cadence, or risk window?
- What breaks first in downside, and what would kill or upgrade the thesis?
- What action is allowed now: research, watchlist, wait for proof, re-underwrite, portfolio/risk review, draft order intent, approval, execution, or blocked?

## Readiness Labels

Use conservative labels:

- `factual-baseline`: source-backed context only.
- `screen-grade`: useful for prioritization, but missing load-bearing evidence.
- `not-decision-ready`: missing current price, source dates, base case, valid probabilities, portfolio context, instrument support, or implementation inputs required for the user's decision.
- `ready-for-portfolio-risk`: enough research/valuation/market context exists for sizing, fit, or risk review.
- `ready-for-draft`: portfolio and risk prerequisites exist and the user explicitly asks for a draft order intent.
- `blocked`: restricted list, secrets, direct broker access, unsupported live execution, unsupported instrument execution, policy-change-plus-execution, or missing required approval path.

These readiness labels complement `synthesize-decision`; they do not authorize orders, approvals, or execution.

## Dispatch Rules

- Preserve the original user request and explicit constraints in every role brief.
- Use the minimum useful role team for the workflow; do not summon the full roster by habit.
- Keep support skills and support artifacts subordinate to the owning investment workflow.
- Do not turn examples from this map into mandatory metrics, models, indicators, ratios, or source lists unless the user requested them or policy requires them.
- Apply `external-data-source-gate` before external MCPs, web sources, connectors, or imported skills.
- Apply `scenario-quality-gates` after this map to set the final lane, team, artifacts, blocked actions, and synthesis gate.
- If the user did not explicitly request subagents or `$orchestrate-workflow`, stop at confirmation/starter prompt for investment analysis; do not answer directly.
