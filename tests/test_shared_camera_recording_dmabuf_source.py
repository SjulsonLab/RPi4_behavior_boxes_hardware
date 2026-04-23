from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np

from debug.shared_camera_recording_dmabuf_source import SharedCameraRecordingDmabufSource


class _FakeStream:
    """Hashable fake stream object carrying a Picamera2-like configuration."""

    def __init__(self, *, pixel_format: str, width_px: int, height_px: int, stride_bytes: int) -> None:
        self.configuration = SimpleNamespace(
            pixel_format=pixel_format,
            size=SimpleNamespace(width=width_px, height=height_px),
            stride=stride_bytes,
        )


def test_shared_camera_recording_dmabuf_source_supports_standalone_local_imports(
    tmp_path: Path,
) -> None:
    """The recording helper should import as a copied standalone debug script."""

    source_path = Path("debug/shared_camera_recording_dmabuf_source.py")
    sibling_path = Path("debug/shared_camera_dmabuf_source.py")
    copied_source_path = tmp_path / "shared_camera_recording_dmabuf_source.py"
    copied_sibling_path = tmp_path / "shared_camera_dmabuf_source.py"
    copied_source_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    copied_sibling_path.write_text(sibling_path.read_text(encoding="utf-8"), encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "standalone_shared_camera_recording_dmabuf_source",
        copied_source_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    original_sys_path = list(sys.path)
    original_module = sys.modules.get(spec.name)
    try:
        sys.path.insert(0, str(tmp_path))
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_sys_path
        if original_module is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = original_module

    assert hasattr(module, "SharedCameraRecordingDmabufSource")


def test_shared_camera_recording_dmabuf_source_records_and_writes_timestamp_csv(
    tmp_path: Path,
) -> None:
    """Recording dmabuf source should start recording and persist all three timestamps."""

    calls: list[object] = []

    class FakePlane:
        def __init__(self, fd: int) -> None:
            self.fd = fd

    class FakeBuffer:
        def __init__(self, fd: int) -> None:
            self.planes = [FakePlane(fd)]

    class FakeCompletedRequest:
        def __init__(self) -> None:
            stream = _FakeStream(
                pixel_format="XBGR8888",
                width_px=1024,
                height_px=600,
                stride_bytes=4096,
            )
            self.config = {"display": "main"}
            self.stream_map = {"main": stream}
            self.request = SimpleNamespace(buffers={stream: FakeBuffer(57)})
            self.release_calls = 0

        def release(self) -> None:
            self.release_calls += 1

        def get_metadata(self) -> dict[str, object]:
            return {
                "SensorTimestamp": 1234567890,
                "FrameDuration": 20000,
            }

    class FakePicamera2:
        def __init__(self, *, camera_num: int) -> None:
            calls.append(("picam2:init", camera_num))
            self.sensor_modes = [
                {"size": (2304, 1296), "bit_depth": 10},
                {"size": (1536, 864), "bit_depth": 10},
            ]
            self._request = FakeCompletedRequest()
            self.pre_callback = None

        def create_video_configuration(self, **kwargs):
            calls.append(("picam2:create", kwargs))
            return {"video_config": kwargs}

        def align_configuration(self, config) -> None:
            calls.append(("picam2:align", config))

        def configure(self, config) -> None:
            calls.append(("picam2:configure", config))

        def start(self) -> None:
            calls.append("picam2:start")

        def start_encoder(self, encoder, output, *, name: str) -> None:
            calls.append(("picam2:start_encoder", encoder, output, name))

        def capture_request(self, **_kwargs):
            calls.append("picam2:capture_request")
            return self._request

        def stop_encoder(self, encoder) -> None:
            calls.append(("picam2:stop_encoder", encoder))

        def stop(self) -> None:
            calls.append("picam2:stop")

        def close(self) -> None:
            calls.append("picam2:close")

    class FakeEncoder:
        def __init__(self, *, bitrate: int) -> None:
            calls.append(("encoder:init", bitrate))
            self.bitrate = bitrate

    class FakeFileOutput:
        def __init__(self, path: str) -> None:
            calls.append(("file_output:init", path))
            self.path = path
            self.closed = False

        def close(self) -> None:
            calls.append(("file_output:close", self.path))
            self.closed = True

    class FakeOverlayRenderer:
        def draw_overlay(
            self,
            frame_y: np.ndarray,
            *,
            elapsed_s: float,
            unix_timestamp_s: float,
            frame_rate_hz: float,
        ) -> None:
            calls.append(("overlay:draw", frame_y.shape, elapsed_s, unix_timestamp_s, frame_rate_hz))

    class FakeMappedArray:
        def __init__(self, request, stream_name: str) -> None:
            calls.append(("mapped_array:init", stream_name))
            self.array = np.zeros((600, 1024, 3), dtype=np.uint8)

        def __enter__(self):
            calls.append("mapped_array:enter")
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            calls.append("mapped_array:exit")

    video_path = tmp_path / "preview_recording_output.h264"
    timestamp_csv_path = tmp_path / "preview_recording_timestamp.csv"

    source = SharedCameraRecordingDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        video_path=video_path,
        timestamp_csv_path=timestamp_csv_path,
        frame_rate_hz=50.0,
        sensor_mode=1,
        picamera2_factory=FakePicamera2,
        encoder_factory=FakeEncoder,
        file_output_factory=FakeFileOutput,
        overlay_renderer_factory=lambda: FakeOverlayRenderer(),
        mapped_array_factory=FakeMappedArray,
        time_fn=lambda: 1710000000.25,
    )

    frame = source.capture_frame_for_preview()
    source._append_timestamp(frame.request)
    source.release_frame(frame)
    source.close()

    create_kwargs = next(entry[1] for entry in calls if isinstance(entry, tuple) and entry[0] == "picam2:create")
    assert create_kwargs["sensor"] == {"output_size": (1536, 864), "bit_depth": 10}
    assert create_kwargs["controls"]["FrameDurationLimits"] == (20000, 20000)
    assert ("encoder:init", 30_000_000) in calls
    assert ("file_output:init", str(video_path)) in calls
    assert any(isinstance(entry, tuple) and entry[0] == "picam2:start_encoder" for entry in calls)
    assert any(isinstance(entry, tuple) and entry[0] == "overlay:draw" for entry in calls)

    diagnostics = source.diagnostics()
    assert diagnostics["sensor_mode"] == 1
    assert diagnostics["request_mode"] == "next"
    assert diagnostics["overlay_enabled"] is True
    assert diagnostics["video_path"] == str(video_path)
    assert diagnostics["timestamp_csv_path"] == str(timestamp_csv_path)

    csv_lines = timestamp_csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert csv_lines == [
        "SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s",
        "1234567890,20000,1710000000.25",
    ]


def test_shared_camera_recording_dmabuf_source_can_disable_overlay(tmp_path: Path) -> None:
    """Disabling overlay should skip mapped-array overlay rendering while still recording timestamps."""

    calls: list[object] = []

    class FakeCompletedRequest:
        def __init__(self) -> None:
            stream = _FakeStream(
                pixel_format="XBGR8888",
                width_px=1024,
                height_px=600,
                stride_bytes=4096,
            )
            self.config = {"display": "main"}
            self.stream_map = {"main": stream}
            self.request = SimpleNamespace(
                buffers={stream: SimpleNamespace(planes=[SimpleNamespace(fd=57)])}
            )

        def release(self) -> None:
            return None

        def get_metadata(self) -> dict[str, object]:
            return {"SensorTimestamp": 111, "FrameDuration": 33333}

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            self.sensor_modes = [
                {"size": (2304, 1296), "bit_depth": 10},
                {"size": (1536, 864), "bit_depth": 10},
            ]
            self._request = FakeCompletedRequest()
            self.pre_callback = None

        def create_video_configuration(self, **kwargs):
            return kwargs

        def align_configuration(self, _config) -> None:
            return None

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def start_encoder(self, encoder, output, *, name: str) -> None:
            calls.append(("start_encoder", encoder, output, name))

        def capture_request(self, **_kwargs):
            return self._request

        def stop_encoder(self, _encoder) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeEncoder:
        def __init__(self, *, bitrate: int) -> None:
            self.bitrate = bitrate

    class FakeFileOutput:
        def __init__(self, path: str) -> None:
            self.path = path

        def close(self) -> None:
            return None

    class FailingMappedArray:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("mapped array should not be used when overlay is disabled")

    timestamp_csv_path = tmp_path / "recording_timestamp.csv"
    source = SharedCameraRecordingDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        video_path=tmp_path / "recording_output.h264",
        timestamp_csv_path=timestamp_csv_path,
        overlay_enabled=False,
        picamera2_factory=FakePicamera2,
        encoder_factory=FakeEncoder,
        file_output_factory=FakeFileOutput,
        mapped_array_factory=FailingMappedArray,
        time_fn=lambda: 42.0,
    )

    frame = source.capture_frame_for_preview()
    source._append_timestamp(frame.request)
    source.release_frame(frame)
    source.close()

    assert source.diagnostics()["overlay_enabled"] is False
    csv_lines = timestamp_csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert csv_lines == [
        "SensorTimestamp_ns,FrameDuration_us,UnixTimestamp_s",
        "111,33333,42.0",
    ]
