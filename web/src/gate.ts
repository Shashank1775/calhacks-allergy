/**
 * What the gate shows for the plate at the pass, for the gate panel on /expo.
 *
 * Reads: zone, entity, entity_state, plate_binding, ticket, verdict; the feed status; vision health.
 * Rules: fail closed, plain-code verdict. The same order as kitchen/gate/service.py's gate_state:
 * stale inputs give FAULT, two plates at the pass give HOLD, no plate or no verdict yet gives
 * IDLE (closed), and only plate_badge()'s PASS opens it. A plate counts at the pass only while
 * vision still tracks it (plates_at_pass). This decides nothing new: PASS comes only from
 * plate_badge(), which only reads verdict rows.
 */
import { latest_verdict, plate_badge } from "./badge";
import type { FeedStatus, Tables } from "./state/state_source";
import type { EntityState, Ticket, Verdict } from "./types/TEMPORARY";
import type { VisionHealth } from "./vision_health";

export type GateState = "idle" | "pass" | "hold" | "fault";

export type GateView = {
  state: GateState;
  /** Why it isn't open, in a few words, when that isn't the verdict's own reasons. */
  note: string | null;
  plate_id: string | null;
  ticket: Ticket | undefined;
  verdict: Verdict | undefined;
};

export const GATE_TEXT: Record<GateState, string> = {
  idle: "WAITING",
  pass: "PASS",
  hold: "HOLD",
  fault: "FAULT",
};

type GateTables = Pick<Tables, "zone" | "entity" | "entity_state" | "plate_binding" | "ticket" | "verdict">;

export function gate_view(feed: FeedStatus, vision: VisionHealth, tables: GateTables): GateView {
  const closed = (state: GateState, note: string | null): GateView => ({
    state,
    note,
    plate_id: null,
    ticket: undefined,
    verdict: undefined,
  });

  if (feed !== "live") return closed("fault", "The kitchen feed is lost");
  if (vision === "stale") return closed("fault", "The camera stopped sending frames");

  const plates = plates_at_pass(tables);
  if (plates.length > 1) return closed("hold", "Two plates at the pass: one at a time");
  if (plates.length === 0) return closed("idle", "No plate at the pass");

  const plate_id = plates[0];
  const binding = tables.plate_binding.find((row) => row.plate_id === plate_id);
  const ticket = binding ? tables.ticket.find((row) => row.ticket_id === binding.ticket_id) : undefined;
  const verdicts = tables.verdict.filter((row) => row.plate_id === plate_id);
  const badge = plate_badge(feed, ticket, verdicts);
  const view = { plate_id, ticket, verdict: latest_verdict(verdicts) };

  if (badge === "pass") return { ...view, state: "pass", note: null };
  if (badge === "waiting") return { ...view, state: "idle", note: "Checking this plate" };
  if (badge === "rechecking") return { ...view, state: "hold", note: "The ticket changed: rechecking the plate" };
  if (!ticket) return { ...view, state: "hold", note: "This plate's ticket isn't known" };
  return { ...view, state: "hold", note: null };
}

/** Same limit as GateSettings.plate_lost_ms: vision's track_lost_ms. */
export const PLATE_LOST_MS = 1000;

/**
 * Plates whose latest position is a pass zone and that vision still tracks, the same rule as
 * kitchen/gate/service.py's plates_at_pass().
 *
 * Vision ends a track once its object goes unseen for track_lost_ms and never revives it, so a
 * plate whose newest row is more than PLATE_LOST_MS older than the newest row of any entity has
 * no track: it was lifted out of view, or a hand hid it for over a second and it will come back
 * as a new plate with unknown history, which holds. Age is measured against that newest row, not
 * this browser's clock: rows carry vision's frame times, so feed lag can't age out a plate vision
 * still has. A stale camera is still FAULT, and two plates both seen recently still HOLD.
 */
export function plates_at_pass(tables: Pick<GateTables, "zone" | "entity" | "entity_state">): string[] {
  const pass_zones = new Set(tables.zone.filter((zone) => zone.kind === "pass").map((zone) => zone.zone_id));
  const plate_ids = new Set(
    tables.entity.filter((entity) => entity.kind === "plate").map((entity) => entity.entity_id),
  );
  const latest = latest_states(tables.entity_state);
  const newest_t_ms = newest_sighting(latest);
  const plates: string[] = [];
  for (const [entity_id, state] of latest) {
    const at_pass = state.zone !== null && pass_zones.has(state.zone);
    const still_tracked = newest_t_ms - state.t_ms <= PLATE_LOST_MS;
    if (plate_ids.has(entity_id) && at_pass && still_tracked) plates.push(entity_id);
  }
  return plates;
}

/** The newest t_ms among entities' latest states: when vision last saw anything. */
function newest_sighting(latest: Map<string, EntityState>): number {
  let newest = -Infinity;
  for (const state of latest.values()) newest = Math.max(newest, state.t_ms);
  return newest;
}

/** Each entity's most recent state: the highest t_ms, and the row written last on a tie. */
export function latest_states(states: EntityState[]): Map<string, EntityState> {
  const latest = new Map<string, EntityState>();
  for (const state of states) {
    const seen = latest.get(state.entity_id);
    if (!seen || state.t_ms >= seen.t_ms) latest.set(state.entity_id, state);
  }
  return latest;
}
