"""Tests for zones: the file format and its validator, point-in-zone lookup, saving, and pushing to the database.

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.zones, tests/fixtures/zones.sample.json, kitchen/vision/default_zones.json, FakeStateClient
Emits: nothing
Rules: fail closed (a point in no zone gets None; bad files are refused; a zone change applies whole or not at all);
       known-clean source depends on the clean_stack zone being found exactly, so overlaps must resolve the same way
       every time
"""

import json
import random
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kitchen.shared.fakes import FakeStateClient
from kitchen.shared.interfaces import StateUnavailable
from kitchen.shared.models import Zone
from kitchen.vision import zones as zones_module
from kitchen.vision.zones import (
    DEFAULT_ZONES_PATH,
    SAVED_ZONES_PATH,
    LiveZones,
    ZoneFileError,
    add_zone,
    load_zones,
    main,
    parse_zones,
    pixel_to_point,
    point_in_polygon,
    polygon_area,
    push_zones,
    save_zones,
    zone_lookup,
    zone_record,
    zones_files,
)

SAMPLE = Path(__file__).parent / "fixtures" / "zones.sample.json"


def rect(x1, y1, x2, y2):
    """A rectangle polygon, corners clockwise from the top left."""
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def zone_entry(zone_id="slot-1", kind="bin_slot", polygon=None):
    """One zones-file entry as parsed JSON."""
    return {"zone_id": zone_id, "kind": kind, "polygon": polygon or [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]}


unit = st.floats(min_value=0, max_value=1, allow_nan=False)


# the sample file


def test_sample_file_has_five_slots_and_one_of_each_station():
    zones = load_zones(SAMPLE)
    kinds = [zone.kind for zone in zones]
    assert [zone.zone_id for zone in zones if zone.kind == "bin_slot"] == [f"slot-{n}" for n in range(1, 6)]
    for kind in ("plating_spot", "wash", "clean_stack", "pass"):
        assert kinds.count(kind) == 1


def test_sample_zones_do_not_overlap():
    zones = load_zones(SAMPLE)
    for x in range(101):
        for y in range(101):
            point = (x / 100, y / 100)
            assert sum(point_in_polygon(point, zone.polygon) for zone in zones) <= 1, point


# the validator


@pytest.mark.safety
@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        (zone_entry(kind="fridge"), "kind"),
        (zone_entry(kind="clean_stack ", zone_id="rack"), "kind"),
        (zone_entry(polygon=[[0.1, 0.1], [1.2, 0.1], [0.2, 0.2]]), "outside"),
        (zone_entry(polygon=[[0.1, -0.01], [0.2, 0.1], [0.2, 0.2]]), "outside"),
        (zone_entry(polygon=[[0.1, 0.1], [0.2, 0.2]]), "at least 3"),
        (zone_entry(polygon=[[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]), "no area"),
        (zone_entry(polygon=[[0.1, 0.1], [0.5, 0.1], [0.1, 0.3], [0.6, 0.4]]), "edges cross"),
        (zone_entry(polygon=[[0.1, 0.1], [0.3, 0.1], [0.1, 0.3], [0.3, 0.3]]), "edges cross"),
        (zone_entry(polygon=[[0.1, 0.1], [0.3, 0.1], [0.2, 0.1], [0.2, 0.3]]), "edges cross"),
        (zone_entry(polygon=[[0.1, 0.1], [0.2, 0.1], [0.2, 0.1], [0.2, 0.2]]), "twice"),
        (zone_entry(zone_id="bin-1"), "slot-<n>"),
        (zone_entry(zone_id="slot-01"), "slot-<n>"),
        (zone_entry(zone_id="slot-a"), "slot-<n>"),
        (zone_entry(zone_id="", kind="other"), "non-empty"),
        (zone_entry(polygon=[[0.1, 0.1], [0.2, True], [0.2, 0.2]]), "number pairs"),
        (zone_entry(polygon=[[0.1, 0.1], [0.2], [0.2, 0.2]]), "number pairs"),
        ({**zone_entry(), "colour": "red"}, "exactly the fields"),
        ({"zone_id": "slot-1", "kind": "bin_slot"}, "exactly the fields"),
    ],
)
def test_validator_rejects_a_bad_zone(entry, problem):
    with pytest.raises(ZoneFileError, match=problem):
        parse_zones({"zones": [entry]})


@pytest.mark.safety
def test_validator_rejects_duplicate_zone_ids():
    with pytest.raises(ZoneFileError, match="more than once"):
        parse_zones({"zones": [zone_entry(), zone_entry()]})


@pytest.mark.safety
@pytest.mark.parametrize("data", [[], {"zone": []}, {"zones": {}}, {"zones": [], "extra": 1}])
def test_validator_rejects_a_bad_top_level(data):
    with pytest.raises(ZoneFileError, match='"zones"'):
        parse_zones(data)


def test_validator_accepts_every_kind_and_named_slots():
    entries = [zone_entry(zone_id="slot-12")] + [
        zone_entry(zone_id=kind, kind=kind) for kind in ("plating_spot", "wash", "clean_stack", "pass", "other")
    ]
    assert [zone.zone_id for zone in parse_zones({"zones": entries})][0] == "slot-12"


@pytest.mark.safety
def test_load_names_the_file_when_it_is_bad(tmp_path):
    bad = tmp_path / "zones.json"
    bad.write_text(json.dumps({"zones": [zone_entry(kind="fridge")]}))
    with pytest.raises(ZoneFileError, match="zones.json"):
        load_zones(bad)


@pytest.mark.safety
@pytest.mark.parametrize(
    "content",
    [None, b"{not json", b"\xff\xfe\x00garbage", '{"zones": []}'.encode("utf-16")],
    ids=["missing", "not-json", "not-utf8", "utf16"],
)
def test_load_refuses_a_missing_or_broken_file(tmp_path, content):
    path = tmp_path / "zones.json"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(ZoneFileError, match="zones.json: can't read"):
        load_zones(path)


# saving


def test_save_then_load_gives_the_same_zones(tmp_path):
    zones = load_zones(SAMPLE)
    out = tmp_path / "zones.json"
    save_zones(out, zones)
    assert load_zones(out) == zones


@pytest.mark.safety
def test_save_refuses_bad_zones_and_writes_nothing(tmp_path):
    out = tmp_path / "zones.json"
    with pytest.raises(ZoneFileError):
        save_zones(
            out, [Zone("slot-1", "bin_slot", rect(0.1, 0.1, 0.2, 0.2)), Zone("slot-1", "wash", rect(0, 0, 1, 1))]
        )
    assert not out.exists()


# lookup


def test_lookup_finds_each_sample_zone_at_its_middle():
    zones = load_zones(SAMPLE)
    lookup = zone_lookup(zones)
    for zone in zones:
        xs = [x for x, _ in zone.polygon]
        ys = [y for _, y in zone.polygon]
        assert lookup(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)) == zone


@pytest.mark.safety
@pytest.mark.parametrize("point", [(0.5, 0.3), (0.0, 0.0), (1.0, 1.0), (0.19, 0.1), (0.5, 0.71)])
def test_a_point_outside_every_zone_gets_none(point):
    assert zone_lookup(load_zones(SAMPLE))(point) is None


@pytest.mark.safety
@pytest.mark.parametrize("point", [(float("nan"), 0.5), (0.5, float("nan")), (-0.1, 0.5), (1.5, 0.5)])
def test_an_impossible_point_gets_none(point):
    assert zone_lookup([Zone("all", "other", rect(0, 0, 1, 1))])(point) is None


@pytest.mark.safety
def test_no_zones_means_no_zone_for_any_point():
    assert zone_lookup([])((0.5, 0.5)) is None


@pytest.mark.parametrize(
    "point", [(0.35, 0.5), (0.65, 0.5), (0.5, 0.4), (0.5, 0.7), (0.35, 0.4), (0.65, 0.7)], ids=lambda p: str(p)
)
def test_a_point_on_an_edge_or_corner_is_in_the_zone(point):
    assert zone_lookup(load_zones(SAMPLE))(point).zone_id == "plating"


def test_a_point_just_outside_an_edge_is_not():
    assert zone_lookup(load_zones(SAMPLE))((0.349, 0.5)) is None


def test_a_concave_zone_excludes_its_notch():
    l_shape = Zone("ell", "other", [(0.1, 0.1), (0.5, 0.1), (0.5, 0.3), (0.3, 0.3), (0.3, 0.5), (0.1, 0.5)])
    lookup = zone_lookup([l_shape])
    assert lookup((0.2, 0.4)) == l_shape
    assert lookup((0.4, 0.2)) == l_shape
    assert lookup((0.4, 0.4)) is None


@pytest.mark.safety
def test_overlapping_zones_give_the_smallest():
    counter = Zone("counter", "other", rect(0.0, 0.0, 1.0, 1.0))
    rack = Zone("rack", "clean_stack", rect(0.6, 0.6, 0.8, 0.8))
    lookup = zone_lookup([counter, rack])
    assert lookup((0.7, 0.7)) == rack
    assert lookup((0.2, 0.2)) == counter


@pytest.mark.safety
def test_equal_overlapping_zones_break_ties_by_zone_id():
    b = Zone("b", "wash", rect(0.2, 0.2, 0.6, 0.6))
    a = Zone("a", "other", rect(0.4, 0.4, 0.8, 0.8))
    assert zone_lookup([b, a])((0.5, 0.5)) == a
    assert zone_lookup([a, b])((0.5, 0.5)) == a


@pytest.mark.safety
def test_a_shared_edge_resolves_the_same_way_every_time():
    left = Zone("left", "other", rect(0.0, 0.0, 0.5, 1.0))
    right = Zone("right", "other", rect(0.5, 0.0, 1.0, 1.0))
    assert zone_lookup([right, left])((0.5, 0.5)) == left


@pytest.mark.safety
def test_lookup_refuses_bad_zones():
    with pytest.raises(ZoneFileError):
        zone_lookup([Zone("slot-1", "bin_slot", [(0.1, 0.1), (0.2, 0.2)])])


@pytest.mark.safety
@given(point=st.tuples(unit, unit), seed=st.integers(0, 2**32 - 1))
def test_lookup_answer_never_depends_on_zone_order(point, seed):
    zones = load_zones(SAMPLE) + [
        Zone("counter", "other", rect(0.0, 0.0, 1.0, 1.0)),
        Zone("left-half", "other", rect(0.0, 0.0, 0.5, 1.0)),
        Zone("top-half", "other", rect(0.0, 0.0, 1.0, 0.5)),
    ]
    shuffled = zones[:]
    random.Random(seed).shuffle(shuffled)
    assert zone_lookup(zones)(point) == zone_lookup(shuffled)(point)


@pytest.mark.safety
@given(point=st.tuples(unit, unit))
def test_lookup_gives_a_zone_that_holds_the_point_and_none_smaller(point):
    zones = load_zones(SAMPLE) + [Zone("left-half", "other", rect(0.0, 0.0, 0.5, 1.0))]
    found = zone_lookup(zones)(point)
    holding = [zone for zone in zones if point_in_polygon(point, zone.polygon)]
    if found is None:
        assert holding == []
    else:
        assert found in holding
        assert polygon_area(found.polygon) == min(polygon_area(zone.polygon) for zone in holding)


# pushing to the database


def test_push_replaces_the_calibration():
    state = FakeStateClient()
    state.set_zones([Zone("old", "other", rect(0, 0, 1, 1))])
    zones = load_zones(SAMPLE)
    push_zones(state, zones)
    assert sorted(state.list_zones(), key=lambda zone: zone.zone_id) == sorted(zones, key=lambda zone: zone.zone_id)


@pytest.mark.safety
def test_push_refuses_bad_zones_before_writing():
    state = FakeStateClient()
    with pytest.raises(ZoneFileError):
        push_zones(state, [Zone("slot-1", "fridge", rect(0, 0, 1, 1))])
    assert state.list_zones() == []


@pytest.mark.safety
def test_push_passes_state_unavailable_through():
    state = FakeStateClient()
    state.set_unavailable()
    with pytest.raises(StateUnavailable):
        push_zones(state, load_zones(SAMPLE))


# the default layout and which zones file a run uses


def test_the_default_layout_is_the_shared_kitchen():
    """The layout every test and the dummy kitchen share: five bin slots across the top, the utensil rack and the
    clean plates on the right, the plating spot in the middle, wash bottom left, pass bottom right."""
    expected = {
        "slot-1": ("bin_slot", (0.02, 0.04, 0.16, 0.30)),
        "slot-2": ("bin_slot", (0.18, 0.04, 0.32, 0.30)),
        "slot-3": ("bin_slot", (0.34, 0.04, 0.48, 0.30)),
        "slot-4": ("bin_slot", (0.50, 0.04, 0.64, 0.30)),
        "slot-5": ("bin_slot", (0.66, 0.04, 0.80, 0.30)),
        "rack": ("clean_stack", (0.82, 0.04, 0.99, 0.36)),
        "plate-stack": ("clean_stack", (0.82, 0.40, 0.99, 0.74)),
        "plating": ("plating_spot", (0.40, 0.40, 0.60, 0.74)),
        "wash": ("wash", (0.02, 0.72, 0.18, 0.98)),
        "pass": ("pass", (0.70, 0.78, 0.99, 0.99)),
    }
    zones = load_zones(DEFAULT_ZONES_PATH)
    assert {zone.zone_id: (zone.kind, zone.polygon) for zone in zones} == {
        zone_id: (kind, rect(*corners)) for zone_id, (kind, corners) in expected.items()
    }


def test_the_default_layout_is_written_as_save_zones_writes_it(tmp_path):
    """So saving the default layout from the web app unchanged gives a byte-identical data/zones.json."""
    save_zones(tmp_path / "zones.json", load_zones(DEFAULT_ZONES_PATH))
    assert (tmp_path / "zones.json").read_text() == DEFAULT_ZONES_PATH.read_text()


def test_without_zones_a_run_uses_the_default_layout_and_saves_to_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert zones_files(None) == (DEFAULT_ZONES_PATH, SAVED_ZONES_PATH)


def test_zones_saved_from_the_web_app_come_before_the_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_zones(SAVED_ZONES_PATH, load_zones(SAMPLE))
    assert zones_files(None) == (SAVED_ZONES_PATH, SAVED_ZONES_PATH)


def test_an_explicit_zones_file_wins_and_is_where_changes_are_saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_zones(SAVED_ZONES_PATH, load_zones(SAMPLE))
    assert zones_files(tmp_path / "mine.json") == (tmp_path / "mine.json", tmp_path / "mine.json")


@pytest.mark.safety
def test_a_failed_save_leaves_the_old_file_whole(tmp_path, monkeypatch):
    """A save that fails partway must not leave half a file that fails to load at the next start."""
    path = tmp_path / "zones.json"
    save_zones(path, load_zones(SAMPLE))
    before = path.read_text()

    def failing_replace(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr(zones_module.os, "replace", failing_replace)
    with pytest.raises(OSError):
        save_zones(path, load_zones(DEFAULT_ZONES_PATH))
    assert path.read_text() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["zones.json"]


# replacing the zones while the camera runs (LiveZones, PUT /zones)


class LiveKitchen:
    """A LiveZones on the sample layout, a saved zones file, a FakeStateClient and a recorded lookup swap."""

    def __init__(self, save_path: Path):
        self.zones = load_zones(SAMPLE)
        self.state = FakeStateClient()
        push_zones(self.state, self.zones)
        save_zones(save_path, self.zones)
        self.save_path = save_path
        self.lookups = []
        self.live = LiveZones(self.zones, save_path, self.state, self.lookups.append)

    def assert_unchanged(self, file_text: str) -> None:
        """Nothing changed anywhere: the zones in use, the frame loop's lookup, the file and the zone table."""
        assert self.live.current() == self.zones
        assert self.lookups == []
        assert self.save_path.read_text() == file_text
        assert sorted(self.state.list_zones(), key=lambda zone: zone.zone_id) == sorted(
            self.zones, key=lambda zone: zone.zone_id
        )


def layout_body(zones: list[Zone]) -> dict:
    """A PUT /zones body for `zones`."""
    return {"zones": [zone_record(zone) for zone in zones]}


def test_a_good_layout_is_saved_used_from_the_next_frame_and_pushed(tmp_path):
    kitchen = LiveKitchen(tmp_path / "zones.json")
    new_layout = load_zones(DEFAULT_ZONES_PATH)

    assert kitchen.live.replace(layout_body(new_layout)) == new_layout

    assert kitchen.live.current() == new_layout
    assert load_zones(kitchen.save_path) == new_layout
    assert sorted(kitchen.state.list_zones(), key=lambda zone: zone.zone_id) == sorted(
        new_layout, key=lambda zone: zone.zone_id
    )
    [lookup] = kitchen.lookups
    assert lookup((0.85, 0.2)).zone_id == "rack"  # in no zone of the sample layout


BAD_LAYOUTS = [
    None,
    [],
    {"zones": "slot-1"},
    {"zones": [zone_entry(kind="fridge")]},
    {"zones": [zone_entry(), zone_entry()]},
    {"zones": [zone_entry(zone_id="bin-1")]},
    {"zones": [zone_entry(polygon=[[0.1, 0.1], [0.5, 0.5], [0.1, 0.5], [0.5, 0.1]])]},  # a bow-tie
    {"zones": [zone_entry(polygon=[[0.1, 0.1], [1.5, 0.1], [0.1, 0.5]])]},
    {"zones": [{**zone_entry(), "colour": "red"}]},
    {"zones": [zone_entry()], "version": 2},
]


@pytest.mark.safety
@pytest.mark.parametrize("data", BAD_LAYOUTS)
def test_a_bad_layout_changes_nothing(tmp_path, data):
    """Bad zones never half-apply: the lookup, the file and the zone table all keep the old layout."""
    kitchen = LiveKitchen(tmp_path / "zones.json")
    file_text = kitchen.save_path.read_text()
    with pytest.raises(ZoneFileError):
        kitchen.live.replace(data)
    kitchen.assert_unchanged(file_text)


@pytest.mark.safety
def test_a_layout_that_cant_be_saved_changes_nothing(tmp_path):
    (tmp_path / "not-a-folder").write_text("")
    kitchen = LiveKitchen(tmp_path / "zones.json")
    file_text = kitchen.save_path.read_text()
    kitchen.live.save_path = tmp_path / "not-a-folder" / "zones.json"

    with pytest.raises(OSError):
        kitchen.live.replace(layout_body(load_zones(DEFAULT_ZONES_PATH)))
    kitchen.assert_unchanged(file_text)


@pytest.mark.safety
def test_when_the_zone_table_cant_be_written_the_lookup_and_file_go_back(tmp_path):
    """The gate and the verdict service would still see the old pass, so the frame loop must use it too."""
    kitchen = LiveKitchen(tmp_path / "zones.json")
    file_text = kitchen.save_path.read_text()
    kitchen.state.fail_next(1)

    with pytest.raises(StateUnavailable):
        kitchen.live.replace(layout_body(load_zones(DEFAULT_ZONES_PATH)))

    assert kitchen.live.current() == kitchen.zones
    assert load_zones(kitchen.save_path) == kitchen.zones
    assert kitchen.save_path.read_text() == file_text
    new_lookup, restored_lookup = kitchen.lookups
    assert new_lookup((0.85, 0.2)).zone_id == "rack"
    assert restored_lookup((0.85, 0.2)) is None  # the sample layout again


def with_zone(zones: list[Zone], zone_id: str, kind: str | None = None, polygon=None) -> list[Zone]:
    """`zones` with zone `zone_id` given another kind or polygon."""
    return [
        Zone(zone.zone_id, kind or zone.kind, polygon or zone.polygon) if zone.zone_id == zone_id else zone
        for zone in zones
    ]


@pytest.mark.safety
@pytest.mark.parametrize(
    ("zone_id", "new_kind"), [("wash", "other"), ("clean-stack", "wash"), ("pass", "plating_spot")]
)
def test_a_zone_id_never_changes_kind_and_the_refusal_changes_nothing(tmp_path, zone_id, new_kind):
    """The interactions step would keep the old kind: a wash redrawn as "other" would still wash (toward PASS)."""
    kitchen = LiveKitchen(tmp_path / "zones.json")
    file_text = kitchen.save_path.read_text()
    with pytest.raises(
        ZoneFileError,
        match=f"zone '{zone_id}' is a .* zone in this run, and its kind can't change; delete it and draw a new one",
    ):
        kitchen.live.replace(layout_body(with_zone(kitchen.zones, zone_id, kind=new_kind)))
    kitchen.assert_unchanged(file_text)


@pytest.mark.safety
def test_a_zone_deleted_in_one_put_cant_come_back_with_another_kind(tmp_path):
    kitchen = LiveKitchen(tmp_path / "zones.json")
    without_wash = [zone for zone in kitchen.zones if zone.zone_id != "wash"]
    kitchen.live.replace(layout_body(without_wash))
    file_text = kitchen.save_path.read_text()

    comes_back = [*without_wash, Zone("wash", "other", rect(0.02, 0.7, 0.25, 0.98))]
    with pytest.raises(ZoneFileError, match="zone 'wash' is a wash zone in this run"):
        kitchen.live.replace(layout_body(comes_back))
    assert kitchen.live.current() == without_wash
    assert kitchen.save_path.read_text() == file_text


def test_a_zone_can_move_and_a_new_zone_id_can_have_any_kind(tmp_path):
    kitchen = LiveKitchen(tmp_path / "zones.json")
    moved = with_zone(kitchen.zones, "wash", polygon=rect(0.3, 0.75, 0.5, 0.98))
    assert kitchen.live.replace(layout_body(moved)) == moved

    redrawn = [zone for zone in moved if zone.zone_id != "wash"] + [Zone("prep", "other", rect(0.3, 0.75, 0.5, 0.98))]
    assert kitchen.live.replace(layout_body(redrawn)) == redrawn


# the calibration tool's logic (the window itself needs a display)


def test_pixel_to_point_normalizes_to_the_image():
    assert pixel_to_point(320, 240, 640, 480) == (0.5, 0.5)
    assert pixel_to_point(0, 0, 640, 480) == (0.0, 0.0)
    assert pixel_to_point(700, -5, 640, 480) == (1.0, 0.0)


@pytest.mark.safety
def test_corners_clicked_out_of_order_are_refused_so_the_person_redoes_them():
    # TL, TR, BL, BR on a skewed slot: a bow-tie that would leave most of the slot in no zone.
    clicks = [(13, 10), (115, 10), (13, 86), (122, 91)]
    polygon = [pixel_to_point(x, y, 640, 480) for x, y in clicks]
    with pytest.raises(ZoneFileError, match="edges cross"):
        add_zone([], Zone("slot-1", "bin_slot", polygon))
    in_order = [polygon[0], polygon[1], polygon[3], polygon[2]]
    assert add_zone([], Zone("slot-1", "bin_slot", in_order))[0].polygon == in_order


def test_add_zone_appends_a_good_zone_and_refuses_a_bad_one():
    first = Zone("slot-1", "bin_slot", rect(0.1, 0.1, 0.2, 0.2))
    zones = add_zone([], first)
    assert zones == [first]
    with pytest.raises(ZoneFileError, match="more than once"):
        add_zone(zones, Zone("slot-1", "bin_slot", rect(0.3, 0.3, 0.4, 0.4)))
    assert zones == [first]


def test_calibrate_reports_an_unreadable_image(tmp_path, capsys):
    pytest.importorskip("cv2")
    out = tmp_path / "zones.json"
    assert main(["calibrate", "--image", str(tmp_path / "missing.jpg"), "--out", str(out)]) == 1
    assert "can't read the image" in capsys.readouterr().err
    assert not out.exists()


def test_draw_marks_zones_and_clicks_without_changing_the_still():
    pytest.importorskip("cv2")
    import numpy as np

    from kitchen.vision.zones import draw

    still = np.zeros((48, 64, 3), dtype=np.uint8)
    canvas = draw(still, load_zones(SAMPLE), [(10, 10), (30, 10), (30, 30)])
    assert canvas.shape == still.shape
    assert canvas.any()
    assert not still.any()
