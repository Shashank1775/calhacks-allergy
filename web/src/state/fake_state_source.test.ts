import { describe, expect, it } from "vitest";
import { FakeStateSource } from "./fake_state_source";
import { scenario_by_name, scenario_names } from "./source_for";

describe("FakeStateSource", () => {
  it.each(scenario_names())("replays %s into the verdicts it expects", (name) => {
    const scenario = scenario_by_name(name)!;
    const source = new FakeStateSource(scenario);
    source.advance_to(Number.POSITIVE_INFINITY);

    const verdicts = source.rows("verdict");
    for (const expected of scenario.expected) {
      const verdict = verdicts.find(
        (candidate) =>
          candidate.plate_id === expected.plate_id && candidate.ticket_revision === expected.ticket_revision,
      );
      expect(verdict?.decision).toBe(expected.decision);
      if (expected.decision === "hold") expect(verdict?.reasons.length).toBeGreaterThan(0);
      expect(verdict?.chain.map((step) => step.event_id)).toEqual(expected.chain_event_ids ?? []);
    }
  });

  it("keeps only the latest ticket revision in the ticket table", () => {
    const source = new FakeStateSource(scenario_by_name("late-amendment-adds-dairy")!);
    source.advance_to(Number.POSITIVE_INFINITY);
    const tickets = source.rows("ticket");
    expect(tickets).toHaveLength(1);
    expect(tickets[0].revision).toBe(2);
  });

  it("replaces a table's array on change and keeps it otherwise", () => {
    const source = new FakeStateSource(scenario_by_name("knife-carries-peanut-to-plate")!);
    source.advance_to(0);
    const before = source.rows("verdict");
    expect(source.rows("verdict")).toBe(before);
    source.advance_to(Number.POSITIVE_INFINITY);
    expect(source.rows("verdict")).not.toBe(before);
  });

  it("is connecting until started, and a replay", () => {
    const source = new FakeStateSource(scenario_by_name("knife-carries-peanut-to-plate")!);
    expect(source.feed_status()).toBe("connecting");
    expect(source.is_replay).toBe(true);
  });
});
