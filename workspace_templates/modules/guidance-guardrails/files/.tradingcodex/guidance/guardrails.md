# Guidance Guardrails

Guidance guardrails reduce unsafe behavior, but they do not prove that an action is blocked.

Examples:

- `AGENTS.md` instructions
- role subagent instructions
- skill checklists
- hooks that detect direct broker paths
- secret scan warnings
- MCP server instructions
- doctor checks

Execution-sensitive actions still require enforcement through TradingCodex MCP.
Guidance guardrails sit under the top-level TradingCodex harness alongside
enforcement guardrails, information barriers, and Improvement loops.
