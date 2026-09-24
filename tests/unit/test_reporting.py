from __future__ import annotations

import io

import pytest

from mongomig.cli import reporting
from mongomig.cli.reporting import ConsoleReporter, _duration
from mongomig.output.console import Output


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(reporting.time, "monotonic", fake)
    return fake


def test_progress_is_throttled_and_has_rate_and_eta(clock: FakeClock) -> None:
    buf = io.StringIO()
    rep = ConsoleReporter(Output(stdout=buf))
    rep.progress("backfill users", 0, 1000)
    rep.progress("backfill users", 100, 1000)  # < 2s since start: silent
    clock.now += 2
    rep.progress("backfill users", 400, 1000)
    clock.now += 1
    rep.progress("backfill users", 1000, 1000)  # within interval: silent...
    rep.progress_done("backfill users")  # ...but the final line is printed
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    assert "400 / ~1,000 (40.0%)" in lines[0]
    assert "200 docs/s" in lines[0]
    assert "ETA 3.0s" in lines[0]
    assert "1,000 / ~1,000 (100.0%)" in lines[1]


def test_short_tasks_print_nothing(clock: FakeClock) -> None:
    buf = io.StringIO()
    rep = ConsoleReporter(Output(stdout=buf))
    rep.progress("t", 0, 10)
    rep.progress("t", 10, 10)
    rep.progress_done("t")
    assert buf.getvalue() == ""


def test_json_mode_is_silent(clock: FakeClock) -> None:
    buf = io.StringIO()
    rep = ConsoleReporter(Output(json_mode=True, stdout=buf))
    rep.log("hello")
    rep.progress("t", 0, None)
    clock.now += 5
    rep.progress("t", 5, None)
    assert buf.getvalue() == ""


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.25, "250ms"), (3.21, "3.2s"), (442, "07:22"), (3725, "1:02:05")],
)
def test_duration(seconds: float, text: str) -> None:
    assert _duration(seconds) == text
