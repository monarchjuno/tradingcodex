# External Result Recording Examples

These are argument shapes for `record_external_data_result`, not calls to a
specific live provider. Replace every `COPY_...` value with the exact value
observed in the current task. In particular, never copy a compatibility hash,
route, tool name, returned provider, warning, or adjustment value from this
page. Omit `family_id` on the first attempt; retain the service-returned value
for later cards and residual handoffs.

## Complete validated rows

The following illustrates an explicitly consented screen-grade price result.
All rows used by the analysis are included, up to the 120-row cap.

```json
{
  "data_need": {
    "run_id": "COPY_CURRENT_WORKFLOW_RUN_ID",
    "data_kind": "equity_price",
    "asset_type": "equity",
    "identifiers": ["000660.KS"],
    "fields": ["timestamp", "symbol", "close"],
    "period_start": "2026-07-14T00:00:00Z",
    "period_end": "2026-07-16T00:00:00Z",
    "as_of": "2026-07-16T00:00:00Z",
    "frequency": "1d",
    "adjustment_policy": "unadjusted",
    "minimum_evidence_grade": "screen-grade",
    "owner_role": "technical-analyst",
    "source_policy": "best_available"
  },
  "source_tier": "openbb",
  "skipped_tier_attestations": [
    {
      "source_tier": "user_capability",
      "status": "unavailable",
      "reason": "COPY_EXACT_BOUNDED_REASON_NO_RELEVANT_CALLABLE_USER_CAPABILITY_EXISTED"
    }
  ],
  "transport": "openbb-mcp",
  "requested_provider": "yfinance",
  "returned_provider": "yfinance",
  "upstream_provider": "yfinance",
  "tool_name": "COPY_EXACT_OPENBB_TOOL_FQN",
  "route": "COPY_EXACT_COMPATIBLE_ROUTE",
  "returned_adjustment_policy": "unadjusted",
  "compatibility_receipt_hash": "COPY_CURRENT_64_HEX_COMPATIBILITY_RECEIPT_HASH",
  "result_status": "complete_valid",
  "evidence_grade": "screen-grade",
  "provider_query": {
    "provider": "yfinance",
    "symbol": "000660.KS",
    "start_date": "2026-07-14",
    "end_date": "2026-07-16",
    "interval": "1d",
    "adjustment": "unadjusted",
    "chart": false,
    "limit": 120
  },
  "source_locator": "provider:yfinance:COPY_EXACT_COMPATIBLE_ROUTE",
  "timezone": "UTC",
  "rows": [
    {"timestamp": "2026-07-14T00:00:00Z", "symbol": "000660.KS", "close": 1678000.0},
    {"timestamp": "2026-07-15T00:00:00Z", "symbol": "000660.KS", "close": 1900000.0},
    {"timestamp": "2026-07-16T00:00:00Z", "symbol": "000660.KS", "close": 1842000.0}
  ],
  "columns": [
    {"name": "timestamp", "type": "timestamp", "nullable": false},
    {"name": "symbol", "type": "string", "nullable": false},
    {"name": "close", "type": "float64", "nullable": false, "unit": "price", "currency": "KRW"}
  ],
  "symbols": ["000660.KS"],
  "redistribution": "not_specified",
  "warnings": ["unofficial provider; secondary-source consent and official cross-check required"]
}
```

The response must contain non-empty `snapshot_id`, `dataset_id`, and
`receipt_id`. Copy those IDs into the compact handoff. Do not claim success from
the provider response alone.

## Partial validated rows

Use the same complete shape, but declare only a residual that is proven by the
returned schema or coverage. For example, if `volume` was requested but absent:

```json
{
  "data_need": {
    "run_id": "COPY_CURRENT_WORKFLOW_RUN_ID",
    "data_kind": "equity_price",
    "asset_type": "equity",
    "identifiers": ["000660.KS"],
    "fields": ["timestamp", "symbol", "close", "volume"],
    "period_start": "2026-07-14T00:00:00Z",
    "period_end": "2026-07-16T00:00:00Z",
    "as_of": "2026-07-16T00:00:00Z",
    "frequency": "1d",
    "adjustment_policy": "unadjusted",
    "minimum_evidence_grade": "screen-grade",
    "owner_role": "technical-analyst",
    "source_policy": "best_available"
  },
  "source_tier": "openbb",
  "skipped_tier_attestations": [
    {
      "source_tier": "user_capability",
      "status": "unavailable",
      "reason": "COPY_EXACT_BOUNDED_REASON_NO_RELEVANT_CALLABLE_USER_CAPABILITY_EXISTED"
    }
  ],
  "transport": "openbb-mcp",
  "requested_provider": "COPY_REQUESTED_PROVIDER",
  "returned_provider": "COPY_IDENTICAL_RETURNED_PROVIDER",
  "upstream_provider": "COPY_IDENTICAL_RETURNED_PROVIDER",
  "tool_name": "COPY_EXACT_OPENBB_TOOL_FQN",
  "route": "COPY_EXACT_COMPATIBLE_ROUTE",
  "returned_adjustment_policy": "unadjusted",
  "compatibility_receipt_hash": "COPY_CURRENT_64_HEX_COMPATIBILITY_RECEIPT_HASH",
  "result_status": "partial_valid",
  "evidence_grade": "screen-grade",
  "provider_query": {
    "provider": "COPY_REQUESTED_PROVIDER",
    "symbol": "000660.KS",
    "start_date": "2026-07-14",
    "end_date": "2026-07-16",
    "interval": "1d",
    "adjustment": "unadjusted",
    "chart": false,
    "limit": 120
  },
  "missing_fields": ["volume"],
  "coverage_note": "Returned schema contained validated close rows but no volume column.",
  "timezone": "UTC",
  "rows": [
    {"timestamp": "2026-07-14T00:00:00Z", "symbol": "000660.KS", "close": 1678000.0},
    {"timestamp": "2026-07-15T00:00:00Z", "symbol": "000660.KS", "close": 1900000.0},
    {"timestamp": "2026-07-16T00:00:00Z", "symbol": "000660.KS", "close": 1842000.0}
  ],
  "columns": [
    {"name": "timestamp", "type": "timestamp", "nullable": false},
    {"name": "symbol", "type": "string", "nullable": false},
    {"name": "close", "type": "float64", "nullable": false, "unit": "price", "currency": "KRW"}
  ]
}
```

Promote this prefix once. Pass only `missing_fields: ["volume"]` to the next
source tier; do not fetch `close` again.

## Receipt-only failure

For an empty, auth, entitlement, timeout, rate-limit, unsafe, conflict, or other
rowless result, omit `rows` and `columns`. Keep returned-provider and returned-
adjustment attestations empty because no usable response was accepted.

```json
{
  "data_need": {
    "run_id": "COPY_CURRENT_WORKFLOW_RUN_ID",
    "data_kind": "equity_price",
    "asset_type": "equity",
    "identifiers": ["000660.KS"],
    "fields": ["timestamp", "symbol", "close"],
    "period_start": "2026-07-14T00:00:00Z",
    "period_end": "2026-07-16T00:00:00Z",
    "as_of": "2026-07-16T00:00:00Z",
    "frequency": "1d",
    "adjustment_policy": "unadjusted",
    "minimum_evidence_grade": "screen-grade",
    "owner_role": "technical-analyst",
    "source_policy": "best_available"
  },
  "source_tier": "openbb",
  "skipped_tier_attestations": [
    {
      "source_tier": "user_capability",
      "status": "unavailable",
      "reason": "COPY_EXACT_BOUNDED_REASON_NO_RELEVANT_CALLABLE_USER_CAPABILITY_EXISTED"
    }
  ],
  "transport": "openbb-mcp",
  "requested_provider": "COPY_REQUESTED_PROVIDER",
  "returned_provider": "",
  "upstream_provider": "COPY_REQUESTED_PROVIDER",
  "tool_name": "COPY_EXACT_OPENBB_TOOL_FQN",
  "route": "COPY_EXACT_COMPATIBLE_ROUTE",
  "returned_adjustment_policy": "",
  "compatibility_receipt_hash": "COPY_CURRENT_64_HEX_COMPATIBILITY_RECEIPT_HASH",
  "result_status": "terminal_gap",
  "fallback_reason": "Provider returned an authenticated empty result for the exact request.",
  "evidence_grade": "unusable",
  "provider_query": {
    "provider": "COPY_REQUESTED_PROVIDER",
    "symbol": "000660.KS",
    "start_date": "2026-07-14",
    "end_date": "2026-07-16",
    "interval": "1d",
    "adjustment": "unadjusted",
    "chart": false,
    "limit": 120
  }
}
```

The response must have a `receipt_id` and empty Snapshot/Dataset ids. Return the
typed failure to `tcx-source-gate`. For `correctable_error`, make at most one
new provider call only when the error names one concrete field correction; the
recorder query must change, and the second receipt must identify the first as
`corrects_receipt_id`. Never synthesize that link yourself.
