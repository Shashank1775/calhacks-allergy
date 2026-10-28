import { act, fireEvent, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw/http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { example_zones } from "../api/mocks/handlers";
import { server, TEST_VISION_URL } from "../api/mocks/server";
import { empty_tables, type StateSource } from "../state/state_source";
import { render_with_source } from "../test_utils";
import { LAYOUT_RETRY_MS, ZonesScreen } from "./zones_screen";

/** Vision down, then up: GET /zones fails while `vision.up` is false, and every request is counted. */
const vision = { up: false, gets: 0, zones: example_zones() };

beforeEach(() => {
  // The frame is 1000 × 500 px, so a pointer at (100, 50) is the point (0.1, 0.1).
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 1000, 500));
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vision.up = false;
  vision.gets = 0;
  vision.zones = example_zones();
  server.use(
    http.get(`${TEST_VISION_URL}/zones`, () => {
      vision.gets += 1;
      return vision.up ? HttpResponse.json({ zones: vision.zones }) : HttpResponse.error();
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** A state with bin-1 in it, so a loaded layout with no slot for it shows the "no slot" warning. */
function source_with_bin(): StateSource {
  const tables = empty_tables();
  tables.bin_profile = [{ entity_id: "bin-1", probabilities: { peanut: 0.98 }, unknown: false, source: "manual" }];
  return {
    label: "test",
    is_replay: true,
    rows: (table) => tables[table],
    feed_status: () => "live",
    subscribe: () => () => {},
    start: () => {},
  };
}

async function wait_one_retry() {
  await act(() => vi.advanceTimersByTimeAsync(LAYOUT_RETRY_MS));
}

/**
 * Runs the clock in small steps until vision has had `count` requests. The next retry is timed from when the last
 * answer was handled, which a test can't see, so one big jump could land before that timer even exists.
 */
async function run_until_gets(count: number) {
  // Two retry periods at most; vi.waitFor can't be used here, since its own timeout runs on the faked clock.
  for (let step = 0; step < 12 && vision.gets < count; step += 1) {
    await act(() => vi.advanceTimersByTimeAsync(LAYOUT_RETRY_MS / 6));
  }
  expect(vision.gets).toBe(count);
}

describe("ZonesScreen opened while vision is down", () => {
  it("says it's trying again, and loads the layout by itself once vision answers", async () => {
    render_with_source(<ZonesScreen />, null);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't load the layout from vision.*Trying again\./);

    vision.up = true;
    await wait_one_retry();

    expect(await screen.findByLabelText("zone slot-1")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps asking every LAYOUT_RETRY_MS while vision stays down", async () => {
    render_with_source(<ZonesScreen />, null);
    await screen.findByRole("alert");
    expect(vision.gets).toBe(1);

    await run_until_gets(2);
    await run_until_gets(3);
    expect(screen.getByRole("alert")).toHaveTextContent(/Trying again\./);
  });

  it("stops asking once a layout has loaded, even an empty one", async () => {
    vision.up = true;
    vision.zones = [];
    render_with_source(<ZonesScreen />, source_with_bin());
    // The warning shows only once a layout has loaded.
    await screen.findByLabelText("Bins with no slot");

    await wait_one_retry();
    await wait_one_retry();
    expect(vision.gets).toBe(1);
  });

  it("never replaces a zone drawn before any layout loaded", async () => {
    render_with_source(<ZonesScreen />, null);
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: "Bin" }));
    const frame = screen.getByLabelText("Counter picture with zones");
    fireEvent.pointerDown(frame, { clientX: 100, clientY: 50, pointerId: 1, button: 0, buttons: 1 });
    fireEvent.pointerMove(frame, { clientX: 300, clientY: 150, pointerId: 1, buttons: 1 });
    fireEvent.pointerUp(frame, { clientX: 300, clientY: 150, pointerId: 1, button: 0, buttons: 0 });
    const drawn = screen.getByLabelText("zone slot-1").getAttribute("points");

    vision.up = true;
    await wait_one_retry();
    await wait_one_retry();

    expect(vision.gets).toBe(1);
    expect(screen.getByLabelText("zone slot-1")).toHaveAttribute("points", drawn!);
    expect(screen.queryByLabelText("zone pass")).toBeNull();
    expect(screen.getByRole("alert")).not.toHaveTextContent(/Trying again/);
  });
});
