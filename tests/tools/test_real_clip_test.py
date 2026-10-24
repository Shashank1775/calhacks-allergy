"""Tests for tools/real_clip_test.py: labels, timing, detector geometry, tracking, clips and the baseline rule.

Owner: platform (agents)
Step:  ConvLSTM evaluation on real footage
Reads: tools/real_clip_test.py (loaded by path), kitchen.shared, contracts/schemas
Emits: nothing
Rules: blind classifier: real clips are valid interaction_clip records too; no detector or model weights needed here
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "real_clip_test.py"
spec = importlib.util.spec_from_file_location("real_clip_test", TOOL)
rct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rct)

HEADER = "event,video,t_s,kind,actor,touched,moved,container_box,notes\n"


def write_sheet(tmp_path: Path, rows: str) -> Path:
    sheet = tmp_path / "labels.csv"
    sheet.write_text(HEADER + rows)
    return sheet


def test_labels_parse_and_skip_blank_rows(tmp_path):
    sheet = write_sheet(
        tmp_path, "s01,take1.mp4,12.4,scoop,tool,y,y,,\n,,,,,,,,\nh01,take1.mp4,3,hover,hand,n,n,0.1 0.2 0.3 0.4,odd\n"
    )
    first, second = rct.read_labels(sheet)
    assert (first.event, first.t_s, first.touched, first.moved, first.container_box) == ("s01", 12.4, True, True, None)
    assert second.container_box == (0.1, 0.2, 0.3, 0.4) and second.actor == "hand" and not second.touched


@pytest.mark.parametrize(
    ("row", "complaint"),
    [
        ("x1,t.mp4,1,scooping,tool,y,y,,\n", "kind"),
        ("x1,t.mp4,1,scoop,spoon,y,y,,\n", "actor"),
        ("x1,t.mp4,1,scoop,tool,yes,y,,\n", "touched"),
        ("x1,t.mp4,1,scoop,tool,n,y,,\n", "only move"),
        ("x1,t.mp4,1,scoop,tool,y,y,0.1 0.2,\n", "4 numbers"),
    ],
)
def test_bad_label_rows_name_the_event_and_the_problem(tmp_path, row, complaint):
    with pytest.raises(ValueError, match=complaint):
        rct.read_labels(write_sheet(tmp_path, row))


def test_action_follows_what_happened_not_the_kind_name():
    label = rct.Label("e", "v", 1.0, "hover", "tool", True, False, None, "")
    assert rct.action_for(label) == "transfer.contact"
    assert rct.action_for(label.__class__(**{**label.__dict__, "kind": "wipe", "moved": True})) == "transfer.wipe"
    assert rct.action_for(label.__class__(**{**label.__dict__, "touched": False})) == "unimportant"


def test_the_labeled_moment_lands_on_the_touch_step_with_even_gaps():
    at_30 = rct.frame_indices(10.0, 30)
    assert len(at_30) == rct.STEPS + 1 and at_30[rct.TOUCH_STEP] == 300
    assert set(np.diff(at_30)) == {2}  # every 2nd frame: 67 ms, never a mix of 33 and 67
    assert set(np.diff(rct.frame_indices(10.0, 60))) == {4}
    assert set(np.diff(rct.frame_indices(10.0, 16))) == {1}


def test_detector_coordinates_map_back_to_the_source_frame():
    """A 1280x720 frame sits in the 640 square at scale 0.5 with 140 px of padding on top."""
    assert rct.detector_input_layout(1280, 720, 640) == (0.5, 0, 140)
    row = np.array([320.0, 140.0, 640.0, 320.0, 0.9, 45.0])
    assert rct.box_to_source(row, 1280, 720, 640) == pytest.approx((0.5, 0.0, 1.0, 0.5))


def test_hand_box_sits_past_the_wrist_away_from_the_elbow():
    wrist, elbow = np.array([0.5, 0.5]), np.array([0.5, 0.3])  # forearm points down the frame
    x1, y1, x2, y2 = rct.hand_box(wrist, elbow, 1000, 1000)
    assert (x1 + x2) / 2 == pytest.approx(0.5)
    assert (y1 + y2) / 2 > 0.5  # centered below the wrist, on the far side from the elbow


def test_tracking_follows_the_nearest_box_and_gives_up_on_big_jumps():
    per_frame = [[(0.1, 0.1, 0.2, 0.2)], [(0.12, 0.1, 0.22, 0.2), (0.8, 0.8, 0.9, 0.9)], [(0.8, 0.8, 0.9, 0.9)], []]
    boxes = rct.track(per_frame, 0, (0.1, 0.1, 0.2, 0.2), aspect=1.0)
    assert boxes[1] == (0.12, 0.1, 0.22, 0.2)  # the near one, not the far one
    assert boxes[2] is None and boxes[3] is None  # the only box left is too far to be the same object


def test_containers_hold_their_last_box_through_short_misses():
    per_frame = [[(0.4, 0.4, 0.6, 0.6)], [], [], [], [], [(0.4, 0.4, 0.6, 0.6)]]
    boxes = rct.track(per_frame, 0, (0.4, 0.4, 0.6, 0.6), aspect=1.0, hold=3)
    assert boxes[1:4] == [(0.4, 0.4, 0.6, 0.6)] * 3 and boxes[4] is None and boxes[5] == (0.4, 0.4, 0.6, 0.6)


def test_pick_pair_tracks_the_tool_next_to_the_container():
    label = rct.Label("e", "v", 1.0, "scoop", "tool", True, True, None, "")
    near_tool, far_tool = (0.45, 0.3, 0.5, 0.45), (0.9, 0.9, 0.95, 0.98)
    bowl = (0.4, 0.4, 0.6, 0.6)
    found = [{"tool": [near_tool, far_tool], "hand": [], "container": [bowl]} for _ in range(rct.STEPS + 1)]
    a_boxes, b_boxes = rct.pick_pair(found, label, aspect=1.0)
    assert a_boxes[rct.TOUCH_STEP] == near_tool and b_boxes[rct.TOUCH_STEP] == bowl


def test_pick_pair_gives_up_without_a_container():
    label = rct.Label("e", "v", 1.0, "scoop", "tool", True, True, None, "")
    found = [{"tool": [(0.1, 0.1, 0.2, 0.2)], "hand": [], "container": []} for _ in range(rct.STEPS + 1)]
    assert rct.pick_pair(found, label, aspect=1.0) is None


@pytest.mark.safety
def test_real_event_clip_is_a_valid_interaction_clip_with_boxes_on_their_change():
    """Blind classifier: a real clip validates like a sim one, and a wide frame's box covers its own change."""
    label = rct.Label("e1", "v", 1.0, "scoop", "tool", True, True, None, "")
    frames, t_ms = [], []
    a_boxes, b_boxes = [], []
    for f in range(rct.STEPS + 1):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        left = 400 + 20 * f  # a bright square slides right
        frame[50:150, left : left + 100] = 255  # near the top, where a stretched box would miss it
        frames.append(frame)
        t_ms.append(1000 + 62 * f)
        a_boxes.append((left / 1280, 50 / 720, (left + 100) / 1280, 150 / 720))
        b_boxes.append((0.3, 0.3, 0.7, 0.7))
    record, tensor = rct.event_clip(label, frames, t_ms, (1280, 720), a_boxes, b_boxes)
    assert record["labels"] == {
        "contact": True,
        "transferred": True,
        "action": "transfer.scoop",
        "label_source": "human",
    }
    assert record["source"] == "recording" and tensor.shape == (rct.STEPS, 3, 128, 128)
    changed = tensor[5, 2] > 128
    boxes = (tensor[5, 0] > 0) | (tensor[4, 0] > 0)  # A now and a step before
    # The change mask is blurred, so allow a 2-pixel margin; a wrong letterbox would be off by dozens of pixels.
    padded = np.pad(boxes, 2)
    near_boxes = np.zeros_like(boxes)
    for dy in range(5):
        for dx in range(5):
            near_boxes |= padded[dy : dy + boxes.shape[0], dx : dx + boxes.shape[1]]
    assert changed.any() and (changed <= near_boxes).all()


def test_overlap_rule():
    tensor = np.zeros((rct.STEPS, 3, 8, 8), dtype=np.uint8)
    tensor[:, 1, 2:6, 2:6] = 255  # B
    assert rct.overlap_rule(tensor) == (False, False)
    tensor[3:, 0, 1:3, 1:3] = 255  # A overlaps B's corner from step 3
    assert rct.overlap_rule(tensor) == (True, False)
    tensor[3:, 2, 4:6, 4:6] = 255  # and B's middle keeps changing
    assert rct.overlap_rule(tensor) == (True, True)
