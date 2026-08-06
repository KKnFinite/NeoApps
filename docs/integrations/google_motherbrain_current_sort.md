# Google MotherBrain Current-Sort Preview

## Purpose

This integration accepts a complete current-sort snapshot from the locked RFD Google
MotherBrain workbook, normalizes it with NeoMotherBrain rules, resolves the exact RFD
Night Sort operation, and returns a machine-readable preview.

This first version is **preview-only**. It contains no Apply endpoint and cannot create,
update, delete, cancel, park, unpark, or otherwise change operational records.

## Safety Boundary

- The feature defaults to disabled.
- The route is authenticated with a dedicated integration token, not a browser session.
- The token is compared with `hmac.compare_digest` and is never returned or logged.
- The CSRF exemption is attached only to this resolved view function.
- The request must be JSON and must stay under the configured byte limit.
- Workbook, gateway, sort, timezone, schema version, and operation identity are validated.
- The service reads the exact unarchived operation for gateway/date/sort and never creates one.
- Preview parking validation uses transient assignments, never persisted assignments.
- The endpoint unconditionally rolls back the SQLAlchemy session before returning, including
  successful and exceptional responses.
- Responses use `Cache-Control: no-store`.

There is no Google Apps Script in this release. There are no automatic triggers and no
Neo-to-Google publishing.

## Endpoint

```text
POST /integrations/google-motherbrain/current-sort/preview
Content-Type: application/json
X-Neo-Integration-Token: <configured token>
```

## Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_MOTHERBRAIN_IMPORT_ENABLED` | `false` | Enables the endpoint when explicitly set true. |
| `GOOGLE_MOTHERBRAIN_IMPORT_TOKEN` | none | Required shared integration token. Never commit a real value. |
| `GOOGLE_MOTHERBRAIN_SPREADSHEET_ID` | `10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg` | Locked workbook ID. |
| `GOOGLE_MOTHERBRAIN_MAX_REQUEST_BYTES` | `524288` | Maximum accepted JSON request size. |

When disabled, the endpoint returns `404`. When enabled without a valid token, it returns a
generic `401` without exposing configuration details.

## Schema Version 1

The integration is locked to:

- Spreadsheet: `RFD-N-sim: Mother Brain`
- Gateway: `RFD`
- Sort: `night`
- Timezone: `America/Chicago`

The snapshot must contain inbound manual/ALP/order collections, outbound
manual/ALP/order/tail-swap collections, and a complete parking assignment collection.

### Complete Sample Request

```json
{
  "schema_version": 1,
  "spreadsheet_id": "10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg",
  "spreadsheet_title": "RFD-N-sim: Mother Brain",
  "gateway_code": "RFD",
  "sort_name": "night",
  "sort_date": "2026-08-05",
  "timezone": "America/Chicago",
  "submitted_at": "2026-08-05T06:30:00Z",
  "snapshot": {
    "inbound": {
      "manual_rows": [
        {
          "sheet_row": 4,
          "date": "2026-08-05",
          "flight_number": "UPS9998",
          "origin": "HERE",
          "tail_number": "N123UP",
          "parking": "A01",
          "status": "HERE",
          "time": ""
        }
      ],
      "alp_rows": [
        {
          "sheet_row": 15,
          "date": "2026-08-05",
          "flight_number": "UPS1487",
          "origin": "DTW",
          "tail_number": "N152UP",
          "parking": "B01",
          "status": "ARR",
          "time": "03:09 (A)"
        }
      ],
      "official_order": ["UPS1487"]
    },
    "outbound": {
      "manual_rows": [
        {
          "sheet_row": 4,
          "date": "2026-08-05",
          "flight_number": "UPS9329",
          "destination": "HOT",
          "tail_number": "N445UP",
          "parking": "A05",
          "status": "HOT",
          "time": ""
        }
      ],
      "alp_rows": [
        {
          "sheet_row": 15,
          "date": "2026-08-05",
          "flight_number": "UPS7831",
          "destination": "SDF",
          "tail_number": "N303UP",
          "parking": "E06",
          "status": "",
          "time": "06:15 (S)"
        }
      ],
      "official_order": ["UPS7831"],
      "tail_swaps": [
        {
          "sheet_row": 4,
          "flight_number": "UPS7831",
          "destination": "SDF",
          "new_tail": "N999UP",
          "scorpion_unlock": ""
        }
      ]
    },
    "parking": {
      "assignments": [
        {
          "tail_number": "N303UP",
          "position": "E06-b"
        }
      ]
    }
  }
}
```

The explicit tail-swap acknowledgments currently recognized as
`ready_to_finalize` are: `1`, `ACK`, `ACKNOWLEDGED`, `APPROVED`, `READY`, `UNLOCK`,
`UNLOCKED`, `TRUE`, `X`, `Y`, and `YES`. Any other value remains pending. Preview never
finalizes a swap.

## Success Response

```json
{
  "ok": true,
  "preview_only": true,
  "schema_version": 1,
  "fingerprint": "<sha256 of canonical snapshot JSON>",
  "operation": {
    "id": 123,
    "sort_date": "2026-08-05",
    "gateway_code": "RFD",
    "sort_name": "night"
  },
  "summary": {
    "inbound": {},
    "outbound": {},
    "tail_swaps": {},
    "parking": {}
  },
  "sections": {
    "inbound": {},
    "outbound": {},
    "tail_swaps": {},
    "parking": {}
  },
  "warnings": [],
  "errors": []
}
```

The preview includes matched/unmatched/missing/duplicate/invalid mission rows, proposed
tail/timing/status changes, standalone HERE/SPARE/HOT tail actions, tail-swap readiness,
complete-snapshot parking differences, and physical parking conflicts. Google `A01-b`
normalizes to position `A01`, lane 2; `A01` uses lane 1.

## Error Responses

Errors use a stable code and readable message:

```json
{
  "ok": false,
  "preview_only": true,
  "error": {
    "code": "invalid_payload",
    "message": "snapshot.inbound.alp_rows must be a list."
  }
}
```

Relevant codes include:

- `not_found` (`404`) when the feature is disabled
- `unauthorized` (`401`)
- `unsupported_media_type` (`415`)
- `payload_too_large` (`413`)
- `malformed_json` (`400`)
- `invalid_payload`, `invalid_spreadsheet`, `invalid_gateway`, `invalid_sort`,
  `invalid_sort_date`, `invalid_timezone`, `invalid_submitted_at` (`400`)
- `unsupported_schema_version` (`400`)
- `operation_not_found` (`404`)
- `operation_ambiguous` (`409`)
- `preview_failed` (`500`)

## Next Planned Step

After production preview validation, a separately reviewed atomic Apply design may use the
fingerprint for duplicate/no-op protection. This release intentionally contains no Apply
route or operational mutation behavior.
