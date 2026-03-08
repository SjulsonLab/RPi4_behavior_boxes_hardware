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
    from video_acquisition.camera_client import CameraClient, CameraClientError
except ImportError:
    try:
        from essential.video_acquisition.camera_client import CameraClient, CameraClientError
    except ImportError:
        CameraClient = None
        CameraClientError = RuntimeError

class VideoCapture():
    """this class is to make video captures independently of the behavior box,
    this way we can record video in freely moving or sleep boxes using a simple
    Raspberry pi
    
    You have to provide several variables:
        IP_address_video: the host name of the RPi with camera, ip or hostname,
            for example 'sleepboxpi'
        video_name: name of the video file, example: B6_sleeprecording_20221028
        base_pi_dir: where in the pi to save your videos, I recommend you to
            create your own folder
        local_storage_dir: final destination where you want to save the videos
            to, ideally in a HDD in the computer where ephys is also stored.
            Usually is a windows computer. This has to be something like- 
            E:/MyProject/Session/
    
    """
    def __init__(self,IP_address_video,video_name,base_pi_dir,local_storage_dir,frame_rate=30):
            self.IP_address_video = IP_address_video
            self.basename = video_name
            self.base_dir = base_pi_dir
            self.local_storage_dir = local_storage_dir
            self.frame_rate = frame_rate
            self._camera_client = None
        
    def video_preview_only(self):
            IP_address_video = self.IP_address_video
            print(Fore.CYAN + "\nPreview is served by the camera service." + Style.RESET_ALL)
            print(
                Fore.GREEN
                + f"\nOpen http://{IP_address_video}:8000/manual for manual control "
                + f"or http://{IP_address_video}:8000/monitor for view-only monitoring."
                + Style.RESET_ALL
            )
                
    def video_start(self):
            if CameraClient is None:
                raise RuntimeError("CameraClient is unavailable; camera HTTP control cannot start")
            IP_address_video = self.IP_address_video
            basename = self.basename
            hd_dir = self.local_storage_dir

            try:
                client = CameraClient(IP_address_video)
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
            # Get the basename from the session information
            basename = self.basename
            # Get the ip address for the box video:
            IP_address_video = self.IP_address_video
            try:
                client = self._camera_client or CameraClient(IP_address_video)
                client.stop_recording(owner="automated")
                time.sleep(2)
                
                hostname = socket.gethostname()
                print("Moving video files from " + hostname + "video to " + hostname + ":")
    
                # Create a directory for storage on the hard drive mounted on the box behavior
                hd_dir = self.local_storage_dir
                
                client.offload_session(basename, hd_dir)
                print("camera session offload finished!")
                # print("Control-C to quit (ignore the error for now)")
            except CameraClientError as e:
                print(e)
                raise
            except Exception as e:
                print(e)
