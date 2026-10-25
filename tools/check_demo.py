"""Demo check: one command on the laptop that says whether the Pi's runner and the web app can talk, and what to fix.

Owner: platform (agents)
Step:  end-to-end harness (demo day)
Reads: the live runner on the Pi (tools/run_local.py --live): GET /state and the Orders API on --orders-port, and the
       Vision API (GET /health, GET /stream.mjpg) on --vision-port; DEMO_BINS (kitchen/orders/bins.py)
Emits: one line per check on stdout (✓ passed, ⚠ works but isn't demo-ready, ✗ failed), each warning and failure with a
       one-line fix; exit 1 if any check failed
Rules: read-only: only GET requests and CORS preflights (OPTIONS, which never reach a handler), so running it against
       a live demo never starts a plate, sends a ticket or writes anything; it reads the first camera frame and hangs up

    uv run python tools/check_demo.py --pi http://192.168.1.20                                       # browser on the laptop
    uv run python tools/check_demo.py --pi http://192.168.1.20 --web-origin http://192.168.1.10:5173  # browser elsewhere

docs/demo-runbook.md says how to bring the demo up.
"""

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from kitchen.orders.api import DEFAULT_CORS_ORIGINS, DEFAULT_PORT
from kitchen.orders.bins import DEMO_BINS

PASS, WARN, FAIL = "pass", "warn", "fail"
SYMBOLS = {PASS: "✓", WARN: "⚠", FAIL: "✗"}

VISION_PORT = 8002  # VISION_PORT's default (kitchen/vision/common.py)
CONNECT_TIMEOUT_S = 1.0  # on the same network the Pi accepts in milliseconds, so a wrong address fails fast
STATE_TIMEOUT_S = 2.0  # the screens poll GET /state twice a second; a slower answer is a broken demo
STREAM_TIMEOUT_S = 3.0  # the first camera frame must arrive within this
FRAME_WAIT_S = 0.5  # last_frame_t_ms must advance between two /health reads this far apart
MIN_FPS = 10  # below this the tracker loses a moving knife: fail
TARGET_FPS = 30  # det + pose on the Hailo hold this (kitchen/vision/README.md): warn below

NO_RUNNER = "start the runner on the Pi (docs/demo-runbook.md), and check --pi is its address on this network"
NO_VISION = "the runner serves the Vision API only with --live: restart it with --live --picamera --zones zones.json"


@dataclass
class Result:
    """One check's outcome: PASS, WARN or FAIL, what was found, and for a warning or failure what to do about it."""

    status: str
    found: str
    fix: str = ""


class NoAnswerError(Exception):
    """Nothing answered a request: the host is down, the port is closed, or it timed out."""


def request(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    """One request. Anything but an answer raises NoAnswerError, so a section that can't reach its service stops."""
    try:
        return client.request(method, path, **kwargs)
    except httpx.TransportError as error:
        raise NoAnswerError(f"{client.base_url.join(path)} ({type(error).__name__})") from error


# ---------- the runner: GET /state ----------


def check_runner(orders: httpx.Client) -> list[Result]:
    """GET /state answers in time, says it's live, and holds the zones and the demo bins."""
    started = time.monotonic()
    response = request(orders, "GET", "/state")  # main() gives the Orders client a STATE_TIMEOUT_S timeout
    elapsed_ms = round(1000 * (time.monotonic() - started))
    if response.status_code != 200:
        return [Result(FAIL, f"GET /state answered {response.status_code}", "restart tools/run_local.py on the Pi")]
    try:
        snapshot = response.json()
        tables = snapshot["tables"]
        zones, bins = tables["zone"], tables["bin_profile"]
    except (ValueError, KeyError, TypeError):
        return [Result(FAIL, "GET /state isn't the runner's snapshot", "is something else on this port? check --pi")]
    return [
        Result(PASS, f"GET /state answered in {elapsed_ms} ms"),
        check_mode(snapshot.get("mode")),
        check_zones(zones),
        check_bins(bins),
    ]


def check_mode(mode) -> Result:
    """The screens drop REPLAY only when the runner says it's live."""
    if mode == "live":
        return Result(PASS, "mode is live")
    if mode == "replay":
        fix = "restart the runner with --live --picamera --zones zones.json"
        return Result(WARN, "mode is replay: the runner is playing a golden scenario, not the camera", fix)
    return Result(FAIL, f"mode is {mode!r}", "update the Pi's checkout to main and restart the runner with --live")


def check_zones(zones: list[dict]) -> Result:
    """Without zones nothing reaches the pass and no plate can start."""
    if not zones:
        fix = "restart the runner with --zones zones.json, calibrated on a still from the mounted camera"
        return Result(FAIL, "no zones in GET /state", fix)
    return Result(PASS, f"{len(zones)} zones: {', '.join(sorted(zone['zone_id'] for zone in zones))}")


def check_bins(bins: list[dict]) -> Result:
    """The runner writes the demo bins at startup; a missing one carries unknown allergens and holds every plate."""
    missing = sorted(set(DEMO_BINS) - {row["entity_id"] for row in bins})
    if missing:
        fix = "the runner writes kitchen/orders/bins.py's DEMO_BINS at startup: update the Pi to main and restart it"
        return Result(FAIL, f"bin_profile is missing {', '.join(missing)}", fix)
    return Result(PASS, f"bin_profile has the {len(DEMO_BINS)} demo bins")


# ---------- CORS: what the browser needs before it will call the Pi ----------


def check_cors(client: httpx.Client, post_path: str, get_path: str, origin: str, env_var: str) -> list[Result]:
    """A preflight for a POST the web app makes succeeds, and a GET it makes is allowed for `origin`.

    The preflight is an OPTIONS request: the CORS middleware answers it, so the POST's handler never runs.
    """
    fix = f"add {origin} to {env_var} on the Pi (comma-separated, no spaces), then restart the runner"
    asks = {"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    preflight = request(client, "OPTIONS", post_path, headers=asks)
    granted = preflight.headers.get("access-control-allow-origin")
    if preflight.status_code == 200 and granted == origin:
        results = [Result(PASS, f"preflight for POST {post_path} allowed")]
    else:
        results = [Result(FAIL, f"preflight for POST {post_path} refused ({preflight.status_code})", fix)]

    response = request(client, "GET", get_path, headers={"Origin": origin})
    granted = response.headers.get("access-control-allow-origin")
    if granted == origin:
        results.append(Result(PASS, f"GET {get_path} allows the origin"))
    else:
        results.append(Result(FAIL, f"GET {get_path} doesn't allow the origin (it allows {granted or 'none'})", fix))
    return results


# ---------- vision: GET /health and the camera stream ----------


def read_health(vision: httpx.Client) -> dict:
    """GET /health's body, or {} if it isn't a JSON object from a 200."""
    response = request(vision, "GET", "/health")
    try:
        body = response.json() if response.status_code == 200 else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def check_vision(vision: httpx.Client, sleep: Callable[[float], None]) -> list[Result]:
    """The detector is the Hailo, frames come fast enough, and they're still coming (last_frame_t_ms advances)."""
    first = read_health(vision)
    if "last_frame_t_ms" not in first:
        return [Result(FAIL, "GET /health isn't the Vision API's", "is something else on this port? check --pi")]
    results = [check_detector(first.get("detector")), check_fps(first.get("fps") or 0)]

    sleep(FRAME_WAIT_S)
    second = read_health(vision)
    before, after = first["last_frame_t_ms"], second.get("last_frame_t_ms")
    fix = "frames stopped: read the runner's log, check the camera with rpicam-hello, then restart the runner"
    if before is None or after is None:
        results.append(Result(FAIL, "no frame yet (last_frame_t_ms is null)", fix))
    elif after <= before:
        results.append(Result(FAIL, f"last_frame_t_ms stuck at {after} for {FRAME_WAIT_S} s", fix))
    else:
        results.append(Result(PASS, f"frames advancing ({after - before} ms of frames in {FRAME_WAIT_S} s)"))
    return results


def check_detector(detector) -> Result:
    """ONNX on the Pi's CPU works but is far too slow for the demo."""
    if detector == "hailo":
        return Result(PASS, "detector is hailo")
    fix = (
        "on the Pi set VISION_DETECTOR=hailo VISION_BUNDLE_DIR=data/models/stock-yolov8s-hailo8l and restart the runner"
    )
    return Result(WARN, f"detector is {detector}, not hailo", fix)


def check_fps(fps: float) -> Result:
    """Fail below MIN_FPS, warn below TARGET_FPS."""
    if fps < MIN_FPS:
        fix = "the tracker will lose moving tools: use the Hailo (VISION_DETECTOR=hailo)"
        return Result(FAIL, f"vision runs at {fps} fps, under {MIN_FPS}", fix)
    if fps < TARGET_FPS:
        fix = "check VISION_DETECTOR=hailo, and that the Pi isn't throttling (vcgencmd get_throttled)"
        return Result(WARN, f"vision runs at {fps} fps, under {TARGET_FPS}", fix)
    return Result(PASS, f"vision runs at {fps} fps")


def check_stream(vision: httpx.Client) -> list[Result]:
    """GET /stream.mjpg sends a JPEG part within STREAM_TIMEOUT_S. Reads the first part only, then hangs up."""
    fix = "the camera stream is broken: read the runner's log and restart the runner"
    started = time.monotonic()
    try:
        with vision.stream("GET", "/stream.mjpg") as response:  # main() gives it a STREAM_TIMEOUT_S read timeout
            if response.status_code != 200:
                return [Result(FAIL, f"GET /stream.mjpg answered {response.status_code}", fix)]
            headers, body = first_part(response.iter_bytes(), started + STREAM_TIMEOUT_S)
    except httpx.ConnectError as error:
        raise NoAnswerError(f"{vision.base_url.join('/stream.mjpg')} ({type(error).__name__})") from error
    except httpx.TimeoutException:  # it answered, then sent no frame in time
        headers, body = b"", b""
    if b"content-type: image/jpeg" not in headers.lower() or not body.startswith(b"\xff\xd8"):
        return [Result(FAIL, f"no JPEG frame from /stream.mjpg within {STREAM_TIMEOUT_S:g} s", fix)]
    return [Result(PASS, f"/stream.mjpg sent a JPEG in {time.monotonic() - started:.1f} s")]


def first_part(chunks, deadline: float) -> tuple[bytes, bytes]:
    """The first multipart part's headers and the start of its body, or empty bytes if it isn't there by deadline."""
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        headers, found, body = buffer.partition(b"\r\n\r\n")
        if found and len(body) >= 2:  # two bytes are enough to check the JPEG start-of-image marker
            return headers, body
        if time.monotonic() > deadline:
            break
    return b"", b""


# ---------- running it ----------


def check_demo(
    orders: httpx.Client, vision: httpx.Client, origin: str, sleep: Callable[[float], None] = time.sleep
) -> list[tuple[str, list[Result]]]:
    """Every check, as (section title, results). A section whose service doesn't answer gets one ✗ and stops."""
    sections = [
        ("Runner", NO_RUNNER, lambda: check_runner(orders)),
        ("Orders API CORS", NO_RUNNER, lambda: check_cors(orders, "/parse", "/state", origin, "ORDERS_CORS_ORIGINS")),
        ("Vision", NO_VISION, lambda: check_vision(vision, sleep)),
        (
            "Vision API CORS",
            NO_VISION,
            lambda: check_cors(vision, "/plates/start", "/health", origin, "VISION_CORS_ORIGINS"),
        ),
        ("Camera stream", NO_VISION, lambda: check_stream(vision)),
    ]
    report = []
    for title, no_answer_fix, run in sections:
        try:
            results = run()
        except NoAnswerError as error:
            results = [Result(FAIL, f"no answer from {error}", no_answer_fix)]
        report.append((title, results))
    return report


def print_report(report: list[tuple[str, list[Result]]]) -> int:
    """Print every section and result. Returns the exit code: 1 if any check failed, else 0."""
    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for title, results in report:
        print(title)
        for result in results:
            counts[result.status] += 1
            print(f"  {SYMBOLS[result.status]} {result.found}")
            if result.fix:
                print(f"      fix: {result.fix}")
    print(f"\n{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed")
    return 1 if counts[FAIL] else 0


def main(argv: list[str] | None = None) -> int:
    """CLI: check the Pi at --pi for a browser on --web-origin."""
    cli = argparse.ArgumentParser(description="Check that the Pi's live runner and the web app can talk.")
    cli.add_argument("--pi", required=True, help="the Pi's address, without a port, e.g. http://192.168.1.20")
    cli.add_argument("--web-origin", default=DEFAULT_CORS_ORIGINS, help="where the browser opens the web app")
    cli.add_argument("--orders-port", type=int, default=DEFAULT_PORT, help="the Orders API and GET /state")
    cli.add_argument("--vision-port", type=int, default=VISION_PORT, help="the Vision API")
    args = cli.parse_args(argv)

    pi = args.pi.rstrip("/")
    if "://" not in pi:
        pi = f"http://{pi}"
    print(f"Checking the Pi at {pi} for a web app at {args.web_origin}\n")
    orders_timeout = httpx.Timeout(STATE_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    vision_timeout = httpx.Timeout(STREAM_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    with (
        httpx.Client(base_url=f"{pi}:{args.orders_port}", timeout=orders_timeout) as orders,
        httpx.Client(base_url=f"{pi}:{args.vision_port}", timeout=vision_timeout) as vision,
    ):
        return print_report(check_demo(orders, vision, args.web_origin))


if __name__ == "__main__":
    sys.exit(main())
