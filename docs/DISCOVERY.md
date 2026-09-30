# Discovery record — 2026-09-29

Baseline `2bbe87873e1b031a01adc4c202f0c1be954c6e96` is on `main` and the
existing `feature/sgp32-prototype`. Initial working tree was clean. Python is
3.14.7; minimum supported version remains 3.12. Tool versions and final checks
are recorded in IMPLEMENTATION_REPORT.md. All eight normative documents and
HANDOFF_PROMPT.md were read completely. No additional document links exist in
them outside that set. `api-keys.md` has not been opened or imported.

## API boundary

Only the three baseline-confirmed endpoints are implemented:

* GET `/api/euicc`
* POST `/api/orders/psmo` with a single listProfileInfo order
* GET `/api/orders/psmo/{resourceId}`

Authenticated beta docs are unavailable at discovery. Public connectivity docs
at https://docs.onomondo.com/ describe a different API and are not substituted.
https://github.com/onomondo/onomondo-eim/blob/master/doc/rest_api.md confirms
status/outcome separation, but its paths differ from the hosted beta service.
It is supporting context, not confirmation of the hosted JSON wire schema.

The initial mock contract was provisional. Subsequent user-provided evidence from
a successful hosted `listProfileInfo` operation confirms that submission uses
`eidValue` and `order.psmo=[{"listProfileInfo":{}}]`, creation returns
`resourceId`/`lookup`, and a successful outcome contains
`listProfileInfoResult.finalResult=successResult` plus `profileInfoList`. The
adapter and fixtures use that exact field name. Unknown/missing fields still fail
safely. Live API use remains gated by `ONOMONDO_SCHEMA_CONFIRMED=true`; setting
the flag is an explicit operator acknowledgement for this read-only contract,
not authorization for destructive operations.

## Hardware boundary

No USB UART was present at initial discovery. Do not claim firmware identity or
hardware success until sanitized evidence is recorded. Read-only AT discovery
is authorized. No +MSTK responses, APDUs or SGP.32 commands are sent by the agent.

Use host PPP for the first modem-carried MQTTS proof. The UART used by pppd must
be separate from the diagnostic AT port (or diagnostics must stop while PPP owns
it). No implicit route changes are made. Host Internet MQTT proves software only;
the hardware runbook requires verifying the broker route through PPP. The
MessageBus protocol permits a future modem-native MQTT adapter.

## Follow-up: authorized live inventory and local Docker

After the initial implementation the user explicitly permitted reading the key
from `api-keys.md` for a test, with no undisclosed copying. The key was held only
in the host test process and sent via HTTPS Bearer to the configured Onomondo
origin. It was not persisted into another file, environment or Docker container.
The generic runtime Settings loader still rejects `api-keys.md`; this was an
explicit, one-off manual validation rather than automatic application import.

Confirmed live `GET /api/euicc`: HTTP 200, five eUICCs. The returned JSON shape is:

```json
{
  "data": [
    {
      "id": "string",
      "eid_value": "32-digit string",
      "counter_value": 0,
      "consumer_euicc": false,
      "association_token": "REDACTED",
      "sign_pub_key": "OMITTED",
      "sign_algo": "string",
      "created_at": "timestamp string",
      "updated_at": "timestamp string"
    }
  ],
  "pagination": {"has_more": false, "next_page": null}
}
```

The adapter now maps `eid_value` to its internal `eidValue` and stores only allowed
non-secret metadata. Association tokens and signing material are omitted. If a
response has more pages, inventory fails explicitly rather than silently losing
cards; the hosted pagination request contract remains unconfirmed.

The application's own public frontend links to `/api-docs`. That route returns
401 even with the working API Bearer key. Hosted order submission/outcome schema
therefore remains unconfirmed; **no live PSMO POST was sent**. The mock order
contract is not promoted to a verified hosted contract.

Docker Desktop was started successfully. Local deployment uses PostgreSQL 17,
TLS Mosquitto, API/worker, synthetic HTTPS Onomondo, device simulator and a
loopback-only TCP gateway. See `docs/LOCAL_DOCKER.md`; no production key is present
in that stack. The production default schema gate remains false.
