# Local read-only validation against hosted Onomondo

This mode is intentionally separate from the default synthetic stack. It uses a
different Compose project name and therefore different PostgreSQL/MQTT volumes.
Only the API and worker receive the organization API key and outbound Internet
access. The device agent never receives the key. No destructive SGP.32 route is
implemented.

## 1. Stop the synthetic stack and host agent

Only one project can bind the loopback development ports. Stop the host hardware
agent with Ctrl-C in its terminal, then preserve and stop the mock stack:

```sh
.venv/bin/uv run python deploy/local/manage.py stop
```

## 2. Provision a dedicated runtime secret

Do not point the application at `api-keys.md`, put the token in command history,
or pass it as a command-line/environment value. Create a separate ignored file:

```sh
install -d -m 700 .local/sgp32-live
install -m 600 /dev/null .local/sgp32-live/onomondo_api_key
nano .local/sgp32-live/onomondo_api_key
```

Paste only the bearer token, as one line, without `Bearer`, quotes, Markdown or a
variable assignment. Save and verify permissions without printing the value:

```sh
test -s .local/sgp32-live/onomondo_api_key
stat -f '%Sp %N' .local/sgp32-live/onomondo_api_key
```

Expected mode on macOS is `-rw-------`.

## 3. Validate the merged Compose configuration

Docker Desktop's credential helper directory may need to be present in PATH:

```sh
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker compose \
  -f deploy/local/compose.yaml \
  -f deploy/local/compose.live.yaml \
  config --quiet
```

Never run `docker compose config` without `--quiet` in a secret-bearing deployment.

## 4. Start the isolated live project

```sh
docker compose \
  -f deploy/local/compose.yaml \
  -f deploy/local/compose.live.yaml \
  up -d --build --wait --wait-timeout 180
```

Confirm that `mock-onomondo` and `agent-sim` are absent:

```sh
docker compose \
  -f deploy/local/compose.yaml \
  -f deploy/local/compose.live.yaml \
  ps
```

The API remains at `https://localhost:8443`; it uses the generated development CA
and operator password from `.local/sgp32/`. The live database is separate because
the Compose project is named `sgp32-live`.

## 5. Synchronize real eUICC inventory

Use an interactive password prompt:

```sh
curl --cacert .local/sgp32/ca.crt --user admin \
  -X POST https://localhost:8443/api/v1/euiccs/sync

curl --cacert .local/sgp32/ca.crt --user admin \
  https://localhost:8443/api/v1/euiccs
```

The first response must report five real eUICCs. Stop if it does not.

## 6. Bind the bench device

Use the EID physically present in the modem. Never derive it from ICCID. Replace
the placeholder locally:

```sh
curl --cacert .local/sgp32/ca.crt --user admin \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"device-b","name":"Bench A7670E","eid":"<32-digit-EID>"}' \
  https://localhost:8443/api/v1/devices
```

Then restart the host hardware agent against the live project's MQTTS broker:

```sh
.venv/bin/uv run python deploy/local/manage.py agent \
  --port /dev/cu.usbserial-A50285BI
```

## 7. Submit the only supported live PSMO

The prototype exposes only `listProfileInfo`. Generate an idempotency key without
putting sensitive material in it:

```sh
operation_key=$(uuidgen)
curl --cacert .local/sgp32/ca.crt --user admin \
  -H "Idempotency-Key: $operation_key" \
  -X POST \
  https://localhost:8443/api/v1/euiccs/<32-digit-EID>/profiles/refresh
unset operation_key
```

Retain the returned local `operationId`, then poll the local service (not Onomondo
directly) every 10-15 seconds:

```sh
curl --cacert .local/sgp32/ca.crt --user admin \
  https://localhost:8443/api/v1/operations/<operationId>
```

Success requires local state `succeeded` and an upstream outcome containing
`listProfileInfoResult.finalResult=successResult`. A terminal `failed` with
`procedure_error` is not success.

## 8. Stop without deleting evidence

```sh
docker compose \
  -f deploy/local/compose.yaml \
  -f deploy/local/compose.live.yaml \
  stop
```

Do not use `down -v`; the live PostgreSQL volume contains the audit trail and
profile observations. Revoke/rotate the copied API key after the test if it was
created only for this validation.

