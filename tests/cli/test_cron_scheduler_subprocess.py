"""Subprocess test for ``litehorse cron scheduler`` SIGTERM shutdown.

Plan acceptance (Phase 29): ``litehorse cron scheduler`` + SIGTERM → clean
shutdown inside 3 s, the pid file is removed, and no jobs remain "running".
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_scheduler_shuts_down_on_sigterm(tmp_path: Path) -> None:
    home = tmp_path / "state"
    home.mkdir()
    env = {
        **os.environ,
        "LITEHORSE_HOME": str(home),
        "PYTHONUNBUFFERED": "1",
    }
    for key in list(env):
        if key.startswith("LITEHORSE_") and key != "LITEHORSE_HOME":
            env.pop(key, None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "lite_horse.cli.app", "cron", "scheduler"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        pid_file = home / "cron.pid"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pid_file.exists():
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                pytest.fail(
                    f"scheduler exited before pid file appeared: rc={proc.returncode}\n"
                    f"stderr: {stderr}"
                )
            time.sleep(0.05)
        assert pid_file.exists(), "scheduler never wrote cron.pid"

        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("scheduler did not exit within 3 s of SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)

    assert rc == 0, f"non-zero exit: rc={rc}"
    assert not pid_file.exists(), "cron.pid was not cleaned up on shutdown"
