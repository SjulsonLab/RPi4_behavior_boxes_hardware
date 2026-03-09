#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Oct 28 12:30:51 2022

@author: eliezyer

based on the BehavBox class
"""
import os
from colorama import Fore, Style
import time
import scipy
import socket
import pickle

try:
    from box_runtime.video_recording.camera_client import CameraClient, CameraClientError
except ImportError:
    CameraClient = None
    CameraClientError = RuntimeError

class VideoCapture:
    """HTTP camera-control wrapper for standalone video acquisition sessions.

    Args:
        IP_address_video: Camera service host string. ``None`` defaults to
            ``"127.0.0.1"`` for the one-Pi deployment.
        video_name: Session identifier string used for camera storage and offload.
        base_pi_dir: Legacy remote storage hint string. Preserved for API
            compatibility and not used by the HTTP camera service path.
        local_storage_dir: Local destination directory string for offloaded
            recordings.
        frame_rate: Requested acquisition frame rate in Hz.
    """

    def __init__(self, IP_address_video, video_name, base_pi_dir, local_storage_dir, frame_rate=30):
        """Configure one video-capture session wrapper.

        Args:
            IP_address_video: Camera service host string or ``None``.
            video_name: Session identifier string.
            base_pi_dir: Legacy remote storage hint string.
            local_storage_dir: Local offload destination directory string.
            frame_rate: Requested recording frame rate in Hz.
        """

        self.IP_address_video = IP_address_video or "127.0.0.1"
        self.camera_service_port = int(os.environ.get("CAMERA_SERVICE_PORT", "8000"))
        self.basename = video_name
        self.base_dir = base_pi_dir
        self.local_storage_dir = local_storage_dir
        self.frame_rate = frame_rate
        self._camera_client = None

    def video_preview_only(self):
        """Print the manual and monitor URLs for the camera HTTP service.

        Returns:
            None.
        """

        IP_address_video = self.IP_address_video
        print(Fore.CYAN + "\nPreview is served by the camera service." + Style.RESET_ALL)
        print(
            Fore.GREEN
            + f"\nOpen http://{IP_address_video}:{self.camera_service_port}/manual for manual control "
            + f"or http://{IP_address_video}:{self.camera_service_port}/monitor for view-only monitoring."
            + Style.RESET_ALL
        )

    def video_start(self):
        """Start recording through the camera HTTP service.

        Returns:
            None.
        """

        if CameraClient is None:
            raise RuntimeError("CameraClient is unavailable; camera HTTP control cannot start")
        IP_address_video = self.IP_address_video
        basename = self.basename
        hd_dir = self.local_storage_dir

        try:
            client = CameraClient(IP_address_video, port=self.camera_service_port)
            self._camera_client = client
            print(Fore.GREEN + "\nStart Recording!" + Style.RESET_ALL)
            client.start_recording(
                session_id=basename,
                owner="automated",
                fps=self.frame_rate,
                duration_s=0,
            )
            os.makedirs(hd_dir, exist_ok=True)
        except CameraClientError as e:
            print(e)
            raise
        except Exception as e:
            print(e)

    def video_stop(self):
        """Stop recording and offload the completed session locally.

        Returns:
            None.
        """

        basename = self.basename
        IP_address_video = self.IP_address_video
        try:
            client = self._camera_client or CameraClient(
                IP_address_video,
                port=self.camera_service_port,
            )
            client.stop_recording(owner="automated")
            time.sleep(2)

            hostname = socket.gethostname()
            print("Moving video files from " + hostname + "video to " + hostname + ":")

            hd_dir = self.local_storage_dir
            client.offload_session(basename, hd_dir)
            print("camera session offload finished!")
        except CameraClientError as e:
            print(e)
            raise
        except Exception as e:
            print(e)
