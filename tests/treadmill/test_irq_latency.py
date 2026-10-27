"""Synthetic kernel traces verify timing without claiming GPIO or enabling tracing."""

import errno
import json
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def latency():
    """Return the diagnostic module; no hardware, timestamps or physical units."""
    return import_module("debug.treadmill_irq_latency")


def trace_line(kind, time_us, *, irq=180, pin=23, tid=900, cpu=0, value=0):
    """Return one ftrace text record from integer us and scalar IRQ/pin/task IDs."""
    seconds, microseconds = divmod(time_us, 1_000_000)
    fields = {
        "irq_handler_entry": f"irq={irq} name=behavbox-treadmill",
        "thread_start": f"(edge_irq_thread+0x0/0x100) irq={irq}",
        "read_start": f"(rp1_gpio_get+0x0/0x100) pin={pin}",
        "read_end": f"(rp1_gpio_get+0x40/0x100) pin={pin} value={value}",
    }
    return (
        f" irq/{irq}-behav-{tid} [{cpu:03d}] d.h. "
        f"{seconds}.{microseconds:06d}: {kind}: {fields[kind]}\n"
    )


def read_cycle(start_us, *, irq=180, pin=23, tid=900, cpu=0, value=0):
    """Return four trace lines: IRQ, thread +7 us, read +9 us, return +11 us."""
    return [
        trace_line("irq_handler_entry", start_us, irq=irq, tid=44),
        trace_line("thread_start", start_us + 7, irq=irq, tid=tid, cpu=cpu),
        trace_line("read_start", start_us + 9, pin=pin, tid=tid, cpu=cpu),
        trace_line("read_end", start_us + 11, pin=pin, tid=tid, cpu=cpu, value=value),
    ]


def trace_time_key(line):
    """Return a trace string's seconds field for sorting same-second test records."""
    return line.split("] ")[1].split()[1]


def analyze(latency, lines, *, losses=None):
    """Analyze trace strings for two selected pins; return the primitive summary."""
    return latency.analyze_trace(
        latency.parse_trace(lines),
        {180: 23, 181: 24},
        {180: 900, 181: 901},
        loss_counts=losses,
    )


def test_pairs_interleaved_pins_and_cpu_migration(latency):
    """Pin identity, not CPU or the current hard-IRQ task, controls pairing."""
    a = read_cycle(1_000_000, cpu=2)
    b = read_cycle(1_000_003, irq=181, pin=24, tid=901, cpu=1, value=1)
    lines = sorted(a + b, key=trace_time_key)
    summary = analyze(latency, lines)
    assert summary["capture_complete"] is True
    assert summary["pairing_issues"] == {}
    for pin in ("23", "24"):
        result = summary["pins"][pin]
        assert result["matched_reads"] == 1
        assert result["latency_us"]["thread_start"]["maximum"] == 7
        assert result["latency_us"]["read_start"]["maximum"] == 9
        assert result["latency_us"]["read_end"]["maximum"] == 11
    assert summary["pins"]["24"]["longest_reads"][0]["value"] == 1


def test_summary_percentiles_thresholds_and_integer_time(latency):
    """Large boot timestamps retain us resolution and threshold counts are inclusive."""
    lines = []
    for index, delay in enumerate((5, 20, 50, 80)):
        base = 1_223_000_000_000 + index * 1_000
        lines.extend(
            [
                trace_line("irq_handler_entry", base),
                trace_line("thread_start", base + delay - 2),
                trace_line("read_start", base + delay),
                trace_line("read_end", base + delay + 1),
            ]
        )
    stats = analyze(latency, lines)["pins"]["23"]["latency_us"]["read_start"]
    assert stats == {
        "count": 4,
        "median": 35,
        "p95": 80,
        "p99": 80,
        "maximum": 80,
        "at_least_50_us": 2,
    }


@pytest.mark.parametrize("extra_at", [2, 8, 10])
def test_overlapping_interrupts_are_not_given_fabricated_latencies(latency, extra_at):
    """An IRQ before thread start or during the read makes that interval ambiguous."""
    lines = read_cycle(100)
    lines.append(trace_line("irq_handler_entry", 100 + extra_at))
    lines.sort(key=trace_time_key)
    lines.extend(read_cycle(200))
    summary = analyze(latency, lines)
    assert summary["pins"]["23"]["matched_reads"] == 1
    assert summary["pairing_issues"]["ambiguous_read"] == 1
    assert summary["pins"]["23"]["latency_us"]["read_end"]["maximum"] == 11


def test_second_thread_start_invalidates_incomplete_previous_read(latency):
    """A missing read cannot be silently reused as the next cycle's partner."""
    lines = read_cycle(100)[:2] + read_cycle(200)
    summary = analyze(latency, lines)
    assert summary["pins"]["23"]["matched_reads"] == 1
    assert summary["pairing_issues"]["incomplete_read"] == 1


def test_boundary_records_and_unrelated_activity_are_separate(latency):
    """Ignore other IRQs and tasks; count unpaired start/end records explicitly."""
    lines = [trace_line("read_end", 10)]
    lines += read_cycle(100)
    lines += read_cycle(200, irq=999, pin=27, tid=12)
    lines += [trace_line("read_start", 250, tid=50)]
    lines += [trace_line("irq_handler_entry", 300)]
    summary = analyze(latency, lines)
    assert summary["pins"]["23"]["matched_reads"] == 1
    assert summary["pairing_issues"]["unpaired_read_end"] == 1
    assert summary["pairing_issues"]["irq_without_read"] == 1


def test_wrong_pin_or_read_error_is_not_a_valid_sample(latency):
    """Pin/task disagreements and negative hardware-read returns are excluded."""
    lines = read_cycle(100, pin=24) + read_cycle(200, value=-5)
    summary = analyze(latency, lines)
    assert summary["pins"]["23"]["matched_reads"] == 0
    assert summary["pairing_issues"]["pin_mismatch"] >= 1
    assert summary["pairing_issues"]["read_error"] == 1


@pytest.mark.parametrize(
    "loss", ["overrun", "commit_overrun", "dropped_events", "probe_misses"]
)
def test_trace_loss_invalidates_capture(latency, loss):
    """A timing sample is not evidence of a complete capture when tracing lost data."""
    summary = analyze(latency, read_cycle(100), losses={loss: 1})
    assert summary["capture_complete"] is False
    assert summary["loss_counts"][loss] == 1


def test_parser_rejects_malformed_owned_events(latency):
    """Truncated relevant records must produce a clear error, not disappear."""
    with pytest.raises(ValueError, match="trace record"):
        list(latency.parse_trace(["broken read_start: pin=23\n"]))


def test_parser_ignores_headers_but_rejects_lost_event_markers(latency):
    """Plain ftrace headers are harmless; explicit LOST markers invalidate parsing."""
    assert list(latency.parse_trace(["# tracer: nop\n", "\n"])) == []
    with pytest.raises(ValueError, match="LOST"):
        list(latency.parse_trace(["CPU:0 [LOST 10 EVENTS]\n"]))


def test_parser_accepts_live_pi_format_and_unique_probe_names(latency):
    """Actual Pi traces have five flags and a caller/function pair on return."""
    records = list(
        latency.parse_trace(
            [
                (
                    " python-116155 [003] d.... 1224531.686400: owned_read_start: "
                    "(rp1_gpio_get+0x0/0xb0) pin=23\n"
                ),
                (
                    " python-116155 [003] d..1. 1224531.686409: owned_read_end: "
                    "(gpio_chip_get_multiple.part.0+0x40/0xb8 <- rp1_gpio_get) pin=23 value=1\n"
                ),
            ]
        )
    )
    assert [record.kind for record in records] == ["read_start", "read_end"]
    assert records[1].time_ns - records[0].time_ns == 9000
    assert records[1].value == 1


def test_discovers_only_selected_rp1_counter_irqs(latency):
    """Decode /proc/interrupts without hardcoding changing Linux IRQ numbers."""
    text = """           CPU0 CPU1
 180: 10 0 pinctrl-rp1 23 Edge behavbox-treadmill
 181: 10 0 pinctrl-rp1 24 Edge behavbox-treadmill
 182: 10 0 pinctrl-rp1 25 Edge other-consumer
 183: 10 0 other-chip 23 Edge behavbox-treadmill
"""
    assert latency.find_gpio_irqs(text, (23, 24)) == {180: 23, 181: 24}
    with pytest.raises(RuntimeError, match="counter"):
        latency.find_gpio_irqs(text, (13, 16))
    with pytest.raises(RuntimeError, match="ambiguous"):
        latency.find_gpio_irqs(
            text + "184: 0 0 pinctrl-rp1 23 Edge behavbox-treadmill\n", (23, 24)
        )


def test_discovers_irq_threads_without_relying_on_truncated_consumer_name(
    latency, tmp_path
):
    """Kernel comm names may be truncated; the IRQ-number prefix remains usable."""
    for pid, comm in (
        (900, "irq/180-behavbo"),
        (901, "irq/181-behavbo"),
        (902, "python"),
    ):
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "comm").write_text(comm + "\n")
    assert latency.find_irq_threads({180: 23, 181: 24}, tmp_path) == {
        180: 900,
        181: 901,
    }


@pytest.fixture
def fake_tracefs(latency, tmp_path, monkeypatch):
    """Provide a tracefs control recorder; no actual kernel configuration is changed."""
    root = tmp_path / "tracing"
    (root / "instances").mkdir(parents=True)
    writes = []
    instances = []

    def write_control(path, text):
        """Record Path/string control writes; fake tracefs has no generated files."""
        writes.append((path.relative_to(root).as_posix(), text))

    def create_instance(trace_root, name):
        """Create and return one empty fake instance Path for the requested name."""
        path = trace_root / "instances" / name
        path.mkdir()
        instances.append(path)
        return path

    monkeypatch.setattr(latency, "write_control", write_control)
    monkeypatch.setattr(latency, "create_trace_instance", create_instance)
    return root, writes, instances


@pytest.mark.parametrize("interruption", [None, KeyboardInterrupt, ValueError])
def test_session_filters_and_cleanup_preserve_other_tracing(
    latency, fake_tracefs, interruption
):
    """Normal completion, Ctrl-C and analysis errors delete only the owned resources."""
    root, writes, instances = fake_tracefs
    try:
        with latency.trace_session(
            root, {180: 23, 181: 24}, {180: 900, 181: 901}
        ) as session:
            instance, group = session
            assert instance.parent == root / "instances"
            assert instance.name == group
            if interruption is not None:
                raise interruption("test interruption")
    except (KeyboardInterrupt, ValueError):
        if interruption is None:
            raise
    assert not instances[0].exists()
    registrations = [text for path, text in writes if path == "dynamic_events"]
    assert len([text for text in registrations if not text.startswith("-:")]) == 3
    assert len([text for text in registrations if text.startswith("-:")]) == 3
    assert all("instances/" in path or path == "dynamic_events" for path, _ in writes)
    filters = [text for path, text in writes if path.endswith("/filter")]
    assert any("irq == 180" in text and "irq == 181" in text for text in filters)
    assert any("common_pid == 900" in text and "pin == 23" in text for text in filters)
    assert any(path.endswith("/tracing_on") and text == "0" for path, text in writes)


def test_partial_probe_registration_is_cleaned_on_setup_error(
    latency, fake_tracefs, monkeypatch
):
    """A rejected second probe must remove the first registration before returning."""
    root, writes, _ = fake_tracefs
    original_write = latency.write_control

    def reject_read_probe(path, text):
        """Simulate unsupported probe syntax after the first successful registration."""
        if "read_start rp1_gpio_get" in text:
            raise OSError("test registration failure")
        original_write(path, text)

    monkeypatch.setattr(latency, "write_control", reject_read_probe)
    with (
        pytest.raises(OSError, match="registration failure"),
        latency.trace_session(root, {180: 23, 181: 24}, {180: 900, 181: 901}),
    ):
        pytest.fail("A failed setup must not yield a session")
    registrations = [text for path, text in writes if path == "dynamic_events"]
    assert len(registrations) == 2
    assert registrations[1].startswith("-:")


def test_loss_counters_cover_every_cpu_and_owned_probe_only(latency, tmp_path):
    """Sum ring-buffer losses across CPUs without attributing other probes' misses."""
    for cpu in (0, 2):
        directory = tmp_path / "instance" / "per_cpu" / f"cpu{cpu}"
        directory.mkdir(parents=True)
        (directory / "stats").write_text(
            "entries: 12\noverrun: 1\ncommit overrun: 2\ndropped events: 3\n"
        )
    (tmp_path / "kprobe_profile").write_text(
        "other_entry 100 99\nowned_thread_start 20 1\n"
        "owned_read_start 20 0\nowned_read_end 20 2\n"
    )
    assert latency.read_loss_counts(tmp_path / "instance", tmp_path, "owned") == {
        "overrun": 2,
        "commit_overrun": 4,
        "dropped_events": 6,
        "probe_misses": 3,
    }


def test_missing_owned_probe_statistics_are_not_treated_as_zero_loss(latency, tmp_path):
    """The Pi's profile uses bare event names, so each probe needs a unique name."""
    directory = tmp_path / "instance/per_cpu/cpu0"
    directory.mkdir(parents=True)
    (directory / "stats").write_text(
        "overrun: 0\ncommit overrun: 0\ndropped events: 0\n"
    )
    (tmp_path / "kprobe_profile").write_text("other_entry 1 0\n")
    with pytest.raises(RuntimeError, match="probe.*statistics"):
        latency.read_loss_counts(tmp_path / "instance", tmp_path, "owned")


def test_longest_examples_are_bounded(latency):
    """Reporting retains only ten timestamped examples per pin."""
    lines = []
    for index in range(30):
        lines.extend(read_cycle(index * 100))
    result = analyze(latency, lines)["pins"]["23"]
    assert result["matched_reads"] == 30
    assert len(result["longest_reads"]) == 10


def test_help_needs_neither_root_nor_kernel_tracing(latency):
    """The ordinary uv-run entry point should expose help before privileged work."""
    with pytest.raises(SystemExit) as error:
        latency.main(["--help"])
    assert error.value.code == 0


@pytest.fixture
def capture_environment(latency, tmp_path, monkeypatch):
    """Provide stopped trace text/statistics and a fake kernel session for capture tests."""
    repo = tmp_path / "repo"
    (repo / "debug").mkdir(parents=True)
    root = tmp_path / "tracefs"
    instance = root / "instances/owned"
    (instance / "per_cpu/cpu0").mkdir(parents=True)
    (instance / "per_cpu/cpu0/stats").write_text(
        "overrun: 0\ncommit overrun: 0\ndropped events: 0\n"
    )
    (instance / "trace").write_text("".join(read_cycle(100)))
    (root / "kprobe_profile").write_text(
        "owned_thread_start 1 0\nowned_read_start 1 0\nowned_read_end 1 0\n"
    )
    writes = []
    closed = []

    @contextmanager
    def session(*args, **kwargs):
        """Yield a fake instance/group; record that cleanup runs after any exception."""
        try:
            yield instance, "owned"
        finally:
            closed.append(True)

    def write(path, text):
        """Record control writes as Path/text pairs, with no tracefs side effects."""
        writes.append((path, text))

    def interrupt_wait(seconds):
        """Simulate Ctrl-C while waiting for the burst; seconds is a float."""
        raise KeyboardInterrupt

    def revision(*args, **kwargs):
        """Return a fake git command result without invoking a real process."""
        return SimpleNamespace(returncode=0, stdout="abc123\n")

    monkeypatch.setattr(
        latency, "__file__", str(repo / "debug/treadmill_irq_latency.py")
    )
    monkeypatch.setattr(latency, "TRACE_ROOT", root)
    monkeypatch.setattr(latency, "trace_session", session)
    monkeypatch.setattr(latency, "write_control", write)
    monkeypatch.setattr(latency.time, "sleep", interrupt_wait)
    monkeypatch.setattr(latency.subprocess, "run", revision)
    return repo, instance, writes, closed


def test_ctrl_c_saves_trace_and_metadata_after_disabling_probes(
    latency, capture_environment
):
    """Ctrl-C stops capture, disables event overhead and saves readable trace/metadata."""
    _, instance, writes, closed = capture_environment
    directory = latency.capture_trace({180: 23, 181: 24}, {180: 900, 181: 901}, None)
    metadata = json.loads((directory / "metadata.json").read_text())
    assert closed == [True]
    assert (instance / "tracing_on", "0") in writes
    assert (instance / "events/enable", "0") in writes
    assert metadata["status"] == "captured"
    assert metadata["git_revision"] == "abc123"
    assert (
        len(
            list(
                latency.parse_trace((directory / "trace.txt").read_text().splitlines())
            )
        )
        == 4
    )


def test_failed_capture_keeps_incomplete_metadata_and_cleans_up(
    latency, capture_environment, monkeypatch
):
    """An export/statistics error leaves a diagnosable incomplete run, not a success."""
    repo, _, _, closed = capture_environment

    def fail_statistics(*args):
        """Simulate an I/O failure after recording stops; never returns a loss count."""
        raise OSError("test statistics failure")

    monkeypatch.setattr(latency, "read_loss_counts", fail_statistics)
    with pytest.raises(OSError, match="statistics failure"):
        latency.capture_trace({180: 23, 181: 24}, {180: 900, 181: 901}, 1.0)
    assert closed == [True]
    paths = list(
        (repo / "tmp_operator_runs/treadmill_irq_latency").glob("*/metadata.json")
    )
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["status"] == "incomplete"


def test_control_write_supports_tracefs_without_seek_and_preserves_existing_events(
    latency, tmp_path, monkeypatch
):
    """Python buffered append seeks; tracefs rejects that even though direct writes work."""
    control = tmp_path / "dynamic_events"
    control.write_text("other_probe\n")
    original_open = Path.open

    def reject_buffered_append(path, mode="r", *args, **kwargs):
        """Simulate tracefs EINVAL on append-mode seek; other file reads still work."""
        if path == control and mode == "a":
            raise OSError(errno.EINVAL, "tracefs cannot seek to end")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_buffered_append)
    latency.write_control(control, "new_probe")
    assert control.read_text() == "other_probe\nnew_probe\n"


def test_control_write_error_names_command_and_path_and_closes_descriptor(
    latency, tmp_path, monkeypatch
):
    """A kernel-rejected command must identify its control file and leave no open fd."""
    control = tmp_path / "dynamic_events"
    control.touch()
    opened = []
    closed = []
    original_open = latency.os.open
    original_close = latency.os.close

    def track_open(*args, **kwargs):
        """Open the real temporary file, recording the returned integer descriptor."""
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def reject_command(fd, data):
        """Simulate kernel rejection of an ASCII tracefs command (integer fd, bytes)."""
        raise OSError(errno.EINVAL, "Invalid argument")

    def track_close(fd):
        """Close one integer descriptor and record it for the cleanup assertion."""
        closed.append(fd)
        return original_close(fd)

    monkeypatch.setattr(latency.os, "open", track_open)
    monkeypatch.setattr(latency.os, "write", reject_command)
    monkeypatch.setattr(latency.os, "close", track_close)
    with pytest.raises(OSError) as failure:
        latency.write_control(control, "test_probe_command")
    assert failure.value.errno == errno.EINVAL
    assert failure.value.filename == str(control)
    assert "test_probe_command" in str(failure.value)
    assert len(opened) == 1
    assert closed == opened


def test_short_control_write_is_reported_without_retry(latency, tmp_path, monkeypatch):
    """A partial kernel command is an error; retrying could repeat a side effect."""
    control = tmp_path / "dynamic_events"
    control.touch()
    writes = []

    def short_write(fd, data):
        """Return a short byte count instead of accepting the full command."""
        writes.append(data)
        return 1

    monkeypatch.setattr(latency.os, "write", short_write)
    with pytest.raises(OSError, match="[Ss]hort"):
        latency.write_control(control, "test_probe_command")
    assert len(writes) == 1
