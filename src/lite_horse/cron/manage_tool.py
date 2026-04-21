"""``cron_manage`` @function_tool — agent-driven CRUD over scheduled jobs.

Actions: ``add`` / ``list`` / ``remove`` / ``enable`` / ``disable``. Bounded
delivery platforms — ``log`` for local dev, ``webhook`` for embed. Writes go
through :class:`JobStore`; the cron scheduler picks them up on next boot or on
its own reload cycle. Tests target the pure :func:`dispatch` helper.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal
from urllib.parse import urlparse

from agents import RunContextWrapper, function_tool

from lite_horse.cron.jobs import JobStore
from lite_horse.cron.scheduler import parse_schedule

Action = Literal["add", "list", "remove", "enable", "disable"]
DeliveryPlatform = Literal["log", "webhook"]

_ALLOWED_PLATFORMS: tuple[str, ...] = ("log", "webhook")
_ALLOWED_URL_SCHEMES: tuple[str, ...] = ("http", "https")


def _build_delivery(
    platform: str | None, url: str | None
) -> dict[str, Any]:
    """Validate + assemble the delivery spec. Raises ``ValueError`` on bad input."""
    if platform is None:
        raise ValueError("delivery_platform is required for 'add'")
    if platform not in _ALLOWED_PLATFORMS:
        raise ValueError(
            f"unknown delivery_platform {platform!r}; "
            f"must be one of {_ALLOWED_PLATFORMS}"
        )
    if platform == "webhook":
        if not url:
            raise ValueError("delivery_url is required for platform='webhook'")
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
            raise ValueError(
                f"delivery_url must be http(s); got {url!r}"
            )
        return {"platform": "webhook", "url": url}
    return {"platform": "log"}


def dispatch(  # noqa: PLR0911 — branch-per-action; flat dispatch is the readable shape
    action: Action,
    *,
    schedule: str | None = None,
    prompt: str | None = None,
    delivery_platform: str | None = None,
    delivery_url: str | None = None,
    job_id: str | None = None,
    store: JobStore | None = None,
) -> dict[str, Any]:
    """Execute a cron-management action and return a JSON-serializable result."""
    s = store or JobStore()

    if action == "list":
        return {
            "success": True,
            "jobs": [asdict(j) for j in s.all()],
        }

    if action == "add":
        if not schedule:
            return {"success": False, "error": "schedule is required"}
        if not prompt:
            return {"success": False, "error": "prompt is required"}
        try:
            parse_schedule(schedule)
        except (ValueError, KeyError) as e:
            return {"success": False, "error": f"invalid schedule: {e}"}
        try:
            delivery = _build_delivery(delivery_platform, delivery_url)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        job = s.add(schedule=schedule, prompt=prompt, delivery=delivery)
        return {"success": True, "job": asdict(job)}

    if action == "remove":
        if not job_id:
            return {"success": False, "error": "job_id is required"}
        if not s.remove(job_id):
            return {"success": False, "error": f"no such job: {job_id}"}
        return {"success": True}

    if action in ("enable", "disable"):
        if not job_id:
            return {"success": False, "error": "job_id is required"}
        if not s.set_enabled(job_id, enabled=(action == "enable")):
            return {"success": False, "error": f"no such job: {job_id}"}
        return {"success": True}

    return {"success": False, "error": f"unknown action {action!r}"}


@function_tool(
    name_override="cron_manage",
    description_override=(
        "Manage scheduled cron jobs. Actions: 'add' (needs schedule + prompt + "
        "delivery_platform, plus delivery_url when platform='webhook'), 'list' "
        "(no args), 'remove' / 'enable' / 'disable' (need job_id). Schedule is "
        "a 5-field crontab or alias @minutely/@hourly/@daily/@weekly. Delivery "
        "platforms: 'log' (local dev) or 'webhook' (POST to webapp). Jobs "
        "persist to disk and fire on the cron process's next reload."
    ),
)
async def cron_manage(
    ctx: RunContextWrapper[Any],
    action: Action,
    schedule: str | None = None,
    prompt: str | None = None,
    delivery_platform: DeliveryPlatform | None = None,
    delivery_url: str | None = None,
    job_id: str | None = None,
) -> str:
    del ctx
    result = dispatch(
        action,
        schedule=schedule,
        prompt=prompt,
        delivery_platform=delivery_platform,
        delivery_url=delivery_url,
        job_id=job_id,
    )
    return json.dumps(result)
