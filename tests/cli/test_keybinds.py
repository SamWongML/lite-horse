from __future__ import annotations

from prompt_toolkit.keys import Keys

from lite_horse.cli.repl.keybinds import make_prompt_keybindings


def test_make_prompt_keybindings_registers_expected_keys() -> None:
    kb = make_prompt_keybindings()
    keys_seen = [tuple(b.keys) for b in kb.bindings]
    assert (Keys.Escape, Keys.ControlM) in keys_seen or (Keys.Escape, Keys.Enter) in keys_seen
    assert any(Keys.ControlD in k for k in keys_seen)
    assert any(Keys.ControlL in k for k in keys_seen)
