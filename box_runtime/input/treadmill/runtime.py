"""Parent facade for isolated acquisition, bounded controls and cached cleanup."""

from dataclasses import asdict, dataclass, replace
import json
import multiprocessing
from queue import Empty, Full
import threading
import time

from .acquisition import ControlResult, acquisition_worker
from .config import TreadmillConfig
from .decoder import FirstFailureDiagnostic
from .gpio_backend import GpiodSource
from .state import (
    COHERENT_STATE_READ_TIMEOUT_S,
    FAILED_CODES,
    HEALTH_CODES,
    SharedState,
    StateReadError,
    TreadmillState,
    materialize_state,
    ordered_codes,
)


CONTROL_RESULT_TIMEOUT_S = 1.0
LOGGER_START_TIMEOUT_S = 5.0  # Reserved for TW-4, with TW-0 target timing evidence.


class LifecycleError(RuntimeError):
    """Operation is invalid during a recording, transition, or closed lifetime."""


class UnavailableError(RuntimeError):
    """No usable acquisition state was established."""


class HealthError(RuntimeError):
    """The requested operation requires healthier acquisition state."""


class ZeroError(RuntimeError):
    """A zero was confirmed not applied or could not be resolved in time."""

    def __init__(self, result: ControlResult):
        """Store a typed control result, in integer ID/version and monotonic ns."""
        super().__init__(f"Treadmill zero {result.command_id}: {result.status}")
        self.result = result


@dataclass(frozen=True)
class HealthReport:
    """Effective state plus ordered active and lifetime-observed stable codes."""

    health: str
    integrity_valid: bool
    acquisition_available: bool
    failure_code: str | None
    active_codes: tuple[str, ...] = ()
    observed_health_codes: tuple[str, ...] = ()
    message: str = ""


class TreadmillRuntime:
    """One acquisition lifetime; standalone callers may own the lifecycle guard."""

    def __init__(
        self,
        config: TreadmillConfig,
        *,
        source_factory=GpiodSource,
        lifecycle_lock=None,
        close_requested=None,
        recording_active=None,
    ):
        """Construct a facade without starting hardware or child processes.

        Args:
            config: Frozen validated settings in documented physical/time units.
            source_factory: Importable child-side EdgeSource constructor; the
                production default is real GPIO, alternatives are tests only.
            lifecycle_lock: Optional parent threading.RLock owned by InputService.
            close_requested: Optional parent threading.Event guarding IPC teardown.
            recording_active: Optional zero-argument shared-recording-state callback.

        Returns:
            None. The standalone entry point uses its own parent guard; BehavBox
            integration injects the same guard used for recording transitions.
        """
        self.config = config
        self._source_factory = source_factory
        self._lock = lifecycle_lock if lifecycle_lock is not None else threading.RLock()
        self._close_requested = (
            close_requested if close_requested is not None else threading.Event()
        )
        self._recording_active = (
            recording_active if recording_active is not None else _not_recording
        )
        self._context = multiprocessing.get_context("spawn")
        self._shared = None
        self._process = None
        self._channels = []
        self._started = self._closed = False
        self._last_state = None
        self._final_worker_state = None
        self._first_failure_diagnostic: FirstFailureDiagnostic | None = None
        self._command_id = 0
        self._active_parent_codes: set[str] = set()
        self._observed: dict[str, int] = {}
        self._last_report = HealthReport("STARTING", False, False, None)
        self._failure_message = ""
        self._startup_failed = False
        self._unresolved_zero_id = None
        self._failure_context_json = None
        self._startup_diagnostics = []
        self.mapping = {}
        self.startup_excluded_event_count = 0

    @property
    def first_failure_diagnostic(self) -> FirstFailureDiagnostic | None:
        """Return cached first integrity-failure context from the STOP result.

        Returns:
            FirstFailureDiagnostic | None: At most 13 immutable event records,
            with monotonic-ns timestamps and dimensionless counts/positions.
            None before delivery, on a clean run, or when shutdown could not
            deliver the report. This property never reads live or released IPC;
            callers use final integrity/health to distinguish clean/unavailable.
        """
        return self._first_failure_diagnostic

    @property
    def failure_context(self) -> dict | None:
        """Return a detached primitive copy of immutable serialized startup failure.

        Returns:
            dict | None: Effective settings, cached health/diagnostics and platform
            context, or None before startup failure. No live IPC is referenced.
        """
        return (
            None
            if self._failure_context_json is None
            else json.loads(self._failure_context_json)
        )

    # Public lifecycle and measurement operations.
    def start(self) -> None:
        """Start once and await usable initial state within startup_timeout_s.

        Returns:
            None. Optional startup failure cleans resources and retains failed
            health; required startup failure raises UnavailableError after cleanup.
        """
        with self._lock:
            self._check_open()
            if self._started:
                raise LifecycleError("A treadmill facade starts only once")
            self._started = True
            self._shared = SharedState(self._context)
            self._controls = self._context.Queue(8)
            self._results = self._context.Queue(8)
            self._diagnostics = self._context.Queue(
                self.config.diagnostic_event_queue_size
            )
            self._status = self._context.Queue(4)
            self._failure_ring = self._context.Queue(1)
            self._channels = [
                self._controls,
                self._results,
                self._diagnostics,
                self._status,
                self._failure_ring,
            ]
            deadline = time.monotonic() + self.config.startup_timeout_s
            try:
                self._process = self._context.Process(
                    target=acquisition_worker,
                    args=(
                        self.config,
                        self._shared,
                        self._controls,
                        self._results,
                        self._diagnostics,
                        self._status,
                        self._failure_ring,
                        self._source_factory,
                    ),
                    name="treadmill-acquisition",
                )
                self._process.start()
                message = self._status.get(timeout=max(0, deadline - time.monotonic()))
                if message[0] == "failed":
                    self._observe_failure(message)
                    raise UnavailableError(self._failure_message)
                self.mapping, self.startup_excluded_event_count = message[1:]
                self._read_locked()
                self._last_report = self._report_from_state(self._last_state)
            except Exception as exc:
                if not self._active_parent_codes:
                    self._observe("ACQUISITION_START_FAILED")
                    self._failure_message = str(exc)
                self._startup_failed = True
                self._stop_failed_start()
                self._last_report = HealthReport(
                    "FAILED",
                    False,
                    False,
                    ordered_codes(self._active_parent_codes)[0],
                    ordered_codes(self._active_parent_codes),
                    self._observed_order(),
                    self._failure_message,
                )
                self._failure_context_json = json.dumps(
                    {
                        "effective_config": asdict(self.config),
                        "health": asdict(self._last_report),
                        "mapping": self.mapping,
                        "diagnostics": self._startup_diagnostics,
                        "failure_monotonic_ns": time.monotonic_ns(),
                        "last_state": None
                        if self._last_state is None
                        else asdict(self._last_state),
                    }
                )
                if self.config.treadmill_required:
                    raise UnavailableError(self._failure_message) from exc

    def snapshot(self) -> TreadmillState:
        """Return immutable effective mm/mm/s/ns/count state, with a bounded copy.

        Raises:
            LifecycleError: Close is in progress without a final cached state.
            UnavailableError: Startup never established state.
            StateReadError: No coherent copy was obtained by the read deadline.
        """
        self._check_closing_reader()
        with self._lock:
            self._check_closing_reader()
            if self._closed:
                if self._last_state is None:
                    raise UnavailableError(
                        self._failure_message or "No final treadmill state"
                    )
                return self._last_state
            return self._read_locked()

    def health_report(self) -> HealthReport:
        """Return nonthrowing immutable health/codes without consuming diagnostics."""
        if self._close_requested.is_set():
            return self._closing_report()
        with self._lock:
            if self._close_requested.is_set():
                return self._closing_report()
            if self._startup_failed or not self._started:
                return self._last_report
            try:
                state = self._read_locked()
                self._last_report = self._report_from_state(state)
            except (StateReadError, UnavailableError):
                codes = ordered_codes(
                    set(self._last_report.active_codes) | self._active_parent_codes
                )
                self._last_report = replace(
                    self._last_report,
                    health="FAILED" if set(codes) & FAILED_CODES else "DEGRADED",
                    active_codes=codes,
                    failure_code=codes[0] if codes else None,
                    observed_health_codes=self._observed_order(),
                )
            return self._last_report

    def require_healthy(self) -> None:
        """Return None only for READY and valid integrity; otherwise raise HealthError."""
        report = self.health_report()
        if report.health != "READY" or not report.integrity_valid:
            raise HealthError(f"Treadmill {report.health}: {report.failure_code}")

    def zero(self) -> ControlResult:
        """Apply one ID-matched origin zero within the total control deadline.

        Returns:
            ControlResult: Applied zero ID, worker monotonic ns and state version.

        Raises:
            LifecycleError: Recording/close/mutex deadline prevents admission.
            HealthError: Acquisition is unavailable or integrity-invalid.
            ZeroError: Typed confirmed_not_applied or indeterminate result.
        """
        self._check_open()
        started = time.monotonic()
        deadline = started + CONTROL_RESULT_TIMEOUT_S
        worker_deadline = deadline - COHERENT_STATE_READ_TIMEOUT_S
        if not self._lock.acquire(timeout=max(0, worker_deadline - time.monotonic())):
            raise LifecycleError(
                "Recording transition exceeded the zero admission deadline"
            )
        try:
            self._check_open()
            if self._recording_active():
                raise LifecycleError("Cannot zero while shared recording is active")
            state = self._read_locked()
            if not state.acquisition_available or not state.integrity_valid:
                raise HealthError(
                    "Cannot zero unavailable or integrity-invalid acquisition"
                )
            self._command_id += 1
            identifier = self._command_id
            try:
                self._controls.put_nowait(
                    ("ZERO", identifier, int(worker_deadline * 1e9))
                )
            except Full:
                return self._zero_failure(
                    identifier, "confirmed_not_applied", state.state_version
                )
            result = self._wait_result("ZERO", identifier, worker_deadline)
            if result is not None:
                if result.status == "applied":
                    self._active_parent_codes -= {
                        "ZERO_NOT_APPLIED",
                        "ZERO_RESULT_INDETERMINATE",
                    }
                    self._unresolved_zero_id = None
                    return result
                return self._zero_failure(
                    identifier, result.status, result.state_version
                )
            while time.monotonic() < deadline:
                try:
                    state = self._read_locked(
                        timeout_s=max(
                            0,
                            min(
                                COHERENT_STATE_READ_TIMEOUT_S,
                                deadline - time.monotonic(),
                            ),
                        )
                    )
                except StateReadError:
                    continue
                if state.last_resolved_zero_command_id == identifier:
                    if state.last_zero_result == "applied":
                        self._active_parent_codes -= {
                            "ZERO_NOT_APPLIED",
                            "ZERO_RESULT_INDETERMINATE",
                        }
                        self._unresolved_zero_id = None
                        return ControlResult(
                            identifier,
                            "applied",
                            state.zero_resolved_monotonic_ns,
                            state.state_version,
                            "shared_state",
                        )
                    return self._zero_failure(
                        identifier, "confirmed_not_applied", state.state_version
                    )
                time.sleep(min(0.001, max(0, deadline - time.monotonic())))
            return self._zero_failure(identifier, "indeterminate", state.state_version)
        finally:
            self._lock.release()

    def close(self) -> None:
        """Drain/stop the child, release owned IPC and cache final state idempotently.

        Returns:
            None after orderly cleanup. LifecycleError reports forced/incomplete
            cleanup only after all attempts; cached health retains that failure.
        """
        self._close_requested.set()
        with self._lock:
            if self._closed:
                return
            errors = []
            final = self._last_state
            unexpected_dead = (
                self._process is not None
                and self._process.pid is not None
                and not self._process.is_alive()
                and not self._startup_failed
            )
            if self._channels:
                self._poll_status()
            stop_requested = False
            if self._process is not None and self._process.is_alive():
                stop_requested = True
                deadline = time.monotonic() + self.config.shutdown_timeout_s
                self._command_id += 1
                try:
                    self._controls.put_nowait(
                        ("STOP", self._command_id, int(deadline * 1e9))
                    )
                    result = self._wait_result("STOP", self._command_id, deadline - 0.5)
                    self._process.join(max(0, deadline - time.monotonic() - 0.4))
                    if result is None and self._process.is_alive():
                        errors.append("Acquisition did not acknowledge/complete STOP")
                except Exception as exc:
                    errors.append(str(exc))
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(0.2)
                    errors.append("Acquisition required forced termination")
                    if self._process.is_alive():
                        self._process.kill()
                        self._process.join(0.2)
                self._poll_status()
            if self._shared is not None:
                try:
                    final = self._shared.read()
                except StateReadError:
                    pass
            if self._final_worker_state is not None and (
                final is None
                or self._final_worker_state.state_version >= final.state_version
            ):
                final = self._final_worker_state
            if stop_requested and (final is None or final.health != "STOPPED"):
                errors.append("Acquisition did not publish an orderly final STOP state")
            if "SHUTDOWN_INCOMPLETE" in self._active_parent_codes:
                errors.append(
                    self._failure_message or "Worker reported incomplete cleanup"
                )
            if unexpected_dead and final is not None:
                causal_exit = bool(
                    set(final.active_acquisition_health_codes) & set(HEALTH_CODES[3:9])
                )
                if not causal_exit and final.health != "STOPPED":
                    self._observe("ACQUISITION_PROCESS_EXITED")
            if errors:
                self._observe("SHUTDOWN_INCOMPLETE")
            errors.extend(self._release_ipc())
            if self._process is not None and self._process.pid is not None:
                try:
                    self._process.close()
                except Exception as exc:
                    errors.append(str(exc))
            if errors:
                self._observe("SHUTDOWN_INCOMPLETE")
            self._closed = True
            if final is not None:
                if errors:
                    final = materialize_state(
                        final,
                        time.monotonic_ns(),
                        self.config,
                        worker_alive=False,
                        extra_codes=("SHUTDOWN_INCOMPLETE",),
                    )
                elif not self._startup_failed:
                    # An earlier unexpected worker loss must not become clean STOPPED.
                    if unexpected_dead:
                        final = materialize_state(
                            final,
                            time.monotonic_ns(),
                            self.config,
                            worker_alive=False,
                            extra_codes=tuple(self._active_parent_codes),
                        )
                    else:
                        final = materialize_state(
                            final, time.monotonic_ns(), self.config, stopped=True
                        )
                self._last_state = final
                for code, first_ns in zip(
                    HEALTH_CODES, final.observed_acquisition_code_times
                ):
                    if first_ns >= 0:
                        self._observed.setdefault(code, first_ns)
                for code in self._effective_codes(final):
                    self._observed.setdefault(code, time.monotonic_ns())
                self._last_report = self._report_from_state(final)
            elif not self._startup_failed:
                self._last_report = HealthReport(
                    "STOPPED",
                    False,
                    False,
                    None,
                    observed_health_codes=self._observed_order(),
                )
            if errors:
                raise LifecycleError("; ".join(errors))

    # Helpers: shared reads, bounded control, retained health and resource release.
    def _read_locked(
        self, timeout_s: float = COHERENT_STATE_READ_TIMEOUT_S
    ) -> TreadmillState:
        """Read IPC under the lifecycle lock; return effective immutable state."""
        if self._shared is None:
            raise UnavailableError(
                self._failure_message or "Acquisition has not started"
            )
        self._poll_status()
        try:
            state = self._shared.read(timeout_s=timeout_s)
        except StateReadError:
            self._observe("STATE_READ_TIMEOUT")
            raise
        self._active_parent_codes.discard("STATE_READ_TIMEOUT")
        if (
            self._unresolved_zero_id is not None
            and state.last_resolved_zero_command_id == self._unresolved_zero_id
        ):
            self._active_parent_codes.discard("ZERO_RESULT_INDETERMINATE")
            if state.last_zero_result == "applied":
                self._active_parent_codes.discard("ZERO_NOT_APPLIED")
            else:
                self._observe("ZERO_NOT_APPLIED")
            self._unresolved_zero_id = None
        for code, first_ns in zip(HEALTH_CODES, state.observed_acquisition_code_times):
            if first_ns >= 0:
                self._observed.setdefault(code, first_ns)
        causal_exit = bool(
            set(state.active_acquisition_health_codes) & set(HEALTH_CODES[3:9])
        )
        alive = self._process is not None and self._process.is_alive()
        effective = materialize_state(
            state,
            time.monotonic_ns(),
            self.config,
            worker_alive=alive or causal_exit,
            extra_codes=tuple(self._active_parent_codes),
        )
        if not alive:
            effective = replace(effective, acquisition_available=False)
        for code in self._effective_codes(effective):
            self._observed.setdefault(code, time.monotonic_ns())
        self._last_state = effective
        self._last_report = self._report_from_state(effective)
        return effective

    def _wait_result(
        self, kind: str, identifier: int, deadline: float
    ) -> ControlResult | None:
        """Return a matching control result by absolute monotonic seconds, or None."""
        while time.monotonic() < deadline:
            try:
                received_kind, result, final = self._results.get(
                    timeout=max(0, min(0.05, deadline - time.monotonic()))
                )
            except Empty:
                if self._process is not None and not self._process.is_alive():
                    return None
                continue
            if received_kind == kind and result.command_id == identifier:
                if kind == "STOP":
                    self._final_worker_state = final.state
                    self._first_failure_diagnostic = final.first_failure_diagnostic
                return result
        return None

    def _zero_failure(self, identifier: int, status: str, version: int):
        """Raise ZeroError with ID/status/version and update bounded parent health."""
        self._observe(
            "ZERO_NOT_APPLIED"
            if status == "confirmed_not_applied"
            else "ZERO_RESULT_INDETERMINATE"
        )
        self._unresolved_zero_id = identifier if status == "indeterminate" else None
        raise ZeroError(ControlResult(identifier, status, time.monotonic_ns(), version))

    def _observe(self, code: str) -> None:
        """Activate one parent code and retain its first monotonic ns; return None."""
        self._active_parent_codes.add(code)
        self._observed.setdefault(code, time.monotonic_ns())

    def _observe_failure(self, message) -> None:
        """Retain one primitive worker failure message without draining diagnostics."""
        self._observe(message[1])
        self._failure_message = message[2]

    def _poll_status(self) -> None:
        """Consume bounded parent status only; leave the logger diagnostic queue alone."""
        for _ in range(4):
            try:
                message = self._status.get_nowait()
            except Empty:
                break
            if message[0] == "failed":
                self._observe_failure(message)

    def _observed_order(self) -> tuple[str, ...]:
        """Return lifetime codes in first-observation order, then public priority."""
        return tuple(
            code
            for _, _, code in sorted(
                (timestamp, HEALTH_CODES.index(code), code)
                for code, timestamp in self._observed.items()
            )
        )

    def _effective_codes(self, state: TreadmillState) -> tuple[str, ...]:
        """Return active acquisition and parent codes in priority order."""
        codes = set(state.active_acquisition_health_codes) | self._active_parent_codes
        if state.failure_code:
            codes.add(state.failure_code)
        return ordered_codes(codes)

    def _report_from_state(self, state: TreadmillState) -> HealthReport:
        """Convert a scalar state to immutable health, retaining lifetime codes."""
        return HealthReport(
            state.health,
            state.integrity_valid,
            state.acquisition_available,
            state.failure_code,
            self._effective_codes(state),
            self._observed_order(),
            self._failure_message,
        )

    def _check_open(self) -> None:
        """Reject operations after the close request; return None or raise."""
        if self._close_requested.is_set():
            raise LifecycleError("Treadmill is closing or closed")

    def _check_closing_reader(self) -> None:
        """Reject uncached reads during close without touching released IPC."""
        if self._close_requested.is_set() and not self._closed:
            raise LifecycleError("Treadmill is closing")

    def _closing_report(self) -> HealthReport:
        """Return cached close health only; no locks or IPC are touched."""
        if self._closed or self._last_report.health == "FAILED":
            return self._last_report
        return replace(self._last_report, health="CLOSING", failure_code=None)

    def _stop_failed_start(self) -> None:
        """Join/terminate a failed startup before optional return; release IPC."""
        if self._process is not None and self._process.pid is not None:
            self._process.join(self.config.shutdown_timeout_s)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(0.2)
                self._observe("SHUTDOWN_INCOMPLETE")
        if self._shared is not None:
            try:
                self._last_state = self._shared.read()
            except StateReadError:
                pass
        if self._channels:
            for _ in range(self.config.diagnostic_event_queue_size):
                try:
                    self._startup_diagnostics.append(self._diagnostics.get_nowait())
                except Empty:
                    break
        self._startup_diagnostics.append(
            {
                "monotonic_ns": time.monotonic_ns(),
                "code": ordered_codes(self._active_parent_codes)[0],
                "message": self._failure_message,
            }
        )
        if self._release_ipc():
            self._observe("SHUTDOWN_INCOMPLETE")

    def _release_ipc(self) -> list[str]:
        """Attempt every queue close and release references; return ordered errors."""
        errors = []
        for channel in self._channels:
            try:
                channel.cancel_join_thread()
                channel.close()
            except Exception as exc:
                errors.append(str(exc))
        self._channels.clear()
        self._shared = None
        self._controls = self._results = self._diagnostics = self._status = (
            self._failure_ring
        ) = None
        return errors


def _not_recording() -> bool:
    """Return False for standalone acquisition without a shared IO recorder."""
    return False
