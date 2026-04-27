"""Phase 36 cloud scheduler service — separate ECS task.

A long-running process that wakes every 60 s, scans ``cron_jobs`` for
rows whose next fire boundary has elapsed, and enqueues a
:class:`~lite_horse.cron.scheduler.CronMessage` per recipient onto SQS.
The :mod:`lite_horse.worker` service drains the queue and runs the
turns. Importing this package boots the AsyncIOScheduler runtime, so it
must stay out of ``lite_horse.api`` (the embedded webapp surface).
"""
