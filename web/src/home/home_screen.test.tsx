import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { HomeScreen } from "./home_screen";

describe("HomeScreen sources", () => {
  it("marks the live camera as to be implemented and can't connect it", () => {
    render(<HomeScreen />);
    expect(screen.getByRole("button", { name: "CONNECT CAMERA" })).toBeDisabled();
    expect(screen.getByText(/Live camera feed\./)).toBeInTheDocument();
  });

  it("labels a picked video as a replay, not a live plate", async () => {
    const user = userEvent.setup();
    render(<HomeScreen />);
    const video = new File(["frames"], "prep-line-1.mp4", { type: "video/mp4" });
    await user.upload(screen.getByLabelText(/STAPLE VIDEO HERE/), video);

    expect(screen.getByText("prep-line-1.mp4")).toBeInTheDocument();
    expect(screen.getByText("** REPLAY · NOT A LIVE PLATE **")).toBeInTheDocument();
  });
});
