"""Phase 36 cloud worker service — separate ECS task.

Drains the SQS queue produced by :mod:`lite_horse.scheduler`, runs each
message as a tenant-scoped agent turn, and posts the final output to
the per-job webhook URL. Autoscales on queue depth (Phase 38 IaC).
"""
