# Financial Workflow References

TradingCodex should stay Codex-native while making expert financial workflow
steps legible to non-expert users. Product surfaces should translate a plain
request into a role workflow, show what is still blocked, and ask for missing
investor context before recommendation, sizing, approval, or execution.
This is the product's safe intuition-led workflow: a user can start with a
rough market intuition, but TradingCodex turns it into evidence gathering,
profile questions, role handoffs, and explicit next allowed actions instead of
jumping straight to a trade.
Short ticker-first intuition such as "TSLA feels interesting" or
"AAPL seems cheap" should therefore route to a research-first workflow unless
the user explicitly asks for valuation, portfolio fit, recommendation, order
drafting, approval, or execution.

## Workflow Principles

| Principle | Product implication | Reference |
| --- | --- | --- |
| Portfolio risk comes before single-security action | Portfolio, risk, and draft-order lanes ask for objective, horizon, risk tolerance, liquidity needs, holdings, and constraints before recommendation, sizing, or approval. Approved-action execution lanes verify the existing approval receipt instead of re-asking profile questions. | FINRA Rule 2111 customer investment profile and suitability guidance |
| Retail recommendations require investor-profile context | Decision-support workflows should expose missing profile fields rather than producing blanket recommendations. | SEC Regulation Best Interest staff bulletin on care obligations |
| Asset allocation, diversification, and rebalancing are the baseline risk frame | Research and portfolio outputs should separate single-name evidence from portfolio exposure, concentration, and rebalancing context. | SEC Investor.gov asset allocation/diversification/rebalancing guidance |
| Mean-variance and factor thinking are analytical tools, not automatic advice | Valuation and portfolio roles can use expected return, variance, covariance, and factor exposure concepts, but must expose assumptions and uncertainty. | Markowitz (1952); Fama and French (1993) |
| Strategic allocation depends on horizon and risk tolerance | Long-horizon workflows should treat time horizon and loss capacity as required context, not afterthoughts. | Campbell and Viceira (2002) |
| Investment policy is objectives plus constraints | Portfolio workflows should frame recommendations against return/risk objectives, liquidity, time horizon, tax, legal/regulatory, and unique constraints. | CFA Institute investment policy statement guidance |
| Planning is a process, not a one-shot answer | Beginner-facing UX should show workflow stage, next evidence needed, and blocked actions before presenting outputs as advice. | CFP Board financial planning process |
| Each workflow should make later workflows easier | Role artifacts should preserve reusable context summaries, reader summaries, source snapshots, missing-evidence notes, and improvement proposals instead of making future agents rediscover the same gaps. | Every compound engineering guide; Will Larson commentary |
| Agentic work should be a bounded loop, not open-ended autonomy | TradingCodex workflows may repeat evidence gathering, artifact verification, synthesis, and memory capture, but every lane must expose stop conditions and blocked actions. | Recent loop-engineering practice across Codex, Claude Code, OpenClaw, and memory-layer frameworks |
| Loop reliability depends on the verifier | Every workflow lane should name what gets checked after each pass: artifact quality, source freshness, profile gaps, and blocked actions. Failed checks return `revise`, `blocked`, or `waiting` rather than widening the lane. | Addy Osmani loop-engineering framing; recent verifier/budget loop guidance |
| Financial agent teams need explicit opposition | Decision-support synthesis should test the favorable case against a bearish or skeptical case, stale data, missing profile context, policy conflicts, and selected-strategy conflicts before naming a decision state. | TradingAgents bull/bear researcher debate; risk management team |
| AI-assisted finance must stay auditable and bounded | Agent workflows should keep role boundaries, source/as-of posture, handoff states, policy gates, duplicate-request controls, and audit evidence visible. | NIST AI Risk Management Framework |
| AI/ML in capital markets needs governance, testing, monitoring, data quality, and explainability | Agentic workflow features should keep dispatch, role outputs, source quality, and audit trails inspectable. | IOSCO AI/ML guidance for market intermediaries and asset managers |
| Plain English increases investor comprehension without deleting complexity | User-facing web output should show plain-language workflow summaries first, with professional evidence and caveats behind them. | SEC Plain English Handbook |

## Source Map

| Source | TradingCodex design rule |
| --- | --- |
| [FINRA Rule 2111](https://www.finra.org/rules-guidance/rulebooks/finra-rules/2111) | Investor-profile fields are first-class workflow inputs: objective, other investments, tax/account constraints, horizon, liquidity, and risk tolerance. |
| [SEC Reg BI care-obligation staff bulletin](https://www.sec.gov/about/divisions-offices/division-trading-markets/broker-dealers/staff-bulletin-standards-conduct-broker-dealers-investment-advisers-care-obligations) | Decision-support lanes must expose missing retail-investor context before recommendation, sizing, or order work. |
| [SEC Investor.gov asset allocation guidance](https://www.investor.gov/introduction-investing/getting-started/asset-allocation) | Single-name research should stay separate from portfolio allocation, diversification, and rebalancing checks. |
| [Markowitz, "Portfolio Selection" (1952)](https://ideas.repec.org/a/bla/jfinan/v7y1952i1p77-91.html) | Portfolio roles should treat expected return, variance, covariance, and diversification as analytical lenses, not as automatic trade instructions. |
| [Fama and French, "Common risk factors in the returns on stocks and bonds" (1993)](https://ideas.repec.org/a/eee/jfinec/v33y1993i1p3-56.html) | Research and portfolio outputs should separate issuer-specific thesis claims from factor, market, maturity, and default-risk exposure. |
| [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework) | Agentic finance workflows need visible governance, context mapping, measurement, and risk-management checkpoints. |
| [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) | Workflow intake, handoff states, quality checks, and audit outputs should map to govern, map, measure, and manage functions. |
| [IOSCO AI/ML report for market intermediaries and asset managers](https://www.iosco.org/library/pubdocs/pdf/IOSCOPD684.pdf) | AI-assisted capital-markets features need governance, testing, monitoring, data-quality controls, explainability, and outsourcing/third-party oversight posture. |
| [SEC Plain English Handbook](https://www.sec.gov/pdf/handbook.pdf) | User-facing screens should start with plain-English workflow meaning before professional evidence, assumptions, caveats, and artifacts. |
| [Every, "Compound Engineering"](https://every.to/guides/compound-engineering) | Engineering work should teach the system reusable capabilities; TradingCodex applies this by making role outputs preserve compact context, reader summaries, source posture, and missing-evidence notes for future workflows. |
| [Will Larson, "Learning from Every's Compound Engineering"](https://lethain.com/everyinc-compound-engineering/) | The useful pattern is separating research/planning, work, review, and compounding lessons; TradingCodex mirrors that with starter intake, role artifacts, quality checks, and improvement proposals. |
| [Addy Osmani, "Loop Engineering"](https://addyosmani.com/blog/loop-engineering/) | A useful agentic loop combines automations, isolated workspaces, skills, connectors, subagents, and durable external memory; TradingCodex maps these to workflow intake, fixed subagents, MCP/service boundaries, artifacts, and generated workspace state. |
| [Business Insider, "Forget prompt engineering: Loop engineering is all the rage now"](https://www.businessinsider.com/what-are-loops-ai-engineering-tips-2026-6) | Current loop-engineering discourse emphasizes designing recurring systems that prompt agents, with cost and oversight tradeoffs; TradingCodex exposes loop controls and token/context budgets rather than unlimited autonomy. |
| [Anthropic Claude Code skills docs](https://docs.anthropic.com/en/docs/claude-code/skills) | Recent agent tooling exposes loops through reusable skills, bundled `/loop`, subagent execution, and dynamic context; TradingCodex mirrors the reusable-skill and fixed-subagent pattern without depending on Claude Code. |
| [Anthropic Claude Code hooks docs](https://docs.anthropic.com/en/docs/claude-code/hooks-guide) | Agent hooks can verify conditions before allowing a workflow to continue; TradingCodex maps this to hook gates, context-audit, quality-check, and handoff states. |
| [OpenClaw](https://github.com/openclaw/openclaw) | Agent control planes show the value and risk of persistent assistant loops across devices/channels; TradingCodex keeps loops workspace-scoped, auditable, and blocked from secrets or execution unless service gates allow it. |
| [Mem0, "Loop Engineering for AI Agents: Memory-First Design"](https://mem0.ai/blog/loop-engineering-for-ai-agents-memory-first-design) | Memory-first loop design separates durable memory from a single chat context; TradingCodex uses workspace-native artifacts, source snapshots, context summaries, and generated manifests as its durable memory layer. |
| [TradingAgents repository](https://github.com/tauricresearch/tradingagents) and [TradingAgents paper](https://arxiv.org/abs/2412.20138) | The useful pattern is not autonomous execution, but explicit role specialization, bull/bear researcher debate, trader synthesis, and risk review. TradingCodex adapts this as challenge review before synthesis while keeping order, approval, and execution behind service gates. |

## Workflow Handoff Intake Contract

The workflow planner is a local preparation surface that produces a Codex
handoff. It must not spawn agents, produce investment analysis, approve orders,
or execute trades. It should:

- classify the request into a workflow lane and investment universe
- translate rough investor intuition into a plain-English working hypothesis
  and safety boundary
- show the stage order in plain language, such as evidence, valuation,
  portfolio fit, risk review, approved action boundary, and synthesis
- show stage exit criteria so users can see what must be true before the
  workflow moves forward
- keep lane loop controls, judgment controls, strategy baseline, and method
  lenses available in a review section so users can inspect professional
  reasoning without making the first screen feel like an expert checklist
- expose method lenses such as suitability/profile, portfolio risk, factor
  exposure, execution-boundary, or AI-governance references with a short
  plain-language reason each lens matters
- show the selected role team in readable labels and explain why each role is
  needed for the lane
- show blocked actions before artifacts exist, with a plain-language reason
  for each blocked action
- show the next allowed actions so users can distinguish safe preparation from
  blocked order, approval, or execution paths
- ask for investor profile context when the lane reaches decision support,
  portfolio fit, order drafting, approval, or execution
- reuse answered active-profile investor context so users are not asked the
  same suitability/profile question every time
- translate missing profile fields into direct user questions, with a short
  reason each answer is required
- keep the generated Codex prompt compact enough for native dispatch and place
  the raw prompt in a handoff section rather than making it the first-read UX
- instruct agents to write plain-English first, then professional evidence,
  assumptions, and caveats
- instruct agents to preserve reusable context, source snapshots, missing
  evidence, tests, or improvement proposals when a lesson should compound into
  future workflows

Loop engineering applies only inside the workflow boundary selected at intake.
TradingCodex can loop over research discovery, artifact quality, stale-source
checks, profile gaps, and synthesis revisions. It must not let a loop widen
itself into recommendation, order drafting, approval, execution, raw broker API,
or secret handling. Those transitions require explicit user intent and the
separate service-layer gates already documented in the harness.

When no active user-approved strategy exists, TradingCodex should not invent a
fixed contract. It should state that the current request uses generated
TradingCodex rules, explicit user constraints, investor-profile context, and
temporary scenario assumptions. A durable strategy is created only through the
strategy authoring path, not as a side effect of analysis.

## Source Notes

- SEC Investor.gov describes asset allocation, diversification, and
  rebalancing as core risk-management practices for individual investors.
- SEC Reg BI staff guidance describes investment-profile factors such as
  financial situation, needs, investments, tax status, time horizon, liquidity
  needs, risk tolerance, investment experience, objectives, and goals.
- FINRA suitability materials define customer-specific suitability around the
  customer's investment profile, including objectives, risk tolerance, horizon,
  liquidity needs, and other customer-specific factors.
- Markowitz's portfolio-selection framework is the product reason portfolio
  review treats variance, covariance, and diversification as required context
  before action.
- Fama-French factor framing is the product reason research outputs must
  distinguish company-specific claims from market, size, value, maturity, and
  default-risk exposures.
- CFA Institute IPS guidance frames investment strategy around overall
  financial plans, objectives, risk tolerance, preferences, and constraints.
- CFP Board practice standards treat planning as a collaborative process that
  integrates personal and financial circumstances before advice.
- The SEC Plain English Handbook frames plain English as deciding what
  investors need to know, then presenting complex information clearly.
- NIST AI RMF emphasizes governing, mapping, measuring, and managing AI risks
  across real-world use, including transparency and documentation concerns.
- IOSCO AI/ML guidance highlights governance and oversight, algorithm
  development/testing/monitoring, data quality and bias, transparency and
  explainability, outsourcing, and ethical concerns for capital-markets AI.
- Addy Osmani frames loop engineering as designing the system that prompts
  agents, built from automations, worktrees, skills, connectors, subagents, and
  memory outside a single conversation.
- Recent loop-engineering frameworks converge on recurring agent triggers,
  reusable procedures, subagent decomposition, verification hooks, connectors,
  and durable memory. TradingCodex should stay framework-agnostic: use these
  ideas through its own Codex-native workflow intake, skills, MCP/service boundary,
  context-audit, role artifacts, and explicit human approval gates.
- The verifier is the bottleneck in a useful loop. TradingCodex therefore keeps
  loop controls tied to source freshness, artifact quality, profile gaps, and
  blocked actions rather than letting the agent repeat work until it invents a
  broader mandate.
- TradingAgents uses specialized analysts, bull and bear researchers, trader
  synthesis, and risk management. TradingCodex borrows the opposition and risk
  review structure, but does not let that structure self-approve, self-execute,
  or silently create a user strategy.
