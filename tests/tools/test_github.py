"""Bundled ``gh_*`` tools: token capture, request shape, error mapping."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext

from lite_horse.tools import github as gh_mod
from lite_horse.tools.github import build_github_tools


def _ctx(name: str, args: dict[str, Any]) -> ToolContext[Any]:
    return ToolContext(
        context=RunContextWrapper(context=None).context,
        tool_name=name,
        tool_call_id=f"tc-{name}",
        tool_arguments=json.dumps(args),
    )


def _mock(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Force every ``httpx.AsyncClient`` in github.py through ``handler``."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(gh_mod.httpx, "AsyncClient", factory)


def _by_name(tools: list[Any], name: str) -> Any:
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not in bundle: {[t.name for t in tools]}")


async def _invoke(tool: Any, args: dict[str, Any]) -> Any:
    return json.loads(
        await tool.on_invoke_tool(_ctx(tool.name, args), json.dumps(args))
    )


@pytest.mark.asyncio
async def test_issue_list_sends_bearer_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("Authorization")
        return httpx.Response(200, json=[{"number": 1, "title": "x"}])

    _mock(monkeypatch, handler)
    tools = build_github_tools(token_provider=lambda: "ghp_test")
    out = await _invoke(
        _by_name(tools, "gh_issue_list"),
        {"repo": "octo/cat", "state": "open", "labels": "bug,help", "limit": 25},
    )

    assert out["success"] is True
    assert out["data"][0]["number"] == 1
    assert captured["auth"] == "Bearer ghp_test"
    assert "/repos/octo/cat/issues" in captured["url"]
    assert "state=open" in captured["url"]
    assert "labels=bug%2Chelp" in captured["url"]
    assert "per_page=25" in captured["url"]


@pytest.mark.asyncio
async def test_invalid_repo_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, lambda req: httpx.Response(500))
    tools = build_github_tools(token_provider=lambda: "tok")
    out = await _invoke(_by_name(tools, "gh_issue_list"), {"repo": "no-slash"})
    assert out["success"] is False
    assert out["status"] == 400


@pytest.mark.asyncio
async def test_issue_create_posts_json_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            201,
            json={"number": 7, "html_url": "https://x", "state": "open"},
        )

    _mock(monkeypatch, handler)
    tools = build_github_tools(token_provider=lambda: "tok")
    out = await _invoke(
        _by_name(tools, "gh_issue_create"),
        {
            "repo": "octo/cat",
            "title": "fix",
            "body": "details",
            "labels": ["bug", "p1"],
        },
    )
    assert out["success"] is True
    assert out["data"]["number"] == 7
    assert seen["body"] == {
        "title": "fix",
        "body": "details",
        "labels": ["bug", "p1"],
    }


@pytest.mark.asyncio
async def test_pr_view_propagates_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock(
        monkeypatch,
        lambda req: httpx.Response(404, json={"message": "Not Found"}),
    )
    tools = build_github_tools(token_provider=lambda: "tok")
    out = await _invoke(
        _by_name(tools, "gh_pr_view"), {"repo": "octo/cat", "number": 42}
    )
    assert out["success"] is False
    assert out["status"] == 404
    assert out["error"]["message"] == "Not Found"


@pytest.mark.asyncio
async def test_diff_view_uses_diff_accept_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["accept"] = req.headers.get("Accept")
        return httpx.Response(200, text="diff --git a b\n+x\n")

    _mock(monkeypatch, handler)
    tools = build_github_tools(token_provider=lambda: "tok")
    out = await _invoke(
        _by_name(tools, "gh_diff_view"),
        {"repo": "octo/cat", "number": 9},
    )
    assert out["success"] is True
    assert "diff --git" in out["data"]
    assert seen["accept"] == "application/vnd.github.v3.diff"


@pytest.mark.skipif(
    not __import__("os").environ.get("GITHUB_TEST_TOKEN"),
    reason="GITHUB_TEST_TOKEN not set; skipping real GH smoke test",
)
@pytest.mark.asyncio
async def test_real_github_smoke() -> None:
    """Phase 37 acceptance gate (4): hits live api.github.com with a PAT."""
    import os

    token = os.environ["GITHUB_TEST_TOKEN"]
    tools = build_github_tools(token_provider=lambda: token)
    issue_list = _by_name(tools, "gh_issue_list")
    out = await _invoke(
        issue_list, {"repo": "anthropics/claude-code", "limit": 5}
    )
    assert out["success"] is True
    assert isinstance(out["data"], list)


@pytest.mark.asyncio
async def test_token_provider_called_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.headers.get("Authorization", ""))
        return httpx.Response(200, json=[])

    _mock(monkeypatch, handler)

    tokens = iter(["t1", "t2"])
    tools = build_github_tools(token_provider=lambda: next(tokens))
    issue_list = _by_name(tools, "gh_issue_list")
    await _invoke(issue_list, {"repo": "a/b"})
    await _invoke(issue_list, {"repo": "a/b"})
    assert calls == ["Bearer t1", "Bearer t2"]
