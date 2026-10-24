import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { empty_tables, type StateSource, type Tables } from "../state/state_source";
import { render_with_source } from "../test_utils";
import type { EntityState } from "../types/TEMPORARY";
import { CameraPanel, risk_of } from "./camera_panel";

function state(
  entity_id: string,
  box: EntityState["box"],
  probabilities: Record<string, number>,
  unknown = false,
): EntityState {
  return {
    state_id: entity_id,
    entity_id,
    version: 1,
    t_ms: 1,
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

describe("CameraPanel", () => {
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

  it("treats unknown history as the highest risk", () => {
    expect(risk_of(state("x", null, { milk: 0.1 }, true))).toBe("unknown");
    expect(risk_of(state("x", null, { milk: 0.1 }))).toBe("some");
  });
});
