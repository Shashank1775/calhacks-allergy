"""Recording and replay: ObservationFrames to frames.jsonl and change masks to change.npz, replayed byte for byte.

Owner: vision (agents)
Step:  vision
Reads: FrameOutputs from the frame loop (run.py); a recording directory to replay
Emits: frames.jsonl + change.npz + manifest.json in a recording directory (under data/, which git ignores);
       read_recording and ReplaySource, which hand the FrameOutputs back
Rules: time runs forward: a recording's t_ms is strictly increasing and replay keeps the recorded t_ms; every line
       validates against observation_frame; a recording whose frames don't match its manifest is refused, not replayed;
       an existing recording is never overwritten; masks stream to and from disk, so a long shift fits in memory

A recording directory (record it under data/recordings/<name>/; large artifacts never go in git):
- frames.jsonl   one ObservationFrame per line, canonical JSON (sorted keys, compact separators), so the same frames
                 always give the same bytes
- change.npz     key "change": uint8 [N, S, S], each frame's change mask times 255, rounded (S is the clip size).
                 Divide by 255 before the clip builder, which rejects integer masks (contracts/apis.md). It is a plain
                 npz (np.load reads it), written and read one mask at a time
- manifest.json  what the recording is, with these keys written by the Recorder:
                     camera_id          the frames' camera_id
                     width, height      the original camera frame's size (the clip builder's source_size)
                     clip_size          S, the change mask's side
                     frame_count        N, the number of lines and masks
                     first_t_ms         t_ms of the first frame (null if empty)
                     last_t_ms          t_ms of the last frame (null if empty)
                     frames_sha256      sha256 of frames.jsonl, checked on replay
                 and every key the caller passed, which must be plain JSON (checked when recording starts) and must
                 include change_mask_params ({gain, blur_sigma_px, decay}, as the clip builder records them) and
                 should include source, zones_sha256, shift_start and model versions
                 manifest.json is written last, so a directory that has one holds a whole recording
"""

import hashlib
import json
import shutil
import time
import zipfile
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

# The array inside change.npz, as np.savez names the key "change".
CHANGE_ENTRY = "change.npy"

# Bytes copied at a time when packing the masks into change.npz.
COPY_CHUNK_BYTES = 64 * 1024

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
        # A value JSON can't hold (a Path, a numpy number, NaN) would only fail at close(), after a whole session was
        # recorded, and leave a recording with no manifest that can't be replayed. So it's refused before any frame.
        try:
            self.caller_manifest = json.loads(json.dumps(manifest, allow_nan=False))
        except (TypeError, ValueError) as error:
            raise ValueError(f"the manifest must be plain JSON: {error}") from error

        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # Opening an earlier recording's files for writing would wipe it, so a directory that holds one is refused.
        for name in (FRAMES_FILE, CHANGE_FILE, MANIFEST_FILE, PARTIAL_CHANGE_FILE):
            if (self.out_dir / name).exists():
                raise FileExistsError(f"{self.out_dir} already holds a recording ({name}); record into a new directory")

        self.frames_file = open(self.out_dir / FRAMES_FILE, "xb")
        self.masks_file = open(self.out_dir / PARTIAL_CHANGE_FILE, "xb")
        self.frames_hash = hashlib.sha256()
        self.closed = False

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
            # int() so a numpy size from a frame's shape still writes to manifest.json.
            self.width = int(output.width)
            self.height = int(output.height)
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
        """Finish the recording: pack the masks into change.npz, then write manifest.json, then drop the partial file.

        Safe to call twice. If a step fails (a full disk, say) nothing is lost: the partial file stays until the
        manifest is written, and calling close() again retries.
        """
        if self.closed:
            return
        self.frames_file.close()
        self.masks_file.close()

        partial_path = self.out_dir / PARTIAL_CHANGE_FILE
        side = self.clip_size or 0
        pack_masks(partial_path, self.out_dir / CHANGE_FILE, (self.frame_count, side, side))

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
        partial_path.unlink()
        self.closed = True

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def pack_masks(partial_path: Path, change_path: Path, shape: tuple[int, int, int]) -> None:
    """Write the raw uint8 masks in `partial_path` as change.npz, a chunk at a time, so the masks never all sit in memory.

    The result is what np.savez_compressed would write: a zip with one deflated "change.npy" entry.
    """
    header = {"descr": np.lib.format.dtype_to_descr(np.dtype(np.uint8)), "fortran_order": False, "shape": shape}
    with zipfile.ZipFile(change_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # force_zip64 because a long shift's masks can pass the 2 GiB a plain zip entry holds.
        with archive.open(CHANGE_ENTRY, "w", force_zip64=True) as entry, open(partial_path, "rb") as partial_file:
            np.lib.format.write_array_header_1_0(entry, header)
            shutil.copyfileobj(partial_file, entry, COPY_CHUNK_BYTES)


def iter_masks(change_path: Path, frame_count: int, side: int) -> Iterator[np.ndarray]:
    """Yield change.npz's uint8 masks one at a time, refusing a file whose array isn't [frame_count, side, side]."""
    with zipfile.ZipFile(change_path) as archive, archive.open(CHANGE_ENTRY) as entry:
        if np.lib.format.read_magic(entry) != (1, 0):
            raise ValueError(f"{change_path} wasn't written by the Recorder (npy format version)")
        shape, fortran_order, dtype = np.lib.format.read_array_header_1_0(entry)
        if dtype != np.uint8 or fortran_order or shape != (frame_count, side, side):
            raise ValueError(
                f"{change_path}: masks are {dtype} {shape}, manifest says uint8 {(frame_count, side, side)}"
            )
        for _ in range(frame_count):
            stored = np.frombuffer(entry.read(side * side), dtype=np.uint8)
            if stored.size != side * side:
                raise ValueError(f"{change_path} ends before its {frame_count} masks")
            yield stored.reshape(side, side)


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
    if len(lines) != manifest["frame_count"]:
        raise ValueError(f"{recording_dir}: {len(lines)} frames, manifest says {manifest['frame_count']}")
    # The mask count and size are checked against the manifest before the first frame is yielded.
    masks = iter_masks(recording_dir / CHANGE_FILE, manifest["frame_count"], manifest["clip_size"] or 0)

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


def main(argv: list[str] | None = None) -> int:
    """CLI: record a video or camera through the whole vision pipeline into a new recording directory.

        uv run python -m kitchen.vision.record --source data/recordings/prep.mp4 --out data/recordings/run1
        uv run python -m kitchen.vision.record --source camera:0 --zones zones.json --out data/recordings/run2

    It is kitchen.vision.run with --record (which builds the frames); `camera:<n>` is webcam n.
    """
    import argparse

    from kitchen.vision import run

    parser = argparse.ArgumentParser(description="Record vision frames, change masks and a manifest.")
    parser.add_argument("--source", required=True, help="a video file, or camera:<index> for a webcam")
    parser.add_argument("--out", required=True, type=Path, help="a new directory for the recording")
    parser.add_argument("--zones", type=Path, help="zones.json")
    args = parser.parse_args(argv)

    if args.source.startswith("camera:"):
        source = ["--webcam", args.source.removeprefix("camera:")]
    else:
        source = ["--video", args.source]
    zones = ["--zones", str(args.zones)] if args.zones else []
    return run.main([*source, *zones, "--record", str(args.out)])


if __name__ == "__main__":
    import logging
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
