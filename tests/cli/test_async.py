from lite_horse.cli._async import arun


def test_arun_runs_coroutine_synchronously() -> None:
    calls: list[int] = []

    @arun
    async def body(x: int) -> int:
        calls.append(x)
        return x * 2

    assert body(3) == 6
    assert calls == [3]


def test_arun_propagates_exceptions() -> None:
    @arun
    async def body() -> None:
        raise RuntimeError("boom")

    try:
        body()
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
