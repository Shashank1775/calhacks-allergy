import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { live_replay, render_with_source } from "../test_utils";
import { ExpoScreen } from "./expo_screen";

describe("ExpoScreen on a replay", () => {
  it("holds the peanut plate and shows the chain", () => {
    const source = live_replay("knife-carries-peanut-to-plate");
    render_with_source(<ExpoScreen />, source);
    act(() => source.advance_to(Number.POSITIVE_INFINITY));

    const plate = screen.getByRole("article", { name: "Plate plate-t1" });
    expect(within(plate).getByText("HOLD")).toBeInTheDocument();
    expect(within(plate).getByText("tool-knife-1 → plate-t1")).toBeInTheDocument();
  });

  it("safety: an amended ticket shows RECHECKING, never the old PASS", () => {
    const source = live_replay("late-amendment-adds-dairy");
    render_with_source(<ExpoScreen />, source);

    act(() => source.advance_to(3500));
    expect(within(screen.getByRole("article", { name: "Plate plate-t5" })).getByText("PASS")).toBeInTheDocument();

    act(() => source.advance_to(4100));
    const plate = screen.getByRole("article", { name: "Plate plate-t5" });
    expect(within(plate).getByText("RECHECKING")).toBeInTheDocument();
    expect(within(plate).queryByText("PASS")).toBeNull();

    act(() => source.advance_to(Number.POSITIVE_INFINITY));
    expect(within(screen.getByRole("article", { name: "Plate plate-t5" })).getByText("HOLD")).toBeInTheDocument();
  });

  it("safety: a passing plate loses PASS when the feed drops", () => {
    const source = live_replay("clean-plate-from-stack");
    render_with_source(<ExpoScreen />, source);
    act(() => source.advance_to(Number.POSITIVE_INFINITY));
    expect(screen.getByText("PASS")).toBeInTheDocument();

    act(() => source.stop());
    expect(screen.queryByText("PASS")).toBeNull();
    expect(screen.getAllByText("FEED LOST").length).toBeGreaterThan(0);
  });
});
