"""Video summary: a recorded video (not live) run through the live pipeline offline, summarized as what was on the
counter, every touch, and which allergens could have reached each tool, hand and plate.

Owner: platform (agents)
Step:  end-to-end harness (live mode's parts, driven offline from a file)
Reads: a video file; a zones file (optional); VISION_ settings (the ONNX bundle in VISION_BUNDLE_DIR);
       SUMMARY_MAX_FPS, SUMMARY_MAX_MB, SUMMARY_CORS_ORIGINS
Emits: a Summary (below) as text or JSON on stdout, or over the HTTP API (--serve)
Rules: fail closed in wording: this is information, not a verdict, so the text never says PASS, HOLD or "clean", names
       unknown-history entities as "could carry any allergen", and says "no touch seen" where nothing was seen; a
       failing interactions step, entity store or graph ends the job failed, never in a summary that looks complete;
       known-clean source: objects first seen after shift_start_ms have unknown history, as live

It runs on the laptop (ONNX Runtime), never on the Pi, whose live loop holds the Hailo. The wiring is live mode's
(tools/run_local.py, LiveRunner) on a fresh FakeStateClient: zones and the demo bins written first, then each frame
through vision's frame loop into the partner's EntityStore and the interactions step, the store synced after every
frame (edges name entities the graph must know), and finally the partner's ProvenanceGraph asked about every tool,
hand and plate. The clock is a FakeClock(0) set to each frame's own timestamp, so one file always gives the same
summary. At most SUMMARY_MAX_FPS frames per second of video are processed; the rest are skipped (zone rules are timed
in ms, so this is safe).

    uv run python tools/video_summary.py data/recordings/sample.avi --zones zones.json          # the text
    uv run python tools/video_summary.py data/recordings/sample.avi --zones zones.json --json   # the Summary
    uv run python tools/video_summary.py --serve --port 8003 --zones zones.json                # the HTTP API

HTTP API (--serve; CORS for SUMMARY_CORS_ORIGINS, default http://localhost:5173):
- POST /summaries: multipart, field `video` (the file) and optional field `zones` (zones.json text; default the
  service's --zones). Returns 202 {"summary_id": "s-1", "status": "running"}; 400 {"error": {"code": "bad_video" |
  "bad_zones", "message"}}; 413 {"error": {"code": "too_large", ...}} over SUMMARY_MAX_MB.
- GET /summaries/{summary_id}: {"summary_id", "status": "running" | "done" | "failed", "progress": 0..1,
  "summary": Summary | null, "error": {"code", "message"} | null}; 404 for an unknown ID.
Jobs run one at a time on a background thread, in the order posted.

A Summary:
    {"video": {"name", "duration_ms", "frames", "frames_processed"}, "detector": "onnx", "shift_start_ms": 3000,
     "entities": [{"entity_id", "kind", "label", "origin", "first_seen_t_ms", "last_seen_t_ms"}],
     "touches": [{"event_id", "t_start_ms", "t_end_ms", "a_id", "b_id", "action", "probability"}],
     "exposure": [{"entity_id", "kind", "unknown_history", "allergens": [AllergenReach]}],
     "text": "In 0:42 of video ...", "warnings": []}
Every t_ms in a Summary (entities' first and last seen, touches, chain steps) counts from the start of the video, by
the file's own timestamps: the screens show t_start_ms directly as m:ss.s. shift_start_ms is a cutoff on that same
timeline (what is first seen before it counts as there at shift start), not an offset. `touches` leaves out
"unimportant" edges; each allergen's `chain` is the graph's ChainStep list; `unknown_history` is true when an
unknown-history entity's state reached the entity (itself included).
"""

import argparse
import importlib
import json
import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

logger = logging.getLogger("video_summary")

DEFAULT_PORT = 8003
INTERACTIONS_STEP = "kitchen.interactions.events:live_consumer"  # live mode's step, built as factory(state, clock)
EXPOSED_KINDS = ("tool", "hand", "plate")  # what can carry food to a guest; bins are the sources
ACTION_WORDS = {
    "transfer.scoop": "scooped from",
    "transfer.contact": "touched",
    "transfer.wipe": "wiped",
}


@dataclass
class SummarySettings:
    """The summary service's settings, from SUMMARY_* environment variables."""

    max_fps: float = 10.0  # frames per second of video processed at most; the rest are skipped
    max_mb: int = 200  # the largest upload POST /summaries takes
    cors_origins: str = "http://localhost:5173"


class SummaryError(Exception):
    """A summary that can't be made. `code` is what the HTTP API reports (bad_video, interactions_failed, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WatchedConsumer:
    """A frame consumer that keeps its first error.

    Vision's frame loop logs a failing consumer and carries on, which is right for a live camera but would let a
    summary miss touches and still look complete. The summary checks `error` after every frame and fails instead.
    """

    def __init__(self, name: str, consumer) -> None:
        self.name = name
        self.consumer = consumer
        self.error: Exception | None = None

    def __call__(self, output) -> None:
        try:
            self.consumer(output)
        except Exception as error:
            if self.error is None:
                self.error = error
            raise


# ---------- making one summary ----------


def load_factory(spec):
    """The interactions step's factory: the callable named "module:attribute", or `spec` itself if it's callable."""
    if callable(spec):
        return spec
    module_name, _, attribute = spec.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def probe_video(path: Path) -> None:
    """Raise SummaryError("bad_video") unless OpenCV can open the file and read a first frame."""
    import cv2

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened() or not capture.read()[0]:
            raise SummaryError("bad_video", "OpenCV can't read a frame from this file")
    finally:
        capture.release()


def summarize(
    video_path: Path,
    zones: list,
    detector,
    name: str | None = None,
    vision_settings=None,
    interactions=INTERACTIONS_STEP,
    max_fps: float = SummarySettings.max_fps,
    progress=None,
) -> dict:
    """Run the video through live mode's parts offline and return its Summary. Raises SummaryError.

    `detector` is an opened vision detector (open_detector), reused across summaries because it takes seconds to
    load. `progress`, if given, is called with the fraction of the video read so far.
    """
    import cv2

    from kitchen.entities.store import EntityStore
    from kitchen.graph import ProvenanceGraph
    from kitchen.orders.bins import DEMO_BINS, save_bins
    from kitchen.shared.clock import FakeClock
    from kitchen.shared.fakes import FakeStateClient
    from kitchen.shared.interfaces import GraphUnavailable
    from kitchen.vision.camera import VideoSource
    from kitchen.vision.common import VisionSettings
    from kitchen.vision.run import build_frame_loop, entity_store_consumer
    from kitchen.vision.zones import push_zones

    settings = vision_settings or VisionSettings()
    clock = FakeClock(0)
    state = FakeStateClient()
    push_zones(state, zones)
    profiles = save_bins(state)  # the hard-coded demo bins, as live mode writes them at startup

    store = EntityStore(clock)
    try:
        step = load_factory(interactions)(state, clock)
    except Exception as error:
        raise SummaryError(
            "interactions_failed", f"the interactions step {interactions} can't start: {error}"
        ) from error
    watched = [
        WatchedConsumer("entities", entity_store_consumer(store)),
        WatchedConsumer("interactions", step),
    ]
    loop = build_frame_loop(settings, zones, watched, detector=detector)

    try:
        source = VideoSource(video_path, clock)
    except OSError as error:
        raise SummaryError("bad_video", str(error)) from error
    total_frames = int(source.capture.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_gap_ms = 1000 / max_fps
    frames = processed = 0
    previous_t_ms = last_t_ms = last_processed_t_ms = None
    for frame in source:
        frames += 1
        previous_t_ms, last_t_ms = last_t_ms, frame.t_ms
        if last_processed_t_ms is None or frame.t_ms - last_processed_t_ms >= frame_gap_ms:
            run_frame(loop, watched, store, state, clock, frame)
            last_processed_t_ms = frame.t_ms
            processed += 1
        if progress is not None and total_frames > 0:
            progress(min(frames / total_frames, 0.99))
    if processed == 0:
        raise SummaryError("bad_video", "the video has no frames")

    end_t_ms = last_processed_t_ms
    graph = ProvenanceGraph(state)
    entities = store.snapshot()
    try:
        exposure = [exposure_of(graph, entity, end_t_ms) for entity in entities if entity["kind"] in EXPOSED_KINDS]
    except GraphUnavailable as error:
        raise SummaryError("graph_failed", f"the graph can't answer: {error}") from error

    touches = touches_in(state.list_interactions(0, end_t_ms))
    bins = {entity_id: {"label": DEMO_BINS[entity_id]["label"], **profile} for entity_id, profile in profiles.items()}
    warnings = summary_warnings(zones, touches, end_t_ms)
    summary = {
        "video": {
            "name": name or Path(video_path).name,
            "duration_ms": last_t_ms + (last_t_ms - previous_t_ms if previous_t_ms is not None else 0),
            "frames": frames,
            "frames_processed": processed,
        },
        "detector": loop.detector.backend,
        "shift_start_ms": settings.shift_start_ms,
        "entities": [entity_row(entity) for entity in entities],
        "touches": touches,
        "exposure": exposure,
        "warnings": warnings,
    }
    summary["text"] = summary_text(summary, bins)
    return summary


def run_frame(loop, watched: list[WatchedConsumer], store, state, clock, frame) -> None:
    """One frame through vision and its consumers, then the entity store synced. Raises SummaryError on any error."""
    clock.set(frame.t_ms)  # the frame's own time, so the interactions step and the store see the video's clock
    try:
        loop.step(frame)
    except Exception as error:
        raise SummaryError("vision_failed", f"vision failed at {clock_text(frame.t_ms)}: {error}") from error
    for consumer in watched:
        if consumer.error is not None:
            raise SummaryError(
                f"{consumer.name}_failed",
                f"the {consumer.name} step failed at {clock_text(frame.t_ms)}: {consumer.error}",
            )
    try:
        store.sync_once(state)
    except Exception as error:
        raise SummaryError(
            "entities_failed", f"the entity store couldn't sync at {clock_text(frame.t_ms)}: {error}"
        ) from error


def exposure_of(graph, entity: dict, end_t_ms: int) -> dict:
    """What the graph says could have reached one tool, hand or plate by the end of the video."""
    entity_id = entity["entity_id"]
    return {
        "entity_id": entity_id,
        "kind": entity["kind"],
        "unknown_history": bool(graph.unknown_ancestors(entity_id, end_t_ms)),
        "allergens": graph.reach(entity_id, end_t_ms),
    }


def entity_row(entity: dict) -> dict:
    """The Summary's row for one entity."""
    keys = ("entity_id", "kind", "label", "origin", "first_seen_t_ms", "last_seen_t_ms")
    return {key: entity[key] for key in keys}


def touches_in(events: list[dict]) -> list[dict]:
    """The Summary's touches: every edge but "unimportant" ones, in the graph's order."""
    keys = ("event_id", "t_start_ms", "t_end_ms", "a_id", "b_id", "action", "probability")
    kept = [{key: event[key] for key in keys} for event in events if event["action"] != "unimportant"]
    return sorted(kept, key=lambda touch: (touch["t_start_ms"], touch["t_end_ms"], touch["event_id"]))


def summary_warnings(zones: list, touches: list[dict], end_t_ms: int) -> list[str]:
    """What the reader must know before trusting the summary."""
    from kitchen.interactions.settings import load_interaction_settings

    warnings = []
    if not zones:
        warnings.append(
            "No zones were given, so no bowl became a bin and nothing on the counter is an allergen source."
        )
    elif not any(zone.kind == "bin_slot" for zone in zones):
        warnings.append("The zones have no bin slot, so no bowl became a bin and nothing is an allergen source.")

    # The interactions step writes a touch's first hit at once and the rest once it ends, so a touch still going at
    # the last frame may count only by its start.
    merge_gap_ms = load_interaction_settings().merge_gap_ms
    running = [touch for touch in touches if touch["t_end_ms"] >= end_t_ms - merge_gap_ms]
    if running:
        last = running[-1]
        warnings.append(
            f"The video ended during a touch ({last['a_id']} and {last['b_id']} at {clock_text(last['t_end_ms'])}), "
            "so the end of that touch may not be counted."
        )
    return warnings


# ---------- the text: plain sentences built in code ----------


def clock_text(t_ms: int) -> str:
    """t_ms as m:ss of video, e.g. 65000 -> "1:05"."""
    seconds = t_ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def percent(probability: float) -> str:
    return f"{round(probability * 100)}%"


def allergen_name(unit_id: str) -> str:
    return unit_id.replace("_", " ")


def join_words(words: list[str]) -> str:
    """["a", "b", "c"] -> "a, b and c"."""
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def bin_detail(bin_id: str, bins: dict) -> str:
    """What a bin holds, e.g. "satay sauce: peanut 98%, soy 90%". A bin without a profile could hold anything."""
    profile = bins.get(bin_id)
    if profile is None or profile["unknown"]:
        return "could carry any allergen"
    listed = sorted(profile["probabilities"].items(), key=lambda item: (-item[1], item[0]))
    allergens = ", ".join(f"{allergen_name(unit)} {percent(p)}" for unit, p in listed) or "no listed allergens"
    return f"{profile['label']}: {allergens}"


def entity_name(entity: dict, bins: dict) -> str:
    """How the overview names an entity: a bin with what it holds, anything else by its ID."""
    if entity["kind"] == "bin" and entity["entity_id"] in bins:
        return f"{entity['entity_id']} ({bins[entity['entity_id']]['label']})"
    return entity["entity_id"]


def merged_touches(touches: list[dict]) -> list[dict]:
    """One line per touch for the text: the interactions step writes a touch's first hit apart from the rest, and
    cuts long touches, so edges on the same pair and action that follow on are told as one touch."""
    from kitchen.interactions.settings import load_interaction_settings

    merge_gap_ms = load_interaction_settings().merge_gap_ms
    told: list[dict] = []
    for touch in touches:
        same = [
            earlier
            for earlier in told
            if (earlier["a_id"], earlier["b_id"], earlier["action"]) == (touch["a_id"], touch["b_id"], touch["action"])
            and touch["t_start_ms"] - earlier["t_end_ms"] <= merge_gap_ms
        ]
        if same:
            same[-1]["t_end_ms"] = max(same[-1]["t_end_ms"], touch["t_end_ms"])
        else:
            told.append(dict(touch))
    return told


def touch_sentence(touch: dict, kinds: dict, bins: dict) -> str:
    """One touch, e.g. "At 0:05 tool-knife-1 scooped from bin-1 (satay sauce: peanut 98%, soy 90%)."."""
    a_id, b_id, action = touch["a_id"], touch["b_id"], touch["action"]
    when = f"At {clock_text(touch['t_start_ms'])}"
    if action == "clean.wash":
        return f"{when} {a_id} was washed."
    words = ACTION_WORDS.get(action)
    sentence = f"{when} {a_id} {words} {b_id}" if words else f"{when} {a_id} and {b_id} interacted ({action})"
    bin_ids = [entity_id for entity_id in (a_id, b_id) if kinds.get(entity_id) == "bin"]
    if len(bin_ids) == 1:
        sentence += f" ({bin_detail(bin_ids[0], bins)})"
    elif len(bin_ids) == 2:
        sentence += " (" + "; ".join(f"{bin_id}: {bin_detail(bin_id, bins)}" for bin_id in bin_ids) + ")"
    return sentence + "."


def unknown_sentences(entities: list[dict], shift_start_ms: int) -> list[str]:
    """The entities with unknown history, as live: first seen after the shift start, or seen again under a new ID."""
    unknown = [entity for entity in entities if entity["origin"] == "unknown"]
    late = [entity["entity_id"] for entity in unknown if entity["first_seen_t_ms"] >= shift_start_ms]
    early = [entity["entity_id"] for entity in unknown if entity["first_seen_t_ms"] < shift_start_ms]
    sentences = []
    if late:
        their, they = ("its", "it") if len(late) == 1 else ("their", "they")
        sentences.append(
            f"{join_words(late)} first appeared after the first {clock_text(shift_start_ms)} of video, so {their} "
            f"history is unknown and {they} could carry any allergen."
        )
    if early:
        have, they = ("has", "it") if len(early) == 1 else ("have", "they")
        sentences.append(f"{join_words(early)} {have} unknown history, so {they} could carry any allergen.")
    return sentences


def exposure_sentences(exposure: list[dict], unknown_ids: set[str], touched_ids: set[str]) -> list[str]:
    """What could have reached each tool, hand and plate. Never "clean": where nothing was seen, it says so.

    Entities whose own history is unknown were named by unknown_sentences() already.
    """
    sentences = []
    untouched, unreached = [], []
    for row in exposure:
        entity_id = row["entity_id"]
        if row["unknown_history"]:
            if entity_id not in unknown_ids:
                sentences.append(
                    f"{entity_id} could carry any allergen: something whose history is unknown touched it."
                )
        elif row["allergens"]:
            listed = sorted(row["allergens"], key=lambda reach: (-reach["probability"], reach["allergen_unit_id"]))
            words = [
                f"{allergen_name(reach['allergen_unit_id'])} ({percent(reach['probability'])})" for reach in listed
            ]
            sentences.append(f"{entity_id} could now carry {join_words(words)}.")
        elif entity_id in touched_ids:
            unreached.append(entity_id)
        else:
            untouched.append(entity_id)
    if unreached:
        sentences.append(f"No allergen seen reaching {join_words(unreached)}.")
    if untouched:
        sentences.append(f"No touch seen on {join_words(untouched)}.")
    return sentences


def summary_text(summary: dict, bins: dict) -> str:
    """The Summary as plain sentences: what was seen, warnings, unknown history, each touch, then each exposure."""
    video, entities = summary["video"], summary["entities"]
    kinds = {entity["entity_id"]: entity["kind"] for entity in entities}
    # Hands come and go under new IDs all the time, so they're counted; everything else is named.
    named = [entity_name(entity, bins) for entity in entities if entity["kind"] != "hand"]
    hands = sum(entity["kind"] == "hand" for entity in entities)
    if hands:
        named.append("1 hand" if hands == 1 else f"{hands} hands")
    sentences = [
        f"In {clock_text(video['duration_ms'])} of video ({video['frames_processed']} of {video['frames']} frames "
        f"looked at), the camera saw {join_words(named) or 'nothing it tracks'}."
    ]
    sentences += summary["warnings"]
    sentences += unknown_sentences(entities, summary["shift_start_ms"])

    told = merged_touches(summary["touches"])
    sentences += [touch_sentence(touch, kinds, bins) for touch in told] or ["No touch was seen."]
    unknown_ids = {entity["entity_id"] for entity in entities if entity["origin"] == "unknown"}
    touched_ids = {entity_id for touch in told for entity_id in (touch["a_id"], touch["b_id"])}
    sentences += exposure_sentences(summary["exposure"], unknown_ids, touched_ids)
    return " ".join(sentences)


def summary_json(summary: dict) -> str:
    """The Summary as canonical JSON, so the same video always gives the same bytes."""
    return json.dumps(summary, sort_keys=True, indent=2) + "\n"


# ---------- the HTTP service ----------


class SummaryService:
    """Summary jobs by ID, run one at a time on a background thread in the order posted.

    Uploads are kept in a temporary folder until their job ends. `detector` is opened once and shared by every job,
    which is safe because only one job runs at a time.
    """

    def __init__(
        self,
        detector,
        default_zones: list,
        vision_settings=None,
        interactions=INTERACTIONS_STEP,
        max_fps: float = SummarySettings.max_fps,
    ) -> None:
        self.detector = detector
        self.default_zones = default_zones
        self.vision_settings = vision_settings
        self.interactions = interactions
        self.max_fps = max_fps
        self.uploads = Path(tempfile.mkdtemp(prefix="video-summary-"))
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="summaries").start()

    def new_upload_path(self, filename: str) -> Path:
        """A fresh path in the uploads folder, keeping the file's extension (OpenCV picks a reader by it)."""
        handle, path = tempfile.mkstemp(dir=self.uploads, suffix=Path(filename).suffix)
        os.close(handle)
        return Path(path)

    def submit(self, video_path: Path, name: str, zones: list | None) -> str:
        """Queue a summary of the uploaded file; returns its ID ("s-1", "s-2", ...). The job owns the file now."""
        with self._lock:
            summary_id = f"s-{len(self._jobs) + 1}"
            self._jobs[summary_id] = {
                "summary_id": summary_id,
                "status": "running",
                "progress": 0.0,
                "summary": None,
                "error": None,
            }
        self._queue.put((summary_id, video_path, name, self.default_zones if zones is None else zones))
        return summary_id

    def get(self, summary_id: str) -> dict | None:
        """A copy of the job's status, or None for an unknown ID."""
        with self._lock:
            job = self._jobs.get(summary_id)
            return None if job is None else dict(job)

    def _update(self, summary_id: str, **fields) -> None:
        with self._lock:
            self._jobs[summary_id].update(fields)

    def _work(self) -> None:
        """The job thread: summarize each queued upload, then delete it. A job never ends in a partial summary."""
        while True:
            summary_id, video_path, name, zones = self._queue.get()
            try:
                summary = summarize(
                    video_path,
                    zones,
                    self.detector,
                    name=name,
                    vision_settings=self.vision_settings,
                    interactions=self.interactions,
                    max_fps=self.max_fps,
                    progress=lambda fraction, job=summary_id: self._update(job, progress=round(fraction, 3)),
                )
                self._update(summary_id, status="done", progress=1.0, summary=summary)
            except SummaryError as error:
                logger.error("summary %s failed: %s", summary_id, error.message)
                self._update(summary_id, status="failed", error={"code": error.code, "message": error.message})
            except Exception as error:
                logger.exception("summary %s failed", summary_id)
                self._update(summary_id, status="failed", error={"code": "internal", "message": str(error)})
            finally:
                video_path.unlink(missing_ok=True)

    def close(self) -> None:
        """Delete the uploads folder."""
        shutil.rmtree(self.uploads, ignore_errors=True)


def error_response(status_code: int, code: str, message: str):
    """The standard error body from contracts/apis.md (HTTP conventions)."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def create_app(service: SummaryService, cors_origins: list[str], max_bytes: int):
    """The HTTP API above, on `service`. Uploads over `max_bytes` get 413."""
    from fastapi import FastAPI, File, Form, UploadFile
    from fastapi.middleware.cors import CORSMiddleware

    from kitchen.vision.zones import ZoneFileError, parse_zones

    app = FastAPI(title="Video summary")
    app.add_middleware(
        CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"]
    )

    @app.post("/summaries", status_code=202)
    async def post_summary(
        video: Annotated[UploadFile | None, File()] = None, zones: Annotated[str | None, Form()] = None
    ):
        """Take an upload and queue its summary."""
        if video is None:
            return error_response(400, "bad_video", "no file in the form field 'video'")
        parsed_zones = None
        if zones:
            try:
                parsed_zones = parse_zones(json.loads(zones))
            except (ValueError, ZoneFileError) as error:
                return error_response(400, "bad_zones", f"zones: {error}")

        path = service.new_upload_path(video.filename or "video")
        size = 0
        with open(path, "wb") as handle:
            while chunk := await video.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    break
                handle.write(chunk)
        if size > max_bytes:
            path.unlink(missing_ok=True)
            return error_response(413, "too_large", f"the video is over {max_bytes // (1 << 20)} MB")
        try:
            probe_video(path)
        except SummaryError as error:
            path.unlink(missing_ok=True)
            return error_response(400, error.code, error.message)

        summary_id = service.submit(path, video.filename or path.name, parsed_zones)
        return {"summary_id": summary_id, "status": "running"}

    @app.get("/summaries/{summary_id}")
    def get_summary(summary_id: str):
        """A job's status, and its Summary once done."""
        job = service.get(summary_id)
        if job is None:
            return error_response(404, "not_found", f"no summary {summary_id}")
        return job

    return app


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    """Summarize one video and print it, or serve the HTTP API."""
    from kitchen.shared.config import load_settings, log_settings
    from kitchen.vision.common import VisionSettings
    from kitchen.vision.detect import open_detector
    from kitchen.vision.zones import load_zones

    cli = argparse.ArgumentParser(description="Summarize a recorded video: what was seen, every touch, every exposure.")
    cli.add_argument("video", nargs="?", type=Path, help="the video file to summarize")
    cli.add_argument("--zones", type=Path, help="zones.json (kitchen/vision/zones.py); without it nothing is a bin")
    cli.add_argument("--json", action="store_true", help="print the Summary as JSON instead of the text")
    cli.add_argument("--serve", action="store_true", help="serve the HTTP API instead of summarizing one file")
    cli.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = cli.parse_args(argv)
    if args.serve == (args.video is not None):
        cli.error("give a video file, or --serve")

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(SummarySettings, "SUMMARY_")
    vision_settings = load_settings(VisionSettings, "VISION_")
    log_settings(settings)
    log_settings(vision_settings)
    zones = load_zones(args.zones) if args.zones else []
    detector = open_detector(vision_settings)

    if args.serve:
        import uvicorn

        service = SummaryService(detector, zones, vision_settings=vision_settings, max_fps=settings.max_fps)
        origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
        app = create_app(service, origins, settings.max_mb * (1 << 20))
        print(f"video_summary: POST /summaries on http://localhost:{args.port}, CORS for {origins}", flush=True)
        try:
            uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
        finally:
            service.close()
        return 0

    try:
        summary = summarize(args.video, zones, detector, vision_settings=vision_settings, max_fps=settings.max_fps)
    except SummaryError as error:
        print(f"video_summary: failed ({error.code}): {error.message}", file=sys.stderr)
        return 1
    print(summary_json(summary) if args.json else summary["text"], end="" if args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
