"""Tracking: a small ByteTrack-style tracker per class, then each frame's observation detections, with stable entity IDs
(identity.py), hand boxes from pose wrists, zones and origins.

Owner: vision (agents)
Step:  vision
Reads: list[Detection] and list[Person] from detect.py, a ZoneLookup from zones.py, the frame's t_ms and size
Emits: observation_frame detection items (track_id, entity_id, kind, class_name, box, confidence, zone, origin, and
       person_id and wrist for hands), in track order
Rules: fail closed: a lost track emits nothing (no box is predicted or interpolated) and no hand is made from an
       unsure wrist; known-clean source: a track that ends is never revived, and track IDs are never reused, so an
       object seen again is a new entity (identity.py)

Association, per class group (each object class, and pose people), ByteTrack style but with no motion model:
    1. detections scoring >= track_high_score match tracks by IoU, greedily, best pair first, IoU >= track_match_iou;
    2. detections scoring >= track_low_score match the tracks still unmatched, the same way;
    3. unmatched high detections start new tracks; unmatched low ones are dropped;
    4. a track unmatched for more than track_lost_frames frames ends for good.
"""

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from kitchen.shared.models import Zone
from kitchen.vision.common import Box, Detection, Keypoint, Person, VisionSettings, ZoneLookup
from kitchen.vision.identity import BOWL, TOOL_CLASSES, Identity, IdentityBook

OBJECT_CLASSES = (*TOOL_CLASSES, BOWL)


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
    """One live track: its ID, the box it was last matched to, and how many frames in a row it has gone unmatched."""

    track_id: int
    box: Box
    lost_frames: int = 0


class TrackSet:
    """The tracks of one class group. `new_track_id` hands out IDs that only go up, shared by every group, so a track
    ID is never reused anywhere in the shift."""

    def __init__(self, settings: VisionSettings, new_track_id: Callable[[], int]):
        self.settings = settings
        self.new_track_id = new_track_id
        self.tracks: list[Track] = []

    def update(self, boxes: list[Box], scores: list[float]) -> tuple[dict[int, int], list[int]]:
        """Associate one frame's detections with the tracks.

        Returns which track each matched or new detection belongs to ({detection index: track_id}) and the IDs of
        tracks that ended this frame. Detections not in the first dict are dropped.
        """
        high = [index for index, score in enumerate(scores) if score >= self.settings.track_high_score]
        low = [
            index
            for index, score in enumerate(scores)
            if self.settings.track_low_score <= score < self.settings.track_high_score
        ]

        owners: dict[int, int] = {}
        unmatched_tracks = self._match(self.tracks, high, boxes, owners)
        unmatched_tracks = self._match(unmatched_tracks, low, boxes, owners)

        ended = self._age(unmatched_tracks)

        # Only a confident detection starts a track: a weak one alone is too likely a false positive.
        for index in high:
            if index not in owners:
                track = Track(self.new_track_id(), boxes[index])
                self.tracks.append(track)
                owners[index] = track.track_id

        return owners, ended

    def _match(self, tracks: list[Track], indexes: list[int], boxes: list[Box], owners: dict[int, int]) -> list[Track]:
        """Match `tracks` to the detections at `indexes`, record the matches in `owners`, and return the tracks left
        unmatched."""
        iou = iou_matrix([track.box for track in tracks], [boxes[index] for index in indexes])
        matched = set()
        for row, column in greedy_match(iou, self.settings.track_match_iou):
            track = tracks[row]
            track.box = boxes[indexes[column]]
            track.lost_frames = 0
            owners[indexes[column]] = track.track_id
            matched.add(row)
        return [track for row, track in enumerate(tracks) if row not in matched]

    def _age(self, unmatched_tracks: list[Track]) -> list[int]:
        """Count a missed frame for each unmatched track and end those lost too long. Returns the ended IDs."""
        ended = []
        for track in unmatched_tracks:
            track.lost_frames += 1
            if track.lost_frames > self.settings.track_lost_frames:
                ended.append(track.track_id)
        self.tracks = [track for track in self.tracks if track.track_id not in ended]
        return ended


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
        self.settings = settings
        track_ids = itertools.count(1)
        self.object_tracks = {name: TrackSet(settings, lambda: next(track_ids)) for name in OBJECT_CLASSES}
        self.person_tracks = TrackSet(settings, lambda: next(track_ids))
        self.identities = IdentityBook(settings.shift_start_ms)

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

        items = []
        for class_name in OBJECT_CLASSES:
            detections = [detection for detection in objects if detection.class_name == class_name]
            items += self._track_objects(class_name, detections, t_ms, zone_of)
        items += self._track_people(people, t_ms, zone_of, width, height)

        items.sort(key=lambda item: (item["track_id"], item["entity_id"]))
        return items

    def _track_objects(self, class_name: str, detections: list[Detection], t_ms: int, zone_of: ZoneLookup) -> list:
        """Track one object class and return its detection items."""
        owners, ended = self.object_tracks[class_name].update(
            [detection.box for detection in detections], [detection.confidence for detection in detections]
        )
        # Ended tracks go first, so a bin slot freed this frame can go to a bowl first seen in it this frame.
        for track_id in ended:
            self.identities.end_track(track_id)

        items = []
        for index, track_id in owners.items():
            detection = detections[index]
            zone = zone_of(box_centre(detection.box))
            identity = self.identities.object_identity(track_id, class_name, t_ms, zone)
            items.append(detection_item(track_id, identity, class_name, detection.box, detection.confidence, zone))
        return items

    def _track_people(self, people: list[Person], t_ms: int, zone_of: ZoneLookup, width: int, height: int) -> list:
        """Track pose people and return a hand item for each confident wrist."""
        owners, ended = self.person_tracks.update(
            [person.box for person in people], [person.confidence for person in people]
        )
        for track_id in ended:
            self.identities.end_track(track_id)

        items = []
        for index, track_id in owners.items():
            person = people[index]
            hands = (("left", person.left_wrist, person.left_elbow), ("right", person.right_wrist, person.right_elbow))
            for side, wrist, elbow in hands:
                # An unsure wrist isn't a hand: no hand is invented from a guess (fail closed).
                if wrist[2] < self.settings.wrist_min_confidence:
                    continue
                zone = zone_of((wrist[0], wrist[1]))
                identity = self.identities.hand_identity(track_id, side, t_ms)
                box = hand_box(wrist, elbow, self.settings, width, height)
                item = detection_item(track_id, identity, "wrist", box, wrist[2], zone)
                item["person_id"] = self.identities.person_label(track_id)
                item["wrist"] = [float(wrist[0]), float(wrist[1]), float(wrist[2])]
                items.append(item)
        return items


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
