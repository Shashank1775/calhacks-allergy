"""Tracking: a small ByteTrack-style tracker per class, then each frame's observation detections, with stable entity IDs
(identity.py), hand boxes from pose wrists, zones and origins.

Owner: vision (agents)
Step:  vision
Reads: list[Detection] and list[Person] from detect.py, a ZoneLookup from zones.py, the frame's t_ms and size
Emits: observation_frame detection items (track_id, entity_id, kind, class_name, box, confidence, zone, origin, and
       person_id and wrist for hands), in track order
Rules: fail closed: a lost track emits nothing (no box is predicted or interpolated; a predicted box is only ever used
       to match) and no hand is made from an unsure wrist; known-clean source: a track that ends is never revived, and
       track IDs are never reused, so an object seen again is a new entity (identity.py)

Association, per class group (each object class, and pose people), ByteTrack style:
    1. a track last matched more than track_lost_ms ago ends for good, even if frames stopped coming meanwhile;
    2. detections scoring >= track_high_score match tracks by IoU, greedily, best pair first, IoU >= track_match_iou;
    3. detections scoring >= track_low_score match the tracks still unmatched, the same way;
    4. unmatched high detections start new tracks; unmatched low ones are dropped.

The tracks a frame leaves unmatched are handed to identity.py with every new track that frame: a new track then may be
one of those objects under a new ID (an ID switch), so it can't claim a clean origin. Bowls and plates share this
guard: a detector with a plate class may call one plate a plate in one frame and a bowl in the next, so a relabeled
dirty plate carried into the clean stack can't start clean.

VISION_TRACKER picks one of two trackers. "iou" (the default) matches each detection to a track's last box, as above.
"motion" is experimental, off until real overhead footage shows it helps (compare_trackers.py). It adds to the above:
    - a recently seen track is matched at its predicted box (last box moved by its velocity), not its last box, so a
      utensil moving faster than its own width per frame still overlaps itself;
    - recently seen tracks match first, and a track lost for longer only gets the detections left over, so an object
      that is being followed is not handed to the stale track of another object that used to lie where it now is;
    - a new track started next to a track that just went missing takes that track's motion (never its identity: it is
      a new entity, with unknown history if it may be a switch), so an object that broke its track at full speed is
      followed again from its next frame instead of breaking every frame;
    - after both IoU passes, a recently seen track that is still unmatched and moving may take the one free detection
      within motion_gate_diagonals of its predicted centre, but only if no other recent track could take that
      detection and no other free detection is in its reach. A still object the detector missed for a frame is still
      where it was, so it never reaches for a neighbour. Anything ambiguous stays unmatched: the newcomer starts a new
      track with unknown history rather than inheriting a guessed identity;
    - stricter identity guards: one ID-switch guard and one clean-stack loss count for all tool classes together (a
      knife the detector relabels "spoon" can't start clean), and a pose left/right wrist swap re-acquires both hands.
"""

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from kitchen.shared.models import Zone
from kitchen.vision.common import Box, Detection, Keypoint, Person, VisionSettings, ZoneLookup
from kitchen.vision.identity import DISH_CLASSES, OBJECT_CLASSES, TOOL_CLASSES, Identity, IdentityBook

TRACKERS = ("iou", "motion")

# Motion mode: how much each new measured velocity moves a track's velocity (an exponential moving average).
VELOCITY_SMOOTHING = 0.5
# Motion mode: how far (in the lost track's box diagonals) a new track may be from a track that just went missing to
# take its motion. A utensil carried at a frame-width a second moves about one diagonal a frame at 15 fps.
HANDOFF_REACH_DIAGONALS = 3.0
# Motion mode: a lost track counts as having been moving if its velocity would carry it out of its own box (half its
# size along x or y) in this long, a frame or two. Box jitter on a still knife is a few percent of the box.
MOVING_HORIZON_MS = 100


def iou_matrix(boxes_a: list[Box], boxes_b: list[Box]) -> np.ndarray:
    """IoU of every box in `boxes_a` (rows) with every box in `boxes_b` (columns)."""
    if not boxes_a or not boxes_b:
        return np.zeros((len(boxes_a), len(boxes_b)))

    a = np.asarray(boxes_a, dtype=np.float64)[:, None, :]
    b = np.asarray(boxes_b, dtype=np.float64)[None, :, :]
    width = np.clip(np.minimum(a[..., 2], b[..., 2]) - np.maximum(a[..., 0], b[..., 0]), 0, None)
    height = np.clip(np.minimum(a[..., 3], b[..., 3]) - np.maximum(a[..., 1], b[..., 1]), 0, None)
    overlap = width * height
    area_a = (a[..., 2] - a[..., 0]) * (a[..., 3] - a[..., 1])
    area_b = (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
    union = area_a + area_b - overlap
    return np.divide(overlap, union, out=np.zeros_like(overlap), where=union > 0)


def greedy_match(iou: np.ndarray, min_iou: float) -> list[tuple[int, int]]:
    """Pairs (row, column) chosen best IoU first, each row and column used at most once, all with IoU >= min_iou.

    Ties go to the lower row, then the lower column, so the same input always gives the same pairs.
    """
    pairs = []
    used_rows = set()
    used_columns = set()
    for flat_index in np.argsort(-iou, axis=None, kind="stable"):
        row, column = divmod(int(flat_index), iou.shape[1])
        if iou[row, column] < min_iou:
            break
        if row in used_rows or column in used_columns:
            continue
        pairs.append((row, column))
        used_rows.add(row)
        used_columns.add(column)
    return pairs


@dataclass
class Track:
    """One live track: its ID, the box it was last matched to, and the t_ms of that match. Motion mode also keeps its
    velocity (normalized units per ms) and how many updates in a row it has gone unmatched."""

    track_id: int
    box: Box
    last_seen_ms: int
    velocity: tuple[float, float] = (0.0, 0.0)
    misses: int = 0


class TrackSet:
    """The tracks of one class group. `new_track_id` hands out IDs that only go up, shared by every group, so a track
    ID is never reused anywhere in the shift."""

    def __init__(self, settings: VisionSettings, new_track_id: Callable[[], int]):
        self.settings = settings
        self.new_track_id = new_track_id
        self.tracks: list[Track] = []
        self.motion = settings.tracker == "motion"

    def update(
        self, boxes: list[Box], scores: list[float], t_ms: int, frame_size: tuple[int, int] = (1, 1)
    ) -> tuple[dict[int, int], list[int], list[int]]:
        """Associate one frame's detections, seen at `t_ms`, with the tracks.

        Returns which track each matched or new detection belongs to ({detection index: track_id}), the IDs of tracks
        that ended this frame, and the IDs of every track left unmatched this frame (the ended ones too). Detections
        not in the first dict are dropped. `frame_size` (width, height) in pixels is used by motion mode, so its reach
        is measured in pixels and stays round on a wide frame.
        """
        high = [index for index, score in enumerate(scores) if score >= self.settings.track_high_score]
        low = [
            index
            for index, score in enumerate(scores)
            if self.settings.track_low_score <= score < self.settings.track_high_score
        ]

        ended = self._end_stale_tracks(t_ms)

        owners: dict[int, int] = {}
        if self.motion:
            unmatched_tracks = self._match_recent_first(high, low, boxes, owners, t_ms, frame_size)
        else:
            unmatched_tracks = self._match(self.tracks, high, boxes, owners, t_ms)
            unmatched_tracks = self._match(unmatched_tracks, low, boxes, owners, t_ms)
        missing_recent = [track for track in unmatched_tracks if self._is_recent(track, t_ms)]
        for track in unmatched_tracks:
            track.misses += 1
        missing = ended + [track.track_id for track in unmatched_tracks]

        # Only a confident detection starts a track: a weak one alone is too likely a false positive.
        for index in high:
            if index not in owners:
                track = Track(self.new_track_id(), boxes[index], t_ms)
                if self.motion:
                    track.velocity = self._handed_off_velocity(track, missing_recent, frame_size)
                self.tracks.append(track)
                owners[index] = track.track_id

        return owners, ended, missing

    def _match_recent_first(
        self,
        high: list[int],
        low: list[int],
        boxes: list[Box],
        owners: dict[int, int],
        t_ms: int,
        frame_size: tuple[int, int],
    ) -> list[Track]:
        """Motion mode's association: recently seen tracks match first (both score bands, then the second chance), and
        only then do tracks lost for longer get the detections left over. Returns the tracks left unmatched.

        Two rules keep a stale track from taking another object's detection (fail closed: the detection starts a new
        track with unknown history instead):
            - a stale track that was moving when it was lost never matches again: its object isn't where it was lost,
              so whatever is there now (say, a dirty knife carried onto the spot a clean one just left) is another
              object. A still object, such as a knife covered by a hand, is still matched where it lay;
            - a stale track never takes a detection that overlaps a box another track matched this frame (two objects
              are crossing there), or that a moving recent track still unmatched could reach (an object just carried
              onto the spot where the stale track's object lay, as when two knives swap places): either way it could
              as well be claiming the other object.
        """
        recent = [track for track in self.tracks if self._is_recent(track, t_ms)]
        stale = [track for track in self.tracks if not self._is_recent(track, t_ms)]
        still_stale = [track for track in stale if not self._leaves_its_box(track, MOVING_HORIZON_MS)]
        unmatched_recent = self._match(recent, high, boxes, owners, t_ms)
        unmatched_recent = self._match(unmatched_recent, low, boxes, owners, t_ms)
        unmatched_recent = self._second_chance(unmatched_recent, stale, high + low, boxes, owners, t_ms, frame_size)

        matched_boxes = [boxes[index] for index in owners]
        moving_unmatched = [
            track for track in unmatched_recent if self._leaves_its_box(track, t_ms - track.last_seen_ms)
        ]
        clear = {
            index
            for index in high + low
            if index not in owners
            and not overlaps_any(boxes[index], matched_boxes)
            and not any(self._within_reach(track, boxes[index], t_ms, frame_size) for track in moving_unmatched)
        }
        unmatched_still = self._match(still_stale, [index for index in high if index in clear], boxes, owners, t_ms)
        unmatched_still = self._match(unmatched_still, [index for index in low if index in clear], boxes, owners, t_ms)
        return unmatched_recent + unmatched_still + [track for track in stale if track not in still_stale]

    def _handed_off_velocity(
        self, new_track: Track, missing_recent: list[Track], frame_size: tuple[int, int]
    ) -> tuple[float, float]:
        """Motion mode: the velocity a new track starts with. If exactly one recently seen track went missing this
        frame within HANDOFF_REACH_DIAGONALS of it, the move from that track's last box to the new one, since the new
        track is most likely that object outrunning its track. Otherwise at rest.

        Only motion is handed off, never identity: the new track is a new entity (identity.py makes it unknown, since
        a track went missing), and a wrong velocity can at worst make it lose its object again.
        """
        width, height = frame_size
        new_x, new_y = box_centre(new_track.box)
        near = []
        for track in missing_recent:
            old_x, old_y = box_centre(track.box)
            x1, y1, x2, y2 = track.box
            diagonal = math.hypot((x2 - x1) * width, (y2 - y1) * height)
            distance = math.hypot((new_x - old_x) * width, (new_y - old_y) * height)
            if distance <= HANDOFF_REACH_DIAGONALS * diagonal:
                near.append(track)
        if len(near) != 1:
            return (0.0, 0.0)
        old_x, old_y = box_centre(near[0].box)
        elapsed_ms = max(1, new_track.last_seen_ms - near[0].last_seen_ms)
        return ((new_x - old_x) / elapsed_ms, (new_y - old_y) / elapsed_ms)

    def _match(
        self, tracks: list[Track], indexes: list[int], boxes: list[Box], owners: dict[int, int], t_ms: int
    ) -> list[Track]:
        """Match `tracks` to the detections at `indexes` not already in `owners`, record the matches in `owners`, and
        return the tracks left unmatched. Motion mode compares each recently seen track's predicted box, not its last
        box."""
        indexes = [index for index in indexes if index not in owners]
        iou = iou_matrix([self._match_box(track, t_ms) for track in tracks], [boxes[index] for index in indexes])
        matched = set()
        for row, column in greedy_match(iou, self.settings.track_match_iou):
            self._take(tracks[row], boxes[indexes[column]], t_ms)
            owners[indexes[column]] = tracks[row].track_id
            matched.add(row)
        return [track for row, track in enumerate(tracks) if row not in matched]

    def _take(self, track: Track, box: Box, t_ms: int) -> None:
        """Move a track to its matched detection's box. Motion mode also updates its velocity, from this move if the
        track was recently seen, or from rest if it had been missing longer (its old motion says nothing now)."""
        if self.motion:
            if self._is_recent(track, t_ms):
                elapsed_ms = max(1, t_ms - track.last_seen_ms)
                (old_x, old_y), (new_x, new_y) = box_centre(track.box), box_centre(box)
                measured = ((new_x - old_x) / elapsed_ms, (new_y - old_y) / elapsed_ms)
                track.velocity = (
                    VELOCITY_SMOOTHING * measured[0] + (1 - VELOCITY_SMOOTHING) * track.velocity[0],
                    VELOCITY_SMOOTHING * measured[1] + (1 - VELOCITY_SMOOTHING) * track.velocity[1],
                )
            else:
                track.velocity = (0.0, 0.0)
        track.box = box
        track.last_seen_ms = t_ms
        track.misses = 0

    def _is_recent(self, track: Track, t_ms: int) -> bool:
        """Whether a track was matched in the last update or the one before, at most motion_recent_ms ago: only then is
        its motion current enough to predict from."""
        return track.misses <= 1 and t_ms - track.last_seen_ms <= self.settings.motion_recent_ms

    def _match_box(self, track: Track, t_ms: int) -> Box:
        """The box a track is matched at: in motion mode a recently seen track's last box moved by its velocity, and
        otherwise its last box. Used only to match: an emitted box is always the detection's own."""
        if not (self.motion and self._is_recent(track, t_ms)):
            return track.box
        elapsed_ms = t_ms - track.last_seen_ms
        shift_x, shift_y = track.velocity[0] * elapsed_ms, track.velocity[1] * elapsed_ms
        x1, y1, x2, y2 = track.box
        return (x1 + shift_x, y1 + shift_y, x2 + shift_x, y2 + shift_y)

    def _second_chance(
        self,
        tracks: list[Track],
        stale: list[Track],
        indexes: list[int],
        boxes: list[Box],
        owners: dict[int, int],
        t_ms: int,
        frame_size: tuple[int, int],
    ) -> list[Track]:
        """Motion mode: let each moving, recently seen, still unmatched track take the one free detection within its
        reach, when the pairing is unambiguous both ways. Returns the tracks still unmatched.

        Only a track moving out of its own box may take one: a still object the detector missed for a frame hasn't gone anywhere, so a
        detection near it is another object (say a knife just put down beside it in the rack). Unambiguous means
        exactly one free detection is within the track's reach and no other unmatched track, recent or `stale`, could
        claim that detection (_could_claim). Two utensils crossing reach each other's detections, so neither takes one here: a wrong
        pairing would hand one object the other's history (possibly a clean one), while no pairing makes the newcomer
        a new track with unknown history (fail closed).
        """
        free = [index for index in indexes if index not in owners]
        recent = [track for track in tracks if self._is_recent(track, t_ms)]
        reach = {
            track.track_id: [index for index in free if self._within_reach(track, boxes[index], t_ms, frame_size)]
            for track in recent
        }
        claimants: dict[int, int] = {}
        for track in recent + stale:
            for index in free:
                if self._could_claim(track, boxes[index], t_ms, frame_size):
                    claimants[index] = claimants.get(index, 0) + 1

        taken = set()
        for track in recent:
            if not self._leaves_its_box(track, t_ms - track.last_seen_ms):
                continue
            candidates = reach[track.track_id]
            if len(candidates) == 1 and claimants[candidates[0]] == 1:
                self._take(track, boxes[candidates[0]], t_ms)
                owners[candidates[0]] = track.track_id
                taken.add(track.track_id)
        return [track for track in tracks if track.track_id not in taken]

    def _could_claim(self, track: Track, box: Box, t_ms: int, frame_size: tuple[int, int]) -> bool:
        """Whether an unmatched track could be the owner of a detection, so that another track taking it would be a
        guess: a moving track anywhere within its reach (from its last box if it is stale), a still track only where
        the detection overlaps its box (a still object reappears where it lay)."""
        if self._leaves_its_box(track, t_ms - track.last_seen_ms):
            return self._within_reach(track, box, t_ms, frame_size)
        return overlaps_any(box, [track.box])

    def _leaves_its_box(self, track: Track, elapsed_ms: float) -> bool:
        """Whether a track's velocity carries it at least half its own width (or height) along x (or y) in
        `elapsed_ms`: only then can its object have left its old box. Measured per axis, against the box's size along
        that axis, so box jitter (a few percent of the box) never counts as moving, whatever the box's shape."""
        x1, y1, x2, y2 = track.box
        return (
            abs(track.velocity[0]) * elapsed_ms >= (x2 - x1) / 2 or abs(track.velocity[1]) * elapsed_ms >= (y2 - y1) / 2
        )

    def _within_reach(self, track: Track, box: Box, t_ms: int, frame_size: tuple[int, int]) -> bool:
        """Whether a detection's centre is within motion_gate_diagonals of the track's predicted centre, the distance
        measured in pixels as a multiple of the track box's diagonal (a small, thin knife reaches less far than a
        plate)."""
        width, height = frame_size
        predicted_x, predicted_y = box_centre(self._match_box(track, t_ms))
        centre_x, centre_y = box_centre(box)
        distance = math.hypot((centre_x - predicted_x) * width, (centre_y - predicted_y) * height)
        x1, y1, x2, y2 = track.box
        diagonal = math.hypot((x2 - x1) * width, (y2 - y1) * height)
        return distance <= self.settings.motion_gate_diagonals * diagonal

    def _end_stale_tracks(self, t_ms: int) -> list[int]:
        """End the tracks last matched more than track_lost_ms before `t_ms`, before they can match. Returns their IDs.

        Time, not a frame count: at a low frame rate, or after the camera stalls, a frame count would let an object
        come back under its old ID (and its clean origin) after a much longer absence than the lost buffer.
        """
        ended = [track.track_id for track in self.tracks if t_ms - track.last_seen_ms > self.settings.track_lost_ms]
        self.tracks = [track for track in self.tracks if track.track_id not in ended]
        return ended


def overlaps_any(box: Box, others: list[Box]) -> bool:
    """Whether a box shares any area with one of `others`."""
    return any(
        min(box[2], other[2]) > max(box[0], other[0]) and min(box[3], other[3]) > max(box[1], other[1])
        for other in others
    )


def box_centre(box: Box) -> tuple[float, float]:
    """The centre point of a normalized box."""
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def hand_box(wrist: Keypoint, elbow: Keypoint, settings: VisionSettings, width: int, height: int) -> Box:
    """The square box a hand covers: side hand_box_size times the frame's larger side, placed beyond the wrist away
    from the elbow (the hand, not the forearm), or centred on the wrist when the elbow isn't confident.

    Works in pixels so the box stays square on a non-square frame. A box past the frame edge is shifted back inside,
    not clipped: clipping would shrink a hand at the edge to a sliver (or nothing) that never overlaps the plate or bin
    it touches, and a missed contact leans toward PASS.
    """
    side = settings.hand_box_size * max(width, height)
    centre_x = wrist[0] * width
    centre_y = wrist[1] * height

    if elbow[2] >= settings.wrist_min_confidence:
        forearm_x = centre_x - elbow[0] * width
        forearm_y = centre_y - elbow[1] * height
        length = math.hypot(forearm_x, forearm_y)
        if length > 0:
            centre_x += forearm_x / length * side / 2
            centre_y += forearm_y / length * side / 2

    x1, x2 = span_inside(centre_x, side, width)
    y1, y2 = span_inside(centre_y, side, height)
    return (x1 / width, y1 / height, x2 / width, y2 / height)


def span_inside(centre: float, side: float, size: int) -> tuple[float, float]:
    """The span of length `side` around `centre`, shifted to lie within [0, size], or all of [0, size] when `side`
    is longer than that."""
    if side >= size:
        return (0.0, float(size))
    start = min(max(centre - side / 2, 0.0), size - side)
    return (start, start + side)


class Tracker:
    """Tracks every class group across frames and builds each frame's observation detections."""

    def __init__(self, settings: VisionSettings):
        if settings.tracker not in TRACKERS:
            raise ValueError(f"unknown VISION_TRACKER {settings.tracker!r}; expected one of {TRACKERS}")
        self.settings = settings
        self.strict = settings.tracker == "motion"
        track_ids = itertools.count(1)
        self.object_tracks = {name: TrackSet(settings, lambda: next(track_ids)) for name in OBJECT_CLASSES}
        self.person_tracks = TrackSet(settings, lambda: next(track_ids))
        self.identities = IdentityBook(settings.shift_start_ms, settings.track_lost_ms, strict=self.strict)
        # Motion mode: each pose track's last sighting with both wrists, (t_ms, left (x, y), right (x, y)).
        self._both_wrists: dict[int, tuple[int, tuple[float, float], tuple[float, float]]] = {}

    def update(
        self, objects: list[Detection], people: list[Person], t_ms: int, zone_of: ZoneLookup, width: int, height: int
    ) -> list[dict]:
        """One frame: track the objects and people, and return the frame's observation_frame detection items, sorted
        by track ID, then entity ID.

        `width` and `height` are the original frame's, which the hand box needs to stay square. Raises ValueError for
        a detector class with no entity kind, before any track changes.
        """
        unknown = sorted({detection.class_name for detection in objects} - set(OBJECT_CLASSES))
        if unknown:
            raise ValueError(f"no entity kind for detector classes {unknown}")

        items = self._track_objects(objects, t_ms, zone_of, (width, height))
        items += self._track_people(people, t_ms, zone_of, width, height)

        items.sort(key=lambda item: (item["track_id"], item["entity_id"]))
        return items

    def _track_objects(
        self, objects: list[Detection], t_ms: int, zone_of: ZoneLookup, frame_size: tuple[int, int]
    ) -> list:
        """Track every object class and return their detection items.

        All classes are associated before any identity is made, so the identities can see which tracks of every
        class went unmatched this frame (motion mode's one switch guard for all tools needs that).
        """
        results = {}
        for class_name in OBJECT_CLASSES:
            detections = [detection for detection in objects if detection.class_name == class_name]
            owners, ended, missing = self.object_tracks[class_name].update(
                [detection.box for detection in detections],
                [detection.confidence for detection in detections],
                t_ms,
                frame_size,
            )
            results[class_name] = (detections, owners, missing)
            # Ended tracks go first, so a bin slot freed this frame can go to a bowl first seen in it this frame.
            for track_id in ended:
                self.identities.end_track(track_id)

        # A detector can call one utensil a knife in one frame and a spoon in the next. Motion mode treats a missing
        # track of any tool class as a possible switch for a new tool of any class, so the relabel can't start clean.
        # Both modes do the same for plates and bowls: a plate relabeled as it is carried must not start clean either.
        any_tool_missing = any(results[class_name][2] for class_name in TOOL_CLASSES)
        any_dish_missing = any(results[class_name][2] for class_name in DISH_CLASSES)

        items = []
        for class_name, (detections, owners, missing) in results.items():
            maybe_switched = (
                bool(missing)
                or (class_name in DISH_CLASSES and any_dish_missing)
                or (self.strict and class_name in TOOL_CLASSES and any_tool_missing)
            )
            for index, track_id in owners.items():
                detection = detections[index]
                zone = zone_of(box_centre(detection.box))
                identity = self.identities.object_identity(track_id, class_name, t_ms, zone, maybe_switched)
                items.append(detection_item(track_id, identity, class_name, detection.box, detection.confidence, zone))
        return items

    def _track_people(self, people: list[Person], t_ms: int, zone_of: ZoneLookup, width: int, height: int) -> list:
        """Track pose people and return a hand item for each confident wrist."""
        owners, ended, missing = self.person_tracks.update(
            [person.box for person in people], [person.confidence for person in people], t_ms, (width, height)
        )
        for track_id in ended:
            self.identities.end_track(track_id)
            self._both_wrists.pop(track_id, None)

        items = []
        for index, track_id in owners.items():
            person = people[index]
            if self.strict and self._wrists_swapped(track_id, person, t_ms, width, height):
                # Pose put "left" on the hand that was right and vice versa: each hand would take the other's history,
                # so both start over as new hands with unknown history.
                self.identities.reacquire_hands(track_id)
            hands = (("left", person.left_wrist, person.left_elbow), ("right", person.right_wrist, person.right_elbow))
            for side, wrist, elbow in hands:
                # An unsure wrist isn't a hand: no hand is invented from a guess (fail closed).
                if wrist[2] < self.settings.wrist_min_confidence:
                    continue
                zone = zone_of((wrist[0], wrist[1]))
                identity = self.identities.hand_identity(track_id, side, t_ms, maybe_switched=bool(missing))
                box = hand_box(wrist, elbow, self.settings, width, height)
                item = detection_item(track_id, identity, "wrist", box, wrist[2], zone)
                item["person_id"] = self.identities.person_label(track_id)
                item["wrist"] = [float(wrist[0]), float(wrist[1]), float(wrist[2])]
                items.append(item)
        return items

    def _wrists_swapped(self, track_id: int, person: Person, t_ms: int, width: int, height: int) -> bool:
        """Motion mode: whether pose seems to have swapped this person's left and right wrists since the last frame
        that showed both, and remember this frame's wrists for the next check.

        Swapped means pairing each wrist with the other side's last position explains the move better than pairing it
        with its own, by more than one hand box. Hands farther apart than that can't trade places between two recent
        frames, so a pose label flip is the likelier story. Only checked when both wrists are confident now and were in
        a frame at most motion_recent_ms ago.
        """
        confident = min(person.left_wrist[2], person.right_wrist[2]) >= self.settings.wrist_min_confidence
        if not confident:
            return False
        left, right = (person.left_wrist[0], person.left_wrist[1]), (person.right_wrist[0], person.right_wrist[1])
        last = self._both_wrists.get(track_id)
        self._both_wrists[track_id] = (t_ms, left, right)
        if last is None or t_ms - last[0] > self.settings.motion_recent_ms:
            return False

        def pixels(a: tuple[float, float], b: tuple[float, float]) -> float:
            return math.hypot((a[0] - b[0]) * width, (a[1] - b[1]) * height)

        _, last_left, last_right = last
        straight = pixels(left, last_left) + pixels(right, last_right)
        crossed = pixels(left, last_right) + pixels(right, last_left)
        return straight - crossed > self.settings.hand_box_size * max(width, height)


def detection_item(
    track_id: int, identity: Identity, class_name: str, box: Box, confidence: float, zone: Zone | None
) -> dict:
    """One observation_frame detection item. Hand items get person_id and wrist added by the caller."""
    return {
        "track_id": track_id,
        "entity_id": identity.entity_id,
        "kind": identity.kind,
        "class_name": class_name,
        "box": [float(value) for value in box],
        "confidence": float(confidence),
        "zone": zone.zone_id if zone is not None else None,
        "origin": identity.origin,
    }
