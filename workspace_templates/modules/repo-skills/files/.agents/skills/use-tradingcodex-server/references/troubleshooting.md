# Troubleshooting

## Doctor Fails

- Run `./tcx doctor --layer mcp` first.
- Confirm only the `tradingcodex` MCP server is configured.
- Confirm head-manager lacks `submit_approved_order` and `cancel_approved_order`.

## Credential Reference Missing

- Ask the user to configure a secret outside workspace files.
- Register only a stable `credential_ref` such as `env:ALPACA_PAPER`.
- Do not inspect `.env` or print raw credential values.

## Signed Credential Rejected

- Read `get_broker_connection_status.health.details` and the connection
  `metadata.credential_validation_details`.
- If the code is `binance_auth_rejected`, report that the Spot Testnet signed
  account check failed because the key, permissions, or IP allowlist does not
  currently authorize the action.
- Keep the connector read-only with no trade scopes. Do not proceed to account
  sync, order approval, or execution until signed health returns `ok`.
- Do not echo the key, inspect local secret files, call raw exchange APIs, or
  widen role authority to debug the credential.

## Profile Drift

- Re-run connector status and profile inspection.
- Treat changed auth, order, instrument, or blocked-surface fields as `review_required`.
- Do not enable execution to work around drift.

## Rate Limits

- Prefer read-only sync and cached profile data over repeated polling.
- Report the rate-limit group, scope, and retry posture if present in the profile.
- For websocket-capable venues, prefer event streams for order/fill monitoring when the adapter supports it.

## Unsupported Instruments

- Check `get_broker_instrument_constraints`.
- If identity translation fails, request an instrument map or broker-specific identifier.
- Mark the workflow blocked rather than inventing broker symbols.

## Preview Or Broker Validation Fails

- Preserve the exact failed check and broker/profile reason.
- Send order-shape issues back to `portfolio-manager`.
- Send policy or approval readiness issues to `risk-manager`.
- Do not submit through execution-operator until checks and approval match.
