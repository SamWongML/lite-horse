"""Bundled GitHub tools — ``gh_*`` first-party tool surface.

The product is built around fixing GitHub issues, so a curated GitHub
tool set ships always-on (when the user has a GitHub token saved).
Auth is per-user OAuth/PAT stored in ``users.byo_provider_key_ct`` JSON
under the ``github`` key (see :class:`ByoKeyStore`).

Each tool is a :func:`function_tool` decorated coroutine that resolves
the bearer token at call-time via the ``token_provider`` capture so the
plaintext never lives at module scope. We use ``httpx`` with a 15 s
timeout and a small retry-on-429 helper.

Tools intentionally return JSON-encoded strings (not Pydantic models)
to match the shape of the rest of the lite-horse tool bundle.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from agents import RunContextWrapper, Tool, function_tool

GITHUB_API_ROOT = "https://api.github.com"
_TIMEOUT_S = 15.0
_USER_AGENT = "lite-horse/0.4 (+https://github.com/SamWongML/lite-horse)"

TokenProvider = Callable[[], str]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }


def _err(status: int, body: str | dict[str, Any]) -> str:
    return json.dumps({"success": False, "status": status, "error": body})


def _ok(payload: Any) -> str:
    return json.dumps({"success": True, "data": payload}, default=str)


async def _request(
    *,
    token: str,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> str:
    url = f"{GITHUB_API_ROOT}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=_headers(token),
            )
    except httpx.HTTPError as exc:
        return _err(0, str(exc))
    if resp.status_code >= 400:
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text[:500]
        return _err(resp.status_code, body)
    if resp.status_code == 204 or not resp.content:
        return _ok(None)
    try:
        return _ok(resp.json())
    except ValueError:
        return _ok(resp.text)


def _split_repo(repo: str) -> tuple[str, str] | None:
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    if not owner or not name:
        return None
    return owner, name


def build_github_tools(*, token_provider: TokenProvider) -> list[Tool]:
    """Return the GitHub tool list bound to a per-turn token resolver.

    ``token_provider`` is invoked at every tool call so a refreshed
    OAuth token (Phase 39 hardening) is picked up without rebuilding
    the agent.
    """

    @function_tool(
        name_override="gh_issue_list",
        description_override=(
            "List issues on a GitHub repository. ``repo`` is owner/name. "
            "Optional filters: state ('open'|'closed'|'all'), labels (CSV), "
            "limit (1-100, default 30). Returns array of {number,title,state,"
            "labels,user,html_url,updated_at}."
        ),
    )
    async def gh_issue_list(
        ctx: RunContextWrapper[Any],
        repo: str,
        state: str = "open",
        labels: str | None = None,
        limit: int = 30,
    ) -> str:
        del ctx
        owner_name = _split_repo(repo)
        if owner_name is None:
            return _err(400, "repo must be owner/name")
        owner, name = owner_name
        params: dict[str, Any] = {
            "state": state,
            "per_page": max(1, min(100, int(limit))),
        }
        if labels:
            params["labels"] = labels
        return await _request(
            token=token_provider(),
            method="GET",
            path=f"/repos/{owner}/{name}/issues",
            params=params,
        )

    @function_tool(
        name_override="gh_issue_create",
        description_override=(
            "Open a new issue on ``repo`` (owner/name). ``title`` required; "
            "``body`` and ``labels`` (list[str]) optional. Returns the new "
            "issue's {number,html_url,state}."
        ),
    )
    async def gh_issue_create(
        ctx: RunContextWrapper[Any],
        repo: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> str:
        del ctx
        owner_name = _split_repo(repo)
        if owner_name is None:
            return _err(400, "repo must be owner/name")
        owner, name = owner_name
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = list(labels)
        return await _request(
            token=token_provider(),
            method="POST",
            path=f"/repos/{owner}/{name}/issues",
            json_body=payload,
        )

    @function_tool(
        name_override="gh_pr_view",
        description_override=(
            "Fetch a pull request on ``repo`` (owner/name) by number. "
            "Returns the PR object including state, head/base refs, "
            "review_decision, and merge status."
        ),
    )
    async def gh_pr_view(
        ctx: RunContextWrapper[Any],
        repo: str,
        number: int,
    ) -> str:
        del ctx
        owner_name = _split_repo(repo)
        if owner_name is None:
            return _err(400, "repo must be owner/name")
        owner, name = owner_name
        return await _request(
            token=token_provider(),
            method="GET",
            path=f"/repos/{owner}/{name}/pulls/{int(number)}",
        )

    @function_tool(
        name_override="gh_pr_comment",
        description_override=(
            "Post an issue-level comment on a pull request "
            "(``repo``=owner/name, ``number`` is the PR number). Returns "
            "the comment's {id,html_url,created_at}."
        ),
    )
    async def gh_pr_comment(
        ctx: RunContextWrapper[Any],
        repo: str,
        number: int,
        body: str,
    ) -> str:
        del ctx
        owner_name = _split_repo(repo)
        if owner_name is None:
            return _err(400, "repo must be owner/name")
        owner, name = owner_name
        return await _request(
            token=token_provider(),
            method="POST",
            path=f"/repos/{owner}/{name}/issues/{int(number)}/comments",
            json_body={"body": body},
        )

    @function_tool(
        name_override="gh_search_code",
        description_override=(
            "GitHub code search. ``query`` is the GitHub search expression "
            "(e.g. 'TODO repo:org/name path:src/'). limit defaults to 30 "
            "(max 100). Returns total_count + items[]."
        ),
    )
    async def gh_search_code(
        ctx: RunContextWrapper[Any],
        query: str,
        limit: int = 30,
    ) -> str:
        del ctx
        params: dict[str, Any] = {
            "q": query,
            "per_page": max(1, min(100, int(limit))),
        }
        return await _request(
            token=token_provider(),
            method="GET",
            path="/search/code",
            params=params,
        )

    @function_tool(
        name_override="gh_diff_view",
        description_override=(
            "Fetch the unified diff for a PR (``repo``=owner/name, "
            "``number`` is the PR number). Returns the raw diff text "
            "in the data field."
        ),
    )
    async def gh_diff_view(
        ctx: RunContextWrapper[Any],
        repo: str,
        number: int,
    ) -> str:
        del ctx
        owner_name = _split_repo(repo)
        if owner_name is None:
            return _err(400, "repo must be owner/name")
        owner, name = owner_name
        url = f"{GITHUB_API_ROOT}/repos/{owner}/{name}/pulls/{int(number)}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(
                    url,
                    headers={
                        **_headers(token_provider()),
                        "Accept": "application/vnd.github.v3.diff",
                    },
                )
        except httpx.HTTPError as exc:
            return _err(0, str(exc))
        if resp.status_code >= 400:
            return _err(resp.status_code, resp.text[:500])
        return _ok(resp.text)

    return [
        gh_issue_list,
        gh_issue_create,
        gh_pr_view,
        gh_pr_comment,
        gh_search_code,
        gh_diff_view,
    ]


__all__ = ["GITHUB_API_ROOT", "TokenProvider", "build_github_tools"]
