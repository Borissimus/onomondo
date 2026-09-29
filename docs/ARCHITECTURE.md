# Architecture

## Trust boundaries

```text
Operator browser/CLI
        |
        | HTTPS + operator authentication
        v
VPS -------------------------------------------------------------+
| Management API                                                  |
| Policy service -- Database -- Audit log                          |
|        |                    ^                                    |
|        v                    |                                    |
| Onomondo client <---- Order worker                               |
|        |                                                         |
|        +---- HTTPS/Bearer ----> Onomondo REST API                |
|                                                                  |
| MQTT publisher/subscriber ----> Mosquitto :8883                  |
+---------------------------------------|--------------------------+
                                        | MQTTS + per-device auth
                                        v
                                Python device agent
                                        |
                                        | UART / AT
                                        v
                                  SIMCom A7670E
                                        |
                               LTE + proactive UICC BIP
                                        v
                              Onomondo eUICC / IPAe
                                        |
                                        | ESipa over TLS
                                        v
                                  Onomondo eIM
```

The device application connection and the IPAe service connection are logically
separate. Successful device MQTT does not prove that IPAe retrieved an order, and
successful SGP.32 execution does not prove the device application recovered.

## Backend modules

- `api`: HTTP routes, authentication and request validation.
- `domain`: state machines, policies and normalized result types.
- `onomondo`: isolated typed REST client. It must contain no business policy.
- `workers`: durable order polling and reconciliation.
- `mqtt`: command publication, results and device presence handling.
- `storage`: ORM models, repositories and migrations.
- `audit`: append-only security-relevant event recording.

## Device-agent modules

- `serial_transport`: port ownership, reading, framing and reconnection.
- `at_engine`: serialized commands, timeouts, final result parsing and URC routing.
- `modem`: A7670E-specific command adapter behind a generic interface.
- `diagnostics`: normalized observations without SGP.32 implementation.
- `mqtt`: TLS session, subscriptions, publications and reconnect/backoff.
- `commands`: allowlisted command dispatcher with TTL and deduplication.

## Concurrency rules

- Only one process owns a UART port.
- Only one in-flight AT command exists unless CMUX is deliberately implemented.
- URCs are dispatched separately from solicited command responses.
- At most one profile-changing operation may run per EID.
- Read-only refresh may be queued behind a profile-changing operation.
- Worker leasing must prevent two workers from polling/updating the same operation
  concurrently.

## Portability boundary

The MCU port should preserve the following interfaces:

- `ModemTransport.send_command()`
- `ModemDiagnostics.collect()`
- `MessageBus.publish()/subscribe()`
- `Clock.now()` and a persistent command-ID store

Python-specific HTTP, ORM and VPS code must not leak into device-agent domain
logic. The MCU never receives the Onomondo API key.

