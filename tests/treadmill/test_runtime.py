"""Spawn-safe process, coherence and standalone-counter contract tests."""

from dataclasses import replace
import multiprocessing
import threading
import time

import pytest

from test_core import config, trace
from box_runtime.input.treadmill.gpio_backend import BackendError, InitialState
from box_runtime.input.treadmill.state import SharedState, StateReadError, TreadmillState
from box_runtime.input.treadmill.runtime import (
    HealthError, LifecycleError, TreadmillRuntime, UnavailableError, ZeroError,
)
from debug import treadmill_counter


class SyntheticSource:
    """Finite deterministic source; after motion ends it waits like real GPIO."""

    def __init__(self, settings):
        """Use validated config and generate 800 exact transitions, dimensionless."""
        self.settings = settings
        self.events = []
        self.mapping = {"test_source":True}
        self.index = 0

    def open(self, deadline_ns):
        """Return startup AB bits and schedule 800 past but monotonic-ns events."""
        start = time.monotonic_ns()-80_000_000
        self.events = trace([1]*600+[-1]*200,start_ns=start,period_ns=50_000)
        return InitialState(0)

    def read_batch(self, timeout_s):
        """Return 40 events or wait at most requested seconds when stationary."""
        if self.index < len(self.events):
            batch=self.events[self.index:self.index+40]
            self.index += len(batch)
            return batch
        time.sleep(min(timeout_s,0.005))
        return []

    def close(self):
        """Synthetic source owns no external resources; return None."""


class FailingSource(SyntheticSource):
    """Backend with a specific, actionable startup failure."""

    def open(self, deadline_ns):
        """Raise a line-claim failure without publishing initial motion state."""
        raise BackendError("GPIO_LINE_CLAIM_FAILED","test line already owned")


def wait_count(runtime, expected=800):
    """Return a snapshot once expected events arrive, within a 3 s test limit."""
    deadline=time.monotonic()+3
    while time.monotonic() < deadline:
        state=runtime.snapshot()
        if state.edge_event_count >= expected:
            return state
        time.sleep(0.005)
    pytest.fail("acquisition did not drain the synthetic source")


@pytest.fixture
def runtime():
    """Yield a real spawned runtime with a synthetic source, then close it."""
    instance=TreadmillRuntime(config(processing_lag_warning_ns=1_000_000_000),source_factory=SyntheticSource)
    instance.start()
    try:
        yield instance
    finally:
        instance.close()


def test_shared_state_has_explicit_optionals_and_nonblocking_double_slots():
    context=multiprocessing.get_context("spawn")
    shared=SharedState(context)
    original=TreadmillState(position_mm=None,speed_mm_s=None,state_version=1,
                            processing_lag_histogram_counts=tuple(range(11)))
    assert shared.publish(original)
    assert shared.read() == original
    shared._locks[0].acquire()
    shared._locks[1].acquire()
    try:
        start=time.monotonic()
        assert shared.publish(replace(original,state_version=2)) is False
        assert time.monotonic()-start < 0.1
        with pytest.raises(StateReadError):
            shared.read(timeout_s=0.005)
    finally:
        shared._locks[1].release()
        shared._locks[0].release()
    assert shared.publish(replace(original,state_version=3))
    assert shared.read().state_version == 3


def test_shared_readers_receive_coherent_combinations():
    shared=SharedState(multiprocessing.get_context("spawn"))
    assert shared.publish(TreadmillState())
    errors=[]
    finished=threading.Event()
    def read():
        """Check a field invariant in repeated bounded snapshots."""
        while not finished.is_set():
            try:
                state=shared.read()
                if state.raw_transition_position != state.valid_transition_count:
                    errors.append(state)
            except StateReadError:
                pass
    readers=[threading.Thread(target=read) for _ in range(3)]
    for reader in readers:
        reader.start()
    try:
        for number in range(1,1000):
            shared.publish(TreadmillState(raw_transition_position=number,valid_transition_count=number))
    finally:
        finished.set()
        for reader in readers:
            reader.join(2)
    assert not errors


def test_process_counts_heartbeat_zero_and_cached_final_state(runtime):
    state=wait_count(runtime)
    assert state.valid_transition_count == 800 and state.raw_transition_position == 400
    assert state.positive_transition_count == 600 and state.negative_transition_count == 200
    assert state.integrity_valid
    first_heartbeat=state.last_heartbeat_monotonic_ns
    time.sleep(0.12)
    assert runtime.snapshot().last_heartbeat_monotonic_ns > first_heartbeat
    result=runtime.zero()
    assert result.status == "applied"
    state=runtime.snapshot()
    assert state.position_mm == 0 and state.valid_transition_count == 800
    runtime.close()
    assert runtime.health_report().health == "STOPPED"
    assert runtime.snapshot().valid_transition_count == 800
    with pytest.raises(LifecycleError):
        runtime.zero()
    runtime.close()


def test_zero_reconciles_without_direct_acknowledgement(runtime,monkeypatch):
    wait_count(runtime)
    monkeypatch.setattr(runtime,"_wait_result",lambda *args: None)
    result=runtime.zero()
    assert result.status == "applied" and result.confirmation_source == "shared_state"
    assert runtime.snapshot().last_applied_zero_command_id == result.command_id


def test_zero_rejects_active_recording_without_enqueuing(runtime):
    wait_count(runtime)
    runtime._recording_active=lambda: True
    before=runtime._command_id
    with pytest.raises(LifecycleError):
        runtime.zero()
    assert runtime._command_id == before


def test_worker_death_is_failed_not_stationary(runtime):
    wait_count(runtime)
    runtime._process.terminate()
    runtime._process.join(2)
    state=runtime.snapshot()
    assert state.health == "FAILED" and state.position_mm is None
    assert runtime.health_report().failure_code == "ACQUISITION_PROCESS_EXITED"
    with pytest.raises(HealthError):
        runtime.require_healthy()


def test_startup_failure_retains_specific_cause_and_cleans_children():
    instance=TreadmillRuntime(config(),source_factory=FailingSource)
    instance.start()
    assert instance.health_report().failure_code == "GPIO_LINE_CLAIM_FAILED"
    assert not instance._process.is_alive()
    with pytest.raises(UnavailableError):
        instance.snapshot()
    instance.close()
    required=TreadmillRuntime(config(treadmill_required=True),source_factory=FailingSource)
    with pytest.raises(UnavailableError):
        required.start()
    required.close()


def test_state_timeout_is_nonthrowing_in_health_then_clears(runtime):
    wait_count(runtime)
    for lock in runtime._shared._locks:
        lock.acquire()
    try:
        with pytest.raises(StateReadError):
            runtime.snapshot()
        assert "STATE_READ_TIMEOUT" in runtime.health_report().active_codes
    finally:
        for lock in reversed(runtime._shared._locks):
            lock.release()
    assert "STATE_READ_TIMEOUT" not in runtime.health_report().active_codes
    assert "STATE_READ_TIMEOUT" in runtime.health_report().observed_health_codes


def test_close_gate_blocks_queued_reader_without_ipc_access(runtime,monkeypatch):
    wait_count(runtime)
    runtime._close_requested.set()
    monkeypatch.setattr(runtime._shared,"read",lambda **kwargs: pytest.fail("closing reader touched IPC"))
    with pytest.raises(LifecycleError):
        runtime.snapshot()
    assert runtime.health_report().health == "CLOSING"
    monkeypatch.undo()


def test_source_is_created_in_child_and_global_start_method_unchanged(runtime):
    before=multiprocessing.get_start_method(allow_none=True)
    assert runtime._process.pid != __import__("os").getpid()
    assert runtime._context.get_start_method() == "spawn"
    assert multiprocessing.get_start_method(allow_none=True) == before


def test_standalone_duration_and_ctrl_c_paths(capsys):
    """Both normal stop paths report final counts and release the runtime."""
    def factory(settings):
        """Construct the real production process facade with an injected source."""
        return TreadmillRuntime(settings,source_factory=SyntheticSource)
    assert treadmill_counter.main(["--duration-s","0.2"],runtime_factory=factory) == 0
    output=capsys.readouterr().out
    assert "800" in output and "400" in output and "x4" in output
    assert "Integrity" in output and "Sequence" in output
    def interrupt(seconds):
        """Represent the operator interrupting after synthetic events are drained."""
        time.sleep(0.2)
        raise KeyboardInterrupt
    assert treadmill_counter.main([],runtime_factory=factory,wait_fn=interrupt) == 0


@pytest.mark.parametrize("duration",["0","-1","nan","inf"])
def test_standalone_rejects_invalid_duration(duration):
    with pytest.raises(SystemExit) as error:
        treadmill_counter.main(["--duration-s",duration])
    assert error.value.code != 0
