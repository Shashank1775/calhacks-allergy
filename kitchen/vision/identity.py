"""Identity: turns a track into a stable entity ID and origin (bin-<n>, tool-<class>-<n>, plate-<n>, hand-p<k>-left|right),
fixed for the entity's life.

Owner: vision (agents)
Step:  vision
Reads: track IDs and classes from tracking.py, the zone each track is first seen in, whether a track of the same group
       went unmatched that frame, the shift start time and the lost buffer (track_lost_ms)
Emits: Identity (entity_id, kind, origin) for every tracked object and hand
Rules: known-clean source: a new track is a new entity, so a re-acquired object, person or hand gets a new ID; origin
       is shift_start only up to shift start, clean_stack only for a tool or plate first seen in a clean_stack zone (a
       hand never comes out of a stack), otherwise unknown; numbers are never reused

A new track can only start clean when it can't be an object already seen. Two cases say it might be:
    - an ID switch: a track of the same group went unmatched in the frame the new track started (a knife carried fast
      into the rack, or covered by a hand there and uncovered a little to one side, comes back as a new track while its
      old one is still lost). The newcomer is then unknown, not shift_start or clean_stack. Bowls and plates are one
      group (tracking.py), since a detector may call one plate a plate in one frame and a bowl in the next;
    - a loss inside a clean stack: when a tool or plate track ends while in a clean_stack zone, the object may still be
      there, hidden. So the next new track of that class first seen in a clean stack is unknown, once for each loss.
      Plates share one loss count, whether seen as plates or as bowls.
A hand is the same: a wrist unseen for longer than the lost buffer may have touched anything out of sight, so it comes
back as a new hand, hand-p<k>-<side>-<n>, with unknown history. Every judgment call leans toward HOLD: a genuinely
clean item caught by one of these holds once, an annoyance, not a risk. What no tracker can catch is an object hidden
for longer than the lost buffer while it is carried into a clean stack (see README.md, Watch out for).

Strict mode (VISION_TRACKER=motion, experimental) adds two guards. The clean-stack loss count is shared by all tool
classes, since the detector may call the same utensil a knife and later a spoon. And reacquire_hands() lets the tracker
start a person's hands over when pose swaps left and right.

The one exception is bins. Orders keys bin profiles by bin-<n>, so a bowl first seen in the bin slot zone slot-<n> is
bin-<n>, and a bowl re-acquired in slot-<n> is bin-<n> again, keeping the origin bin-<n> was first given. Bins are
identified by slot, so swapping bins between slots mid-shift is unsupported. While a track holds bin-<n>, another new
bowl in slot-<n> is a plate: two tracks never share one entity ID. Only a bowl can be a bin: a detector with a plate
class (a custom bundle's class_names, detect.py) makes every plate detection a plate, even in a bin slot.
"""

import re
from dataclasses import dataclass

from kitchen.shared.models import Zone

TOOL_CLASSES = ("knife", "spoon", "fork")
BOWL = "bowl"
PLATE = "plate"
# The detector classes a plate can be seen as. A stock COCO detector knows only bowls (a bowl outside a bin slot is a
# plate); a fine-tuned one also has plates, and may call one plate either.
DISH_CLASSES = (BOWL, PLATE)
# Every detector class with an entity kind. A detector's other classes are dropped (detect.py), never relabeled.
OBJECT_CLASSES = (*TOOL_CLASSES, *DISH_CLASSES)
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

    def __init__(self, shift_start_ms: int, lost_ms: int, strict: bool = False):
        self.shift_start_ms = shift_start_ms
        self.lost_ms = lost_ms
        self.strict = strict
        self._by_track: dict[int, Identity] = {}
        self._tool_counts = dict.fromkeys(TOOL_CLASSES, 0)
        self._plate_count = 0
        self._person_count = 0
        self._person_by_track: dict[int, int] = {}
        self._hands: dict[tuple[int, str], Identity] = {}
        self._hand_seen_ms: dict[tuple[int, str], int] = {}
        self._hand_sightings: dict[tuple[int, str], int] = {}
        self._bin_holders: dict[str, int] = {}
        self._bin_origins: dict[str, str] = {}
        # Per detector class (strict: all tools share one count): tracks that ended inside a clean stack and may still
        # be hiding there.
        self._lost_in_clean_stack: dict[str, int] = {}
        self._class_by_track: dict[int, str] = {}
        self._zone_by_track: dict[int, Zone | None] = {}

    def object_identity(
        self, track_id: int, class_name: str, t_ms: int, zone: Zone | None, maybe_switched: bool
    ) -> Identity:
        """The identity of an object track, made the first time the track is seen and fixed after that.

        `zone` is where the track's box centre is in this frame. It sets the identity on the track's first frame, and
        the latest one is remembered so a track that ends inside a clean stack is noticed. `maybe_switched` says a
        track of this class went unmatched this frame, so a new track may be that object under a new ID.
        """
        self._zone_by_track[track_id] = zone
        known = self._by_track.get(track_id)
        if known is not None:
            return known
        self._class_by_track[track_id] = class_name

        if class_name in TOOL_CLASSES:
            identity = self._new_tool(class_name, t_ms, zone, maybe_switched)
        elif class_name == BOWL:
            identity = self._new_bowl(track_id, t_ms, zone, maybe_switched)
        elif class_name == PLATE:
            identity = self._new_plate(t_ms, zone, maybe_switched)
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

    def hand_identity(self, person_track_id: int, side: str, t_ms: int, maybe_switched: bool) -> Identity:
        """The identity of one hand of a pose track at a confident sighting of its wrist at `t_ms`.

        A hand is made the first time its wrist is seen, and its origin comes from that sighting, not from when the
        person was first seen: a hand that only appears after shift start has unknown history (fail closed). A wrist
        unseen for longer than lost_ms may have touched anything out of sight, so its next sighting makes a new hand,
        hand-p<k>-<side>-<n>, with unknown history, as a re-acquired track gets (AGENTS.md safety rule 8).
        `maybe_switched` says a pose track went unmatched this frame, so this person may be one already seen under a
        new track ID: their hands then have unknown history even at shift start. The hand's zone is not asked: hands
        never come out of a clean stack, so a hand first seen reaching into one is still unknown.
        """
        if side not in HAND_SIDES:
            raise ValueError(f"no hand side {side!r}")

        key = (person_track_id, side)
        known = self._hands.get(key)
        last_seen_ms = self._hand_seen_ms.get(key)
        self._hand_seen_ms[key] = t_ms
        if known is not None and t_ms - last_seen_ms <= self.lost_ms:
            return known

        person = self.person_label(person_track_id)
        sightings = self._hand_sightings.get(key, 0) + 1
        self._hand_sightings[key] = sightings
        if sightings == 1:
            entity_id = f"hand-{person}-{side}"
            clean = t_ms <= self.shift_start_ms and not maybe_switched
        else:
            entity_id = f"hand-{person}-{side}-{sightings}"
            clean = False
        identity = Identity(entity_id, "hand", "shift_start" if clean else "unknown")
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
            key = self._loss_key(class_name)
            self._lost_in_clean_stack[key] = self._lost_in_clean_stack.get(key, 0) + 1

        if track_id in self._person_by_track:
            del self._person_by_track[track_id]
            for side in HAND_SIDES:
                self._hands.pop((track_id, side), None)
                self._hand_seen_ms.pop((track_id, side), None)
                self._hand_sightings.pop((track_id, side), None)

    def _origin(self, class_name: str, t_ms: int, zone: Zone | None, maybe_switched: bool) -> str:
        """origin_for(), except that a newcomer that may be an object already seen is unknown (see the module docstring):
        one that started while a track of its class went unmatched, or one first seen in a clean stack where an object
        of its class went missing."""
        origin = origin_for(t_ms, zone, self.shift_start_ms)
        if origin == "unknown":
            return origin
        if maybe_switched:
            return "unknown"
        key = self._loss_key(class_name)
        if origin == "clean_stack" and self._lost_in_clean_stack.get(key, 0) > 0:
            self._lost_in_clean_stack[key] -= 1
            return "unknown"
        return origin

    def _loss_key(self, class_name: str) -> str:
        """Which clean-stack loss count a class uses: its own, except that plates share one whether seen as plates or
        bowls (a bin never counts a loss), and in strict mode all tool classes share one."""
        if class_name in DISH_CLASSES:
            return PLATE
        if self.strict and class_name in TOOL_CLASSES:
            return "tool"
        return class_name

    def reacquire_hands(self, person_track_id: int) -> None:
        """Forget both hands of a pose track, so the next sighting of each is a new hand with unknown history.

        The tracker calls this when pose seems to have swapped the person's left and right wrists: carrying on would
        give each hand the other's history (possibly a clean one).
        """
        for side in HAND_SIDES:
            self._hands.pop((person_track_id, side), None)

    def _new_tool(self, class_name: str, t_ms: int, zone: Zone | None, maybe_switched: bool) -> Identity:
        """A new tool entity: the next number for its class."""
        self._tool_counts[class_name] += 1
        entity_id = f"tool-{class_name}-{self._tool_counts[class_name]}"
        return Identity(entity_id, "tool", self._origin(class_name, t_ms, zone, maybe_switched))

    def _new_bowl(self, track_id: int, t_ms: int, zone: Zone | None, maybe_switched: bool) -> Identity:
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

        return self._new_plate(t_ms, zone, maybe_switched)

    def _new_plate(self, t_ms: int, zone: Zone | None, maybe_switched: bool) -> Identity:
        """A new plate entity: the next plate number, from one count for plates seen as plates or as bowls."""
        self._plate_count += 1
        return Identity(f"plate-{self._plate_count}", "plate", self._origin(PLATE, t_ms, zone, maybe_switched))
