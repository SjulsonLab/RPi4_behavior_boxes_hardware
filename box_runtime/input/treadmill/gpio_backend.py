"""Verified libgpiod v2 adaptation; hardware is touched only by open()."""

import importlib
import os
import platform
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from .config import TreadmillConfig
from .decoder import EdgeEvent
from .ordering import EventOrderBuffer

INITIAL_SYNC_QUIET_PERIOD_S = 0.001


class BackendError(RuntimeError):
    """Hardware failure carrying a stable code and bounded resolution context."""

    def __init__(self, code: str, message: str, context: object = None):
        """Store known code string, human message and optional primitive context."""
        super().__init__(message)
        self.code = code
        self.context = context


@dataclass(frozen=True)
class InitialState:
    """Accepted AB bits, request/line uint32 baselines and excluded-event count."""

    ab_state: int
    global_sequence_number: int = 0
    line_sequence_numbers: tuple[int, int] = (0, 0)
    excluded_event_count: int = 0


class EdgeSource(Protocol):
    """Small production/test boundary; the acquisition child owns its lifetime."""

    def open(self, deadline_ns: int) -> InitialState:
        """Acquire/synchronize by absolute monotonic ns; return startup baselines."""
        ...

    def read_batch(self, timeout_s: float) -> list[EdgeEvent]:
        """Wait at most seconds, then return one bounded received-order event batch."""
        ...

    def close(self) -> None:
        """Release only source-owned resources; return None, idempotently."""
        ...


class GpiodSource:
    """One joint input request for manifest-defined A/B on the Pi 5 RP1."""

    def __init__(self, config: TreadmillConfig, *, binding=None, chip_paths=None):
        """Store config and optional test binding/inventory; create no resources.

        Args:
            config: Validated settings (BCM pins, buffer counts, debounce us).
            binding: Injected verified-API test double; None loads real gpiod.
            chip_paths: Optional sequence of test chip path strings.

        Returns:
            None. Neither importing nor construction claims hardware.
        """
        self.config = config
        self.binding = binding
        self.chip_paths = chip_paths
        self.request = None
        self.mapping: dict[str, object] = {}
        self.candidates: list[dict[str, object]] = []
        self.offsets = (config.a_bcm_pin, config.b_bcm_pin)

    # User-facing source operations.
    def open(self, deadline_ns: int) -> InitialState:
        """Validate API, claim both lines and establish a quiet startup boundary.

        Args:
            deadline_ns: Absolute monotonic startup deadline in nanoseconds.

        Returns:
            InitialState: AB and sequence baselines after excluded startup motion.

        Raises:
            BackendError: Actionable stable cause; partial requests are released.
        """
        try:
            if self.binding is None:
                self._load_real_binding()
            self._check_capabilities()
            self._resolve_lines()
            line = self.binding.line
            bias = {
                "pull_up": line.Bias.PULL_UP,
                "pull_down": line.Bias.PULL_DOWN,
                "disabled": line.Bias.DISABLED,
            }[self.config.gpio_bias]
            settings = self.binding.LineSettings(
                direction=line.Direction.INPUT,
                edge_detection=line.Edge.BOTH,
                bias=bias,
                active_low=False,
                debounce_period=timedelta(microseconds=self.config.debounce_period_us),
                event_clock=line.Clock.MONOTONIC,
            )
            try:
                self.request = self.binding.request_lines(
                    self.mapping["chip_path"],
                    consumer="behavbox-treadmill",
                    config={self.offsets: settings},
                    event_buffer_size=self.config.kernel_event_buffer_size,
                )
            except Exception as exc:
                raise BackendError(
                    "GPIO_LINE_CLAIM_FAILED",
                    f"Joint treadmill line request failed: {exc}",
                    self.mapping,
                ) from exc
            return self._synchronize(deadline_ns)
        except BaseException:
            self.close()
            raise

    def read_batch(self, timeout_s: float) -> list[EdgeEvent]:
        """Return up to read_batch_size received-order events, waiting at most seconds.

        Returns:
            list[EdgeEvent]: Kernel monotonic ns, channel/edge strings and uint32
            sequences. Empty means a normal timeout. BackendError means I/O loss.
        """
        try:
            if not self.request.wait_edge_events(timeout_s):
                return []
            return [
                self._adapt(event)
                for event in self.request.read_edge_events(
                    max_events=self.config.read_batch_size
                )
            ]
        except Exception as exc:
            raise BackendError(
                "BACKEND_IO_FAILED", f"Treadmill event read failed: {exc}", self.mapping
            ) from exc

    def close(self) -> None:
        """Release the joint request once; return None, propagating release errors."""
        request, self.request = self.request, None
        if request is not None:
            request.release()

    # Helpers: API validation, inventory resolution and initial synchronization.
    def _load_real_binding(self) -> None:
        """Load the official binding on an unforced Pi 5; return None or raise."""
        override = os.environ.get("BEHAVBOX_FORCE_MOCK")
        if override is not None and override.lower() not in ("0", "false", "no", "off"):
            raise BackendError(
                "BACKEND_CAPABILITY_MISSING",
                "Real treadmill acquisition is unavailable in forced/invalid mock mode",
            )
        try:
            model = Path("/proc/device-tree/model").read_text().rstrip("\0")
        except OSError:
            model = ""
        if not model.startswith("Raspberry Pi 5 ") or platform.machine() != "aarch64":
            raise BackendError(
                "BACKEND_CAPABILITY_MISSING",
                "Treadmill hardware requires an aarch64 Raspberry Pi 5",
            )
        try:
            self.binding = importlib.import_module("gpiod")
        except ImportError as exc:
            raise BackendError(
                "BACKEND_CAPABILITY_MISSING",
                "Install the official Trixie python3-libgpiod v2 binding",
            ) from exc

    def _check_capabilities(self) -> None:
        """Verify the installed v2 method/field/enum surface; return None or raise."""
        try:
            g = self.binding
            for name in (
                "request_lines",
                "LineSettings",
                "Chip",
                "LineRequest",
                "EdgeEvent",
            ):
                getattr(g, name)
            for name in (
                "get_values",
                "wait_edge_events",
                "read_edge_events",
                "release",
            ):
                getattr(g.LineRequest, name)
            if not {
                "timestamp_ns",
                "line_offset",
                "event_type",
                "global_seqno",
                "line_seqno",
            } <= set(g.EdgeEvent.__dataclass_fields__):
                raise AttributeError("Missing kernel event fields")
            for owner, name in (
                (g.line.Direction, "INPUT"),
                (g.line.Edge, "BOTH"),
                (g.line.Clock, "MONOTONIC"),
                (g.line.Bias, "PULL_UP"),
                (g.line.Bias, "PULL_DOWN"),
                (g.line.Bias, "DISABLED"),
            ):
                getattr(owner, name)
        except AttributeError as exc:
            raise BackendError(
                "BACKEND_CAPABILITY_MISSING", f"Incomplete libgpiod v2 API: {exc}"
            ) from exc

    def _resolve_lines(self) -> None:
        """Resolve named BCM lines on one distinct RP1; return None or raise."""
        paths = (
            list(Path("/dev").glob("gpiochip*"))
            if self.chip_paths is None
            else self.chip_paths
        )
        seen = set()
        accepted = []
        claimed = False
        for path in sorted(map(str, paths)):
            canonical = str(Path(path).resolve())
            record = {"path": path, "canonical_path": canonical}
            self.candidates.append(record)
            if canonical in seen:
                record["reason"] = "alias of an already inspected device"
                continue
            seen.add(canonical)
            chip = None
            try:
                chip = self.binding.Chip(canonical)
                info = chip.get_info()
                record.update(
                    name=info.name, label=info.label, num_lines=info.num_lines
                )
                if info.label != "pinctrl-rp1":
                    record["reason"] = "not the Pi 5 RP1 header controller"
                    continue
                offsets = tuple(
                    chip.line_offset_from_id(f"GPIO{pin}")
                    for pin in (self.config.a_bcm_pin, self.config.b_bcm_pin)
                )
                lines = [chip.get_line_info(offset) for offset in offsets]
                if offsets[0] == offsets[1] or any(
                    offset < 0 or offset >= info.num_lines for offset in offsets
                ):
                    raise ValueError("Invalid or duplicate resolved offsets")
                if any(line.used for line in lines):
                    claimed = True
                    record["reason"] = "configured line already owned"
                    continue
                record["reason"] = "eligible RP1 header controller"
                accepted.append((canonical, info.label, offsets))
            except (OSError, ValueError) as exc:
                record["reason"] = str(exc)
            finally:
                if chip is not None:
                    chip.close()
        if len(accepted) != 1:
            code = (
                "GPIO_LINE_CLAIM_FAILED"
                if claimed and not accepted
                else "GPIO_RESOLUTION_FAILED"
            )
            raise BackendError(
                code,
                "Cannot resolve one unambiguous, unclaimed RP1 controller",
                self.candidates,
            )
        path, label, self.offsets = accepted[0]
        self.mapping = {
            "chip_path": path,
            "chip_label": label,
            "a_bcm_pin": self.config.a_bcm_pin,
            "b_bcm_pin": self.config.b_bcm_pin,
            "a_offset": self.offsets[0],
            "b_offset": self.offsets[1],
        }

    def _adapt(self, event) -> EdgeEvent:
        """Convert a binding event into the pure ns/uint32 representation."""
        channel = (
            "A"
            if event.line_offset == self.offsets[0]
            else "B"
            if event.line_offset == self.offsets[1]
            else "unknown"
        )
        types = self.binding.EdgeEvent.Type
        edge = (
            "rising"
            if event.event_type == types.RISING_EDGE
            else "falling"
            if event.event_type == types.FALLING_EDGE
            else "unknown"
        )
        return EdgeEvent(
            event.timestamp_ns, channel, edge, event.global_seqno, event.line_seqno
        )

    def _synchronize(self, deadline_ns: int) -> InitialState:
        """Exclude startup motion using a quiet interval; preserve every sequence.

        Args:
            deadline_ns: Absolute monotonic startup deadline in ns.

        Returns:
            InitialState: Stable A/B levels and last excluded global/A/B numbers.
            A gap or expired deadline raises BackendError and open releases GPIO.
        """
        sequence = 0
        lines = [0, 0]
        excluded = 0
        ordering = EventOrderBuffer()
        while time.monotonic_ns() < deadline_ns:
            while True:
                batch = self.read_batch(0)
                for event in ordering.push(batch, time.monotonic_ns()):
                    channel = {"A": 0, "B": 1}.get(event.channel)
                    if (
                        channel is None
                        or event.edge_type not in ("rising", "falling")
                        or (event.global_sequence_number - sequence) % 2**32 != 1
                        or (event.line_sequence_number - lines[channel]) % 2**32 != 1
                    ):
                        raise BackendError(
                            "INITIAL_SYNC_FAILED",
                            "Startup sequence gap or malformed event",
                            self.mapping,
                        )
                    sequence = event.global_sequence_number
                    lines[channel] = event.line_sequence_number
                    excluded += 1
                if ordering.failed:
                    raise BackendError(
                        "INITIAL_SYNC_FAILED",
                        "Startup sequence gap exceeded ordering limits",
                        self.mapping,
                    )
                if time.monotonic_ns() >= deadline_ns:
                    raise BackendError(
                        "INITIAL_SYNC_FAILED",
                        "No quiet startup boundary before deadline",
                    )
                if not batch:
                    break
            before = self.request.get_values(list(self.offsets))
            remaining = (deadline_ns - time.monotonic_ns()) / 1e9
            if remaining <= 0:
                break
            if self.request.wait_edge_events(
                min(INITIAL_SYNC_QUIET_PERIOD_S, remaining)
            ):
                continue
            after = self.request.get_values(list(self.offsets))
            if (
                before != after
                or self.request.wait_edge_events(0)
                or ordering.pending_count
            ):
                continue
            levels = [int(value.value) for value in after]
            return InitialState(
                (levels[0] << 1) | levels[1], sequence, tuple(lines), excluded
            )
        raise BackendError(
            "INITIAL_SYNC_FAILED",
            "No quiet startup boundary before deadline",
            self.mapping,
        )
