"""Bounded delivery ordering: preserve original events and reject unresolved gaps."""

from dataclasses import replace
from importlib import import_module

import pytest
from test_core import config, trace
from test_gpio import RawEvent, Request, source
from test_publication import START_NS, request_stop
from test_publication import run_worker as run_worker  # noqa: PLC0414 - pytest fixture re-export

from box_runtime.input.treadmill.acquisition import shutdown_deadlines
from box_runtime.input.treadmill.decoder import Decoder
from box_runtime.input.treadmill.gpio_backend import BackendError
from box_runtime.input.treadmill.state import TreadmillState, _pack_state, _unpack_state


@pytest.fixture
def ordering():
    """Return the pure ordering module; tests supply integer monotonic ns clocks."""
    return import_module("box_runtime.input.treadmill.ordering")


def decoded(events, baseline=0):
    """Return a pure decoder fed EdgeEvents with processing times later than kernel ns."""
    decoder = Decoder(config(), global_sequence_number=baseline)
    for event in events:
        decoder.process_event(event, event.timestamp_ns + 10_000_000)
    return decoder


def test_ordered_path_emits_immediately_and_retains_event_identity(ordering):
    """A normal batch needs neither a timer nor a retained event copy."""
    events = trace([1] * 12)
    buffer = ordering.EventOrderBuffer()
    result = buffer.push(events, 100)
    assert result == events
    assert all(a is b for a, b in zip(result, events))
    assert buffer.pending_count == 0 and buffer.deadline_ns is None
    assert buffer.delivery_reorder_count == 0


@pytest.mark.parametrize("batch_size", [1, 2, 4, 16])
def test_reorders_across_read_boundaries_with_exact_quadrature_counts(
    ordering, batch_size
):
    """Delivery swaps cannot change x4 position, direction or timestamp-based speed."""
    events = trace([1] * 16)
    received = list(events)
    received[3], received[4] = received[4], received[3]
    buffer = ordering.EventOrderBuffer()
    result = []
    for index in range(0, len(received), batch_size):
        result.extend(buffer.push(received[index : index + batch_size], 100 + index))
    assert result == events
    decoder = decoded(result)
    assert decoder.integrity_valid and decoder.position == 16
    assert decoder.counters["direction_reversal_count"] == 0
    assert buffer.delivery_reorder_count == 1


def test_nonzero_baseline_and_uint32_wrap(ordering):
    """Ordering uses serial distance rather than numeric sorting at rollover."""
    baseline = 2**32 - 2
    events = trace([1] * 4, global_baseline=baseline)
    buffer = ordering.EventOrderBuffer(baseline)
    assert buffer.push([events[1]], 0) == []
    assert buffer.push([events[0], *events[2:]], 10) == events
    assert buffer.pending_count == 0


def test_timeout_without_new_edges_preserves_every_received_record(ordering):
    """No new input is needed to resolve a missing-predecessor wait into a gap."""
    events = trace([1] * 4)
    buffer = ordering.EventOrderBuffer(max_wait_ns=100)
    assert buffer.push(events[1:], 10) == []
    assert buffer.deadline_ns == 110
    assert buffer.push([], 109) == []
    result = buffer.push([], 110)
    assert result == events[1:]
    assert not decoded(result).integrity_valid
    assert buffer.failed and buffer.pending_count == 0
    assert buffer.delivery_reorder_count == 0
    assert buffer.push([events[0]], 111) == [events[0]]


def test_new_arrivals_do_not_restart_the_gap_timer(ordering):
    """Continuous future events cannot keep an unresolved gap alive indefinitely."""
    events = trace([1] * 8)
    buffer = ordering.EventOrderBuffer(max_wait_ns=100)
    assert buffer.push([events[2]], 0) == []
    assert buffer.push([events[3]], 90) == []
    assert buffer.deadline_ns == 100
    result = buffer.push([events[4]], 100)
    assert result == events[2:5]
    assert buffer.failed


def test_capacity_failure_is_bounded_and_does_not_drop_the_overflow_record(ordering):
    """The record that exceeds capacity is still presented for failed-run diagnostics."""
    events = trace([1] * 6)
    buffer = ordering.EventOrderBuffer(max_pending=2)
    result = buffer.push(events[1:], 0)
    assert result == events[1:]
    assert buffer.failed and buffer.pending_count == 0


def test_flush_exposes_a_pending_gap_instead_of_claiming_a_clean_tail(ordering):
    """STOP/backend cleanup can hand all pending records to strict decoder checks."""
    events = trace([1] * 4)
    buffer = ordering.EventOrderBuffer()
    buffer.push([events[2], events[1]], 0)
    assert buffer.flush() == [events[1], events[2]]
    assert buffer.pending_count == 0 and buffer.failed
    assert buffer.flush() == []


def test_pending_duplicate_records_are_not_overwritten(ordering):
    """A same-sequence group retains both records for existing duplicate checks."""
    events = trace([1] * 4)
    buffer = ordering.EventOrderBuffer()
    assert buffer.push([events[1], events[1]], 0) == []
    result = buffer.push([events[0], *events[2:]], 10)
    assert result == [events[0], events[1], events[1], *events[2:]]
    decoder = decoded(result)
    assert decoder.integrity_valid and decoder.position == 4
    assert "SEQUENCE_DUPLICATE" in decoder.code_times


def test_conflicting_sequence_metadata_and_replayed_events_still_fail(ordering):
    """Ordering cannot hide per-line conflicts or an event older than committed state."""
    events = trace([1] * 8)
    conflicting = replace(
        events[1], line_sequence_number=events[1].line_sequence_number + 1
    )
    buffer = ordering.EventOrderBuffer()
    assert buffer.push([events[1], conflicting], 0) == []
    result = buffer.push([events[0], *events[2:]], 10)
    decoder = decoded(result)
    assert not decoder.integrity_valid
    assert "SEQUENCE_METADATA_INCONSISTENT" in decoder.code_times
    assert buffer.push([events[0]], 20) == [events[0]]


def test_per_line_failure_is_not_repaired_by_global_ordering(ordering):
    """A contiguous global stream with a line-sequence jump remains invalid."""
    events = trace([1] * 8)
    events[3] = replace(events[3], line_sequence_number=99)
    result = ordering.EventOrderBuffer().push(events, 0)
    assert not decoded(result).integrity_valid


def worker_events():
    """Return sixteen x4 events whose kernel ns all precede the fixture's clock."""
    return trace([1] * 16, start_ns=START_NS - 1_000_000, period_ns=25_000)


def add_missing_predecessor(context):
    """Append sequence 1 and the remaining burst after one intentionally empty read."""
    events = worker_events()
    context.source.events.extend([events[0], *events[2:]])


def request_zero(context):
    """Queue ZERO ID 1 with a 10 ms synthetic deadline; return None."""
    context.controls.put_nowait(("ZERO", 1, START_NS + 10_000_000))


def note_early_zero(context):
    """Record whether ZERO resolved before the missing predecessor was admitted."""
    context.early_zero = any(
        state.last_resolved_zero_command_id == 1 for _, state in context.successes
    )


def fail_read(context):
    """Raise the backend I/O error used to verify pending-record cleanup."""
    raise BackendError("BACKEND_IO_FAILED", "test read failure")


def test_worker_reorders_and_publishes_the_successful_reorder_count(run_worker):
    """The actual worker must admit normalized events and publish its correction count."""
    events = worker_events()
    events[2], events[3] = events[3], events[2]
    context = run_worker(events, batch_size=1)
    state = context.stop_report.state
    assert state.integrity_valid and state.raw_transition_position == 16
    assert state.edge_event_count == state.valid_transition_count == 16
    assert state.delivery_reorder_count == 1


def test_zero_waits_for_both_gpio_and_ordering_to_drain(run_worker):
    """An empty raw read while a predecessor is pending cannot establish ZERO."""
    context = run_worker(
        worker_events()[1:2],
        empty_step_ns=10_000,
        actions={1: request_zero, 2: add_missing_predecessor, 3: note_early_zero},
    )
    state = context.stop_report.state
    assert not context.early_zero
    assert state.last_applied_zero_command_id == 1
    assert state.integrity_valid and state.raw_transition_position == 16
    assert state.zeroed_transition_position == 0


def test_stop_waits_for_a_pending_predecessor_inside_the_existing_budget(run_worker):
    """STOP must not finish on the first empty raw read with buffered events."""
    context = run_worker(
        worker_events()[1:2],
        stop_after_ns=None,
        empty_step_ns=10_000,
        actions={1: request_stop, 2: add_missing_predecessor},
    )
    report = context.stop_report
    assert report.drain_complete and report.state.integrity_valid
    assert report.state.edge_event_count == report.state.raw_transition_position == 16


def test_stop_with_an_unresolved_gap_preserves_all_admitted_records(run_worker):
    """A drained raw queue is compatible with complete counts but invalid trajectory."""
    context = run_worker(
        worker_events()[1:4],
        batch_size=3,
        stop_after_ns=None,
        actions={1: request_stop},
    )
    report = context.stop_report
    assert report.drain_complete and not report.state.integrity_valid
    assert report.state.edge_event_count == 3
    assert "GLOBAL_SEQUENCE_GAP" in report.state.observed_acquisition_health_codes


def request_stop_at_ordering_deadline(context):
    """Queue a late STOP whose remaining drain budget equals the 1 ms grace."""
    drain_offset = shutdown_deadlines(0, 3.0)[0]
    context.controls.put_nowait(("STOP", 99, context.now_ns + 1_000_000 - drain_offset))


def test_stop_cutoff_is_not_clean_when_ordering_expires_at_the_same_instant(run_worker):
    """Hitting both limits must expose the gap and retain the partial-drain warning."""
    context = run_worker(
        worker_events()[1:2],
        stop_after_ns=None,
        actions={1: request_stop_at_ordering_deadline},
    )
    assert not context.stop_report.drain_complete
    assert not context.stop_report.state.integrity_valid
    assert context.stop_report.state.edge_event_count == 1


def test_backend_error_flushes_pending_records_before_final_publication(run_worker):
    """An I/O failure cannot silently discard records already admitted to the buffer."""
    context = run_worker(worker_events()[1:3], batch_size=2, actions={2: fail_read})
    state = context.shared.read()
    assert state.edge_event_count == 2 and not state.integrity_valid
    assert "BACKEND_IO_FAILED" in state.observed_acquisition_health_codes


def test_shared_state_roundtrip_includes_reorder_count():
    """The added dimensionless counter survives the existing packed-state transport."""
    state = replace(TreadmillState(), delivery_reorder_count=7)
    assert _unpack_state(_pack_state(state)) == state


def test_startup_can_exclude_a_reordered_burst_without_sorting_later_raw_reads():
    """Synchronization accepts reordered startup metadata; the raw reader stays raw."""
    events = trace([1] * 4)
    received = [events[1], events[0], *events[2:]]
    raw = [
        RawEvent(
            e.timestamp_ns,
            13 if e.channel == "A" else 16,
            1 if e.edge_type == "rising" else 2,
            e.global_sequence_number,
            e.line_sequence_number,
        )
        for e in received
    ]
    backend, request, _ = source(Request(raw))
    try:
        initial = backend.open(__import__("time").monotonic_ns() + 1_000_000_000)
        assert initial.global_sequence_number == initial.excluded_event_count == 4
        request.events = raw[:2]
        assert [e.global_sequence_number for e in backend.read_batch(0)] == [2, 1]
    finally:
        backend.close()
