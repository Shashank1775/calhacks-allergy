"""Short simulated episodes for ConvLSTM training clips: one tool or hand (A) and one container (B), plus bystanders.

Owner: sim (agents)
Step:  simulator
Reads: a seed and a layout family
Emits: Episode: where every shape is at each frame, the food in each container, tracker boxes, and the true labels
Rules: labels come from what happens in the episode, never from a model; plain shapes only, never realistic utensils
       (blind classifier); deterministic per seed

An episode covers STEPS + 1 frames (about one second), so the clip builder gets STEPS change masks. Each episode is one
kind: real transfers (scoop, deposit, wipe), contact without transfer (touch, hover), and the hard negatives that stop
the model from learning "boxes overlap means contact" (pass over, motion nearby, approach and leave).
"""

from dataclasses import dataclass, field

import numpy as np

GENERATOR_VERSION = "2.3.0"
STEPS = 16  # clip length T: about one second (AGENTS.md, Open questions: clip geometry)
FRAMES = STEPS + 1  # one extra frame, so the first step has a change mask too
STEP_MS = 1000 / STEPS
# A spoonful seen from overhead: a radius of about 3 to 6 px (6 to 12 px across) in a 128-px clip. Smaller portions
# sank into the camera noise in v1, so the model missed real scoops and deposits. Ordinary food spans the same sizes,
# so a big blob moving is never a giveaway on its own.
PORTION_RADIUS = (0.025, 0.045)
FOOD_RADIUS = (0.012, PORTION_RADIUS[1])
RIM_WIDTH = 0.012  # drawn inside each container's half_size


@dataclass(frozen=True)
class Kind:
    """One kind of episode, the truth it carries, and its share of a dataset."""

    contact: bool
    transferred: bool
    action: str
    direction: str | None
    hard_negative: str | None
    share: float


KINDS = {
    "scoop": Kind(True, True, "transfer.scoop", "b_to_a", None, 0.15),
    "deposit": Kind(True, True, "transfer.scoop", "a_to_b", None, 0.10),
    "wipe": Kind(True, True, "transfer.wipe", "both", None, 0.10),
    "touch": Kind(True, False, "transfer.contact", "both", "contact_no_motion", 0.15),
    "pass_over": Kind(False, False, "unimportant", None, "overlap_no_contact", 0.15),
    # A tool resting over the food in B: an overhead camera can't tell that from touching it, so it counts as
    # contact (the safe side). No food moves, so it isn't a transfer.
    "hover": Kind(True, False, "transfer.contact", "both", "contact_no_motion", 0.10),
    "motion_nearby": Kind(False, False, "unimportant", None, "motion_no_contact", 0.10),
    "approach_leave": Kind(False, False, "unimportant", None, None, 0.15),
}


@dataclass
class Container:
    """A bin, bowl or plate seen from above: a rim around a body, possibly nudged when something touches it."""

    center: np.ndarray  # [FRAMES, 2]
    half_size: np.ndarray  # (half width, half height), rim included
    is_round: bool
    body_color: np.ndarray
    rim_color: np.ndarray


@dataclass
class Food:
    """A blob of food: where it is each frame, and how smeared it is."""

    position: np.ndarray  # [FRAMES, 2]
    radius: float
    stretch: np.ndarray  # [FRAMES], 1 = round, larger = smeared along `angle`
    angle: np.ndarray  # [FRAMES]
    color: np.ndarray
    on_actor: np.ndarray = field(default_factory=lambda: np.zeros(FRAMES, dtype=bool))  # carried by A, drawn on top


@dataclass
class Actor:
    """A tool (a plain bar, maybe held by a hand) or a bare hand with its forearm."""

    is_tool: bool
    tip: np.ndarray  # [FRAMES, 2]: the end that touches things
    angle: np.ndarray  # [FRAMES]: direction from the tip back toward the handle or arm
    half_length: float  # tools: half the bar length; hands: palm half-length
    half_width: float
    color: np.ndarray
    holder_color: np.ndarray | None = None  # the hand holding a tool, if drawn


@dataclass
class Episode:
    """Everything needed to draw one clip and label it."""

    seed: int
    family: int
    kind: str
    times_ms: np.ndarray  # [FRAMES], strictly increasing
    containers: list[Container]  # containers[0] is B
    food: list[Food]
    actor: Actor  # A
    bystanders: list[Actor]  # other hands; never A or B
    flicker: np.ndarray  # [FRAMES] global brightness gain
    noise_sigma: float  # per-pixel camera noise, 0 to 1 scale
    box_a: list  # [FRAMES] tracker box for A, or None when missed
    box_b: list
    labels: dict = field(default_factory=dict)


def direction(angle: float) -> np.ndarray:
    """Unit vector for an angle in radians."""
    return np.array([np.cos(angle), np.sin(angle)])


def smoothstep(s: float) -> float:
    """Ease in and out, so movers slow down before they stop, as real hands do."""
    s = min(max(s, 0.0), 1.0)
    return s * s * (3 - 2 * s)


def keyframe_path(u: np.ndarray, keys: list[tuple[float, np.ndarray]]) -> np.ndarray:
    """Positions at normalized times u, easing between (time, point) keyframes."""
    path = np.empty((len(u), 2))
    for i, t in enumerate(u):
        path[i] = keys[-1][1]
        for (t0, p0), (t1, p1) in zip(keys, keys[1:], strict=False):
            if t <= t1:
                s = 1.0 if t1 == t0 else smoothstep((t - t0) / (t1 - t0))
                path[i] = p0 + (p1 - p0) * s
                break
    return path


def frame_times(rng: np.random.Generator) -> np.ndarray:
    """Frame timestamps with jitter and the occasional dropped frame, as from a real camera."""
    gaps = np.clip(rng.normal(STEP_MS, 6.0, FRAMES - 1), 30, None)
    dropped = rng.random(FRAMES - 1) < 0.05
    gaps = np.where(dropped, gaps * 2, gaps)
    start = int(rng.integers(0, 600_000))
    return start + np.concatenate([[0], np.cumsum(np.round(gaps))]).astype(np.int64)


def random_color(rng: np.random.Generator, low: float = 0.1, high: float = 0.95) -> np.ndarray:
    """An RGB color in [low, high]."""
    return rng.uniform(low, high, 3)


def distinct_color(rng: np.random.Generator, avoid: list[np.ndarray], min_gap: float = 0.35) -> np.ndarray:
    """A color at least `min_gap` (summed over channels) from every color in `avoid`."""
    for _ in range(50):
        color = random_color(rng)
        if all(np.abs(color - other).sum() >= min_gap for other in avoid):
            return color
    return color


def edge_distance(container: Container, angle: float) -> float:
    """Distance from a container's center to its rim along an angle."""
    half_w, half_h = container.half_size
    c, s = abs(np.cos(angle)), abs(np.sin(angle))
    if container.is_round:
        return 1.0 / np.sqrt((c / half_w) ** 2 + (s / half_h) ** 2)
    return min(half_w / max(c, 1e-6), half_h / max(s, 1e-6))


def make_container(rng: np.random.Generator, center: np.ndarray) -> Container:
    """A container at a fixed center with a random size, shape and colors."""
    half_size = rng.uniform([0.07, 0.07], [0.16, 0.16])
    body = random_color(rng)
    rim = np.clip(body + rng.choice([-1, 1]) * rng.uniform(0.15, 0.35), 0, 1)
    return Container(np.tile(center, (FRAMES, 1)), half_size, bool(rng.random() < 0.6), body, rim)


def place_bystander_containers(rng: np.random.Generator, b: Container, count: int) -> list[Container]:
    """Other containers on the counter that never touch B."""
    placed: list[Container] = []
    for _ in range(count * 20):
        if len(placed) == count:
            break
        candidate = make_container(rng, rng.uniform(0.1, 0.9, 2))
        clear = True
        for other in [b, *placed]:
            gap = np.abs(candidate.center[0] - other.center[0]) - (candidate.half_size + other.half_size)
            if (gap < 0.03).all():
                clear = False
        if clear:
            placed.append(candidate)
    return placed


def fill_with_food(rng: np.random.Generator, container: Container) -> list[Food]:
    """A few food blobs inside a container, in colors that stand out from it."""
    blobs = []
    for _ in range(int(rng.integers(3, 9))):
        offset = rng.uniform(-0.7, 0.7, 2) * container.half_size
        color = distinct_color(rng, [container.body_color])
        radius = float(rng.uniform(*FOOD_RADIUS))
        blobs.append(
            Food(
                position=np.tile(pull_inside(container, container.center[0] + offset, radius, 0), (FRAMES, 1)),
                radius=radius,
                stretch=np.ones(FRAMES),
                angle=np.full(FRAMES, rng.uniform(0, np.pi)),
                color=color,
            )
        )
    return blobs


def make_actor(rng: np.random.Generator, tip: np.ndarray, angle: np.ndarray, is_tool: bool) -> Actor:
    """A tool or a bare hand following a path."""
    if is_tool:
        holder = random_color(rng, 0.3, 0.9) if rng.random() < 0.6 else None
        half_length, half_width = rng.uniform(0.07, 0.14), rng.uniform(0.008, 0.02)
        return Actor(True, tip, angle, half_length, half_width, random_color(rng), holder)
    half_length, half_width = rng.uniform(0.035, 0.06), rng.uniform(0.025, 0.045)
    return Actor(False, tip, angle, half_length, half_width, random_color(rng, 0.3, 0.9))


def footprint(actor_is_tool: bool) -> float:
    """How far past its tip point A can reach (a hand's palm, or food loaded on a tool), so near misses keep a real gap."""
    return PORTION_RADIUS[1] if actor_is_tool else 0.06


def dwell_wiggle(rng: np.random.Generator, u: np.ndarray, start: float, end: float, amplitude: float, axis: float):
    """A small back-and-forth along an axis while A dwells, zero outside the dwell."""
    cycles = rng.uniform(1.0, 2.5)
    phase = np.clip((u - start) / max(end - start, 1e-6), 0, 1)
    inside = (u > start) & (u < end)
    return np.outer(np.where(inside, np.sin(2 * np.pi * cycles * phase) * amplitude, 0.0), direction(axis))


def plan_path(rng: np.random.Generator, kind: str, b: Container, food: list[Food], u: np.ndarray, is_tool: bool):
    """Where A's tip goes and when it arrives and leaves, for one kind of episode."""
    center = b.center[0]
    approach = rng.uniform(0, 2 * np.pi)  # A comes in from this side of B
    arrive = rng.uniform(0.25, 0.5)
    leave = arrive + rng.uniform(0.15, 0.3)
    reach = rng.uniform(0.12, 0.35)

    if kind == "pass_over":
        # A crosses over B at a steady pace, never stopping: the boxes overlap, but nothing touches.
        offset = rng.uniform(-0.4, 0.4, 2) * b.half_size
        start = center + offset + direction(approach) * reach * 1.5
        end = center + offset - direction(approach) * reach * 1.5
        path = start + np.outer(u, end - start)
        return path, approach, None

    # Half of all touches land inside B, so stopping inside B doesn't by itself mean food moved.
    inside_touch = kind == "touch" and rng.random() < 0.5
    if kind in ("scoop", "deposit", "hover") or inside_touch:
        target = center + rng.uniform(-0.5, 0.5, 2) * b.half_size
    elif kind == "wipe":
        # A wipe goes through food, so something smears; kept in from the rim so its wide wiggle stays over B.
        target = pull_inside(b, food[int(rng.integers(len(food)))].position[0], 0.06, 0)
    elif kind == "touch":
        target = center + direction(approach) * (edge_distance(b, approach) - RIM_WIDTH)  # the tip reaches the rim
    else:  # approach_leave, motion_nearby: stop short of B with a real gap
        low, high = (0.02, 0.06) if kind == "motion_nearby" else (0.05, 0.15)
        gap = edge_distance(b, approach) + footprint(is_tool) + rng.uniform(low, high)
        target = center + direction(approach) * gap

    start = target + direction(approach) * reach
    end = target + direction(approach + rng.uniform(-1.0, 1.0)) * rng.uniform(0.12, 0.35)
    path = keyframe_path(u, [(0.0, start), (arrive, target), (leave, target), (1.0, end)])

    # Every still dwell shares one wiggle band, so how steady A is says nothing about the label.
    amplitude_by_kind = {"wipe": (0.025, 0.06)}  # a scoop shows by the spoonful and the hole, not by jiggling
    low, high = amplitude_by_kind.get(kind, (0.0, 0.008))
    axis = rng.uniform(0, np.pi)
    path = path + dwell_wiggle(rng, u, arrive, leave, rng.uniform(low, high), axis)
    return path, approach, (arrive, leave)


def drag_food(
    container: Container, food: list[Food], movers: np.ndarray, frames: range, radius: float, strength: float
) -> None:
    """Food near a moving tip gets pushed along and smeared, frame by frame (a real transfer the camera can see).

    Pushed food stays inside its container, so it never ends up on the counter under someone else's tool.
    """
    for f in frames:
        step = movers[f] - movers[f - 1]
        for blob in food:
            blob.position[f:] = blob.position[f - 1]
            if np.linalg.norm(blob.position[f] - movers[f]) < radius + blob.radius:
                # Smear it, but never longer than the container is wide, and keep the whole smear inside the rim.
                longest = max((container.half_size.min() - RIM_WIDTH) / blob.radius, 1.0)
                blob.stretch[f:] = min(blob.stretch[f - 1] + np.linalg.norm(step) * 25, 3.0, longest)
                shifted = blob.position[f] + step * strength
                blob.position[f:] = pull_inside(container, shifted, blob.radius * blob.stretch[f], f)
                blob.angle[f:] = np.arctan2(step[1], step[0])


def nudge(b: Container, food: list[Food], frame: int, shift: np.ndarray) -> None:
    """Shift B and everything in it from `frame` on: the container moved because something touched it."""
    b.center[frame:] += shift
    for blob in food:
        blob.position[frame:] += shift


def pull_inside(container: Container, point: np.ndarray, margin: float, f: int) -> np.ndarray:
    """The point, moved toward the container's center if needed so a blob of radius `margin` sits inside its body."""
    offset = point - container.center[f]
    room = np.maximum(container.half_size - RIM_WIDTH - margin, 1e-3)
    if container.is_round:
        reach = np.sqrt(((offset / room) ** 2).sum())
    else:
        reach = np.abs(offset / room).max()
    return container.center[f] + offset / max(reach, 1.0)


def nearest_color(food: list[Food], point: np.ndarray) -> np.ndarray:
    """The color of the food blob nearest a point (what a scoop there picks up)."""
    return min(food, key=lambda blob: np.linalg.norm(blob.position[0] - point)).color


def stirring_hand(rng: np.random.Generator, b: Container, u: np.ndarray, avoid_angle: float) -> Actor:
    """Someone else's hand reaching into B from another side and stirring (motion in B that A didn't cause)."""
    side = avoid_angle + np.pi + rng.uniform(-0.8, 0.8)
    center = b.center[0]
    target = center + rng.uniform(-0.3, 0.3, 2) * b.half_size
    start = center + direction(side) * rng.uniform(0.25, 0.4)
    arrive = rng.uniform(0.0, 0.3)
    path = keyframe_path(u, [(0.0, start), (arrive, target), (1.0, target)])
    path = path + dwell_wiggle(rng, u, arrive, 1.0, rng.uniform(0.02, 0.05), rng.uniform(0, np.pi))
    return make_actor(rng, path, np.full(FRAMES, side), is_tool=False)


def wandering_hand(rng: np.random.Generator, b: Container) -> Actor | None:
    """An unrelated hand moving somewhere else on the counter, or None if there's no room far from B."""
    for _ in range(20):
        start, end = rng.uniform(0.05, 0.95, 2), rng.uniform(0.05, 0.95, 2)
        u = np.linspace(0, 1, 8)
        line = start + np.outer(u, end - start)
        if (np.linalg.norm(line - b.center[0], axis=1) > 0.35).all():
            path = start + np.outer(np.linspace(0, 1, FRAMES), end - start)
            angle = np.arctan2(*(start - end)[::-1])
            return make_actor(rng, path, np.full(FRAMES, angle), is_tool=False)
    return None


def tracker_boxes(rng: np.random.Generator, true_boxes: list[np.ndarray]) -> list:
    """Tracker output: true boxes with jitter and occasional misses (None), like a real detector."""
    jitter = rng.uniform(0.02, 0.08)
    miss_rate = rng.uniform(0.0, 0.05)
    boxes = []
    for box in true_boxes:
        size = box[2:] - box[:2]
        noisy = box + rng.normal(0, jitter, 4) * np.concatenate([size, size])
        boxes.append(None if rng.random() < miss_rate else tuple(float(v) for v in np.clip(noisy, 0, 1)))
    if sum(box is None for box in boxes) > STEPS // 2:  # a pair that's never seen wouldn't be a candidate
        boxes = [tuple(float(v) for v in np.clip(box, 0, 1)) for box in true_boxes]
    return boxes


def actor_extent(actor: Actor, f: int) -> np.ndarray:
    """True [x1, y1, x2, y2] of A: the bar for a tool, the palm for a hand (never the holder or the arm)."""
    tip, axis = actor.tip[f], direction(actor.angle[f])
    if actor.is_tool:
        handle = tip + axis * 2 * actor.half_length
        side = np.array([-axis[1], axis[0]]) * actor.half_width
        corners = np.array([tip + side, tip - side, handle + side, handle - side])
        return np.concatenate([corners.min(axis=0), corners.max(axis=0)])
    c, s = abs(axis[0]), abs(axis[1])
    a, b = actor.half_length, actor.half_width
    half = np.array([np.hypot(a * c, b * s), np.hypot(a * s, b * c)])
    center = tip + axis * a
    return np.concatenate([center - half, center + half])


def container_extent(container: Container, f: int) -> np.ndarray:
    """True [x1, y1, x2, y2] of a container."""
    return np.concatenate([container.center[f] - container.half_size, container.center[f] + container.half_size])


def lighting(rng: np.random.Generator) -> tuple[np.ndarray, float, str | None]:
    """Per-frame brightness gain and camera noise; a strong dose of either can be a hard negative of its own."""
    strong_flicker = rng.random() < 0.15
    strong_noise = rng.random() < 0.10
    amplitude = rng.uniform(0.03, 0.08) if strong_flicker else rng.uniform(0.0, 0.015)
    flicker = 1 + amplitude * np.clip(rng.normal(0, 1, FRAMES), -2.5, 2.5)
    noise = (rng.uniform(5, 10) if strong_noise else rng.uniform(0.5, 3)) / 255
    tag = "lighting_flicker" if strong_flicker else "camera_noise" if strong_noise else None
    return flicker, noise, tag


def simulate_episode(seed: int, family: int, kind: str | None = None) -> Episode:
    """Simulate one episode. The kind is drawn by its dataset share unless given."""
    rng = np.random.default_rng(seed)
    if kind is None:
        names = list(KINDS)
        kind = str(rng.choice(names, p=[KINDS[name].share for name in names]))
    truth = KINDS[kind]

    times = frame_times(rng)
    u = (times - times[0]) / (times[-1] - times[0])

    b = make_container(rng, rng.uniform(0.3, 0.7, 2))
    bystander_containers = place_bystander_containers(rng, b, int(rng.integers(0, 4)))
    b_food = fill_with_food(rng, b)
    other_food = [blob for container in bystander_containers for blob in fill_with_food(rng, container)]

    is_tool = bool(rng.random() < 0.7)
    tip, approach, dwell = plan_path(rng, kind, b, b_food, u, is_tool)
    angle = approach + rng.normal(0, 0.15) + np.linspace(0, rng.normal(0, 0.3), FRAMES)
    actor = make_actor(rng, tip, angle, is_tool)

    bystanders = []
    contact_frame = None
    if dwell is not None:
        arrive_frame = int(np.searchsorted(u, dwell[0]))
        leave_frame = min(int(np.searchsorted(u, dwell[1])), FRAMES - 1)
        contact_frame = min(arrive_frame, FRAMES - 1)

        if kind == "scoop":
            # A digs into the food under its tip, stirring what's around it, then lifts a spoonful out. The spoonful
            # rides away on A and leaves a hole where it was: the cue that separates a scoop from a hover.
            radius = float(rng.uniform(*PORTION_RADIUS))
            portion = Food(
                position=np.tile(pull_inside(b, tip[arrive_frame], radius, arrive_frame), (FRAMES, 1)),
                radius=radius,
                stretch=np.ones(FRAMES),
                angle=np.zeros(FRAMES),
                color=nearest_color(b_food, tip[arrive_frame]),
            )
            drag_food(b, b_food, tip, range(arrive_frame + 1, leave_frame + 1), 0.05, 0.6)
            portion.position[leave_frame:] = tip[leave_frame:]
            portion.on_actor[leave_frame:] = True
            b_food.append(portion)
        elif kind == "deposit":
            # A arrives with a spoonful of something else (sauce onto rice) and drops it just past its tip, so the
            # camera sees it land rather than it appearing from under the tool.
            drop = (arrive_frame + leave_frame) // 2
            radius = float(rng.uniform(*PORTION_RADIUS))
            landing = tip[drop] - direction(angle[drop]) * (radius + 0.01)  # just past the tip, away from the handle
            # Most deposits are something else (sauce onto rice); some are more of the same, which is harder to see.
            if rng.random() < 0.3:
                color = nearest_color(b_food, landing)
            else:
                color = distinct_color(rng, [b.body_color, *[blob.color for blob in b_food]])
            carried = Food(tip.copy(), radius, np.ones(FRAMES), np.zeros(FRAMES), color)
            # The smeared blob lands inside B, never on the counter.
            carried.position[drop:] = pull_inside(b, landing, radius * 1.4, drop)
            carried.stretch[drop:] = 1.4
            carried.on_actor[:drop] = True
            b_food.append(carried)
        elif kind == "wipe":
            drag_food(b, b_food, tip, range(arrive_frame + 1, FRAMES), 0.035, rng.uniform(0.3, 0.6))
        elif kind == "touch" and rng.random() < 0.7:
            # Touching nudges the container a little, one frame after A arrives so A is seen reaching it first;
            # nothing inside moves relative to the container.
            nudge(b, b_food, min(contact_frame + 1, FRAMES - 1), -direction(approach) * rng.uniform(0.006, 0.02))

    carried_load = []
    if not truth.transferred and rng.random() < 0.3:
        # A loaded tool or hand that never puts its food down: food on A says nothing about a transfer by itself.
        load = Food(
            tip.copy(), float(rng.uniform(*PORTION_RADIUS)), np.ones(FRAMES), np.zeros(FRAMES), random_color(rng)
        )
        load.on_actor[:] = True
        carried_load.append(load)  # kept apart from B's food, so nothing below drags it off A

    if kind == "motion_nearby":
        stirrer = stirring_hand(rng, b, u, approach)
        drag_food(b, b_food, stirrer.tip, range(1, FRAMES), 0.04, rng.uniform(0.3, 0.6))
        bystanders.append(stirrer)
    if rng.random() < 0.25:
        wanderer = wandering_hand(rng, b)
        if wanderer is not None:
            bystanders.append(wanderer)

    flicker, noise_sigma, lighting_tag = lighting(rng)
    labels = {"contact": truth.contact, "transferred": truth.transferred, "action": truth.action}
    if truth.direction:
        labels["direction"] = truth.direction
    if truth.contact and contact_frame is not None:
        labels["contact_t_ms"] = int(times[contact_frame])
    labels["label_source"] = "sim_truth"
    tag = truth.hard_negative or (lighting_tag if not truth.contact else None)
    if tag:
        labels["hard_negative"] = tag

    return Episode(
        seed=seed,
        family=family,
        kind=kind,
        times_ms=times,
        containers=[b, *bystander_containers],
        food=b_food + other_food + carried_load,
        actor=actor,
        bystanders=bystanders,
        flicker=flicker,
        noise_sigma=noise_sigma,
        box_a=tracker_boxes(rng, [actor_extent(actor, f) for f in range(FRAMES)]),
        box_b=tracker_boxes(rng, [container_extent(b, f) for f in range(FRAMES)]),
        labels=labels,
    )
