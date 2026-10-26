"""GPIO adapter tests against the verified v2 surface; no GPIO is claimed."""

from dataclasses import dataclass
from enum import IntEnum
from types import SimpleNamespace

import pytest

from test_core import config, trace
from box_runtime.input.treadmill.gpio_backend import BackendError, GpiodSource


class Values(IntEnum):
    """Fake logical line values, matching the verified inactive/active integers."""
    INACTIVE = 0
    ACTIVE = 1


@dataclass
class RawEvent:
    """Fake binding event: kernel ns, line offset, edge type and uint32 sequences."""
    timestamp_ns: int
    line_offset: int
    event_type: int
    global_seqno: int
    line_seqno: int


class Request:
    """Bounded fake request with events injected at selected synchronization calls."""

    def __init__(self, events=(), inject_at=None, gap=False):
        """Store raw events and optional boundary-injection operation number."""
        self.events = list(events)
        self.inject_at = inject_at
        self.calls = 0
        self.values = [Values.INACTIVE,Values.INACTIVE]
        self.released = False
        self.gap = gap

    def boundary(self):
        """Advance one fake API boundary and optionally emit a B rising event."""
        self.calls += 1
        if self.calls == self.inject_at:
            self.events.append(RawEvent(100,16,1,2 if self.gap else 1,1))
            self.values[1] = Values.ACTIVE

    def get_values(self, lines):
        """Return logical levels in supplied A/B offset order."""
        self.boundary()
        return list(self.values)

    def wait_edge_events(self, timeout=None):
        """Return pending state after an optional deterministic injected edge."""
        self.boundary()
        return bool(self.events)

    def read_edge_events(self, max_events=None):
        """Return at most the requested raw events in input order."""
        self.boundary()
        result,self.events=self.events[:max_events],self.events[max_events:]
        return result

    def release(self):
        """Record exclusive-request release; return None."""
        self.released = True


class Chip:
    """Minimal chip inventory object; input metadata is supplied by the fixture."""

    def __init__(self, info, used=False):
        """Store chip-info object and whether the configured lines are busy."""
        self.info,self.used,self.closed = info,used,False

    def get_info(self):
        """Return immutable chip metadata."""
        return self.info

    def line_offset_from_id(self, name):
        """Resolve GPIO13/GPIO16 names to integer offsets."""
        return int(name.removeprefix("GPIO"))

    def get_line_info(self, offset):
        """Return line metadata; raise for out-of-range offsets."""
        if offset >= self.info.num_lines:
            raise ValueError("out of range")
        return SimpleNamespace(name=f"GPIO{offset}",offset=offset,used=self.used,consumer="other" if self.used else None)

    def close(self):
        """Record closure of the inventory handle."""
        self.closed = True


def binding(request, chips):
    """Return a fake v2 binding and captured request-argument list."""
    calls=[]
    def request_lines(path, **kwargs):
        """Record exact request settings and return the fake request."""
        calls.append((path,kwargs))
        return request
    return SimpleNamespace(
        Chip=chips.__getitem__, request_lines=request_lines,
        LineSettings=SimpleNamespace, LineRequest=Request,
        EdgeEvent=SimpleNamespace(Type=SimpleNamespace(RISING_EDGE=1,FALLING_EDGE=2),
                                  __dataclass_fields__=RawEvent.__dataclass_fields__),
        line=SimpleNamespace(Direction=SimpleNamespace(INPUT=1),Edge=SimpleNamespace(BOTH=3),
                             Bias=SimpleNamespace(PULL_UP=1,PULL_DOWN=2,DISABLED=3),
                             Clock=SimpleNamespace(MONOTONIC=1))),calls


def source(request=None, *, chip_paths=None, used=False, count=54, label="pinctrl-rp1"):
    """Build source, fake request and captured calls for one synthetic RP1 chip."""
    request=Request() if request is None else request
    chip_paths=chip_paths or ["/fake/gpiochip7"]
    chips={str(path):Chip(SimpleNamespace(name="gpiochip7",label=label,num_lines=count),used)
           for path in chip_paths}
    fake,calls=binding(request,chips)
    return GpiodSource(config(),binding=fake,chip_paths=chip_paths),request,calls


def test_joint_request_settings_and_ordered_multi_batch_adaptation():
    backend,request,calls=source()
    initial=backend.open(__import__("time").monotonic_ns()+1_000_000_000)
    assert initial.ab_state == initial.global_sequence_number == 0
    assert initial.line_sequence_numbers == (0,0)
    assert calls[0][1]["event_buffer_size"] == 8192
    settings=calls[0][1]["config"][(13,16)]
    assert settings.edge_detection == 3 and settings.event_clock == settings.bias == 1
    assert settings.debounce_period.total_seconds() == 0
    events=trace([1]*600)
    request.events=[RawEvent(event.timestamp_ns,13 if event.channel=="A" else 16,
                            1 if event.edge_type=="rising" else 2,
                            event.global_sequence_number,event.line_sequence_number) for event in events]
    received=[]
    while batch := backend.read_batch(0):
        received.extend(batch)
    assert received == events
    backend.close()
    backend.close()
    assert request.released


@pytest.mark.parametrize("boundary",range(1,10))
def test_startup_boundary_event_is_excluded_once_or_delivered_once(boundary):
    backend,request,_=source(Request(inject_at=boundary))
    initial=backend.open(__import__("time").monotonic_ns()+1_000_000_000)
    post=backend.read_batch(0)
    assert (initial.global_sequence_number,len(post)) in ((1,0),(0,1),(0,0))
    if initial.global_sequence_number:
        assert initial.ab_state == 1 and initial.line_sequence_numbers == (0,1)
    backend.close()


def test_startup_gap_fails_and_releases_partial_request():
    backend,request,_=source(Request(inject_at=2,gap=True))
    with pytest.raises(BackendError,match="sequence|gap"):
        backend.open(__import__("time").monotonic_ns()+1_000_000_000)
    assert request.released


@pytest.mark.parametrize("kwargs,code", [
    ({"used":True},"GPIO_LINE_CLAIM_FAILED"),
    ({"count":12},"GPIO_RESOLUTION_FAILED"),
    ({"label":"not-rp1"},"GPIO_RESOLUTION_FAILED"),
    ({"chip_paths":["/fake/gpiochip7","/fake/gpiochip8"]},"GPIO_RESOLUTION_FAILED"),
])
def test_inventory_resolution_failures(kwargs,code):
    backend,_,_=source(**kwargs)
    with pytest.raises(BackendError) as error:
        backend.open(__import__("time").monotonic_ns()+1_000_000_000)
    assert error.value.code == code


def test_alias_paths_do_not_create_false_ambiguity(tmp_path):
    canonical=tmp_path/"gpiochip0"
    canonical.touch()
    alias=tmp_path/"gpiochip4"
    alias.symlink_to(canonical)
    backend,_,_=source(chip_paths=[str(canonical),str(alias)])
    backend.open(__import__("time").monotonic_ns()+1_000_000_000)
    assert backend.mapping["chip_path"] == str(canonical)
    backend.close()


def test_incomplete_binding_has_actionable_error():
    backend=GpiodSource(config(),binding=SimpleNamespace(),chip_paths=[])
    with pytest.raises(BackendError) as error:
        backend.open(__import__("time").monotonic_ns()+1_000_000_000)
    assert error.value.code == "BACKEND_CAPABILITY_MISSING"
