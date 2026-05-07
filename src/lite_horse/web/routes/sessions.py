"""``/v1/sessions/*`` — list, inspect, fork, compact, delete, search, export.

Per the v0.4 locked HTTP surface (plan §"Locked HTTP surface"). All
routes are JWT-gated via :func:`get_db_session` and tenant-scoped: the
underlying repos read ``app.user_id`` from the GUC and RLS backs the
filter up.

`:compact` reuses the v0.3 :class:`Consolidator` side-agent that
``BudgetHook`` invokes at the WARNING tier — same distillation, same
``MEMORY.md`` write path. It fails open: a consolidator error returns
``{entries_added: 0, skipped: 0}`` rather than 5xx.

`:export` writes a JSON dump (`{"session": ..., "messages": [...]}`) to
the exports bucket under ``exports/{user_id}/{session_id}-{ts}.json``
and returns a 15-minute presigned URL. The bucket is the only blob
surface the public API exposes — `attachments`/`evolve`/`audit` stay
internal.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.agent.consolidator import Consolidator
from lite_horse.repositories import (
    MemoryFull,
    MemoryRepo,
    MessageRepo,
    SessionRepo,
    UnsafeMemoryContent,
    UserSettingsRepo,
)
from lite_horse.storage.blob import BlobStore
from lite_horse.web.context import RequestContext
from lite_horse.web.deps import (
    get_blob_store_exports,
    get_db_session,
    get_request_context,
)
from lite_horse.web.errors import ErrorKind, http_error
from lite_horse.web.schemas import (
    SessionCompactOut,
    SessionExportOut,
    SessionForkIn,
    SessionForkOut,
    SessionMessageOut,
    SessionMessagesOut,
    SessionMetaOut,
    SessionSearchHitOut,
    SessionSearchOut,
)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Ctx = Annotated[RequestContext, Depends(get_request_context)]
BlobExports = Annotated[BlobStore, Depends(get_blob_store_exports)]

_DEFAULT_COMPACT_MODEL = "gpt-5.2"
_EXPORT_URL_TTL_SECONDS = 900


def _meta_to_out(row: dict[str, Any]) -> SessionMetaOut:
    return SessionMetaOut(
        id=str(row["id"]),
        source=str(row["source"]),
        model=row.get("model"),
        started_at=row.get("started_at"),
        ended_at=row.get("ended_at"),
        end_reason=row.get("end_reason"),
        message_count=int(row.get("message_count") or 0),
        tool_call_count=int(row.get("tool_call_count") or 0),
        input_tokens=int(row.get("input_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        title=row.get("title"),
    )


async def _resolve_key(repo: SessionRepo, key: str) -> str:
    """Treat the path segment as either a full id or a unique prefix."""
    meta = await repo.get_session_meta(key)
    if meta is not None:
        return key
    try:
        resolved = await repo.find_session_by_prefix(key)
    except ValueError as exc:
        raise http_error(ErrorKind.CONFLICT, str(exc)) from exc
    if resolved is None:
        raise http_error(ErrorKind.NOT_FOUND, f"session {key!r} not found")
    return resolved


@router.get("", response_model=list[SessionMetaOut])
async def list_sessions(
    session: DbSession,
    limit: int = 20,
    include_ended: bool = True,
) -> list[SessionMetaOut]:
    rows = await SessionRepo(session).list_recent_sessions(
        limit=limit, include_ended=include_ended
    )
    return [_meta_to_out(r) for r in rows]


@router.get("/search", response_model=SessionSearchOut)
async def search_messages(session: DbSession, q: str, limit: int = 20) -> SessionSearchOut:
    hits = await MessageRepo(session).search_messages(q, limit=limit)
    return SessionSearchOut(
        hits=[
            SessionSearchHitOut(
                id=h.id,
                session_id=h.session_id,
                role=h.role,
                timestamp=h.timestamp,
                snippet=h.snippet,
                source=h.source,
            )
            for h in hits
        ]
    )


@router.get("/{key}", response_model=SessionMetaOut)
async def get_session(key: str, session: DbSession) -> SessionMetaOut:
    repo = SessionRepo(session)
    resolved = await _resolve_key(repo, key)
    meta = await repo.get_session_meta(resolved)
    if meta is None:
        raise http_error(ErrorKind.NOT_FOUND, f"session {key!r} not found")
    return _meta_to_out(meta)


@router.get("/{key}/messages", response_model=SessionMessagesOut)
async def get_session_messages(
    key: str, session: DbSession, limit: int | None = None
) -> SessionMessagesOut:
    repo = SessionRepo(session)
    resolved = await _resolve_key(repo, key)
    rows = await MessageRepo(session).get_messages(resolved, limit=limit)
    return SessionMessagesOut(
        messages=[SessionMessageOut(**r) for r in rows]
    )


@router.post(
    "/{key}:fork",
    response_model=SessionForkOut,
    status_code=status.HTTP_201_CREATED,
)
async def fork_session(
    key: str, body: SessionForkIn, session: DbSession
) -> SessionForkOut:
    sess_repo = SessionRepo(session)
    msg_repo = MessageRepo(session)
    src = await _resolve_key(sess_repo, key)
    src_meta = await sess_repo.get_session_meta(src)
    if src_meta is None:
        raise http_error(ErrorKind.NOT_FOUND, f"session {key!r} not found")
    if body.new_key == src:
        raise http_error(ErrorKind.CONFLICT, "new_key must differ from source")
    existing = await sess_repo.get_session_meta(body.new_key)
    if existing is not None:
        raise http_error(
            ErrorKind.CONFLICT, f"session {body.new_key!r} already exists"
        )
    await sess_repo.create_session(
        session_id=body.new_key,
        source=str(src_meta["source"]),
        model=src_meta.get("model"),
    )
    copied = await msg_repo.copy_messages(src_session_id=src, dst_session_id=body.new_key)
    return SessionForkOut(src_key=src, new_key=body.new_key, copied=copied)


@router.post("/{key}:compact", response_model=SessionCompactOut)
async def compact_session(key: str, session: DbSession) -> SessionCompactOut:
    """Distill the session's trajectory into MEMORY.md.

    Mirrors the REPL ``/compact`` slash command: run :class:`Consolidator`
    over the message list and append each surviving entry to ``memory.md``.
    Failures (LLM error, oversize entry, injection content) are counted in
    ``skipped`` rather than raising.
    """
    sess_repo = SessionRepo(session)
    resolved = await _resolve_key(sess_repo, key)
    msg_repo = MessageRepo(session)
    messages = await msg_repo.get_messages(resolved)
    if not messages:
        return SessionCompactOut(entries_added=0, skipped=0)

    settings_repo = UserSettingsRepo(session)
    user_settings = await settings_repo.get()
    model = user_settings.default_model or _DEFAULT_COMPACT_MODEL
    consolidator = Consolidator(model=model)
    try:
        entries = await consolidator.run(turn_input=messages)
    except Exception:
        return SessionCompactOut(entries_added=0, skipped=1)

    if not entries:
        return SessionCompactOut(entries_added=0, skipped=0)

    mem_repo = MemoryRepo(session)
    current = await mem_repo.get("memory.md")
    added = 0
    skipped = 0
    body = current
    for entry in entries:
        sep = "" if (not body or body.endswith("\n")) else "\n"
        candidate = (body + sep + f"- {entry}\n").strip() + "\n"
        try:
            await mem_repo.put("memory.md", candidate)
        except (MemoryFull, UnsafeMemoryContent, ValueError):
            skipped += 1
            continue
        body = candidate
        added += 1
    return SessionCompactOut(entries_added=added, skipped=skipped)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(key: str, session: DbSession) -> None:
    sess_repo = SessionRepo(session)
    resolved = await _resolve_key(sess_repo, key)
    if not await sess_repo.delete_session(resolved):
        raise http_error(ErrorKind.NOT_FOUND, f"session {key!r} not found")


@router.get("/{key}/export", response_model=SessionExportOut)
async def export_session(
    key: str,
    session: DbSession,
    ctx: Ctx,
    blob: BlobExports,
) -> SessionExportOut:
    sess_repo = SessionRepo(session)
    msg_repo = MessageRepo(session)
    resolved = await _resolve_key(sess_repo, key)
    meta = await sess_repo.get_session_meta(resolved)
    if meta is None:
        raise http_error(ErrorKind.NOT_FOUND, f"session {key!r} not found")
    messages = await msg_repo.get_messages(resolved)
    document = {
        "session": meta,
        "messages": messages,
        "exported_at": int(time.time()),
    }
    payload = json.dumps(document, separators=(",", ":"), default=str).encode("utf-8")
    blob_key = f"exports/{ctx.user_id}/{resolved}-{int(time.time())}-{uuid4().hex[:8]}.json"
    await blob.put(blob_key, payload, content_type="application/json")
    url = await blob.presign_get(blob_key, expires=_EXPORT_URL_TTL_SECONDS)
    return SessionExportOut(url=url, expires_in=_EXPORT_URL_TTL_SECONDS)
