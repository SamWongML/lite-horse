# Secret rotation runbook (v0.4)

How to rotate a Secrets Manager secret in lite-horse staging/prod
without service downtime. Tested under Phase 39 hardening.

## What rotates how

| Secret                       | Strategy                            | Recovery time |
|------------------------------|-------------------------------------|---------------|
| `litehorse/db`               | RDS-managed Lambda rotation         | 1× pool round-trip (`pool_pre_ping=True`) |
| `litehorse/redis`            | manual rotate-secret                | next request after rotation |
| `litehorse/openai-key`       | manual rotate-secret                | next turn that resolves a new client |
| `litehorse/anthropic-key`    | manual rotate-secret                | same as openai |
| `litehorse/webhook-secret`   | manual rotate-secret                | next worker delivery (5-min TTL cache in `cron/delivery.py`) |
| `litehorse/jwt-jwks-url`     | seldom; URL change only             | 1 h JWKS cache TTL |

The `SecretsProvider` (see `src/lite_horse/storage/secrets_aws.py`)
uses `aws-secretsmanager-caching` with a 5-minute TTL. Rotations
become visible to the service within one cache cycle — no restart
required.

## RDS password rotation

RDS-managed rotation is the lowest-effort path: AWS Secrets Manager
calls a Lambda that updates the master password and writes the new
value back to the secret atomically. Our SQLAlchemy pool has
`pool_pre_ping=True` (see `storage/db.py`) so dead connections are
detected and recycled; the next request opens a fresh one against
the rotated password.

```bash
# Trigger an immediate rotation (replace ENV with dev / staging / prod):
aws secretsmanager rotate-secret \
    --secret-id litehorse/db \
    --rotation-rules AutomaticallyAfterDays=30 \
    --region us-east-1
```

Verification:

```bash
# Tail api logs for db_session reconnect:
aws logs tail --follow /ecs/lite-horse-api --filter-pattern "pool_pre_ping"

# Open one new turn — it must succeed within 5s.
curl -sS -X POST https://staging.lite-horse.example/v1/turns \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d '{"session_key":"rotation-smoke","text":"ping"}'
```

## Manual rotation (BYO LLM keys, webhook secret, Redis password)

```bash
# Stage the new value:
aws secretsmanager put-secret-value \
    --secret-id litehorse/openai-key \
    --secret-string '{"OPENAI_API_KEY":"sk-...new..."}' \
    --region us-east-1

# Wait one cache TTL (5 min) or force a new ECS deploy:
aws ecs update-service \
    --cluster litehorse-staging \
    --service api --force-new-deployment
```

## Emergency revoke

If a key is compromised:

1. Set the secret to a placeholder string (`{"OPENAI_API_KEY":"REVOKED"}`).
2. Wait < 5 min for the cache to refresh — every new turn will fail
   fast with a 401 from the upstream provider, which we map to
   `INTERNAL` and return as 500.
3. Revoke the upstream key directly with the provider.
4. Set the secret to the new value.

## Failure modes & detection

| Symptom                         | Likely cause                | Action |
|---------------------------------|-----------------------------|--------|
| Bursts of `INTERNAL` from /v1/turns after rotate | provider key invalid     | re-stage secret |
| api task healthcheck failing    | DB password mismatch        | check Lambda rotation logs |
| Webhook deliveries returning 401 from webapp | `webhook-secret` desync between worker + webapp | rotate same value on both sides |

The CloudWatch alarm on EMF `errors_total` is the primary catch-all;
secondary signal is the ALB 5xx alarm.

## RLS verification (Phase 39 gate)

Run the leak suite against staging with a temporary read-only DB user:

```bash
LITEHORSE_DATABASE_URL="postgresql+asyncpg://readonly:...@staging-db" \
LITEHORSE_ENV=staging \
uv run pytest -q tests/security/ -m integration
```

Expected: green. A failure here is a security incident — page the
on-call engineer.
