import { http, HttpResponse } from "msw/http";
import { afterEach, describe, expect, it, vi } from "vitest";
import pass_clean from "../../../contracts/fixtures/valid/verdict/pass-clean.json";
import hold_peanut from "../../../contracts/fixtures/valid/verdict/hold-peanut.json";
import ticket_t7 from "../../../contracts/fixtures/valid/ticket/peanut-anaphylactic.json";
import scoop from "../../../contracts/fixtures/valid/interaction_event/scoop.json";
import { server } from "../api/mocks/server";
import { plate_badge } from "../badge";
import type { Ticket, Verdict } from "../types/TEMPORARY";
import { LocalStateSource, POLL_MS } from "./local_state_source";
import type { TableName } from "./state_source";

const RUNNER_URL = "http://runner.test";
const TICKET = ticket_t7 as Ticket;
const HOLD = hold_peanut as Verdict;
// The passing fixture, moved onto the same ticket and plate as the hold.
const PASS: Verdict = { ...(pass_clean as Verdict), ticket_id: "t7", plate_id: "plate-t7" };
const WEB_TABLES: TableName[] = [
  "ticket",
  "bin_profile",
  "entity",
  "entity_state",
  "plate_binding",
  "interaction",
  "verdict",
];

/** A GET /state body shaped like tools/run_local.py's, with plate-t7 at the pass. */
function snapshot(verdicts: Verdict[] = [HOLD]) {
  return {
    scenario_id: "knife-carries-peanut-to-plate",
    t_ms: 190100,
    tables: {
      allergen: [{ id: "peanut", name: "peanut", default_severity: "allergy" }],
      zone: [
        {
          zone_id: "pass",
          kind: "pass",
          polygon: [
            [0.8, 0],
            [1, 0],
            [1, 1],
            [0.8, 1],
          ],
        },
      ],
      ticket: [TICKET],
      bin_profile: [{ entity_id: "bin-4", probabilities: { peanut: 1 }, unknown: false, source: "manual" }],
      entity: [
        { entity_id: "plate-t7", kind: "plate", label: "plate for t7", origin: "shift_start", first_seen_t_ms: 0 },
      ],
      entity_state: [
        {
          state_id: "s1",
          entity_id: "plate-t7",
          version: 1,
          t_ms: 190000,
          zone: "pass",
          box: null,
          probabilities: {},
          unknown: false,
          caused_by: null,
        },
      ],
      plate_binding: [{ plate_id: "plate-t7", ticket_id: "t7", t_ms: 150000 }],
      interaction: [scoop],
      verdict: verdicts,
    },
  };
}

function runner_answers(respond: () => Response) {
  server.use(http.get(`${RUNNER_URL}/state`, respond));
}

/** A source after one good poll of `body`. */
async function live_source(body = snapshot()): Promise<LocalStateSource> {
  runner_answers(() => HttpResponse.json(body));
  const source = new LocalStateSource(RUNNER_URL);
  await source.poll_once();
  return source;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("LocalStateSource", () => {
  it("is connecting before the first poll, and a replay", () => {
    const source = new LocalStateSource(RUNNER_URL);
    expect(source.feed_status()).toBe("connecting");
    expect(source.is_replay).toBe(true);
    expect(source.label).toBe("local runner");
  });

  it("goes live with every table's rows and the scenario in its label", async () => {
    const body = snapshot();
    const source = await live_source(body);

    expect(source.feed_status()).toBe("live");
    expect(source.label).toBe("local runner · knife-carries-peanut-to-plate");
    for (const table of WEB_TABLES) expect(source.rows(table)).toEqual(body.tables[table]);
  });

  it("keeps a table's array when a poll brings the same rows, and replaces it when they change", async () => {
    const source = await live_source();
    const tickets = source.rows("ticket");
    const verdicts = source.rows("verdict");

    runner_answers(() => HttpResponse.json(snapshot([HOLD, PASS])));
    await source.poll_once();

    expect(source.rows("ticket")).toBe(tickets);
    expect(source.rows("verdict")).not.toBe(verdicts);
    expect(source.rows("verdict")).toHaveLength(2);
  });

  it("notifies listeners on a change and not when nothing changed", async () => {
    runner_answers(() => HttpResponse.json(snapshot()));
    const source = new LocalStateSource(RUNNER_URL);
    const listener = vi.fn();
    source.subscribe(listener);

    await source.poll_once();
    expect(listener).toHaveBeenCalledTimes(1);

    await source.poll_once();
    expect(listener).toHaveBeenCalledTimes(1);

    runner_answers(() => HttpResponse.json(snapshot([PASS])));
    await source.poll_once();
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("polls again POLL_MS after each poll settles, never two at once", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let finish_poll: (response: Response) => void = () => {};
    const fetch_spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => new Promise<Response>((resolve) => (finish_poll = resolve)));
    const source = new LocalStateSource(RUNNER_URL);

    source.start();
    await vi.advanceTimersByTimeAsync(POLL_MS * 5);
    expect(fetch_spy).toHaveBeenCalledTimes(1);

    finish_poll(Response.json(snapshot()));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(fetch_spy).toHaveBeenCalledTimes(2);
    expect(source.feed_status()).toBe("live");
    source.stop();
  });

  it("safety: stop() ends polling and loses the feed", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const fetch_spy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(Response.json(snapshot())));
    const source = new LocalStateSource(RUNNER_URL);
    source.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(source.feed_status()).toBe("live");

    source.stop();
    await vi.advanceTimersByTimeAsync(POLL_MS * 5);
    expect(fetch_spy).toHaveBeenCalledTimes(1);
    expect(source.feed_status()).toBe("lost");
  });

  it("safety: a poll that lands after stop() keeps the feed lost", async () => {
    let finish_poll: (response: Response) => void = () => {};
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>((resolve) => (finish_poll = resolve)));
    const source = new LocalStateSource(RUNNER_URL);

    const poll = source.poll_once();
    source.stop();
    finish_poll(Response.json(snapshot([PASS])));
    await poll;

    expect(source.feed_status()).toBe("lost");
  });
});

describe("safety: LocalStateSource fails closed", () => {
  it.each<[string, () => Response]>([
    ["a network error", () => HttpResponse.error()],
    // A good-looking body, so only the status can make the poll fail.
    ["HTTP 500", () => HttpResponse.json(snapshot([PASS]), { status: 500 })],
    ["a body that isn't JSON", () => HttpResponse.text("<html>proxy error</html>")],
    ["a body with no tables", () => HttpResponse.json({ scenario_id: "x", t_ms: 0 })],
    ["a body with no scenario id", () => HttpResponse.json({ ...snapshot(), scenario_id: undefined })],
    [
      "a body missing a table",
      () => HttpResponse.json({ ...snapshot(), tables: { ...snapshot().tables, verdict: undefined } }),
    ],
    [
      "a table that isn't an array",
      () => HttpResponse.json({ ...snapshot(), tables: { ...snapshot().tables, verdict: { v1: HOLD } } }),
    ],
  ])("goes lost on %s and keeps the last rows", async (_name, respond) => {
    const source = await live_source();
    const verdicts = source.rows("verdict");

    runner_answers(respond);
    await source.poll_once();

    expect(source.feed_status()).toBe("lost");
    expect(source.rows("verdict")).toBe(verdicts);
  });

  it.each(["TimeoutError", "AbortError"])("goes lost when the poll ends in %s", async (error_name) => {
    const source = await live_source();
    const fetch_spy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new DOMException("stopped", error_name));

    await source.poll_once();

    expect(fetch_spy).toHaveBeenCalledWith(
      `${RUNNER_URL}/state`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(source.feed_status()).toBe("lost");
  });

  it("goes from connecting to lost when the first poll fails", async () => {
    runner_answers(() => HttpResponse.error());
    const source = new LocalStateSource(RUNNER_URL);

    await source.poll_once();
    expect(source.feed_status()).toBe("lost");
  });

  it("comes back live on the next good poll", async () => {
    const source = await live_source();
    runner_answers(() => HttpResponse.error());
    await source.poll_once();
    expect(source.feed_status()).toBe("lost");

    runner_answers(() => HttpResponse.json(snapshot()));
    await source.poll_once();
    expect(source.feed_status()).toBe("live");
  });

  it.each([
    ["HOLD", HOLD, "hold"],
    ["PASS", PASS, "pass"],
  ])("a %s plate shows FEED LOST once the feed drops", async (_name, verdict, live_badge) => {
    const source = await live_source(snapshot([verdict]));
    const badge = () => plate_badge(source.feed_status(), source.rows("ticket")[0], source.rows("verdict"));
    expect(badge()).toBe(live_badge);

    runner_answers(() => HttpResponse.error());
    await source.poll_once();
    expect(badge()).toBe("feed_lost");
  });
});
