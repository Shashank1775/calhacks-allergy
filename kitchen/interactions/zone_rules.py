"""Hand-written zone-rule classifier: the always-available InteractionClassifier named zone_rules.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: PairWindow.features (a window of interaction_features rows); never the clip
Emits: list of (action, probability); constants from InteractionSettings, encoded in version
Rules: 7 always available, the fallback for everything else; 6 reads only allergen-blind features; 1 a wash needs the
       wash zone, a long dwell and a high probability, and only a self pair (a_id == b_id) can be a wash

The rules, newest feature row first:
- clean.wash: a self pair (a tool or hand paired with itself) in a wash zone for wash_min_dwell_ms.
- transfer.scoop: a tool overlapping a bin, inside that bin's slot, for scoop_min_ms.
- transfer.contact: any pair overlapping for contact_min_ms. A scoop is a contact too, so both are returned: the scoop
  moves food bin to tool, and the contact lets a dirty tool dirty the bin (allergens move both ways).
- unimportant otherwise: hovering near, or passing over without staying.
"""

from collections.abc import Sequence

from kitchen.interactions.candidates import WASHABLE_KINDS
from kitchen.interactions.settings import InteractionSettings, settings_digest
from kitchen.shared.interfaces import PairWindow
from kitchen.shared.models import Action, InteractionFeatures

# Bumped when a rule changes; the settings digest after it changes whenever a constant does.
RULES_VERSION = "1.0.0"

NOTHING: list[tuple[Action, float]] = [("unimportant", 1.0)]


def overlap_ms(rows: Sequence[InteractionFeatures], min_overlap: float) -> int:
    """How long the pair has been overlapping, up to the newest row: 0 unless the newest row overlaps.

    Measured from the first overlapping row in the window, so one jittery frame doesn't restart the count, and in
    milliseconds, so it means the same at any FPS.
    """
    if not rows or rows[-1]["overlap"] < min_overlap:
        return 0
    first = next(row for row in rows if row["overlap"] >= min_overlap)
    return rows[-1]["t_ms"] - first["t_ms"]


class ZoneRules:
    """The zone-rule InteractionClassifier. Pure: the same window always gets the same labels."""

    name = "zone_rules"

    def __init__(self, settings: InteractionSettings | None = None) -> None:
        self.settings = settings or InteractionSettings()
        self.version = f"{RULES_VERSION}-{settings_digest(self.settings)}"

    def classify(self, window: PairWindow) -> list[tuple[Action, float]]:
        """Label one pair window. Returns [("unimportant", 1.0)] when no rule fires."""
        rows = window.features
        if not rows:
            return NOTHING
        if window.a_id == window.b_id:
            return self.wash(rows[-1])
        return self.transfers(rows) or NOTHING

    def wash(self, latest: InteractionFeatures) -> list[tuple[Action, float]]:
        """A tool or hand that has stayed in the wash zone long enough was washed. A brief pass is nothing."""
        s = self.settings
        washable = latest["a_kind"] in WASHABLE_KINDS
        if washable and latest["zone_kind"] == "wash" and latest["dwell_ms"] >= s.wash_min_dwell_ms:
            return [("clean.wash", s.wash_probability)]
        return NOTHING

    def transfers(self, rows: Sequence[InteractionFeatures]) -> list[tuple[Action, float]]:
        """Scoop and contact for a two-entity pair; empty when neither fires."""
        s = self.settings
        latest = rows[-1]
        overlapping_ms = overlap_ms(rows, s.contact_min_overlap)
        labels: list[tuple[Action, float]] = []

        in_own_bin_slot = latest["inside_zone"] and latest["zone_kind"] == "bin_slot"
        if latest["a_kind"] == "tool" and latest["b_kind"] == "bin" and in_own_bin_slot:
            if overlapping_ms >= s.scoop_min_ms:
                labels.append(("transfer.scoop", s.scoop_probability))

        if overlapping_ms >= s.contact_min_ms:
            labels.append(("transfer.contact", s.contact_probability))
        return labels
