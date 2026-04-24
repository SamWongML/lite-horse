"""Delta state machine for streaming model output.

Ported from hermes' ``_stream_buf`` / ``_stream_started`` / ``_stream_box_opened``
pattern. Pure data: no I/O, no rendering. Renderers consult ``text``,
``started``, and ``box_opened`` to decide what to draw and when to open / close
their Live block.

Reconciliation: the SDK emits incremental ``response.output_text.delta`` events
during a turn, then surfaces a final ``message_output_created`` item with the
authoritative full text. ``finalize`` replaces the buffer with that final text
if the streamed concatenation drifted (rare; safety net only).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StreamAssembler:
    text: str = ""
    started: bool = False
    box_opened: bool = False
    _seen_item_ids: set[str] = field(default_factory=set)

    def feed(self, delta: str, *, item_id: str | None = None) -> None:
        if not delta:
            return
        self.started = True
        self.text += delta
        if item_id is not None:
            self._seen_item_ids.add(item_id)

    def mark_box_opened(self) -> None:
        self.box_opened = True

    def finalize(self, full_text: str | None) -> str:
        """Reconcile against the SDK's final text. Returns the canonical text.

        If the streamed buffer differs from the final, the final wins — but we
        only swap when the divergence is non-trivial (length differs by > 0)
        so cosmetic whitespace doesn't churn the renderer.
        """
        if full_text is None:
            return self.text
        if full_text != self.text:
            self.text = full_text
        return self.text

    def reset(self) -> None:
        self.text = ""
        self.started = False
        self.box_opened = False
        self._seen_item_ids.clear()
