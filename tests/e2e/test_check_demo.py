"""tools/check_demo.py against the real live-mode apps (tools/run_local.py's LiveRunner) and against broken setups.

Owner: platform (agents)
Step:  end-to-end harness
Reads: tools/check_demo.py and tools/run_local.py (loaded by path); a scripted detector instead of a camera
Emits: nothing
Rules: the check is read-only, so it can run against a live demo: it sends only GET and OPTIONS requests
"""

import importlib.util
import socket
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from kitchen.shared.clock import FakeClock
from kitchen.shared.models import Zone
from kitchen.vision.common import SourceFrame, VisionSettings

TOOLS = Path(__file__).resolve().parents[2] / "tools"
ORIGIN = "http://localhost:5173"
ZONES = [
    Zone("slot-1", "bin_slot", [(0.0, 0.0), (0.2, 0.0), (0.2, 0.4), (0.0, 0.4)]),
    Zone("plating-spot", "plating_spot", [(0.35, 0.3), (0.65, 0.3), (0.65, 0.7), (0.35, 0.7)]),
    Zone("pass", "pass", [(0.75, 0.0), (1.0, 0.0), (1.0, 1.0), (0.75, 1.0)]),
]
DEMO_BIN_ROWS = [{"entity_id": f"bin-{n}"} for n in range(1, 6)]


def load(name: str):
    """A tools/ script as a module (tools/ isn't a package)."""
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_demo = load("check_demo")
run_local = load("run_local")


class ScriptedDetector:
    """An empty counter, reported as coming from `backend`."""

    def __init__(self, backend: str):
        self.backend = backend

    def detect(self, frame):
        return [], []


class Pi:
    """The live runner as the Pi runs it, on a FakeClock: the Orders app and GET /state, and the Vision app."""

    def __init__(self, cors_origins=(ORIGIN,), backend="hailo", frame_ms=33):
        self.clock = FakeClock(0)
        self.frame_ms = frame_ms
        self.runner = run_local.LiveRunner(
            self.clock,
            ZONES,
            vision_settings=VisionSettings(),
            detector=ScriptedDetector(backend),
            interactions=lambda state, clock: lambda output: None,
            cors_origins=list(cors_origins),
            vision_cors_origins=list(cors_origins),
        )
        self.frame_id = 0
        self.methods = []
        self.orders = self.client(self.runner.app)
        self.vision = self.client(self.runner.vision_app)
        for _ in range(5):
            self.frame()

    def client(self, app) -> TestClient:
        """A test client for one app that records the method of every request it sends."""
        client = TestClient(app)
        client.event_hooks = {"request": [lambda request: self.methods.append(request.method)], "response": []}
        return client

    def frame(self, seconds: float = 0) -> None:
        """One camera frame, frame_ms after the last. Takes the check's sleep argument, so frames keep coming."""
        self.clock.advance(self.frame_ms)
        rgb = np.full((96, 128, 3), self.frame_id % 256, dtype=np.uint8)
        self.runner.step_frame(SourceFrame(self.frame_id, self.clock.now_ms(), rgb))
        self.frame_id += 1


@pytest.fixture(autouse=True)
def streams_end_soon_after_the_last_frame(monkeypatch):
    """After the check hangs up, a test client (unlike a real server) waits for the stream to end, which vision's
    stream does once no frame has come for 5 s. The real Vision app, with that cut to 0.1 s."""
    from kitchen.vision import server

    create_app = server.create_app
    monkeypatch.setattr(server, "create_app", lambda *args, **kwargs: create_app(*args, stream_idle_s=0.1, **kwargs))


def statuses(report) -> dict[str, list[str]]:
    return {title: [result.status for result in results] for title, results in report}


def test_a_healthy_demo_passes_every_check_and_only_reads(capsys):
    pi = Pi()
    report = check_demo.check_demo(pi.orders, pi.vision, ORIGIN, sleep=pi.frame)

    assert all(status == "pass" for found in statuses(report).values() for status in found), report
    assert check_demo.print_report(report) == 0
    assert "0 failed" in capsys.readouterr().out
    assert set(pi.methods) == {"GET", "OPTIONS"}  # never POST /plates/start, or anything else that writes
    assert pi.runner.mirror.snapshot()["plate_binding"] == []


def test_a_web_origin_the_pi_doesnt_allow_fails_cors_and_names_the_setting():
    pi = Pi(cors_origins=("http://localhost:5173",))
    ipad = "http://192.168.1.10:5173"
    report = dict(check_demo.check_demo(pi.orders, pi.vision, ipad, sleep=pi.frame))

    for title, setting in (("Orders API CORS", "ORDERS_CORS_ORIGINS"), ("Vision API CORS", "VISION_CORS_ORIGINS")):
        assert [result.status for result in report[title]] == ["fail", "fail"]
        assert all(f"add {ipad} to {setting}" in result.fix for result in report[title])
    assert all(result.status == "pass" for result in report["Runner"] + report["Camera stream"])
    assert check_demo.print_report(list(report.items())) == 1


def test_frames_that_stopped_fail_even_though_health_answers():
    pi = Pi()
    report = dict(check_demo.check_demo(pi.orders, pi.vision, ORIGIN, sleep=lambda seconds: None))

    stale = report["Vision"][-1]
    assert stale.status == "fail"
    assert "stuck" in stale.found
    assert "rpicam-hello" in stale.fix


@pytest.mark.parametrize(
    ("backend", "frame_ms", "expected"),
    [("hailo", 33, ["pass", "pass"]), ("onnx", 50, ["warn", "warn"]), ("onnx", 150, ["warn", "fail"])],
    ids=["hailo-30fps", "onnx-20fps", "onnx-7fps"],
)
def test_the_detector_and_frame_rate_are_judged_against_the_hailo_target(backend, frame_ms, expected):
    pi = Pi(backend=backend, frame_ms=frame_ms)
    detector, fps, _ = check_demo.check_vision(pi.vision, sleep=pi.frame)

    assert [detector.status, fps.status] == expected


def runner_answering(snapshot: dict) -> httpx.Client:
    """A client whose GET /state answers `snapshot`."""
    return httpx.Client(
        base_url="http://pi.test:8001",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=snapshot)),
    )


@pytest.mark.parametrize(
    ("mode", "zones", "bins", "expected", "fix"),
    [
        ("live", [{"zone_id": "pass"}], DEMO_BIN_ROWS, ["pass", "pass", "pass", "pass"], ""),
        ("replay", [{"zone_id": "pass"}], DEMO_BIN_ROWS, ["pass", "warn", "pass", "pass"], "--live"),
        ("live", [], DEMO_BIN_ROWS, ["pass", "pass", "fail", "pass"], "--zones zones.json"),
        ("live", [{"zone_id": "pass"}], DEMO_BIN_ROWS[:3], ["pass", "pass", "pass", "fail"], "DEMO_BINS"),
    ],
    ids=["live", "replay", "no-zones", "missing-bins"],
)
def test_the_runners_state_is_checked_for_live_mode_zones_and_bins(mode, zones, bins, expected, fix):
    snapshot = {"mode": mode, "tables": {"zone": zones, "bin_profile": bins}}
    results = check_demo.check_runner(runner_answering(snapshot))

    assert [result.status for result in results] == expected
    assert all(fix in result.fix for result in results if result.status != "pass")


def test_a_stream_part_that_isnt_a_jpeg_fails():
    part = b"--frame\r\nContent-Type: text/plain\r\n\r\nnot a frame\r\n"
    stream = httpx.Client(
        base_url="http://pi.test:8002", transport=httpx.MockTransport(lambda request: httpx.Response(200, content=part))
    )

    assert [result.status for result in check_demo.check_stream(stream)] == ["fail"]


def test_an_unreachable_pi_fails_every_section_with_its_fix(capsys):
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])

    code = check_demo.main(["--pi", "127.0.0.1", "--orders-port", port, "--vision-port", port])

    out = capsys.readouterr().out
    assert code == 1
    assert out.count("✗ no answer from http://127.0.0.1") == 5
    assert "fix: start the runner on the Pi" in out
    assert "fix: the runner serves the Vision API only with --live" in out
