"""Identity: turns a track into a stable entity ID and origin (bin-<n>, tool-<class>-<n>, plate-<n>, hand-p<k>-left|right),
fixed for the entity's life.

Owner: vision (agents)
Step:  vision
Reads: track IDs and classes from tracking.py, the zone each track is first seen in, the shift start time
Emits: Identity (entity_id, kind, origin) for every tracked object and hand
Rules: known-clean source: a new track is a new entity, so a re-acquired object or person gets a new ID; origin is
       shift_start only up to shift start, clean_stack only for a tool or bowl first seen in a clean_stack zone (a
       hand never comes out of a stack), otherwise unknown; numbers are never reused

A clean stack can't vouch for an object that went missing inside it. When a tool or plate track ends while it is in a
clean_stack zone, the object may still be there, hidden, and may come back as a new track: a dirty knife put back in
the rack and covered by a hand would otherwise reappear clean. So the next new track of that class first seen in a
clean stack is unknown, not clean_stack, once for each such loss (every judgment call leans toward HOLD). A genuinely
new clean item arriving just then holds once: an annoyance, not a risk.

The one exception is bins. Orders keys bin profiles by bin-<n>, so a bowl first seen in the bin slot zone slot-<n> is
bin-<n>, and a bowl re-acquired in slot-<n> is bin-<n> again, keeping the origin bin-<n> was first given. Bins are
identified by slot, so swapping bins between slots mid-shift is unsupported. While a track holds bin-<n>, another new
bowl in slot-<n> is a plate: two tracks never share one entity ID.
"""

import re
from dataclasses import dataclass

from kitchen.shared.models import Zone

TOOL_CLASSES = ("knife", "spoon", "fork")
BOWL = "bowl"
HAND_SIDES = ("left", "right")

# A bin slot's zone ID must say its number; a slot named any other way gives no bin ID (no guessing).
SLOT_ZONE_ID = re.compile(r"^slot-([a-z0-9]+)$")


@dataclass(frozen=True)
class Identity:
    """The stable identity of one entity: its ID, its kind and how it entered the shift."""

    entity_id: str
    kind: str
    origin: str


def origin_for(t_ms: int, zone: Zone | None, shift_start_ms: int) -> str:
    """The origin of an entity first seen at `t_ms` in `zone` (AGENTS.md safety rule 8, known-clean source).

    Only shift_start and clean_stack start clean, so anything else is unknown and counts as carrying every allergen.
    """
    if t_ms <= shift_start_ms:
        return "shift_start"
    if zone is not None and zone.kind == "clean_stack":
        return "clean_stack"
    return "unknown"


def slot_number(zone: Zone | None) -> str | None:
    """The bin number of a bin slot zone named slot-<n>, or None for any other zone or none."""
    if zone is None or zone.kind != "bin_slot":
        return None
    match = SLOT_ZONE_ID.match(zone.zone_id)
    if match is None:
        return None
    return match.group(1)


class IdentityBook:
    """Hands out entity identities by track and remembers them for the track's life.

    Track IDs are never reused (tracking.py), so keying by track ID makes every new track a new entity. The counters
    only go up, so no tool, plate or person number is ever handed out twice in a shift.
    """

    def __init__(self, shift_start_ms: int):
        self.shift_start_ms = shift_start_ms
        self._by_track: dict[int, Identity] = {}
        self._tool_counts = dict.fromkeys(TOOL_CLASSES, 0)
        self._plate_count = 0
        self._person_count = 0
        self._person_by_track: dict[int, int] = {}
        self._hands: dict[tuple[int, str], Identity] = {}
        self._bin_holders: dict[str, int] = {}
        self._bin_origins: dict[str, str] = {}
        # Per detector class: tracks that ended inside a clean stack and may still be hiding there.
        self._lost_in_clean_stack = dict.fromkeys((*TOOL_CLASSES, BOWL), 0)
        self._class_by_track: dict[int, str] = {}
        self._zone_by_track: dict[int, Zone | None] = {}

    def object_identity(self, track_id: int, class_name: str, t_ms: int, zone: Zone | None) -> Identity:
        """The identity of an object track, made the first time the track is seen and fixed after that.

        `zone` is where the track's box centre is in this frame. It sets the identity on the track's first frame, and
        the latest one is remembered so a track that ends inside a clean stack is noticed.
        """
        self._zone_by_track[track_id] = zone
        known = self._by_track.get(track_id)
        if known is not None:
            return known
        self._class_by_track[track_id] = class_name

        if class_name in TOOL_CLASSES:
            identity = self._new_tool(class_name, t_ms, zone)
        elif class_name == BOWL:
            identity = self._new_bowl(track_id, t_ms, zone)
        else:
            raise ValueError(f"no entity kind for detector class {class_name!r}")

        self._by_track[track_id] = identity
        return identity

    def person_label(self, track_id: int) -> str:
        """The person ID p<k> of a pose track. A new pose track is always a new person, so a re-acquired person gets
        new hand IDs."""
        if track_id not in self._person_by_track:
            self._person_count += 1
            self._person_by_track[track_id] = self._person_count
        return f"p{self._person_by_track[track_id]}"

    def hand_identity(self, person_track_id: int, side: str, t_ms: int) -> Identity:
        """The identity of one hand of a pose track, made the first time that wrist is confidently seen.

        The hand's origin comes from that first sighting, not from when the person was first seen: a hand that only
        appears after shift start has unknown history (fail closed). The hand's zone is not asked: hands never come
        out of a clean stack, so a hand first seen reaching into one is still unknown (AGENTS.md safety rule 8).
        """
        key = (person_track_id, side)
        known = self._hands.get(key)
        if known is not None:
            return known

        if side not in HAND_SIDES:
            raise ValueError(f"no hand side {side!r}")

        person = self.person_label(person_track_id)
        origin = "shift_start" if t_ms <= self.shift_start_ms else "unknown"
        identity = Identity(f"hand-{person}-{side}", "hand", origin)
        self._hands[key] = identity
        return identity

    def end_track(self, track_id: int) -> None:
        """Forget a track that has ended for good: it frees the bin slot it held, and its entity is never handed out
        again (except a bin, by its slot)."""
        identity = self._by_track.pop(track_id, None)
        class_name = self._class_by_track.pop(track_id, None)
        last_zone = self._zone_by_track.pop(track_id, None)
        if identity is not None and identity.kind == "bin":
            del self._bin_holders[identity.entity_id]
        elif identity is not None and last_zone is not None and last_zone.kind == "clean_stack":
            self._lost_in_clean_stack[class_name] += 1

        if track_id in self._person_by_track:
            del self._person_by_track[track_id]
            for side in HAND_SIDES:
                self._hands.pop((track_id, side), None)

    def _origin(self, class_name: str, t_ms: int, zone: Zone | None) -> str:
        """origin_for(), except that a newcomer to a clean stack where an object of its class went missing is unknown."""
        origin = origin_for(t_ms, zone, self.shift_start_ms)
        if origin == "clean_stack" and self._lost_in_clean_stack[class_name] > 0:
            self._lost_in_clean_stack[class_name] -= 1
            return "unknown"
        return origin

    def _new_tool(self, class_name: str, t_ms: int, zone: Zone | None) -> Identity:
        """A new tool entity: the next number for its class."""
        self._tool_counts[class_name] += 1
        entity_id = f"tool-{class_name}-{self._tool_counts[class_name]}"
        return Identity(entity_id, "tool", self._origin(class_name, t_ms, zone))

    def _new_bowl(self, track_id: int, t_ms: int, zone: Zone | None) -> Identity:
        """A new bowl track: bin-<n> when first seen in a free slot-<n>, otherwise a new plate."""
        number = slot_number(zone)
        if number is not None:
            entity_id = f"bin-{number}"
            # A slot already held by a live track can't hand out its bin twice; the newcomer is a plate instead.
            if entity_id not in self._bin_holders:
                self._bin_holders[entity_id] = track_id
                # bin-<n> keeps the origin it was first given, whichever bowl track carries it later.
                origin = self._bin_origins.setdefault(entity_id, origin_for(t_ms, zone, self.shift_start_ms))
                return Identity(entity_id, "bin", origin)

        self._plate_count += 1
        return Identity(f"plate-{self._plate_count}", "plate", self._origin(BOWL, t_ms, zone))
