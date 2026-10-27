"""Attach to a running treadmill counter and measure IRQ-to-pin-read latency.

Uses Linux tracefs only: no GPIO claims, kernel patches or additional packages.
Start either counter first, then run this script in a second terminal. Tracing
adds overhead. The pin is sampled somewhere between rp1_gpio_get entry/return;
these are timing brackets, not timestamps of the physical electrical edge.
Use --analyze-capture DIRECTORY to print a saved capture without root or GPIO.
"""

from __future__ import annotations

import argparse
import errno
import heapq
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    from debug.treadmill_options import parse_counter_options
except ModuleNotFoundError:
    from treadmill_options import parse_counter_options


TRACE_ROOT = Path("/sys/kernel/tracing")
BUFFER_KB_PER_CPU = 65536
EVENT_NAMES = ("irq_handler_entry", "thread_start", "read_start", "read_end")
MAX_EXCLUDED_CONTEXTS = 8
CONTEXT_RECORDS = 8
TRACE_LINE = re.compile(
    r"^\s*.*-(\d+)\s+\[(\d+)\]\s+(?:\S+\s+)?"
    r"(\d+)\.(\d+):\s+(?:\w+:)?(\w+):\s*(.*)$"
)


@dataclass(frozen=True)
class TraceRecord:
    """One record: monotonic ns (text resolution 1 us), IDs, optional raw level.

    kind is one of EVENT_NAMES. irq/pin/value are integers or None; tid/cpu
    identify the executing kernel task and CPU, not the userspace counter.
    """

    time_ns: int
    kind: str
    tid: int
    cpu: int
    irq: int | None = None
    pin: int | None = None
    value: int | None = None


@dataclass
class ReadAttempt:
    """Pending per-IRQ pairing state; all timestamps are TraceRecord monotonic ns."""

    irq_entry: TraceRecord | None
    thread_entry: TraceRecord
    read_entry: TraceRecord | None = None
    ambiguous: bool = False
    invalid: bool = False
    latest_irq_entry: TraceRecord | None = None


def main(argv: list[str] | None = None) -> int:
    """Accept pin/duration CLI strings; capture/report with sudo; return exit code.

    Pins are BCM numbers and duration is seconds (None means until Ctrl-C).
    Returns 0 for a loss-free capture, 1 for an error or trace loss. Ambiguous
    pairs are reported separately and excluded from all latency statistics.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if any(argument.split("=", 1)[0] == "--analyze-capture" for argument in arguments):
        parser = argparse.ArgumentParser(
            description="Print a saved IRQ latency capture; no files are changed."
        )
        parser.add_argument("--analyze-capture", type=Path, required=True)
        offline = parser.parse_args(arguments)
        try:
            summary = analyze_capture(offline.analyze_capture)
            for line in format_summary(summary):
                print(line)
            return int(not summary["capture_complete"])
        except (OSError, ValueError) as error:
            print(f"ERROR: {error}")
            return 1
    args, config = parse_counter_options(arguments, __doc__)
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        print("ERROR: This diagnostic requires the aarch64 Pi 5 Linux kernel.")
        return 1
    directory = None
    try:
        if os.geteuid() != 0:
            # Keep the output parent writable by the operator after sudo exits.
            (
                Path(__file__).resolve().parents[1]
                / "tmp_operator_runs/treadmill_irq_latency"
            ).mkdir(parents=True, exist_ok=True)
            print(
                "Kernel tracing needs sudo; the existing counter keeps running.",
                flush=True,
            )
            # Reuse uv's interpreter; do not run uv/package management as root.
            os.execvp(
                "sudo",
                [
                    "sudo",
                    "--",
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    *arguments,
                ],
            )
        pins = (config.a_bcm_pin, config.b_bcm_pin)
        irq_pins = find_gpio_irqs(Path("/proc/interrupts").read_text(), pins)
        thread_ids = find_irq_threads(irq_pins)
        directory = capture_trace(irq_pins, thread_ids, args.duration_s)
        summary = analyze_capture(directory)
        (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        for line in format_summary(summary):
            print(line)
        print(f"Capture saved: {directory}")
        return int(not summary["capture_complete"])
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}")
        return 1
    finally:
        if directory is not None:
            return_capture_ownership(directory)


def analyze_capture(directory: Path) -> dict:
    """Return a report from saved trace/metadata Paths; do not write or claim hardware.

    IRQ/TID JSON map keys are converted back to integers. The returned summary
    uses the same ns/us/count contract as analyze_trace. Unfinished captures
    raise ValueError; recorded trace losses remain present in the report.
    """
    metadata = json.loads((directory / "metadata.json").read_text())
    if metadata.get("status") != "captured":
        raise ValueError("Capture is incomplete; no complete report can be produced")
    try:
        irq_pins = {int(irq): pin for irq, pin in metadata["irq_pins"].items()}
        thread_ids = {int(irq): tid for irq, tid in metadata["thread_ids"].items()}
        losses = metadata["loss_counts"]
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(
            "Capture metadata lacks IRQ/thread/loss information"
        ) from error
    with (directory / "trace.txt").open() as source:
        return analyze_trace(
            parse_trace(source), irq_pins, thread_ids, loss_counts=losses
        )


def parse_trace(lines: Iterable[str]) -> Iterator[TraceRecord]:
    """Yield records from ftrace text; preserve order and integer monotonic ns.

    Input is an iterable of text lines from this diagnostic's private instance.
    Headers are ignored; malformed relevant lines and LOST markers raise
    ValueError. Timestamp decimals are converted without floating-point loss.
    """
    for line in lines:
        if "LOST" in line:
            raise ValueError("LOST events in trace; capture is incomplete")
        if line.lstrip().startswith("#") or not line.strip():
            continue
        match = TRACE_LINE.match(line)
        if match is None:
            if any(f"{name}:" in line for name in EVENT_NAMES):
                raise ValueError(f"Malformed trace record: {line.strip()}")
            continue
        tid, cpu, seconds, fraction, event, payload = match.groups()
        kind = next(
            (
                name
                for name in EVENT_NAMES
                if event == name or event.endswith("_" + name)
            ),
            None,
        )
        if kind is None:
            continue
        fields = dict(re.findall(r"\b(irq|pin|value)=(-?\d+)\b", payload))
        required = {"irq"} if kind in EVENT_NAMES[:2] else {"pin"}
        if kind == "read_end":
            required.add("value")
        if not required <= fields.keys() or len(fraction) > 9:
            raise ValueError(f"Malformed trace record: {line.strip()}")
        yield TraceRecord(
            int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0")),
            kind,
            int(tid),
            int(cpu),
            **{name: int(value) for name, value in fields.items()},
        )


def analyze_trace(
    records: Iterable[TraceRecord],
    irq_pins: dict[int, int],
    thread_ids: dict[int, int],
    *,
    loss_counts: dict[str, int] | None = None,
) -> dict:
    """Return primitive per-pin latency statistics from ordered trace records.

    irq_pins maps Linux IRQ to BCM pin; thread_ids maps IRQ to kernel TID.
    loss_counts contains nonnegative dropped/missed-event counts. Latencies
    are microseconds; saved example timestamps remain monotonic ns. Only
    unique IRQ/thread/read matches contribute. Overlap is never repaired.
    """
    pending = {irq: deque() for irq in irq_pins}
    active: dict[int, ReadAttempt] = {}
    task_irqs = {tid: irq for irq, tid in thread_ids.items()}
    issues = Counter()
    counts = {irq: Counter() for irq in irq_pins}
    delays = {irq: {name: [] for name in EVENT_NAMES[1:]} for irq in irq_pins}
    examples: dict[int, list] = {irq: [] for irq in irq_pins}
    history: deque[TraceRecord] = deque(maxlen=CONTEXT_RECORDS + 1)
    excluded = []
    ordinal = 0
    last_record = None
    for record in records:
        last_record = record
        for context in excluded:
            if len(context["records"]) < context["trigger_index"] + 1 + CONTEXT_RECORDS:
                context["records"].append(record)
        history.append(record)
        if record.kind in EVENT_NAMES[:2]:
            irq = record.irq
        else:
            irq = task_irqs.get(record.tid)
        if irq not in irq_pins:
            continue
        if record.kind == "irq_handler_entry":
            counts[irq]["irq_entries"] += 1
            pending[irq].append(record)
            if irq in active:
                active[irq].ambiguous = True
                active[irq].latest_irq_entry = record
            continue
        if record.tid != thread_ids[irq]:
            continue
        if record.kind == "thread_start":
            counts[irq]["thread_entries"] += 1
            if irq in active:
                issues["incomplete_read"] += 1
                _exclude_read(
                    excluded,
                    "incomplete_read",
                    irq_pins[irq],
                    active[irq],
                    record,
                    history,
                )
            active[irq] = ReadAttempt(
                pending[irq][0] if pending[irq] else None,
                record,
                ambiguous=len(pending[irq]) > 1,
                latest_irq_entry=pending[irq][-1] if pending[irq] else None,
            )
            pending[irq].clear()
            continue
        attempt = active.get(irq)
        if attempt is None:
            issues[f"unpaired_{record.kind}"] += 1
            _exclude_read(
                excluded,
                f"unpaired_{record.kind}",
                irq_pins[irq],
                None,
                record,
                history,
            )
            continue
        if record.pin != irq_pins[irq]:
            issues["pin_mismatch"] += 1
            attempt.invalid = True
        if record.kind == "read_start":
            if attempt.read_entry is not None:
                attempt.ambiguous = True
            attempt.read_entry = record
            continue
        counts[irq]["read_returns"] += 1
        del active[irq]
        if attempt.ambiguous:
            issues["ambiguous_read"] += 1
            _exclude_read(
                excluded, "ambiguous_read", irq_pins[irq], attempt, record, history
            )
            # Resynchronize after the uncertain interval. A queued thread with
            # no new IRQ will be reported unpaired, not assigned a guessed IRQ.
            pending[irq].clear()
            continue
        if attempt.invalid:
            _exclude_read(
                excluded, "pin_mismatch", irq_pins[irq], attempt, record, history
            )
            continue
        if record.value not in (0, 1):
            issues["read_error"] += 1
            _exclude_read(
                excluded, "read_error", irq_pins[irq], attempt, record, history
            )
            continue
        if attempt.irq_entry is None or attempt.read_entry is None:
            issues["incomplete_read"] += 1
            _exclude_read(
                excluded, "incomplete_read", irq_pins[irq], attempt, record, history
            )
            continue
        times = (
            attempt.irq_entry.time_ns,
            attempt.thread_entry.time_ns,
            attempt.read_entry.time_ns,
            record.time_ns,
        )
        if list(times) != sorted(times):
            issues["timestamp_order_invalid"] += 1
            _exclude_read(
                excluded,
                "timestamp_order_invalid",
                irq_pins[irq],
                attempt,
                record,
                history,
            )
            continue
        for name, timestamp in zip(EVENT_NAMES[1:], times[1:]):
            delays[irq][name].append(timestamp - times[0])
        counts[irq]["matched_reads"] += 1
        ordinal += 1
        example = {
            "irq_ns": times[0],
            "thread_ns": times[1],
            "read_start_ns": times[2],
            "read_end_ns": times[3],
            "value": record.value,
            "irq_cpu": attempt.irq_entry.cpu,
            "read_cpu": record.cpu,
        }
        heapq.heappush(examples[irq], (times[3] - times[0], ordinal, example))
        if len(examples[irq]) > 10:
            heapq.heappop(examples[irq])
    issues["irq_without_read"] += sum(len(queue) for queue in pending.values())
    issues["incomplete_read"] += len(active)
    for irq, attempt in active.items():
        _exclude_read(
            excluded, "incomplete_read", irq_pins[irq], attempt, last_record, history
        )
    for irq, queue in pending.items():
        if queue:
            _exclude_read(
                excluded, "irq_without_read", irq_pins[irq], None, last_record, history
            )
    for context in excluded:
        context["records"] = [asdict(record) for record in context["records"]]
    losses = dict(loss_counts or {})
    return {
        "capture_complete": not any(losses.values()),
        "loss_counts": losses,
        "pairing_issues": {name: count for name, count in issues.items() if count},
        "excluded_contexts": excluded,
        "timestamp_resolution_us": 1,
        "pins": {
            str(pin): {
                **{
                    name: counts[irq][name]
                    for name in (
                        "irq_entries",
                        "thread_entries",
                        "read_returns",
                        "matched_reads",
                    )
                },
                "irq": irq,
                "latency_us": {
                    name: latency_statistics(values)
                    for name, values in delays[irq].items()
                },
                "longest_reads": [
                    item[2] for item in sorted(examples[irq], reverse=True)
                ],
            }
            for irq, pin in irq_pins.items()
        },
    }


def _exclude_read(
    contexts: list[dict],
    reason: str,
    pin: int,
    attempt: ReadAttempt | None,
    record: TraceRecord | None,
    history: deque[TraceRecord],
) -> None:
    """Retain one of eight excluded contexts, using only known ns/us timing points.

    Inputs are bounded context dictionaries, a reason, BCM pin, optional pending
    attempt/current record and recent records. Return None. Candidate delays
    are an ambiguous range, never an extra matched latency sample.
    """
    if len(contexts) >= MAX_EXCLUDED_CONTEXTS:
        return
    first = attempt.irq_entry.time_ns if attempt and attempt.irq_entry else None
    latest = (
        attempt.latest_irq_entry.time_ns
        if attempt and attempt.latest_irq_entry
        else None
    )
    thread = attempt.thread_entry.time_ns if attempt else None
    read = attempt.read_entry.time_ns if attempt and attempt.read_entry else None
    returned = record is not None and record.kind == "read_end" and record.pin == pin
    candidates = None
    if first is not None and latest is not None and read is not None and read >= latest:
        candidates = [(read - latest) / 1000, (read - first) / 1000]
    contexts.append(
        {
            "reason": reason,
            "pin": pin,
            "first_irq_ns": first,
            "latest_irq_ns": latest,
            "thread_ns": thread,
            "read_start_ns": read,
            "read_end_ns": record.time_ns if returned else None,
            "value": record.value if returned else None,
            "thread_to_read_us": (read - thread) / 1000
            if read is not None and thread is not None
            else None,
            "candidate_irq_to_read_us": candidates,
            "trigger_index": max(0, len(history) - 1),
            "records": list(history),
        }
    )


def latency_statistics(values_ns: list[int]) -> dict:
    """Return count, median, nearest-rank p95/p99, max and >=50 us count in us.

    Input is a one-dimensional list of nonnegative integer nanosecond delays.
    Empty input returns count=0 with None for all latency values.
    """
    values = sorted(values_ns)
    return {
        "count": len(values),
        "median": statistics.median(values) / 1000 if values else None,
        "p95": values[math.ceil(len(values) * 0.95) - 1] / 1000 if values else None,
        "p99": values[math.ceil(len(values) * 0.99) - 1] / 1000 if values else None,
        "maximum": values[-1] / 1000 if values else None,
        "at_least_50_us": sum(value >= 50_000 for value in values),
    }


def find_gpio_irqs(text: str, pins: tuple[int, int]) -> dict[int, int]:
    """Return Linux-IRQ -> BCM-pin mapping from /proc/interrupts text.

    Require exactly one pinctrl-rp1/behavbox-treadmill row for each selected pin;
    otherwise raise RuntimeError directing the user to start the counter first.
    """
    matches = {}
    for line in text.splitlines():
        fields = line.split()
        if "pinctrl-rp1" not in fields:
            continue
        index = fields.index("pinctrl-rp1")
        if fields[index + 2 :] != ["Edge", "behavbox-treadmill"]:
            continue
        pin = int(fields[index + 1])
        if pin not in pins:
            continue
        if pin in matches.values():
            raise RuntimeError(f"GPIO{pin} has ambiguous counter IRQ rows")
        matches[int(fields[0].rstrip(":"))] = pin
    if set(matches.values()) != set(pins):
        raise RuntimeError(
            f"Start the raw or quadrature counter on BCM{pins[0]}/{pins[1]} first"
        )
    return matches


def find_irq_threads(
    irq_pins: dict[int, int], proc_root: Path = Path("/proc")
) -> dict[int, int]:
    """Return IRQ -> kernel TID mapping by reading proc-root PID/comm text files.

    IRQ keys are integers; proc_root is /proc or a test fixture Path. A missing
    or ambiguous thread raises RuntimeError; no affinity or priority is changed.
    """
    matches = {}
    for path in proc_root.glob("[0-9]*/comm"):
        try:
            name = path.read_text().strip()
        except FileNotFoundError:
            continue
        for irq in irq_pins:
            if name.startswith(f"irq/{irq}-"):
                if irq in matches:
                    raise RuntimeError(f"IRQ {irq} has ambiguous kernel threads")
                matches[irq] = int(path.parent.name)
    if set(matches) != set(irq_pins):
        raise RuntimeError("Counter IRQ threads are missing; leave the counter running")
    return matches


@contextmanager
def trace_session(
    trace_root: Path,
    irq_pins: dict[int, int],
    thread_ids: dict[int, int],
    buffer_kb: int = BUFFER_KB_PER_CPU,
) -> Iterator[tuple[Path, str]]:
    """Yield (private instance Path, probe group); remove owned resources on exit.

    Inputs are tracefs Path, IRQ -> BCM-pin and IRQ -> TID integer maps, and
    buffer KiB per CPU. Requires root and kernel kprobe support. Changes no
    global tracer settings; dynamic probes are uniquely named and owned here.
    """
    group = f"treadmill_latency_{os.getpid()}_{time.monotonic_ns()}"
    definitions = {
        "thread_start": f"p:{group}/{group}_thread_start edge_irq_thread irq=$arg1:u32",
        "read_start": f"p:{group}/{group}_read_start rp1_gpio_get pin=$arg2:u32",
        "read_end": f"r128:{group}/{group}_read_end rp1_gpio_get pin=$arg2:u32 value=$retval:s32",
    }
    registered = []
    instance = None
    try:
        for name, definition in definitions.items():
            write_control(trace_root / "dynamic_events", definition)
            registered.append(name)
        instance = create_trace_instance(trace_root, group)
        write_control(instance / "tracing_on", "0")
        write_control(instance / "trace_clock", "mono")
        write_control(instance / "buffer_size_kb", str(buffer_kb))
        irq_filter = " || ".join(f"irq == {irq}" for irq in irq_pins)
        read_filter = " || ".join(
            f"(common_pid == {thread_ids[irq]} && pin == {pin})"
            for irq, pin in irq_pins.items()
        )
        event_filters = {
            "irq/irq_handler_entry": irq_filter,
            f"{group}/{group}_thread_start": irq_filter,
            f"{group}/{group}_read_start": read_filter,
            f"{group}/{group}_read_end": read_filter,
        }
        for event, condition in event_filters.items():
            write_control(instance / "events" / event / "filter", condition)
            write_control(instance / "events" / event / "enable", "1")
        write_control(instance / "tracing_on", "1")
        yield instance, group
    finally:
        errors = []
        if instance is not None:
            for path in (instance / "tracing_on", instance / "events/enable"):
                try:
                    write_control(path, "0")
                except OSError as error:
                    errors.append(str(error))
        for name in reversed(registered):
            try:
                write_control(
                    trace_root / "dynamic_events", f"-:{group}/{group}_{name}"
                )
            except OSError as error:
                errors.append(str(error))
        if instance is not None:
            try:
                instance.rmdir()
            except OSError as error:
                errors.append(str(error))
        if errors:
            raise RuntimeError("Trace cleanup failed: " + "; ".join(errors))


def write_control(path: Path, text: str) -> None:
    """Write one ASCII tracefs command without seeking; return None or OSError.

    path is a tracefs control Path and text is one command without its newline.
    Appending must preserve other probes, but buffered Python append opens seek
    to EOF, which tracefs rejects. Use one direct write and close even on error.
    """
    command = (text + "\n").encode("ascii")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
        if os.write(descriptor, command) != len(command):
            raise OSError(errno.EIO, "Short tracefs command write")
    except OSError as error:
        raise OSError(
            error.errno, f"Tracefs command {text!r}: {error.strerror}", str(path)
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def create_trace_instance(trace_root: Path, name: str) -> Path:
    """Create one uniquely named tracefs instance and return its Path; no units."""
    instance = trace_root / "instances" / name
    instance.mkdir()
    return instance


def read_loss_counts(instance: Path, trace_root: Path, group: str) -> dict[str, int]:
    """Return summed integer ring-buffer losses and this probe group's miss count.

    Paths point to the stopped instance and tracefs root. Counter files must
    exist and be readable; missing statistics are an error, not zero loss.
    """
    losses = {"overrun": 0, "commit_overrun": 0, "dropped_events": 0, "probe_misses": 0}
    statistics_files = list((instance / "per_cpu").glob("cpu*/stats"))
    if not statistics_files:
        raise RuntimeError("Trace buffer loss statistics unavailable")
    for path in statistics_files:
        fields = dict(
            re.findall(r"^([a-z ]+):\s*(\d+)", path.read_text(), re.MULTILINE)
        )
        for label in ("overrun", "commit overrun", "dropped events"):
            losses[label.replace(" ", "_")] += int(fields[label])
    expected = {f"{group}_{name}" for name in EVENT_NAMES[1:]}
    observed = set()
    for line in (trace_root / "kprobe_profile").read_text().splitlines():
        name, _, misses = line.split()
        # This kernel prints the bare event name, without its group. Unique
        # names prevent another tracing session's counters being mistaken for ours.
        if name in expected:
            observed.add(name)
            losses["probe_misses"] += int(misses)
    if observed != expected:
        raise RuntimeError("Owned probe loss statistics unavailable")
    return losses


def capture_trace(
    irq_pins: dict[int, int], thread_ids: dict[int, int], duration_s: float | None
) -> Path:
    """Capture until duration seconds/Ctrl-C; return a new ignored run-directory Path.

    IRQ/pin/TID integer maps refer to an already running counter. Recording is
    entirely in kernel RAM; trace text and metadata are saved after stopping.
    """
    repo = Path(__file__).resolve().parents[1]
    directory = (
        repo
        / "tmp_operator_runs/treadmill_irq_latency"
        / datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    )
    directory.mkdir(parents=True)
    metadata = {
        "status": "incomplete",
        "kernel": platform.release(),
        "clock": "mono",
        "irq_pins": irq_pins,
        "thread_ids": thread_ids,
        "duration_s": duration_s,
        "buffer_kb_per_cpu": BUFFER_KB_PER_CPU,
        "limitations": "Tracing adds overhead. Text timestamps resolve 1 us. Pin sampling is bracketed by read entry/return.",
    }
    revision = subprocess.run(
        ["git", "-c", f"safe.directory={repo}", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    metadata["git_revision"] = (
        revision.stdout.strip() if revision.returncode == 0 else None
    )
    print(f"Capture directory: {directory}", flush=True)
    try:
        with trace_session(TRACE_ROOT, irq_pins, thread_ids) as (instance, group):
            metadata["started_monotonic_ns"] = time.monotonic_ns()
            print(
                "IRQ latency tracer ready. Start the generator now; Ctrl-C stops tracing.",
                flush=True,
            )
            print(
                f"Capturing IRQs {irq_pins}; buffer {BUFFER_KB_PER_CPU} KiB per CPU.",
                flush=True,
            )
            try:
                if duration_s is None:
                    while True:
                        time.sleep(1)
                else:
                    time.sleep(duration_s)
            except KeyboardInterrupt:
                pass
            write_control(instance / "tracing_on", "0")
            write_control(instance / "events/enable", "0")
            metadata["stopped_monotonic_ns"] = time.monotonic_ns()
            metadata["loss_counts"] = read_loss_counts(instance, TRACE_ROOT, group)
            with (
                (instance / "trace").open() as source,
                (directory / "trace.txt").open("x") as target,
            ):
                shutil.copyfileobj(source, target)
        metadata["status"] = "captured"
        return directory
    finally:
        try:
            (directory / "metadata.json").write_text(
                json.dumps(metadata, indent=2) + "\n"
            )
        finally:
            return_capture_ownership(directory)


def return_capture_ownership(directory: Path) -> None:
    """Give only this run's files back to sudo's invoking UID/GID; no units/return."""
    if os.geteuid() == 0 and "SUDO_UID" in os.environ and "SUDO_GID" in os.environ:
        uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
        for path in [*directory.iterdir(), directory]:
            os.chown(path, uid, gid, follow_symlinks=False)


def format_summary(summary: dict) -> list[str]:
    """Return human-readable lines from analyze_trace's primitive us/ns summary."""
    lines = [
        f"Trace capture complete: {summary['capture_complete']}",
        f"Trace losses / probe misses: {summary['loss_counts']}",
        f"Excluded pairing issues: {summary['pairing_issues']}",
        "Delays are microseconds from IRQ-handler entry; timestamp resolution is 1 us.",
        "Latency statistics exclude ambiguous/incomplete pairs, which can include the longest delays.",
    ]
    for pin, result in summary["pins"].items():
        lines.append(
            f"BCM{pin}: {result['irq_entries']} IRQ entries; {result['matched_reads']} matched pin reads"
        )
        for name, stats in result["latency_us"].items():
            lines.append(
                f"  {name}: median={stats['median']}, p95={stats['p95']}, p99={stats['p99']}, max={stats['maximum']}; >=50 us={stats['at_least_50_us']}"
            )
        lines.append(
            "  Longest read-end delays (IRQ monotonic ns, delay us, sampled level):"
        )
        for example in result["longest_reads"]:
            lines.append(
                f"    {example['irq_ns']} {(example['read_end_ns'] - example['irq_ns']) / 1000:.1f} {example['value']}"
            )
    for context in summary["excluded_contexts"]:
        lines.append(
            f"Excluded BCM{context['pin']} read: {context['reason']}; value={context['value']}"
        )
        lines.append(
            f"  Thread-to-read: {context['thread_to_read_us']} us; candidate IRQ-to-read range: {context['candidate_irq_to_read_us']} us (not a matched sample)"
        )
        lines.append(
            "  Trace context: monotonic_ns CPU TID kind IRQ pin value; > marks exclusion"
        )
        for index, record in enumerate(context["records"]):
            marker = ">" if index == context["trigger_index"] else " "
            lines.append(
                f"  {marker} {record['time_ns']} {record['cpu']} {record['tid']} {record['kind']} {record['irq']} {record['pin']} {record['value']}"
            )
    lines.append("Tracing changes timing. Compare with identical untraced bursts.")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
