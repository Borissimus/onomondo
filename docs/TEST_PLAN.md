# Test plan

## Unit tests

- Parse Onomondo `new`, `work`, `done`, `absent` payloads.
- Distinguish successful `done` from `procedureError`.
- Normalize `listProfileInfoResult` without reversing a conventional ICCID.
- Handle missing fields and beta API error payloads safely.
- Calculate polling delay and honor `Retry-After`.
- Enforce one profile-changing operation per EID.
- Verify idempotency-key replay and conflict behavior.
- Redact authorization, activation code, IMSI, ICCID and EID from logs.
- Parse AT `OK`, `ERROR`, timeout, multiline response and interleaved URCs.
- Reject expired and duplicate MQTT commands.

## Backend integration tests

Use a fake HTTP server, not the live Onomondo API, in CI:

1. Create profile refresh.
2. Fake server returns a `resourceId`.
3. Poll transitions `new -> work -> done/success`.
4. Database and MQTT event contain the normalized success.

Repeat for:

- `done/procedureError`;
- `absent`;
- `429` with `Retry-After`;
- transient `502` on GET;
- timeout after POST where submission outcome is unknown;
- malformed JSON and schema drift.

## MQTT tests

- TLS hostname/CA validation.
- Authentication required.
- Per-device ACL isolation using two test identities.
- QoS 1 duplicate delivery does not repeat device command.
- Last Will changes retained status to offline.
- Oversized and malformed payload rejection.

## Serial tests

Use a pseudo-terminal modem simulator for deterministic tests:

- command/response handling;
- URC interleaving;
- delayed response and timeout;
- serial disconnect/reconnect;
- sensitive identifier redaction;
- no raw remote AT command execution.

## Manual hardware test sequence

1. Insert the known Onomondo eUICC in A7670E.
2. Connect UART through FTDI with correct voltage and common ground.
3. Confirm `AT`, SIM ready and registration.
4. Start the device agent and confirm MQTTS online status.
5. Request diagnostics through the management API.
6. Confirm correlated MQTT result.
7. Synchronize eUICC inventory from Onomondo.
8. Request profile refresh.
9. Observe local operation and Onomondo status transitions.
10. Confirm `listProfileInfoResult.finalResult == successResult` and enabled
    Onomondo operational profile.
11. Confirm no key or full sensitive identifiers appear in logs.

## Phase 2 destructive-safety test

Use only a designated test eUICC and approved profile:

- download profile;
- verify installed profile before enable;
- collect pre-switch diagnostics;
- enable with rollback true;
- collect post-switch registration and data diagnostics;
- verify failure produces rollback/recovery evidence;
- never test delete or eIM configuration in the initial campaign.

