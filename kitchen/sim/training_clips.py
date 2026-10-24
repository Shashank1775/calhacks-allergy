"""ConvLSTM training clips: simulated episodes drawn as abstract frames, run through the shared change mask and clip
builder, labeled from the episode's truth (with hard negatives), and split into leak-free train/val/test sets.

Owner: sim (agents)
Step:  simulator
Reads: episodes (episodes.py); kitchen/shared/change_mask.py and clips.py
Emits: data/clips/<version>/{train,val,test}/ with clips.jsonl (interaction_clip records), one npz per clip,
       plus manifest.json and datasheet.md
Rules: blind classifier: plain shapes and food blobs only, never realistic utensils; labels from the episode, never a model;
       no seed or kitchen layout in two splits; deterministic per seed

Generate a dataset:
    uv run python -m kitchen.sim.training_clips --out data/clips/v2 --train 40000 --val 4000 --test 4000 --workers 32
"""

import argparse
import hashlib
import json
import os
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from kitchen.shared.change_mask import DEFAULT_BLUR_SIGMA_PX, DEFAULT_DECAY, DEFAULT_GAIN, change_mask
from kitchen.shared.clips import BUILDER_VERSION, ClipStep, build_clip
from kitchen.sim.episodes import (
    FRAMES,
    GENERATOR_VERSION,
    KINDS,
    RIM_WIDTH,
    STEPS,
    Actor,
    Episode,
    direction,
    simulate_episode,
)

SIZE = 128  # clip H and W (AGENTS.md, Open questions: clip geometry)
SUPERSAMPLE = 2  # draw at 2x and average down, so moving edges give soft, camera-like change
CHANGE_MASK_PARAMS = {"gain": DEFAULT_GAIN, "blur_sigma_px": DEFAULT_BLUR_SIGMA_PX, "decay": DEFAULT_DECAY}

# Layout families are background styles. Test gets families train and val never see, so it measures generalization.
TRAIN_FAMILIES = (0, 1, 2, 3)
TEST_FAMILIES = (4, 5)
SPLIT_SEED_OFFSET = {"train": 0, "val": 1_000_000, "test": 2_000_000}


def pixel_grid(size: int) -> tuple[np.ndarray, np.ndarray]:
    """Normalized x and y coordinates of every pixel center."""
    coords = (np.arange(size) + 0.5) / size
    return np.meshgrid(coords, coords)


def background(family: int, rng: np.random.Generator, size: int) -> np.ndarray:
    """A counter surface in one of six styles, as float RGB [size, size, 3] in [0, 1]."""
    xs, ys = pixel_grid(size)
    low, high = rng.uniform(0.15, 0.85, 3), rng.uniform(0.15, 0.85, 3)

    if family == 0:  # blotchy noise
        coarse = rng.random((8, 8))
        weight = np.kron(coarse, np.ones((size // 8, size // 8)))
    elif family == 1:  # stripes
        angle, frequency = rng.uniform(0, np.pi), rng.uniform(8, 30)
        weight = 0.5 + 0.5 * np.sin((xs * np.cos(angle) + ys * np.sin(angle)) * frequency)
    elif family == 2:  # tiles with grout lines
        tiles, line = rng.integers(3, 9), rng.uniform(0.04, 0.1)
        weight = (((xs * tiles) % 1 < line) | ((ys * tiles) % 1 < line)).astype(float)
    elif family == 3:  # wood grain
        warp = np.kron(rng.random((4, 4)), np.ones((size // 4, size // 4)))
        weight = 0.5 + 0.5 * np.sin((xs + 0.15 * warp) * rng.uniform(20, 50))
    elif family == 4:  # speckled stone
        weight = (rng.random((size, size)) < rng.uniform(0.05, 0.25)).astype(float)
    else:  # plain gradient
        angle = rng.uniform(0, 2 * np.pi)
        weight = np.clip(0.5 + (xs - 0.5) * np.cos(angle) + (ys - 0.5) * np.sin(angle), 0, 1)

    surface = low + weight[..., None] * (high - low)
    vignette = 1 - rng.uniform(0.0, 0.35) * ((xs - 0.5) ** 2 + (ys - 0.5) ** 2)[..., None] * 2
    return np.clip(surface * vignette, 0, 1)


def paint(canvas: np.ndarray, inside: np.ndarray, color: np.ndarray) -> None:
    """Fill the pixels where `inside` is true with a color."""
    canvas[inside] = color


def ellipse(xs, ys, center, half_a, half_b, angle) -> np.ndarray:
    """Pixels inside a rotated ellipse."""
    dx, dy = xs - center[0], ys - center[1]
    c, s = np.cos(angle), np.sin(angle)
    u, v = dx * c + dy * s, -dx * s + dy * c
    return (u / half_a) ** 2 + (v / half_b) ** 2 <= 1


def bar(xs, ys, start, end, half_width) -> np.ndarray:
    """Pixels within half_width of the segment from start to end (a bar with rounded ends)."""
    segment = end - start
    length_sq = max(float(segment @ segment), 1e-9)
    t = np.clip(((xs - start[0]) * segment[0] + (ys - start[1]) * segment[1]) / length_sq, 0, 1)
    px, py = start[0] + t * segment[0], start[1] + t * segment[1]
    return (xs - px) ** 2 + (ys - py) ** 2 <= half_width**2


def draw_actor(canvas, xs, ys, actor: Actor, f: int) -> None:
    """Draw a tool (a plain bar, maybe held by a hand) or a hand with its forearm."""
    tip, axis = actor.tip[f], direction(actor.angle[f])
    if actor.is_tool:
        handle = tip + axis * 2 * actor.half_length
        paint(canvas, bar(xs, ys, tip, handle, actor.half_width), actor.color)
        if actor.holder_color is not None:
            palm = handle + axis * 0.02
            paint(canvas, bar(xs, ys, palm, palm + axis * 1.0, 0.022), actor.holder_color * 0.9)
            paint(canvas, ellipse(xs, ys, palm, 0.035, 0.028, actor.angle[f]), actor.holder_color)
        return
    palm = tip + axis * actor.half_length
    paint(canvas, bar(xs, ys, palm, palm + axis * 1.0, actor.half_width * 0.7), actor.color * 0.9)
    paint(canvas, ellipse(xs, ys, palm, actor.half_length, actor.half_width, actor.angle[f]), actor.color)


def render_frame(episode: Episode, f: int, surface: np.ndarray, xs, ys, noise_rng: np.random.Generator) -> np.ndarray:
    """Draw frame f as uint8 RGB [SIZE, SIZE, 3]: surface, containers, food, then hands and tools on top."""
    canvas = surface.copy()

    for container in episode.containers:
        center, (half_w, half_h) = container.center[f], container.half_size
        rim = RIM_WIDTH
        if container.is_round:
            paint(canvas, ellipse(xs, ys, center, half_w, half_h, 0.0), container.rim_color)
            paint(canvas, ellipse(xs, ys, center, half_w - rim, half_h - rim, 0.0), container.body_color)
        else:
            outer = (np.abs(xs - center[0]) <= half_w) & (np.abs(ys - center[1]) <= half_h)
            inner = (np.abs(xs - center[0]) <= half_w - rim) & (np.abs(ys - center[1]) <= half_h - rim)
            paint(canvas, outer, container.rim_color)
            paint(canvas, inner, container.body_color)

    def draw_food(carried: bool) -> None:
        for blob in episode.food:
            if blob.on_actor[f] == carried:
                long_axis = blob.radius * blob.stretch[f]
                short_axis = blob.radius / np.sqrt(blob.stretch[f])
                paint(canvas, ellipse(xs, ys, blob.position[f], long_axis, short_axis, blob.angle[f]), blob.color)

    draw_food(carried=False)
    for bystander in episode.bystanders:
        draw_actor(canvas, xs, ys, bystander, f)
    draw_actor(canvas, xs, ys, episode.actor, f)
    draw_food(carried=True)

    # Average SUPERSAMPLE x SUPERSAMPLE blocks down to SIZE x SIZE, then apply lighting and camera noise.
    small = canvas.reshape(SIZE, SUPERSAMPLE, SIZE, SUPERSAMPLE, 3).mean(axis=(1, 3))
    lit = small * episode.flicker[f] + noise_rng.normal(0, episode.noise_sigma, small.shape)
    return np.round(np.clip(lit, 0, 1) * 255).astype(np.uint8)


def entity_ids(episode: Episode) -> tuple[str, str]:
    """Plain IDs for A and B; they say what kind of entity it is, never what object."""
    a_id = "tool-sim-a" if episode.actor.is_tool else "hand-p1-right"
    return a_id, "bin-sim-b"


def make_clip(seed: int, family: int, tensor_file: str, kind: str | None = None) -> tuple[dict, np.ndarray, Episode]:
    """Simulate, draw and build one clip: its interaction_clip record, uint8 [T, C, H, W] tensor, and the episode."""
    episode = simulate_episode(seed, family, kind)
    draw_rng = np.random.default_rng([seed, 1])
    surface = np.kron(background(family, draw_rng, SIZE), np.ones((SUPERSAMPLE, SUPERSAMPLE, 1)))
    xs, ys = pixel_grid(SIZE * SUPERSAMPLE)
    frames = [render_frame(episode, f, surface, xs, ys, draw_rng) for f in range(FRAMES)]

    steps = []
    mask = None
    for f in range(1, FRAMES):
        mask = change_mask(frames[f - 1], frames[f], mask, **CHANGE_MASK_PARAMS)
        steps.append(ClipStep(int(episode.times_ms[f]), episode.box_a[f], episode.box_b[f], mask))

    a_id, b_id = entity_ids(episode)
    record, tensor = build_clip(
        a_id,
        b_id,
        steps,
        clip_id=f"sim-{seed}",
        source="sim",
        tensor_file=tensor_file,
        change_mask_params=CHANGE_MASK_PARAMS,
        H=SIZE,
        W=SIZE,
        generator={"version": GENERATOR_VERSION, "seed": seed},
        labels=episode.labels,
    )
    return record, tensor, episode


def write_one(job: tuple[str, str, int, int]) -> tuple[int, dict, str]:
    """Build one clip and save its npz; return its index, record and episode kind (runs in a worker process)."""
    out_dir, split, index, seed_base = job
    seed = seed_base + SPLIT_SEED_OFFSET[split] + index
    families = TEST_FAMILIES if split == "test" else TRAIN_FAMILIES
    family = int(np.random.default_rng([seed, 2]).choice(families))
    relative = f"{split}/clips/{index:06d}.npz"
    record, tensor, episode = make_clip(seed, family, relative)
    record["generator"]["scenario_id"] = f"family-{family}-{episode.kind}"
    np.savez_compressed(Path(out_dir) / relative, x=tensor, dt_ms=np.array(record["dt_ms"], dtype=np.int32))
    return index, record, episode.kind


def write_split(out_dir: Path, split: str, count: int, seed_base: int, workers: int) -> dict:
    """Write one split's clips and clips.jsonl; return its counts for the manifest."""
    (out_dir / split / "clips").mkdir(parents=True, exist_ok=True)
    jobs = [(str(out_dir), split, index, seed_base) for index in range(count)]
    records: list[dict | None] = [None] * count
    kinds: Counter = Counter()
    with Pool(workers) as pool:
        for index, record, kind in pool.imap_unordered(write_one, jobs, chunksize=16):
            records[index] = record
            kinds[kind] += 1

    lines = "".join(json.dumps(record) + "\n" for record in records)
    (out_dir / split / "clips.jsonl").write_text(lines)
    labels = [record["labels"] for record in records]
    return {
        "clips": count,
        "seeds": [seed_base + SPLIT_SEED_OFFSET[split], seed_base + SPLIT_SEED_OFFSET[split] + count - 1],
        "families": list(TEST_FAMILIES if split == "test" else TRAIN_FAMILIES),
        "kinds": dict(sorted(kinds.items())),
        "contact_rate": round(sum(label["contact"] for label in labels) / count, 4),
        "transferred_rate": round(sum(label["transferred"] for label in labels) / count, 4),
        "clips_jsonl_sha256": hashlib.sha256(lines.encode()).hexdigest(),
    }


DATASHEET = """# Sim training clips for the ConvLSTM

Generated by `kitchen/sim/training_clips.py` (generator {generator}, clip builder {builder}).

- **What a clip is:** one candidate pair over {steps} steps (about one second), as a uint8 tensor `[T, C, H, W]` =
  `[{steps}, 3, {size}, {size}]` with channels `box_a`, `box_b`, `change`, plus `dt_ms` per step. Key `x` and `dt_ms` in
  each npz; the matching `interaction_clip` record (with labels) is the same line number in `clips.jsonl`.
- **Labels** come from the simulated episode: `contact` (the two touched) and `transferred` (material moved between
  them), plus `action`, `direction`, `contact_t_ms` and `hard_negative`.
- **Kinds and shares:** {shares}.
- **Contact without transfer** (tagged `contact_no_motion`): touching the rim or the food without moving any, and
  hovering over the food (an overhead camera can't tell that from touching, so it counts as contact).
- **Hard negatives:** passing over a container without touching, someone else stirring nearby, approaching and
  leaving, strong lighting flicker and camera noise. Every clip also has box jitter, missed detections,
  frame-timing jitter and dropped frames.
- **Splits:** no seed is in two splits. Train and val share background families {train_families}; test uses
  families {test_families}, which train and val never see (it differs only in background texture).
- **Known gaps:** shapes are abstract (that's the point), but real kitchens have steam, shadows, reflections and
  crowded scenes. Judge the model on real labeled clips before adopting it.

Counts per split are in `manifest.json`.
"""


def main() -> None:
    """Command line: write a full dataset with manifest and datasheet."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--train", type=int, default=20000)
    parser.add_argument("--val", type=int, default=2000)
    parser.add_argument("--test", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0, help="seed base; datasets with different bases don't overlap")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args()

    seed_base = args.seed * 10_000_000
    splits = {}
    for split, count in (("train", args.train), ("val", args.val), ("test", args.test)):
        splits[split] = write_split(args.out, split, count, seed_base, args.workers)
        print(f"{split}: {count} clips, kinds {splits[split]['kinds']}", flush=True)

    manifest = {
        "generator_version": GENERATOR_VERSION,
        "builder_version": BUILDER_VERSION,
        "shape": {"T": STEPS, "C": 3, "H": SIZE, "W": SIZE},
        "channels": ["box_a", "box_b", "change"],
        "change_mask": CHANGE_MASK_PARAMS,
        "kind_shares": {name: kind.share for name, kind in KINDS.items()},
        "splits": splits,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shares = ", ".join(f"{name} {kind.share:.0%}" for name, kind in KINDS.items())
    datasheet = DATASHEET.format(
        generator=GENERATOR_VERSION,
        builder=BUILDER_VERSION,
        steps=STEPS,
        size=SIZE,
        shares=shares,
        train_families=TRAIN_FAMILIES,
        test_families=TEST_FAMILIES,
    )
    (args.out / "datasheet.md").write_text(datasheet)


if __name__ == "__main__":
    main()
