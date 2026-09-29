# Management REST API contract

Prefix all endpoints with `/api/v1`. JSON timestamps use UTC RFC 3339. Every
response includes or echoes `X-Correlation-ID`.

## Health

- `GET /health/live`: process is alive; no authentication.
- `GET /health/ready`: dependencies required for safe operation are ready; no
  secret details in failure response.

## Authentication

The implementation may initially use HTTP Basic behind TLS or bearer sessions,
but it must isolate authentication so OIDC can replace it. All following endpoints
require authentication.

## eUICCs and devices

- `POST /euiccs/sync`: synchronize inventory from Onomondo.
- `GET /euiccs`: list known eUICCs with pagination.
- `GET /euiccs/{eid}`: eUICC, last observed profiles and active operations.
- `POST /devices`: bind a local device ID/name to a known EID.
- `GET /devices`: list devices and last observed connectivity state.
- `GET /devices/{device_id}`: one device with eUICC and modem summary.

## Profile operations

- `POST /euiccs/{eid}/profiles/refresh`
  - creates a local `listProfileInfo` operation;
  - returns HTTP 202 and `operationId`.
- `GET /euiccs/{eid}/profiles`
  - returns the most recently persisted profile inventory and observation time.
- Phase 2: `POST /euiccs/{eid}/profiles/download`
  - body contains activation code and confirmation metadata;
  - activation code must never be returned.
- Phase 2: `POST /euiccs/{eid}/profiles/{iccid}/enable`
  - body: `{ "rollback": true }`;
  - disabling rollback requires elevated authorization.

## Operations

- `GET /operations/{operation_id}`: normalized state plus safe raw outcome.
- `GET /operations?eid=&status=&type=`: filtered paginated history.
- `POST /operations/{operation_id}/reconcile`: privileged manual reconciliation
  for `submission_unknown` or unexpected timeout; it must not blindly resubmit.

## Device commands

- `POST /devices/{device_id}/commands/diagnostics`
  - publishes a diagnostic command;
  - returns HTTP 202 and `commandId`.
- `GET /device-commands/{command_id}`: status and normalized result.

## Common error format

```json
{
  "error": {
    "code": "operation_conflict",
    "message": "Another profile-changing operation is active for this eUICC",
    "correlationId": "uuid",
    "details": {}
  }
}
```

Do not return stack traces, upstream authorization headers, activation codes or
database connection details.

## Idempotency

Mutating operator calls accept `Idempotency-Key`. The backend stores the key,
actor, canonical request hash and result. Reuse with a different request returns
HTTP 409. This local mechanism does not imply the Onomondo beta API itself is
idempotent.

