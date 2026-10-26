"""Standalone acquisition must not serialize an unused large failure history."""

import time

import pytest

from test_core import config
from test_counter_pins import CounterRuntime, interrupt
from test_first_failure import ExtraEdgeSource
from test_runtime import wait_count
from box_runtime.input.treadmill.gpio_backend import BackendError
from box_runtime.input.treadmill.runtime import TreadmillRuntime
from debug import treadmill_counter


class ReadFailureSource(ExtraEdgeSource):
    """Finite failing input followed by an actionable backend read error."""

    def read_batch(self, timeout_s):
        """Accept seconds; raise BackendError after all queued events are read."""
        if self.index >= len(self.events):
            time.sleep(0.05)
            raise BackendError("BACKEND_IO_FAILED", "injected read failure")
        return super().read_batch(timeout_s)


def test_history_disabled_allocates_no_queue_and_preserves_small_stop_report():
    runtime = TreadmillRuntime(
        config(diagnostic_ring_buffer_size=16),
        source_factory=ExtraEdgeSource,
        capture_failure_history=False,
    )
    runtime.start()
    try:
        assert runtime._failure_ring is None and len(runtime._channels) == 4
        wait_count(runtime, 801)
    finally:
        runtime.close()
    assert runtime.first_failure_diagnostic.event_number == 41
    assert runtime.first_failure_diagnostic.following_event_count == 4
    assert not runtime.snapshot().integrity_valid


def test_runtime_default_preserves_existing_failure_history_delivery():
    runtime = TreadmillRuntime(
        config(diagnostic_ring_buffer_size=16), source_factory=ExtraEdgeSource
    )
    runtime.start()
    try:
        wait_count(runtime, 801)
        history = runtime._failure_ring.get(timeout=1)
        assert len(history) == 16
    finally:
        runtime.close()
    assert runtime.first_failure_diagnostic.event_number == 41


def test_history_disabled_does_not_mask_backend_exception_status():
    runtime = TreadmillRuntime(
        config(diagnostic_ring_buffer_size=16),
        source_factory=ReadFailureSource,
        capture_failure_history=False,
    )
    runtime.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            report = runtime.health_report()
            if "injected read failure" in report.message:
                break
            time.sleep(0.005)
        else:
            pytest.fail("Backend failure status was not delivered")
        assert "BACKEND_IO_FAILED" in report.observed_health_codes
        assert runtime._failure_ring is None
    finally:
        runtime.close()


def test_default_standalone_factory_disables_unused_history(monkeypatch):
    from box_runtime.input.treadmill import runtime as runtime_module

    selected = []

    def factory(settings, **options):
        """Inspect default-constructor options; return a resource-free test runtime."""
        selected.append(options)
        return CounterRuntime(settings)

    monkeypatch.setattr(runtime_module, "TreadmillRuntime", factory)
    assert treadmill_counter.main([], wait_fn=interrupt, output_fn=print) == 0
    assert selected == [{"capture_failure_history": False}]
