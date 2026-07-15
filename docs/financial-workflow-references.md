# Financial Workflow References

TradingCodex should stay Codex-native while making expert financial workflow
steps legible to non-expert users. Product surfaces should translate a plain
request into a role workflow, show what is still blocked, and ask for missing
investor context before recommendation, sizing, approval, or execution.
This is the product's safe intuition-led workflow: a user can start with a
rough market intuition, but TradingCodex turns it into evidence gathering,
investor-context questions, role handoffs, and explicit next allowed actions instead of
jumping straight to a trade.
For short ticker-first intuition such as "TSLA feels interesting" or "AAPL
seems cheap", Head Manager should begin with the smallest useful research
question unless the user explicitly asks for valuation, portfolio fit,
recommendation, order drafting, approval, or execution. No ticker keyword maps
to a default team.

## Reference Posture

Primary regulatory, standards-body, and original research sources define the
product rationale. Consultation papers are directional evidence, not binding
standards. Open-source projects, vendor documentation, and practitioner writing
below are design analogies only; they cannot justify execution authority,
performance claims, or another orchestration layer.

Controls stay proportional to the actual use case and materiality. A narrow
factual request should not inherit the full workflow for a recommendation or
order. A high-consequence decision should add the relevant source, data,
independent-challenge, suitability, and service gates without turning every
request into the same fixed team or checklist.

## Workflow Principles

| Principle | Product implication | Reference |
| --- | --- | --- |
| Portfolio risk comes before single-security action | Portfolio, risk, and draft-order work asks for objective, horizon, risk tolerance, liquidity needs, holdings, and constraints before recommendation, sizing, or approval. An explicitly approved action verifies its existing approval receipt instead of re-asking Investor Context questions. | FINRA Rule 2111 customer investment profile and suitability guidance |
| Retail recommendations require investor context | Decision-support workflows should expose missing investor-context fields rather than producing blanket recommendations. | SEC Regulation Best Interest staff bulletin on care obligations |
| Asset allocation, diversification, and rebalancing are the baseline risk frame | Research and portfolio outputs should separate single-name evidence from portfolio exposure, concentration, and rebalancing context. | SEC Investor.gov asset allocation/diversification/rebalancing guidance |
| Mean-variance and factor thinking are analytical tools, not automatic advice | Valuation and portfolio roles can use expected return, variance, covariance, and factor exposure concepts, but must expose assumptions and uncertainty. | Markowitz (1952); Fama and French (1993) |
| Strategic allocation depends on horizon and risk tolerance | Long-horizon work should treat time horizon and loss capacity as required context, not afterthoughts. | Campbell and Viceira (2002) |
| Investment policy is objectives plus constraints | Portfolio workflows should frame recommendations against return/risk objectives, liquidity, time horizon, tax, legal/regulatory, and unique constraints. | CFA Institute investment policy statement guidance |
| Planning is a process, not a one-shot answer | Beginner-facing UX should show current evidence state, the next evidence needed, and blocked actions before presenting outputs as advice. | CFP Board financial planning process |
| Each workflow should make later workflows easier | Role artifacts should preserve reusable context summaries, reader summaries, source snapshots, missing-evidence notes, and improve records instead of making future agents rediscover the same gaps. | Every compound engineering guide; Will Larson commentary |
| Agentic work should be a bounded loop, not open-ended autonomy | Head Manager may repeat evidence gathering, artifact verification, synthesis, and memory capture, but the current user mandate and service boundaries always expose stop conditions and blocked actions. | Recent loop-engineering practice across Codex, Claude Code, OpenClaw, and memory-layer frameworks |
| Loop reliability depends on the verifier | Each pass should name what gets checked: artifact quality, source freshness, Investor Context gaps, and blocked actions. Failed checks return `revise`, `blocked`, or `waiting` rather than widening the mandate. | Addy Osmani loop-engineering framing; recent verifier/budget loop guidance |
| Financial agent teams need explicit opposition | Decision-support synthesis should test the favorable case against a bearish or skeptical case, stale data, missing investor context, policy conflicts, and selected-strategy conflicts before naming a decision state. | TradingAgents bull/bear researcher debate; risk management team |
| AI-assisted finance must stay auditable and bounded | Agent workflows should keep role boundaries, source/as-of posture, handoff states, policy gates, duplicate-request controls, and audit evidence visible. | NIST AI Risk Management Framework |
| AI/ML in capital markets needs governance, testing, monitoring, data quality, and explainability | Agentic workflow features should keep dispatch, role outputs, source quality, and audit trails inspectable. | IOSCO AI/ML guidance for market intermediaries and asset managers |
| AI controls should follow use-case materiality | Apply lighter checks to low-risk inspection and stronger governance to material research, recommendation, portfolio, and execution-sensitive use. Do not use AI governance as a reason to force every request through the same team. | FSB 2026 responsible-AI sound-practices consultation; directional, not a standard |
| Data and third-party dependencies are model risks | Preserve source identity, knowledge cutoffs, data-quality notes, provider boundaries, and fail-closed connector review instead of treating a model answer as self-authenticating. | BIS FSI 2026 AI data-governance review; FSB 2025 AI-vulnerability monitoring report |
| Shared-model decisions need explicit disagreement tests | Material decisions should surface contrary evidence and independent challenge, while treating correlated LLM behavior as a risk to test rather than an established TradingCodex performance claim. | BIS Innovation Hub Project Logos research scope |
| Plain English increases investor comprehension without deleting complexity | User-facing web output should show plain-language workflow summaries first, with professional evidence and caveats behind them. | SEC Plain English Handbook |

## Source Map

| Source | TradingCodex design rule |
| --- | --- |
| [FINRA Rule 2111](https://www.finra.org/rules-guidance/rulebooks/finra-rules/2111) | Investor-profile fields are first-class workflow inputs: objective, other investments, tax/account constraints, horizon, liquidity, and risk tolerance. |
| [SEC Reg BI care-obligation staff bulletin](https://www.sec.gov/about/divisions-offices/division-trading-markets/broker-dealers/staff-bulletin-standards-conduct-broker-dealers-investment-advisers-care-obligations) | Decision-support work must expose missing retail-investor context before recommendation, sizing, or order work. |
| [SEC Investor.gov asset allocation guidance](https://www.investor.gov/introduction-investing/getting-started/asset-allocation) | Single-name research should stay separate from portfolio allocation, diversification, and rebalancing checks. |
| [Markowitz, "Portfolio Selection" (1952)](https://ideas.repec.org/a/bla/jfinan/v7y1952i1p77-91.html) | Portfolio roles should treat expected return, variance, covariance, and diversification as analytical lenses, not as automatic trade instructions. |
| [Fama and French, "Common risk factors in the returns on stocks and bonds" (1993)](https://ideas.repec.org/a/eee/jfinec/v33y1993i1p3-56.html) | Research and portfolio outputs should separate issuer-specific thesis claims from factor, market, maturity, and default-risk exposure. |
| [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework) | Agentic finance workflows need visible governance, context mapping, measurement, and risk-management checkpoints. |
| [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) | Request scoping, handoff states, quality checks, and audit outputs should map to govern, map, measure, and manage functions. |
| [IOSCO AI/ML report for market intermediaries and asset managers](https://www.iosco.org/library/pubdocs/pdf/IOSCOPD684.pdf) | AI-assisted capital-markets features need governance, testing, monitoring, data-quality controls, explainability, and outsourcing/third-party oversight posture. |
| [IOSCO 2025 AI in capital markets consultation](https://www.iosco.org/library/pubdocs/pdf/IOSCOPD788.pdf) | GenAI expands research, trading, operations, risk, and compliance use cases while preserving model/data, malicious-use, concentration, outsourcing, third-party, and human-interaction risks. Its consultation status does not make it a binding TradingCodex requirement. |
| [FSB 2026 responsible-AI sound-practices consultation](https://www.fsb.org/2026/06/fsb-consults-on-sound-practices-for-the-responsible-adoption-of-artificial-intelligence-ai/) | Use organisation-wide governance and lifecycle, cyber, ICT, and third-party controls in proportion to the actual use and materiality. The FSB explicitly describes these as consultation practices rather than an international standard or one prescriptive implementation. |
| [FSB 2025 AI adoption and vulnerability monitoring](https://www.fsb.org/uploads/P101025.pdf) | Keep model, data-quality, governance, third-party concentration, market-correlation, cyber, fraud, and disinformation risks observable through provenance and monitoring rather than hidden behind the agent interface. |
| [BIS FSI 2026, "In data we trust?"](https://www.bis.org/fsi/publ/insights73.htm) | Data privacy, quality, security, third-party dependency, and provider concentration support point-in-time source snapshots, redaction, provider review, and fail-closed data boundaries. |
| [BIS Innovation Hub Project Logos](https://www.bis.org/about/bisih/topics/suptech_regtech/logos.htm) | LLM portfolio-manager homogeneity and correlated responses are hypotheses being tested in simulation. TradingCodex uses contrary evidence and independent review but does not claim the project proves real-market harm or product superiority. |
| [SEC Plain English Handbook](https://www.sec.gov/pdf/handbook.pdf) | User-facing screens should start with plain-English workflow meaning before professional evidence, assumptions, caveats, and artifacts. |
| [Every, "Compound Engineering"](https://every.to/guides/compound-engineering) | Engineering work should teach the system reusable capabilities; TradingCodex applies this by making role outputs preserve compact context, reader summaries, source posture, and missing-evidence notes for future workflows. |
| [Will Larson, "Learning from Every's Compound Engineering"](https://lethain.com/everyinc-compound-engineering/) | The useful pattern is separating research/planning, work, review, and compounding memory; TradingCodex mirrors that with request scoping, role artifacts, quality checks, postmortem review, and improve records. |
| [Hermes Agent documentation](https://hermes-agent.nousresearch.com/docs/) and [Hermes skills system](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | Self-improvement should be procedural and user/profile-owned: durable memory and skills live outside the package update path, load on demand, and improve through explicit writable artifacts rather than hidden model-weight changes. TradingCodex adapts this as workspace-owned improve records and indexes. |
| [Hermes Honcho memory docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/honcho) | Long-term personalization works best as a bounded context layer with cadence, budget, and profile/session isolation. TradingCodex keeps improve memory workspace-scoped, compact, and read-only for future judgment until explicit maintenance applies changes. |
| [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) | Language feedback can improve future trials without model fine-tuning. TradingCodex stores reflective improve records as reviewable investment-judgment memory, not automatic policy or prompt mutation. |
| [Self-Refine: Iterative Refinement with Self-Feedback](https://arxiv.org/abs/2303.17651) | Iterative feedback/refinement can improve outputs at test time. TradingCodex maps this to artifact revision, waiting, blocked, and accepted handoff states. |
| [Voyager](https://arxiv.org/abs/2305.16291) and [MemGPT](https://arxiv.org/abs/2310.08560) | Skill libraries and hierarchical memory show why compact retrieval layers matter. TradingCodex keeps lesson history append-only and authenticates each lesson chain head for bounded reuse. |
| [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110) and [GEPA](https://arxiv.org/html/2507.19457v1) | Dynamic linking and trace-driven reflection inform reviewed lesson promotion, but TradingCodex stops short of autonomous prompt evolution because finance workflows need explicit audit and approval boundaries. |
| [Addy Osmani, "Loop Engineering"](https://addyosmani.com/blog/loop-engineering/) | A useful agentic loop combines automations, isolated workspaces, skills, connectors, subagents, and durable external memory; TradingCodex maps these to Head Manager coordination, fixed subagents, MCP/service boundaries, artifacts, and generated workspace state. |
| [Business Insider, "Forget prompt engineering: Loop engineering is all the rage now"](https://www.businessinsider.com/what-are-loops-ai-engineering-tips-2026-6) | Current loop-engineering discourse emphasizes designing recurring systems that prompt agents, with cost and oversight tradeoffs; TradingCodex exposes loop controls and token/context budgets rather than unlimited autonomy. |
| [Anthropic Claude Code skills docs](https://docs.anthropic.com/en/docs/claude-code/skills) | Recent agent tooling exposes loops through reusable skills, bundled `/loop`, subagent execution, and dynamic context; TradingCodex mirrors the reusable-skill and fixed-subagent pattern without depending on Claude Code. |
| [Anthropic Claude Code hooks docs](https://docs.anthropic.com/en/docs/claude-code/hooks-guide) | Agent hooks can verify conditions before allowing a workflow to continue; TradingCodex maps this to hook gates, context-audit, quality-check, and handoff states. |
| [OpenClaw](https://github.com/openclaw/openclaw) | Agent control planes show the value and risk of persistent assistant loops across devices/channels; TradingCodex keeps loops workspace-scoped, auditable, and blocked from secrets or execution unless service gates allow it. |
| [Mem0, "Loop Engineering for AI Agents: Memory-First Design"](https://mem0.ai/blog/loop-engineering-for-ai-agents-memory-first-design) | Memory-first loop design separates durable memory from a single chat context; TradingCodex uses workspace-native artifacts, source snapshots, context summaries, and generated manifests as its durable memory layer. |
| [TradingAgents repository](https://github.com/tauricresearch/tradingagents) and [TradingAgents paper](https://arxiv.org/abs/2412.20138) | The useful pattern is not autonomous execution, but explicit role specialization, bull/bear researcher debate, trader synthesis, and risk review. TradingCodex adapts this as challenge review before synthesis while keeping order, approval, and execution behind service gates. |

## Task Scope And Dynamic Coordination Contract

The native Codex task is the only analysis and skill-invocation surface. A user
begins with natural language or an exact projected skill, and only the generated
Head Manager dynamically selects and revises roles. The read-only browser viewer
may inspect resulting state but cannot preview prompts, start or resume runs, or
widen role and service authority. Native task handling should:

- preserve the user's objective, subject scope, explicit prohibitions, and
  safety boundary without assigning a semantic lane
- translate rough investor intuition into a plain-English working hypothesis
- show current evidence needs and blocked actions without presenting a
  precompiled stage order or selected team
- apply judgment controls, the Strategy baseline, and method lenses without
  turning the initial user request into an expert checklist
- expose method lenses such as investor-context suitability, portfolio risk, factor
  exposure, execution-boundary, or AI-governance references with a short
  plain-language reason each lens matters
- record roles only after Head Manager actually chooses them and explain the
  request-specific question each role owns
- show blocked actions before artifacts exist, with a plain-language reason
  for each blocked action
- show the next allowed actions so users can distinguish safe preparation from
  blocked order, approval, or execution paths
- ask for Investor Context when the requested work reaches decision support,
  portfolio fit, order drafting, approval, or execution
- reuse saved workspace investor context so users are not asked the
  same suitability question every time
- translate missing investor-context fields into direct user questions, with a short
  reason each answer is required
- keep child dispatch prompts compact and run-bound rather than copying the
  entire root task history
- instruct agents to write plain-English first, then professional evidence,
  assumptions, and caveats
- instruct agents to preserve reusable context, source snapshots, missing
  evidence, tests, or improve records when a finding should compound into
  future workflows

Loop engineering applies only inside the current user mandate and service
authority boundary. Head Manager can loop over research discovery, artifact
quality, stale-source checks, Investor Context gaps, and synthesis revisions. It
must not let a loop widen itself into recommendation, order drafting, approval,
execution, raw broker API, or secret handling. Those transitions require
explicit user intent and the separate service-layer gates already documented in
the harness.

When no active user-approved strategy exists, TradingCodex should not invent a
fixed contract. It should state that the current request uses generated
TradingCodex rules, explicit user constraints, investor context, and
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
- The 2026 FSB consultation emphasizes use-case materiality and proportionality
  across governance, model lifecycle, cyber, ICT, and third-party controls. It
  remains a consultation until the announced final report, so TradingCodex uses
  it as current direction rather than a binding standard.
- The 2025 FSB monitoring report and 2026 BIS FSI review reinforce observable
  model/data/provider dependencies. TradingCodex maps that to source snapshots,
  cutoffs, connector approval, redaction, and explicit unknown states rather
  than a new semantic router.
- BIS Project Logos is an active simulated-market research project. It supports
  testing disagreement and correlation risk, but it is not evidence that this
  product improves returns or prevents systemic outcomes.
- Addy Osmani frames loop engineering as designing the system that prompts
  agents, built from automations, worktrees, skills, connectors, subagents, and
  memory outside a single conversation.
- Recent loop-engineering frameworks converge on recurring agent triggers,
  reusable procedures, subagent decomposition, verification hooks, connectors,
  and durable memory. TradingCodex should stay framework-agnostic: use these
  ideas through its own Codex-native request scoping, skills, MCP/service boundary,
  context-audit, role artifacts, and explicit human approval gates.
- The verifier is the bottleneck in a useful loop. TradingCodex therefore keeps
  loop controls tied to source freshness, artifact quality, investor-context gaps, and
  blocked actions rather than letting the agent repeat work until it invents a
  broader mandate.
- TradingAgents uses specialized analysts, bull and bear researchers, trader
  synthesis, and risk management. TradingCodex borrows the opposition and risk
  review structure, but does not let that structure self-approve, self-execute,
  or silently create a user strategy.
