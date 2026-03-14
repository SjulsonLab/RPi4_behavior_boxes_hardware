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
from pathlib import Path

import numpy as np
import scipy.io, pickle

import logging
from colorama import Fore, Style
from typing import Optional

from box_runtime.behavior.gpio_backend import (
    PWMLED,
    LED,
    is_raspberry_pi,
    register_pin_label,
    set_visual_stim_state,
)
from box_runtime.audio.importer import AudioPaths
from box_runtime.audio.runtime import SoundRuntime
from box_runtime.input import InputService
from box_runtime.video_recording.camera_client import CameraClient, CameraClientError

try:
    from box_runtime.behavior import ADS1x15
except Exception:
    ADS1x15 = None

PLOTTING_AVAILABLE = False
try:
    import pygame
    import pygame.display
    import matplotlib
    matplotlib.use('module://box_runtime.support.pygame_matplotlib.backend_pygame')
    import matplotlib.pyplot as plt
    import matplotlib.figure as fg
    PLOTTING_AVAILABLE = True
except Exception:
    pygame = None
    plt = None
    fg = None


HEAD_FIXED_GPIO = {
    "user_configurable": [4],
    "unused": [5, 6, 11, 12],
    "inputs": {
        "treadmill_1_input": 13,
        "treadmill_2_input": 16,
        "lick_1": 26,
        "lick_2": 27,
        "lick_3": 15,
    },
    "outputs": {
        "cue_led_1": 22,
        "cue_led_2": 18,
        "cue_led_3": 17,
        "cue_led_4": 14,
        "sound_1": 23,
        "sound_2": 24,
        "sound_3": 9,
        "sound_4": 10,
    },
    "pumps": {
        "reward_left": 19,
        "reward_right": 20,
        "reward_center": 21,
        "pump4": 7,
        "airpuff": 8,
        "vacuum": 25,
    },
}


@dataclass(frozen=True)
class BehaviorEvent:
    """Event emitted by hardware callbacks and consumed by task code."""

    name: str
    timestamp: float


class BehavBox(object):
    event_list = (
        deque()
    )  # all detected events are added to this queue to be read out by the behavior class

    def __init__(self, session_info):
        try:
            self.session_info = session_info

            # make data directory and initialize logfile
            os.makedirs(session_info['dir_name'])
            os.chdir(session_info['dir_name'])
            session_info['file_basename'] = session_info['dir_name'] + '/' + session_info['mouse_name'] + "_" + session_info['datetime']
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s.%(msecs)03d,[%(levelname)s],%(message)s",
                datefmt=('%H:%M:%S'),
                handlers=[
                    logging.FileHandler(session_info['file_basename'] + '.log'),
                    logging.StreamHandler()  # sends copy of log output to screen
                ]
            )
            logging.info(";" + str(time.time()) + ";[initialization];behavior_box_initialized")
        except Exception as error_message:
            print("Logging error")
            print(str(error_message))

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

        # Default to the local camera service on the same Pi. Older two-Pi
        # deployments can still override this explicitly in session_info.
        self.IP_address_video = str(self.session_info.get("camera_host", "127.0.0.1"))
        self.camera_service_port = int(os.environ.get("CAMERA_SERVICE_PORT", "8000"))

        ###############################################################################################
        # event list trigger by the interaction between the RPi and the animal for visualization
        # interact_list: lick, choice interaction between the board and the animal for visualization
        ###############################################################################################
        self.interact_list = []
        self.sound_runtime = self._build_sound_runtime()

        pins = HEAD_FIXED_GPIO
        output_pins = pins["outputs"]

        ###############################################################################################
        # Head-fixed GPIO map (strict CSV semantics)
        ###############################################################################################
        self.cueLED1 = BoxLED(output_pins["cue_led_1"], frequency=200)
        self.cueLED2 = BoxLED(output_pins["cue_led_2"], frequency=200)
        self.cueLED3 = BoxLED(output_pins["cue_led_3"], frequency=200)
        self.cueLED4 = BoxLED(output_pins["cue_led_4"], frequency=200)
        register_pin_label(output_pins["cue_led_1"], "cue_led_1", direction="output")
        register_pin_label(output_pins["cue_led_2"], "cue_led_2", direction="output")
        register_pin_label(output_pins["cue_led_3"], "cue_led_3", direction="output")
        register_pin_label(output_pins["cue_led_4"], "cue_led_4", direction="output")

        # GPIO11 is reserved for the IRIG timecode sender output and is not owned by BehavBox.
        self.user_output = None
        self.DIO5 = None

        # Legacy GPIO sound outputs are retired from the supported runtime path.
        self.DIO4 = None

        # CSV reserves GPIO5/6/11/12 for non-BehavBox use; do not initialize them.
        self.IR_rx1 = None
        self.IR_rx2 = None
        self.IR_rx3 = None
        self.IR_rx4 = None
        self.IR_rx5 = None
        self.lick1 = None
        self.lick2 = None
        self.lick3 = None
        self.ttl_trigger = None
        self.treadmill_input_1 = None
        self.treadmill_input_2 = None
        self.treadmill_encoder = None
        self.poke_extra1 = None
        self.poke_extra2 = None

        ###############################################################################################
        # pump: trigger signal output to a driver board induce the solenoid valve to deliver reward
        ###############################################################################################
        self.pump = Pump(self.session_info)
        self.input_service = InputService(self, self.session_info)

        ###############################################################################################
        # visual stimuli initiation
        ###############################################################################################
        self.visualstim = None
        visual_enabled = bool(self.session_info.get("visual_stimulus", False))
        set_visual_stim_state(
            visual_stim_enabled=visual_enabled,
            visual_stim_active=False,
            current_grating=None,
        )
        if visual_enabled:
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

        ###############################################################################################
        # ADC(Adafruit_ADS1x15) setup
        ###############################################################################################
        self.ADC = None
        if ADS1x15 is not None:
            try:
                self.ADC = ADS1x15.ADS1015
            except Exception as error_message:
                print("ADC issue\n")
                print(str(error_message))
        else:
            print("ADC module unavailable; continuing without ADC support.")

        ###############################################################################################
        # pygame window setup and keystroke handler
        ###############################################################################################
        self.keyboard_active = False
        if PLOTTING_AVAILABLE:
            try:
                pygame.init()
                self.main_display = pygame.display.set_mode((800, 600))
                pygame.display.set_caption(session_info["box_name"])
                fig, axes = plt.subplots(1, 1, )
                axes.plot()
                self.check_plot(fig)
                print(
                    "\nKeystroke handler initiated. In order for keystrokes to register, the pygame window"
                )
                print("must be in the foreground. Keys are as follows:\n")
                print(
                    Fore.YELLOW
                    + "         1: left poke            2: center poke            3: right poke"
                )
                print(
                    "         Q: pump_1            W: pump_2            E: pump_3            R: pump_4"
                )
                print(
                    Fore.CYAN
                    + "                       Esc: close key capture window\n"
                    + Style.RESET_ALL
                )
                print(
                    Fore.GREEN
                    + Style.BRIGHT
                    + "         TO EXIT, CLICK THE MAIN TEXT WINDOW AND PRESS CTRL-C "
                    + Fore.RED
                    + "ONCE\n"
                    + Style.RESET_ALL
                )

                self.keyboard_active = True
            except Exception as error_message:
                print("pygame issue\n")
                print(str(error_message))
        else:
            print("Pygame/matplotlib plotting unavailable; keyboard simulation disabled.")

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
        event = BehaviorEvent(name=event_name, timestamp=time.time())
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

        audio_root = Path(__file__).resolve().parents[1] / "audio"
        paths = AudioPaths(
            tracked_sounds_dir=audio_root / "sounds",
            local_source_dir=audio_root / "local_source_wavs",
            local_sounds_dir=audio_root / "local_sounds",
        )
        device_name = str(self.session_info.get("audio_device", os.environ.get("BEHAVBOX_AUDIO_DEVICE", "default")))
        return SoundRuntime(paths=paths, device_name=device_name)

    def configure_user_output(self, label: str = "ttl_output"):
        """Hand off the default TTL pin to output ownership.

        Args:
            label: Registry/UI label for the output-side pin ownership.

        Returns:
            ``DigitalOutputDevice`` bound to the former TTL input pin.
        """

        return self.input_service.handoff_ttl_to_output(label=label)

    def configure_user_input(
        self,
        label: str = "ttl_trigger",
        pull_up=None,
        active_state: bool = True,
    ):
        """Return the active TTL trigger input for compatibility.

        Args:
            label: Ignored compatibility label.
            pull_up: Unused compatibility argument.
            active_state: Unused compatibility argument.

        Returns:
            The active TTL ``Button`` instance, if still owned by the input service.
        """

        del label, pull_up, active_state
        return self.ttl_trigger

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

    def close(self) -> None:
        """Close long-lived runtime resources owned by BehavBox.

        Returns:
            ``None``.
        """

        if getattr(self, "input_service", None) is not None:
            self.input_service.close()
            self.input_service = None
        if getattr(self, "sound_runtime", None) is not None:
            self.sound_runtime.close()
            self.sound_runtime = None

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
        if figure and PLOTTING_AVAILABLE:
            FramePerSec = pygame.time.Clock()
            figure.canvas.draw()
            self.main_display.blit(figure, (0, 0))
            pygame.display.update()
            FramePerSec.tick(FPS)
        else:
            print("No figure available")

    ###############################################################################################
    # check for key presses - uses pygame window to simulate nosepokes and licks
    ###############################################################################################

    def check_keybd(self):
        if self.keyboard_active and PLOTTING_AVAILABLE:
            # event = pygame.event.get()
            for event in pygame.event.get():
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.keyboard_active = False
                    elif event.key == pygame.K_1:
                        self.left_entry()
                        logging.info(";" + str(time.time()) + ";[action];key_pressed_left_entry()")
                    elif event.key == pygame.K_2:
                        self.center_entry()
                        logging.info(";" + str(time.time()) + ";[action];key_pressed_center_entry()")
                    elif event.key == pygame.K_3:
                        self.right_entry()
                        logging.info(";" + str(time.time()) + ";[action];key_pressed_right_entry()")
                    # elif event.key == pygame.K_4:
                    #     self.reserved_rx1_pressed()
                    #     logging.info(";" + str(time.time()) + ";[action];key_pressed_reserved_rx1_pressed()")
                    # elif event.key == pygame.K_5:
                    #     self.reserved_rx2_pressed()
                    #     logging.info(";" + str(time.time()) + ";[action];key_pressed_reserved_rx2_pressed()")
                    elif event.key == pygame.K_q:
                        # print("Q down: syringe pump 1 moves")
                        # logging.info(";" + str(time.time()) + ";[reward];key_pressed_pump1")
                        self.pump.reward("key_1", self.session_info["key_reward_amount"])
                    elif event.key == pygame.K_w:
                        # print("W down: syringe pump 2 moves")
                        # logging.info(";" + str(time.time()) + ";[reward];key_pressed_pump2")
                        self.pump.reward("key_2", self.session_info["key_reward_amount"])
                    elif event.key == pygame.K_e:
                        # print("E down: syringe pump 3 moves")
                        # logging.info(";" + str(time.time()) + ";[reward];key_pressed_pump3")
                        self.pump.reward("key_3", self.session_info["key_reward_amount"])
                    elif event.key == pygame.K_r:
                        # print("R down: syringe pump 4 moves")
                        # logging.info(";" + str(time.time()) + ";[reward];key_pressed_pump4")
                        self.pump.reward("key_4", self.session_info["key_reward_amount"])
                    elif event.key == pygame.K_t:
                        # print("T down: vacuum on")
                        # logging.info(";" + str(time.time()) + ";[reward];key_pressed_pump_vacuum")
                        self.pump.reward("key_vacuum", 1)
                elif event.type == pygame.KEYUP:
                    if event.key == pygame.K_1:
                        self.left_exit()
                    elif event.key == pygame.K_2:
                        self.center_exit()
                    elif event.key == pygame.K_3:
                        self.right_exit()

    ###############################################################################################
    # methods to start and stop video
    # These work with fake video files but haven't been tested with real ones
    ###############################################################################################
    def video_start(self):
        """Start the configured camera session over the HTTP camera service.

        Returns:
            None.
        """

        if CameraClient is None:
            raise RuntimeError("CameraClient is unavailable; camera HTTP control cannot start")
        IP_address_video = self.IP_address_video
        basename = self.session_info['basename']
        base_dir = self.session_info['external_storage'] + '/'
        hd_dir = base_dir + basename
        os.mkdir(hd_dir)

        print(Fore.YELLOW + "Starting camera session via HTTP service.\n" + Style.RESET_ALL)
        task_recording_started = False
        try:
            self.start_task_recording()
            task_recording_started = True
            client = CameraClient(IP_address_video, port=self.camera_service_port)

            print(Fore.GREEN + "\nStart Recording!" + Style.RESET_ALL)
            client.start_recording(
                session_id=basename,
                owner="automated",
                duration_s=0,
            )
            self._camera_client = client

            # start initiating the dumping of the session information when available
            scipy.io.savemat(hd_dir + "/" + basename + '_session_info.mat', {'session_info': self.session_info})
            print("dumping session_info")
            pickle.dump(self.session_info, open(hd_dir + "/" + basename + '_session_info.pkl', "wb"))

        except CameraClientError as e:
            if task_recording_started:
                self.stop_task_recording()
            print(e)
            raise
        except Exception as e:
            if task_recording_started:
                self.stop_task_recording()
            print(e)

    def video_stop(self):
        """Stop the current camera session and offload it to external storage.

        Returns:
            None.
        """

        # Get the basename from the session information
        basename = self.session_info['basename']
        # Get the ip address for the box video:
        IP_address_video = self.IP_address_video
        try:
            client = getattr(self, "_camera_client", None)
            if client is None:
                client = CameraClient(IP_address_video, port=self.camera_service_port)
            client.stop_recording(owner="automated")
            time.sleep(1)
            time.sleep(2)
            hostname = socket.gethostname()
            print("Moving video files from " + hostname + "video to " + hostname + ":")

            # Create a directory for storage on the hard drive mounted on the box behavior
            base_dir = self.session_info['external_storage'] + '/'
            hd_dir = base_dir + basename

            scipy.io.savemat(hd_dir + "/" + basename + '_session_info.mat', {'session_info': self.session_info})
            print("dumping session_info")
            pickle.dump(self.session_info, open(hd_dir + "/" + basename + '_session_info.pkl', "wb"))

            client.offload_session(basename, base_dir)
            print("camera session offload finished!")
        except CameraClientError as e:
            print(e)
            raise
        except Exception as e:
            print(e)
        finally:
            self.stop_task_recording()

    def start_recording(self) -> str:
        """Start standalone input recording under user ownership.

        Returns:
            Absolute path to the active input-recording directory.
        """

        return self.input_service.start_recording(owner="user")

    def stop_recording(self) -> dict[str, object]:
        """Clear standalone user recording demand.

        Returns:
            Status dictionary describing whether recording stopped or deferred.
        """

        return self.input_service.stop_recording(owner="user")

    def start_task_recording(self) -> str:
        """Assert task-owned input recording using the active session directory.

        Returns:
            Absolute path to the active input-recording directory.
        """

        return self.input_service.start_recording(
            owner="task",
            task_dir=self.session_info["dir_name"],
        )

    def stop_task_recording(self) -> dict[str, object]:
        """Clear task-owned input-recording demand.

        Returns:
            Status dictionary describing whether recording stopped or remained active.
        """

        return self.input_service.stop_recording(owner="task")

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

# this is for the cue LEDs. BoxLED.value is the intensity value (PWM duty cycle, from 0 to 1)
# currently. BoxLED.set_value is the saved intensity value that determines how bright the
# LED will be if BoxLED.on() is called. This is better than the original PWMLED class.
class BoxLED(PWMLED):
    set_value = 1  # the intensity value, ranging from 0-1

    def on(
            self,
    ):  # unlike PWMLED, here the on() function sets the intensity to set_value,
        # not to full intensity
        self.value = self.set_value


class Pump(object):
    def __init__(self, session_info):
        self.session_info = session_info
        pump_pins = HEAD_FIXED_GPIO["pumps"]
        self.pump1 = LED(pump_pins["reward_left"])
        self.pump2 = LED(pump_pins["reward_right"])
        self.pump3 = LED(pump_pins["reward_center"])
        self.pump4 = LED(pump_pins["pump4"])
        self.pump_air = LED(pump_pins["airpuff"])
        self.pump_vacuum = LED(pump_pins["vacuum"])
        register_pin_label(pump_pins["reward_left"], "reward_left", direction="output")
        register_pin_label(pump_pins["reward_right"], "reward_right", direction="output")
        register_pin_label(pump_pins["reward_center"], "reward_center", direction="output")
        register_pin_label(pump_pins["pump4"], "pump4", direction="output")
        register_pin_label(pump_pins["airpuff"], "airpuff", direction="output")
        register_pin_label(pump_pins["vacuum"], "vacuum", direction="output")
        self.reward_list = [] # a list of tuple (pump_x, reward_amount) with information of reward history for data
        # visualization

    def reward(self, which_pump, reward_size):
        # import coefficient from the session_information
        coefficient_p1 = self.session_info["calibration_coefficient"]['1']
        coefficient_p2 = self.session_info["calibration_coefficient"]['2']
        coefficient_p3 = self.session_info["calibration_coefficient"]['3']
        coefficient_p4 = self.session_info["calibration_coefficient"]['4']
        duration_air = self.session_info['air_duration']
        duration_vac = self.session_info["vacuum_duration"]

        if which_pump == "1":
            duration = round((coefficient_p1[0] * (reward_size / 1000) + coefficient_p1[1]), 5)  # linear function
            self.pump1.blink(duration, 0.1, 1)
            self.reward_list.append(("pump1_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[reward];pump1_reward(reward_coeff: " + str(coefficient_p1) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "2":
            duration = round((coefficient_p2[0] * (reward_size / 1000) + coefficient_p2[1]), 5)  # linear function
            self.pump2.blink(duration, 0.1, 1)
            self.reward_list.append(("pump2_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[reward];pump2_reward(reward_coeff: " + str(coefficient_p2) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "3":
            duration = round((coefficient_p3[0] * (reward_size / 1000) + coefficient_p3[1]), 5)  # linear function
            self.pump3.blink(duration, 0.1, 1)
            self.reward_list.append(("pump3_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[reward];pump3_reward(reward_coeff: " + str(coefficient_p3) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "4":
            duration = round((coefficient_p4[0] * (reward_size / 1000) + coefficient_p4[1]), 5)  # linear function
            self.pump4.blink(duration, 0.1, 1)
            self.reward_list.append(("pump4_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[reward];pump4_reward(reward_coeff: " + str(coefficient_p4) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "air_puff":
            self.pump_air.blink(duration_air, 0.1, 1)
            self.reward_list.append(("air_puff", reward_size))
            logging.info(";" + str(time.time()) + ";[reward];pump4_reward_" + str(reward_size))
        elif which_pump == "vacuum":
            self.pump_vacuum.blink(duration_vac, 0.1, 1)
            logging.info(";" + str(time.time()) + ";[reward];pump_vacuum" + str(duration_vac))
        elif which_pump == "key_1":
            duration = round((coefficient_p1[0] * (reward_size / 1000) + coefficient_p1[1]), 5)  # linear function
            self.pump1.blink(duration, 0.1, 1)
            self.reward_list.append(("pump1_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[key];pump1_reward(reward_coeff: " + str(coefficient_p1) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "key_2":
            duration = round((coefficient_p2[0] * (reward_size / 1000) + coefficient_p2[1]), 5)  # linear function
            self.pump2.blink(duration, 0.1, 1)
            self.reward_list.append(("pump2_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[key];pump2_reward(reward_coeff: " + str(coefficient_p2) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "key_3":
            duration = round((coefficient_p3[0] * (reward_size / 1000) + coefficient_p3[1]), 5)  # linear function
            self.pump3.blink(duration, 0.1, 1)
            self.reward_list.append(("pump3_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[key];pump3_reward(reward_coeff: " + str(coefficient_p3) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "key_4":
            duration = round((coefficient_p4[0] * (reward_size / 1000) + coefficient_p4[1]), 5)  # linear function
            self.pump4.blink(duration, 0.1, 1)
            self.reward_list.append(("pump4_reward", reward_size))
            logging.info(";" + str(time.time()) + ";[key];pump4_reward(reward_coeff: " + str(coefficient_p4) +
                         ", reward_amount: " + str(reward_size) + "duration: " + str(duration) + ")")
        elif which_pump == "key_air_puff":
            self.pump_air.blink(duration_air, 0.1, 1)
            self.reward_list.append(("air_puff", reward_size))
            logging.info(";" + str(time.time()) + ";[key];pump4_reward_" + str(reward_size))
        elif which_pump == "key_vacuum":
            self.pump_vacuum.blink(duration_vac, 0.1, 1)
            logging.info(";" + str(time.time()) + ";[key];pump_vacuum" + str(duration_vac))
