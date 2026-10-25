import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { empty_tables, type StateSource, type Tables } from "../state/state_source";
import { render_with_source } from "../test_utils";
import type { EntityState } from "../types/TEMPORARY";
import { CameraPanel, fast_boxes, risk_of, TRACK_LOST_MS, tracked_now } from "./camera_panel";

function state(
  entity_id: string,
  box: EntityState["box"],
  probabilities: Record<string, number>,
  unknown = false,
  t_ms = 1,
): EntityState {
  return {
    state_id: entity_id,
    entity_id,
    version: 1,
    t_ms,
    zone: null,
    box,
    probabilities,
    unknown,
    caused_by: null,
  };
}

/** A live source holding just these entity states. */
function source_with(states: EntityState[]): StateSource {
  const tables: Tables = { ...empty_tables(), entity_state: states };
  return {
    label: "test",
    is_replay: false,
    rows: (table) => tables[table],
    feed_status: () => "live",
    subscribe: () => () => {},
    start: () => {},
  };
}

afterEach(() => vi.unstubAllEnvs());

describe("CameraPanel", () => {
  it("in the fast view shows vision's stream with the detector's boxes drawn in, and no entity boxes", () => {
    vi.stubEnv("WEB_FAST_BOXES", "true");
    const { container } = render_with_source(
      <CameraPanel />,
      source_with([state("tool-knife-1", [0.1, 0.1, 0.2, 0.3], { peanut: 0.9 })]),
    );

    expect(screen.getByAltText("Live view of the counter")).toHaveAttribute(
      "src",
      "http://vision.test/stream.mjpg?boxes=true",
    );
    expect(container.querySelectorAll(".entity_box")).toHaveLength(0);
    expect(screen.getByText("CAM-01 · 1 tracked")).toBeInTheDocument(); // the backend still tracks it
  });

  it("is in the fast view unless WEB_FAST_BOXES says false", () => {
    vi.stubEnv("WEB_FAST_BOXES", "");
    expect(fast_boxes()).toBe(true);
    vi.stubEnv("WEB_FAST_BOXES", "false");
    expect(fast_boxes()).toBe(false);
    vi.stubEnv("WEB_FAST_BOXES", "0");
    expect(fast_boxes()).toBe(false);
  });

  it("shows vision's stream with each entity's box colored by risk", () => {
    render_with_source(
      <CameraPanel />,
      source_with([
        state("tool-knife-1", [0.1, 0.1, 0.2, 0.3], { peanut: 0.9 }),
        state("tool-spoon-9", [0.4, 0.4, 0.5, 0.6], {}, true),
        state("plate-t1", [0.6, 0.5, 0.8, 0.8], {}),
        state("hand-p1-left", null, {}),
      ]),
    );

    expect(screen.getByAltText("Live view of the counter")).toHaveAttribute("src", "http://vision.test/stream.mjpg");
    expect(screen.getByLabelText("tool-knife-1: Peanuts")).toHaveClass("risk_high");
    expect(screen.getByLabelText("tool-spoon-9: unknown history")).toHaveClass("risk_unknown");
    expect(screen.getByLabelText("plate-t1: clean")).toHaveClass("risk_clean");
    expect(screen.queryByLabelText(/hand-p1-left/)).toBeNull();
  });

  it("says the camera is offline when the stream fails, without touching verdicts", () => {
    render_with_source(<CameraPanel />, source_with([]));
    fireEvent.error(screen.getByAltText("Live view of the counter"));
    expect(screen.getByText(/Camera offline/)).toBeInTheDocument();
  });

  it("lists and draws only entities tracked now, and counts them", () => {
    const { container } = render_with_source(
      <CameraPanel />,
      source_with([
        state("tool-ghost-1", [0.1, 0.1, 0.2, 0.3], {}, false, 10_000),
        state("tool-knife-2", [0.4, 0.4, 0.5, 0.6], { peanut: 0.9 }, false, 20_000),
        state("plate-t1", [0.6, 0.5, 0.8, 0.8], {}, false, 20_500),
      ]),
    );

    expect(screen.getByText("CAM-01 · 2 tracked")).toBeInTheDocument();
    expect(container.querySelectorAll(".entity_box")).toHaveLength(2);
    expect(screen.getByLabelText("tool-knife-2: Peanuts")).toBeInTheDocument();
    expect(screen.getByLabelText("plate-t1: clean")).toBeInTheDocument();
    expect(screen.queryByLabelText(/tool-ghost-1/)).toBeNull();
  });

  it("shows the empty view when there are no states", () => {
    const { container } = render_with_source(<CameraPanel />, source_with([]));

    expect(screen.getByText("CAM-01 · 0 tracked")).toBeInTheDocument();
    expect(screen.getByAltText("Live view of the counter")).toBeInTheDocument();
    expect(container.querySelectorAll(".entity_box")).toHaveLength(0);
  });

  it("treats unknown history as the highest risk", () => {
    expect(risk_of(state("x", null, { milk: 0.1 }, true))).toBe("unknown");
    expect(risk_of(state("x", null, { milk: 0.1 }))).toBe("some");
  });
});

describe("tracked_now", () => {
  const box: EntityState["box"] = [0.1, 0.1, 0.2, 0.2];
  const ids = (states: EntityState[]) => tracked_now(states).map((entry) => entry.entity_id);

  it("drops an entity last seen more than TRACK_LOST_MS before the newest state", () => {
    const newest = 50_000;
    const lost = state("tool-lost-1", box, {}, false, newest - TRACK_LOST_MS - 1);
    expect(ids([lost, state("plate-t1", box, {}, false, newest)])).toEqual(["plate-t1"]);
  });

  it("keeps an entity seen within TRACK_LOST_MS of the newest state, up to the limit", () => {
    const newest = 50_000;
    const just_inside = state("tool-a-1", box, {}, false, newest - TRACK_LOST_MS);
    const recent = state("tool-b-2", box, {}, false, newest - 200);
    const current = state("plate-t1", box, {}, false, newest);
    expect(ids([just_inside, recent, current])).toEqual(["tool-a-1", "tool-b-2", "plate-t1"]);
  });

  it("judges each entity by its latest state, not by older rows", () => {
    const rows = [
      state("tool-knife-2", box, {}, false, 1_000),
      state("tool-knife-2", box, { peanut: 0.9 }, false, 20_000),
      state("plate-t1", box, {}, false, 20_000),
    ];
    const tracked = tracked_now(rows);
    expect(tracked.map((entry) => entry.entity_id)).toEqual(["tool-knife-2", "plate-t1"]);
    expect(tracked[0].probabilities).toEqual({ peanut: 0.9 });
  });

  it("takes the newest frame as now even when that entity has no box", () => {
    const rows = [state("tool-a-1", box, {}, false, 20_000), state("hand-p1-left", null, {}, false, 22_000)];
    expect(ids(rows)).toEqual([]);
  });

  it("returns nothing for no states", () => {
    expect(tracked_now([])).toEqual([]);
  });
});
