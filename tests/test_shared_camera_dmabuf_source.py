from __future__ import annotations

from types import SimpleNamespace

import pytest

from debug.shared_camera_dmabuf_source import SharedCameraDmabufSource


class _FakeStream:
    """Hashable fake stream object carrying a Picamera2-like configuration."""

    def __init__(self, *, pixel_format: str, width_px: int, height_px: int, stride_bytes: int) -> None:
        self.configuration = SimpleNamespace(
            pixel_format=pixel_format,
            size=SimpleNamespace(width=width_px, height=height_px),
            stride=stride_bytes,
        )


def test_shared_camera_dmabuf_source_captures_display_frame_contract() -> None:
    """The dmabuf source should expose a display-ready buffer contract."""

    calls: list[str] = []

    class FakePlane:
        def __init__(self, fd: int) -> None:
            self.fd = fd

    class FakeBuffer:
        def __init__(self, fd: int) -> None:
            self.planes = [FakePlane(fd)]

    class FakeRequestObject:
        def __init__(self, stream: object) -> None:
            self.buffers = {stream: FakeBuffer(57)}

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
            self.request = FakeRequestObject(self.stream_map["main"])
            self.release_calls = 0

        def release(self) -> None:
            self.release_calls += 1

    class FakePicamera2:
        def __init__(self, *, camera_num: int) -> None:
            calls.append(f"picam2:init:{camera_num}")
            self._request = FakeCompletedRequest()

        def create_video_configuration(self, **kwargs):
            calls.append(f"picam2:create:{kwargs}")
            return kwargs

        def configure(self, config) -> None:
            calls.append(f"picam2:configure:{config['main']}")

        def start(self) -> None:
            calls.append("picam2:start")

        def stop(self) -> None:
            calls.append("picam2:stop")

        def close(self) -> None:
            calls.append("picam2:close")

        def capture_request(self):
            calls.append("picam2:capture_request")
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    frame = source.capture_frame()

    assert calls[0] == "picam2:init:0"
    assert "picam2:start" in calls
    assert frame.stream_name == "main"
    assert frame.pixel_format == "XBGR8888"
    assert frame.width_px == 1024
    assert frame.height_px == 600
    assert frame.strides_bytes == (4096,)
    assert frame.offsets_bytes == (0,)
    assert frame.plane_fds == (57,)
    assert frame.buffer_key is frame.request.request.buffers[frame.request.stream_map["main"]]

    source.release_frame(frame)
    assert frame.request.release_calls == 1

    source.close()
    assert calls[-2:] == ["picam2:stop", "picam2:close"]


def test_shared_camera_dmabuf_source_uses_sensor_mode_zero_and_fixed_frame_duration() -> None:
    """The dmabuf source should default to sensor mode 0 and fixed frame timing."""

    calls: list[str] = []

    class FakePicamera2:
        def __init__(self, *, camera_num: int) -> None:
            calls.append(f"picam2:init:{camera_num}")
            self.sensor_modes = [
                {"size": (2304, 1296), "bit_depth": 10},
                {"size": (1536, 864), "bit_depth": 10},
            ]

        def create_video_configuration(self, **kwargs):
            calls.append(("create", kwargs))
            return {"video_config": kwargs}

        def align_configuration(self, config) -> None:
            calls.append(("align", config))

        def configure(self, config) -> None:
            calls.append(("configure", config))

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

        def close(self) -> None:
            calls.append("close")

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=50.0,
        picamera2_factory=FakePicamera2,
    )

    create_kwargs = next(entry[1] for entry in calls if isinstance(entry, tuple) and entry[0] == "create")
    assert create_kwargs["main"] == {"size": (1024, 600), "format": "XBGR8888"}
    assert create_kwargs["sensor"] == {"output_size": (2304, 1296), "bit_depth": 10}
    assert create_kwargs["controls"]["FrameDurationLimits"] == (20000, 20000)
    assert create_kwargs["controls"]["FrameRate"] == 50.0

    align_index = next(index for index, value in enumerate(calls) if isinstance(value, tuple) and value[0] == "align")
    configure_index = next(index for index, value in enumerate(calls) if isinstance(value, tuple) and value[0] == "configure")
    assert align_index < configure_index

    diagnostics = source.diagnostics()
    assert diagnostics["sensor_mode"] == 0
    assert diagnostics["frame_duration_us"] == 20000

    source.close()


def test_shared_camera_dmabuf_source_releases_outstanding_frame_on_close() -> None:
    """Closing the dmabuf source should release an outstanding captured request."""

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
            self.request = SimpleNamespace(buffers={self.stream_map["main"]: SimpleNamespace(planes=[SimpleNamespace(fd=57)])})
            self.release_calls = 0

        def release(self) -> None:
            self.release_calls += 1

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            self._request = FakeCompletedRequest()

        def create_video_configuration(self, **kwargs):
            return kwargs

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def capture_request(self):
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    frame = source.capture_frame()
    assert frame.request.release_calls == 0

    source.close()
    assert frame.request.release_calls == 1


def test_shared_camera_dmabuf_source_rejects_non_display_stream_requests() -> None:
    """The dmabuf source should fail clearly when no display stream is configured."""

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            request_stream = object()
            self._request = SimpleNamespace(
                config={"display": None},
                stream_map={"main": SimpleNamespace(configuration=SimpleNamespace(pixel_format="XBGR8888", size=SimpleNamespace(width=1024, height=600), stride=4096))},
                request=SimpleNamespace(buffers={request_stream: SimpleNamespace(planes=[SimpleNamespace(fd=57)])}),
            )

        def create_video_configuration(self, **kwargs):
            return kwargs

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def capture_request(self):
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    with pytest.raises(RuntimeError, match="display stream"):
        source.capture_frame()

    source.close()


def test_shared_camera_dmabuf_source_falls_back_to_structural_buffer_key_when_needed() -> None:
    """Non-hashable camera buffers should produce a stable structural cache key."""

    class UnhashableBuffer:
        __hash__ = None

        def __init__(self, fd: int) -> None:
            self.planes = [SimpleNamespace(fd=fd)]

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
            self.request = SimpleNamespace(buffers={stream: UnhashableBuffer(57)})

        def release(self) -> None:
            return None

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            self._request = FakeCompletedRequest()

        def create_video_configuration(self, **kwargs):
            return kwargs

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def capture_request(self):
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    frame = source.capture_frame()

    assert frame.buffer_key == ("XBGR8888", 1024, 600, (57,), (4096,), (0,))

    source.release_frame(frame)
    source.close()


def test_shared_camera_dmabuf_source_capture_latest_frame_uses_flush_true() -> None:
    """Latest-frame capture should request a post-now frame from Picamera2."""

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
                buffers={self.stream_map["main"]: SimpleNamespace(planes=[SimpleNamespace(fd=57)])}
            )

        def release(self) -> None:
            return None

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            self._request = FakeCompletedRequest()

        def create_video_configuration(self, **kwargs):
            return kwargs

        def align_configuration(self, _config) -> None:
            return None

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def capture_request(self, **kwargs):
            calls.append(kwargs)
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    frame = source.capture_latest_frame()

    assert calls == [{"flush": True}]
    assert frame.width_px == 1024

    source.release_frame(frame)
    source.close()


def test_shared_camera_dmabuf_source_capture_for_preview_uses_next_mode_by_default() -> None:
    """Preview capture should default to sequential next-frame requests."""

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
                buffers={self.stream_map["main"]: SimpleNamespace(planes=[SimpleNamespace(fd=57)])}
            )

        def release(self) -> None:
            return None

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            self._request = FakeCompletedRequest()

        def create_video_configuration(self, **kwargs):
            return kwargs

        def align_configuration(self, _config) -> None:
            return None

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def capture_request(self, **kwargs):
            calls.append(kwargs)
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        picamera2_factory=FakePicamera2,
    )

    frame = source.capture_frame_for_preview()

    assert calls == [{}]
    assert source.diagnostics()["request_mode"] == "next"

    source.release_frame(frame)
    source.close()


def test_shared_camera_dmabuf_source_capture_for_preview_uses_next_mode_without_flush() -> None:
    """Sequential preview mode should request the next completed frame without flush."""

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
                buffers={self.stream_map["main"]: SimpleNamespace(planes=[SimpleNamespace(fd=57)])}
            )

        def release(self) -> None:
            return None

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            self._request = FakeCompletedRequest()

        def create_video_configuration(self, **kwargs):
            return kwargs

        def align_configuration(self, _config) -> None:
            return None

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def capture_request(self, **kwargs):
            calls.append(kwargs)
            return self._request

    source = SharedCameraDmabufSource(
        camera_id="camera0",
        resolution_px=(1024, 600),
        frame_rate_hz=30.0,
        request_mode="next",
        picamera2_factory=FakePicamera2,
    )

    frame = source.capture_frame_for_preview()

    assert calls == [{}]
    assert source.diagnostics()["request_mode"] == "next"

    source.release_frame(frame)
    source.close()


def test_shared_camera_dmabuf_source_rejects_unknown_request_mode() -> None:
    """Unknown request modes should fail fast with a clear error."""

    class FakePicamera2:
        def __init__(self, **_kwargs) -> None:
            return None

        def create_video_configuration(self, **kwargs):
            return kwargs

        def align_configuration(self, _config) -> None:
            return None

        def configure(self, _config) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    with pytest.raises(ValueError, match="request_mode"):
        SharedCameraDmabufSource(
            camera_id="camera0",
            resolution_px=(1024, 600),
            frame_rate_hz=30.0,
            request_mode="mystery",
            picamera2_factory=FakePicamera2,
        )
