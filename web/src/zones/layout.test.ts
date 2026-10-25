import { describe, expect, it } from "vitest";
import type { Zone } from "../types/TEMPORARY";
import { is_rectangle, move_polygon, next_zone_id, rect_polygon } from "./layout";

function zone(zone_id: string, kind: Zone["kind"]): Zone {
  return { zone_id, kind, polygon: rect_polygon([0, 0], [0.1, 0.1]) };
}

describe("next_zone_id", () => {
  it("gives a bin the lowest free slot number", () => {
    expect(next_zone_id("bin_slot", [])).toBe("slot-1");
    expect(next_zone_id("bin_slot", [zone("slot-1", "bin_slot"), zone("slot-3", "bin_slot")])).toBe("slot-2");
  });

  it("names other kinds after the kind, then numbers them", () => {
    expect(next_zone_id("pass", [])).toBe("pass");
    expect(next_zone_id("wash", [zone("wash", "wash"), zone("wash-2", "wash")])).toBe("wash-3");
    expect(next_zone_id("plating_spot", [zone("plating", "plating_spot")])).toBe("plating-2");
    expect(next_zone_id("clean_stack", [])).toBe("clean-stack");
  });
});

describe("rectangles", () => {
  it("builds a rectangle clockwise from the top left, whichever way it was dragged", () => {
    expect(rect_polygon([0.5, 0.6], [0.1, 0.2])).toEqual([
      [0.1, 0.2],
      [0.5, 0.2],
      [0.5, 0.6],
      [0.1, 0.6],
    ]);
  });

  it("tells an upright rectangle from other shapes", () => {
    expect(is_rectangle(rect_polygon([0.1, 0.1], [0.3, 0.4]))).toBe(true);
    // A bow-tie: the same four corners out of order.
    const [a, b, c, d] = rect_polygon([0.1, 0.1], [0.3, 0.4]);
    expect(is_rectangle([a, c, b, d])).toBe(false);
    expect(
      is_rectangle([
        [0.7, 0.55],
        [0.96, 0.5],
        [0.96, 0.95],
        [0.74, 0.95],
      ]),
    ).toBe(false);
  });

  it("stops a moved zone at the frame's edge", () => {
    expect(move_polygon(rect_polygon([0.8, 0.8], [0.9, 0.9]), 0.5, -0.9)).toEqual(rect_polygon([0.9, 0], [1, 0.1]));
  });
});
