"""``@file`` / ``@url`` attachments + clipboard image capture.

``_detect_file_drop`` is a faithful port of the helper hermes uses so
dragged paths with escaped spaces, ``file://`` URIs, and tildes resolve
cleanly. The clipboard-image path shells out to the platform's native
tool (``osascript`` on macOS, ``wl-paste`` on Wayland, ``xclip`` on X11,
``powershell`` on Windows) instead of pulling Pillow into the runtime.

The public contract is a list of ``Attachment`` dicts pushed onto
``ReplState.pending_attachments``. The REPL loop flushes them into the
next user turn by prepending a short serialized description to
``user_text`` — the model then sees the file/url/image reference in
context. This keeps the attachment plumbing free of SDK-shape details.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import unquote, urlparse

MAX_INLINE_FILE_BYTES = 64 * 1024  # attachments larger than this are referenced, not inlined


class Attachment(TypedDict, total=False):
    kind: Literal["file", "url", "image", "text"]
    path: str
    url: str
    content: str
    bytes_b64: str
    mime: str


@dataclass(frozen=True)
class ParsedToken:
    """One ``@<path-or-url>`` token extracted from a user line."""

    raw: str
    target: str


# ``@`` followed by a run of non-whitespace chars — whitespace inside the
# target must be escaped with backslashes (matches hermes' behaviour).
_TOKEN_RE = re.compile(r"(?:^|\s)@((?:\\\s|\S)+)")


def extract_tokens(line: str) -> list[ParsedToken]:
    """Pull every ``@target`` token out of ``line``. Preserves order."""
    return [
        ParsedToken(raw=f"@{m.group(1)}", target=_unescape(m.group(1)))
        for m in _TOKEN_RE.finditer(line)
    ]


def _unescape(target: str) -> str:
    return target.replace("\\ ", " ").replace("\\\t", "\t")


def _detect_file_drop(target: str) -> Path | None:
    """Resolve ``target`` to a local file path if it looks like one.

    Handles:
      - ``file://`` URIs with percent-encoding
      - tildes (``~/foo.md``)
      - relative paths (resolved against the CWD)
      - escaped whitespace (already unescaped upstream)
    """
    candidate = target.strip()
    if not candidate:
        return None
    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        candidate = unquote(parsed.path)
    if candidate.startswith("~"):
        candidate = os.path.expanduser(candidate)
    path = Path(candidate)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return None
    if resolved.is_file():
        return resolved
    return None


def _looks_like_url(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def parse_attachment(target: str) -> Attachment | None:
    """Classify ``target`` as a file, a url, or ``None`` if ambiguous."""
    if _looks_like_url(target):
        return {"kind": "url", "url": target}
    path = _detect_file_drop(target)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) <= MAX_INLINE_FILE_BYTES and _is_probably_text(data):
        return {
            "kind": "file",
            "path": str(path),
            "content": data.decode("utf-8", errors="replace"),
        }
    return {
        "kind": "file",
        "path": str(path),
        "bytes_b64": base64.b64encode(data).decode("ascii"),
    }


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in data


def detect_attachments(line: str) -> list[Attachment]:
    """Return resolved attachments for every ``@token`` in ``line``."""
    out: list[Attachment] = []
    for tok in extract_tokens(line):
        att = parse_attachment(tok.target)
        if att is not None:
            out.append(att)
    return out


def get_clipboard_image() -> Attachment | None:
    """Capture an image off the system clipboard, if any.

    Returns a ``{"kind": "image", "bytes_b64": ..., "mime": "image/png"}``
    attachment on success, ``None`` otherwise. Always safe to call — unknown
    platforms or missing tools return ``None`` without raising.
    """
    raw: bytes | None = None
    if sys.platform == "darwin":
        raw = _capture_darwin()
    elif sys.platform.startswith("linux"):
        raw = _capture_linux()
    elif sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        raw = _capture_windows()
    if not raw:
        return None
    return {
        "kind": "image",
        "bytes_b64": base64.b64encode(raw).decode("ascii"),
        "mime": "image/png",
    }


def _capture_darwin() -> bytes | None:
    if shutil.which("osascript") is None:
        return None
    script = (
        'try\n'
        '  set theImage to (the clipboard as «class PNGf»)\n'
        '  set tmp to (POSIX path of (path to temporary items)) '
        '& "litehorse-clip-" & ((do shell script "date +%s")) & ".png"\n'
        '  set fh to open for access POSIX file tmp with write permission\n'
        '  write theImage to fh\n'
        '  close access fh\n'
        '  return tmp\n'
        'on error\n'
        '  return ""\n'
        'end try'
    )
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    tmp = completed.stdout.strip()
    if not tmp or not os.path.exists(tmp):
        return None
    try:
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _capture_linux() -> bytes | None:
    for tool, args in (
        ("wl-paste", ["--type", "image/png"]),
        ("xclip", ["-selection", "clipboard", "-t", "image/png", "-o"]),
    ):
        if shutil.which(tool) is None:
            continue
        try:
            completed = subprocess.run(
                [tool, *args],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0 and completed.stdout:
            return completed.stdout
    return None


def _capture_windows() -> bytes | None:  # pragma: no cover
    if shutil.which("powershell") is None:
        return None
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$img = [System.Windows.Forms.Clipboard]::GetImage();"
        "if ($img) {"
        "  $ms = New-Object System.IO.MemoryStream;"
        "  $img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png);"
        "  [Console]::OpenStandardOutput().Write($ms.ToArray(), 0, $ms.Length);"
        "}"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 and completed.stdout else None


def format_attachments_for_turn(attachments: list[Attachment]) -> str:
    """Serialize ``attachments`` as a prefix block for the next user message.

    Keeps the format lean: the model sees one ``<attachment>`` block per
    entry with kind + identifier + (for text files) the content. Binary
    attachments are referenced by path only — the agent can read them if
    needed via tools.
    """
    if not attachments:
        return ""
    blocks: list[str] = []
    for att in attachments:
        kind = att.get("kind", "")
        if kind == "url":
            blocks.append(f"<attachment kind=\"url\" url=\"{att.get('url', '')}\" />")
        elif kind == "file" and "content" in att:
            blocks.append(
                f"<attachment kind=\"file\" path=\"{att.get('path', '')}\">"
                f"\n{att['content']}\n</attachment>"
            )
        elif kind == "file":
            blocks.append(
                f"<attachment kind=\"file\" path=\"{att.get('path', '')}\" "
                f"bytes=\"binary\" />"
            )
        elif kind == "image":
            blocks.append(
                f"<attachment kind=\"image\" mime=\"{att.get('mime', '')}\" "
                f"bytes=\"{len(att.get('bytes_b64', ''))}b64chars\" />"
            )
        elif kind == "text":
            blocks.append(f"<attachment kind=\"text\">\n{att.get('content', '')}\n</attachment>")
    return "\n".join(blocks) + "\n"


async def attach_handler(args: list[str], state: Any) -> Any:
    """Slash handler for ``/attach <path|url> ...`` — stages each as a pending attachment."""
    from lite_horse.cli.repl.slash import SlashOutcome

    printer = getattr(state, "print_line", print)
    if not args:
        printer("[attach] usage: /attach <path-or-url> [...]")
        return SlashOutcome.CONTINUE
    staged = 0
    for target in args:
        att = parse_attachment(target)
        if att is None:
            printer(f"[attach] could not resolve: {target!r}")
            continue
        state.pending_attachments.append(att)
        staged += 1
    if staged:
        printer(f"[attach] staged {staged} attachment{'s' if staged != 1 else ''}")
    return SlashOutcome.CONTINUE


async def paste_image_handler(args: list[str], state: Any) -> Any:
    """Slash handler for ``/paste-image`` — grab an image off the clipboard."""
    from lite_horse.cli.repl.slash import SlashOutcome

    printer = getattr(state, "print_line", print)
    att = get_clipboard_image()
    if att is None:
        printer("[paste-image] no image on the clipboard (or tool unavailable)")
        return SlashOutcome.CONTINUE
    state.pending_attachments.append(att)
    printer("[paste-image] clipboard image staged for the next turn")
    return SlashOutcome.CONTINUE
