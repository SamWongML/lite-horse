"""Phase 42 — ``memory_search`` tool wire-shape + recall round-trip.

The tool body is JSON-in / JSON-out: this exercise drives it through a
``ToolContext`` carrying a fake :class:`TenantContext`, asserts the
output shape, and confirms the tool delegates to the recall backend.
The integration-grade pgvector path is covered in
``tests/repositories/test_memory_chunk_repo.py`` (gated on docker).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from agents.tool_context import ToolContext

from lite_horse.agent.backends import (
    RecallBackend,
    Recalled,
    SourceKind,
    TenantContext,
)
from lite_horse.memory.search_tool import memory_search


class _StubRecallBackend(RecallBackend):
    def __init__(self, hits: list[Recalled] | None = None) -> None:
        self.hits = hits or []
        self.calls: list[tuple[str, int]] = []

    async def index(
        self, *, source_kind: SourceKind, source_id: str | None, content: str
    ) -> int:
        del source_kind, source_id, content
        return 0

    async def search(self, query: str, *, k: int = 5) -> list[Recalled]:
        self.calls.append((query, k))
        return list(self.hits[:k])

    async def delete(
        self, *, source_kind: SourceKind, source_id: str | None
    ) -> int:
        del source_kind, source_id
        return 0


def _make_ctx(recall: RecallBackend) -> ToolContext[TenantContext]:
    from tests.security.test_tool_tenant_isolation import (
        _InMemoryCronBackend,
        _InMemoryMemoryBackend,
        _InMemorySkillBackend,
    )

    tenant = TenantContext(
        user_id="u",
        agent_id="a",
        memory=_InMemoryMemoryBackend(),
        skill=_InMemorySkillBackend(),
        cron=_InMemoryCronBackend(),
        recall=recall,
    )
    return ToolContext(
        context=tenant,
        tool_name="memory_search",
        tool_call_id="tc-1",
        tool_arguments="{}",
    )


@pytest.mark.asyncio
async def test_memory_search_returns_results_in_wire_shape() -> None:
    backend = _StubRecallBackend(
        hits=[
            Recalled(
                source_kind="memory_md",
                source_id=None,
                content="I prefer pnpm",
                score=0.92,
                ts_iso="2026-05-07T00:00:00Z",
            )
        ]
    )
    ctx = _make_ctx(backend)
    raw = await memory_search.on_invoke_tool(  # type: ignore[attr-defined]
        ctx, json.dumps({"query": "package manager", "k": 3})
    )
    out: dict[str, Any] = json.loads(raw)
    assert out["success"] is True
    assert out["query"] == "package manager"
    assert len(out["results"]) == 1
    assert out["results"][0]["source_kind"] == "memory_md"
    assert out["results"][0]["score"] == 0.92
    assert "pnpm" in out["results"][0]["content"]
    assert backend.calls == [("package manager", 3)]


@pytest.mark.asyncio
async def test_memory_search_clamps_k() -> None:
    backend = _StubRecallBackend(hits=[])
    ctx = _make_ctx(backend)
    await memory_search.on_invoke_tool(  # type: ignore[attr-defined]
        ctx, json.dumps({"query": "x", "k": 999})
    )
    # k bounded to 20.
    assert backend.calls[-1][1] == 20


@pytest.mark.asyncio
async def test_memory_search_rejects_empty_query() -> None:
    backend = _StubRecallBackend(hits=[])
    ctx = _make_ctx(backend)
    raw = await memory_search.on_invoke_tool(  # type: ignore[attr-defined]
        ctx, json.dumps({"query": "  "})
    )
    out = json.loads(raw)
    assert out["success"] is False
    assert "query" in out["error"]


@pytest.mark.asyncio
async def test_memory_search_truncates_long_content() -> None:
    long = "x" * 1000
    backend = _StubRecallBackend(
        hits=[
            Recalled(
                source_kind="message",
                source_id="s1",
                content=long,
                score=0.5,
                ts_iso="",
            )
        ]
    )
    ctx = _make_ctx(backend)
    raw = await memory_search.on_invoke_tool(  # type: ignore[attr-defined]
        ctx, json.dumps({"query": "x"})
    )
    out = json.loads(raw)
    body = out["results"][0]["content"]
    # Tool truncates to ~480 chars + ellipsis.
    assert body.endswith("…")
    assert len(body) < len(long)
