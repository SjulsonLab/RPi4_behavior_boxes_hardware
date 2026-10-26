"""Minimal GPIO-source comparison: raw counts, bounded anomalies and CLI cleanup."""

from dataclasses import replace
import signal

import pytest

from test_core import trace
from test_first_failure import extra_level_trace
from box_runtime.input.treadmill.decoder import EdgeEvent
from box_runtime.input.treadmill.gpio_backend import InitialState
from debug import treadmill_raw_counter


def feed(counter, events):
    """Process list[EdgeEvent] with a fixed later monotonic-ns processing time."""
    for event in events:
        counter.process_event(event, 10_000_000_000)


def test_clean_raw_counts_and_lag_without_quadrature_decoding():
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    events = trace([1] * 200)
    for event in events:
        counter.process_event(event, event.timestamp_ns + 5000)
    assert counter.edge_event_count == 200
    assert counter.edge_counts == [[50, 50], [50, 50]]
    assert counter.maximum_processing_lag_ns == 5000
    assert counter.anomaly_counts == {} and counter.anomalies == []


def test_same_level_event_is_reported_and_later_events_are_still_counted():
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    events = extra_level_trace(20, 8)
    feed(counter, events)
    assert counter.edge_event_count == 21
    assert counter.edge_counts == [[5, 6], [5, 5]]
    assert counter.anomaly_counts == {"A_EDGE_LEVEL_REPEATED": 1}
    assert counter.anomalies[0].event_number == 9
    assert counter.anomalies[0].event == events[8]


def test_cross_line_reordering_is_reported_without_sorting_or_claiming_loss():
    events = trace([1] * 12)
    events[8], events[9] = events[9], events[8]
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    feed(counter, events)
    assert counter.edge_event_count == 12
    assert counter.anomaly_counts["GLOBAL_SEQUENCE_GAP"] == 2
    assert counter.anomaly_counts["GLOBAL_SEQUENCE_ORDER_INVALID"] == 1
    assert counter.anomaly_counts["TIMESTAMP_ORDER_INVALID"] == 1
    assert counter.anomalies[0].event == events[8]
    assert counter.anomalies[1].event == events[9]
    assert counter.anomalies[0].global_sequence_before == 8
    assert counter.anomalies[1].global_sequence_before == 10


def test_sequence_wrap_and_nonzero_startup_baselines_are_clean():
    initial = InitialState(3, 2**32 - 3, (2**32 - 2, 2**32 - 2))
    counter = treadmill_raw_counter.RawEdgeCounter(initial)
    events = trace(
        [1] * 8,
        initial=3,
        global_baseline=initial.global_sequence_number,
        line_baselines=initial.line_sequence_numbers,
    )
    feed(counter, events)
    assert counter.edge_counts == [[2, 2], [2, 2]]
    assert counter.anomaly_counts == {}


def test_duplicates_and_same_line_timestamp_reversals_remain_visible():
    event = trace([1])[0]
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    feed(counter, [event, event])
    assert counter.edge_event_count == 2
    assert counter.anomaly_counts["GLOBAL_SEQUENCE_DUPLICATE"] == 1
    assert counter.anomaly_counts["B_LINE_SEQUENCE_DUPLICATE"] == 1
    assert counter.anomaly_counts["B_TIMESTAMP_ORDER_INVALID"] == 1
    assert counter.anomaly_counts["B_EDGE_LEVEL_REPEATED"] == 1


def test_future_timestamp_has_no_valid_lag_measurement():
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    counter.process_event(trace([1])[0], 1)
    assert counter.maximum_processing_lag_ns is None
    assert counter.anomaly_counts["EVENT_TIMESTAMP_INVALID"] == 1


def test_anomaly_samples_remain_bounded_while_counts_continue():
    event = trace([1])[0]
    events = [
        replace(event, global_sequence_number=i, line_sequence_number=i, timestamp_ns=i)
        for i in range(1, 101)
    ]
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    feed(counter, events)
    assert counter.edge_event_count == 100
    assert counter.anomaly_counts["B_EDGE_LEVEL_REPEATED"] == 99
    assert len(counter.anomalies) == 8
    assert counter.anomalies[0].event_number == 2
    assert counter.anomalies[-1].event_number == 9


def test_invalid_payload_is_reported_without_inventing_a_channel_count():
    counter = treadmill_raw_counter.RawEdgeCounter(InitialState(0))
    feed(counter, [EdgeEvent(1, "unknown", "rising", 1, 1)])
    assert counter.edge_event_count == 1
    assert counter.edge_counts == [[0, 0], [0, 0]]
    assert counter.anomaly_counts["EVENT_FORMAT_INVALID"] == 1


class ScriptedSource:
    """In-process source with explicit batches/errors and resource-release state."""

    def __init__(self, settings, batches, *, interrupt=False, send_sigint=False):
        """Store config, list[list[EdgeEvent]], and Boolean stop-mode test flags."""
        self.settings = settings
        self.batches = list(batches)
        self.interrupt = interrupt
        self.send_sigint = send_sigint
        self.closed = False
        self.reads = 0
        self.startup_error = None
        self.read_error = None
        self.close_error = None

    def open(self, deadline_ns):
        """Accept monotonic deadline ns; return a quiet AB=00 startup boundary."""
        if self.startup_error:
            raise self.startup_error
        return InitialState(0)

    def read_batch(self, timeout_s):
        """Accept seconds; return one supplied batch or raise an injected failure."""
        self.reads += 1
        if self.read_error:
            raise self.read_error
        if self.send_sigint:
            signal.raise_signal(signal.SIGINT)
        if self.batches:
            return self.batches.pop(0)
        if self.interrupt:
            raise KeyboardInterrupt
        return []

    def close(self):
        """Mark the source released and optionally raise an injected I/O error."""
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.fixture
def run_raw():
    """Return a CLI harness with synthetic monotonic time and an injected source."""

    def run(batches, *, arguments=None, interrupt=False, send_sigint=False, error=None):
        """Return (exit int, source, output str) using events/ns and CLI strings."""
        instances = []
        lines = []
        now_ns = 2_000_000_000

        def clock_ns():
            """Advance a deterministic monotonic clock by 10 ms; return integer ns."""
            nonlocal now_ns
            now_ns += 10_000_000
            return now_ns

        def factory(settings):
            """Accept validated config; return the single scripted input source."""
            source = ScriptedSource(
                settings, batches, interrupt=interrupt, send_sigint=send_sigint
            )
            if error:
                setattr(source, error, OSError(f"injected {error}"))
            instances.append(source)
            return source

        def output(line):
            """Collect terminal text; summaries must follow source release."""
            if line.startswith("Kernel edge events:"):
                assert instances[0].closed
            lines.append(line)

        result = treadmill_raw_counter.main(
            ["--duration-s", "1"] if arguments is None else arguments,
            source_factory=factory,
            clock_ns_fn=clock_ns,
            output_fn=output,
        )
        return result, instances[0], "\n".join(lines)

    return run


@pytest.mark.parametrize("interrupt", [False, True])
def test_raw_cli_duration_or_ctrl_c_uses_selected_pins_and_closes(run_raw, interrupt):
    arguments = ["--a-pin", "23", "--b-pin", "24"]
    if not interrupt:
        arguments += ["--duration-s", "1"]
    result, source, output = run_raw(
        [trace([1] * 20)], arguments=arguments, interrupt=interrupt
    )
    assert result == 0 and source.closed
    assert (source.settings.a_bcm_pin, source.settings.b_bcm_pin) == (23, 24)
    assert "BCM23/24" in output and "Kernel edge events: 20" in output
    assert "A rising / falling: 5 / 5" in output
    assert "B rising / falling: 5 / 5" in output
    assert "Maximum processing lag:" in output
    assert "Raw anomaly observations: 0" in output


def test_sigint_finishes_the_admitted_batch_and_restores_signal_handler(run_raw):
    previous = signal.getsignal(signal.SIGINT)
    result, source, output = run_raw([trace([1] * 20)], send_sigint=True)
    assert result == 0 and source.closed and source.reads == 1
    assert "Kernel edge events: 20" in output
    assert signal.getsignal(signal.SIGINT) is previous


def test_raw_cli_continues_through_failure_until_the_whole_burst_is_counted(run_raw):
    events = extra_level_trace(64, 8)
    result, source, output = run_raw([events[:20], events[20:]])
    assert result == 1 and source.closed and source.reads >= 2
    assert "Kernel edge events: 65" in output
    assert "A_EDGE_LEVEL_REPEATED" in output
    assert "Observed gaps can include reordered events" in output


@pytest.mark.parametrize("error", ["startup_error", "read_error", "close_error"])
def test_raw_cli_reports_io_failures_and_always_releases_source(run_raw, error):
    result, source, output = run_raw([], error=error)
    assert result == 1 and source.closed
    assert f"injected {error}" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--a-pin", "23", "--b-pin", "23"],
        ["--a-pin", "9"],
        ["--b-pin", "28"],
        ["--a-pin", "23.0"],
        ["--duration-s", "0"],
        ["--duration-s", "nan"],
    ],
)
def test_raw_cli_invalid_options_fail_before_source_creation(arguments):
    def factory(settings):
        """Reject any attempt to construct a source with invalid CLI options."""
        pytest.fail("Invalid options reached source construction")

    with pytest.raises(SystemExit) as error:
        treadmill_raw_counter.main(arguments, source_factory=factory)
    assert error.value.code == 2


def test_raw_cli_defaults_match_manifest_and_help_has_simple_options(run_raw, capsys):
    result, source, _ = run_raw([])
    assert result == 0
    assert (source.settings.a_bcm_pin, source.settings.b_bcm_pin) == (13, 16)
    with pytest.raises(SystemExit) as error:
        treadmill_raw_counter.main(["--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert (
        "--duration-s" in help_text
        and "--a-pin" in help_text
        and "--b-pin" in help_text
    )
    assert "--expected" not in help_text
