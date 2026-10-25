import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App, screen_for_path } from "./app";
import { render_with_source } from "./test_utils";

describe("screen_for_path", () => {
  it.each([
    ["/tablet", "tablet"],
    ["/tablet/", "tablet"],
    ["/expo", "expo"],
    ["/summary", "summary"],
    ["/zones", "zones"],
    ["/", "home"],
    ["/chef", "home"],
    ["/typo", "home"],
  ])("maps %s to %s", (path, expected) => {
    expect(screen_for_path(path)).toBe(expected);
  });
});

describe("safety: with no state source every screen shows FEED LOST", () => {
  it.each(["/", "/tablet", "/expo"])("%s shows FEED LOST and no PASS", (path) => {
    render_with_source(<App path={path} />, null);
    expect(screen.getByRole("status")).toHaveTextContent("FEED LOST");
    expect(screen.queryByText("PASS")).toBeNull();
  });
});

describe("/summary", () => {
  it("shows the recorded-video banner instead of the live feed's, and never PASS", () => {
    render_with_source(<App path="/summary" />, null);
    expect(screen.getByRole("note")).toHaveTextContent(
      "RECORDED VIDEO · NOT THE LIVE KITCHEN · NOT A PASS/HOLD VERDICT",
    );
    expect(screen.queryByText(/FEED LOST/)).toBeNull();
    expect(screen.queryByText("PASS")).toBeNull();
    expect(screen.getByRole("link", { name: "SUMMARY" })).toHaveAttribute("aria-current", "page");
  });
});
