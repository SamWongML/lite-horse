"""Phase 39 per-tenant MCP connection pool — TTL + LRU + isolation."""
from __future__ import annotations

import pytest

from lite_horse.agent.mcp_pool import McpPool
from lite_horse.effective import EffectiveConfig, ResolvedMcpServer

pytestmark = pytest.mark.asyncio


class _FakeKms:
    """Stand-in for the storage Kms protocol."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def encrypt(self, *_args, **_kwargs):
        raise NotImplementedError

    async def decrypt(self, ciphertext: bytes, _ctx: dict[str, str]) -> bytes:
        self.calls.append(ciphertext)
        return ciphertext


class _FakeServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.connected = False
        self.cleaned = False

    async def connect(self) -> None:
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned = True


@pytest.fixture(autouse=True)
def _stub_server_factory(monkeypatch: pytest.MonkeyPatch) -> list[_FakeServer]:
    built: list[_FakeServer] = []

    def fake_factory(*, name: str, params: dict, cache_tools_list: bool):
        srv = _FakeServer(name)
        built.append(srv)
        return srv

    import lite_horse.agent.mcp_pool as mod

    monkeypatch.setattr(mod, "MCPServerStreamableHttp", fake_factory)
    return built


def _eff(*entries: ResolvedMcpServer) -> EffectiveConfig:
    return EffectiveConfig.build(
        skills=[],
        instructions=[],
        commands=[],
        mcp_servers=list(entries),
    )


def _entry(slug: str, *, url: str | None = None) -> ResolvedMcpServer:
    return ResolvedMcpServer(
        slug=slug,
        url=url or f"https://mcp.example/{slug}",
        scope="user",
        user_id="u1",
        cache_tools_list=True,
        auth_header=None,
        auth_value_ct=None,
    )


async def test_acquire_caches_per_user_and_url(_stub_server_factory: list[_FakeServer]) -> None:
    pool = McpPool(kms=_FakeKms())  # type: ignore[arg-type]
    eff = _eff(_entry("s1"))
    a = await pool.acquire(user_id="u1", eff=eff)
    b = await pool.acquire(user_id="u1", eff=eff)
    assert a == b  # same instance returned, no rebuild
    assert len(_stub_server_factory) == 1
    assert _stub_server_factory[0].connected is True
    await pool.shutdown()
    assert _stub_server_factory[0].cleaned is True


async def test_distinct_users_get_distinct_servers(
    _stub_server_factory: list[_FakeServer],
) -> None:
    pool = McpPool(kms=_FakeKms())  # type: ignore[arg-type]
    eff = _eff(_entry("s1"))
    s_a = await pool.acquire(user_id="u1", eff=eff)
    s_b = await pool.acquire(user_id="u2", eff=eff)
    assert s_a is not s_b
    assert len(pool) == 2


async def test_ttl_expiry_rebuilds(
    _stub_server_factory: list[_FakeServer],
) -> None:
    times = iter([0.0, 1000.0])

    def clock() -> float:
        return next(times)

    pool = McpPool(kms=_FakeKms(), ttl_seconds=10.0, clock=clock)  # type: ignore[arg-type]
    eff = _eff(_entry("s1"))
    first = await pool.acquire(user_id="u1", eff=eff)
    second = await pool.acquire(user_id="u1", eff=eff)
    assert first[0] is not second[0]
    assert _stub_server_factory[0].cleaned is True


async def test_max_entries_lru_evicts_oldest(
    _stub_server_factory: list[_FakeServer],
) -> None:
    pool = McpPool(kms=_FakeKms(), max_entries=2)  # type: ignore[arg-type]
    await pool.acquire(user_id="u1", eff=_eff(_entry("s1")))
    await pool.acquire(user_id="u2", eff=_eff(_entry("s2")))
    await pool.acquire(user_id="u3", eff=_eff(_entry("s3")))
    assert len(pool) == 2
    assert _stub_server_factory[0].cleaned is True
    assert _stub_server_factory[1].cleaned is False


async def test_invalidate_drops_user_entries(
    _stub_server_factory: list[_FakeServer],
) -> None:
    pool = McpPool(kms=_FakeKms())  # type: ignore[arg-type]
    await pool.acquire(user_id="u1", eff=_eff(_entry("s1")))
    await pool.acquire(user_id="u2", eff=_eff(_entry("s2")))
    dropped = await pool.invalidate(user_id="u1")
    assert dropped == 1
    assert len(pool) == 1
