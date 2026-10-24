"""Hand-written kitchen scenes for the interactions tests, and the events each one should produce.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (tests)
Reads: nothing
Emits: SCENES (name -> tracks, length, expected events), render() to ObservationFrames at any FPS, ZONES
Rules: frames validate against observation_frame (every detection carries origin); expected events are worked out by
       hand from the keyframes below, so they don't depend on the code they test

A scene is a few entities, each moving in straight lines between (t_ms, center) keyframes. render() samples it at any
frame rate, which is how the tests check that the same scene gives the same events at 15, 30 and 60 FPS. Zones are
rectangles, and a detection's zone is the one its box center is in, as vision assigns them. A track can also go
unseen for a while (a hand over it), keeping its ID, as vision's tracker does through a short loss.
"""

from dataclasses import dataclass

from kitchen.shared.models import Zone, validate


def rectangle(x1: float, y1: float, x2: float, y2: float) -> list[tuple[float, float]]:
    """A rectangular zone polygon."""
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


ZONES = [
    Zone("slot-1", "bin_slot", rectangle(0.05, 0.05, 0.25, 0.25)),
    Zone("plating", "plating_spot", rectangle(0.4, 0.4, 0.6, 0.6)),
    Zone("wash", "wash", rectangle(0.05, 0.7, 0.3, 0.95)),
    Zone("clean-stack", "clean_stack", rectangle(0.75, 0.4, 0.95, 0.6)),
    Zone("pass", "pass", rectangle(0.75, 0.75, 0.95, 0.95)),
]

# Box sizes (width, height) in normalized frame coordinates.
SIZES = {"bin": (0.14, 0.14), "plate": (0.16, 0.16), "tool": (0.03, 0.08), "hand": (0.06, 0.06)}
CLASS_NAMES = {"bin": "bowl", "plate": "bowl", "tool": "knife", "hand": "wrist"}

BIN_AT = (0.15, 0.15)  # inside slot-1
PLATE_AT = (0.5, 0.5)  # inside the plating spot
WASH_AT = (0.17, 0.82)  # inside the wash zone
PASS_AT = (0.85, 0.85)  # inside the pass


@dataclass(frozen=True)
class Track:
    """One entity moving in straight lines between (t_ms, (x, y)) center keyframes, seen from first to last key,
    except during the (start, end) spans in `unseen`, when the detector misses it."""

    entity_id: str
    kind: str
    keys: tuple[tuple[int, tuple[float, float]], ...]
    unseen: tuple[tuple[int, int], ...] = ()

    def center_at(self, t_ms: int) -> tuple[float, float] | None:
        """The center at t_ms, or None before the first or after the last keyframe, or while unseen."""
        if t_ms < self.keys[0][0] or t_ms > self.keys[-1][0]:
            return None
        if any(start <= t_ms < end for start, end in self.unseen):
            return None
        for (t0, (x0, y0)), (t1, (x1, y1)) in zip(self.keys, self.keys[1:], strict=False):
            if t0 <= t_ms <= t1:
                s = 0.0 if t1 == t0 else (t_ms - t0) / (t1 - t0)
                return x0 + s * (x1 - x0), y0 + s * (y1 - y0)
        return self.keys[-1][1]


def still(entity_id: str, kind: str, center: tuple[float, float], length_ms: int) -> Track:
    """An entity that sits in one place for the whole scene."""
    return Track(entity_id, kind, ((0, center), (length_ms, center)))


def zone_at(x: float, y: float) -> str | None:
    """The zone a point is in (zones here don't overlap), or None."""
    for zone in ZONES:
        (x1, y1), _, (x2, y2), _ = zone.polygon
        if x1 <= x <= x2 and y1 <= y <= y2:
            return zone.zone_id
    return None


def detection(track_id: int, track: Track, center: tuple[float, float]) -> dict:
    """One detection of a track at a center, as vision would emit it."""
    width, height = SIZES[track.kind]
    x, y = center
    box = [max(0.0, x - width / 2), max(0.0, y - height / 2), min(1.0, x + width / 2), min(1.0, y + height / 2)]
    found = {
        "track_id": track_id,
        "entity_id": track.entity_id,
        "kind": track.kind,
        "class_name": CLASS_NAMES[track.kind],
        "box": box,
        "confidence": 0.9,
        "zone": zone_at(x, y),
        "origin": "shift_start",
    }
    if track.kind == "hand":
        found["person_id"] = "p1"
        found["wrist"] = [x, y, 0.9]
    return found


def render(tracks: list[Track], length_ms: int, fps: float = 30.0) -> list[dict]:
    """Sample the scene at `fps` into validated ObservationFrames, from t_ms 0 to length_ms."""
    frames = []
    frame_id = 0
    while round(frame_id * 1000 / fps) <= length_ms:
        t_ms = round(frame_id * 1000 / fps)
        detections = [
            detection(track_id, track, center)
            for track_id, track in enumerate(tracks)
            if (center := track.center_at(t_ms)) is not None
        ]
        frame = {"frame_id": frame_id, "t_ms": t_ms, "camera_id": "cam0", "fps_estimate": fps, "detections": detections}
        frames.append(validate(frame, "observation_frame"))
        frame_id += 1
    return frames


@dataclass(frozen=True)
class Scene:
    """A scene's tracks and length, and the events it should produce (action, a_id, b_id, direction, start, end).

    A transfer is one edge at its touch's first hit, so its start and end are the same; a wash spans its rinse.
    """

    tracks: list[Track]
    length_ms: int
    expected: list[tuple[str, str, str, str, int, int]]

    def frames(self, fps: float = 30.0) -> list[dict]:
        """The scene as ObservationFrames at `fps`."""
        return render(self.tracks, self.length_ms, fps)


KNIFE = "tool-knife-1"

SCENES = {
    # The knife dips into the bin (overlapping from about 175 ms) and sits there until 1100, then moves onto the
    # plate (overlapping from about 1395 ms) and rests there until 2000, then leaves (overlap ends about 2140).
    # Contact needs 200 ms of overlap and a scoop 300 ms, so one edge each: contact with the bin at about 375 and a
    # scoop at about 475 (the knife leaves the bin at about 1190), then contact with the plate at about 1595.
    "scoop_then_plate": Scene(
        tracks=[
            still("bin-1", "bin", BIN_AT, 2600),
            still("plate-t1", "plate", PLATE_AT, 2600),
            Track(
                KNIFE,
                "tool",
                (
                    (0, (0.35, 0.15)),
                    (300, BIN_AT),
                    (1100, BIN_AT),
                    (1500, PLATE_AT),
                    (2000, PLATE_AT),
                    (2300, (0.7, 0.5)),
                    (2600, (0.7, 0.5)),
                ),
            ),
        ],
        length_ms=2600,
        expected=[
            ("transfer.contact", KNIFE, "bin-1", "both", 375, 375),
            ("transfer.scoop", KNIFE, "bin-1", "b_to_a", 475, 475),
            ("transfer.contact", KNIFE, "plate-t1", "both", 1595, 1595),
        ],
    ),
    # The knife stops 0.02 widths right of the plate for a second: near enough to be a candidate, never touching.
    "hover": Scene(
        tracks=[
            still("plate-t1", "plate", PLATE_AT, 1900),
            Track(
                KNIFE,
                "tool",
                ((0, (0.8, 0.5)), (300, (0.615, 0.5)), (1300, (0.615, 0.5)), (1600, (0.8, 0.5)), (1900, (0.8, 0.5))),
            ),
        ],
        length_ms=1900,
        expected=[],
    ),
    # The knife sweeps across the plate in 200 ms, overlapping it for under 100 ms: a near miss, not a touch.
    "pass_over": Scene(
        tracks=[
            still("plate-t1", "plate", PLATE_AT, 800),
            Track(KNIFE, "tool", ((0, (0.3, 0.5)), (300, (0.3, 0.5)), (500, (0.7, 0.5)), (800, (0.7, 0.5)))),
        ],
        length_ms=800,
        expected=[],
    ),
    # The knife enters the wash zone at about 180 ms and stays until about 4420. The wash needs 3 s of dwell, so it
    # starts about 3180, and it is a self-loop on the knife.
    "wash": Scene(
        tracks=[
            Track(
                KNIFE,
                "tool",
                ((0, (0.5, 0.8)), (300, WASH_AT), (4300, WASH_AT), (4600, (0.5, 0.8)), (5000, (0.5, 0.8))),
            ),
        ],
        length_ms=5000,
        expected=[("clean.wash", KNIFE, KNIFE, "both", 3180, 4420)],
    ),
    # One rinse with the knife unseen for 400 ms in the middle (a hand over it): it enters the wash zone at about 180
    # and leaves at about 7420, so it is one wash from about 3180 to 7420, never two. Vision keeps its ID through the
    # loss, and two washes would clean twice.
    "wash_with_dropout": Scene(
        tracks=[
            Track(
                KNIFE,
                "tool",
                ((0, (0.5, 0.8)), (300, WASH_AT), (7300, WASH_AT), (7600, (0.5, 0.8)), (8000, (0.5, 0.8))),
                unseen=((4000, 4400),),
            ),
        ],
        length_ms=8000,
        expected=[("clean.wash", KNIFE, KNIFE, "both", 3180, 7420)],
    ),
    # The same rinse with the knife's box center jittering just out of the wash zone (x 0.32, past its 0.3 edge) from
    # about 4040 to 4360: still one wash, from about 3180 to 7420.
    "wash_with_jitter": Scene(
        tracks=[
            Track(
                KNIFE,
                "tool",
                (
                    (0, (0.5, 0.8)),
                    (300, WASH_AT),
                    (4000, WASH_AT),
                    (4050, (0.32, 0.82)),
                    (4350, (0.32, 0.82)),
                    (4400, WASH_AT),
                    (7300, WASH_AT),
                    (7600, (0.5, 0.8)),
                    (8000, (0.5, 0.8)),
                ),
            ),
        ],
        length_ms=8000,
        expected=[("clean.wash", KNIFE, KNIFE, "both", 3180, 7420)],
    ),
    # The knife passes through the wash zone in about a second: too short to be a wash.
    "brief_wash": Scene(
        tracks=[
            Track(
                KNIFE,
                "tool",
                ((0, (0.5, 0.8)), (300, WASH_AT), (1000, WASH_AT), (1300, (0.5, 0.8)), (1600, (0.5, 0.8))),
            ),
        ],
        length_ms=1600,
        expected=[],
    ),
    # A hand comes down onto the plate (overlapping from about 230 ms, so a contact at about 430) and lifts off at
    # about 970.
    "hand_touches_plate": Scene(
        tracks=[
            still("plate-t1", "plate", PLATE_AT, 1500),
            Track(
                "hand-p1-right",
                "hand",
                ((0, (0.5, 0.8)), (300, (0.5, 0.55)), (900, (0.5, 0.55)), (1200, (0.5, 0.8)), (1500, (0.5, 0.8))),
            ),
        ],
        length_ms=1500,
        expected=[("transfer.contact", "hand-p1-right", "plate-t1", "both", 430, 430)],
    ),
}


def unmatched(events: list[dict], expected: list[tuple], tolerance_ms: int = 200) -> list:
    """What doesn't line up between events and the expected ones; empty when every expected event is matched by one
    event within tolerance_ms on both ends, and none is left over."""
    left = list(events)
    missing = []
    for action, a_id, b_id, direction, start_ms, end_ms in expected:
        match = next(
            (
                event
                for event in left
                if (event["action"], event["a_id"], event["b_id"], event["direction"])
                == (action, a_id, b_id, direction)
                and abs(event["t_start_ms"] - start_ms) <= tolerance_ms
                and abs(event["t_end_ms"] - end_ms) <= tolerance_ms
            ),
            None,
        )
        if match is None:
            missing.append((action, a_id, b_id, direction, start_ms, end_ms))
        else:
            left.remove(match)
    return missing + left
