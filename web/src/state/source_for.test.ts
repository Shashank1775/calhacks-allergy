import { afterEach, describe, expect, it, vi } from "vitest";
import { FakeStateSource } from "./fake_state_source";
import { LocalStateSource } from "./local_state_source";
import { scenario_names, state_source_for } from "./source_for";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("safety: fail closed when picking the state source", () => {
  it.each([
    [undefined, undefined, undefined],
    ["", "knife-carries-peanut-to-plate", undefined],
    ["scenaro", "knife-carries-peanut-to-plate", undefined],
    ["Scenario", "knife-carries-peanut-to-plate", undefined],
    ["SCENARIO", "knife-carries-peanut-to-plate", undefined],
    ["scenario", undefined, undefined],
    ["scenario", "", undefined],
    ["scenario", "no-such-scenario", undefined],
    ["spacetime", undefined, undefined],
    ["local", undefined, undefined],
    ["local", undefined, ""],
    ["local", undefined, "not a url"],
    ["local", undefined, "ftp://x"],
    ["Local", undefined, "http://localhost:8001"],
    ["LOCAL", undefined, "http://localhost:8001"],
  ])(
    "returns no source for WEB_STATE_SOURCE=%s WEB_SCENARIO=%s WEB_STATE_URL=%s",
    (source_name, scenario_name, state_url) => {
      expect(state_source_for(source_name, scenario_name, state_url)).toBeNull();
    },
  );

  it("returns a replay only when asked for by exact name", () => {
    const source = state_source_for("scenario", "knife-carries-peanut-to-plate", undefined);
    expect(source).toBeInstanceOf(FakeStateSource);
    expect(source?.is_replay).toBe(true);
  });

  it("returns the local runner, marked as a replay, for an http URL", () => {
    const source = state_source_for("local", undefined, "http://localhost:8001");
    expect(source).toBeInstanceOf(LocalStateSource);
    expect(source?.is_replay).toBe(true);
  });

  it("finds every golden scenario", () => {
    expect(scenario_names().length).toBeGreaterThanOrEqual(6);
  });
});

describe("state_source_for local", () => {
  it("strips one trailing slash, so it polls <url>/state", async () => {
    const fetch_spy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    const source = state_source_for("local", undefined, "http://localhost:8001/") as LocalStateSource;

    await source.poll_once();
    expect(fetch_spy).toHaveBeenCalledWith("http://localhost:8001/state", expect.anything());
  });
});
