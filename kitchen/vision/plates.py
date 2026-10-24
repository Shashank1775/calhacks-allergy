"""Plate binding: when the cook taps "start plate", bind the one plate at the plating spot to that ticket.

Owner: vision (agents)
Step:  vision
Reads: the latest ObservationFrame, the zones, StateClient.get_plate_binding
Emits: StateClient.bind_plate; the bound plate_id, or BindRefused with the contract's 409 code
Rules: fail closed: refuse with 0 plates or 2+ plates at the spot, and never rebind a plate bound to another ticket;
       binding never guesses which plate was meant (contracts/apis.md, Vision HTTP API: POST /plates/start)
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


def start_plate(
    state: StateClient, observation: ObservationFrame | None, zones: list[Zone], ticket_id: str, t_ms: int
) -> str:
    """Bind the single plate at the plating spot to `ticket_id` at `t_ms` and return its plate_id.

    Raises BindRefused("no_plate_at_spot") with no frame yet or no plate there, BindRefused("multiple_plates_at_spot")
    with two or more, and BindRefused("plate_already_bound") if the plate belongs to another ticket. Starting the same
    plate for the same ticket again returns it without writing (the button may be tapped twice). StateUnavailable
    passes through: the HTTP layer reports it as an error, never as a guessed binding.
    """
    if observation is None:
        raise BindRefused("no_plate_at_spot", "no camera frame yet, so no plate can be seen at the plating spot")

    plates = plates_at_spot(observation, zones)
    if not plates:
        raise BindRefused("no_plate_at_spot", "no plate at the plating spot")
    if len(plates) > 1:
        # Which plate the cook meant is a guess, and a wrong guess judges a plate against another guest's ticket.
        raise BindRefused("multiple_plates_at_spot", f"{len(plates)} plates at the plating spot: {', '.join(plates)}")

    plate_id = plates[0]
    binding = state.get_plate_binding(plate_id)
    if binding is not None and binding.ticket_id != ticket_id:
        # A plate binds once: rebinding would judge food made for one ticket against another ticket's allergens.
        raise BindRefused("plate_already_bound", f"{plate_id} is already bound to ticket {binding.ticket_id}")
    if binding is not None:
        return plate_id

    state.bind_plate(plate_id, ticket_id, t_ms)
    return plate_id
