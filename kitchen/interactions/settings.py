"""Every constant of the interactions step in one settings object, loaded from INTERACTIONS_* environment variables.

Owner: partner (written by an agent at the owner's request)
Step:  Interactions (candidate pairs, features, zone rules)
Reads: INTERACTIONS_* environment variables (kitchen.shared.config.load_settings)
Emits: InteractionSettings, load_interaction_settings(), settings_digest()
Rules: 1 every safety-relevant constant lives here, is logged at startup, and is encoded in the zone-rules version, so
       an edge always says which constants made it

Distances are in normalized frame widths and times in milliseconds, never in frames, so the same constants work at
15, 30 or 60 FPS.
"""

import dataclasses
import hashlib
import json
from dataclasses import dataclass

from kitchen.shared.config import load_settings, log_settings


@dataclass(frozen=True)
class InteractionSettings:
    """The interactions step's constants. Each comment says why the default is what it is."""

    # Candidate pairs. Boxes whose edges are this close (or overlapping) are worth judging. Wider than any contact
    # rule needs, so a window already holds the approach when the boxes meet.
    candidate_max_gap: float = 0.05
    # A wrist this close to a pair counts as near it (wrist_proximity); farther is null.
    wrist_near: float = 0.15

    # Features. Speed is averaged over this much time, so it doesn't grow with FPS from box jitter.
    speed_lookback_ms: int = 200
    # A pair missing from candidates for up to this long (a detector miss, a hand in the way) counts as still there:
    # its overlap and dwell go on through the gap.
    pair_gap_ms: int = 300
    # Vision keeps an entity's ID through a loss of up to its track_lost_ms (1000). A pair keeps its window, overlap
    # history and dwell through a loss this long while one of its entities is out of view (a pair seen apart for
    # longer than pair_gap_ms starts over), and a tool or hand in the wash zone (its self pair) keeps its open wash: a
    # grab confirmed after a hand was lost still shows it began before, and one rinse split in two would clean twice.
    # Time unseen for longer than pair_gap_ms is bridged but counts as neither overlap, dwell nor rinsing.
    track_gap_ms: int = 1000

    # Pair windows and clips (AGENTS.md, Open questions: clip geometry: 16 steps over about 1 s, 128 x 128).
    window_ms: int = 1000
    clip_steps: int = 16
    clip_size: int = 128

    # Zone rules: contact. Overlap is intersection over the smaller box. Kept low so a knife tip on a plate's rim
    # counts; a pass-over is told apart by time, not by overlap.
    contact_min_overlap: float = 0.1
    # How long the pair must keep overlapping, counted from one frame before the overlap was first seen (when it may
    # have begun), so a touch measures the same at 10 or 60 FPS. A tool passing over a plate overlaps for about a
    # tenth of a second; a tap that touches lasts longer. Short on purpose: a false contact only holds a plate.
    contact_min_ms: int = 200
    contact_probability: float = 0.8

    # Zone rules: scoop. A tool overlapping a bin while inside that bin's slot, for this long.
    scoop_min_ms: int = 300
    scoop_probability: float = 0.9

    # Zone rules: wash. A tool or hand must stay in the wash zone this long, and also this long after the last transfer
    # that touched it (events.py), so a touch during the soak isn't washed away by a wash moments later. Rinsing a
    # knife takes several seconds, and a false wash would clear real contamination, so a quick pass never counts.
    wash_min_dwell_ms: int = 3000
    # Leaves 5% of what was carried: enough to clear an allergy ticket after a wash, never an anaphylactic one.
    wash_probability: float = 0.95

    # Classifier wrapper. A primary (the ConvLSTM, once adopted) slower than this is skipped for zone rules.
    primary_timeout_ms: int = 100

    # Events. A transfer label below this probability makes no edge. A wash below wash_floor makes no edge either:
    # a wash lowers risk, so it needs to be sure.
    event_floor: float = 0.5
    wash_floor: float = 0.9
    # Hits on the same pair and action this close together are one touch; a longer gap ends it. A touch is one edge,
    # written the frame its first hit is seen, so a plate carried to the pass by a dirty hand already has its edge
    # when it arrives. The graph counts every edge as another chance to move allergens, so a touch held for a few
    # seconds is still one chance; it writes again only to carry news across (events.py).
    merge_gap_ms: int = 300
    # After a failed database write, wait this long before trying the buffered events again.
    retry_ms: int = 250

    # The camera frame size when a recording doesn't say. Needed to measure distances in frame widths and to
    # letterbox boxes for clips. The Pi Camera Module 3 runs 16:9.
    frame_width: int = 1280
    frame_height: int = 720


def load_interaction_settings() -> InteractionSettings:
    """Load the settings from INTERACTIONS_* environment variables and log them, as every step does at startup."""
    settings = load_settings(InteractionSettings, prefix="INTERACTIONS_")
    log_settings(settings)
    return settings


def settings_digest(settings: InteractionSettings) -> str:
    """A short, stable fingerprint of every constant, so two runs with different constants never share a version."""
    values = json.dumps(dataclasses.asdict(settings), sort_keys=True)
    return hashlib.sha256(values.encode()).hexdigest()[:8]
