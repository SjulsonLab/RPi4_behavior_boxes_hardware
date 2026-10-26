"""STOP must reach cleanup/report delivery even while edge batches stay busy."""

from contextlib import suppress
import time

import pytest

from test_core import config, trace
from test_first_failure import extra_level_trace
from test_runtime import SyntheticSource, wait_count
from box_runtime.input.treadmill.acquisition import ControlResult
from box_runtime.input.treadmill.gpio_backend import InitialState
from box_runtime.input.treadmill.runtime import (
    LifecycleError,
    TreadmillRuntime,
    ZeroError,
)
from debug import treadmill_counter


class ContinuousSource(SyntheticSource):
    """Always-ready ordered batches; simulates a reader unable to catch up."""

    fail_first_batch = False

    def open(self, deadline_ns: int) -> InitialState:
        """Accept a monotonic-ns deadline; return AB=00 with zero sequences."""
        self.line_sequences = [0, 0]
        return InitialState(0)

    def read_batch(self, timeout_s: float):
        """Accept wait seconds; return 40 x4 transitions, never an empty list."""
        time.sleep(0.002)
        options = dict(
            start_ns=time.monotonic_ns() - 100_000,
            period_ns=1_000,
            global_baseline=self.index,
            line_baselines=tuple(self.line_sequences),
        )
        if self.fail_first_batch and self.index == 0:
            batch = extra_level_trace(40, 8, **options)
        else:
            batch = trace([1] * 40, **options)
        self.index += len(batch)
        for event in batch:
            self.line_sequences[0 if event.channel == "A" else 1] = (
                event.line_sequence_number
            )
        return batch


class ContinuousFailureSource(ContinuousSource):
    """Continuous input containing one new-sequence same-level event."""

    fail_first_batch = True


class FiniteBurstSource(SyntheticSource):
    """Five seconds of 10 kHz quadrature events, already queued for reading."""

    transition_count = 200_000

    def open(self, deadline_ns: int) -> InitialState:
        """Accept deadline ns; queue x4 events with one extra event after #400."""
        self.events = extra_level_trace(
            self.transition_count,
            400,
            start_ns=time.monotonic_ns() - 5_100_000_000,
            period_ns=25_000,
        )
        return InitialState(0)

    def read_batch(self, timeout_s: float):
        """Return up to 256 events; a 2 ms delay makes the backlog reproducible."""
        if self.index < len(self.events):
            time.sleep(0.002)
            batch = self.events[self.index : self.index + 256]
            self.index += len(batch)
            return batch
        return super().read_batch(timeout_s)


class SmallBacklogSource(FiniteBurstSource):
    """A finite backlog that fits comfortably within the shutdown budget."""

    transition_count = 4096


class HealthyBacklogSource(SmallBacklogSource):
    """Finite positive quadrature backlog for the unchanged ZERO boundary."""

    def open(self, deadline_ns: int) -> InitialState:
        """Accept deadline ns; queue 4096 valid x4 transitions, starting at AB=00."""
        self.events = trace(
            [1] * self.transition_count,
            start_ns=time.monotonic_ns() - 5_100_000_000,
            period_ns=25_000,
        )
        return InitialState(0)


@pytest.fixture
def make_runtime():
    """Yield a factory for spawned synthetic runtimes; release each after tests."""
    instances = []

    def create(source_factory, *, start=True, **overrides):
        """Return a runtime, optionally started; overrides use config's units."""
        settings = dict(
            processing_lag_warning_ns=30_000_000_000,
            diagnostic_ring_buffer_size=16,
            shutdown_timeout_s=1.2,
        )
        settings.update(overrides)
        runtime = TreadmillRuntime(config(**settings), source_factory=source_factory)
        instances.append(runtime)
        if start:
            runtime.start()
        return runtime

    yield create
    for runtime in instances:
        with suppress(LifecycleError):
            runtime.close()


@pytest.mark.parametrize("source_factory", [ContinuousSource, ContinuousFailureSource])
def test_busy_stop_returns_partial_counts_and_releases_without_forcing(
    make_runtime, source_factory, monkeypatch
):
    runtime = make_runtime(source_factory)
    wait_count(runtime, 80)
    terminations = []
    terminate = runtime._process.terminate

    def record_termination():
        """Record any forced termination, then perform it for safe test cleanup."""
        terminations.append(True)
        terminate()

    monkeypatch.setattr(runtime._process, "terminate", record_termination)
    started = time.monotonic()
    with pytest.raises(LifecycleError, match="partial"):
        runtime.close()
    assert time.monotonic() - started < 1.7
    assert not terminations
    assert runtime._final_worker_state.health == "STOPPED"
    assert runtime.health_report().failure_code == "SHUTDOWN_INCOMPLETE"
    assert runtime._shared is None and runtime._results is None
    diagnostic = runtime.first_failure_diagnostic
    if source_factory is ContinuousFailureSource:
        assert diagnostic.event_number == 9 and diagnostic.following_event_count == 4
        assert (
            "EDGE_STATE_INCONSISTENT" in runtime.health_report().observed_health_codes
        )
    else:
        assert diagnostic is None and runtime.snapshot().integrity_valid
    runtime.close()


def test_finite_200000_transition_burst_stops_during_backlog_with_context(make_runtime):
    runtime = make_runtime(FiniteBurstSource, shutdown_timeout_s=0.7)
    wait_count(runtime, 513)
    started = time.monotonic()
    with pytest.raises(LifecycleError, match="partial"):
        runtime.close()
    assert time.monotonic() - started < 1.2
    assert 513 <= runtime.snapshot().edge_event_count < 200_001
    assert runtime.first_failure_diagnostic.event_number == 401
    assert runtime._final_worker_state.health == "STOPPED"


def test_stop_drains_a_finite_backlog_completely(make_runtime):
    runtime = make_runtime(SmallBacklogSource)
    wait_count(runtime, 513)
    runtime.close()
    state = runtime.snapshot()
    assert state.edge_event_count == 4097 and state.valid_transition_count == 4096
    assert state.health == "STOPPED"
    assert runtime.first_failure_diagnostic.event_number == 401
    assert "SHUTDOWN_INCOMPLETE" not in runtime.health_report().observed_health_codes


def test_busy_stop_delivers_context_through_full_result_queue(make_runtime):
    runtime = make_runtime(ContinuousFailureSource)
    wait_count(runtime, 81)
    for number in range(8):
        runtime._results.put_nowait(
            ("ZERO", ControlResult(number, "applied", 0, 0), None)
        )
    with pytest.raises(LifecycleError, match="partial"):
        runtime.close()
    assert runtime.first_failure_diagnostic.event_number == 9


def test_lost_busy_stop_reply_cannot_turn_partial_counts_into_clean_stop(
    make_runtime, monkeypatch
):
    runtime = make_runtime(ContinuousFailureSource)
    wait_count(runtime, 81)

    def lose_reply(*args):
        """Accept control-wait arguments; simulate loss of the STOP result."""
        return None

    monkeypatch.setattr(runtime, "_wait_result", lose_reply)
    with pytest.raises(LifecycleError, match="partial"):
        runtime.close()
    assert runtime.first_failure_diagnostic is None
    assert runtime.health_report().failure_code == "SHUTDOWN_INCOMPLETE"


def test_stop_does_not_apply_pending_zero_on_busy_input(make_runtime):
    runtime = make_runtime(ContinuousSource)
    wait_count(runtime, 80)
    runtime._controls.put_nowait(("ZERO", 1, time.monotonic_ns() + 5_000_000_000))
    with pytest.raises(LifecycleError, match="partial"):
        runtime.close()
    state = runtime.snapshot()
    assert state.last_applied_zero_command_id is None
    assert state.zeroed_transition_position == state.raw_transition_position
    assert runtime._final_worker_state.health == "STOPPED"


def test_zero_on_busy_input_expires_without_application(make_runtime):
    runtime = make_runtime(ContinuousSource)
    wait_count(runtime, 80)
    with pytest.raises(ZeroError) as error:
        runtime.zero()
    assert error.value.result.status == "confirmed_not_applied"
    assert runtime.snapshot().last_applied_zero_command_id is None


def test_zero_still_waits_for_finite_drained_boundary(make_runtime):
    runtime = make_runtime(HealthyBacklogSource)
    result = runtime.zero()
    assert result.status == "applied"
    state = wait_count(runtime, 4096)
    assert state.zeroed_transition_position == 0
    runtime.close()


def test_counter_prints_failure_context_and_partial_count_warning(make_runtime):
    output = []
    instances = []

    def factory(settings):
        """Accept production config; return a spawned continuously failing source."""
        runtime = make_runtime(ContinuousFailureSource, start=False)
        instances.append(runtime)
        return runtime

    def wait(seconds):
        """Accept seconds; await an injected integrity failure before polling."""
        wait_count(instances[0], 81)

    result = treadmill_counter.main(
        ["--duration-s", "1"],
        runtime_factory=factory,
        wait_fn=wait,
        output_fn=output.append,
    )
    text = "\n".join(output)
    assert result == 1
    assert "First integrity failure: EDGE_STATE_INCONSISTENT" in text
    assert "partial" in text
    assert "forced termination" not in text
