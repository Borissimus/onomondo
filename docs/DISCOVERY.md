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

The mock adapter contract is explicitly provisional: inventory is an array of
objects with `eidValue`; submission uses `eidValue` and
`order.psmo=[{"listProfileInfo":{}}]`; response carries `resourceId`; outcome is
an array containing `listProfileInfoResult` with `finalResult` and `profileInfo`.
Unknown/missing fields fail safely. No claim of authenticated schema capture is
made. Live API use fails closed unless an operator explicitly sets
`ONOMONDO_SCHEMA_CONFIRMED=true` after checking this contract against their beta
docs. Adjust the isolated adapter and fixtures if the hosted shape differs.

## Hardware boundary

No USB UART was present at initial discovery. Do not claim firmware identity or
hardware success until sanitized evidence is recorded. Read-only AT discovery
is authorized. No +MSTK responses, APDUs or SGP.32 commands are sent by the agent.

Use host PPP for the first modem-carried MQTTS proof. The UART used by pppd must
be separate from the diagnostic AT port (or diagnostics must stop while PPP owns
it). No implicit route changes are made. Host Internet MQTT proves software only;
the hardware runbook requires verifying the broker route through PPP. The
MessageBus protocol permits a future modem-native MQTT adapter.
