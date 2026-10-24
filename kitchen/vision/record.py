"""Recording and replay: ObservationFrames to frames.jsonl and change masks to change.npz, replayed byte for byte.

Owner: vision (agents)
Step:  vision
Reads: FrameOutputs from the frame loop (run.py); a recording directory to replay
Emits: frames.jsonl + change.npz + manifest.json in a recording directory (under data/, which git ignores);
       read_recording and ReplaySource, which hand the FrameOutputs back
Rules: time runs forward: a recording's t_ms is strictly increasing and replay keeps the recorded t_ms; every line
       validates against observation_frame; a recording whose frames don't match its manifest is refused, not replayed

A recording directory (record it under data/recordings/<name>/; large artifacts never go in git):
- frames.jsonl   one ObservationFrame per line, canonical JSON (sorted keys, compact separators), so the same frames
                 always give the same bytes
- change.npz     key "change": uint8 [N, S, S], each frame's change mask times 255, rounded (S is the clip size).
                 Divide by 255 before the clip builder, which rejects integer masks (contracts/apis.md)
- manifest.json  what the recording is, with these keys written by the Recorder:
                     camera_id          the frames' camera_id
                     width, height      the original camera frame's size (the clip builder's source_size)
                     clip_size          S, the change mask's side
                     frame_count        N, the number of lines and masks
                     first_t_ms         t_ms of the first frame (null if empty)
                     last_t_ms          t_ms of the last frame (null if empty)
                     frames_sha256      sha256 of frames.jsonl, checked on replay
                 and every key the caller passed, which must include change_mask_params ({gain, blur_sigma_px,
                 decay}, as the clip builder records them) and should include source, zones_sha256, shift_start and
                 model versions
"""

import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from kitchen.shared.clock import SystemClock
from kitchen.shared.interfaces import Clock
from kitchen.shared.models import ObservationFrame, validate
from kitchen.vision.common import FrameOutput, VisionSettings

FRAMES_FILE = "frames.jsonl"
CHANGE_FILE = "change.npz"
MANIFEST_FILE = "manifest.json"

# Raw uint8 masks stream here while recording, so a long shift never holds every mask in memory.
PARTIAL_CHANGE_FILE = "change.u8.partial"

# Keys the Recorder fills in itself; a caller passing one would be silently overwritten, so that's refused.
RECORDER_KEYS = {"camera_id", "width", "height", "clip_size", "frame_count", "first_t_ms", "last_t_ms", "frames_sha256"}


def change_mask_params(settings: VisionSettings) -> dict:
    """The change-mask parameters a recording's manifest carries, named as the clip builder records them."""
    return {
        "gain": settings.change_gain,
        "blur_sigma_px": settings.change_blur_sigma_px,
        "decay": settings.change_decay,
    }


def canonical_json(observation: ObservationFrame) -> str:
    """One ObservationFrame as canonical JSON: sorted keys, no spaces, NaN refused, so equal frames give equal bytes."""
    return json.dumps(observation, sort_keys=True, separators=(",", ":"), allow_nan=False)


def mask_to_uint8(mask: np.ndarray) -> np.ndarray:
    """A float change mask in [0, 1] as stored: uint8, mask * 255 rounded. Refuses anything outside [0, 1]."""
    if not np.issubdtype(mask.dtype, np.floating):
        raise ValueError(f"change mask must be float in [0, 1], got {mask.dtype}")
    if not np.all((mask >= 0) & (mask <= 1)):
        raise ValueError("change mask has values outside [0, 1] (or NaN)")
    return np.round(mask * 255).astype(np.uint8)


def mask_from_uint8(stored: np.ndarray) -> np.ndarray:
    """A stored uint8 mask back to float32 in [0, 1], ready for the clip builder."""
    return stored.astype(np.float32) / 255


class Recorder:
    """Writes FrameOutputs to a recording directory: frames.jsonl as they come, change.npz and manifest.json on close.

    Use it as a context manager, or call close(); a recording that was never closed has no manifest and won't replay.
    """

    def __init__(self, out_dir: str | Path, manifest: dict):
        reserved = RECORDER_KEYS & manifest.keys()
        if reserved:
            raise ValueError(f"the Recorder writes these manifest keys itself: {sorted(reserved)}")
        if "change_mask_params" not in manifest:
            raise ValueError("the manifest needs change_mask_params (see change_mask_params(settings))")

        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.caller_manifest = dict(manifest)

        self.frames_file = open(self.out_dir / FRAMES_FILE, "wb")
        self.masks_file = open(self.out_dir / PARTIAL_CHANGE_FILE, "wb")
        self.frames_hash = hashlib.sha256()

        # Set from the first frame; every later frame must match it.
        self.camera_id = None
        self.width = None
        self.height = None
        self.clip_size = None
        self.first_t_ms = None
        self.last_t_ms = None
        self.frame_count = 0

    def write(self, output: FrameOutput) -> None:
        """Append one frame: validate it, check it continues the recording, then write its line and its mask."""
        observation = validate(output.observation, "observation_frame")
        self.check_continues(output)
        stored_mask = mask_to_uint8(output.change_mask)

        line = (canonical_json(observation) + "\n").encode()
        self.frames_file.write(line)
        self.frames_hash.update(line)
        self.masks_file.write(stored_mask.tobytes())

        if self.frame_count == 0:
            self.first_t_ms = observation["t_ms"]
        self.last_t_ms = observation["t_ms"]
        self.frame_count += 1

    def check_continues(self, output: FrameOutput) -> None:
        """Refuse a frame that isn't after the last one (time runs forward) or doesn't match the first frame's shape."""
        observation = output.observation
        mask_shape = output.change_mask.shape
        if len(mask_shape) != 2 or mask_shape[0] != mask_shape[1]:
            raise ValueError(f"change mask must be square [S, S], got {mask_shape}")

        if self.frame_count == 0:
            self.camera_id = observation["camera_id"]
            self.width = output.width
            self.height = output.height
            self.clip_size = mask_shape[0]
            return

        # Safety rule 5: a recording whose t_ms repeats or goes back would replay a path out of order.
        if observation["t_ms"] <= self.last_t_ms:
            raise ValueError(f"time runs forward: t_ms {observation['t_ms']} after {self.last_t_ms}")
        if observation["camera_id"] != self.camera_id:
            raise ValueError(f"one camera per recording: {observation['camera_id']} after {self.camera_id}")
        if (output.width, output.height) != (self.width, self.height):
            raise ValueError(f"frame size changed to {output.width}x{output.height} from {self.width}x{self.height}")
        if mask_shape[0] != self.clip_size:
            raise ValueError(f"change mask size changed to {mask_shape[0]} from {self.clip_size}")

    def close(self) -> None:
        """Finish the recording: pack the masks into change.npz and write manifest.json. Safe to call twice."""
        if self.frames_file.closed:
            return
        self.frames_file.close()
        self.masks_file.close()

        partial_path = self.out_dir / PARTIAL_CHANGE_FILE
        side = self.clip_size or 0
        masks = np.fromfile(partial_path, dtype=np.uint8).reshape(self.frame_count, side, side)
        np.savez_compressed(self.out_dir / CHANGE_FILE, change=masks)
        partial_path.unlink()

        manifest = {
            **self.caller_manifest,
            "camera_id": self.camera_id,
            "width": self.width,
            "height": self.height,
            "clip_size": self.clip_size,
            "frame_count": self.frame_count,
            "first_t_ms": self.first_t_ms,
            "last_t_ms": self.last_t_ms,
            "frames_sha256": self.frames_hash.hexdigest(),
        }
        (self.out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def read_manifest(recording_dir: str | Path) -> dict:
    """The recording's manifest.json. A missing one means the recording was never closed, so it's refused."""
    path = Path(recording_dir) / MANIFEST_FILE
    if not path.is_file():
        raise FileNotFoundError(f"no {MANIFEST_FILE} in {recording_dir}: the recording is missing or was never closed")
    return json.loads(path.read_text())


def read_recording(recording_dir: str | Path) -> Iterator[FrameOutput]:
    """Yield a recording's frames as FrameOutputs, with the recorded t_ms and masks back as float32 in [0, 1].

    The frames file must match the manifest's sha256 and frame count, and t_ms must be strictly increasing, so a
    damaged or hand-edited recording fails loudly instead of replaying something that never happened.
    """
    recording_dir = Path(recording_dir)
    manifest = read_manifest(recording_dir)

    frames_bytes = (recording_dir / FRAMES_FILE).read_bytes()
    if hashlib.sha256(frames_bytes).hexdigest() != manifest["frames_sha256"]:
        raise ValueError(f"{FRAMES_FILE} in {recording_dir} doesn't match the manifest's frames_sha256")

    lines = frames_bytes.decode().splitlines()
    with np.load(recording_dir / CHANGE_FILE) as change_file:
        masks = change_file["change"]
    if not len(lines) == len(masks) == manifest["frame_count"]:
        raise ValueError(
            f"{recording_dir}: {len(lines)} frames, {len(masks)} masks, manifest says {manifest['frame_count']}"
        )

    last_t_ms = -1
    for line, stored_mask in zip(lines, masks, strict=True):
        observation = validate(json.loads(line), "observation_frame")
        if observation["t_ms"] <= last_t_ms:
            raise ValueError(f"time runs forward: t_ms {observation['t_ms']} after {last_t_ms} in {recording_dir}")
        last_t_ms = observation["t_ms"]
        yield FrameOutput(
            observation=observation,
            change_mask=mask_from_uint8(stored_mask),
            width=manifest["width"],
            height=manifest["height"],
        )


class ReplaySource:
    """A recording as a frame source: FrameOutputs with their recorded t_ms, as fast as possible or in real time.

    With pace=True each frame waits until as much time has passed on `clock` (SystemClock if none is given) as passed
    between it and the first frame in the recording. Pacing never changes a frame's t_ms.
    """

    def __init__(self, recording_dir: str | Path, clock: Clock | None = None, pace: bool = False):
        self.recording_dir = Path(recording_dir)
        self.manifest = read_manifest(self.recording_dir)
        self.pace = pace
        self.clock = clock if clock is not None else SystemClock()

    def __iter__(self) -> Iterator[FrameOutput]:
        """Yield every recorded frame once, in order."""
        replay_start_ms = self.clock.now_ms()
        first_t_ms = self.manifest["first_t_ms"]
        for output in read_recording(self.recording_dir):
            if self.pace:
                due_ms = replay_start_ms + output.observation["t_ms"] - first_t_ms
                wait_ms = due_ms - self.clock.now_ms()
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000)
            yield output
