/**
 * Picks the state source from build-time settings, never from the URL.
 *
 * Reads: WEB_STATE_SOURCE and WEB_SCENARIO (passed in by main.tsx).
 * Rules: fail closed. Anything other than an exact, known value returns no
 * source, and with no source every screen shows FEED LOST.
 */
import type { Scenario } from "../types/TEMPORARY";
import { FakeStateSource } from "./fake_state_source";
import type { StateSource } from "./state_source";

const SCENARIO_FILES = import.meta.glob<Scenario>("../../../contracts/scenarios/*.json", {
  eager: true,
  import: "default",
});

/** Golden scenario names, e.g. "knife-carries-peanut-to-plate". */
export function scenario_names(): string[] {
  return Object.keys(SCENARIO_FILES).map((path) =>
    path
      .split("/")
      .pop()!
      .replace(/\.json$/, ""),
  );
}

export function scenario_by_name(name: string): Scenario | undefined {
  const path = Object.keys(SCENARIO_FILES).find((candidate) => candidate.endsWith(`/${name}.json`));
  return path ? SCENARIO_FILES[path] : undefined;
}

/**
 * Returns the source the build asked for, or null.
 * "spacetime" waits on the partner's TypeScript bindings, so it counts as unknown for now.
 */
export function state_source_for(
  source_name: string | undefined,
  scenario_name: string | undefined,
): StateSource | null {
  if (source_name === "scenario" && scenario_name) {
    const scenario = scenario_by_name(scenario_name);
    return scenario ? new FakeStateSource(scenario) : null;
  }
  return null;
}
