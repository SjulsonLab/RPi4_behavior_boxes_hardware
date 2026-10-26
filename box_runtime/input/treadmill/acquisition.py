"""Spawn-safe acquisition worker; canonical decoding never waits on readers."""

from dataclasses import dataclass
from queue import Empty, Full
import signal
import time

from .decoder import Decoder, FirstFailureDiagnostic
from .gpio_backend import BackendError
from .state import TreadmillState, materialize_state


INCOMPLETE_DRAIN_MESSAGE = (
    "Acquisition STOP drain reached its deadline before an empty queue; "
    "final counts are partial"
)


@dataclass(frozen=True)
class ControlResult:
    """Matched zero/stop result: integer ID/version, monotonic ns, status/source."""

    command_id: int
    status: str
    worker_timestamp_ns: int
    state_version: int
    confirmation_source: str = "acknowledgement"


@dataclass(frozen=True)
class StopReport:
    """Final immutable state and optional bounded first-failure context.

    state retains the public mm/mm/s/ns/count contract. first_failure_diagnostic
    contains received-order records with monotonic ns and dimensionless counts.
    It is None when no event invalidated integrity. drain_complete is True only
    if a nonblocking read found the queue empty before the shutdown cutoff;
    False means final counts may omit pending events. This report travels once
    on the existing STOP result channel; individual edges never stream over IPC.
    """

    state: TreadmillState
    first_failure_diagnostic: FirstFailureDiagnostic | None
    drain_complete: bool


def shutdown_deadlines(deadline_ns: int, timeout_s: float) -> tuple[int, int, int]:
    """Return absolute monotonic-ns cutoffs for drain, STOP reply, and child exit.

    Args:
        deadline_ns: Integer end of the parent's complete shutdown budget, ns.
        timeout_s: Positive configured duration of that budget, seconds.

    Returns:
        tuple[int, int, int]: Drain, reply, exit deadlines in ascending order.
        Reserve up to 0.4 s each for GPIO/report delivery and forced cleanup,
        and up to 0.1 s for orderly joining. Scale reserves for short timeouts.
        These are scheduling budgets, not guarantees for a blocked backend.
    """
    cleanup_reserve_ns = int(min(0.4, timeout_s / 4) * 1e9)
    join_reserve_ns = int(min(0.1, timeout_s / 8) * 1e9)
    exit_deadline = deadline_ns - cleanup_reserve_ns
    reply_deadline = exit_deadline - join_reserve_ns
    return reply_deadline - cleanup_reserve_ns, reply_deadline, exit_deadline


def _offer(channel, message) -> bool:
    """Offer one bounded message without waiting; return False if channel is full."""
    try:
        channel.put_nowait(message)
        return True
    except Full:
        return False


def acquisition_worker(
    config, shared, controls, results, diagnostics, status, failure_ring, source_factory
) -> None:
    """Own source/decoder and publish bounded state until STOP or backend failure.

    Args:
        config: Frozen scalar settings in mm/cycle, seconds, Hz and event counts.
        shared: Double-slot single-writer SharedState from the spawn context.
        controls: Bounded queue of (ZERO|STOP, integer ID, monotonic deadline ns).
        results: Bounded matched-result queue; shared zero state is authoritative.
        diagnostics: Bounded low-rate record queue, reserved for the later logger.
        status: Bounded startup/backend-failure metadata channel to the parent.
        failure_ring: Capacity-one best-effort failure-history channel.
        source_factory: Importable callable(config) -> EdgeSource; child owns it.

    Returns:
        None. Final state/report follow source release. No normal
        disk, network, console, behavior or GUI work occurs in this worker.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    source = decoder = None
    version = 0
    heartbeat = time.monotonic_ns()
    ready = False
    sent_codes = set()
    pending_zero = None
    pending_stop = None
    last_result = None
    ring_sent = False
    stop_result_sent = False
    try:
        source = source_factory(config)
        initial = source.open(heartbeat + int(config.startup_timeout_s * 1e9))
        decoder = Decoder(
            config,
            initial.ab_state,
            initial.global_sequence_number,
            initial.line_sequence_numbers,
        )
        if "CALIBRATION_UNVERIFIED" in decoder.code_times:
            decoder.code_times["CALIBRATION_UNVERIFIED"] = heartbeat
        heartbeat = time.monotonic_ns()
        version += 1
        if not shared.publish(
            decoder.snapshot(heartbeat, heartbeat_ns=heartbeat, state_version=version)
        ):
            raise BackendError(
                "ACQUISITION_START_FAILED", "Initial state publication unavailable"
            )
        _offer(
            status,
            ("ready", getattr(source, "mapping", {}), initial.excluded_event_count),
        )
        ready = True
        while True:
            now = time.monotonic_ns()
            wait_s = max(
                0, (heartbeat + int(config.heartbeat_interval_s * 1e9) - now) / 1e9
            )
            if pending_zero is not None:
                wait_s = min(wait_s, max(0, (pending_zero[1] - now) / 1e9))
            # A STOP drain never waits for new edges or starts a read after its
            # cutoff. Finish each admitted batch before servicing controls.
            drain_expired = pending_stop is not None and now >= pending_stop[1]
            if drain_expired:
                batch = []
            else:
                batch = source.read_batch(0 if pending_stop is not None else wait_s)
            if batch:
                for event in batch:
                    decoder.process_event(event, time.monotonic_ns())
            now = time.monotonic_ns()
            heartbeat_due = now - heartbeat >= config.heartbeat_interval_s * 1e9
            if heartbeat_due:
                heartbeat = now
            if batch or heartbeat_due:
                version = _publish(decoder, shared, now, heartbeat, version)
            # STOP is urgent even with backlog. ZERO is only admitted here;
            # applying its offset still requires an actually drained boundary.
            for _ in range(8):
                try:
                    kind, identifier, deadline = controls.get_nowait()
                except Empty:
                    break
                if kind == "STOP":
                    drain_deadline, reply_deadline, _ = shutdown_deadlines(
                        deadline, config.shutdown_timeout_s
                    )
                    pending_stop = (identifier, drain_deadline, reply_deadline)
                    break
                if kind != "ZERO":
                    continue
                if last_result is not None and identifier == last_result.command_id:
                    _offer(results, ("ZERO", last_result, None))
                elif last_result is not None and identifier < last_result.command_id:
                    _offer(
                        results,
                        (
                            "ZERO",
                            ControlResult(
                                identifier, "confirmed_not_applied", now, version
                            ),
                            None,
                        ),
                    )
                elif pending_zero is None:
                    pending_zero = (identifier, deadline)

            if not batch or pending_stop is not None:
                for code in tuple(decoder.code_times):
                    if code not in sent_codes:
                        sent_codes.add(code)
                        if not _offer(diagnostics, {"monotonic_ns": now, "code": code}):
                            decoder.counters["diagnostic_event_drop_count"] += 1
                            decoder.note_code("DIAGNOSTIC_EVENTS_DROPPED", now)
                if not decoder.integrity_valid and not ring_sent:
                    ring_sent = True
                    _offer(failure_ring, tuple(decoder.recent_events))

            if pending_stop is not None:
                identifier, drain_deadline, reply_deadline = pending_stop
                if not batch or time.monotonic_ns() >= drain_deadline:
                    drain_complete = not batch and not drain_expired
                    if not drain_complete:
                        # Preserve the partial-count warning even if the matched
                        # STOP report is lost; the existing status path survives it.
                        _offer(
                            status,
                            (
                                "failed",
                                "SHUTDOWN_INCOMPLETE",
                                INCOMPLETE_DRAIN_MESSAGE,
                                None,
                            ),
                        )
                    source.close()
                    source = None
                    final = decoder.snapshot(
                        time.monotonic_ns(),
                        heartbeat_ns=heartbeat,
                        state_version=version + 1,
                    )
                    final = materialize_state(
                        final, time.monotonic_ns(), config, stopped=True
                    )
                    shared.publish(final)
                    message = (
                        "STOP",
                        ControlResult(
                            identifier, "stopped", time.monotonic_ns(), version + 1
                        ),
                        StopReport(
                            final, decoder.first_failure_diagnostic, drain_complete
                        ),
                    )
                    # Release GPIO and deliver even an incomplete-drain report
                    # before the parent's matching reply/join/cleanup deadlines.
                    try:
                        results.put(
                            message,
                            timeout=max(
                                0, (reply_deadline - time.monotonic_ns()) / 1e9
                            ),
                        )
                        stop_result_sent = True
                    except Full:
                        pass
                    return
                continue

            if pending_zero is not None and (
                not batch or now >= pending_zero[1] or not decoder.integrity_valid
            ):
                identifier, deadline = pending_zero
                now = time.monotonic_ns()
                slot = shared.reserve()
                if slot is not None or now >= deadline or not decoder.integrity_valid:
                    applied = (
                        not batch
                        and slot is not None
                        and now < deadline
                        and decoder.integrity_valid
                    )
                    if applied:
                        decoder.apply_zero(identifier, now)
                    else:
                        decoder.last_resolved_zero_id = identifier
                        decoder.last_zero_result = "rejected"
                        decoder.zero_resolved_ns = decoder.last_update_ns = now
                    if slot is not None:
                        version += 1
                        shared.publish(
                            decoder.snapshot(
                                now, heartbeat_ns=heartbeat, state_version=version
                            ),
                            slot,
                        )
                    last_result = ControlResult(
                        identifier,
                        "applied" if applied else "confirmed_not_applied",
                        now,
                        version,
                    )
                    _offer(results, ("ZERO", last_result, None))
                    pending_zero = None
    except BaseException as exc:
        now = time.monotonic_ns()
        code = (
            exc.code
            if isinstance(exc, BackendError)
            else "BACKEND_IO_FAILED"
            if ready
            else "ACQUISITION_START_FAILED"
        )
        if decoder is not None:
            decoder.note_code(code, now)
            _publish(decoder, shared, now, heartbeat, version)
            if not ring_sent:
                _offer(failure_ring, tuple(decoder.recent_events))
        _offer(status, ("failed", code, str(exc), getattr(exc, "context", None)))
    finally:
        if source is not None:
            try:
                source.close()
            except Exception as exc:
                _offer(status, ("failed", "SHUTDOWN_INCOMPLETE", str(exc), None))
        # The parent receives the small normal STOP report before joining us.
        # Its shutdown deadline/forced cleanup still bounds a stalled sender.
        if stop_result_sent:
            results.close()
            results.join_thread()
        else:
            results.cancel_join_thread()
        # Large failure history and logger diagnostics remain best effort.
        for channel in (diagnostics, failure_ring):
            channel.cancel_join_thread()


def _publish(decoder, shared, now_ns: int, heartbeat_ns: int, version: int) -> int:
    """Publish a batch snapshot or diagnose a skip; return last published version."""
    if shared.publish(
        decoder.snapshot(now_ns, heartbeat_ns=heartbeat_ns, state_version=version + 1)
    ):
        return version + 1
    decoder.counters["publication_skip_count"] += 1
    decoder.note_code("STATE_PUBLICATION_SKIPPED", now_ns)
    return version
