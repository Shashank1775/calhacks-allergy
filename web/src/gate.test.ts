import { describe, expect, it } from "vitest";
import hold_peanut from "../../contracts/fixtures/valid/verdict/hold-peanut.json";
import pass_clean from "../../contracts/fixtures/valid/verdict/pass-clean.json";
import { gate_view, LOST_PLATE_CROWDS_MS, PLATE_LOST_MS, plates_at_pass } from "./gate";
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

describe("a plate counts at the pass only while vision still tracks it", () => {
  /** The tables after `plate_id` was last seen at the pass at `t_ms` and lifted out of view, with a bin seen since. */
  function lifted_out(tables: ReturnType<typeof kitchen>, bin_seen_t_ms: number) {
    tables.entity_state.push(at("bin-1", "slot-1", bin_seen_t_ms));
    return tables;
  }

  /** The held plate last seen at the pass at t_ms 2, and the passing plate beside it seen at `seen_t_ms`. */
  function beside_a_passing_plate(seen_t_ms: number) {
    const tables = kitchen(hold, "pass");
    tables.entity.push({ ...tables.entity[0], entity_id: pass.plate_id });
    tables.entity_state.push(at(pass.plate_id, "pass", seen_t_ms));
    tables.plate_binding.push({ plate_id: pass.plate_id, ticket_id: pass.ticket_id, t_ms: 1 });
    tables.ticket.push(ticket_for(pass));
    tables.verdict.push(pass);
    return tables;
  }

  it("lets a lone plate lifted out of view go once unseen for longer than vision's track_lost_ms", () => {
    expect(gate_view("live", "ok", lifted_out(kitchen(hold, "pass"), 2 + PLATE_LOST_MS)).state).toBe("hold");

    const gone = lifted_out(kitchen(hold, "pass"), 2 + PLATE_LOST_MS + 1);
    expect(plates_at_pass(gone)).toEqual([{ plate_id: hold.plate_id, unseen_ms: PLATE_LOST_MS + 1 }]);
    expect(gate_view("live", "ok", gone).state).toBe("idle");
  });

  it("safety: holds a PASS plate while one beside it was seen within LOST_PLATE_CROWDS_MS", () => {
    // The other plate is hidden by a hand (vision ended its track after 1 s), or was just lifted out.
    expect(gate_view("live", "ok", beside_a_passing_plate(2 + 1200)).state).toBe("hold");
    expect(gate_view("live", "ok", beside_a_passing_plate(2 + LOST_PLATE_CROWDS_MS)).state).toBe("hold");
  });

  it("safety: the gap left on purpose: a plate unseen for over LOST_PLATE_CROWDS_MS lets the gate open beside it", () => {
    // Also how the next plate passes once the last one has been gone 5 s. See kitchen/gate/README.md.
    const view = gate_view("live", "ok", beside_a_passing_plate(2 + LOST_PLATE_CROWDS_MS + 1));
    expect(view.state).toBe("pass");
    expect(view.plate_id).toBe(pass.plate_id);
  });

  it("safety: holds two plates at the pass both seen within the limit", () => {
    const tables = kitchen(pass, "pass");
    tables.entity.push({ ...tables.entity[0], entity_id: "plate-other" });
    tables.entity_state.push(at("plate-other", "pass", 2 + PLATE_LOST_MS));
    expect(plates_at_pass(tables)).toHaveLength(2);
    expect(gate_view("live", "ok", tables).state).toBe("hold");
  });

  it("safety: still faults on a stale camera after a plate leaves", () => {
    const tables = lifted_out(kitchen(pass, "pass"), 2 + PLATE_LOST_MS + 1);
    expect(gate_view("live", "stale", tables).state).toBe("fault");
  });
});
