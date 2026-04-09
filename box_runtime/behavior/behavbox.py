# python3: behavbox.py
"""
author: tian qiu
date: 2022-05-15
name: behavbox.py
goal: base framework for running wide range of behavioral task
description:
    an updated test version for online behavior performance visualization

"""

# contains the behavior box class, which includes pin numbers and whether DIO pins are
# configured as input or output

import os
import platform
import socket
import time
from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

import logging
from colorama import Fore, Style
from typing import Callable, Optional

from box_runtime.behavior.plotting_support import (
    detect_plotting_dependencies,
    detect_desktop_session,
    import_plotting_modules,
    require_plotting_ready,
)
from box_runtime.behavior.gpio_backend import (
    is_raspberry_pi,
    set_audio_state,
    set_camera_state,
    set_plot_state,
    set_session_state,
    set_task_state,
    set_visual_stim_state,
)
from box_runtime.audio.importer import AudioPaths
from box_runtime.audio.runtime import RecordingPlaybackBackend, SoundRuntime
from box_runtime.io_manifest import load_box_profile
from box_runtime.io_recording import SharedIoRecorder
from box_runtime.input import InputService
from box_runtime.output import OutputService
from box_runtime.video_recording.local_camera_runtime import CameraManager
from box_runtime.visual_stimuli.visual_runtime.drm_runtime import query_display_config

try:
    from box_runtime.behavior import ADS1x15
except Exception:
    ADS1x15 = None

PLOTTING_AVAILABLE = detect_plotting_dependencies().ok
pygame = None
plt = None
fg = None

@dataclass(frozen=True)
class BehaviorEvent:
    """Event emitted by hardware callbacks and consumed by task code."""

    name: str
    timestamp: float


class BehavBox(object):
    event_list = (
        deque()
    )  # all detected events are added to this queue to be read out by the behavior class

    def __init__(
        self,
        session_info,
        *,
        sound_runtime_factory: Optional[Callable[["BehavBox"], SoundRuntime]] = None,
        camera_manager_factory: Optional[Callable[["BehavBox"], CameraManager]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        """Construct one BehavBox appliance wrapper without starting a session.

        Args:
            session_info: Mapping-like session configuration. Filesystem paths
                are expected under ``dir_name`` and ``external_storage``.
            sound_runtime_factory: Optional factory returning the long-lived
                ``SoundRuntime`` instance for this box.
            camera_manager_factory: Optional factory returning the local
                one-Pi ``CameraManager`` instance for this box.
            clock: Optional zero-argument wall-clock callable returning POSIX
                seconds as ``float``.
        """

        self.session_info = session_info
        self._clock = clock or time.time
        self._sound_runtime_factory = sound_runtime_factory
        self._camera_manager_factory = camera_manager_factory
        self._lifecycle_state = "created"
        self._session_started_at_s: Optional[float] = None
        self._session_stopped_at_s: Optional[float] = None
        self._prepared_session_dir: Optional[Path] = None
        self._runtime_events: list[dict[str, object]] = []
        self._logging_configured = False
        self._is_closed = False
        self.runtime_status = {
            "session": {
                "active": False,
                "lifecycle_state": "created",
                "protocol_name": None,
                "box_name": self.session_info.get("box_name"),
            },
            "task": {
                "protocol_name": None,
                "phase": None,
                "trial_index": None,
                "trial_type": None,
                "completed_trials": 0,
                "max_trials": None,
                "stimulus_active": False,
            },
            "audio": {
                "active": False,
                "current_cue_name": None,
                "last_cue_name": None,
            },
            "camera": {},
            "plot": {
                "kind": "gonogo_performance",
                "trial_outcomes": [],
                "counts": {
                    "completed_trials": 0,
                    "hits": 0,
                    "misses": 0,
                    "false_alarms": 0,
                    "correct_rejects": 0,
                },
                "rates": {
                    "hit_rate": None,
                    "false_alarm_rate": None,
                },
            },
        }
        self.event_list = deque()
        self.interact_list = []
        self.box_profile = str(self.session_info.get("box_profile") or self.session_info.get("input_profile") or "head_fixed").lower()
        self.box_manifest = load_box_profile(self.box_profile)

        self.sound_runtime = None
        self.input_service = None
        self.output_service = None
        self.io_recorder = None
        self.visualstim = None
        self.camera_manager = None
        self.ADC = None
        self.keyboard_active = False
        self.main_display = None
        self._pygame = None
        self._plt = None
        self._fg = None
        self.plotting_status = {
            "dependencies_ok": PLOTTING_AVAILABLE,
            "desktop_session_ok": False,
            "display_env": None,
            "required": bool(self.session_info.get("plotting_required", False)),
            "probe_ok": False,
            "failure_reason": None,
        }
        self.user_output = None
        self.DIO5 = None
        self.DIO4 = None
        self.IR_rx1 = None
        self.IR_rx2 = None
        self.IR_rx3 = None
        self.IR_rx4 = None
        self.IR_rx5 = None
        self.lick1 = None
        self.lick2 = None
        self.lick3 = None
        self.lick_left = None
        self.lick_right = None
        self.lick_center = None
        self.ir_lick_left = None
        self.ir_lick_right = None
        self.ir_lick_center = None
        self.trigger_in = None
        self.trigger_out = None
        self.ttl_trigger = None
        self.treadmill_input_1 = None
        self.treadmill_input_2 = None
        self.treadmill_encoder = None
        self.poke_left = None
        self.poke_right = None
        self.poke_center = None
        self.poke_extra1 = None
        self.poke_extra2 = None
        self.cueLED1 = None
        self.cueLED2 = None
        self.cueLED3 = None
        self.cueLED4 = None
        self.cueLED5 = None
        self.cueLED6 = None

        try:
            if platform.system() == "Linux":
                from subprocess import check_output
                ip_output = check_output(['hostname', '-I']).decode('ascii').strip()
                self.IP_address = ip_output.split()[0] if ip_output else socket.gethostbyname(socket.gethostname())
            else:
                self.IP_address = socket.gethostbyname(socket.gethostname())
        except Exception:
            try:
                self.IP_address = socket.gethostbyname(socket.gethostname())
            except Exception:
                self.IP_address = "127.0.0.1"

        self.IP_address_video = str(self.session_info.get("camera_host", "127.0.0.1"))
        self.camera_service_port = int(os.environ.get("CAMERA_SERVICE_PORT", "8000"))

    def _require_lifecycle(self, *allowed_states: str) -> None:
        if self._lifecycle_state not in allowed_states:
            raise RuntimeError(
                f"BehavBox lifecycle error: current state is {self._lifecycle_state!r}, "
                f"expected one of {allowed_states!r}."
            )

    def _set_lifecycle_state(self, lifecycle_state: str, *, active: bool) -> None:
        """Update the authoritative lifecycle state and published session state.

        Args:
            lifecycle_state: New BehavBox lifecycle state string.
            active: Whether the session should be reported as active.

        Returns:
            ``None``.
        """

        self._lifecycle_state = str(lifecycle_state)
        self.publish_runtime_state(
            "session",
            active=bool(active),
            lifecycle_state=self._lifecycle_state,
            box_name=self.session_info.get("box_name"),
        )

    def _drain_runtime_events(self) -> list[BehaviorEvent]:
        """Drain queued runtime events in first-in-first-out order.

        Returns:
            list[BehaviorEvent]: Events drained from ``event_list``.
        """

        drained_events: list[BehaviorEvent] = []
        while self.event_list:
            drained_events.append(self.event_list.popleft())
        return drained_events

    def _configure_logging(self) -> None:
        session_dir = Path(self.session_info["dir_name"])
        session_dir.mkdir(parents=True, exist_ok=True)
        file_basename = session_dir / f'{self.session_info["mouse_name"]}_{self.session_info["datetime"]}'
        self.session_info["file_basename"] = str(file_basename)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s.%(msecs)03d,[%(levelname)s],%(message)s",
            datefmt="%H:%M:%S",
            handlers=[
                logging.FileHandler(str(file_basename) + ".log"),
                logging.StreamHandler(),
            ],
            force=True,
        )
        self._logging_configured = True
        logging.info(";%s;[initialization];behavior_box_initialized", self._clock())

    def publish_runtime_state(self, section: str, **values) -> None:
        """Publish one generic runtime-state update.

        Args:
            section: Runtime-state section name, typically ``session``,
                ``task``, or ``audio``.
            values: JSON-serializable key/value updates for that section.
        """

        if section not in self.runtime_status:
            self.runtime_status[section] = {}
        self.runtime_status[section].update(values)
        if section == "session":
            set_session_state(**values)
        elif section == "task":
            set_task_state(**values)
        elif section == "audio":
            set_audio_state(**values)
        elif section == "camera":
            set_camera_state(**values)
        elif section == "plot":
            set_plot_state(**values)

    def _handle_audio_runtime_state(self, payload: dict[str, object]) -> None:
        """Receive audio-runtime state updates from the playback layer."""

        self.publish_runtime_state("audio", **payload)

    def _handle_camera_runtime_state(self, payload: dict[str, object]) -> None:
        """Receive per-camera runtime-state updates from the camera manager."""

        self.publish_runtime_state("camera", **payload)

    def _build_camera_manager(self) -> CameraManager | None:
        """Create the local one-Pi camera manager when camera support is enabled."""

        if not bool(self.session_info.get("camera_enabled", False)):
            self._handle_camera_runtime_state({})
            return None
        if self._camera_manager_factory is not None:
            return self._camera_manager_factory(self)
        return CameraManager(
            self.session_info,
            state_callback=self._handle_camera_runtime_state,
        )

    def validate_media_config(self) -> None:
        """Validate one-Pi visual and local camera preview configuration.

        Raises:
            ValueError: If the requested display topology is unsupported or
                connector discovery fails for the visual stimulus display.
        """

        visual_enabled = bool(self.session_info.get("visual_stimulus", False))
        visual_connector = str(self.session_info.get("visual_display_connector", "HDMI-A-2")).strip() or "HDMI-A-2"
        if visual_enabled and visual_connector != "HDMI-A-2":
            raise ValueError("Visual stimulus must use HDMI-A-2 in the supported one-Pi topology.")

        preview_modes = self.session_info.get(
            "camera_preview_modes",
            self.session_info.get("camera_preview_mode", "off"),
        )
        camera_ids = self.session_info.get("camera_ids", ["camera0"])
        if isinstance(camera_ids, str):
            camera_ids = [camera_ids]
        preview_camera_ids = []
        for camera_id in camera_ids:
            preview_mode = (
                str(preview_modes.get(camera_id, "off")).strip().lower()
                if isinstance(preview_modes, dict)
                else str(preview_modes).strip().lower()
            )
            if preview_mode == "drm_local":
                preview_camera_ids.append(str(camera_id))
        if len(preview_camera_ids) > 1:
            raise ValueError("Only one drm_local camera preview is supported in the current one-Pi topology.")
        if preview_camera_ids:
            preview_connector = str(self.session_info.get("camera_preview_connector", "HDMI-A-1")).strip() or "HDMI-A-1"
            if preview_connector != "HDMI-A-1":
                raise ValueError("Local camera preview must use HDMI-A-1 in the supported one-Pi topology.")
            if visual_enabled and preview_connector == visual_connector:
                raise ValueError("Visual stimulus and local camera preview cannot share the same DRM connector.")

        if visual_enabled:
            visual_backend = str(
                self.session_info.get(
                    "visual_display_backend",
                    self.session_info.get("visual_backend", "drm" if is_raspberry_pi() else "fake"),
                )
            ).lower()
            query_display_config(
                backend=visual_backend,
                requested_resolution_px=None,
                requested_refresh_hz=self.session_info.get("visual_display_refresh_hz"),
                requested_connector=visual_connector,
            )

    def _prepare_visual_runtime(self) -> None:
        visual_enabled = bool(self.session_info.get("visual_stimulus", False))
        set_visual_stim_state(
            visual_stim_enabled=visual_enabled,
            visual_stim_active=False,
            current_grating=None,
        )
        if not visual_enabled:
            self.visualstim = None
            return
        try:
            if is_raspberry_pi():
                from box_runtime.visual_stimuli.visualstim import VisualStim

                self.visualstim = VisualStim(self.session_info)
            else:
                from box_runtime.mock_hw.visual_stim import MockVisualStim

                self.visualstim = MockVisualStim(self.session_info)
        except Exception as error_message:
            print("visualstim issue\n")
            print(str(error_message))
            self.visualstim = None

    def _prepare_adc(self) -> None:
        if ADS1x15 is not None:
            try:
                self.ADC = ADS1x15.ADS1015
            except Exception as error_message:
                print("ADC issue\n")
                print(str(error_message))
                self.ADC = None
        else:
            print("ADC module unavailable; continuing without ADC support.")
            self.ADC = None

    def _prepare_keyboard(self) -> None:
        self.keyboard_active = False
        self.main_display = None
        self._pygame = None
        self._plt = None
        self._fg = None
        plotting_required = bool(self.session_info.get("plotting_required", False))
        dependency_status = detect_plotting_dependencies()
        desktop_status = detect_desktop_session()
        self.plotting_status.update(
            {
                "dependencies_ok": dependency_status.ok,
                "desktop_session_ok": desktop_status.ok,
                "display_env": desktop_status.display_env,
                "required": plotting_required,
                "probe_ok": False,
                "failure_reason": None,
            }
        )
        if not dependency_status.ok:
            message = "Pygame/matplotlib plotting unavailable; keyboard simulation disabled."
            print(message)
            self.plotting_status["failure_reason"] = dependency_status.reason
            if plotting_required:
                raise RuntimeError(dependency_status.reason)
            return
        if not desktop_status.ok:
            message = "Desktop plotting unavailable; keyboard simulation disabled."
            print(message)
            self.plotting_status["failure_reason"] = desktop_status.reason
            if plotting_required:
                raise RuntimeError(desktop_status.reason)
            return
        try:
            require_plotting_ready(repo_root=Path(__file__).resolve().parents[2], timeout_s=10.0)
            self._pygame, self._plt, self._fg = import_plotting_modules()
            self._pygame.init()
            self.main_display = self._pygame.display.set_mode((800, 600))
            self._pygame.display.set_caption(self.session_info["box_name"])
            fig, axes = self._plt.subplots(1, 1)
            axes.plot()
            self.check_plot(fig)
            print(
                "\nKeystroke handler initiated. In order for keystrokes to register, the pygame window"
            )
            print("must be in the foreground. Keys are as follows:\n")
            print(Fore.YELLOW + "         1: left poke            2: center poke            3: right poke")
            print("         Q: pump_1            W: pump_2            E: pump_3            R: pump_4")
            print(Fore.CYAN + "                       Esc: close key capture window\n" + Style.RESET_ALL)
            print(
                Fore.GREEN
                + Style.BRIGHT
                + "         TO EXIT, CLICK THE MAIN TEXT WINDOW AND PRESS CTRL-C "
                + Fore.RED
                + "ONCE\n"
                + Style.RESET_ALL
            )
            self.keyboard_active = True
            self.plotting_status["probe_ok"] = True
        except Exception as error_message:
            print("pygame issue\n")
            print(str(error_message))
            self.main_display = None
            self.plotting_status["failure_reason"] = str(error_message)
            if plotting_required:
                raise

    def prepare_session(self) -> None:
        """Prepare all long-lived runtime resources for one upcoming session."""

        self._require_lifecycle("created")
        try:
            self._configure_logging()
            self._prepared_session_dir = Path(self.session_info["dir_name"])
            self.validate_media_config()
            self.sound_runtime = self._build_sound_runtime()
            self.io_recorder = SharedIoRecorder(self.session_info)
            self.output_service = OutputService(self, self.session_info, self.box_manifest, self.io_recorder)
            self.input_service = InputService(self, self.session_info, self.box_manifest, self.io_recorder)
            self.camera_manager = self._build_camera_manager()
            if self.camera_manager is not None:
                self.camera_manager.prepare()
            self._prepare_visual_runtime()
            self._prepare_adc()
            self._prepare_keyboard()
            self._runtime_events.append({"name": "session_prepared", "timestamp": self._clock()})
            self._is_closed = False
            self._set_lifecycle_state("prepared", active=False)
        except Exception:
            self.close()
            raise

    def start_session(self) -> None:
        """Transition the appliance from prepared to active session state."""

        self._require_lifecycle("prepared")
        self.start_task_recording()
        try:
            if self.camera_manager is not None:
                self.camera_manager.start_session(owner="automated")
            self._session_started_at_s = self._clock()
            self._runtime_events.append({"name": "session_started", "timestamp": self._session_started_at_s})
            self._handle_input_event("session_started", record_interaction=False, log_category="configuration")
            self._set_lifecycle_state("running", active=True)
        except Exception:
            self.stop_task_recording()
            raise

    def poll_runtime(self) -> list[BehaviorEvent]:
        """Run lightweight non-task-specific runtime work and drain current events."""

        self._require_lifecycle("prepared", "running")
        if self._lifecycle_state == "prepared":
            return []
        self.check_keybd()
        return self._drain_runtime_events()

    def stop_session(self) -> None:
        """Leave the active session state and stop task-owned recording."""

        self._require_lifecycle("running")
        if self.camera_manager is not None:
            self.camera_manager.stop_session()
        self.stop_task_recording()
        self.stop_sound()
        self._session_stopped_at_s = self._clock()
        self._runtime_events.append({"name": "session_stopped", "timestamp": self._session_stopped_at_s})
        self._handle_input_event("session_stopped", record_interaction=False, log_category="configuration")
        self._set_lifecycle_state("stopped", active=False)

    def finalize_session(self) -> Path:
        """Write standardized session metadata after the run has stopped."""

        self._require_lifecycle("stopped")
        session_dir = Path(self.session_info["dir_name"])
        session_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = session_dir / "session_metadata.json"
        payload = {
            "session_info": self.session_info,
            "runtime_events": list(self._runtime_events),
            "session_started_at_s": self._session_started_at_s,
            "session_stopped_at_s": self._session_stopped_at_s,
        }
        metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self._set_lifecycle_state("finalized", active=False)
        self.publish_runtime_state("audio", active=False, current_cue_name=None)
        return metadata_path

    @staticmethod
    def event_name(event: object) -> str:
        """Compatibility helper: returns event name from object or legacy string."""
        if isinstance(event, BehaviorEvent):
            return event.name
        if isinstance(event, str):
            return event
        if isinstance(event, dict):
            name = event.get("name")
            return str(name) if name is not None else ""
        return str(event)

    @staticmethod
    def event_timestamp(event: object) -> Optional[float]:
        """Compatibility helper: returns wall-clock timestamp if available."""
        if isinstance(event, BehaviorEvent):
            return event.timestamp
        if isinstance(event, dict):
            value = event.get("timestamp")
            if isinstance(value, (int, float)):
                return float(value)
            return None
        return None

    def _push_event(self, event_name: str) -> BehaviorEvent:
        event = BehaviorEvent(name=event_name, timestamp=self._clock())
        self.event_list.append(event)
        return event

    def _handle_input_event(
        self,
        event_name: str,
        *,
        record_interaction: bool = True,
        log_category: str = "action",
    ) -> BehaviorEvent:
        """Emit one runtime input event and mirror it into current input artifacts.

        Args:
            event_name: Canonical event name string.
            record_interaction: Whether to append the event to ``interact_list``.
            log_category: Legacy log category token written to the text log.

        Returns:
            Minimal ``BehaviorEvent`` with wall-clock timestamp.
        """

        event = self._push_event(event_name)
        if record_interaction:
            self.interact_list.append((event.timestamp, event.name))
        logging.info(";%s;[%s];%s", event.timestamp, log_category, event.name)
        if getattr(self, "input_service", None) is not None:
            self.input_service.record_event(event, log_category=log_category)
        return event

    def _build_sound_runtime(self) -> SoundRuntime:
        """Create the persistent audio runtime for named cue playback.

        Returns:
            SoundRuntime configured with the repository audio directories.
        """

        if self._sound_runtime_factory is not None:
            return self._sound_runtime_factory(self)

        audio_root = Path(__file__).resolve().parents[1] / "audio"
        paths = AudioPaths(
            tracked_sounds_dir=audio_root / "sounds",
            local_source_dir=audio_root / "local_source_wavs",
            local_sounds_dir=audio_root / "local_sounds",
        )
        device_name = str(self.session_info.get("audio_device", os.environ.get("BEHAVBOX_AUDIO_DEVICE", "default")))
        use_mock_audio = bool(self.session_info.get("mock_audio", False)) or not is_raspberry_pi()
        backend = (
            RecordingPlaybackBackend(
                sample_rate_hz=48_000,
                chunk_sleep_s=256.0 / 48_000.0,
            )
            if use_mock_audio
            else None
        )
        return SoundRuntime(
            paths=paths,
            device_name=device_name,
            backend=backend,
            state_callback=self._handle_audio_runtime_state,
        )

    def configure_user_output(self, label: str = "ttl_output"):
        """Hand off the default TTL pin to output ownership.

        Args:
            label: Registry/UI label for the output-side pin ownership.

        Returns:
            ``DigitalOutputDevice`` bound to the former TTL input pin.
        """

        if self.output_service is None:
            raise RuntimeError("Output runtime is unavailable before prepare_session().")
        return self.output_service.configure_user_output(label=label)

    def configure_user_input(
        self,
        label: str = "user_input",
        pull_up=None,
        active_state: bool = True,
    ):
        """Claim the generic user-configurable GPIO4 line as an input.

        Args:
            label: Ignored compatibility label.
            pull_up: Unused compatibility argument.
            active_state: Unused compatibility argument.

        Returns:
            The active user-configurable ``Button`` instance.
        """

        del pull_up, active_state
        if self.input_service is None:
            raise RuntimeError("Input runtime is unavailable before prepare_session().")
        return self.input_service.configure_user_input(label=label)

    def import_wav_file(
        self,
        source_name: str,
        cue_name: Optional[str] = None,
        overwrite: bool = False,
        max_duration_s: float = 10.0,
        allow_longer: bool = False,
    ) -> Path:
        """Import a source WAV into the local canonical cue directory.

        Args:
            source_name: Source basename resolved under the local source-waveform
                directory.
            cue_name: Optional canonical cue basename.
            overwrite: Whether an existing canonical cue may be replaced.
            max_duration_s: Maximum imported duration in seconds when
                ``allow_longer`` is ``False``.
            allow_longer: Whether to preserve source duration beyond
                ``max_duration_s``.

        Returns:
            Path to the canonical WAV written by the importer.
        """

        return self.sound_runtime.import_wav_file(
            source_name=source_name,
            cue_name=cue_name,
            overwrite=overwrite,
            max_duration_s=max_duration_s,
            allow_longer=allow_longer,
        )

    def load_sound(self, name: str):
        """Load one canonical cue into random-access memory (RAM).

        Args:
            name: Canonical cue basename with or without ``.wav`` suffix.

        Returns:
            LoadedSound prepared for playback.
        """

        return self.sound_runtime.load_sound(name)

    def clear_sounds(self) -> None:
        """Release all loaded cues from random-access memory (RAM)."""

        self.sound_runtime.clear_sounds()

    def play_sound(
        self,
        name: str,
        side: str = "both",
        gain_db: float = 0.0,
        duration_s: Optional[float] = None,
    ) -> None:
        """Play a named cue through the persistent audio runtime.

        Args:
            name: Loaded cue basename.
            side: Playback side, one of ``"left"``, ``"right"``, or ``"both"``.
            gain_db: Playback gain in decibels.
            duration_s: Optional requested duration in seconds.

        Returns:
            ``None``.
        """

        self.sound_runtime.play_sound(
            name=name,
            side=side,
            gain_db=gain_db,
            duration_s=duration_s,
        )

    def register_noise_cue(self, name: str, duration_s: float, seed: int = 0) -> None:
        """Create or replace one generated white-noise cue in the audio runtime.

        Args:
            name: Cue identifier without requiring a filesystem-backed WAV.
            duration_s: Cue duration in seconds.
            seed: Deterministic random seed for reproducible waveform content.
        """

        self.sound_runtime.register_white_noise(name=name, duration_s=duration_s, seed=seed)

    def stop_sound(self) -> None:
        """Stop the currently playing cue, if any."""

        self.sound_runtime.stop_sound()

    def start_sound_calibration(self, side: str = "both", gain_db: float = 0.0) -> None:
        """Start continuous white-noise playback for speaker calibration."""

        self.sound_runtime.start_sound_calibration(side=side, gain_db=gain_db)

    def stop_sound_calibration(self) -> None:
        """Stop the calibration playback mode."""

        self.sound_runtime.stop_sound_calibration()

    def measure_sound_latency(
        self,
        name: str,
        side: str = "both",
        gain_db: float = 0.0,
        repeats: int = 3,
    ) -> list[float]:
        """Measure loopback latency for a loaded cue.

        Args:
            name: Loaded cue basename.
            side: Playback side.
            gain_db: Playback gain in decibels.
            repeats: Number of repeated measurements.

        Returns:
            List of latency values in milliseconds.
        """

        return self.sound_runtime.measure_sound_latency(
            name=name,
            side=side,
            gain_db=gain_db,
            repeats=repeats,
        )

    def deliver_reward(self, output_name: str = "reward_center", reward_size_ul: Optional[float] = None) -> None:
        """Deliver liquid reward through a stable named output path.

        Args:
            output_name: One of ``reward_left``, ``reward_right``, or
                ``reward_center``.
            reward_size_ul: Reward amount in microliters.
        """

        if self.output_service is None:
            raise RuntimeError("Output runtime is unavailable before prepare_session().")
        reward_size = float(self.session_info.get("reward_size", 50) if reward_size_ul is None else reward_size_ul)
        compatibility_lookup = {
            "1": "reward_left",
            "2": "reward_right",
            "3": "reward_center",
            "4": "reward_4",
        }
        canonical_output = compatibility_lookup.get(str(output_name), str(output_name))
        self.output_service.deliver_reward(canonical_output, reward_size_ul=reward_size)

    def pulse_output(self, output_name: str, duration_s: Optional[float] = None) -> None:
        """Pulse one named GPIO output.

        Args:
            output_name: Canonical output name or supported alias.
            duration_s: Optional pulse duration in seconds.
        """

        if self.output_service is None:
            raise RuntimeError("Output runtime is unavailable before prepare_session().")
        self.output_service.pulse_output(output_name, duration_s=duration_s)

    def set_output(self, output_name: str, active: bool) -> None:
        """Set one named GPIO output high or low."""

        if self.output_service is None:
            raise RuntimeError("Output runtime is unavailable before prepare_session().")
        self.output_service.set_output(output_name, active)

    def toggle_output(self, output_name: str) -> None:
        """Toggle one named GPIO output."""

        if self.output_service is None:
            raise RuntimeError("Output runtime is unavailable before prepare_session().")
        self.output_service.toggle_output(output_name)

    def close(self) -> None:
        """Close long-lived runtime resources owned by BehavBox.

        Returns:
            ``None``.
        """

        if self._is_closed:
            return
        if getattr(self, "input_service", None) is not None:
            self.input_service.close()
            self.input_service = None
        if getattr(self, "output_service", None) is not None:
            self.output_service.close()
            self.output_service = None
        if getattr(self, "io_recorder", None) is not None:
            self.io_recorder.close()
            self.io_recorder = None
        if getattr(self, "sound_runtime", None) is not None:
            self.sound_runtime.close()
            self.sound_runtime = None
        if getattr(self, "camera_manager", None) is not None:
            self.camera_manager.close()
            self.camera_manager = None
        if getattr(self, "visualstim", None) is not None and hasattr(self.visualstim, "close"):
            try:
                self.visualstim.close()
            except Exception:
                pass
            self.visualstim = None
        if self._pygame is not None:
            try:
                self._pygame.quit()
            except Exception:
                pass
        self.keyboard_active = False
        self.main_display = None
        self._pygame = None
        self._plt = None
        self._fg = None
        self._is_closed = True
        self._set_lifecycle_state("closed", active=False)
        self.publish_runtime_state("task", phase=None, stimulus_active=False)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
    ###############################################################################################
    # check for data visualization - uses pygame window to show behavior progress
    ###############################################################################################
    """
    1. show a blank window. (change in the pygame initiation part)
    2. show a x,y axis with a count of trial
    """
    def check_plot(self, figure=None, FPS=144):
        if figure is not None and self._pygame is not None and self.main_display is not None:
            FramePerSec = self._pygame.time.Clock()
            figure.canvas.draw()
            self.main_display.blit(figure, (0, 0))
            self._pygame.display.update()
            FramePerSec.tick(FPS)
        else:
            print("No figure available")

    ###############################################################################################
    # check for key presses - uses pygame window to simulate nosepokes and licks
    ###############################################################################################

    def check_keybd(self):
        if self.keyboard_active and self._pygame is not None:
            # event = pygame.event.get()
            for event in self._pygame.event.get():
                if event.type == self._pygame.KEYDOWN:
                    if event.key == self._pygame.K_ESCAPE:
                        self.keyboard_active = False
                    elif event.key == self._pygame.K_1:
                        self.left_entry()
                        logging.info(";" + str(time.time()) + ";[action];key_pressed_left_entry()")
                    elif event.key == self._pygame.K_2:
                        self.center_entry()
                        logging.info(";" + str(time.time()) + ";[action];key_pressed_center_entry()")
                    elif event.key == self._pygame.K_3:
                        self.right_entry()
                        logging.info(";" + str(time.time()) + ";[action];key_pressed_right_entry()")
                    # elif event.key == pygame.K_4:
                    #     self.reserved_rx1_pressed()
                    #     logging.info(";" + str(time.time()) + ";[action];key_pressed_reserved_rx1_pressed()")
                    # elif event.key == pygame.K_5:
                    #     self.reserved_rx2_pressed()
                    #     logging.info(";" + str(time.time()) + ";[action];key_pressed_reserved_rx2_pressed()")
                    elif event.key == self._pygame.K_q:
                        self.deliver_reward("reward_left", self.session_info["key_reward_amount"])
                    elif event.key == self._pygame.K_w:
                        self.deliver_reward("reward_right", self.session_info["key_reward_amount"])
                    elif event.key == self._pygame.K_e:
                        self.deliver_reward("reward_center", self.session_info["key_reward_amount"])
                    elif event.key == self._pygame.K_r:
                        self.deliver_reward("reward_4", self.session_info["key_reward_amount"])
                    elif event.key == self._pygame.K_t:
                        self.pulse_output("vacuum")
                elif event.type == self._pygame.KEYUP:
                    if event.key == self._pygame.K_1:
                        self.left_exit()
                    elif event.key == self._pygame.K_2:
                        self.center_exit()
                    elif event.key == self._pygame.K_3:
                        self.right_exit()

    ###############################################################################################
    # methods to start and stop local camera recording / preview
    ###############################################################################################
    def start_camera_recording(self, camera_id: str = "camera0") -> None:
        """Start one local camera recording by semantic camera identifier.

        Args:
            camera_id: Camera identifier string such as ``"camera0"``.
        """

        if self.camera_manager is None:
            raise RuntimeError("Camera runtime is unavailable before prepare_session().")
        self.camera_manager.start_recording(camera_id=camera_id, owner="automated")

    def stop_camera_recording(self, camera_id: str = "camera0") -> None:
        """Stop one local camera recording by semantic camera identifier."""

        if self.camera_manager is None:
            raise RuntimeError("Camera runtime is unavailable before prepare_session().")
        self.camera_manager.stop_recording(camera_id=camera_id)

    def start_camera_preview(self, camera_id: str = "camera0") -> None:
        """Start one local camera preview by semantic camera identifier."""

        if self.camera_manager is None:
            raise RuntimeError("Camera runtime is unavailable before prepare_session().")
        self.camera_manager.start_preview(camera_id=camera_id)

    def stop_camera_preview(self, camera_id: str = "camera0") -> None:
        """Stop one local camera preview by semantic camera identifier."""

        if self.camera_manager is None:
            raise RuntimeError("Camera runtime is unavailable before prepare_session().")
        self.camera_manager.stop_preview(camera_id=camera_id)

    def video_start(self):
        """Compatibility wrapper that starts the default local camera recording."""

        self.start_camera_recording("camera0")

    def video_stop(self):
        """Compatibility wrapper that stops the default local camera recording."""

        self.stop_camera_recording("camera0")

    def start_recording(self) -> str:
        """Start standalone input recording under user ownership.

        Returns:
            Absolute path to the active input-recording directory.
        """

        return self._start_shared_recording(owner="user")

    def stop_recording(self) -> dict[str, object]:
        """Clear standalone user recording demand.

        Returns:
            Status dictionary describing whether recording stopped or deferred.
        """

        return self._stop_shared_recording(owner="user")

    def start_task_recording(self) -> str:
        """Assert task-owned input recording using the active session directory.

        Returns:
            Absolute path to the active input-recording directory.
        """

        return self._start_shared_recording(
            owner="task",
            task_dir=self.session_info["dir_name"],
        )

    def stop_task_recording(self) -> dict[str, object]:
        """Clear task-owned input-recording demand.

        Returns:
            Status dictionary describing whether recording stopped or remained active.
        """

        return self._stop_shared_recording(owner="task")

    def _start_shared_recording(self, owner: str, task_dir: str | None = None) -> str:
        """Start shared input/output recording and attach treadmill sampling."""

        if self.io_recorder is None or self.input_service is None:
            raise RuntimeError("Recording runtime is unavailable before prepare_session().")
        recording_dir, started_now = self.io_recorder.start_recording(owner=owner, task_dir=task_dir)
        if started_now:
            self.input_service.on_recording_started()
            self._handle_input_event("input_recording_started", record_interaction=False, log_category="configuration")
        return recording_dir

    def _stop_shared_recording(self, owner: str) -> dict[str, object]:
        """Stop shared input/output recording when no owner still demands it."""

        if self.io_recorder is None or self.input_service is None:
            raise RuntimeError("Recording runtime is unavailable before prepare_session().")
        status = self.io_recorder.stop_recording(owner=owner)
        if status["status"] == "deferred":
            self._handle_input_event("input_recording_stop_deferred", record_interaction=False, log_category="warning")
            return status
        if status["status"] != "stop_pending":
            return status
        self._handle_input_event("input_recording_stopped", record_interaction=False, log_category="configuration")
        self.input_service.on_recording_stopped()
        return self.io_recorder.finalize_stop()

    ###############################################################################################
    # callbacks
    ###############################################################################################
    def left_entry(self):
        self._handle_input_event("left_entry")

    def center_entry(self):
        self._handle_input_event("center_entry")

    def right_entry(self):
        self._handle_input_event("right_entry")

    def left_exit(self):
        self._handle_input_event("left_exit")

    def center_exit(self):
        self._handle_input_event("center_exit")

    def right_exit(self):
        self._handle_input_event("right_exit")

    def treadmill_1_entry(self):
        self._handle_input_event("treadmill_1_entry")

    def treadmill_1_exit(self):
        self._handle_input_event("treadmill_1_exit")

    def treadmill_2_entry(self):
        self._handle_input_event("treadmill_2_entry")

    def treadmill_2_exit(self):
        self._handle_input_event("treadmill_2_exit")

    # def reserved_rx1_pressed(self):
    #     self.event_list.append("reserved_rx1_pressed")
    #     self.interact_list.append((time.time(), "reserved_rx1_pressed"))
    #     logging.info(";" + str(time.time()) + ";[action];reserved_rx1_pressed")
    #
    # def reserved_rx2_pressed(self):
    #     self.event_list.append("reserved_rx2_pressed")
    #     self.interact_list.append((time.time(), "reserved_rx2_pressed"))
    #     logging.info(";" + str(time.time()) + ";[action];reserved_rx2_pressed")
    #
    # def reserved_rx1_released(self):
    #     self.event_list.append("reserved_rx1_released")
    #     self.interact_list.append((time.time(), "reserved_rx1_released"))
    #     logging.info(";" + str(time.time()) + ";[action];reserved_rx1_released")
    #
    # def reserved_rx2_released(self):
    #     self.event_list.append("reserved_rx2_released")
    #     self.interact_list.append((time.time(), "reserved_rx2_released"))
    #     logging.info(";" + str(time.time()) + ";[action];reserved_rx2_released")
    def IR_1_entry(self):
        event = self._push_event("IR_1_entry")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_2_entry(self):
        event = self._push_event("IR_2_entry")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_3_entry(self):
        event = self._push_event("IR_3_entry")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_4_entry(self):
        event = self._push_event("IR_4_entry")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_5_entry(self):
        event = self._push_event("IR_5_entry")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_1_exit(self):
        event = self._push_event("IR_1_exit")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_2_exit(self):
        event = self._push_event("IR_2_exit")
        # self.cueLED2.off()
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_3_exit(self):
        event = self._push_event("IR_3_exit")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_4_exit(self):
        event = self._push_event("IR_4_exit")
        logging.info("%s, %s", event.timestamp, event.name)

    def IR_5_exit(self):
        event = self._push_event("IR_5_exit")
        logging.info("%s, %s", event.timestamp, event.name)
