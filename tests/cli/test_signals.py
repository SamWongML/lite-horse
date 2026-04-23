import signal

import pytest

from lite_horse.cli import _signals
from lite_horse.cli.exit_codes import ExitCode


def test_install_scripted_handlers_is_idempotent() -> None:
    _signals.install_scripted_handlers()
    first = signal.getsignal(signal.SIGTERM)
    _signals.install_scripted_handlers()
    second = signal.getsignal(signal.SIGTERM)
    assert first is second
    assert first is _signals._sigterm_handler


def test_scripted_signal_guard_translates_keyboard_interrupt() -> None:
    with pytest.raises(SystemExit) as exc:
        with _signals.scripted_signal_guard():
            raise KeyboardInterrupt
    assert exc.value.code == int(ExitCode.SIGINT)
