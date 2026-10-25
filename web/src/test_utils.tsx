/** Test helpers: render a screen with (or without) a state source; fake cameras for the home page. */
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { FakeStateSource } from "./state/fake_state_source";
import { StateSourceContext } from "./state/hooks";
import { scenario_by_name } from "./state/source_for";
import type { StateSource } from "./state/state_source";

export function render_with_source(ui: ReactElement, source: StateSource | null) {
  return render(<StateSourceContext.Provider value={source}>{ui}</StateSourceContext.Provider>);
}

/** A live replay of a golden scenario, not yet advanced past t = 0. */
export function live_replay(scenario_name: string): FakeStateSource {
  const scenario = scenario_by_name(scenario_name);
  if (!scenario) throw new Error(`No golden scenario named ${scenario_name}`);
  const source = new FakeStateSource(scenario);
  source.go_live();
  return source;
}

/** One camera's open stream, as getUserMedia gives it: one video track that can be stopped. */
export type FakeCameraStream = MediaStream & { track: { stop: ReturnType<typeof vi.fn>; readyState: string } };

/**
 * Fakes navigator.mediaDevices with two cameras (and a microphone, which must not be listed), and a <video> that
 * plays. jsdom has neither. Returns getUserMedia's mock and every stream it opened, in order.
 */
export function install_fake_cameras(options: { refuse?: string } = {}) {
  const streams: FakeCameraStream[] = [];
  const getUserMedia = vi.fn(async (constraints: MediaStreamConstraints) => {
    if (options.refuse) throw new DOMException("refused", options.refuse);
    const video = constraints.video as boolean | { deviceId: { exact: string } };
    const device_id = video === true ? "cam-a" : video ? video.deviceId.exact : "";
    const track = {
      stop: vi.fn(),
      clone: vi.fn(() => ({ stop: vi.fn() })),
      readyState: "live",
      getSettings: () => ({ deviceId: device_id }),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    const stream = { track, getTracks: () => [track], getVideoTracks: () => [track] } as unknown as FakeCameraStream;
    streams.push(stream);
    return stream;
  });
  const enumerateDevices = vi.fn(async () => [
    { kind: "videoinput", deviceId: "cam-a", label: "FaceTime HD Camera" },
    { kind: "videoinput", deviceId: "cam-b", label: "Overhead webcam" },
    { kind: "audioinput", deviceId: "mic-a", label: "MacBook microphone" },
  ]);
  vi.stubGlobal("navigator", { ...navigator, mediaDevices: { getUserMedia, enumerateDevices } });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  return { getUserMedia, streams };
}
