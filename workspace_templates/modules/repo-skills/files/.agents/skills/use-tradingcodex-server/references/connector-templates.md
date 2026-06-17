# Connector Templates

Templates describe broker API families. They are not raw API bindings and do not create broker-specific MCP tools.

## US Retail REST

Examples: Alpaca, Tradier, Schwab-style.

- Assets: equities, ETFs, options; sometimes crypto.
- Strengths: REST account/order APIs, paper/sandbox options, simple order status polling.
- Watch: OAuth/API-key auth, account approvals, fractional support, shorting, option permissions.

## Multi-Asset Gateway

Example: IBKR-style.

- Assets: equities, options, futures, FX, bonds, funds.
- Strengths: broad instrument coverage and account data.
- Watch: gateway/session preflight, contract id identity, market-data permissions, broker-side confirmations.

## Options And Futures Specialist

Example: tastytrade-style.

- Assets: options, complex option strategies, futures, equities, crypto metadata.
- Strengths: dry-run/buying-power preview and complex order concepts.
- Watch: multi-leg validation, buying-power impact, account stream semantics.

## Korean Securities

Example: KIS-style.

- Assets: domestic and overseas stocks.
- Strengths: REST and websocket examples for account, quote, and cash order flows.
- Watch: real/mock domains, TR-ID separation, account product code, Korea market sessions.

## Crypto Exchanges

Examples: exchange-specific spot templates.

- Assets: spot, margin, futures/perps depending on venue.
- Strengths: symbol filters, private streams, order-test or order-chance endpoints.
- Watch: quantity vs quote notional, min notional, lot/price filters, STP/SMP, locked balances, withdrawal/travel-rule APIs.
- Test/sandbox crypto connectors may expose broker-native validation endpoints.
  Treat those as validation-only inputs behind TradingCodex MCP; do not expose
  raw exchange tools or paste API keys into files/prompts.

### Binance Spot Testnet

- Template: `binance_spot`.
- Environment: `testnet`.
- Credential reference: use an `env:` reference such as `env:BINANCE_TESTNET`;
  keep raw API keys in the local process environment only.
- Posture: `broker_validation_only`. Registration starts read-only with no
  trade scopes until signed health proves the credential.
- Validation endpoint: `/api/v3/order/test`. It validates parameters,
  permissions, timestamp, and signature but does not create an exchange order.
- Default submit mode: `order_test`; actual testnet order placement requires a
  separate local environment gate and remains outside connector setup work.
- If signed health reports `binance_auth_rejected`, stop at blocked status and
  report credential/permission/IP allowlist remediation. Do not retry by calling
  raw Binance APIs or reading secrets.

## FX/CFD And Terminal Bridges

Examples: OANDA, MT5-style.

- Assets: FX, CFD, broker-specific symbols.
- Strengths: margin/order-check support and position views.
- Watch: terminal/gateway availability, dealer trigger rules, leverage and region restrictions.
