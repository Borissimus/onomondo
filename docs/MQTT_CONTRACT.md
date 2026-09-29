# MQTT contract

## Transport

- MQTT 3.1.1 or 5.0 over TLS.
- Port 8883.
- QoS 1 for commands, results, events and operation status.
- QoS 0 or 1 for periodic telemetry depending on cost.
- Payload encoding is UTF-8 JSON.
- Maximum payload size must be bounded; 32 KiB is sufficient for MVP.

## Topics

```text
devices/{deviceId}/status
devices/{deviceId}/telemetry/modem
devices/{deviceId}/events/modem
devices/{deviceId}/commands
devices/{deviceId}/commands/{commandId}/result
devices/{deviceId}/esim/operations/{operationId}
```

`status` is retained. Commands and command results are not retained. The broker
must not use a wildcard ACL that lets one device access another device ID.

## Envelope

Every non-status message uses:

```json
{
  "schemaVersion": 1,
  "messageId": "uuid",
  "timestamp": "2026-09-23T12:00:00Z",
  "correlationId": "uuid",
  "type": "modem.diagnostics.request",
  "payload": {}
}
```

## Presence

Retained online message:

```json
{
  "schemaVersion": 1,
  "state": "online",
  "sessionId": "uuid",
  "timestamp": "2026-09-23T12:00:00Z"
}
```

Last Will uses the same topic with `state: offline`.

## Diagnostic command

```json
{
  "schemaVersion": 1,
  "messageId": "uuid",
  "timestamp": "2026-09-23T12:00:00Z",
  "correlationId": "uuid",
  "type": "modem.diagnostics.request",
  "payload": {
    "commandId": "uuid",
    "expiresAt": "2026-09-23T12:05:00Z",
    "includeSensitiveIdentifiers": false
  }
}
```

Allowed device commands in MVP are diagnostic only. Raw AT commands are not
accepted over MQTT.

## Diagnostic result

```json
{
  "schemaVersion": 1,
  "messageId": "uuid",
  "timestamp": "2026-09-23T12:00:04Z",
  "correlationId": "uuid",
  "type": "modem.diagnostics.result",
  "payload": {
    "commandId": "uuid",
    "status": "succeeded",
    "registration": "registered_roaming",
    "operator": {"mcc": "255", "mnc": "03", "name": "Kyivstar"},
    "packetAttached": true,
    "signal": {"csq": 18},
    "ipAddresses": ["redacted-or-address"],
    "errors": []
  }
}
```

## SGP.32 operation status notification

This is informational; the device does not execute the SGP.32 payload:

```json
{
  "schemaVersion": 1,
  "messageId": "uuid",
  "timestamp": "2026-09-23T12:01:00Z",
  "correlationId": "uuid",
  "type": "esim.operation.status",
  "payload": {
    "operationId": "uuid",
    "operationType": "list_profile_info",
    "state": "succeeded",
    "deviceActionRequired": false
  }
}
```

