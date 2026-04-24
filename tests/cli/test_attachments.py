"""Attachments: token extraction, file-drop detection, clipboard fallback."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.cli.repl import attachments as att_mod
from lite_horse.cli.repl.attachments import (
    Attachment,
    _detect_file_drop,
    detect_attachments,
    extract_tokens,
    format_attachments_for_turn,
    parse_attachment,
)


def test_extract_tokens_basic() -> None:
    toks = extract_tokens("explain @README.md please")
    assert [t.target for t in toks] == ["README.md"]


def test_extract_tokens_multiple() -> None:
    toks = extract_tokens("compare @a.md and @b.txt")
    assert [t.target for t in toks] == ["a.md", "b.txt"]


def test_extract_tokens_respects_escaped_spaces() -> None:
    toks = extract_tokens(r"@path\ with\ spaces.md extra")
    assert toks[0].target == "path with spaces.md"


def test_extract_tokens_http_url() -> None:
    toks = extract_tokens("see @https://example.com/x")
    assert toks[0].target == "https://example.com/x"


def test_detect_file_drop_resolves_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "demo.md"
    target.write_text("hi", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved = _detect_file_drop("~/demo.md")
    assert resolved is not None
    assert resolved.name == "demo.md"


def test_detect_file_drop_handles_file_uri(tmp_path: Path) -> None:
    target = tmp_path / "x.md"
    target.write_text("ok", encoding="utf-8")
    resolved = _detect_file_drop(f"file://{target}")
    assert resolved == target


def test_detect_file_drop_missing_returns_none(tmp_path: Path) -> None:
    assert _detect_file_drop(str(tmp_path / "nope.md")) is None


def test_parse_attachment_inlines_small_text(tmp_path: Path) -> None:
    p = tmp_path / "s.md"
    p.write_text("hello", encoding="utf-8")
    att = parse_attachment(str(p))
    assert att is not None
    assert att["kind"] == "file"
    assert att["content"] == "hello"


def test_parse_attachment_binary_uses_bytes(tmp_path: Path) -> None:
    p = tmp_path / "bin"
    p.write_bytes(b"\x00\x01\x02\x03")
    att = parse_attachment(str(p))
    assert att is not None
    assert att["kind"] == "file"
    assert "content" not in att
    assert "bytes_b64" in att


def test_parse_attachment_url() -> None:
    att = parse_attachment("https://example.com/foo")
    assert att == {"kind": "url", "url": "https://example.com/foo"}


def test_parse_attachment_unknown_returns_none() -> None:
    assert parse_attachment("not-a-real-path") is None


def test_detect_attachments_integration(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_text("n", encoding="utf-8")
    atts = detect_attachments(f"see @{p} and @https://x")
    kinds = [a["kind"] for a in atts]
    assert "file" in kinds
    assert "url" in kinds


def test_format_attachments_for_turn_file_inline() -> None:
    atts: list[Attachment] = [
        {"kind": "file", "path": "/tmp/a.md", "content": "hi"},
    ]
    block = format_attachments_for_turn(atts)
    assert "<attachment kind=\"file\" path=\"/tmp/a.md\">" in block
    assert "hi" in block


def test_format_attachments_for_turn_url() -> None:
    atts: list[Attachment] = [{"kind": "url", "url": "https://x"}]
    assert "url=\"https://x\"" in format_attachments_for_turn(atts)


def test_format_attachments_for_turn_empty() -> None:
    assert format_attachments_for_turn([]) == ""


@pytest.mark.asyncio
async def test_attach_handler_stages_and_prints(
    tmp_path: Path,
) -> None:
    from lite_horse.cli.repl.slash import SlashOutcome

    p = tmp_path / "file.md"
    p.write_text("contents", encoding="utf-8")

    class FakeState:
        pending_attachments: list[Attachment] = []
        messages: list[str] = []

        @staticmethod
        def print_line(msg: str) -> None:
            FakeState.messages.append(msg)

    outcome = await att_mod.attach_handler([str(p)], FakeState)
    assert outcome is SlashOutcome.CONTINUE
    assert len(FakeState.pending_attachments) == 1
    assert any("staged 1" in m for m in FakeState.messages)


@pytest.mark.asyncio
async def test_paste_image_handler_no_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from lite_horse.cli.repl.slash import SlashOutcome

    monkeypatch.setattr(att_mod, "get_clipboard_image", lambda: None)

    messages: list[str] = []

    class FakeState:
        pending_attachments: list[Attachment] = []
        print_line = staticmethod(messages.append)

    outcome = await att_mod.paste_image_handler([], FakeState)
    assert outcome is SlashOutcome.CONTINUE
    assert any("no image" in m for m in messages)


@pytest.mark.asyncio
async def test_paste_image_handler_with_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from lite_horse.cli.repl.slash import SlashOutcome

    fake_att: Attachment = {"kind": "image", "mime": "image/png", "bytes_b64": "AAAA"}
    monkeypatch.setattr(att_mod, "get_clipboard_image", lambda: fake_att)

    pending: list[Attachment] = []
    messages: list[str] = []

    class FakeState:
        pending_attachments = pending
        print_line = staticmethod(messages.append)

    outcome = await att_mod.paste_image_handler([], FakeState)
    assert outcome is SlashOutcome.CONTINUE
    assert pending == [fake_att]
    assert any("staged" in m for m in messages)
