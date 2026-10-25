import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { afterEach, describe, expect, it, vi } from "vitest";
import { example_zones } from "../api/mocks/handlers";
import { server, TEST_VISION_URL } from "../api/mocks/server";
import { empty_tables, type StateSource, type Tables } from "../state/state_source";
import { render_with_source } from "../test_utils";
import type { EntityState, Zone } from "../types/TEMPORARY";
import { CameraPanel } from "./camera_panel";
import { ZONES_REFRESH_MS } from "./zones_overlay";

/** A live source with bin-1 (satay sauce, peanut) in the state, and these entity states. */
function source_with_bin(entity_state: EntityState[] = []): StateSource {
  const tables: Tables = {
    ...empty_tables(),
    entity_state,
    entity: [{ entity_id: "bin-1", kind: "bin", label: "satay sauce", origin: "shift_start", first_seen_t_ms: 0 }],
    bin_profile: [{ entity_id: "bin-1", probabilities: { peanut: 0.98 }, unknown: false, source: "manual" }],
  };
  return {
    label: "test",
    is_replay: false,
    rows: (table) => tables[table],
    feed_status: () => "live",
    subscribe: () => () => {},
    start: () => {},
  };
}

/** Counts every GET /zones and answers with this layout. */
function serve_zones(zones: () => Zone[]) {
  const calls = { count: 0 };
  server.use(
    http.get(`${TEST_VISION_URL}/zones`, () => {
      calls.count += 1;
      return HttpResponse.json({ zones: zones() });
    }),
  );
  return calls;
}

function toggle() {
  return screen.getByRole("button", { name: "ZONES" });
}

function points_of(zone_id: string) {
  return screen.getByLabelText(`zone ${zone_id}`).getAttribute("points");
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

describe("Zones on the expo camera", () => {
  it("is off by default, and the camera renders as before without fetching zones", async () => {
    const calls = serve_zones(example_zones);
    const { container } = render_with_source(<CameraPanel />, source_with_bin());

    expect(toggle()).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByLabelText("Zones on the camera")).toBeNull();
    // The frame holds just the picture, as before the toggle.
    expect(container.querySelector(".camera_frame")!.children).toHaveLength(1);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(calls.count).toBe(0);
  });

  it("shows the zones when turned on and hides them when turned off", async () => {
    render_with_source(<CameraPanel />, source_with_bin());

    await userEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByLabelText("zone pass")).toBeInTheDocument();

    await userEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByLabelText("Zones on the camera")).toBeNull();
    expect(screen.queryByLabelText("zone pass")).toBeNull();
  });

  it("draws each fetched zone at its normalized position, with its name or its bin", async () => {
    render_with_source(<CameraPanel />, source_with_bin());
    await userEvent.click(toggle());
    await screen.findByLabelText("zone slot-1");

    for (const zone of example_zones()) {
      expect(points_of(zone.zone_id)).toBe(zone.polygon.map(([x, y]) => `${x},${y}`).join(" "));
    }
    expect(screen.getByLabelText("zone pass")).toHaveClass("kind_pass");

    const slot_tag = screen.getByTitle("slot-1 · bin-1 · satay sauce · Peanuts");
    expect(slot_tag).toHaveStyle({ left: "4%", top: "5%" });
    expect(slot_tag).toHaveTextContent("slot-1 · bin-1 · satay sauce · Peanuts");
    expect(screen.getByTitle("pass · Serving area")).toHaveStyle({ left: "70%", top: "50%" });
    expect(screen.getByTitle("wash · Washing area")).toBeInTheDocument();
    expect(screen.getByTitle("slot-2 · bin-2")).toBeInTheDocument();
  });

  it("draws the zones under the entity boxes", async () => {
    vi.stubEnv("WEB_FAST_BOXES", "false");
    const knife: EntityState = {
      state_id: "s1",
      entity_id: "tool-knife-1",
      version: 1,
      t_ms: 1,
      zone: null,
      box: [0.1, 0.1, 0.2, 0.3],
      probabilities: { peanut: 0.9 },
      unknown: false,
      caused_by: null,
    };
    const { container } = render_with_source(<CameraPanel />, source_with_bin([knife]));
    await userEvent.click(toggle());
    await screen.findByLabelText("zone slot-1");

    const frame = container.querySelector(".camera_frame")!;
    expect(frame.children[0].tagName).toBe("IMG");
    expect(frame.children[1]).toHaveAttribute("aria-label", "Zones on the camera");
    expect(frame.children[2]).toHaveAttribute("aria-label", "tool-knife-1: Peanuts");
  });

  it("shows the zones in the fast view too, over the stream with the detector's boxes", async () => {
    vi.stubEnv("WEB_FAST_BOXES", "true");
    render_with_source(<CameraPanel />, source_with_bin());
    await userEvent.click(toggle());

    expect(await screen.findByLabelText("zone pass")).toBeInTheDocument();
    expect(screen.getByAltText("Live view of the counter")).toHaveAttribute(
      "src",
      "http://vision.test/stream.mjpg?boxes=true",
    );
  });

  it("says zones are unavailable and draws none when the fetch fails", async () => {
    server.use(
      http.get(`${TEST_VISION_URL}/zones`, () =>
        HttpResponse.json({ error: { code: "internal_error", message: "boom" } }, { status: 500 }),
      ),
    );
    const { container } = render_with_source(<CameraPanel />, source_with_bin());
    await userEvent.click(toggle());

    expect(await screen.findByText("Zones unavailable")).toBeInTheDocument();
    expect(container.querySelectorAll("polygon")).toHaveLength(0);
    expect(container.querySelectorAll(".zone_tag")).toHaveLength(0);
    // The camera stays up: zones are an extra on the picture, never a condition for it.
    expect(screen.getByAltText("Live view of the counter")).toBeInTheDocument();
  });

  it("fetches the zones again every few seconds, so an edit on /zones shows up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let layout = example_zones();
    serve_zones(() => layout);
    render_with_source(<CameraPanel />, source_with_bin());
    await userEvent.click(toggle());
    await screen.findByLabelText("zone pass");
    const old_points = points_of("pass");

    layout = layout.map((zone) =>
      zone.zone_id === "pass"
        ? {
            ...zone,
            polygon: [
              [0.1, 0.4],
              [0.3, 0.4],
              [0.3, 0.6],
              [0.1, 0.6],
            ],
          }
        : zone,
    );
    await act(() => vi.advanceTimersByTimeAsync(ZONES_REFRESH_MS));

    await vi.waitFor(() => expect(points_of("pass")).toBe("0.1,0.4 0.3,0.4 0.3,0.6 0.1,0.6"));
    expect(points_of("pass")).not.toBe(old_points);
  });

  it("stops fetching once turned off", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = serve_zones(example_zones);
    render_with_source(<CameraPanel />, source_with_bin());
    await userEvent.click(toggle());
    await screen.findByLabelText("zone pass");
    await userEvent.click(toggle());
    const count = calls.count;

    await act(() => vi.advanceTimersByTimeAsync(ZONES_REFRESH_MS * 3));
    expect(calls.count).toBe(count);
  });

  it("remembers the choice for the next visit", async () => {
    const first = render_with_source(<CameraPanel />, source_with_bin());
    await userEvent.click(toggle());
    first.unmount();

    render_with_source(<CameraPanel />, source_with_bin());
    expect(toggle()).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByLabelText("zone pass")).toBeInTheDocument();
  });

  it("still works, off by default, when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render_with_source(<CameraPanel />, source_with_bin());

    expect(toggle()).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle());
    expect(await screen.findByLabelText("zone pass")).toBeInTheDocument();
  });
});
