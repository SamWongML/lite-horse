# §8. Operational / deploy gaps

> Not in v0.5 scope (deferred). See [README.md](README.md).

These are things the CDK stack handles for itself but the application
doesn't yet exercise:

- **Idempotent task definition rollouts**: `deploy.yml` updates the ECS
  service with a new image tag. No blue/green or canary; a bad deploy
  takes the whole tenant base down. CodeDeploy ECS or AppMesh canary
  shapes would help.
- **Migration safety**: `alembic upgrade` runs in CI but not in the deploy
  pipeline. Adding a one-shot "migrate" ECS task that runs before the
  service rollout is standard.
- **Backup / restore drill**: RDS automated backups are on by default in
  CDK; no documented restore runbook. `SECRET_ROTATION.md` exists but
  no `RESTORE.md`.
- **Per-tenant data export**: the `LITEHORSE_S3_BUCKET_EXPORTS` bucket is
  provisioned, the endpoint isn't.
- **Load tests**: `tests/load/` directory exists but appears placeholder.
  No documented capacity model (turns/sec/task, p99 latency, cost/turn).
- **Cold-start of ECS Fargate**: turn p99 includes container cold-start
  the first time scale-out happens. Provisioned concurrency or always-on
  baseline tasks would smooth this. Hermes' Modal/Daytona backends are
  the equivalent for the in-process tool-execution sandbox.

