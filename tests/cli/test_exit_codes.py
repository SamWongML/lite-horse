from lite_horse.cli.exit_codes import ExitCode


def test_documented_values_are_stable() -> None:
    # These values are part of the CLI contract — changing them is a breaking
    # change for scripts that shell out to `litehorse`.
    assert int(ExitCode.OK) == 0
    assert int(ExitCode.GENERIC) == 1
    assert int(ExitCode.USAGE) == 2
    assert int(ExitCode.CONFIG) == 3
    assert int(ExitCode.AUTH) == 4
    assert int(ExitCode.NOT_FOUND) == 5
    assert int(ExitCode.CONFLICT) == 6
    assert int(ExitCode.IO) == 7
    assert int(ExitCode.SIGINT) == 130
    assert int(ExitCode.SIGTERM) == 143
