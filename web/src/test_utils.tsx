/** Test helpers: render a screen with (or without) a state source. */
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
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
