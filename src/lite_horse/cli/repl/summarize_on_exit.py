"""Run the :class:`Summarizer` against the just-ended CLI session.

Invoked from the REPL loop when the user exits with Ctrl-D / ``/exit``.
Reads the session's messages from :class:`LocalSessionRepo`, runs the
side-agent with a 3-turn budget, and persists the resulting record via
:meth:`LocalSessionRepo.upsert_summary`. Best-effort: every failure is
swallowed so a bad model call can never block REPL exit.
"""
from __future__ import annotations

import logging

from lite_horse.agent.summarizer import Summarizer
from lite_horse.sessions.local import LocalSessionRepo

log = logging.getLogger(__name__)


async def summarize_on_exit(*, session_key: str, model: str) -> bool:  # noqa: PLR0911
    """Summarise one CLI session in place. Returns True if a row was written."""
    db = LocalSessionRepo()
    try:
        meta = db.get_session_meta(session_key)
    except Exception:
        log.debug("summarize_on_exit: session lookup failed", exc_info=True)
        return False
    if meta is None:
        return False
    try:
        messages = db.get_messages(session_key)
    except Exception:
        log.debug("summarize_on_exit: get_messages failed", exc_info=True)
        return False
    if not messages:
        return False
    summarizer = Summarizer(model=model)
    try:
        result = await summarizer.run(messages=messages)
    except Exception:
        log.debug("summarize_on_exit: side-agent raised", exc_info=True)
        return False
    if not result.summary.strip():
        return False
    try:
        db.upsert_summary(
            session_id=session_key,
            topic=result.topic or None,
            summary=result.summary,
            generator=model,
        )
    except Exception:
        log.debug("summarize_on_exit: upsert failed", exc_info=True)
        return False
    return True
