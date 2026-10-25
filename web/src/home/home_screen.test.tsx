import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { install_fake_cameras } from "../test_utils";
import { HomeScreen } from "./home_screen";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function use_a_video(user: ReturnType<typeof userEvent.setup>) {
  const video = new File(["frames"], "prep-line-1.mp4", { type: "video/mp4" });
  await user.upload(screen.getByLabelText(/STAPLE VIDEO HERE/), video);
  await user.click(screen.getByRole("button", { name: "USE THIS VIDEO" }));
}

describe("HomeScreen sources", () => {
  it("labels a picked video as a replay, not a live plate", async () => {
    const user = userEvent.setup();
    render(<HomeScreen />);
    const video = new File(["frames"], "prep-line-1.mp4", { type: "video/mp4" });
    await user.upload(screen.getByLabelText(/STAPLE VIDEO HERE/), video);

    expect(screen.getByText("prep-line-1.mp4")).toBeInTheDocument();
    expect(screen.getByText("** REPLAY · NOT A LIVE PLATE **")).toBeInTheDocument();
  });

  it("adds a camera from the popup and takes it to the layout step", async () => {
    const user = userEvent.setup();
    const { streams } = install_fake_cameras();
    render(<HomeScreen />);

    await user.click(screen.getByRole("button", { name: "ADD CAMERA" }));
    expect(screen.getByRole("dialog", { name: "Add a camera" })).toBeInTheDocument();
    await screen.findByRole("combobox", { name: "CAMERA" });
    await user.click(screen.getByRole("button", { name: "USE THIS CAMERA" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Box the kitchen" })).toBeInTheDocument();
    expect(screen.getByText(/STEP 2 OF 3 · FaceTime HD Camera/)).toBeInTheDocument();
    expect(screen.getByLabelText("Camera: FaceTime HD Camera")).toBeInTheDocument();
    expect(streams[0].track.stop).not.toHaveBeenCalled();
  });

  it("turns the camera off when the source is changed", async () => {
    const user = userEvent.setup();
    const { streams } = install_fake_cameras();
    render(<HomeScreen />);
    await user.click(screen.getByRole("button", { name: "ADD CAMERA" }));
    await screen.findByRole("combobox", { name: "CAMERA" });
    await user.click(screen.getByRole("button", { name: "USE THIS CAMERA" }));

    await user.click(screen.getByRole("button", { name: "CHANGE SOURCE" }));

    expect(streams[0].track.stop).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "ADD CAMERA" })).toBeInTheDocument();
  });
});

describe("HomeScreen kitchen run", () => {
  it("safety: START stays off until vision has exactly the layout on screen", async () => {
    const user = userEvent.setup();
    render(<HomeScreen />);
    await use_a_video(user);

    const start = screen.getByRole("button", { name: "START" });
    expect(start).toBeDisabled();
    await waitFor(() => expect(start).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "Delete wash" }));
    expect(start).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "SAVE" }));
    await waitFor(() => expect(start).toBeEnabled());
  });

  it("feeds the kitchen after START, with the stations a new tab away, and STOP goes back to the layout", async () => {
    const user = userEvent.setup();
    render(<HomeScreen />);
    await use_a_video(user);
    await waitFor(() => expect(screen.getByRole("button", { name: "START" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "START" }));

    expect(screen.getByRole("heading", { name: "Feeding the kitchen" })).toBeInTheDocument();
    expect(screen.getByText(/Keep this tab open/)).toBeInTheDocument();
    for (const name of ["OPEN EXPO ↗", "OPEN TABLET ↗"]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("target", "_blank");
    }

    await user.click(screen.getByRole("button", { name: "STOP" }));
    expect(screen.getByRole("heading", { name: "Box the kitchen" })).toBeInTheDocument();
    expect(screen.getByText(/The shift started with the first frame/)).toBeInTheDocument();
  });
});
