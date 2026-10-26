"""Deterministic worker publication cadence, urgent updates and bounded retries."""

import multiprocessing
from queue import Queue
from types import SimpleNamespace

import pytest

from test_core import config, trace
from test_first_failure import extra_level_trace
from box_runtime.input.treadmill import acquisition
from box_runtime.input.treadmill.gpio_backend import BackendError, InitialState
from box_runtime.input.treadmill.state import SharedState


START_NS = 1_000_000_000
INTERVAL_NS = 1_000_000


class LocalChannel(Queue):
    """Bounded queue with the same cleanup methods as worker multiprocessing queues."""

    def close(self):
        """Return None; this in-process test queue owns no feeder resources."""

    def join_thread(self):
        """Return None; there is no feeder thread to join."""

    def cancel_join_thread(self):
        """Return None; there is no feeder thread to detach."""


class ScheduledSource:
    """Single-event or small-batch input driven entirely by a synthetic ns clock."""

    def __init__(
        self, context, events, batch_size, stop_after_ns, actions, empty_step_ns
    ):
        """Store test context, ordered events, sizes/ns, and read-number callbacks."""
        self.context = context
        self.events = events
        self.batch_size = batch_size
        self.stop_after_ns = stop_after_ns
        self.actions = actions
        self.empty_step_ns = empty_step_ns
        self.index = self.reads = 0
        self.waits = []
        self.closed = False
        self.stop_sent = False

    def open(self, deadline_ns):
        """Accept an absolute monotonic-ns deadline; return quiet startup AB=00."""
        return InitialState(0)

    def read_batch(self, timeout_s):
        """Advance synthetic ns and return a batch, executing optional test actions."""
        self.reads += 1
        assert self.reads < 10_000, "Worker did not finish its scripted lifetime"
        self.waits.append((self.context.now_ns, timeout_s))
        if self.index < len(self.events):
            batch = self.events[self.index : self.index + self.batch_size]
            self.index += len(batch)
            self.context.now_ns = max(self.context.now_ns, batch[-1].timestamp_ns)
        else:
            batch = []
            wait_ns = round(timeout_s * 1e9)
            if self.empty_step_ns is not None:
                wait_ns = min(wait_ns, self.empty_step_ns)
            self.context.now_ns += wait_ns
        if self.reads in self.actions:
            self.actions[self.reads](self.context)
        if (
            self.stop_after_ns is not None
            and self.context.now_ns >= START_NS + self.stop_after_ns
            and not self.stop_sent
        ):
            request_stop(self.context)
            self.stop_sent = True
        return batch

    def close(self):
        """Return None after recording that this synthetic source released ownership."""
        self.closed = True


def request_stop(context):
    """Queue STOP ID 99 with a 3 s synthetic deadline; return None."""
    context.controls.put_nowait(("STOP", 99, context.now_ns + 3_000_000_000))


@pytest.fixture
def run_worker(monkeypatch):
    """Yield an in-process real-worker harness with actual packed shared-state slots."""

    def run(
        events,
        *,
        batch_size=1,
        stop_after_ns=10_000_000,
        actions=None,
        empty_step_ns=None,
        diagnostics_full=False,
        **overrides,
    ):
        """Return recorded worker context; event/deadline times are monotonic ns."""
        settings = config(calibration_verified=not diagnostics_full, **overrides)
        context = SimpleNamespace(
            now_ns=START_NS,
            controls=LocalChannel(8),
            results=LocalChannel(8),
            diagnostics=LocalChannel(1 if diagnostics_full else 256),
            status=LocalChannel(4),
            shared=SharedState(multiprocessing.get_context("spawn")),
            attempts=[],
            successes=[],
            snapshots=[],
        )
        if diagnostics_full:
            context.diagnostics.put_nowait({"test": "already full"})
        context.source = ScheduledSource(
            context, events, batch_size, stop_after_ns, actions or {}, empty_step_ns
        )
        real_publish = context.shared.publish
        real_snapshot = acquisition.Decoder.snapshot

        def clock_ns():
            """Return the current deterministic integer monotonic time, ns."""
            return context.now_ns

        def publish(state, reservation=None):
            """Record scalar state/ns and delegate to the real packed-state transport."""
            context.attempts.append((context.now_ns, state))
            result = real_publish(state, reservation)
            if result:
                context.successes.append((context.now_ns, state))
            return result

        def snapshot(decoder, now_ns, **kwargs):
            """Record each full snapshot construction; return the real immutable state."""
            context.snapshots.append(now_ns)
            return real_snapshot(decoder, now_ns, **kwargs)

        def source_factory(settings):
            """Accept validated settings; return the scripted source for this run."""
            return context.source

        def ignore_signal(signum, handler):
            """Accept worker signal setup; leave the pytest runner's handler intact."""

        with monkeypatch.context() as patch:
            patch.setattr(acquisition.time, "monotonic_ns", clock_ns)
            patch.setattr(acquisition.signal, "signal", ignore_signal)
            patch.setattr(context.shared, "publish", publish)
            patch.setattr(acquisition.Decoder, "snapshot", snapshot)
            acquisition.acquisition_worker(
                settings,
                context.shared,
                context.controls,
                context.results,
                context.diagnostics,
                context.status,
                None,
                source_factory,
            )
        context.messages = list(context.results.queue)
        context.stop_report = next(
            (payload for kind, result, payload in context.messages if kind == "STOP"),
            None,
        )
        assert context.source.closed
        return context

    return run


def test_single_event_batches_coalesce_snapshots_without_losing_counts(run_worker):
    """Use the worker fixture to verify exact integer totals and bounded ns cadence."""
    context = run_worker(trace([1] * 120, start_ns=START_NS, period_ns=25_000))
    assert len(context.snapshots) <= 6
    publication_times = [
        now for now, state in context.successes if state.health != "STOPPED"
    ]
    assert all(
        b - a >= INTERVAL_NS for a, b in zip(publication_times, publication_times[1:])
    )
    final = context.stop_report.state
    assert final.edge_event_count == final.valid_transition_count == 120
    assert final.raw_transition_position == 120 and final.integrity_valid
    assert final.publication_skip_count == 0


def test_short_stop_bypasses_cadence_without_constructing_intermediate_snapshots(
    run_worker,
):
    """Use scripted STOP to verify immediate final counts before the 1 ms deadline."""
    context = run_worker(
        trace([1] * 4, start_ns=START_NS, period_ns=25_000),
        actions={4: request_stop},
    )
    assert context.snapshots == [START_NS, START_NS + 100_000]
    assert context.stop_report.state.edge_event_count == 4
    assert context.stop_report.drain_complete


def test_pending_tail_wakes_and_publishes_before_the_stationary_heartbeat(run_worker):
    """Use the worker fixture to verify seconds-valued waits and the last ns update."""
    context = run_worker(trace([1] * 3, start_ns=START_NS, period_ns=25_000))
    now, state = context.successes[1]
    assert now == START_NS + INTERVAL_NS
    assert state.edge_event_count == 3
    assert context.source.waits[3][1] == pytest.approx(0.000925)
    assert context.source.waits[4][1] == pytest.approx(0.049)


@pytest.mark.parametrize("integrity_failure", [False, True])
def test_new_health_condition_forces_one_early_publication(
    run_worker, integrity_failure
):
    """Use a Boolean failure mode to verify immediate health with exact event totals."""
    if integrity_failure:
        events = extra_level_trace(8, 1, start_ns=START_NS, period_ns=25_000)
        code = "EDGE_STATE_INCONSISTENT"
        overrides = {}
    else:
        events = trace([1] * 8, start_ns=START_NS - 10, period_ns=25_000)
        code = "PROCESSING_LAG_HIGH"
        overrides = {"processing_lag_warning_ns": 1}

    def add_processing_delay(context):
        """Advance processing ns by 10 to exercise a lag warning without integrity loss."""
        context.now_ns += 10

    actions = {len(events): request_stop}
    if not integrity_failure:
        actions[1] = add_processing_delay
    context = run_worker(events, actions=actions, **overrides)
    early = [
        (now, state)
        for now, state in context.successes
        if code in state.observed_acquisition_health_codes
    ]
    assert early[0][0] < START_NS + INTERVAL_NS
    assert len(context.snapshots) == 3
    assert context.stop_report.state.edge_event_count == len(events)
    if integrity_failure:
        assert context.stop_report.first_failure_diagnostic.event_number == 2
        assert context.stop_report.first_failure_diagnostic.following_event_count == 4


def test_zero_applies_at_drained_boundary_and_publishes_before_cadence(run_worker):
    """Use scripted ZERO to verify immediate coherent counts/origin and matched ID."""

    def request_zero(context):
        """Admit ZERO ID 1 with an absolute 10 ms synthetic deadline; return None."""
        context.controls.put_nowait(("ZERO", 1, START_NS + 10_000_000))

    context = run_worker(
        trace([1] * 4, start_ns=START_NS, period_ns=25_000),
        actions={2: request_zero, 6: request_stop},
        empty_step_ns=25_000,
    )
    zero_states = [
        (now, state)
        for now, state in context.successes
        if state.last_applied_zero_command_id == 1
    ]
    now, state = zero_states[0]
    assert now < START_NS + INTERVAL_NS
    assert state.edge_event_count == 4 and state.zeroed_transition_position == 0
    assert len(context.snapshots) == 3
    assert (
        context.messages[0][0] == "ZERO" and context.messages[0][1].status == "applied"
    )


def test_backend_error_forces_latest_state_and_status_before_cadence(run_worker):
    """Use an injected read error to verify pending integer totals and failure status."""

    def fail_read(context):
        """Raise a backend failure after one admitted event, before routine publication."""
        raise BackendError("BACKEND_IO_FAILED", "injected read failure")

    context = run_worker(
        trace([1] * 4, start_ns=START_NS, period_ns=25_000), actions={2: fail_read}
    )
    assert len(context.snapshots) == 2
    now, state = context.successes[-1]
    assert now < START_NS + INTERVAL_NS
    assert state.edge_event_count == 1 and state.failure_code == "BACKEND_IO_FAILED"
    assert list(context.status.queue)[-1][1] == "BACKEND_IO_FAILED"


def test_stationary_worker_keeps_heartbeat_without_one_khz_idle_wakes(run_worker):
    """Use empty input to verify the existing 50 ms heartbeat and bounded wake count."""
    context = run_worker([], stop_after_ns=130_000_000)
    live = [
        (now, state) for now, state in context.successes if state.health != "STOPPED"
    ]
    assert [now - START_NS for now, state in live] == [
        0,
        50_000_000,
        100_000_000,
        150_000_000,
    ]
    assert all(state.last_heartbeat_monotonic_ns == now for now, state in live)
    assert len(context.source.waits) == 3


def test_busy_slots_retry_on_cadence_and_publish_pending_skip_diagnostics(run_worker):
    """Hold real shared locks; verify 1 ms retry spacing, skip counts and versions."""

    def lock_slots(context):
        """Acquire both writer slots after startup; return None without any wait."""
        assert context.shared._locks[0].acquire(False)
        assert context.shared._locks[1].acquire(False)

    def unlock_slots(context):
        """Release both slots so pending totals and diagnosed skips can be published."""
        context.shared._locks[0].release()
        context.shared._locks[1].release()

    context = run_worker(
        trace([1] * 100, start_ns=START_NS, period_ns=25_000),
        actions={1: lock_slots, 102: unlock_slots},
    )
    attempts = [
        (now, state) for now, state in context.attempts if state.health != "STOPPED"
    ]
    assert [now - START_NS for now, state in attempts] == [
        0,
        1_000_000,
        2_000_000,
        3_000_000,
        4_000_000,
    ]
    recovered = context.successes[1][1]
    assert recovered.edge_event_count == 100 and recovered.publication_skip_count == 3
    assert "STATE_PUBLICATION_SKIPPED" in recovered.observed_acquisition_health_codes
    assert [state.state_version for now, state in context.successes] == [1, 2, 3]


def test_diagnostic_drop_after_draining_is_published_without_waiting_for_cadence(
    run_worker,
):
    """Fill the diagnostic queue; verify its new code/count are promptly published."""
    context = run_worker(
        trace([1], start_ns=START_NS, period_ns=25_000),
        empty_step_ns=25_000,
        actions={3: request_stop},
        diagnostics_full=True,
    )
    now, state = context.successes[1]
    assert now == START_NS + 50_000
    assert state.diagnostic_event_drop_count == 1
    assert "DIAGNOSTIC_EVENTS_DROPPED" in state.observed_acquisition_health_codes
