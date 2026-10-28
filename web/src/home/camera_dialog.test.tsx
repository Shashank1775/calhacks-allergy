import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { install_fake_cameras } from "../test_utils";
import { CameraDialog } from "./camera_dialog";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function open_dialog() {
  const on_use = vi.fn();
  const on_cancel = vi.fn();
  const view = render(<CameraDialog on_use={on_use} on_cancel={on_cancel} />);
  return { on_use, on_cancel, view };
}

describe("CameraDialog", () => {
  it("lists the computer's cameras, not its microphones, and previews the default one", async () => {
    const { getUserMedia } = install_fake_cameras();
    open_dialog();

    const picker = await screen.findByRole("combobox", { name: "CAMERA" });
    const names = Array.from((picker as HTMLSelectElement).options).map((option) => option.text);
    expect(names).toEqual(["FaceTime HD Camera", "Overhead webcam"]);
    expect(picker).toHaveValue("cam-a");
    expect(getUserMedia).toHaveBeenCalledWith({ video: true });
    expect(screen.getByRole("button", { name: "USE THIS CAMERA" })).toBeEnabled();
  });

  it("opens the camera picked in the list and turns the one before it off", async () => {
    const user = userEvent.setup();
    const { getUserMedia, streams } = install_fake_cameras();
    open_dialog();

    await user.selectOptions(await screen.findByRole("combobox", { name: "CAMERA" }), "Overhead webcam");

    await waitFor(() => expect(getUserMedia).toHaveBeenLastCalledWith({ video: { deviceId: { exact: "cam-b" } } }));
    expect(streams[0].track.stop).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "CAMERA" })).toHaveValue("cam-b"));
  });

  it("hands over the open camera, and closing the popup then leaves it on", async () => {
    const user = userEvent.setup();
    const { streams } = install_fake_cameras();
    const { on_use, view } = open_dialog();
    await screen.findByRole("combobox", { name: "CAMERA" });

    await user.click(screen.getByRole("button", { name: "USE THIS CAMERA" }));
    view.unmount();

    expect(on_use).toHaveBeenCalledWith(streams[0], "FaceTime HD Camera");
    expect(streams[0].track.stop).not.toHaveBeenCalled();
  });

  it("turns the camera off when cancelled", async () => {
    const user = userEvent.setup();
    const { streams } = install_fake_cameras();
    const { on_cancel, view } = open_dialog();
    await screen.findByRole("combobox", { name: "CAMERA" });

    await user.click(screen.getByRole("button", { name: "CANCEL" }));
    view.unmount();

    expect(on_cancel).toHaveBeenCalled();
    expect(streams[0].track.stop).toHaveBeenCalled();
  });

  it("says how to allow a refused camera, and can't use one", async () => {
    install_fake_cameras({ refuse: "NotAllowedError" });
    open_dialog();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Camera access was refused/);
    expect(screen.getByRole("button", { name: "USE THIS CAMERA" })).toBeDisabled();
  });

  it("says when the page can't open a camera at all", async () => {
    vi.stubGlobal("navigator", { ...navigator, mediaDevices: undefined });
    open_dialog();

    expect(await screen.findByRole("alert")).toHaveTextContent(/http:\/\/localhost or over HTTPS/);
  });
});
