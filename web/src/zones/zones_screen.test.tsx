import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { example_zones } from "../api/mocks/handlers";
import { server, TEST_VISION_URL } from "../api/mocks/server";
import { empty_tables, type StateSource } from "../state/state_source";
import { render_with_source } from "../test_utils";
import { ZonesScreen } from "./zones_screen";

// The frame is 1000 × 500 px, so a pointer at (100, 50) is the point (0.1, 0.1).
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1000, 500));
});

async function open_screen(source: StateSource | null = null) {
  render_with_source(<ZonesScreen />, source);
  await screen.findByLabelText("zone slot-1");
}

function frame() {
  return screen.getByLabelText("Counter picture with zones");
}

function drag(start_on: Element, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(start_on, { clientX: from[0], clientY: from[1], pointerId: 1 });
  fireEvent.pointerMove(frame(), { clientX: to[0], clientY: to[1], pointerId: 1 });
  fireEvent.pointerUp(frame(), { clientX: to[0], clientY: to[1], pointerId: 1 });
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
    const tables = empty_tables();
    tables.entity = [
      { entity_id: "bin-1", kind: "bin", label: "satay sauce", origin: "shift_start", first_seen_t_ms: 0 },
    ];
    tables.bin_profile = [{ entity_id: "bin-1", probabilities: { peanut: 0.98 }, unknown: false, source: "manual" }];
    const source: StateSource = {
      label: "test",
      is_replay: true,
      rows: (table) => tables[table],
      feed_status: () => "live",
      subscribe: () => () => {},
      start: () => {},
    };

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

    expect(await screen.findByText(/^Saved\./)).toBeInTheDocument();
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

    expect(screen.getByText(/Camera offline\. Zones are drawn on a plain 16:9 frame\./)).toBeInTheDocument();
    expect(screen.getByLabelText("zone pass")).toBeInTheDocument();
  });
});
