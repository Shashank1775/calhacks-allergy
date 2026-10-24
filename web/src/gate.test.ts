import { describe, expect, it } from "vitest";
import hold_peanut from "../../contracts/fixtures/valid/verdict/hold-peanut.json";
import pass_clean from "../../contracts/fixtures/valid/verdict/pass-clean.json";
import { gate_view, plates_at_pass } from "./gate";
import type { EntityState, Ticket, Verdict } from "./types/TEMPORARY";

const hold = hold_peanut as Verdict;
const pass = pass_clean as Verdict;

function at(plate_id: string, zone: string, t_ms: number): EntityState {
  return {
    state_id: `${plate_id}@${t_ms}`,
    entity_id: plate_id,
    version: t_ms,
    t_ms,
    zone,
    box: null,
    probabilities: {},
    unknown: false,
    caused_by: null,
  };
}

function ticket_for(verdict: Verdict, revision = verdict.ticket_revision): Ticket {
  return {
    ticket_id: verdict.ticket_id,
    number: 7,
    revision,
    created_t_ms: 0,
    source: "typed",
    note: "",
    pass_threshold: verdict.pass_threshold,
    forbidden: [],
  };
}

/** The tables for one plate that ends up in `zone`, with `verdicts` on it. */
function kitchen(verdict: Verdict, zone: string, verdicts: Verdict[] = [verdict], ticket = ticket_for(verdict)) {
  return {
    zone: [
      { zone_id: "spot", kind: "plating_spot" as const, polygon: [] },
      { zone_id: "pass", kind: "pass" as const, polygon: [] },
    ],
    entity: [
      {
        entity_id: verdict.plate_id,
        kind: "plate" as const,
        label: "",
        origin: "shift_start" as const,
        first_seen_t_ms: 0,
      },
    ],
    entity_state: [at(verdict.plate_id, "spot", 1), at(verdict.plate_id, zone, 2)],
    plate_binding: [{ plate_id: verdict.plate_id, ticket_id: verdict.ticket_id, t_ms: 1 }],
    ticket: [ticket],
    verdict: verdicts,
  };
}

describe("safety: the gate panel only opens on plate_badge's PASS", () => {
  it("passes a plate at the pass whose current verdict passes", () => {
    const view = gate_view("live", "ok", kitchen(pass, "pass"));
    expect(view.state).toBe("pass");
    expect(view.ticket?.number).toBe(7);
  });

  it.each(["lost", "connecting"] as const)("faults when the feed is %s", (feed) => {
    expect(gate_view(feed, "ok", kitchen(pass, "pass")).state).toBe("fault");
  });

  it("faults when the camera stops sending frames", () => {
    expect(gate_view("live", "stale", kitchen(pass, "pass")).state).toBe("fault");
  });

  it("holds a plate whose verdict holds", () => {
    expect(gate_view("live", "ok", kitchen(hold, "pass")).state).toBe("hold");
  });

  it("holds while an amended ticket is rechecked", () => {
    const view = gate_view("live", "ok", kitchen(pass, "pass", [pass], ticket_for(pass, pass.ticket_revision + 1)));
    expect(view.state).toBe("hold");
    expect(view.note).toContain("rechecking");
  });

  it("holds two plates at the pass at once", () => {
    const tables = kitchen(pass, "pass");
    tables.entity.push({ ...tables.entity[0], entity_id: "plate-other" });
    tables.entity_state.push(at("plate-other", "pass", 3));
    expect(gate_view("live", "ok", tables).state).toBe("hold");
  });

  it("stays closed with no plate at the pass, or before a verdict", () => {
    expect(gate_view("live", "ok", kitchen(pass, "spot")).state).toBe("idle");
    expect(gate_view("live", "ok", kitchen(pass, "pass", [])).state).toBe("idle");
  });

  it("holds a plate at the pass whose ticket isn't known", () => {
    const tables = kitchen(pass, "pass");
    tables.ticket = [];
    expect(gate_view("live", "ok", tables).state).toBe("hold");
  });

  it("uses each plate's latest position", () => {
    const tables = kitchen(pass, "pass");
    tables.entity_state.push(at(pass.plate_id, "spot", 5));
    expect(plates_at_pass(tables)).toEqual([]);
  });
});
