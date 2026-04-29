# lite-horse v0.4 infra (AWS CDK / Python)

Provisions the prod (and dev/staging) environment for the lite-horse
cloud service: VPC + ECS cluster running the `api`, `scheduler` and
`worker` services, RDS Postgres Multi-AZ, ElastiCache Redis, four S3
buckets, an SQS queue, a customer-managed KMS key, Secrets Manager
secrets, and the CloudWatch dashboard + alarms set.

> The CDK app is intentionally minimal — one stack, one file. It is the
> code source of truth for what AWS resources back the service. Anything
> not declared here is not provisioned.

## Layout

| Path | Purpose |
|---|---|
| `app.py` | CDK app entry-point. Synthesises one `LiteHorseStack` per env (`dev`, `staging`, `prod`). |
| `lite_horse_stack.py` | Single-file stack: networking → data → compute → observability. |
| `cdk.json` | CDK runtime config. |
| `requirements.txt` | CDK Python deps (kept distinct from `pyproject.toml` so app code doesn't pull `aws-cdk-lib`). |

## Deploy

```bash
cd infra
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# AWS_PROFILE / AWS_REGION must be set
cdk diff   --context env=dev
cdk deploy --context env=dev
```

The CI deploy pipeline (`.github/workflows/deploy.yml`) runs
`cdk deploy --require-approval never` after the migration ECS task
completes successfully.

## Image

The same Docker image is used by all three ECS services; entrypoints
differ only by `command:`

| Service   | Command                                                                 |
|-----------|-------------------------------------------------------------------------|
| api       | `uvicorn lite_horse.web.app:create_app --factory --host 0.0.0.0 --port 8080` |
| scheduler | `python -m lite_horse.scheduler`                                         |
| worker    | `python -m lite_horse.worker`                                            |
| migrate   | `alembic -c src/lite_horse/alembic.ini upgrade head` (one-shot run task) |

## Secrets

| Name                          | Used by                       |
|-------------------------------|-------------------------------|
| `litehorse/db`                | api / scheduler / worker      |
| `litehorse/redis`             | api / scheduler / worker      |
| `litehorse/openai-key`        | api / worker                  |
| `litehorse/anthropic-key`     | api / worker                  |
| `litehorse/jwt-jwks-url`      | api                           |
| `litehorse/webhook-secret`    | worker (HMAC sign deliveries) |

KMS key alias: `alias/litehorse-{env}` — encrypts S3 buckets,
RDS storage, and per-user BYO provider keys.
