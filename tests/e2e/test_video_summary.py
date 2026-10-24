"""The video summary: a recorded video through live mode's parts offline, summarized as touches and exposures.

Owner: platform (agents)
Step:  end-to-end harness
Reads: tools/video_summary.py (loaded by path); small videos written here; a scripted detector instead of ONNX
Emits: nothing
Rules: fail closed in wording: no PASS, HOLD or "clean", unknown history named as "could carry any allergen", and a
       failing interactions step fails the job instead of giving a summary that looks complete
"""

import importlib.util
import json
import time
from pathlib import Path

import cv2
import jsonschema
import numpy as np
import pytest
from fastapi.testclient import TestClient

from kitchen.shared.models import Zone, validate
from kitchen.vision.common import Detection, VisionSettings

TOOL = Path(__file__).resolve().parents[2] / "tools" / "video_summary.py"

ZONES = [
    Zone("slot-1", "bin_slot", [(0.0, 0.0), (0.2, 0.0), (0.2, 0.4), (0.0, 0.4)]),
    Zone("plating-spot", "plating_spot", [(0.35, 0.3), (0.65, 0.3), (0.65, 0.7), (0.35, 0.7)]),
    Zone("pass", "pass", [(0.75, 0.0), (1.0, 0.0), (1.0, 1.0), (0.75, 1.0)]),
]
SETTINGS = VisionSettings(shift_start_ms=1000)  # everything first seen in the first second is there at shift start
BIN = Detection("bowl", (0.05, 0.1, 0.15, 0.2), 0.9)  # bin-1: satay sauce, peanut and soy (kitchen/orders/bins.py)
KNIFE = Detection("knife", (0.25, 0.8, 0.3, 0.95), 0.9)
PLATE = Detection("bowl", (0.45, 0.45, 0.55, 0.55), 0.9)  # on the plating spot: plate-1
SPOON = Detection("spoon", (0.6, 0.8, 0.65, 0.95), 0.9)

# The Summary's shape, as the issue and the module docstring give it (the web page builds against exactly this).
ENTITY = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entity_id", "kind", "label", "origin", "first_seen_t_ms", "last_seen_t_ms"],
    "properties": {
        "entity_id": {"type": "string"},
        "kind": {"enum": ["bin", "tool", "hand", "plate"]},
        "label": {"type": "string"},
        "origin": {"enum": ["shift_start", "clean_stack", "unknown"]},
        "first_seen_t_ms": {"type": "integer"},
        "last_seen_t_ms": {"type": "integer"},
    },
}
TOUCH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["event_id", "t_start_ms", "t_end_ms", "a_id", "b_id", "action", "probability"],
    "properties": {
        "event_id": {"type": "string"},
        "t_start_ms": {"type": "integer"},
        "t_end_ms": {"type": "integer"},
        "a_id": {"type": "string"},
        "b_id": {"type": "string"},
        "action": {"enum": ["transfer.contact", "transfer.scoop", "transfer.wipe", "clean.wash"]},
        "probability": {"type": "number"},
    },
}
EXPOSURE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entity_id", "kind", "unknown_history", "allergens"],
    "properties": {
        "entity_id": {"type": "string"},
        "kind": {"enum": ["tool", "hand", "plate"]},
        "unknown_history": {"type": "boolean"},
        "allergens": {"type": "array"},  # each one an AllergenReach, checked against its contract schema
    },
}
SUMMARY = {
    "type": "object",
    "additionalProperties": False,
    "required": ["video", "detector", "shift_start_ms", "entities", "touches", "exposure", "text", "warnings"],
    "properties": {
        "video": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "duration_ms", "frames", "frames_processed"],
            "properties": {
                "name": {"type": "string"},
                "duration_ms": {"type": "integer"},
                "frames": {"type": "integer"},
                "frames_processed": {"type": "integer"},
            },
        },
        "detector": {"type": "string"},
        "shift_start_ms": {"type": "integer"},
        "entities": {"type": "array", "items": ENTITY},
        "touches": {"type": "array", "items": TOUCH},
        "exposure": {"type": "array", "items": EXPOSURE},
        "text": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


def load_tool():
    spec = importlib.util.spec_from_file_location("video_summary", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


video_summary = load_tool()


def write_video(path: Path, seconds: float = 3.0, fps: int = 10) -> Path:
    """A small MJPG video whose frames change shade, so the change mask has something to see."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (128, 96))
    for index in range(round(seconds * fps)):
        writer.write(np.full((96, 128, 3), (index * 7) % 256, dtype=np.uint8))
    writer.release()
    return path


class ScriptedDetector:
    """Returns the objects `script(t_ms)` puts on the counter at each frame's time."""

    backend = "onnx"

    def __init__(self, script=lambda t_ms: [BIN, KNIFE, PLATE]):
        self.script = script

    def detect(self, frame):
        return list(self.script(frame.t_ms)), []


# ---------- stand-in interactions steps, built as factory(state, clock) ----------


def quiet_step(state, clock):
    """Healthy, and sees no touches."""
    return lambda output: None


def broken_step(state, clock):
    """Fails on every frame."""

    def consume(output):
        raise RuntimeError("zone rules crashed")

    return consume


def peanut_knife_step(state, clock):
    """Reports the knife scooping satay from bin-1, then touching the plate, once both have happened (t >= 1300)."""
    written = []

    def consume(output):
        if written or output.observation["t_ms"] < 1300:
            return
        classifier = {"name": "zone_rules", "version": "test"}
        for event_id, t_ms, b_id, action, direction in (
            ("e-scoop", 1100, "bin-1", "transfer.scoop", "b_to_a"),
            ("e-touch", 1200, "plate-1", "transfer.contact", "both"),
        ):
            state.record_interaction(
                {
                    "event_id": event_id,
                    "t_start_ms": t_ms,
                    "t_end_ms": t_ms + 50,
                    "a_id": "tool-knife-1",
                    "b_id": b_id,
                    "action": action,
                    "probability": 0.95,
                    "direction": direction,
                    "classifier": classifier,
                }
            )
        written.append(True)

    return consume


def summarize(video: Path, interactions, script=None, zones=ZONES, **options) -> dict:
    detector = ScriptedDetector(script) if script else ScriptedDetector()
    return video_summary.summarize(
        video, zones, detector, vision_settings=SETTINGS, interactions=interactions, **options
    )


def exposure_of(summary: dict, entity_id: str) -> dict:
    return next(row for row in summary["exposure"] if row["entity_id"] == entity_id)


# ---------- tests ----------


@pytest.mark.safety
def test_a_knife_that_scooped_satay_then_touched_the_plate_gives_it_peanut(tmp_path):
    summary = summarize(write_video(tmp_path / "prep.avi"), peanut_knife_step)

    assert [touch["event_id"] for touch in summary["touches"]] == ["e-scoop", "e-touch"]
    plate = exposure_of(summary, "plate-1")
    peanut = next(reach for reach in plate["allergens"] if reach["allergen_unit_id"] == "peanut")
    assert peanut["probability"] > 0.5
    assert peanut["chain"][0]["from_id"] == "bin-1"
    assert peanut["chain"][-1]["to_id"] == "plate-1"
    for row in summary["exposure"]:
        for reach in row["allergens"]:
            validate(reach, "allergen_reach")

    text = summary["text"]
    assert "At 0:01 tool-knife-1 scooped from bin-1 (satay sauce: peanut 98%, soy 90%)." in text
    assert "At 0:01 tool-knife-1 touched plate-1." in text
    assert "plate-1 could now carry peanut (" in text


@pytest.mark.safety
def test_a_run_with_no_touches_says_so_without_calling_anything_clean(tmp_path):
    summary = summarize(write_video(tmp_path / "quiet.avi"), quiet_step)

    assert summary["touches"] == []
    assert all(row["allergens"] == [] and not row["unknown_history"] for row in summary["exposure"])
    assert "No touch was seen." in summary["text"]
    assert "No touch seen on tool-knife-1 and plate-1." in summary["text"]
    for word in ("clean", "pass", "hold"):
        assert word not in summary["text"].lower()


@pytest.mark.safety
def test_an_object_that_arrives_after_the_shift_start_has_unknown_history(tmp_path):
    def spoon_arrives(t_ms):
        return [BIN, KNIFE, PLATE] + ([SPOON] if t_ms >= 1500 else [])

    summary = summarize(write_video(tmp_path / "late.avi"), quiet_step, spoon_arrives)

    spoon = next(entity for entity in summary["entities"] if entity["label"] == "spoon")
    assert spoon["origin"] == "unknown"
    assert spoon["first_seen_t_ms"] >= SETTINGS.shift_start_ms
    assert exposure_of(summary, spoon["entity_id"])["unknown_history"] is True
    assert exposure_of(summary, "tool-knife-1")["unknown_history"] is False
    assert (
        f"{spoon['entity_id']} first appeared after the first 0:01 of video, so its history is unknown and it could "
        "carry any allergen." in summary["text"]
    )


@pytest.mark.safety
def test_a_failing_interactions_step_fails_the_summary(tmp_path):
    with pytest.raises(video_summary.SummaryError) as failure:
        summarize(write_video(tmp_path / "broken.avi"), broken_step)
    assert failure.value.code == "interactions_failed"
    assert "zone rules crashed" in failure.value.message


def test_without_zones_nothing_is_a_bin_and_the_summary_warns(tmp_path):
    summary = summarize(write_video(tmp_path / "nozones.avi"), quiet_step, zones=[])

    assert not any(entity["kind"] == "bin" for entity in summary["entities"])
    assert summary["warnings"][0].startswith("No zones were given")
    assert summary["warnings"][0] in summary["text"]


def test_frames_over_the_max_rate_are_skipped(tmp_path):
    summary = summarize(write_video(tmp_path / "fast.avi", seconds=2.0, fps=20), quiet_step, max_fps=10)

    assert summary["video"]["frames"] == 40
    assert summary["video"]["frames_processed"] == 20
    assert summary["video"]["duration_ms"] == 2000


def test_the_summary_has_the_documented_shape_and_one_video_gives_identical_json(tmp_path):
    video = write_video(tmp_path / "prep.avi")
    first = summarize(video, peanut_knife_step)
    second = summarize(video, peanut_knife_step)

    jsonschema.validate(first, SUMMARY)
    assert first["video"] == {"name": "prep.avi", "duration_ms": 3000, "frames": 30, "frames_processed": 30}
    assert video_summary.summary_json(first) == video_summary.summary_json(second)


def wait_until_finished(client: TestClient, summary_id: str) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"/summaries/{summary_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"summary {summary_id} still running after 60 s")


def test_the_http_api_runs_a_summary_and_refuses_bad_input(tmp_path):
    service = video_summary.SummaryService(
        ScriptedDetector(), ZONES, vision_settings=SETTINGS, interactions=peanut_knife_step
    )
    client = TestClient(video_summary.create_app(service, ["http://localhost:5173"], max_bytes=10 << 20))
    video = write_video(tmp_path / "prep.avi").read_bytes()

    response = client.post("/summaries", files={"video": ("prep.avi", video, "video/x-msvideo")})
    assert response.status_code == 202
    assert response.json() == {"summary_id": "s-1", "status": "running"}
    job = wait_until_finished(client, "s-1")
    assert job["status"] == "done", job
    assert job["progress"] == 1.0
    assert job["error"] is None
    assert job["summary"]["video"]["name"] == "prep.avi"
    assert exposure_of(job["summary"], "plate-1")["allergens"]

    # The zones field replaces the service's zones for that one video: here, with no bin slot.
    plating_only = json.dumps({"zones": [{"zone_id": "plating", "kind": "plating_spot", "polygon": ZONES[1].polygon}]})
    response = client.post("/summaries", files={"video": ("prep.avi", video)}, data={"zones": plating_only})
    job = wait_until_finished(client, response.json()["summary_id"])
    assert job["summary"]["warnings"][0].startswith("The zones have no bin slot")

    bad_video = client.post("/summaries", files={"video": ("notes.avi", b"not a video")})
    assert bad_video.status_code == 400
    assert bad_video.json()["error"]["code"] == "bad_video"
    bad_zones = client.post("/summaries", files={"video": ("prep.avi", video)}, data={"zones": "{not json"})
    assert bad_zones.status_code == 400
    assert bad_zones.json()["error"]["code"] == "bad_zones"
    assert client.get("/summaries/s-99").status_code == 404

    tiny = TestClient(video_summary.create_app(service, [], max_bytes=1000))
    assert tiny.post("/summaries", files={"video": ("prep.avi", video)}).status_code == 413

    allowed = client.get("/summaries/s-1", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    service.close()


@pytest.mark.safety
def test_a_failing_interactions_step_ends_the_job_failed_with_no_summary(tmp_path):
    service = video_summary.SummaryService(
        ScriptedDetector(), ZONES, vision_settings=SETTINGS, interactions=broken_step
    )
    client = TestClient(video_summary.create_app(service, [], max_bytes=10 << 20))

    response = client.post("/summaries", files={"video": ("prep.avi", write_video(tmp_path / "prep.avi").read_bytes())})
    job = wait_until_finished(client, response.json()["summary_id"])

    assert job["status"] == "failed"
    assert job["summary"] is None
    assert job["error"]["code"] == "interactions_failed"
    service.close()
