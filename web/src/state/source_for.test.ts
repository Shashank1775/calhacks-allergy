import { describe, expect, it } from "vitest";
import { FakeStateSource } from "./fake_state_source";
import { scenario_names, state_source_for } from "./source_for";

describe("safety: fail closed when picking the state source", () => {
  it.each([
    [undefined, undefined],
    ["", "knife-carries-peanut-to-plate"],
    ["scenaro", "knife-carries-peanut-to-plate"],
    ["Scenario", "knife-carries-peanut-to-plate"],
    ["SCENARIO", "knife-carries-peanut-to-plate"],
    ["scenario", undefined],
    ["scenario", ""],
    ["scenario", "no-such-scenario"],
    ["spacetime", undefined],
  ])("returns no source for WEB_STATE_SOURCE=%s WEB_SCENARIO=%s", (source_name, scenario_name) => {
    expect(state_source_for(source_name, scenario_name)).toBeNull();
  });

  it("returns a replay only when asked for by exact name", () => {
    const source = state_source_for("scenario", "knife-carries-peanut-to-plate");
    expect(source).toBeInstanceOf(FakeStateSource);
    expect(source?.is_replay).toBe(true);
  });

  it("finds every golden scenario", () => {
    expect(scenario_names().length).toBeGreaterThanOrEqual(6);
  });
});
