# Security requirements

## Secrets

- Store the Onomondo key only on the VPS.
- For systemd, use a root-owned `EnvironmentFile` with mode `0600`.
- For containers, mount a secret file read-only; do not place secrets in the image
  or committed Compose file.
- Never expose secrets through API responses, metrics, exceptions or debug logs.
- Do not read or automatically import the repository's `api-keys.md` file.
- Support key rotation without database migration.

## Operator access

- All management routes require authentication except liveness/readiness.
- Passwords must be stored as Argon2id hashes, never plaintext.
- Prefer an identity-aware reverse proxy or OIDC for production.
- Profile-changing operations require an elevated role and explicit confirmation.
- CSRF protection is required if browser cookie authentication is added.

## MQTT

- Internet-facing MQTT must use TLS on port 8883.
- Anonymous access is forbidden.
- Each device has a unique credential or client certificate.
- Broker ACLs restrict a device to its own topic subtree.
- The backend and devices validate the broker hostname and CA.
- Commands contain `commandId`, `createdAt`, `expiresAt` and schema version.
- The device persists recently processed command IDs to resist replay after reboot.
- MQTT payloads cannot contain Onomondo credentials or arbitrary Onomondo JSON.

## SGP.32 safety policy

- Never equate Onomondo `done` with success; inspect `outcome`.
- Default profile enable to rollback enabled.
- Never disable or delete the only known enabled/connectivity profile.
- Never delete a profile until another profile has been tested successfully.
- Do not expose eIM configuration or SM-DP+ address changes in MVP.
- Require one active operation per EID and an audit event for every decision.
- Activation codes are one-time sensitive values and must be encrypted at rest or
  discarded immediately after successful submission.

## Network exposure

- Expose only HTTPS 443 and MQTTS 8883 publicly.
- Restrict SSH by firewall/VPN and key authentication.
- PostgreSQL, internal worker ports and metrics must not be public.
- Apply security updates and bounded log retention.
- Use outbound allowlisting where practical for Onomondo API/ESipa-independent VPS
  traffic, DNS, certificate renewal and required package mirrors.

## Threats explicitly addressed

- Extracted firmware cannot reveal the organization Onomondo key.
- One compromised device cannot access another device's MQTT topics.
- Replayed commands do not execute twice.
- An attacker cannot inject raw profile-management payloads through MQTT.
- Logs and support bundles do not leak bearer keys or activation codes.
- Ambiguous POST failures are not automatically duplicated.

