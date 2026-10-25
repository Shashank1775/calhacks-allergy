"""Tests for the frame loop and the run CLI, on a fake detector and a tiny synthetic video.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.run; FakeEntityStore and FakeClock (platform)
Emits: nothing
Rules: fail closed (a detector error stops the loop, never an empty frame; a consumer error never starves the
       others; a bad zone change changes nothing); time runs forward (strictly increasing t_ms); deterministic output
       for one input; known-clean source (a zone change never renames or re-origins a tracked entity)
"""

import json
import signal
import sys
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from kitchen.shared.clock import FakeClock
from kitchen.shared.fakes import FakeEntityStore, FakeStateClient
from kitchen.shared.models import Zone, validate
from kitchen.vision import run as vision_run
from kitchen.vision.common import Detection, SourceFrame, VisionSettings
from kitchen.vision.run import FrameLoop, as_live, build_frame_loop, entity_store_consumer, file_sha256
from kitchen.vision.server import VisionStatus, create_app
from kitchen.vision.zones import DEFAULT_ZONES_PATH, LiveZones, load_zones, push_zones, zone_record

SETTINGS = VisionSettings(shift_start_ms=3000)
KNIFE = Detection("knife", (0.4, 0.4, 0.5, 0.5), 0.9)


@pytest.fixture(autouse=True)
def run_in_an_empty_folder(tmp_path, monkeypatch):
    """Run every test from an empty folder, so a data/zones.json saved on this machine can't change what the CLI
    loads (without --zones it would come before the default layout)."""
    monkeypatch.chdir(tmp_path)


class FakeDetector:
    """Returns the same detections for every frame, or raises when told to."""

    backend = "onnx"

    def __init__(self, objects=(KNIFE,), error=None):
        self.objects = list(objects)
        self.error = error

    def detect(self, frame):
        if self.error is not None:
            raise self.error
        return list(self.objects), []


def frame(frame_id, t_ms, value=0):
    """A small RGB frame, all one gray level, so the change mask sees motion only when the level changes."""
    return SourceFrame(frame_id, t_ms, np.full((96, 128, 3), value, dtype=np.uint8))


def make_loop(detector=None, consumers=None, status=None):
    return build_frame_loop(SETTINGS, [], consumers or [], detector=detector or FakeDetector(), status=status)


def test_a_frame_becomes_a_valid_observation_frame_with_a_change_mask():
    output = make_loop().step(frame(0, 100))

    validate(output.observation, "observation_frame")
    assert output.observation["detections"][0]["entity_id"] == "tool-knife-1"
    assert (output.width, output.height) == (128, 96)
    assert output.change_mask.shape == (SETTINGS.clip_size, SETTINGS.clip_size)
    assert output.change_mask.dtype == np.float32


def test_the_change_mask_is_zero_first_and_lights_up_when_pixels_change():
    loop = make_loop()
    first = loop.step(frame(0, 100, value=0)).change_mask
    still = loop.step(frame(1, 133, value=0)).change_mask
    moved = loop.step(frame(2, 166, value=200)).change_mask

    assert not first.any()
    assert not still.any()
    assert moved.max() > 0.5


@pytest.mark.safety
def test_time_runs_forward_a_repeated_or_earlier_frame_is_refused():
    loop = make_loop()
    loop.step(frame(0, 100))

    with pytest.raises(ValueError, match="time runs forward"):
        loop.step(frame(1, 100))
    with pytest.raises(ValueError, match="time runs forward"):
        loop.step(frame(2, 50))


@pytest.mark.safety
def test_a_detector_error_stops_the_loop_and_nothing_is_handed_on():
    """Fail closed: no frame reaches a consumer or the status, so /health goes stale and the gate faults."""
    seen = []
    status = VisionStatus("onnx")
    loop = make_loop(FakeDetector(error=RuntimeError("device stuck")), [seen.append], status)

    with pytest.raises(RuntimeError, match="device stuck"):
        loop.run([frame(0, 100), frame(1, 133)])

    assert seen == []
    assert status.health()["last_frame_t_ms"] is None


@pytest.mark.safety
def test_a_failing_consumer_never_starves_the_others_or_the_status():
    seen = []
    status = VisionStatus("onnx")

    def broken(output):
        raise OSError("disk full")

    loop = make_loop(consumers=[broken, seen.append], status=status)
    loop.run([frame(0, 100), frame(1, 133)])

    assert [output.observation["frame_id"] for output in seen] == [0, 1]
    assert status.health()["last_frame_t_ms"] == 133


def test_the_stream_status_gets_the_detectors_own_boxes_for_each_frame():
    """The fast camera view draws exactly what the detector found in that frame, not tracked entities."""
    status = VisionStatus("onnx")
    make_loop(status=status).step(frame(0, 100))

    _, boxed = status.next_jpeg(0, 0.1, boxes=True)
    _, plain = status.next_jpeg(0, 0.1)
    assert boxed != plain
    assert status._objects == [KNIFE]


def test_entity_store_consumer_feeds_every_frame_to_the_store():
    store = FakeEntityStore(FakeClock())
    make_loop(consumers=[entity_store_consumer(store)]).run([frame(0, 100), frame(1, 133)])

    assert [entity["entity_id"] for entity in store.snapshot()] == ["tool-knife-1"]


def test_fps_estimate_comes_from_the_frames_own_times():
    loop = make_loop()
    outputs = [loop.step(frame(index, 100 + index * 50)) for index in range(5)]

    assert "fps_estimate" not in outputs[0].observation
    assert outputs[-1].observation["fps_estimate"] == pytest.approx(20.0)


def test_as_live_restamps_with_the_clock_and_keeps_time_moving_forward():
    clock = FakeClock(start_ms=5000)
    stamped = list(as_live([frame(0, 0), frame(1, 33), frame(2, 66)], clock))

    # The fake clock doesn't move, so every frame is due at once and each is bumped just past the last.
    assert [f.t_ms for f in stamped] == [5000, 5001, 5002]


# ---------- changing zones while the frame loop runs (PUT /zones) ----------

ORIGIN = "http://localhost:5173"
BIN_IN_SLOT_1 = Detection("bowl", (0.05, 0.1, 0.13, 0.24), 0.9)  # centre (0.09, 0.17): slot-1 in the default layout
KNIFE_AT_PLATING = Detection("knife", (0.42, 0.5, 0.48, 0.6), 0.9)  # centre (0.45, 0.55): the plating spot


def moved_layout() -> list[Zone]:
    """The default layout with slot-1 and slot-2 swapped, and the pass and the plating spot swapped."""
    zones = {zone.zone_id: zone for zone in load_zones(DEFAULT_ZONES_PATH)}
    swaps = {"slot-1": "slot-2", "slot-2": "slot-1", "pass": "plating", "plating": "pass"}
    return [
        Zone(zone.zone_id, zone.kind, zones[swaps.get(zone.zone_id, zone.zone_id)].polygon) for zone in zones.values()
    ]


class RunningKitchen:
    """A frame loop on the default layout with the Vision API in front, wired as kitchen.vision.run wires it."""

    def __init__(self, save_path, detector):
        self.zones = load_zones(DEFAULT_ZONES_PATH)
        self.state = FakeStateClient()
        push_zones(self.state, self.zones)
        self.loop = build_frame_loop(SETTINGS, self.zones, [], detector=detector)
        self.live = LiveZones(self.zones, save_path, self.state, self.loop.use_zones)
        app = create_app(
            VisionStatus("onnx"), lambda ticket_id: "plate-1", self.live.current, self.live.replace, [ORIGIN]
        )
        self.api = TestClient(app)
        self.save_path = save_path
        self._frame_id = 0

    def step(self) -> dict:
        """One frame, 100 ms after the last; returns its detections by entity ID."""
        self._frame_id += 1
        output = self.loop.step(frame(self._frame_id, self._frame_id * 100))
        return {item["entity_id"]: item for item in output.observation["detections"]}

    def table(self) -> dict:
        """The zone table, by zone ID."""
        return {zone.zone_id: zone for zone in self.state.list_zones()}


def layout_body(zones: list[Zone]) -> dict:
    """A PUT /zones body for `zones`."""
    return {"zones": [zone_record(zone) for zone in zones]}


def test_after_a_good_put_the_next_frame_uses_the_new_zones_and_entities_keep_their_ids(tmp_path):
    kitchen = RunningKitchen(tmp_path / "zones.json", FakeDetector([BIN_IN_SLOT_1, KNIFE_AT_PLATING]))
    before = kitchen.step()
    assert {entity_id: item["zone"] for entity_id, item in before.items()} == {
        "bin-1": "slot-1",
        "tool-knife-1": "plating",
    }

    response = kitchen.api.put("/zones", json=layout_body(moved_layout()))
    assert response.status_code == 200
    assert response.json() == layout_body(moved_layout())

    after = kitchen.step()
    # The zones are the new layout's, but bin-1 is still bin-1 though slot-2 is drawn where it stands now, and no
    # origin changed: origin is decided at first sighting only.
    assert {entity_id: item["zone"] for entity_id, item in after.items()} == {
        "bin-1": "slot-2",
        "tool-knife-1": "pass",
    }
    assert {entity_id: item["origin"] for entity_id, item in after.items()} == {
        entity_id: item["origin"] for entity_id, item in before.items()
    }
    assert kitchen.table() == {zone.zone_id: zone for zone in moved_layout()}
    assert load_zones(kitchen.save_path) == moved_layout()
    assert kitchen.api.get("/zones").json() == layout_body(moved_layout())


@pytest.mark.safety
def test_a_bad_put_changes_neither_the_lookup_the_file_nor_the_zone_table(tmp_path):
    kitchen = RunningKitchen(tmp_path / "zones.json", FakeDetector([BIN_IN_SLOT_1, KNIFE_AT_PLATING]))
    kitchen.live.replace(layout_body(kitchen.zones))  # saved once, as after an earlier good PUT
    file_text = kitchen.save_path.read_text()
    kitchen.step()

    # Valid zones followed by one bad one: nothing of it may apply, not even the good part.
    bad = layout_body(moved_layout())
    bad["zones"].append({"zone_id": "slot-6", "kind": "bin_slot", "polygon": [[0.1, 0.1], [0.2, 0.2]]})
    response = kitchen.api.put("/zones", json=bad)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "bad_zones"

    after = kitchen.step()
    assert {entity_id: item["zone"] for entity_id, item in after.items()} == {
        "bin-1": "slot-1",
        "tool-knife-1": "plating",
    }
    assert kitchen.save_path.read_text() == file_text
    assert kitchen.table() == {zone.zone_id: zone for zone in kitchen.zones}
    assert kitchen.api.get("/zones").json() == layout_body(kitchen.zones)


@pytest.mark.safety
def test_a_put_that_changes_a_zones_kind_is_a_422_and_changes_nothing(tmp_path):
    """The interactions step learns zone kinds at startup, so a wash renamed "other" would still wash there."""
    kitchen = RunningKitchen(tmp_path / "zones.json", FakeDetector([BIN_IN_SLOT_1, KNIFE_AT_PLATING]))
    kitchen.step()

    wash_as_other = [
        Zone(zone.zone_id, "other" if zone.zone_id == "wash" else zone.kind, zone.polygon) for zone in kitchen.zones
    ]
    response = kitchen.api.put("/zones", json=layout_body(wash_as_other))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "bad_zones"
    assert "delete it and draw a new one" in response.json()["error"]["message"]

    assert not kitchen.save_path.exists()
    assert kitchen.table() == {zone.zone_id: zone for zone in kitchen.zones}
    assert kitchen.api.get("/zones").json() == layout_body(kitchen.zones)
    assert kitchen.loop.zone_of((0.1, 0.85)).kind == "wash"


class DetectorThatChangesZones(FakeDetector):
    """A FakeDetector that, on its second frame, changes the zones while the frame is being detected."""

    def __init__(self, objects):
        super().__init__(objects)
        self.frames = 0
        self.change_zones = None

    def detect(self, frame):
        self.frames += 1
        if self.frames == 2:
            self.change_zones()
        return super().detect(frame)


def test_a_zone_change_during_a_frame_applies_from_the_next_frame(tmp_path):
    detector = DetectorThatChangesZones([KNIFE_AT_PLATING])
    kitchen = RunningKitchen(tmp_path / "zones.json", detector)
    detector.change_zones = lambda: kitchen.live.replace(layout_body(moved_layout()))

    assert kitchen.step()["tool-knife-1"]["zone"] == "plating"
    assert kitchen.step()["tool-knife-1"]["zone"] == "plating"  # the change landed while this frame ran
    assert kitchen.step()["tool-knife-1"]["zone"] == "pass"


def test_the_cli_without_zones_loads_the_default_layout_and_logs_its_sha256(tmp_path, monkeypatch, caplog):
    video = tmp_path / "prep.avi"
    write_video(video, frames=2)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())

    with caplog.at_level("INFO", logger="kitchen.vision.run"):
        assert vision_run.main(["--video", str(video), "--out", str(tmp_path / "frames.jsonl")]) == 0

    assert f"from {DEFAULT_ZONES_PATH} (sha256 {file_sha256(DEFAULT_ZONES_PATH)})" in caplog.text
    first = json.loads((tmp_path / "frames.jsonl").read_text().splitlines()[0])
    assert first["detections"][0]["zone"] == "plating"  # the knife at (0.45, 0.45), in the default layout


def write_video(path, frames=12):
    """A tiny MJPG video whose brightness steps every frame, so the change mask has something to see."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (128, 96))
    for index in range(frames):
        writer.write(np.full((96, 128, 3), (index * 20) % 256, dtype=np.uint8))
    writer.release()


def slow_open_detector(settings):
    """A FakeDetector that, like a real one, takes a moment to load."""
    time.sleep(0.05)
    return FakeDetector()


@pytest.mark.safety
def test_the_cli_writes_valid_strictly_increasing_frames_and_two_runs_match(tmp_path, monkeypatch):
    """Issue #14: --video and --out write one valid ObservationFrame per frame, t_ms strictly increasing, and the
    same input gives byte-identical JSONL. An offline video keeps its own time from 0, so how long the detector takes
    to load can't move frames across shift_start_ms and change origins."""
    video = tmp_path / "prep.avi"
    write_video(video)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", slow_open_detector)

    outputs = []
    for name in ("first.jsonl", "second.jsonl"):
        assert vision_run.main(["--video", str(video), "--out", str(tmp_path / name)]) == 0
        outputs.append((tmp_path / name).read_text())

    lines = [json.loads(line) for line in outputs[0].splitlines()]
    assert len(lines) == 12
    assert [line["t_ms"] for line in lines[:2]] == [0, 100]  # the video's own time: 10 frames a second, from 0
    for line in lines:
        validate(line, "observation_frame")
    assert all(b["t_ms"] > a["t_ms"] for a, b in zip(lines, lines[1:], strict=False))
    assert outputs[0] == outputs[1]


def test_the_cli_records_a_replayable_recording(tmp_path, monkeypatch):
    from kitchen.vision.record import read_recording

    video = tmp_path / "prep.avi"
    write_video(video, frames=5)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())

    assert vision_run.main(["--video", str(video), "--record", str(tmp_path / "rec")]) == 0

    replayed = list(read_recording(tmp_path / "rec"))
    assert [output.observation["frame_id"] for output in replayed] == [0, 1, 2, 3, 4]
    assert replayed[-1].change_mask.max() > 0


def test_frame_loop_needs_nothing_but_its_parts():
    """The local runner builds the loop in-process; FrameLoop takes its parts directly."""
    loop = FrameLoop(SETTINGS, FakeDetector(), make_loop().tracker, lambda point: None, [])

    assert loop.step(frame(0, 100)).observation["detections"][0]["zone"] is None


def test_the_record_cli_is_the_run_cli_with_record(tmp_path, monkeypatch):
    from kitchen.vision import record

    video = tmp_path / "prep.avi"
    write_video(video, frames=3)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())

    assert record.main(["--source", str(video), "--out", str(tmp_path / "rec")]) == 0
    assert record.read_manifest(tmp_path / "rec")["frame_count"] == 3


@pytest.mark.safety
def test_serving_closes_the_recording_when_the_video_ends_and_sigterm_stops_cleanly(tmp_path, monkeypatch):
    """With --serve the process lives on after the source ends. The recording must be complete then, not only on
    Ctrl-C, and SIGTERM (how a service manager stops it) must close it too instead of killing the process."""
    from kitchen.vision.record import read_manifest

    video = tmp_path / "prep.avi"
    write_video(video, frames=3)
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())
    monkeypatch.setattr(vision_run, "serve_in_background", lambda app, port: SimpleNamespace(should_exit=False))
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    seen = {}

    def wait_until_stopped():
        seen["frame_count"] = read_manifest(tmp_path / "rec")["frame_count"]  # complete while still serving
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # what a SIGTERM does now

    monkeypatch.setattr(vision_run, "wait_until_stopped", wait_until_stopped)

    assert vision_run.main(["--video", str(video), "--serve", "--record", str(tmp_path / "rec")]) == 0
    assert seen["frame_count"] == 3
    assert signal.getsignal(signal.SIGTERM) == previous_sigterm


@pytest.mark.parametrize("shape", [(720, 1280, 3), (1080, 1920, 3), (480, 640, 3)])
def test_the_change_mask_letterbox_matches_the_clip_letterbox(shape):
    """The live change mask must line up with the clip builder's letterboxed boxes, pixel for pixel."""
    from kitchen.shared.clips import letterbox_frame

    rgb = np.random.default_rng(0).integers(0, 256, shape, dtype=np.uint8)

    assert np.array_equal(vision_run.letterbox_for_change_mask(rgb, 128), letterbox_frame(rgb, 128))


def test_the_change_mask_letterbox_keeps_the_clip_layout_at_any_frame_size():
    from kitchen.shared.clips import LETTERBOX_PAD, letterbox_layout

    rgb = np.full((1232, 1640, 3), 200, dtype=np.uint8)  # the Pi camera's 1640 x 1232 mode: not a whole factor
    square = vision_run.letterbox_for_change_mask(rgb, 128)
    content_width, content_height, left, top = letterbox_layout(1640, 1232, 128)

    assert (square[top : top + content_height, left : left + content_width] == 200).all()
    assert (square[:top] == LETTERBOX_PAD).all()
    assert (square[top + content_height :] == LETTERBOX_PAD).all()


class FakePiCamera:
    """Stands in for picamera2.Picamera2: an endless stream of gray B, G, R frames."""

    camera_controls = {}

    def create_video_configuration(self, main):
        return {"main": main}

    def configure(self, config):
        pass

    def start(self):
        pass

    def capture_array(self):
        return np.full((96, 128, 3), 128, dtype=np.uint8)

    def stop(self):
        pass

    def close(self):
        pass


def test_the_cli_records_from_the_pi_camera(tmp_path, monkeypatch):
    from kitchen.vision.record import read_recording

    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=FakePiCamera))
    monkeypatch.setattr("kitchen.vision.detect.open_detector", lambda settings: FakeDetector())

    assert vision_run.main(["--picamera", "--max-frames", "3", "--record", str(tmp_path / "rec")]) == 0

    assert json.loads((tmp_path / "rec" / "manifest.json").read_text())["source"] == "picamera"
    assert [output.observation["frame_id"] for output in read_recording(tmp_path / "rec")] == [0, 1, 2]
