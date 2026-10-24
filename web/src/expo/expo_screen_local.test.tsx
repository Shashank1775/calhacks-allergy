import { act, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw/http";
import { describe, expect, it } from "vitest";
import hold_peanut from "../../../contracts/fixtures/valid/verdict/hold-peanut.json";
import ticket_t7 from "../../../contracts/fixtures/valid/ticket/peanut-anaphylactic.json";
import { server } from "../api/mocks/server";
import { App } from "../app";
import { LocalStateSource } from "../state/local_state_source";
import { render_with_source } from "../test_utils";

const RUNNER_URL = "http://runner.test";

/** The runner's GET /state with plate-t7 held for peanut. */
const SNAPSHOT = {
  scenario_id: "knife-carries-peanut-to-plate",
  t_ms: 190100,
  tables: {
    allergen: [],
    zone: [],
    ticket: [ticket_t7],
    bin_profile: [],
    entity: [],
    entity_state: [],
    plate_binding: [{ plate_id: "plate-t7", ticket_id: "t7", t_ms: 150000 }],
    interaction: [],
    verdict: [hold_peanut],
  },
};

describe("Expo board on the local runner", () => {
  it("safety: shows the held plate under a REPLAY banner, then FEED LOST when the runner goes away", async () => {
    server.use(http.get(`${RUNNER_URL}/state`, () => HttpResponse.json(SNAPSHOT)));
    const source = new LocalStateSource(RUNNER_URL);
    render_with_source(<App path="/expo" />, source);

    await act(() => source.poll_once());
    expect(screen.getByRole("status")).toHaveTextContent("REPLAY · local runner · knife-carries-peanut-to-plate");
    expect(within(screen.getByRole("article", { name: "Plate plate-t7" })).getByText("HOLD")).toBeInTheDocument();

    server.use(http.get(`${RUNNER_URL}/state`, () => HttpResponse.error()));
    await act(() => source.poll_once());
    expect(screen.getByRole("status")).toHaveTextContent("FEED LOST");
    expect(within(screen.getByRole("article", { name: "Plate plate-t7" })).getByText("FEED LOST")).toBeInTheDocument();
  });
});
