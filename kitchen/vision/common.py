"""What the vision modules hand each other: the detector's output shapes, a source frame, the per-frame output, and
VisionSettings, the one home for vision's thresholds and safety constants.

Owner: vision (agents)
Step:  vision
Reads: VISION_* environment variables (through kitchen.shared.config.load_settings)
Emits: Box, Detection, Person, SourceFrame, FrameOutput, ZoneLookup, BindRefused, VisionSettings, COCO_OBJECTS,
       KEYPOINTS
Rules: every box here is normalized [x1, y1, x2, y2] to the original camera frame (contracts: common.schema.json box);
       the change mask is computed on the frame letterboxed to clip_size, exactly as kitchen/shared/clips.py expects

Pipeline, one call per frame (run.py):
    camera.py  SourceFrame ─▶ detect.py  (list[Detection], list[Person]) ─▶ tracking.py  observation detections
    ─▶ ObservationFrame + change mask (FrameOutput) ─▶ consumers: EntityStore.ingest, interactions, record.py
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from kitchen.shared.change_mask import DEFAULT_BLUR_SIGMA_PX, DEFAULT_DECAY, DEFAULT_GAIN
from kitchen.shared.models import ObservationFrame, Zone

# Normalized [x1, y1, x2, y2] in 0-1 coordinates of the original frame.
Box = tuple[float, float, float, float]

# A keypoint: normalized x, y and the pose model's confidence that it is visible.
Keypoint = tuple[float, float, float]

# The COCO classes we track, by the detector's class index (Ultralytics' 80-class order). Bowls are both bins and
# plates: a bowl first seen in a bin slot is a bin, any other bowl is a plate (tracking.py).
COCO_OBJECTS = {42: "fork", 43: "knife", 44: "spoon", 45: "bowl"}

# COCO pose keypoint indices the hands need.
KEYPOINTS = {"left_elbow": 7, "right_elbow": 8, "left_wrist": 9, "right_wrist": 10}

# Answers "which zone is this point in?" (None for no zone); zones.py builds it from the zones file, so tracking.py
# can use zones without importing zones.py.
ZoneLookup = Callable[[tuple[float, float]], Zone | None]


class BindRefused(Exception):  # noqa: N818  (the name says what happened, like the contract's 409 codes)
    """'Start plate' refused, with the contract's 409 code: no_plate_at_spot, multiple_plates_at_spot or
    plate_already_bound (contracts/apis.md, Vision HTTP API). Binding never guesses which plate was meant."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Detection:
    """One object the detector found in one frame, before tracking: a knife, spoon, fork or bowl."""

    class_name: str
    box: Box
    confidence: float


@dataclass(frozen=True)
class Person:
    """One pose skeleton in one frame, before tracking. Hands come from its wrists, aimed away from its elbows."""

    box: Box
    confidence: float
    left_wrist: Keypoint
    right_wrist: Keypoint
    left_elbow: Keypoint
    right_elbow: Keypoint


@dataclass(frozen=True)
class SourceFrame:
    """One camera frame as a source delivers it: RGB uint8 [H, W, 3], stamped with t_ms from the injected Clock."""

    frame_id: int
    t_ms: int
    rgb: np.ndarray


@dataclass(frozen=True)
class FrameOutput:
    """What the frame loop hands every consumer for one frame.

    `observation` is the ObservationFrame record (observation_frame.schema.json). `change_mask` is float32
    [clip_size, clip_size] in [0, 1], from the frame letterboxed to clip_size. `width` and `height` are the original
    frame's, which the clip builder needs to letterbox the normalized boxes the same way.
    """

    observation: ObservationFrame
    change_mask: np.ndarray
    width: int
    height: int


@dataclass(frozen=True)
class VisionSettings:
    """Vision's settings, from VISION_* environment variables. Safety-relevant ones are logged at startup."""

    # Which backend runs the networks: "onnx" (laptop) or "hailo" (the Pi), and the deploy bundle both read.
    detector: str = "onnx"
    bundle_dir: str = "data/models/stock-yolov8s"
    camera_id: str = "cam0"
    input_size: int = 640

    # The Vision HTTP API (server.py): its port, and the browser origins allowed to call it (comma-separated; no
    # wildcards, per contracts/apis.md HTTP conventions).
    port: int = 8002
    cors_origins: str = "http://localhost:5173"

    # Detector thresholds, shared by both backends (the Hailo NMS floor sits below these: hailo.py).
    object_min_score: float = 0.35
    person_min_score: float = 0.35
    nms_iou: float = 0.45
    # A wrist below this confidence isn't a hand: no hand is invented from a guess (fail closed).
    wrist_min_confidence: float = 0.5
    # Side of the square hand box, normalized to the frame's larger side; it extends from the wrist away from the elbow.
    hand_box_size: float = 0.08

    # Tracking (ByteTrack style): high and low score bands, the IoU a match needs, and how many frames a lost track
    # waits before it ends. A track that ends is never revived: the object, if seen again, is a new entity.
    track_high_score: float = 0.5
    track_low_score: float = 0.1
    track_match_iou: float = 0.3
    track_lost_frames: int = 30

    # Known-clean source: what is visible this long after the shift starts counts as there at shift start (clean).
    shift_start_ms: int = 3000

    # The change mask, with the agreed clip geometry (AGENTS.md, Open questions: clip geometry).
    clip_size: int = 128
    change_gain: float = DEFAULT_GAIN
    change_blur_sigma_px: float = DEFAULT_BLUR_SIGMA_PX
    change_decay: float = DEFAULT_DECAY
