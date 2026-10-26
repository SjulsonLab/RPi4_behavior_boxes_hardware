"""First-integrity-failure context, bounded shutdown transport and CLI output."""

from dataclasses import FrozenInstanceError, replace
from functools import partial
import multiprocessing
import time

import pytest

from test_core import config, trace
from test_runtime import SyntheticSource, wait_count
from box_runtime.input.treadmill.acquisition import ControlResult
from box_runtime.input.treadmill.decoder import Decoder, EdgeEvent
from box_runtime.input.treadmill.runtime import LifecycleError, TreadmillRuntime
from debug import treadmill_counter


def extra_level_trace(
    valid_count: int, after_valid: int, **trace_options
) -> list[EdgeEvent]:
    """Return a positive x4 trace with one extra same-level, new-sequence event.

    Args:
        valid_count: Number of valid dimensionless transitions in the result.
        after_valid: Insert after this many valid events, from zero to count.
        trace_options: Synthetic timestamp/baseline options accepted by trace().

    Returns:
        list[EdgeEvent]: Received-order events, with monotonic ns and uint32
        sequence numbers including the extra event. No GPIO is used.
    """
    events = trace([1] * valid_count, **trace_options)
    if after_valid:
        previous = events[after_valid - 1]
        extra = replace(previous, timestamp_ns=previous.timestamp_ns + 1)
    else:
        extra = EdgeEvent(events[0].timestamp_ns - 1, "A", "falling", 0, 0)
    events.insert(after_valid, extra)
    global_baseline = trace_options.get("global_baseline", 0)
    line_baselines = list(trace_options.get("line_baselines", (0, 0)))
    result = []
    for number, event in enumerate(events, 1):
        channel = 0 if event.channel == "A" else 1
        line_baselines[channel] = (line_baselines[channel] + 1) % 2**32
        result.append(
            replace(
                event,
                global_sequence_number=(global_baseline + number) % 2**32,
                line_sequence_number=line_baselines[channel],
            )
        )
    return result


def feed(decoder: Decoder, events: list[EdgeEvent]) -> None:
    """Process synthetic monotonic-ns events at 5 us lag; return None."""
    for event in events:
        decoder.process_event(event, event.timestamp_ns + 5_000)


@pytest.mark.parametrize("before_count", [0, 1, 8, 39, 40, 63, 64])
def test_first_failure_captures_exact_context_without_changing_counts(before_count):
    events = extra_level_trace(64, before_count)
    decoder = Decoder(config())
    feed(decoder, events)
    diagnostic = decoder.first_failure_diagnostic
    assert diagnostic is not None
    assert diagnostic.event_number == before_count + 1
    assert diagnostic.valid_transition_count_before == before_count
    assert diagnostic.raw_transition_position_before == before_count
    assert diagnostic.preceding_event_count == min(8, before_count)
    assert diagnostic.following_event_count == min(4, 64 - before_count)
    expected = events[max(0, before_count - 8) : min(len(events), before_count + 5)]
    assert [record.event for record in diagnostic.records] == expected
    offending = diagnostic.records[diagnostic.preceding_event_count]
    assert offending.flags == ("EDGE_STATE_INCONSISTENT",)
    assert offending.before == offending.after and offending.delta == 0
    assert offending.processing_lag_ns == 5_000
    assert diagnostic.global_sequence_before == before_count
    previous_lines = [0, 0]
    for event in events[:before_count]:
        previous_lines[0 if event.channel == "A" else 1] = event.line_sequence_number
    assert diagnostic.line_sequence_numbers_before == tuple(previous_lines)
    state = decoder.snapshot(events[-1].timestamp_ns + 5_000)
    assert state.edge_event_count == 65 and state.valid_transition_count == 64
    assert state.raw_transition_position == 64 and not state.integrity_valid
    assert state.event_inconsistency_count == 1
    assert state.global_sequence_gap_count == 0


def test_clean_trace_and_nonintegrity_warnings_do_not_capture():
    decoder = Decoder(config(processing_lag_warning_ns=1))
    events = trace([1] * 16)
    feed(decoder, events)
    assert decoder.first_failure_diagnostic is None
    decoder.process_event(events[-1], events[-1].timestamp_ns + 10_000)
    assert (
        decoder.first_failure_diagnostic is None
    )  # Sequence duplicate: degraded only.
    later = trace([1] * 17)[-1]
    decoder.process_event(replace(later, timestamp_ns=1), later.timestamp_ns + 5_000)
    assert (
        decoder.first_failure_diagnostic is None
    )  # Timestamp warning, no integrity loss.
    assert decoder.snapshot(later.timestamp_ns + 5_000).integrity_valid


@pytest.mark.parametrize("history_size", [1, 4, 8, 32])
def test_capture_survives_ring_wrap_and_remains_bounded(history_size):
    decoder = Decoder(config(diagnostic_ring_buffer_size=history_size))
    events = extra_level_trace(500, 40)
    feed(decoder, events)
    diagnostic = decoder.first_failure_diagnostic
    assert diagnostic.event_number == 41
    assert diagnostic.preceding_event_count == min(8, history_size)
    assert diagnostic.following_event_count == 4
    assert len(diagnostic.records) <= 13
    assert len(decoder.recent_events) == history_size
    assert diagnostic.records[diagnostic.preceding_event_count].event == events[40]
    with pytest.raises(FrozenInstanceError):
        diagnostic.event_number = 999


def test_later_failure_preserves_the_original_report():
    decoder = Decoder(config())
    events = extra_level_trace(20, 8)
    feed(decoder, events)
    original = decoder.first_failure_diagnostic
    previous = events[-1]
    decoder.process_event(
        replace(
            previous,
            timestamp_ns=previous.timestamp_ns + 1,
            global_sequence_number=previous.global_sequence_number + 1,
            line_sequence_number=previous.line_sequence_number + 1,
        ),
        previous.timestamp_ns + 5_000,
    )
    assert decoder.first_failure_diagnostic is original


def test_sequence_baselines_are_preserved_across_uint32_wrap():
    global_baseline = 2**32 - 3
    line_baselines = (2**32 - 2, 2**32 - 2)
    events = extra_level_trace(
        8, 4, global_baseline=global_baseline, line_baselines=line_baselines
    )
    decoder = Decoder(
        config(),
        global_sequence_number=global_baseline,
        line_sequence_numbers=line_baselines,
    )
    feed(decoder, events)
    diagnostic = decoder.first_failure_diagnostic
    assert diagnostic.global_sequence_before == 1
    assert diagnostic.line_sequence_numbers_before == (0, 0)
    assert (
        diagnostic.records[
            diagnostic.preceding_event_count
        ].event.global_sequence_number
        == 2
    )


def test_sequence_gap_captures_the_baseline_before_resynchronization():
    decoder = Decoder(config())
    events = trace([1] * 16)
    feed(decoder, events[:8])
    feed(decoder, events[10:])
    diagnostic = decoder.first_failure_diagnostic
    assert diagnostic.event_number == 9
    assert diagnostic.global_sequence_before == 8
    assert diagnostic.line_sequence_numbers_before == (4, 4)
    assert (
        diagnostic.valid_transition_count_before
        == diagnostic.raw_transition_position_before
        == 8
    )
    assert (
        "GLOBAL_SEQUENCE_GAP"
        in diagnostic.records[diagnostic.preceding_event_count].flags
    )


class ExtraEdgeSource(SyntheticSource):
    """A complete finite trace whose first bad event can leave the recent ring."""

    def open(self, deadline_ns: int):
        """Initialize 800 good transitions plus one bad event; return initial AB=00."""
        initial = super().open(deadline_ns)
        self.events = extra_level_trace(
            800, 40, start_ns=time.monotonic_ns() - 80_000_000, period_ns=50_000
        )
        return initial


class FinalExtraEdgeSource(ExtraEdgeSource):
    """An extra same-level event after a burst, with no following context."""

    def open(self, deadline_ns: int):
        """Initialize 800 good transitions then an extra event; return initial AB=00."""
        initial = super().open(deadline_ns)
        self.events = extra_level_trace(
            800, 800, start_ns=time.monotonic_ns() - 80_000_000, period_ns=50_000
        )
        return initial


class StalledAfterFailureSource(ExtraEdgeSource):
    """A source that signals when it stops servicing controls after its burst."""

    def __init__(self, settings, *, stalled):
        """Store validated config and a same-context multiprocessing.Event; return None."""
        super().__init__(settings)
        self.stalled = stalled

    def read_batch(self, timeout_s: float) -> list[EdgeEvent]:
        """Return the finite burst, then stall to exercise forced cleanup."""
        batch = super().read_batch(timeout_s)
        if not batch:
            self.stalled.set()
            time.sleep(30)
        return batch


@pytest.fixture
def failing_runtime():
    """Yield a spawned synthetic failure runtime and always close it afterward."""
    runtime = TreadmillRuntime(
        config(processing_lag_warning_ns=1_000_000_000, diagnostic_ring_buffer_size=16),
        source_factory=ExtraEdgeSource,
    )
    runtime.start()
    try:
        yield runtime
    finally:
        runtime.close()


def test_stop_result_preserves_report_after_ipc_release(failing_runtime):
    wait_count(failing_runtime, 801)
    assert failing_runtime.first_failure_diagnostic is None
    failing_runtime.close()
    diagnostic = failing_runtime.first_failure_diagnostic
    assert diagnostic.event_number == 41 and len(diagnostic.records) == 13
    assert failing_runtime._results is None and failing_runtime._shared is None
    failing_runtime.close()
    assert failing_runtime.first_failure_diagnostic is diagnostic


def test_normal_stop_delivery_drains_a_full_result_queue(failing_runtime):
    wait_count(failing_runtime, 801)
    for number in range(8):
        failing_runtime._results.put_nowait(
            ("ZERO", ControlResult(number, "applied", 0, 0), None)
        )
    failing_runtime.close()
    assert failing_runtime.first_failure_diagnostic.event_number == 41


def test_lost_stop_result_leaves_explicitly_unavailable_report(
    failing_runtime, monkeypatch
):
    wait_count(failing_runtime, 801)

    def lose_result(*args):
        """Simulate never receiving a STOP reply; return None."""
        return None

    monkeypatch.setattr(failing_runtime, "_wait_result", lose_result)
    started = time.monotonic()
    failing_runtime.close()
    assert time.monotonic() - started < 4
    assert failing_runtime.first_failure_diagnostic is None
    assert not failing_runtime.snapshot().integrity_valid


def test_clean_stop_has_no_failure_report():
    runtime = TreadmillRuntime(
        config(processing_lag_warning_ns=1_000_000_000), source_factory=SyntheticSource
    )
    runtime.start()
    try:
        wait_count(runtime)
    finally:
        runtime.close()
    assert runtime.first_failure_diagnostic is None


def test_forced_cleanup_without_delivery_keeps_report_unavailable():
    """Wait for the deliberate backend stall; verify bounded forced cleanup and no report."""
    stalled = multiprocessing.get_context("spawn").Event()
    runtime = TreadmillRuntime(
        config(processing_lag_warning_ns=1_000_000_000, shutdown_timeout_s=0.7),
        source_factory=partial(StalledAfterFailureSource, stalled=stalled),
    )
    runtime.start()
    try:
        # Coalescing can leave the final tail unpublished when this intentionally
        # broken backend blocks. Synchronize with the stall rather than that tail.
        assert stalled.wait(3)
        assert not runtime.snapshot().integrity_valid
        started = time.monotonic()
        with pytest.raises(LifecycleError):
            runtime.close()
        assert time.monotonic() - started < 1.5
        assert runtime.first_failure_diagnostic is None
        assert runtime.health_report().failure_code == "SHUTDOWN_INCOMPLETE"
    finally:
        runtime.close()


@pytest.mark.parametrize("interrupt", [False, True])
def test_counter_prints_context_only_after_close_and_uses_actual_pins(interrupt):
    runtimes = []
    output = []

    def factory(settings):
        """Use synthetic events and temporary bench pins; return a real runtime."""
        runtime = TreadmillRuntime(
            replace(
                settings,
                a_bcm_pin=23,
                b_bcm_pin=24,
                processing_lag_warning_ns=1_000_000_000,
            ),
            source_factory=FinalExtraEdgeSource,
        )
        runtimes.append(runtime)
        return runtime

    def wait(seconds):
        """Wait for the complete burst and optionally simulate operator Ctrl-C."""
        wait_count(runtimes[0], 801)
        if interrupt:
            raise KeyboardInterrupt

    def record(line):
        """Record output while ensuring context formatting is outside acquisition."""
        if line.startswith("First integrity failure:"):
            assert runtimes[0]._closed
        output.append(line)

    result = treadmill_counter.main(
        ["--duration-s", "1"], runtime_factory=factory, wait_fn=wait, output_fn=record
    )
    text = "\n".join(output)
    assert result == 1 and "BCM23/24" in text
    assert "First integrity failure: EDGE_STATE_INCONSISTENT" in text
    assert "Admitted event: 801" in text
    assert "Accepted transitions before failure: 800" in text
    assert "Signed raw position before failure: 800" in text
    assert "preceding 8/8; following 0/4" in text
    assert "decoder-remembered" in text and "reported target level" in text
    assert "Kernel timestamp (ns):" in text and "Processing timestamp (ns):" in text
    assert "Global sequence before:" in text and "A/B line sequences before:" in text
    assert "kernel_ns" in text and "dt_us" in text and "flags" in text


def test_counter_labels_missing_report_instead_of_claiming_no_failure():
    output = []

    def factory(settings):
        """Construct a failure runtime whose STOP reply is deliberately lost."""
        runtime = TreadmillRuntime(
            replace(settings, processing_lag_warning_ns=1_000_000_000),
            source_factory=FinalExtraEdgeSource,
        )
        runtime._wait_result = lose_result
        return runtime

    def lose_result(*args):
        """Simulate losing the control result without changing shared final state."""
        return None

    assert (
        treadmill_counter.main(
            ["--duration-s", "0.2"], runtime_factory=factory, output_fn=output.append
        )
        == 1
    )
    assert any(
        "First integrity failure context: unavailable" in line for line in output
    )
