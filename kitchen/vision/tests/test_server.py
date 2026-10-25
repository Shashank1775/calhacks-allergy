"""Tests for the Vision HTTP API and FakeVisionAPI, through FastAPI's TestClient (no camera, models or network).

Owner: vision (agents)
Step:  vision
Reads: kitchen.vision.server, kitchen.vision.camera.PushedFrames, FakeClock
Emits: nothing
Rules: fail closed: every /plates/start refusal is a 409 with the contract's code and nothing is bound;
       /health last_frame_t_ms stops advancing when frames stop; the frame loop never waits on a slow client;
       a bad PUT /zones is a 422 bad_zones and changes nothing; a bad POST /frames is a 422 bad_frame and pushes
       nothing, and a runner on its own camera refuses pushed frames (409 not_web_source); /stream.mjpg?boxes=true
       draws the detector's boxes on a copy, never on the frame loop's array
"""

import json
import threading
import time

import anyio
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from kitchen.shared.clock import FakeClock
from kitchen.shared.interfaces import StateUnavailable
from kitchen.vision import server
from kitchen.vision.camera import PushedFrames
from kitchen.vision.common import BindRefused, Detection, FrameOutput
from kitchen.vision.server import FakeVisionAPI, VisionStatus, create_app, parse_cors_origins
from kitchen.vision.zones import DEFAULT_ZONES_PATH, ZoneFileError, load_zones, parse_zones

ORIGIN = "http://localhost:5173"
REFUSAL_CODES = ["no_plate_at_spot", "multiple_plates_at_spot", "plate_already_bound"]


def frame_output(frame_id: int, t_ms: int) -> FrameOutput:
    """A minimal FrameOutput for a frame with no detections."""
    observation = {"frame_id": frame_id, "t_ms": t_ms, "camera_id": "cam0", "detections": []}
    return FrameOutput(observation=observation, change_mask=np.zeros((128, 128), np.float32), width=64, height=48)


def rgb_frame(value: int = 100) -> np.ndarray:
    """A small solid RGB frame."""
    return np.full((48, 64, 3), value, dtype=np.uint8)


def no_zones() -> list:
    """get_zones for tests that don't look at zones."""
    return []


def refuse_zones(data):
    """put_zones for tests that don't change zones."""
    raise AssertionError("PUT /zones was not expected")


def app_for(status: VisionStatus, start_plate=lambda ticket_id: "plate-1", **kwargs):
    """create_app with the default web origin and no zones."""
    return create_app(status, start_plate, no_zones, refuse_zones, [ORIGIN], **kwargs)


def client_for(status: VisionStatus, start_plate=lambda ticket_id: "plate-1", **kwargs) -> TestClient:
    """A TestClient over create_app with the default web origin."""
    return TestClient(app_for(status, start_plate, **kwargs))


def assert_error(response, status_code: int, code: str) -> None:
    """The response is the standard error body (contracts/apis.md HTTP conventions) with this status and code."""
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


# ---------- POST /plates/start ----------


def test_plates_start_returns_the_bound_plate():
    calls = []

    def start_plate(ticket_id):
        calls.append(ticket_id)
        return "plate-3"

    response = client_for(VisionStatus("onnx"), start_plate).post("/plates/start", json={"ticket_id": "t7"})
    assert response.status_code == 200
    assert response.json() == {"plate_id": "plate-3"}
    assert calls == ["t7"]


@pytest.mark.safety
@pytest.mark.parametrize("code", REFUSAL_CODES)
def test_plates_start_refusal_is_a_409_with_its_code(code):
    def start_plate(ticket_id):
        raise BindRefused(code, "refused")

    response = client_for(VisionStatus("onnx"), start_plate).post("/plates/start", json={"ticket_id": "t7"})
    assert_error(response, 409, code)


@pytest.mark.safety
def test_plates_start_without_the_database_is_a_503():
    def start_plate(ticket_id):
        raise StateUnavailable("database down")

    response = client_for(VisionStatus("onnx"), start_plate).post("/plates/start", json={"ticket_id": "t7"})
    assert_error(response, 503, "state_unavailable")


@pytest.mark.safety
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ticket_id": ""},
        {"ticket_id": "   "},
        {"ticket_id": 7},
        {"plate_id": "plate-1"},
        {"ticket_id": "t7", "plate_id": "plate-9"},
    ],
)
def test_plates_start_bad_body_is_a_400_and_binds_nothing(body):
    calls = []
    client = client_for(VisionStatus("onnx"), lambda ticket_id: calls.append(ticket_id) or "plate-1")
    assert_error(client.post("/plates/start", json=body), 400, "bad_request")
    assert calls == []


@pytest.mark.safety
def test_plates_start_unexpected_error_is_a_500_with_the_error_body_and_cors():
    def start_plate(ticket_id):
        raise RuntimeError("bug in the binding")

    client = client_for(VisionStatus("onnx"), start_plate)
    response = client.post("/plates/start", json={"ticket_id": "t7"}, headers={"Origin": ORIGIN})
    assert_error(response, 500, "internal_error")
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_unknown_path_is_a_404_with_the_error_body():
    assert_error(client_for(VisionStatus("onnx")).get("/nope"), 404, "not_found")


def test_wrong_method_is_a_405_with_the_error_body():
    assert_error(client_for(VisionStatus("onnx")).get("/plates/start"), 405, "method_not_allowed")


def test_plates_start_invalid_json_is_a_400():
    client = client_for(VisionStatus("onnx"))
    response = client.post("/plates/start", content=b"{not json", headers={"Content-Type": "application/json"})
    assert_error(response, 400, "bad_request")


# ---------- GET /health ----------


def test_health_before_any_frame_has_no_last_frame():
    response = client_for(VisionStatus("hailo")).get("/health")
    assert response.json() == {"fps": 0.0, "detector": "hailo", "last_frame_t_ms": None}


@pytest.mark.safety
def test_health_last_frame_t_ms_stops_advancing_when_frames_stop():
    status = VisionStatus("onnx")
    client = client_for(status)
    for frame_id in range(5):
        status.update(frame_output(frame_id, 1000 + 100 * frame_id), rgb_frame())

    first = client.get("/health").json()
    assert set(first) == {"fps", "detector", "last_frame_t_ms"}
    assert first["last_frame_t_ms"] == 1400

    # The source stalls: however often health is read, the last frame's time stays put, so the gate can see it.
    for _ in range(3):
        time.sleep(0.01)
        assert client.get("/health").json()["last_frame_t_ms"] == 1400

    status.update(frame_output(5, 1500), rgb_frame())
    assert client.get("/health").json()["last_frame_t_ms"] == 1500


def test_health_fps_follows_the_frame_interval():
    status = VisionStatus("onnx")
    for frame_id in range(20):
        status.update(frame_output(frame_id, 40 * frame_id), rgb_frame())
    assert status.health()["fps"] == pytest.approx(25.0)


# ---------- GET /stream.mjpg ----------


def push_frames(status: VisionStatus, count: int, interval_s: float) -> threading.Thread:
    """Start a thread that plays the frame loop: `count` frames, `interval_s` apart."""

    def loop():
        for frame_id in range(count):
            time.sleep(interval_s)
            status.update(frame_output(frame_id, 33 * frame_id), rgb_frame(frame_id * 20))

    thread = threading.Thread(target=loop)
    thread.start()
    return thread


def test_stream_serves_a_jpeg_part_per_new_frame():
    status = VisionStatus("onnx")
    client = client_for(status, stream_idle_s=0.5)
    pusher = push_frames(status, count=5, interval_s=0.03)

    # The stream ends once frames stop for stream_idle_s, so the whole response can be read here.
    response = client.get("/stream.mjpg")
    pusher.join()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("multipart/x-mixed-replace; boundary=frame")
    parts = [part for part in response.content.split(b"--frame\r\n") if part]
    assert len(parts) >= 3
    for part in parts:
        headers, jpeg = part.split(b"\r\n\r\n", 1)
        assert b"Content-Type: image/jpeg" in headers
        assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9\r\n")


def test_stream_with_no_frames_ends_empty():
    response = client_for(VisionStatus("onnx"), stream_idle_s=0.05).get("/stream.mjpg")
    assert response.status_code == 200
    assert response.content == b""


def test_a_slow_client_never_blocks_the_frame_loop(monkeypatch):
    def slow_encode(rgb):
        time.sleep(0.5)
        return b"\xff\xd8\xff\xd9"

    monkeypatch.setattr(server, "encode_jpeg", slow_encode)
    status = VisionStatus("onnx")
    status.update(frame_output(0, 0), rgb_frame())
    client = threading.Thread(target=status.next_jpeg, args=(0, 1.0))
    client.start()
    time.sleep(0.05)  # the client is now inside the slow encode

    started = time.monotonic()
    for frame_id in range(1, 101):
        status.update(frame_output(frame_id, 33 * frame_id), rgb_frame())
    assert time.monotonic() - started < 0.1
    client.join()


def test_each_frame_is_encoded_at_most_once(monkeypatch):
    encoded = []

    def counting_encode(rgb):
        encoded.append(rgb)
        return b"\xff\xd8\xff\xd9"

    monkeypatch.setattr(server, "encode_jpeg", counting_encode)
    status = VisionStatus("onnx")
    status.update(frame_output(0, 0), rgb_frame())
    first = [status.next_jpeg(0, 0.1) for _ in range(3)]
    assert len(encoded) == 1
    assert first[0] == first[1] == first[2]
    assert status.next_jpeg(first[0][0], 0.05) is None  # nothing newer yet


KNIFE_BOX = Detection("knife", (0.25, 0.25, 0.75, 0.75), 0.91)


def decoded(jpeg: bytes) -> np.ndarray:
    """An MJPEG part's JPEG as RGB."""
    return cv2.cvtColor(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def test_boxes_draws_the_detectors_boxes_for_the_frame_and_leaves_the_plain_stream_alone():
    status = VisionStatus("onnx")
    status.update(frame_output(0, 0), rgb_frame(100), [KNIFE_BOX])

    _, plain = status.next_jpeg(0, 0.1)
    _, boxed = status.next_jpeg(0, 0.1, boxes=True)

    edge = decoded(boxed)[36, 16]  # on the box's left edge (x = 0.25 of 64 px), below its label
    assert abs(int(edge[0]) - 255) < 40 and abs(int(edge[1]) - 170) < 40 and int(edge[2]) < 60
    assert np.abs(decoded(plain).astype(int) - 100).max() < 10  # no box drawn
    assert np.abs(status._rgb.astype(int) - 100).max() == 0  # the frame loop's array is never drawn on


def test_each_view_of_a_frame_is_encoded_at_most_once(monkeypatch):
    encoded = []

    def counting_encode(rgb):
        encoded.append(rgb)
        return b"\xff\xd8\xff\xd9"

    monkeypatch.setattr(server, "encode_jpeg", counting_encode)
    status = VisionStatus("onnx")
    status.update(frame_output(0, 0), rgb_frame(), [KNIFE_BOX])
    for _ in range(3):
        status.next_jpeg(0, 0.1)
        status.next_jpeg(0, 0.1, boxes=True)
    assert len(encoded) == 2


def test_stream_with_boxes_true_serves_frames_with_the_boxes_drawn():
    status = VisionStatus("onnx")
    status.update(frame_output(0, 0), rgb_frame(100), [KNIFE_BOX])
    client = client_for(status, stream_idle_s=0.1)

    boxed = client.get("/stream.mjpg?boxes=true").content.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
    plain = client.get("/stream.mjpg").content.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")

    assert abs(int(decoded(boxed)[36, 16][0]) - 255) < 40
    assert np.abs(decoded(plain).astype(int) - 100).max() < 10


@pytest.mark.safety
def test_many_open_streams_never_stall_health_or_start_plate():
    """Fail closed needs /health answered on time: more open streams than the default thread pool has threads must
    not hold up the gate's health check or the tablet's "start plate"."""
    status = VisionStatus("onnx")
    app = app_for(status, stream_idle_s=1.0)

    async def shrink_default_pool():
        # The default pool has 40 threads; 4 keeps the test small. The streams have their own STREAM_THREADS.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 4

    with TestClient(app) as client:  # one event loop and one default thread pool for every request
        client.portal.call(shrink_default_pool)
        streams = [threading.Thread(target=client.get, args=("/stream.mjpg",)) for _ in range(6)]
        for stream in streams:
            stream.start()
        time.sleep(0.2)  # every stream is now waiting for a frame that never comes

        started = time.monotonic()
        health = client.get("/health")
        bound = client.post("/plates/start", json={"ticket_id": "t7"})
        elapsed = time.monotonic() - started
        for stream in streams:
            stream.join()

    assert health.status_code == 200
    assert bound.json() == {"plate_id": "plate-1"}
    assert elapsed < 0.5


def test_recent_observations_keep_only_the_history_window():
    status = VisionStatus("onnx", history_ms=100)
    assert status.recent_observations() == []
    for frame_id, t_ms in enumerate([0, 50, 100, 150, 201]):
        status.update(frame_output(frame_id, t_ms), rgb_frame())

    assert [observation["t_ms"] for observation in status.recent_observations()] == [150, 201]


# ---------- GET and PUT /zones ----------

DEFAULT_LAYOUT = json.loads(DEFAULT_ZONES_PATH.read_text())


def zones_client(put_zones, get_zones=lambda: load_zones(DEFAULT_ZONES_PATH)) -> TestClient:
    """A TestClient over create_app with these zone callables."""
    return TestClient(create_app(VisionStatus("onnx"), lambda ticket_id: "plate-1", get_zones, put_zones, [ORIGIN]))


def test_get_zones_returns_the_zones_in_use_in_the_zones_file_format():
    response = zones_client(refuse_zones).get("/zones")
    assert response.status_code == 200
    assert response.json() == DEFAULT_LAYOUT


def test_put_zones_hands_over_the_parsed_body_and_returns_the_zones_now_in_use():
    received = []

    def put_zones(data):
        received.append(data)
        return parse_zones(data)

    response = zones_client(put_zones).put("/zones", json=DEFAULT_LAYOUT)
    assert response.status_code == 200
    assert response.json() == DEFAULT_LAYOUT
    assert received == [DEFAULT_LAYOUT]


@pytest.mark.safety
def test_put_zones_bad_layout_is_a_422_bad_zones_with_the_reason():
    def put_zones(data):
        raise ZoneFileError("zone 'slot-1': kind 'fridge' is not one of ...")

    response = zones_client(put_zones).put("/zones", json={"zones": []}, headers={"Origin": ORIGIN})
    assert_error(response, 422, "bad_zones")
    assert "fridge" in response.json()["error"]["message"]
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.safety
@pytest.mark.parametrize("body", [b"{not json", b"", b"\xff\xfe"])
def test_put_zones_body_that_isnt_json_is_a_422_and_changes_nothing(body):
    received = []
    client = zones_client(lambda data: received.append(data) or [])
    response = client.put("/zones", content=body, headers={"Content-Type": "application/json"})
    assert_error(response, 422, "bad_zones")
    assert received == []


@pytest.mark.safety
def test_put_zones_that_cant_be_saved_is_a_500_with_the_error_body_and_cors():
    def put_zones(data):
        raise OSError("read-only file system: 'data/zones.json'")

    response = zones_client(put_zones).put("/zones", json=DEFAULT_LAYOUT, headers={"Origin": ORIGIN})
    assert_error(response, 500, "internal_error")
    assert "read-only" in response.json()["error"]["message"]
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.safety
def test_put_zones_without_the_database_is_a_503():
    def put_zones(data):
        raise StateUnavailable("database down")

    assert_error(zones_client(put_zones).put("/zones", json=DEFAULT_LAYOUT), 503, "state_unavailable")


# ---------- POST /frames ----------

JPEG_HEADERS = {"Content-Type": "image/jpeg"}


def jpeg_frame(value: int = 100) -> bytes:
    """A small solid frame as JPEG bytes, as the web app sends it."""
    ok, jpeg = cv2.imencode(".jpg", rgb_frame(value))
    assert ok
    return jpeg.tobytes()


def test_post_frames_takes_the_jpeg_and_answers_204_with_no_body():
    frames = PushedFrames(FakeClock(start_ms=40))
    client = client_for(VisionStatus("onnx"), push_frame=frames.push)

    response = client.post("/frames", content=jpeg_frame(), headers=JPEG_HEADERS)

    assert response.status_code == 204
    assert response.content == b""
    frame = next(iter(frames))
    assert frame.t_ms == 40  # vision's clock when the frame arrived
    assert frame.rgb.shape == (48, 64, 3)


@pytest.mark.safety
@pytest.mark.parametrize("body", [b"", b"not a jpeg", b"\xff\xd8 broken"], ids=["empty", "text", "broken-jpeg"])
def test_post_frames_bad_jpeg_is_a_422_bad_frame_and_pushes_nothing(body):
    frames = PushedFrames(FakeClock())
    client = client_for(VisionStatus("onnx"), push_frame=frames.push)

    assert_error(client.post("/frames", content=body, headers=JPEG_HEADERS), 422, "bad_frame")
    assert frames.last_t_ms == -1


@pytest.mark.safety
def test_post_frames_to_a_runner_on_its_own_camera_is_a_409_not_web_source():
    """Frames pushed into a runner that reads a camera or video would mix two cameras' pictures, so they're refused."""
    assert_error(
        client_for(VisionStatus("onnx")).post("/frames", content=jpeg_frame(), headers=JPEG_HEADERS),
        409,
        "not_web_source",
    )


def test_post_frames_unexpected_error_is_a_500_with_the_error_body_and_cors():
    def push_frame(jpeg_bytes):
        raise RuntimeError("bug")

    client = client_for(VisionStatus("onnx"), push_frame=push_frame)
    response = client.post("/frames", content=jpeg_frame(), headers={"Origin": ORIGIN, **JPEG_HEADERS})

    assert_error(response, 500, "internal_error")
    assert response.headers["access-control-allow-origin"] == ORIGIN


# ---------- CORS ----------


def test_cors_only_for_listed_origins():
    client = client_for(VisionStatus("onnx"))
    allowed = client.get("/health", headers={"Origin": ORIGIN})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN

    other = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers

    preflight = client.options(
        "/plates/start",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_cors_preflight_allows_put_zones_from_listed_origins_only():
    """The web zone editor saves with PUT and a JSON body, so browsers preflight both the method and Content-Type."""
    client = client_for(VisionStatus("onnx"))
    request = {"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "Content-Type"}

    allowed = client.options("/zones", headers={"Origin": ORIGIN, **request})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "PUT" in allowed.headers["access-control-allow-methods"]
    assert "content-type" in allowed.headers["access-control-allow-headers"].lower()

    other = client.options("/zones", headers={"Origin": "http://evil.example", **request})
    assert "access-control-allow-origin" not in other.headers


def test_cors_preflight_allows_post_frames_with_a_jpeg_body_from_listed_origins_only():
    """image/jpeg isn't a type browsers send without asking, so the web app's frames are preflighted."""
    client = client_for(VisionStatus("onnx"), push_frame=lambda jpeg_bytes: None)
    request = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"}

    allowed = client.options("/frames", headers={"Origin": ORIGIN, **request})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in allowed.headers["access-control-allow-methods"]
    assert "content-type" in allowed.headers["access-control-allow-headers"].lower()

    posted = client.post("/frames", content=jpeg_frame(), headers={"Origin": ORIGIN, **JPEG_HEADERS})
    assert posted.status_code == 204
    assert posted.headers["access-control-allow-origin"] == ORIGIN

    other = client.options("/frames", headers={"Origin": "http://evil.example", **request})
    assert "access-control-allow-origin" not in other.headers


def test_cors_wildcard_is_refused():
    with pytest.raises(ValueError):
        create_app(VisionStatus("onnx"), lambda ticket_id: "plate-1", no_zones, refuse_zones, ["*"])


def test_parse_cors_origins_splits_and_trims():
    assert parse_cors_origins("http://a:1, http://b:2,") == ["http://a:1", "http://b:2"]


# ---------- FakeVisionAPI ----------


def fake_client(plates_at_spot, clock=None) -> tuple[FakeVisionAPI, TestClient]:
    """A FakeVisionAPI and a TestClient over its app."""
    fake = FakeVisionAPI(plates_at_spot, clock=clock or FakeClock())
    return fake, TestClient(fake.app)


def test_fake_api_binds_the_one_plate_at_the_spot():
    fake, client = fake_client(["plate-1"])
    response = client.post("/plates/start", json={"ticket_id": "t7"})
    assert response.json() == {"plate_id": "plate-1"}
    assert fake.bindings == {"plate-1": "t7"}

    # Tapping again for the same ticket is not a new binding, so it is not refused.
    assert client.post("/plates/start", json={"ticket_id": "t7"}).json() == {"plate_id": "plate-1"}


@pytest.mark.safety
@pytest.mark.parametrize(
    ("plates", "code"),
    [([], "no_plate_at_spot"), (["plate-1", "plate-2"], "multiple_plates_at_spot")],
)
def test_fake_api_refuses_zero_or_several_plates(plates, code):
    fake, client = fake_client(plates)
    assert_error(client.post("/plates/start", json={"ticket_id": "t7"}), 409, code)
    assert fake.bindings == {}


@pytest.mark.safety
def test_fake_api_never_rebinds_a_plate_to_another_ticket():
    fake, client = fake_client(["plate-1"])
    client.post("/plates/start", json={"ticket_id": "t7"})
    assert_error(client.post("/plates/start", json={"ticket_id": "t8"}), 409, "plate_already_bound")
    assert fake.bindings == {"plate-1": "t7"}


@pytest.mark.safety
@given(
    scenes=st.lists(
        st.tuples(
            st.lists(st.sampled_from(["plate-1", "plate-2", "plate-3"]), max_size=3, unique=True),
            st.sampled_from(["t1", "t2", "t3"]),
        ),
        max_size=12,
    )
)
def test_fake_api_binds_only_one_plate_alone_at_the_spot_and_never_changes_a_binding(scenes):
    fake = FakeVisionAPI([], clock=FakeClock())
    for plates, ticket_id in scenes:
        fake.set_scene(plates, None)
        before = dict(fake.bindings)
        try:
            plate_id = fake.start_plate(ticket_id)
        except BindRefused:
            assert fake.bindings == before
            continue
        assert plates == [plate_id]
        assert before.get(plate_id) in (None, ticket_id)
        assert fake.bindings == {**before, plate_id: ticket_id}


@pytest.mark.safety
def test_fake_api_health_goes_stale_when_frames_are_stopped():
    clock = FakeClock()
    fake, client = fake_client(["plate-1"], clock)
    assert fake.push_frame()
    clock.advance(100)
    assert fake.push_frame()
    assert client.get("/health").json()["last_frame_t_ms"] == 100

    scene = client.post("/fake/scene", json={"frames_running": False}).json()
    assert scene == {"plates_at_spot": ["plate-1"], "frames_running": False}
    clock.advance(100)
    assert not fake.push_frame()
    assert client.get("/health").json() == {"fps": 10.0, "detector": "onnx", "last_frame_t_ms": 100}


def test_fake_api_scene_changes_the_plates_at_the_spot():
    fake, client = fake_client(["plate-1"])
    client.post("/fake/scene", json={"plates_at_spot": []})
    assert_error(client.post("/plates/start", json={"ticket_id": "t7"}), 409, "no_plate_at_spot")


def test_fake_api_serves_the_default_layout_and_keeps_a_good_put():
    fake, client = fake_client(["plate-1"])
    assert client.get("/zones").json() == DEFAULT_LAYOUT

    plating_only = {"zones": [zone for zone in DEFAULT_LAYOUT["zones"] if zone["kind"] == "plating_spot"]}
    assert client.put("/zones", json=plating_only).json() == plating_only
    assert client.get("/zones").json() == plating_only
    assert fake.get_zones() == parse_zones(plating_only)


@pytest.mark.safety
def test_fake_api_refuses_a_bad_put_exactly_as_the_real_one_and_keeps_its_zones():
    fake, client = fake_client(["plate-1"])
    bad = {"zones": [{**DEFAULT_LAYOUT["zones"][0], "kind": "fridge"}]}
    assert_error(client.put("/zones", json=bad), 422, "bad_zones")
    assert client.get("/zones").json() == DEFAULT_LAYOUT


def test_fake_api_streams_its_test_pattern():
    fake = FakeVisionAPI(clock=FakeClock())
    fake.push_frame()
    frame_number, jpeg = fake.status.next_jpeg(0, 0.1)
    assert frame_number == 1
    assert jpeg.startswith(b"\xff\xd8")
