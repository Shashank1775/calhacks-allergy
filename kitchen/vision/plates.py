"""Plate binding: when the cook taps "start plate", bind the one plate at the plating spot to that ticket.

Owner: vision (agents)
Step:  vision
Reads: the recent ObservationFrames (VisionStatus keeps them), the zones, StateClient.get_plate_binding
Emits: StateClient.bind_plate; the bound plate_id, or BindRefused with the contract's 409 code
Rules: fail closed: refuse with no fresh frame (older than max_age_ms or stamped after the tap), 0 plates or 2+ plates
       at the spot (counting a plate the detector missed in the latest frame), and never rebind a plate bound to
       another ticket; binding never guesses which plate was meant (contracts/apis.md, Vision HTTP API:
       POST /plates/start)
"""

from kitchen.shared.interfaces import StateClient
from kitchen.shared.models import ObservationFrame, Zone
from kitchen.vision.common import BindRefused


def plates_at_spot(observation: ObservationFrame, zones: list[Zone]) -> list[str]:
    """The entity IDs of the plate detections in a plating_spot zone, one per detection.

    Detections are counted, not distinct IDs: two detections with one ID would be a tracking fault, and counting both
    makes start_plate refuse instead of picking one.
    """
    spot_ids = {zone.zone_id for zone in zones if zone.kind == "plating_spot"}
    return [
        detection["entity_id"]
        for detection in observation["detections"]
        if detection["kind"] == "plate" and detection.get("zone") in spot_ids
    ]


def plates_last_seen_at_spot(observations: list[ObservationFrame], zones: list[Zone]) -> set[str]:
    """The plates whose latest sighting in `observations` (oldest first) was in a plating_spot zone.

    A plate the detector missed in the newest frames is still on the spot as far as anyone knows, while one seen
    elsewhere since has left it.
    """
    spot_ids = {zone.zone_id for zone in zones if zone.kind == "plating_spot"}
    last_zone = {}
    for observation in observations:
        for detection in observation["detections"]:
            if detection["kind"] == "plate":
                last_zone[detection["entity_id"]] = detection.get("zone")
    return {plate_id for plate_id, zone in last_zone.items() if zone in spot_ids}


def start_plate(
    state: StateClient,
    observations: list[ObservationFrame],
    zones: list[Zone],
    ticket_id: str,
    t_ms: int,
    max_age_ms: int,
    recent_ms: int,
) -> str:
    """Bind the single plate at the plating spot to `ticket_id` at `t_ms` and return its plate_id.

    `observations` are the recent frames, oldest first. The latest must be at most `max_age_ms` older than `t_ms` and
    not newer than it, and show exactly one plate at the spot; no other plate may have been last seen there within
    `recent_ms` of it (the tracker's lost buffer), since a plate missed for a frame is still a second plate.
    Raises BindRefused("no_plate_at_spot") with no frame yet, a stale or future frame, or no plate there,
    BindRefused("multiple_plates_at_spot") with two or more, and BindRefused("plate_already_bound") if the plate belongs
    to another ticket. Starting the same plate for the same ticket again returns it without writing (the button may be
    tapped twice). StateUnavailable passes through: the HTTP layer reports it as an error, never as a guessed binding.
    """
    if not observations:
        raise BindRefused("no_plate_at_spot", "no camera frame yet, so no plate can be seen at the plating spot")
    observation = observations[-1]
    age_ms = t_ms - observation["t_ms"]
    if age_ms < 0 or age_ms > max_age_ms:
        # A stalled camera's last frame may show a plate that has since been swapped, and a frame stamped after the tap
        # means the clocks disagree: either way the plate on the spot now is unknown (fail closed).
        raise BindRefused(
            "no_plate_at_spot",
            f"the latest camera frame (t_ms={observation['t_ms']}) is {age_ms} ms from the tap; the limit is "
            f"0..{max_age_ms} ms, so the plate at the plating spot now isn't known",
        )

    plates = plates_at_spot(observation, zones)
    if not plates:
        raise BindRefused("no_plate_at_spot", "no plate at the plating spot")
    if len(plates) > 1:
        # Which plate the cook meant is a guess, and a wrong guess judges a plate against another guest's ticket.
        raise BindRefused("multiple_plates_at_spot", f"{len(plates)} plates at the plating spot: {', '.join(plates)}")

    plate_id = plates[0]
    recent = [frame for frame in observations if frame["t_ms"] >= observation["t_ms"] - recent_ms]
    others = sorted(plates_last_seen_at_spot(recent, zones) - {plate_id})
    if others:
        # The detector can miss a plate for a frame or two while its track lives on: it is still on the spot.
        raise BindRefused(
            "multiple_plates_at_spot",
            f"{plate_id} is at the plating spot, and {', '.join(others)} was there moments ago and hasn't been seen to "
            "leave",
        )
    binding = state.get_plate_binding(plate_id)
    if binding is not None and binding.ticket_id != ticket_id:
        # A plate binds once: rebinding would judge food made for one ticket against another ticket's allergens.
        raise BindRefused("plate_already_bound", f"{plate_id} is already bound to ticket {binding.ticket_id}")
    if binding is not None:
        return plate_id

    state.bind_plate(plate_id, ticket_id, t_ms)
    return plate_id
