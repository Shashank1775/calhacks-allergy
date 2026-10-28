import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { example_zones } from "../api/mocks/handlers";
import { server, TEST_VISION_URL } from "../api/mocks/server";
import { STREAM_RETRY_MS } from "../camera_stream";
import { StateSourceContext } from "../state/hooks";
import { empty_tables, type StateSource, type Tables } from "../state/state_source";
import { render_with_source } from "../test_utils";
import type { VisionHealth } from "../vision_health";
import { ZonesScreen } from "./zones_screen";

// Vision's health, set by each test: "ok" while frames flow, "stale" once they stop.
const vision = vi.hoisted(() => ({ health: "ok" as VisionHealth }));
vi.mock("../vision_health", () => ({ useVisionHealth: () => vision.health }));

// The frame is 1000 × 500 px, so a pointer at (100, 50) is the point (0.1, 0.1).
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1000, 500));
});

afterEach(() => {
  vi.useRealTimers();
  vision.health = "ok";
});

async function open_screen(source: StateSource | null = null) {
  const view = render_with_source(<ZonesScreen />, source);
  await screen.findByLabelText("zone slot-1");
  return view;
}

function frame() {
  return screen.getByLabelText("Counter picture with zones");
}

/** A press, a move and a release with the primary button, as a mouse or one finger makes them. */
function drag(start_on: Element, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(start_on, { clientX: from[0], clientY: from[1], pointerId: 1, button: 0, buttons: 1 });
  fireEvent.pointerMove(frame(), { clientX: to[0], clientY: to[1], pointerId: 1, buttons: 1 });
  fireEvent.pointerUp(frame(), { clientX: to[0], clientY: to[1], pointerId: 1, button: 0, buttons: 0 });
}

/** A state source that holds these tables and never changes. */
function source_with(fill: (tables: Tables) => void): StateSource {
  const tables = empty_tables();
  fill(tables);
  return {
    label: "test",
    is_replay: true,
    rows: (table) => tables[table],
    feed_status: () => "live",
    subscribe: () => () => {},
    start: () => {},
  };
}

function points_of(zone_id: string) {
  return screen.getByLabelText(`zone ${zone_id}`).getAttribute("points");
}

/** Records every PUT /zones body and answers as vision does when the layout is good. */
function record_saves() {
  const bodies: unknown[] = [];
  server.use(
    http.put(`${TEST_VISION_URL}/zones`, async ({ request }) => {
      const body = await request.json();
      bodies.push(body);
      return HttpResponse.json(body);
    }),
  );
  return bodies;
}

describe("ZonesScreen", () => {
  it("draws every zone it loads, as it was loaded", async () => {
    await open_screen();

    for (const zone of example_zones()) {
      expect(points_of(zone.zone_id)).toBe(zone.polygon.map(([x, y]) => `${x},${y}`).join(" "));
    }
    expect(screen.getAllByText("Serving area").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Washing area").length).toBeGreaterThan(0);
  });

  it("labels a bin slot with its bin from the state", async () => {
    const source = source_with((tables) => {
      tables.entity = [
        { entity_id: "bin-1", kind: "bin", label: "satay sauce", origin: "shift_start", first_seen_t_ms: 0 },
      ];
      tables.bin_profile = [{ entity_id: "bin-1", probabilities: { peanut: 0.98 }, unknown: false, source: "manual" }];
    });

    await open_screen(source);

    expect(screen.getAllByText("bin-1 · satay sauce · Peanuts").length).toBeGreaterThan(0);
  });

  it("names a new bin with the next free slot-n", async () => {
    const user = userEvent.setup();
    await open_screen();

    await user.click(screen.getByRole("button", { name: "Bin" }));
    drag(frame(), [100, 300], [300, 450]);
    expect(points_of("slot-4")).toBe("0.1,0.6 0.3,0.6 0.3,0.9 0.1,0.9");

    // A freed number is used again, so the new bin is bin-2 again.
    await user.click(screen.getByRole("button", { name: "Delete slot-2" }));
    await user.click(screen.getByRole("button", { name: "Bin" }));
    drag(frame(), [700, 300], [800, 400]);
    expect(points_of("slot-2")).toBe("0.7,0.6 0.8,0.6 0.8,0.8 0.7,0.8");
  });

  it("names other new zones after their kind", async () => {
    const user = userEvent.setup();
    await open_screen();

    await user.click(screen.getByRole("button", { name: "Washing area" }));
    drag(frame(), [300, 400], [400, 480]);

    expect(screen.getByLabelText("zone wash-2")).toBeInTheDocument();
  });

  it("moves a zone by dragging it, and resizes it by a corner", async () => {
    const user = userEvent.setup();
    await open_screen();

    drag(screen.getByLabelText("zone slot-1"), [100, 75], [200, 125]);
    expect(points_of("slot-1")).toBe("0.14,0.15 0.3,0.15 0.3,0.35 0.14,0.35");

    await user.click(screen.getByRole("button", { name: /^slot-1/ }));
    drag(screen.getByLabelText("resize slot-1 from corner 3"), [300, 175], [400, 250]);
    expect(points_of("slot-1")).toBe("0.14,0.15 0.4,0.15 0.4,0.5 0.14,0.5");
  });

  it("saves the whole layout with PUT /zones", async () => {
    const user = userEvent.setup();
    const bodies = record_saves();
    await open_screen();

    await user.click(screen.getByRole("button", { name: "Serving area" }));
    drag(frame(), [800, 50], [950, 200]);
    await user.click(screen.getByRole("button", { name: "Delete pass" }));
    await user.click(screen.getByRole("button", { name: "SAVE" }));

    expect(
      await screen.findByText(
        "Saved. The kitchen uses this layout from the next frame. A new washing area starts washing after the runner restarts.",
      ),
    ).toBeInTheDocument();
    const kept = example_zones().filter((zone) => zone.zone_id !== "pass");
    const new_pass = {
      zone_id: "pass-2",
      kind: "pass",
      polygon: [
        [0.8, 0.1],
        [0.95, 0.1],
        [0.95, 0.4],
        [0.8, 0.4],
      ],
    };
    expect(bodies).toEqual([{ zones: [...kept, new_pass] }]);
    expect(screen.getByRole("button", { name: "SAVE" })).toBeDisabled();
  });

  it("shows vision's message for a refused layout and keeps the edits", async () => {
    const user = userEvent.setup();
    server.use(
      http.put(`${TEST_VISION_URL}/zones`, () =>
        HttpResponse.json(
          { error: { code: "bad_zones", message: "zone 'slot-4': polygon edges cross (corners out of order?)" } },
          { status: 422 },
        ),
      ),
    );
    await open_screen();

    await user.click(screen.getByRole("button", { name: "Bin" }));
    drag(frame(), [100, 300], [300, 450]);
    await user.click(screen.getByRole("button", { name: "SAVE" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Not saved. zone 'slot-4': polygon edges cross (corners out of order?)",
    );
    expect(points_of("slot-4")).toBe("0.1,0.6 0.3,0.6 0.3,0.9 0.1,0.9");
    expect(screen.getByText(/NOT SAVED/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SAVE" })).toBeEnabled();
  });

  it("reverts by loading the layout again with GET /zones", async () => {
    const user = userEvent.setup();
    let gets = 0;
    server.use(
      http.get(`${TEST_VISION_URL}/zones`, () => {
        gets += 1;
        return HttpResponse.json({ zones: example_zones() });
      }),
    );
    await open_screen();

    await user.click(screen.getByRole("button", { name: "Delete slot-1" }));
    expect(screen.queryByLabelText("zone slot-1")).toBeNull();
    await user.click(screen.getByRole("button", { name: "REVERT" }));

    expect(await screen.findByLabelText("zone slot-1")).toBeInTheDocument();
    expect(gets).toBe(2);
    expect(screen.getByRole("button", { name: "SAVE" })).toBeDisabled();
  });

  it("can't save before a layout has loaded, so a failed load never wipes the kitchen's zones", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${TEST_VISION_URL}/zones`, () =>
        HttpResponse.json({ error: { code: "vision_down", message: "Vision isn't running." } }, { status: 503 }),
      ),
    );
    render_with_source(<ZonesScreen />, null);

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the layout from vision.");
    await user.click(screen.getByRole("button", { name: "Bin" }));
    drag(frame(), [100, 300], [300, 450]);

    expect(screen.getByLabelText("zone slot-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SAVE" })).toBeDisabled();
  });

  it("draws on a picture it is given instead of the stream, and says when vision has the layout", async () => {
    const user = userEvent.setup();
    const on_ready = vi.fn();
    const picture = { element: <video aria-label="Camera: overhead" />, aspect: 4 / 3 };
    render_with_source(<ZonesScreen picture={picture} on_ready={on_ready} />, null);
    await screen.findByLabelText("zone slot-1");

    expect(screen.getByLabelText("Camera: overhead")).toBeInTheDocument();
    expect(screen.queryByAltText("Live view of the counter")).toBeNull();
    expect(frame()).toHaveStyle({ aspectRatio: String(4 / 3) });
    expect(on_ready).toHaveBeenLastCalledWith(true);

    await user.click(screen.getByRole("button", { name: "Delete wash" }));
    expect(on_ready).toHaveBeenLastCalledWith(false);
  });

  it("draws on a plain 16:9 frame when the camera is offline", async () => {
    await open_screen();

    fireEvent.error(screen.getByAltText("Live view of the counter"));

    expect(
      screen.getByText(/Camera offline, trying again\. Zones are drawn on a plain 16:9 frame\./),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("zone pass")).toBeInTheDocument();
  });

  it("asks for the camera again after STREAM_RETRY_MS, with no reload", async () => {
    await open_screen();
    vi.useFakeTimers();
    fireEvent.error(screen.getByAltText("Live view of the counter"));
    expect(screen.queryByAltText("Live view of the counter")).toBeNull();

    act(() => vi.advanceTimersByTime(STREAM_RETRY_MS));
    expect(screen.getByAltText("Live view of the counter")).toHaveAttribute("src", "http://vision.test/stream.mjpg");
    expect(screen.queryByText(/Camera offline/)).toBeNull();
  });

  it("opens a new stream when vision's frames come back, without dropping a drag in progress", async () => {
    const { rerender } = await open_screen();
    // The same tree as open_screen's, so the screen keeps its state and only the health changes.
    const same_screen = () => (
      <StateSourceContext.Provider value={null}>
        <ZonesScreen />
      </StateSourceContext.Provider>
    );
    const frozen = screen.getByAltText("Live view of the counter");
    fireEvent.pointerDown(screen.getByLabelText("zone slot-1"), {
      clientX: 100,
      clientY: 75,
      pointerId: 1,
      button: 0,
      buttons: 1,
    });

    vision.health = "stale";
    rerender(same_screen());
    vision.health = "ok";
    rerender(same_screen());
    expect(screen.getByAltText("Live view of the counter")).not.toBe(frozen);

    fireEvent.pointerMove(frame(), { clientX: 200, clientY: 125, pointerId: 1, buttons: 1 });
    fireEvent.pointerUp(frame(), { clientX: 200, clientY: 125, pointerId: 1, button: 0, buttons: 0 });
    expect(points_of("slot-1")).toBe("0.14,0.15 0.3,0.15 0.3,0.35 0.14,0.35");
  });

  it("warns about a bin with no slot, but still saves", async () => {
    const user = userEvent.setup();
    const bodies = record_saves();
    const source = source_with((tables) => {
      tables.bin_profile = [
        { entity_id: "bin-1", probabilities: { peanut: 0.98, soy: 0.9 }, unknown: false, source: "manual" },
        { entity_id: "bin-9", probabilities: { milk: 0.99 }, unknown: false, source: "manual" },
      ];
    });
    await open_screen(source);

    const warnings = screen.getByRole("list", { name: "Bins with no slot" });
    expect(warnings).toHaveTextContent("bin-9 (Milk) has no slot: a bowl there will count as a clean plate.");
    expect(warnings).not.toHaveTextContent("bin-1");

    await user.click(screen.getByRole("button", { name: "Delete slot-1" }));
    expect(warnings).toHaveTextContent(
      "bin-1 (Peanuts, Soybeans) has no slot: a bowl there will count as a clean plate.",
    );

    await user.click(screen.getByRole("button", { name: "SAVE" }));
    expect(await screen.findByText(/^Saved\./)).toBeInTheDocument();
    expect(bodies).toHaveLength(1);
  });
});

describe("ZonesScreen drags", () => {
  it("starts only on a plain primary press, never a right-click or Ctrl-click", async () => {
    const user = userEvent.setup();
    await open_screen();
    const slot_1 = points_of("slot-1");

    // A right-click or Ctrl-click opens the context menu, which swallows the release.
    fireEvent.pointerDown(screen.getByLabelText("zone slot-1"), {
      clientX: 100,
      clientY: 75,
      pointerId: 1,
      button: 2,
      buttons: 2,
    });
    fireEvent.pointerMove(frame(), { clientX: 200, clientY: 125, pointerId: 1, buttons: 2 });
    fireEvent.pointerDown(screen.getByLabelText("zone slot-1"), {
      clientX: 100,
      clientY: 75,
      pointerId: 1,
      button: 0,
      buttons: 1,
      ctrlKey: true,
    });
    fireEvent.pointerMove(frame(), { clientX: 200, clientY: 125, pointerId: 1, buttons: 1 });
    expect(points_of("slot-1")).toBe(slot_1);

    await user.click(screen.getByRole("button", { name: "Bin" }));
    fireEvent.pointerDown(frame(), { clientX: 100, clientY: 300, pointerId: 1, button: 2, buttons: 2 });
    fireEvent.pointerMove(frame(), { clientX: 300, clientY: 450, pointerId: 1, buttons: 2 });
    fireEvent.pointerUp(frame(), { clientX: 300, clientY: 450, pointerId: 1, button: 2, buttons: 0 });
    expect(screen.queryByLabelText("zone slot-4")).toBeNull();
  });

  it("follows only the pointer that started it", async () => {
    await open_screen();
    const slot_2 = points_of("slot-2");

    fireEvent.pointerDown(screen.getByLabelText("zone slot-1"), {
      clientX: 100,
      clientY: 75,
      pointerId: 1,
      button: 0,
      buttons: 1,
    });
    // A second finger can't steer, end or replace the drag.
    fireEvent.pointerMove(frame(), { clientX: 500, clientY: 400, pointerId: 2, buttons: 1 });
    fireEvent.pointerDown(screen.getByLabelText("zone slot-2"), {
      clientX: 300,
      clientY: 75,
      pointerId: 2,
      button: 0,
      buttons: 1,
    });
    fireEvent.pointerUp(frame(), { clientX: 500, clientY: 400, pointerId: 2, button: 0, buttons: 0 });
    expect(points_of("slot-1")).toBe("0.04,0.05 0.2,0.05 0.2,0.25 0.04,0.25");
    expect(points_of("slot-2")).toBe(slot_2);

    fireEvent.pointerMove(frame(), { clientX: 200, clientY: 125, pointerId: 1, buttons: 1 });
    expect(points_of("slot-1")).toBe("0.14,0.15 0.3,0.15 0.3,0.35 0.14,0.35");
  });

  it("ends when a move comes with no button held, so a lost release can't drag the zone around", async () => {
    await open_screen();

    fireEvent.pointerDown(screen.getByLabelText("zone slot-1"), {
      clientX: 100,
      clientY: 75,
      pointerId: 1,
      button: 0,
      buttons: 1,
    });
    // The release never arrives; the next move has no button held.
    fireEvent.pointerMove(frame(), { clientX: 200, clientY: 125, pointerId: 1, buttons: 0 });
    fireEvent.pointerMove(frame(), { clientX: 300, clientY: 175, pointerId: 1, buttons: 1 });

    expect(points_of("slot-1")).toBe("0.04,0.05 0.2,0.05 0.2,0.25 0.04,0.25");
  });
});
