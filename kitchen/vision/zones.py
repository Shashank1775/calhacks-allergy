"""Zones: the zones file, a click-to-calibrate tool, zone assignment for a point, and pushing zones to the database.

Owner: vision (agents)
Step:  vision
Reads: zones.json; a still camera image (calibration)
Emits: list[Zone]; a ZoneLookup (point -> Zone or None) for tracking.py; StateClient.set_zones
Rules: fail closed: a point in no zone gets None, never the nearest zone; overlapping zones resolve the same way every
       time (smallest area, then zone_id); a bad zones file fails loudly at load, never half-loads

The zones file:
    {"zones": [{"zone_id": "slot-1", "kind": "bin_slot", "polygon": [[x, y], [x, y], [x, y]]}, ...]}
kind is one of ZONE_KINDS (contracts/state-tables.md, zone table). Points are normalized to the original camera frame
(0..1, like boxes), a polygon has at least 3 points and some area, zone_ids are unique, and a bin slot is named
slot-<n> because identity maps slot-<n> to the entity bin-<n>.

Usage:
  uv run python -m kitchen.vision.zones calibrate --image <still.jpg> --out zones.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

from kitchen.shared.interfaces import StateClient
from kitchen.shared.models import Zone
from kitchen.vision.common import ZoneLookup

ZONE_KINDS = ("bin_slot", "plating_spot", "wash", "clean_stack", "pass", "other")

# slot-<n> with n a positive whole number written without leading zeros, so slot-1 and slot-01 can't both name bin 1.
BIN_SLOT_ID = re.compile(r"^slot-[1-9][0-9]*$")

ZONE_FIELDS = {"zone_id", "kind", "polygon"}

# Points closer than this to an edge count as on it (normalized units; far below one pixel of any camera).
EDGE_TOLERANCE = 1e-9


class ZoneFileError(ValueError):
    """A zones file or zone list that breaks the format. The message says which zone and what is wrong."""


def check_zones(zones: list[Zone]) -> None:
    """Raise ZoneFileError unless every zone follows the format: a known kind, 3+ points in 0..1 with nonzero area, a
    unique zone_id, and slot-<n> for bin slots. Run on load, save, lookup and push, so bad zones never get used."""
    seen = set()
    for zone in zones:
        name = f"zone {zone.zone_id!r}"
        if not isinstance(zone.zone_id, str) or not zone.zone_id:
            raise ZoneFileError(f"{name}: zone_id must be a non-empty string")
        if zone.zone_id in seen:
            raise ZoneFileError(f"{name}: zone_id is used more than once")
        seen.add(zone.zone_id)
        if zone.kind not in ZONE_KINDS:
            raise ZoneFileError(f"{name}: kind {zone.kind!r} is not one of {', '.join(ZONE_KINDS)}")
        if zone.kind == "bin_slot" and not BIN_SLOT_ID.match(zone.zone_id):
            raise ZoneFileError(f"{name}: a bin_slot must be named slot-<n> (n = 1, 2, ...) so it maps to bin-<n>")
        if len(zone.polygon) < 3:
            raise ZoneFileError(f"{name}: a polygon needs at least 3 points, got {len(zone.polygon)}")
        for x, y in zone.polygon:
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ZoneFileError(f"{name}: point ({x}, {y}) is outside the normalized frame 0..1")
        if polygon_area(zone.polygon) <= EDGE_TOLERANCE:
            raise ZoneFileError(f"{name}: the polygon has no area (its points are on one line)")


def parse_zones(data: object) -> list[Zone]:
    """Turn a zones file's parsed JSON into checked Zones, raising ZoneFileError on any problem.

    Unknown fields are refused rather than ignored, so a misspelled field can't silently drop part of a zone.
    """
    if not isinstance(data, dict) or set(data) != {"zones"} or not isinstance(data["zones"], list):
        raise ZoneFileError('a zones file is a JSON object with exactly one field, "zones", holding a list')
    zones = [parse_zone(index, item) for index, item in enumerate(data["zones"])]
    check_zones(zones)
    return zones


def parse_zone(index: int, item: object) -> Zone:
    """Turn one entry of the zones list into a Zone, checking its fields and their types."""
    name = f"zones[{index}]"
    if not isinstance(item, dict) or set(item) != ZONE_FIELDS:
        raise ZoneFileError(f"{name}: a zone is an object with exactly the fields zone_id, kind and polygon")
    if not isinstance(item["zone_id"], str) or not isinstance(item["kind"], str):
        raise ZoneFileError(f"{name}: zone_id and kind must be strings")
    polygon = item["polygon"]
    if not isinstance(polygon, list) or not all(is_point(point) for point in polygon):
        raise ZoneFileError(f"{name} ({item['zone_id']!r}): polygon must be a list of [x, y] number pairs")
    return Zone(zone_id=item["zone_id"], kind=item["kind"], polygon=[(float(x), float(y)) for x, y in polygon])


def is_point(value: object) -> bool:
    """True for an [x, y] pair of numbers (JSON true and false are not numbers here)."""
    if not isinstance(value, list) or len(value) != 2:
        return False
    return all(isinstance(number, int | float) and not isinstance(number, bool) for number in value)


def load_zones(path: str | Path) -> list[Zone]:
    """Read and check a zones file. A missing, unreadable or malformed file raises ZoneFileError naming the file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ZoneFileError(f"{path}: can't read the zones file: {error}") from error
    try:
        return parse_zones(data)
    except ZoneFileError as error:
        raise ZoneFileError(f"{path}: {error}") from error


def save_zones(path: str | Path, zones: list[Zone]) -> None:
    """Check `zones` and write them as a zones file, so a file this writes always loads again. One zone per line, so
    a person can read and diff the file."""
    check_zones(zones)
    lines = [
        json.dumps({"zone_id": zone.zone_id, "kind": zone.kind, "polygon": [[x, y] for x, y in zone.polygon]})
        for zone in zones
    ]
    Path(path).write_text('{"zones": [\n  ' + ",\n  ".join(lines) + "\n]}\n")


def push_zones(state: StateClient, zones: list[Zone]) -> None:
    """Check `zones` and replace the database's calibration with them. StateUnavailable passes through to the caller."""
    check_zones(zones)
    state.set_zones(zones)


def polygon_area(polygon: list[tuple[float, float]]) -> float:
    """The polygon's area (shoelace formula), always positive whichever way its points wind."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def on_segment(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> bool:
    """True if `point` lies on the segment from `a` to `b` (within EDGE_TOLERANCE)."""
    (px, py), (ax, ay), (bx, by) = point, a, b
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > EDGE_TOLERANCE:
        return False
    within_x = min(ax, bx) - EDGE_TOLERANCE <= px <= max(ax, bx) + EDGE_TOLERANCE
    within_y = min(ay, by) - EDGE_TOLERANCE <= py <= max(ay, by) + EDGE_TOLERANCE
    return within_x and within_y


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """True if `point` is inside `polygon` or on its edge (ray casting). A point on a shared edge is in both zones,
    and the lookup's tie-break then decides, so an edge never makes a point fall into no zone by accident."""
    edges = list(zip(polygon, polygon[1:] + polygon[:1], strict=True))
    if any(on_segment(point, a, b) for a, b in edges):
        return True
    px, py = point
    inside = False
    for (ax, ay), (bx, by) in edges:
        if (ay > py) != (by > py):
            crossing_x = ax + (py - ay) * (bx - ax) / (by - ay)
            if px < crossing_x:
                inside = not inside
    return inside


def zone_lookup(zones: list[Zone]) -> ZoneLookup:
    """Build the point -> zone function tracking.py uses for every detection.

    A point in no zone gets None, never the nearest zone (fail closed: a plate that isn't clearly at the plating spot
    can't be bound, and an object that isn't clearly leaving the clean stack doesn't start clean). Where zones overlap
    the smallest one wins, since it is the more specific place, and equal areas fall back to zone_id, so the answer
    never depends on the order of the zones file.
    """
    check_zones(zones)
    ordered = sorted(zones, key=lambda zone: (polygon_area(zone.polygon), zone.zone_id))

    def lookup(point: tuple[float, float]) -> Zone | None:
        for zone in ordered:
            if point_in_polygon(point, zone.polygon):
                return zone
        return None

    return lookup


def pixel_to_point(x: int, y: int, width: int, height: int) -> tuple[float, float]:
    """A clicked pixel as a normalized (x, y) point in 0..1 of the image, the same normalization boxes use."""
    return (min(max(x / width, 0.0), 1.0), min(max(y / height, 0.0), 1.0))


def add_zone(zones: list[Zone], zone: Zone) -> list[Zone]:
    """Return `zones` with `zone` appended, or raise ZoneFileError if the result breaks the format (the calibration
    tool shows the message and lets the person try again)."""
    updated = [*zones, zone]
    check_zones(updated)
    return updated


def ask_zone(zones: list[Zone], polygon: list[tuple[float, float]]) -> list[Zone]:
    """Ask in the terminal for the new polygon's zone_id and kind, and return the zones with it added. A bad answer
    prints why and returns the zones unchanged, so nothing invalid is ever saved."""
    zone_id = input("zone_id (bin slots are slot-<n>): ").strip()
    kind = input(f"kind ({', '.join(ZONE_KINDS)}): ").strip()
    try:
        return add_zone(zones, Zone(zone_id=zone_id, kind=kind, polygon=polygon))
    except ZoneFileError as error:
        print(f"not added: {error}")
        return zones


def draw(image, zones: list[Zone], clicks: list[tuple[int, int]]):
    """The image with the saved zones (green, labeled) and the polygon being clicked (red) drawn over it."""
    import cv2
    import numpy as np

    canvas = image.copy()
    height, width = canvas.shape[:2]
    for zone in zones:
        corners = np.array([[x * width, y * height] for x, y in zone.polygon], dtype=np.int32)
        cv2.polylines(canvas, [corners], True, (0, 200, 0), 2)
        label_at = (int(corners[0][0]) + 4, int(corners[0][1]) + 16)
        cv2.putText(canvas, f"{zone.zone_id} ({zone.kind})", label_at, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
    if clicks:
        cv2.polylines(canvas, [np.array(clicks, dtype=np.int32)], False, (0, 0, 255), 2)
        for click in clicks:
            cv2.circle(canvas, click, 4, (0, 0, 255), -1)
    return canvas


def calibrate(image_path: Path, out_path: Path) -> int:
    """Click-to-calibrate: click a polygon's corners on the still, press n to close it and name it in the terminal,
    u to undo a click, s to save, q to quit without saving. Returns the CLI exit code."""
    import cv2

    image = cv2.imread(str(image_path))
    if image is None:
        print(f"can't read the image {image_path}", file=sys.stderr)
        return 1
    height, width = image.shape[:2]
    window = "zones: click corners, n close polygon, u undo, s save, q quit"
    try:
        cv2.namedWindow(window)
    except cv2.error as error:
        print(f"this OpenCV build can't open a window ({error}); install a GUI build of opencv-python", file=sys.stderr)
        return 1

    zones: list[Zone] = []
    clicks: list[tuple[int, int]] = []

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicks.append((x, y))

    cv2.setMouseCallback(window, on_mouse)
    try:
        while True:
            cv2.imshow(window, draw(image, zones, clicks))
            key = chr(cv2.waitKey(30) & 0xFF)
            if key == "u" and clicks:
                clicks.pop()
            elif key == "n":
                if len(clicks) < 3:
                    print("a zone needs at least 3 corners")
                    continue
                zones = ask_zone(zones, [pixel_to_point(x, y, width, height) for x, y in clicks])
                clicks.clear()
            elif key == "s":
                if not zones:
                    print("no zones yet: close at least one polygon with n first")
                    continue
                save_zones(out_path, zones)
                print(f"wrote {len(zones)} zones to {out_path}")
                return 0
            elif key == "q":
                print("quit without saving")
                return 1
    finally:
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    """CLI: `calibrate --image <still.jpg> --out zones.json` opens the click-to-calibrate window."""
    parser = argparse.ArgumentParser(description="Kitchen zones: calibrate polygons on a still camera image.")
    commands = parser.add_subparsers(dest="command", required=True)
    calibrate_command = commands.add_parser("calibrate")
    calibrate_command.add_argument("--image", type=Path, required=True)
    calibrate_command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return calibrate(args.image, args.out)


if __name__ == "__main__":
    sys.exit(main())
